/* SPDX-License-Identifier: GPL-2.0-only */
/* No DRM locks in worker/IRQ/owner. control is never acquired by these paths. */
static bool nt_auto_valid(struct nt_thp *nt, unsigned long gen)
{
 bool ok;
 mutex_lock(&nt->panel_lock);
 ok=nt_auto_active(nt) && !nt->auto_state.removed && nt->panel_on &&
    !nt->dead && !READ_ONCE(nt->quiesce) && nt->gen==gen;
 mutex_unlock(&nt->panel_lock);
 return ok;
}

/* panel_lock held by all callers; no formatting helper adds an export. */
static void nt_auto_d18(struct nt_auto *a,char text[3])
{
 static const char digits[]="0123456789abcdef";
 text[0]=a->d18_valid?digits[a->d18>>4]:'-';
 text[1]=a->d18_valid?digits[a->d18&15]:'-';text[2]=0;
}
static void nt_auto_log(struct nt_thp *nt, bool failed)
{
#ifndef THP_HOST_TEST
 char d18[3];nt_auto_d18(&nt->auto_state,d18);
 if(failed)
  dev_err_ratelimited(&nt->spi->dev,"auto failed reason=%s error=%d elapsed_ms=%llu d18=%s probe_irqs=%llu probe_frames=%llu last_bad=%s settle=%lld probe=%lld dl=%lld boot=%lld\n",
    nt->auto_state.last_reason,nt->auto_state.last_error,
    nt_now_ms(nt)-nt->auto_state.enabled_ms,d18,
    READ_ONCE(nt->stream->probe_irqs),READ_ONCE(nt->stream->probe_frames),
    nt_last_bad_text(READ_ONCE(nt->stream->last_bad)),
    nt->auto_state.w_settle,nt->auto_state.w_probe,nt->auto_state.w_dl,nt->auto_state.w_boot);
 else
  dev_info_ratelimited(&nt->spi->dev,"auto %s elapsed_ms=%llu boot_ms=%llu d18=%s settle=%lld probe=%lld dl=%lld boot=%lld\n",
    nt->auto_state.fw==1?"alive":"loaded",nt->auto_state.last_resume_ms,
    nt->auto_state.boot_ms,d18,nt->auto_state.w_settle,nt->auto_state.w_probe,
    nt->auto_state.w_dl,nt->auto_state.w_boot);
#else
 (void)nt;(void)failed;
#endif
}

/* UP and stop are indivisible with IRQ reporting. Owner alone drains IRQ. */
static void nt_auto_stop(struct nt_thp *nt)
{
 struct nt_stream *s=nt->stream;
 bool managed;
 mutex_lock(&s->life);
 mutex_lock(&s->lock);
 managed=s->managed;
 if(managed && s->state==NT_S_RUNNING){
  nt_input_up(s);
  nt_stream_stop(s,NT_S_INPUT_OFF);
 }
 mutex_unlock(&s->lock);
 if(managed)nt_stream_join(s);
 mutex_unlock(&s->life);
}

static void nt_auto_failure(struct nt_thp *nt,unsigned long gen,int error,const char *reason)
{
 struct nt_auto *a=&nt->auto_state;
 mutex_lock(&nt->panel_lock);
 if(nt_auto_active(nt) && !a->removed && nt->panel_on && nt->gen==gen && !READ_ONCE(nt->quiesce)){
  a->fw=3;a->phase=0;a->first_pending=false;a->last_error=error;
  a->last_reason=reason;a->failures++;a->streak++;
  if(a->streak>=3 || error==-EDQUOT)WRITE_ONCE(a->enabled,false);
  nt_auto_log(nt,true);
 }
 mutex_unlock(&nt->panel_lock);
}

