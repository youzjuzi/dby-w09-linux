/* SPDX-License-Identifier: GPL-2.0-only */
/* Included after the unchanged frame transfer/checksum primitives. */
static bool nt_stream_busy(struct nt_stream *s)
{
 bool busy;

 if (!s)
  return false;
 mutex_lock(&s->lock);
 busy = s->state == NT_S_RUNNING;
 mutex_unlock(&s->lock);
 return busy;
}

static bool nt_stream_abnormal(enum nt_stream_reason reason)
{
 return reason >= NT_S_STALL && reason != NT_S_INPUT_OFF;
}

/* lock held. Panel cancellation overrides a normal stop until IRQ is drained. */
static void nt_stream_stop(struct nt_stream *s, enum nt_stream_reason reason)
{
 if(s->managed && reason==NT_S_PANEL)reason=NT_S_INPUT_OFF;
 if (!s->stop || (!nt_stream_abnormal(s->reason) && nt_stream_abnormal(reason)))
  s->reason = reason;
 WRITE_ONCE(s->stop, true);
 if (nt_stream_abnormal(s->reason) && (!s->managed || s->reason==NT_S_STORM))
  s->poisoned = true;
 wake_up_all(&s->wake);
}

static void nt_stream_checks(struct nt_stream *s, u64 now)
{
 /* Probes have their own hard deadline; quiet boot is not a native stall. */
 if(s->probing && now>=s->deadline_ms)nt_stream_stop(s,NT_S_INPUT_OFF);
 if (nt_capture_invalid(s->nt))
  nt_stream_stop(s, NT_S_PANEL);
 /* Do not let a late owner wake mask an already expired stall as a
  * normal deadline/no_reader stop. Panel and frame faults take precedence.
  */
 if (!s->probing && !s->stop && now - s->last_frame_ms >= NT_STREAM_STALL_MS)
  nt_stream_stop(s, NT_S_STALL);
 if (!s->native && !s->stop && now >= s->deadline_ms)
  nt_stream_stop(s, NT_S_DEADLINE);
 if (!s->native && !s->stop && !s->seen_reader && now - s->start_ms >= NT_STREAM_READER_MS)
  nt_stream_stop(s, NT_S_NO_READER);
}

static enum nt_stream_reason nt_stream_frame(struct nt_stream *s, const u8 *data)
{
 unsigned int i;
 u16 counter = nt_le16(data + NT_COUNT_OFFSET);
 bool zero = true, ff = true;

 for (i = 0; i < NT_FRAME_SIZE; i++) {
  zero = zero && data[i] == 0;
  ff = ff && data[i] == 0xff;
 }
 if (zero)
  return NT_S_ZERO;
 if (ff)
  return NT_S_FF;
 if (nt_frame_checksum(data + NT_RAW_BASE, 3214) != nt_le16(data + NT_CRC_OFFSET))
  return NT_S_MAIN_CRC;
 if (nt_frame_checksum(data + NT_PEN_OFFSET, 30) != nt_le16(data + NT_PEN_CRC_OFFSET))
  return NT_S_PEN_CRC;
 if (s->have_counter) {
  u16 delta = (u16)(counter - s->counter);

  if (!delta)
   return NT_S_REPEAT;
  if (delta >= 0x8000U)
   return NT_S_BACKWARD;
  s->gaps += delta - 1;
 }
 s->counter = counter;
 s->have_counter = true;
 return NT_S_NONE;
}

/* Kept separate from the durable stream reason names / raw session ABI. */
static const char *nt_last_bad_text(enum nt_stream_reason reason)
{
 switch(reason){
 case NT_S_ZERO:return "zero";case NT_S_FF:return "ff";
 case NT_S_LENGTH:return "length";case NT_S_MAIN_CRC:return "main_crc";
 case NT_S_PEN_CRC:return "pen_crc";case NT_S_REPEAT:return "repeat";
 case NT_S_BACKWARD:return "backward";case NT_S_SPI:return "spi";
 default:return "none";
 }
}