/* s->lock held. Record first accepted native frame, not merely queued work. */
static void nt_auto_frame(struct nt_thp *nt)
{
 struct nt_auto *a=&nt->auto_state;
 mutex_lock(&nt->panel_lock);
 if(nt_auto_active(nt) && nt->panel_on &&
    !READ_ONCE(nt->quiesce) && nt->gen==a->queued_gen){
  a->streak=0;
  if(a->recovering){a->recovering=false;a->recovery_attempts=0;nt->stream->recoveries++;}
  if(!a->first_pending){mutex_unlock(&nt->panel_lock);return;}
  a->first_pending=false;a->resumes++;
  a->last_resume_ms=nt_now_ms(nt)-a->enabled_ms;a->w_first=a->last_resume_ms;
  a->last_reason=a->fw==1?"alive":"loaded";a->last_error=0;
  nt_auto_log(nt,false);
 }
 mutex_unlock(&nt->panel_lock);
}

/* Owner reports asynchronous IRQ/start/runtime failures to the ordered worker.
 * It never joins itself. The queued invocation only drains and records failure. */
static void nt_auto_ended(struct nt_thp *nt)
{
 struct nt_auto *a=&nt->auto_state;
 mutex_lock(&nt->panel_lock);
 if(nt_auto_active(nt) && !a->removed && nt->panel_on && !READ_ONCE(nt->quiesce) && nt->gen==a->queued_gen){
  a->phase=5;
  mod_delayed_work(a->wq,&a->work,0);
 }
 mutex_unlock(&nt->panel_lock);
}

/* The same read-only session, cancellation, IRQ lifetime and pair rule for
 * pre-download (200ms) and post-download (absolute boot deadline). */
static int nt_auto_probe(struct nt_thp *nt,unsigned long gen,u64 deadline,bool *alive,bool *started)
{
 struct nt_stream *s=nt->stream;
 bool poison,boot=READ_ONCE(nt->auto_state.phase)==6;
 enum nt_stream_reason reason;
 u64 hit;
 int ret;
 *alive=false;*started=false;
 ret=nt_session_start_internal(nt,"",0,true,true,true,gen,deadline);
 if(ret<0)return ret;
 *started=true;
 if(!deadline)deadline=s->deadline_ms;
#ifdef THP_HOST_TEST
 nt_host_auto_boundary(nt,READ_ONCE(nt->auto_state.phase)==6?6:2);
#endif
 while(nt_auto_valid(nt,gen) && nt_now_ms(nt)<deadline &&
       READ_ONCE(s->probe_good)<2 && !READ_ONCE(s->stop))
  wait_event_timeout(s->wake,READ_ONCE(s->probe_good)>=2 || READ_ONCE(s->stop),msecs_to_jiffies(50));
 mutex_lock(&s->lock);
 *alive=READ_ONCE(s->probe_good)>=2;poison=s->poisoned;hit=s->probe_hit_ms;reason=s->reason;
 mutex_unlock(&s->lock);
 nt_auto_stop(nt);
 mutex_lock(&nt->panel_lock);
 if(nt->auto_state.queued_gen==gen && boot)
  nt->auto_state.w_boot=*alive?(s64)(hit-nt->auto_state.download_end_ms):
   (s64)(nt_now_ms(nt)-nt->auto_state.download_end_ms);
 mutex_unlock(&nt->panel_lock);
 if(!nt_auto_valid(nt,gen))return -ECANCELED;
 if(poison || nt_stream_abnormal(reason))return -EIO;
 mutex_lock(&nt->panel_lock);
 if(nt->auto_state.phase==6 && *alive){
  nt->auto_state.boot_ms=hit-nt->auto_state.download_end_ms;
  nt->auto_state.w_boot=nt->auto_state.boot_ms;
  if(nt->auto_state.boot_ms>nt->auto_state.boot_max_ms)
   nt->auto_state.boot_max_ms=nt->auto_state.boot_ms;
 }
 mutex_unlock(&nt->panel_lock);
 return 0;
}

static void nt_auto_work(struct work_struct *work)
{
 struct nt_auto *a=container_of(to_delayed_work(work),struct nt_auto,work);
 struct nt_thp *nt=a->nt;
 struct nt_stream *s=nt->stream;
 const struct firmware *fw;
 unsigned long gen;
 unsigned int phase;
 bool alive,poison,probe_started,skip;
 u64 stage_start;
 int ret;

 mutex_lock(&nt->lock);
 mutex_lock(&nt->panel_lock);gen=a->queued_gen;
 phase=a->phase;
 if(phase==5 || phase==7){
  enum nt_stream_reason reason;
  unsigned int delay;
  mutex_unlock(&nt->panel_lock);mutex_unlock(&nt->lock);
  /* The owner completes IRQ drain before this join can return. */
  nt_auto_stop(nt);
  if(!nt_auto_valid(nt,gen))return;
  mutex_lock(&s->lock);reason=s->reason;mutex_unlock(&s->lock);
  if(phase==5){
   mutex_lock(&nt->panel_lock);
   if(reason==NT_S_STORM || a->recovery_attempts>=3){
    mutex_unlock(&nt->panel_lock);nt_auto_failure(nt,gen,-EIO,"native_fault");return;
   }
   delay=a->recovery_attempts==0?200:a->recovery_attempts==1?1000:3000;
   a->recovery_attempts++;a->recovering=true;a->phase=7;
   mod_delayed_work(a->wq,&a->work,msecs_to_jiffies(delay));
   mutex_unlock(&nt->panel_lock);return;
  }
  mutex_lock(&nt->panel_lock);a->phase=4;mutex_unlock(&nt->panel_lock);
  ret=nt_session_start_internal(nt,"",0,true,true,false,gen,0);
  if(ret<0 && ret!=-ECANCELED && ret!=-ESHUTDOWN){
   if(ret==-EDQUOT)nt_auto_failure(nt,gen,ret,"native_fault");
   else nt_auto_ended(nt);
  }
  return;
 }
 if(a->phase!=1){mutex_unlock(&nt->panel_lock);mutex_unlock(&nt->lock);return;}
 a->w_settle=nt_now_ms(nt)-a->enabled_ms;
 mutex_unlock(&nt->panel_lock);
 if(!nt_auto_valid(nt,gen)){mutex_unlock(&nt->lock);return;}
 nt_auto_stop(nt);
 if(!nt_auto_valid(nt,gen)){mutex_unlock(&nt->lock);return;}
 mutex_lock(&s->lock);
 /* A storm is sticky only within the managed session's panel generation. */
 if(s->managed && s->generation!=gen)s->poisoned=false;
 poison=s->poisoned;mutex_unlock(&s->lock);
 if(poison){mutex_unlock(&nt->lock);nt_auto_failure(nt,gen,-EIO,"poisoned");return;}
 mutex_lock(&nt->panel_lock);
 nt->download_gen=gen;a->phase=2;
 skip=READ_ONCE(skip_probe) && a->fw_lost;
 if(skip){a->probe_skips++;a->w_probe=0;}
 mutex_unlock(&nt->panel_lock);
 mutex_unlock(&nt->lock);

 alive=false;
 if(!skip){
  stage_start=nt_now_ms(nt);
  ret=nt_auto_probe(nt,gen,0,&alive,&probe_started);
  mutex_lock(&nt->panel_lock);
  if(a->queued_gen==gen)a->w_probe=nt_now_ms(nt)-stage_start;
  mutex_unlock(&nt->panel_lock);
  if(ret){if(ret!=-ECANCELED)nt_auto_failure(nt,gen,ret,probe_started?"probe_fault":"probe_start");return;}
 }

 mutex_lock(&nt->lock);
 if(!nt_auto_valid(nt,gen)){mutex_unlock(&nt->lock);return;}
 if(alive){
  mutex_lock(&nt->panel_lock);a->fw=1;a->alive_hits++;if(nt->gen==gen && nt->panel_on && !READ_ONCE(nt->quiesce))a->fw_lost=false;a->w_dl=0;a->w_boot=0;mutex_unlock(&nt->panel_lock);
  nt->state=NT_DONE;nt->download_gen=gen;nt->aborted=false;nt->error=nt->last_error=0;nt->started=true;
 }else{
  mutex_lock(&nt->panel_lock);
  if(a->attempted && a->attempted_gen==gen){mutex_unlock(&nt->panel_lock);mutex_unlock(&nt->lock);return;}
  a->attempted=true;a->attempted_gen=gen;a->reloads++;a->phase=3;
  mutex_unlock(&nt->panel_lock);
#ifdef THP_HOST_TEST
  nt_host_auto_boundary(nt,3);
#endif
  if(!nt_auto_valid(nt,gen)){mutex_unlock(&nt->lock);return;}
  /* Only execution context resets here. Manual used/armed token is untouched. */
  nt->state=NT_IDLE;nt->started=false;nt->verified=false;nt->aborted=false;
  nt->error=0;nt->steps=nt->reads=0;nt->current_step=-1;
  memset(nt->values,0,sizeof(nt->values));memset(nt->value_valid,0,sizeof(nt->value_valid));
  WRITE_ONCE(a->internal_download,true);
  stage_start=nt_now_ms(nt);
  ret=nt_get_firmware(nt,&fw);
  if(!ret){ret=nt_execute_blob(nt,fw->data,fw->size);nt_put_firmware(fw);}
  WRITE_ONCE(a->internal_download,false);
  mutex_lock(&nt->panel_lock);
  if(a->queued_gen==gen)a->w_dl=nt_now_ms(nt)-stage_start;
  mutex_unlock(&nt->panel_lock);
  if(!nt_auto_valid(nt,gen)){mutex_unlock(&nt->lock);return;}
  mutex_lock(&nt->panel_lock);
  a->download_end_ms=nt_now_ms(nt);a->boot_ms=0;
  a->d18_valid=nt->value_valid[4];
  if(a->d18_valid)a->d18=nt->values[4][0];
  mutex_unlock(&nt->panel_lock);
  /* Executor alone enforces the audited D18 a0..af range. */
  if(!ret && (nt->steps!=46 || nt->error || !nt->value_valid[4]))ret=-EPROTO;
  nt->last_error=ret;
  if(ret){mutex_unlock(&nt->lock);nt_auto_failure(nt,gen,ret,"download");return;}
  mutex_lock(&nt->panel_lock);a->phase=6;mutex_unlock(&nt->panel_lock);
  mutex_unlock(&nt->lock);
  ret=nt_auto_probe(nt,gen,a->download_end_ms+a->boot_wait_ms,&alive,&probe_started);
  if(ret || !alive){
   if(ret!=-ECANCELED)nt_auto_failure(nt,gen,ret?ret:-ETIMEDOUT,ret?"boot_fault":"boot_timeout");
   return;
  }
  mutex_lock(&nt->lock);
  if(!nt_auto_valid(nt,gen)){mutex_unlock(&nt->lock);return;}
  mutex_lock(&nt->panel_lock);a->fw=2;if(nt->gen==gen && nt->panel_on && !READ_ONCE(nt->quiesce))a->fw_lost=false;mutex_unlock(&nt->panel_lock);
 }
 mutex_lock(&nt->panel_lock);a->phase=4;a->first_pending=true;mutex_unlock(&nt->panel_lock);
 mutex_unlock(&nt->lock);
#ifdef THP_HOST_TEST
 nt_host_auto_boundary(nt,4);
#endif
 if(!nt_auto_valid(nt,gen))return;
 ret=nt_session_start_internal(nt,"",0,true,true,false,gen,0);
 if(ret<0){nt_auto_stop(nt);nt_auto_failure(nt,gen,ret,"native_start");}
}