static irqreturn_t nt_stream_irq(int irq, void *dev_id)
{
 struct nt_stream *s = dev_id;
 struct nt_thp *nt = s->nt;
 enum nt_stream_reason reason;
 u64 now;
 unsigned int tail;
 int ret;

 (void)irq;
 mutex_lock(&s->lock);
 s->irqs++;
 if(s->probing)s->probe_irqs++;
 now = nt_now_ms(nt);
 /* Exact sliding window at the monotonic millisecond clock resolution.
  * Fixed 1000 buckets retain exact counts even after the storm trip point.
  */
 if (now - s->rate_ms >= NT_STREAM_IRQ_MS) {
  memset(s->irq_slots, 0, sizeof(s->irq_slots));
  s->rate_count = 0;
 } else {
  for (u64 t = s->rate_ms + 1; t <= now; t++) {
   unsigned int slot = t % NT_STREAM_IRQ_MS;

   s->rate_count -= s->irq_slots[slot];
   s->irq_slots[slot] = 0;
  }
 }
 s->rate_ms = now;
 s->irq_slots[now % NT_STREAM_IRQ_MS]++;
 s->rate_count++;
 if (s->rate_count > s->max_irqs)
  s->max_irqs = s->rate_count;
 if (s->rate_count > NT_STREAM_IRQ_LIMIT)
  nt_stream_stop(s, NT_S_STORM);
 nt_stream_checks(s, now);
 if(s->probing && now>=s->deadline_ms)nt_stream_stop(s,NT_S_INPUT_OFF);
 if (!s->stop) {
  /* Only this 3257-byte read message can reach SPI during a stream. */
  memset(nt->tx, 0, NT_FRAME_SIZE);
  memset(nt->rx, 0, NT_FRAME_SIZE);
  nt->tx[0] = 0x20;
  ret = nt_capture_transfer(nt);
  if(s->probing){
   if(nt_now_ms(nt)>=s->deadline_ms){nt_stream_stop(s,NT_S_INPUT_OFF);goto irq_done;}
   u16 prior=s->counter;
   bool had=s->have_counter;
   reason=ret?(ret==-EMSGSIZE?NT_S_LENGTH:NT_S_SPI):nt_stream_frame(s,nt->rx);
   if(reason!=NT_S_NONE)s->last_bad=reason;
   if(ret)nt_stream_stop(s,reason);
   else if(reason==NT_S_NONE){
    unsigned int good=READ_ONCE(s->probe_good);
    WRITE_ONCE(s->probe_good,had && (u16)(s->counter-prior)==1?good+1:1);
    if(READ_ONCE(s->probe_good)>=2){
     s->probe_hit_ms=nt_now_ms(nt);nt_stream_stop(s,NT_S_INPUT_OFF);
    }
    s->last_frame_ms=nt_now_ms(nt);
   }else if(reason==NT_S_REPEAT){s->dups++;}
   else {
    WRITE_ONCE(s->probe_good,0U);
    if(reason==NT_S_BACKWARD){s->have_counter=false;s->resyncs++;}
   }
   s->frames++;s->probe_frames++;
   goto irq_done;
  }
  /* Preserve even a failed/partial RX, padded with zero before the transfer.
   * Such a slot is evidence, NOT a claimed complete valid hardware frame.
   */
  if (!s->native) {
  if (s->count == NT_RING_FRAMES) {
   s->head = (s->head + 1) % NT_RING_FRAMES;
   WRITE_ONCE(s->count, s->count - 1);
   s->dropped++;
  }
  tail = (s->head + s->count) % NT_RING_FRAMES;
  memcpy(s->ring + tail * NT_FRAME_SIZE, nt->rx, NT_FRAME_SIZE);
  WRITE_ONCE(s->count, s->count + 1);
  }
  if (s->native && !ret)nt_input_process(s, nt->rx);
  else if(s->native){s->input_rejected++;nt_tap_reject(&s->tap);}
  s->frames++;
  if(s->native)s->input_frames++;
  reason = ret ? (ret == -EMSGSIZE ? NT_S_LENGTH : NT_S_SPI) : nt_stream_frame(s, nt->rx);
  if(reason!=NT_S_NONE)s->last_bad=reason;
  if(s->native && (reason==NT_S_REPEAT || reason==NT_S_BACKWARD)){
   if(reason==NT_S_REPEAT)s->dups++;
   else{s->have_counter=false;s->resyncs++;}
  }else if (reason != NT_S_NONE)
   nt_stream_stop(s, reason);
  else {
   now = nt_now_ms(nt);
   if (now - s->last_frame_ms >= NT_STREAM_STALL_MS)
    nt_stream_stop(s, NT_S_STALL);
   else
    s->last_frame_ms = now;
  }
  nt_stream_checks(s, nt_now_ms(nt));
 }
irq_done:
 mutex_unlock(&s->lock);
 wake_up_all(&s->wake);
 wake_up_all(&s->readers);
 return IRQ_HANDLED;
}

#ifdef THP_HOST_TEST
static int nt_request_stream_irq(struct nt_stream *s);
static void nt_disable_stream_irq(struct nt_stream *s);
static void nt_free_stream_irq(struct nt_stream *s);
#else
static int nt_request_stream_irq(struct nt_stream *s)
{
 return request_threaded_irq(s->nt->irq, NULL, nt_stream_irq, IRQF_ONESHOT,
                             "nt36523-thp-stream", s);
}
static void nt_disable_stream_irq(struct nt_stream *s) { disable_irq(s->nt->irq); }
static void nt_free_stream_irq(struct nt_stream *s) { free_irq(s->nt->irq, s); }
#endif

/* No caller of this function is allowed in follower/release/remove call graphs.
 * The joinable owner parks after draining; kthread_stop joins it under life.
 */
static int nt_stream_owner(void *data)
{
 struct nt_stream *s = data;
 int ret;

 mutex_lock(&s->lock);
 nt_stream_checks(s, nt_now_ms(s->nt));
 ret = s->stop ? -ECANCELED : 0;
 mutex_unlock(&s->lock);
 if (!ret) {
  ret = nt_request_stream_irq(s);
  if (ret) {
   mutex_lock(&s->lock);
   nt_stream_stop(s, NT_S_REQUEST);
   mutex_unlock(&s->lock);
  }
 }
 if (!ret) {
  for (;;) {
   bool stop;

   mutex_lock(&s->lock);
   nt_stream_checks(s, nt_now_ms(s->nt));
   stop = s->stop;
   mutex_unlock(&s->lock);
   if (stop)
    break;
   wait_event_timeout(s->wake, READ_ONCE(s->stop), msecs_to_jiffies(s->probing ? 50 : NT_STREAM_TICK_MS));
  }
  /* No bookkeeping lock is held while draining an in-flight IRQ. */
  nt_disable_stream_irq(s);
  nt_free_stream_irq(s);
 }
 mutex_lock(&s->lock);
 if (nt_capture_invalid(s->nt))
  nt_stream_stop(s, NT_S_PANEL);
 if(s->native){nt_input_up(s);s->input_phase=s->poisoned?3:2;s->input_reason=s->reason;}
 if(s->managed && !s->probing && nt_stream_abnormal(s->reason))nt_auto_ended(s->nt);
 s->freed = !ret;
 s->elapsed_ms = nt_now_ms(s->nt) - s->start_ms;
 WRITE_ONCE(s->state, s->poisoned ? NT_S_ABORTED : NT_S_ENDED);
 mutex_unlock(&s->lock);
 wake_up_all(&s->readers);
 complete(&s->exited);
 /* Do not return into module text after signalling a potentially final waiter.
  * Joiners keep task_struct alive by having the owner park until stop is set.
  */
 wait_event(s->wake, kthread_should_stop());
 return 0;
}