/* control + panel_lock held. Exactly one delayed_work; enabled resets delay. */
static void nt_auto_queue(struct nt_thp *nt)
{
 struct nt_auto *a=&nt->auto_state;
 if(!READ_ONCE(a->enabled) || a->removed || !nt->panel_on || nt->dead)return;
 a->queued_gen=nt->gen;a->enabled_ms=nt_now_ms(nt);a->phase=1;
 a->recovery_attempts=0;a->recovering=false;
 a->settle_ms=READ_ONCE(settle_ms);
 a->w_prep_gap=a->prep_gap;
 a->w_settle=a->w_probe=a->w_dl=a->w_boot=a->w_first=a->w_off=-1;
 WRITE_ONCE(nt->quiesce,false);
 mod_delayed_work(a->wq,&a->work,msecs_to_jiffies(a->settle_ms));
}

static int nt_auto_event(struct nt_thp *nt,unsigned int event)
{
 struct nt_auto *a=&nt->auto_state;
 u64 off_start=nt_now_ms(nt);
 mutex_lock(&a->control);
 mutex_lock(&nt->panel_lock);
 if(event==0){a->prepared_ms=nt_now_ms(nt);a->prepared_valid=true;}
 else if(event==1){
  a->prep_gap=a->prepared_valid?(s64)(nt_now_ms(nt)-a->prepared_ms):-1;
  a->prepared_valid=false;
 }else if(event==3)a->fw_lost=true;
 mutex_unlock(&nt->panel_lock);
 if(!nt_auto_active(nt)){
  if(event==0)nt_panel_prepared_legacy(&nt->follower);
  else if(event==1)nt_panel_enabled_legacy(&nt->follower);
  else nt_panel_stopping_legacy(&nt->follower);
  if(event>=2){mutex_lock(&nt->panel_lock);a->w_off=nt_now_ms(nt)-off_start;mutex_unlock(&nt->panel_lock);}
  mutex_unlock(&a->control);return 0;
 }
 mutex_lock(&nt->panel_lock);
 if(event==0)nt->gen++;
 else if(event==1){nt->panel_on=true;nt->gen++;a->down_seen=false;nt_auto_queue(nt);}
 else{
  nt->panel_on=false;nt->armed=false;WRITE_ONCE(nt->quiesce,true);
  if(!a->down_seen){nt->gen++;a->down_seen=true;}
  a->fw=0;a->phase=0;a->first_pending=false;a->last_reason="panel_off";
 }
 mutex_unlock(&nt->panel_lock);
 if(event>=2){
  cancel_delayed_work_sync(&a->work);
  nt_auto_stop(nt);
  /* Synchronize a download which left work early at a generation boundary. */
  mutex_lock(&nt->lock);mutex_unlock(&nt->lock);
  mutex_lock(&nt->panel_lock);a->w_off=nt_now_ms(nt)-off_start;mutex_unlock(&nt->panel_lock);
 }
 mutex_unlock(&a->control);return 0;
}