/* life held; no state/panel lock is held across kthread_stop. A parked task
 * cannot disappear before the joiner calls kthread_stop. */
static void nt_stream_join(struct nt_stream *s)
{
 if (s->owner) {
  wait_for_completion(&s->exited);
  kthread_stop(s->owner);
  s->owner = NULL;
  mutex_lock(&s->lock);
  s->pending = false;
  mutex_unlock(&s->lock);
 }
}

static void nt_stream_reap(struct nt_stream *s)
{
 bool finished;

 mutex_lock(&s->life);
 mutex_lock(&s->lock);
 finished = s->state != NT_S_RUNNING;
 mutex_unlock(&s->lock);
 if (finished)
  nt_stream_join(s);
 mutex_unlock(&s->life);
}

static int nt_stream_parse(const char *buf, size_t count, unsigned int *seconds)
{
 unsigned int n = 0;
 size_t i;

 if (count < 13 || memcmp(buf, "THP-STREAM ", 11) || buf[count - 1] != '\n')
  return -EINVAL;
 for (i = 11; i < count - 1; i++) {
  if (buf[i] < '0' || buf[i] > '9')
   return -EINVAL;
  n = n * 10 + buf[i] - '0';
  if (n > NT_STREAM_MAX_SECONDS)
   return -EINVAL;
 }
 if (!n || n > NT_STREAM_MAX_SECONDS)
  return -EINVAL;
 *seconds = n;
 return 0;
}

static ssize_t nt_session_start_internal(struct nt_thp *nt, const char *buf, size_t count, bool native, bool managed, bool probe, unsigned long generation, u64 probe_deadline)
{
 struct nt_stream *s = nt->stream;
 struct task_struct *task;
 unsigned int seconds;
 int ret = native ? 0 : nt_stream_parse(buf, count, &seconds);
 if(native)seconds=0;

 if (ret)
  return ret;
 if (!mutex_trylock(&nt->lock))
  return -EBUSY;
 if(!managed && nt_auto_active(nt)){mutex_unlock(&nt->lock);return -EBUSY;}
 mutex_lock(&s->life);
 /* Terminal state is durable; completion tokens are consumed by join. */
 mutex_lock(&s->lock);
 ret = s->state != NT_S_RUNNING;
 mutex_unlock(&s->lock);
 if (ret)
  nt_stream_join(s);
 ret = 0;
 mutex_lock(&s->lock);
 mutex_lock(&nt->panel_lock);
 if (managed && (!nt_auto_active(nt) || nt->gen!=generation))
  ret=-ECANCELED;
 else if ((!managed || !probe) && nt->state != NT_DONE)
  ret = -EPERM;
 else if (nt->dead || READ_ONCE(nt->quiesce) || s->removed)
  ret = -ESHUTDOWN;
 else if (s->state == NT_S_RUNNING)
  ret = -EBUSY;
 else if (native && s->reader)
  ret = -EBUSY;
 else if (s->poisoned)
  ret = -EIO;
 else if ((!managed && s->sessions >= NT_STREAM_QUOTA) || (managed && !probe && nt->auto_state.sessions>=65535))
  ret = -EDQUOT;
 else if (!nt->panel_on)
  ret = -EAGAIN;
 else if (nt->gen != nt->download_gen)
  ret = -ECANCELED;
 else if (nt->irq <= 0)
  ret = -ENODEV;
 mutex_unlock(&nt->panel_lock);
 if (ret)
  goto out;
 if(native && !probe){
  struct ta_map saved=s->algo->map;
  ret=nt_input_register(s);
  if(ret)goto out;
  nt_tap_init(&s->tap);ta_init(s->algo,8);s->algo->map=saved;
  s->input_accepted=s->input_rejected=s->input_downs=s->input_moves=s->input_ups=0;
  s->proc_count=s->proc_max_ns=s->proc_over2ms=s->proc_over6ms=0;
  s->input_seen=true;s->input_phase=1;s->input_reason=NT_S_NONE;s->input_frames=0;
 }
 s->native=native;s->managed=managed;s->probing=probe;WRITE_ONCE(s->probe_good,0U);
 s->probe_hit_ms=0;s->generation=generation;
 if(probe){s->probe_irqs=s->probe_frames=0;s->last_bad=NT_S_NONE;}
 if(!managed)s->sessions++;
 else if(!probe){mutex_lock(&nt->panel_lock);nt->auto_state.sessions++;mutex_unlock(&nt->panel_lock);}
 WRITE_ONCE(s->state, NT_S_RUNNING);
 s->reason = NT_S_NONE;
 WRITE_ONCE(s->stop, false);
 s->freed = false;
 s->seen_reader = s->reader;
 s->head = 0;
 s->rate_count = s->max_irqs = 0;
 memset(s->irq_slots, 0, sizeof(s->irq_slots));
 WRITE_ONCE(s->count, 0);
 s->frames = s->delivered = s->dropped = s->gaps = s->irqs = s->elapsed_ms = 0;
 s->have_counter = false;
 s->rate_ms = s->start_ms = s->last_frame_ms = nt_now_ms(nt);
 s->deadline_ms = probe ? (probe_deadline ? probe_deadline : s->start_ms+200) : s->start_ms+seconds*1000ULL;
 reinit_completion(&s->exited);
 s->pending = true;
 task = kthread_create(nt_stream_owner, s, "nt36523-thp");
 if (IS_ERR(task)) {
  ret = PTR_ERR(task);
  nt_stream_stop(s, NT_S_REQUEST);
  WRITE_ONCE(s->state, NT_S_ABORTED);
  s->pending = false;
  if(s->native){s->input_phase=3;s->input_reason=s->reason;}
  complete(&s->exited);
 } else {
  s->owner = task;
  wake_up_process(task);
 }
out:
 mutex_unlock(&s->lock);
 mutex_unlock(&s->life);
 mutex_unlock(&nt->lock);
 return ret ? ret : (ssize_t)count;
}

static ssize_t nt_session_start(struct nt_thp *nt,const char *buf,size_t count,bool native)
{
 if(nt_auto_active(nt))return -EBUSY;
 return nt_session_start_internal(nt,buf,count,native,false,false,0,0);
}

static ssize_t nt_stream_start(struct nt_thp *nt, const char *buf, size_t count)
{
 return nt_session_start(nt,buf,count,false);
}

static void nt_stream_quiet(struct nt_stream *s)
{
 bool wait;

 mutex_lock(&s->life);
 mutex_lock(&s->lock);
 wait = s->pending;
 if (s->state == NT_S_RUNNING)
  nt_stream_stop(s, NT_S_PANEL);
 mutex_unlock(&s->lock);
 if (wait)
  nt_stream_join(s);
 mutex_unlock(&s->life);
}

/* Binding owns one reference; the exclusive file owns another. No devm state
 * is accessed through a file after remove. misc core serializes open/deregister.
 */
static void nt_stream_put(struct nt_stream *s)
{
 bool destroy;

 mutex_lock(&s->lock);
 destroy = --s->refs == 0;
 mutex_unlock(&s->lock);
 if (destroy) {
  vfree(s->tap.data);
  kvfree(s->algo);
  kvfree(s->ring);
  kvfree(s);
 }
}