static int nt_auto_init(struct nt_thp *nt,bool initial,unsigned int settle,unsigned int boot_wait)
{
 struct nt_auto *a=&nt->auto_state;
 if(settle<100 || settle>5000 || boot_wait<1000 || boot_wait>120000)return -EINVAL;
 a->prep_gap=a->w_prep_gap=a->w_settle=a->w_probe=a->w_dl=a->w_boot=a->w_first=a->w_off=-1;
 mutex_init(&a->control);a->nt=nt;a->settle_ms=settle;a->boot_wait_ms=boot_wait;a->last_reason="idle";
 INIT_DELAYED_WORK(&a->work,nt_auto_work);
 a->wq=alloc_ordered_workqueue("nt36523-thp",WQ_MEM_RECLAIM);
 if(!a->wq)return -ENOMEM;
 WRITE_ONCE(a->enabled,initial);nt->auto_initialized=true;return 0;
}

static ssize_t nt_auto_control(struct nt_thp *nt,const char *buf,size_t n)
{
 struct nt_auto *a=&nt->auto_state;
 bool on;
 int ret=0;
 if(n!=11 || (memcmp(buf,"THP-AUTO 1\n",11) && memcmp(buf,"THP-AUTO 0\n",11)))return -EINVAL;
 if(!nt->auto_initialized)return -ENODEV;
 on=buf[9]=='1';
 mutex_lock(&a->control);
 if(a->removed){ret=-ESHUTDOWN;goto out;}
 if(on){
  if(READ_ONCE(a->enabled))goto out;
  if(!mutex_trylock(&nt->lock)){ret=-EBUSY;goto out;}
  mutex_lock(&nt->stream->lock);
  if(nt->stream->state==NT_S_RUNNING || nt->stream->reader)ret=-EBUSY;
  else if(nt->stream->poisoned)ret=-EIO;
  mutex_lock(&nt->panel_lock);
  if(nt->dead)ret=-ESHUTDOWN;
  else if(a->sessions>=65535)ret=-EDQUOT;
  if(!ret){WRITE_ONCE(a->enabled,true);nt_auto_queue(nt);}
  mutex_unlock(&nt->panel_lock);mutex_unlock(&nt->stream->lock);mutex_unlock(&nt->lock);
 }else{
  if(!READ_ONCE(a->enabled))goto out;
  mutex_lock(&nt->panel_lock);WRITE_ONCE(a->enabled,false);WRITE_ONCE(nt->quiesce,true);mutex_unlock(&nt->panel_lock);
  cancel_delayed_work_sync(&a->work);nt_auto_stop(nt);
  mutex_lock(&nt->lock);
  mutex_lock(&nt->panel_lock);
  a->phase=0;a->first_pending=false;a->last_reason="auto_off";
  if(nt->panel_on && !nt->dead)WRITE_ONCE(nt->quiesce,false);
  mutex_unlock(&nt->panel_lock);mutex_unlock(&nt->lock);
 }
out:
 mutex_unlock(&a->control);return ret?ret:(ssize_t)n;
}