static int nt_stream_open(struct inode *inode, struct file *file)
{
 struct nt_stream *s = container_of(file->private_data, struct nt_stream, misc);
 int ret = 0;

 if(nt_auto_active(s->nt))return -EBUSY;
 mutex_lock(&s->lock);
 if(nt_auto_active(s->nt)){mutex_unlock(&s->lock);return -EBUSY;}
 if (s->removed)
  ret = -ENODEV;
 else if (s->native && s->state==NT_S_RUNNING)
  ret = -EBUSY;
 else if (s->reader)
  ret = -EBUSY;
 else {
  ret = nonseekable_open(inode, file);
  if (!ret) {
   s->refs++;
   s->reader = s->seen_reader = true;
   file->private_data = s;
  }
 }
 mutex_unlock(&s->lock);
 return ret;
}

static ssize_t nt_stream_read(struct file *file, char __user *buf, size_t size, loff_t *off)
{
 struct nt_stream *s = file->private_data;
 unsigned int session;
 int ret;

 (void)off;
 if (size < NT_FRAME_SIZE)
  return -EINVAL;
 /* Serialize reads on one fd without making the IRQ wait on a user fault. */
 mutex_lock(&s->read_lock);
 for (;;) {
  mutex_lock(&s->lock);
  if (s->removed) {
   mutex_unlock(&s->lock);
   ret = 0;
   break;
  }
  if (s->count) {
   session = s->sessions;
   memcpy(s->reader_frame, s->ring + s->head * NT_FRAME_SIZE, NT_FRAME_SIZE);
   s->head = (s->head + 1) % NT_RING_FRAMES;
   WRITE_ONCE(s->count, s->count - 1);
   mutex_unlock(&s->lock);
   ret = copy_to_user(buf, s->reader_frame, NT_FRAME_SIZE) ? -EFAULT : (int)NT_FRAME_SIZE;
   mutex_lock(&s->lock);
   if (session == s->sessions) {
    if (ret > 0)
     s->delivered++;
    else
     s->dropped++; /* Dequeued snapshot lost on a user-copy fault. */
   }
   mutex_unlock(&s->lock);
   break;
  }
  if (s->state != NT_S_RUNNING) {
   mutex_unlock(&s->lock);
   nt_stream_reap(s);
   ret = 0;
   break;
  }
  mutex_unlock(&s->lock);
  if (file->f_flags & O_NONBLOCK) {
   ret = -EAGAIN;
   break;
  }
  ret = wait_event_interruptible(s->readers, READ_ONCE(s->removed) ||
        READ_ONCE(s->count) || READ_ONCE(s->state) != NT_S_RUNNING);
  if (ret)
   break;
 }
 mutex_unlock(&s->read_lock);
 return ret;
}

static __poll_t nt_stream_poll(struct file *file, poll_table *wait)
{
 struct nt_stream *s = file->private_data;
 __poll_t result = 0;

 poll_wait(file, &s->readers, wait);
 mutex_lock(&s->lock);
 if (s->removed || s->state != NT_S_RUNNING)
  result |= EPOLLHUP;
 if (!s->removed && s->count)
  result |= EPOLLIN | EPOLLRDNORM;
 mutex_unlock(&s->lock);
 return result;
}

static int nt_stream_release(struct inode *inode, struct file *file)
{
 struct nt_stream *s = file->private_data;
 bool wait;

 (void)inode;
 mutex_lock(&s->life);
 mutex_lock(&s->lock);
 s->reader = false;
 wait = s->pending;
 if (s->state == NT_S_RUNNING)
  nt_stream_stop(s, NT_S_CLOSED);
 mutex_unlock(&s->lock);
 if (wait)
  nt_stream_join(s);
 mutex_unlock(&s->life);
 file->private_data = NULL;
 nt_stream_put(s);
 return 0;
}

static const struct file_operations nt_stream_fops = {
 .owner = THIS_MODULE,
 .open = nt_stream_open,
 .read = nt_stream_read,
 .poll = nt_stream_poll,
 .release = nt_stream_release,
};

static int nt_stream_alloc(struct nt_thp *nt)
{
 struct nt_stream *s;
 int ret;

 s = kvzalloc(sizeof(*s), GFP_KERNEL);
 if (!s)
  return -ENOMEM;
 s->algo=kvzalloc(sizeof(*s->algo),GFP_KERNEL);
 if(!s->algo){kvfree(s);return -ENOMEM;}
 nt_tap_init(&s->tap);ta_init(s->algo,8);s->algo->map=nt_native_map;nt_edge_recalc(s);
 s->ring = kvmalloc(NT_RING_FRAMES * NT_FRAME_SIZE, GFP_KERNEL);
 if (!s->ring) {
  vfree(s->tap.data);
  kvfree(s->algo);
  kvfree(s);
  return -ENOMEM;
 }
 mutex_init(&s->life);
 mutex_init(&s->lock);
 mutex_init(&s->read_lock);
 init_completion(&s->exited);
 init_waitqueue_head(&s->wake);
 init_waitqueue_head(&s->readers);
 s->refs = 1;
 s->nt = nt;
 s->misc.minor = MISC_DYNAMIC_MINOR;
 s->misc.name = "nt36523-thp";
 s->misc.mode = 0600;
 s->misc.fops = &nt_stream_fops;
#ifndef THP_HOST_TEST
 s->misc.parent = &nt->spi->dev;
#endif
 ret = misc_register(&s->misc);
 if (ret) {
  nt_stream_put(s);
  return ret;
 }
 s->registered = true;
 nt->stream = s;
 return 0;
}

static void nt_stream_detach(struct nt_stream *s)
{
 mutex_lock(&s->lock);
 WRITE_ONCE(s->removed, true);
 mutex_unlock(&s->lock);
 wake_up_all(&s->readers);
 nt_stream_quiet(s);
 if(s->input_dev){
  input_unregister_device(s->input_dev);
#ifdef THP_HOST_TEST
  free(s->input_dev);host_input_devices--;
#endif
  s->input_dev=NULL;
 }
 if (s->registered) {
  misc_deregister(&s->misc);
  s->registered = false;
 }
}

static const char *nt_stream_reason_text(enum nt_stream_reason reason)
{
 static const char * const names[] = { "none", "deadline", "reader_closed", "no_reader",
  "stall", "irq_storm", "panel", "spi_error", "actual_length", "main_checksum",
  "pen_checksum", "all_zero", "all_ff", "counter_repeat", "counter_backward", "irq_request", "input_off" };
 return names[reason];
}
static const char *nt_stream_reason_name(struct nt_stream *s)
{
 return nt_stream_reason_text(s->reason);
}
#ifndef THP_HOST_TEST
static void nt_stream_binding_put(void *data) { nt_stream_put(data); }

static ssize_t stream_store(struct device *dev, struct device_attribute *attr,
                            const char *buf, size_t count)
{
 return nt_stream_start(dev_get_drvdata(dev), buf, count);
}
static ssize_t stream_status_show(struct device *dev, struct device_attribute *attr, char *buf)
{
 struct nt_stream *s = ((struct nt_thp *)dev_get_drvdata(dev))->stream;
 static const char * const states[] = { "idle", "running", "ended", "aborted" };
 ssize_t ret;

 mutex_lock(&s->lock);
 ret = sysfs_emit(buf, "state=%s reason=%s session=%u quota=%u frames=%llu delivered=%llu dropped=%llu gaps=%llu irqs=%llu max_1s_irqs=%llu elapsed_ms=%llu deadline_ms=%llu free_irq=%u reader=%u\n",
  states[s->state], nt_stream_reason_name(s), s->sessions, NT_STREAM_QUOTA, s->frames, s->delivered,
  s->dropped, s->gaps, s->irqs, s->max_irqs, s->state == NT_S_RUNNING ?
  nt_now_ms(s->nt) - s->start_ms : s->elapsed_ms, s->deadline_ms - s->start_ms,
  s->freed, s->reader);
 mutex_unlock(&s->lock);
 return ret;
}
#endif