static void nt_auto_destroy(struct nt_thp *nt)
{
 struct nt_auto *a=&nt->auto_state;
 if(!nt->auto_initialized)return;
 mutex_lock(&a->control);
 if(!a->removed){
  mutex_lock(&nt->panel_lock);a->removed=true;WRITE_ONCE(a->enabled,false);WRITE_ONCE(nt->quiesce,true);mutex_unlock(&nt->panel_lock);
  cancel_delayed_work_sync(&a->work);nt_auto_stop(nt);
  destroy_workqueue(a->wq);a->wq=NULL;
 }
 mutex_unlock(&a->control);
}

#ifndef THP_HOST_TEST
static ssize_t managed_show(struct device *dev,struct device_attribute *attr,char *buf)
{ return sysfs_emit(buf,"%u\n",nt_auto_active(dev_get_drvdata(dev))?1:0); }
static ssize_t managed_store(struct device *dev,struct device_attribute *attr,const char *buf,size_t n)
{ return nt_auto_control(dev_get_drvdata(dev),buf,n); }
/* Caller holds s->lock; panel_lock is the last lock in the hierarchy. */
static ssize_t nt_auto_status(struct nt_thp *nt,char *buf,ssize_t at)
{
 static const char *const fw[]={"unknown","alive","loaded","failed"};
 struct nt_auto *a=&nt->auto_state;
 char d18[3];
 mutex_lock(&nt->panel_lock);nt_auto_d18(a,d18);
 /* Keep original fields unchanged, append to the original single status line. */
 if(at>0 && buf[at-1]=='\n')at--;
 at+=sysfs_emit_at(buf,at," auto=%u fw=%s resumes=%llu reloads=%llu alive_hits=%llu failures=%llu last_resume_ms=%llu last_reason=%s boot_ms=%llu boot_max_ms=%llu d18=%s\n",
   nt_auto_active(nt)?1:0,fw[a->fw],a->resumes,a->reloads,a->alive_hits,a->failures,a->last_resume_ms,a->last_reason,a->boot_ms,a->boot_max_ms,d18);
 mutex_unlock(&nt->panel_lock);return at;
}
#endif

/* s->lock held in production. Existing panel_lock protects the whole record. */
static ssize_t __attribute__((unused)) nt_auto_wake_status(struct nt_thp *nt,char *buf,ssize_t at)
{
 struct nt_auto *a=&nt->auto_state;
 mutex_lock(&nt->panel_lock);
 if(at>0 && buf[at-1]=='\n')at--;
 at+=sysfs_emit_at(buf,at," w_prep_gap=%lld w_settle=%lld w_probe=%lld w_dl=%lld w_boot=%lld w_first=%lld w_off=%lld probe_skips=%llu\n",
  a->w_prep_gap,a->w_settle,a->w_probe,a->w_dl,a->w_boot,a->w_first,a->w_off,a->probe_skips);
 mutex_unlock(&nt->panel_lock);return at;
}
