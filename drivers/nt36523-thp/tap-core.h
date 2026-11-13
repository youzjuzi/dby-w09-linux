/* SPDX-License-Identifier: GPL-2.0-only */
/* Lock order: stream->lock -> tap.lock; control -> tap.lock. No reverse edge.
 * Control/read never acquire stream->lock or perform SPI/input operations. */
static void nt_tap_init(struct nt_tap *t)
{
 spin_lock_init(&t->lock);mutex_init(&t->control);
}
static void nt_tap_put16(u8 *p,u16 v){p[0]=v;p[1]=v>>8;}
static void nt_tap_put32(u8 *p,u32 v)
{unsigned int i;for(i=0;i<4;i++)p[i]=v>>(8*i);}
static void nt_tap_put64(u8 *p,u64 v)
{unsigned int i;for(i=0;i<8;i++)p[i]=v>>(8*i);}
static inline ssize_t nt_tap_control(struct nt_tap *t,const char *buf,size_t count)
{
 unsigned long flags;u8 *next=NULL,*old=NULL;unsigned int command;
 if(count!=10 || memcmp(buf,"THP-TAP ",8) || buf[9]!='\n' || buf[8]<'0' || buf[8]>'2')return -EINVAL;
 command=buf[8]-'0';
 mutex_lock(&t->control);
 if(command==1){
  next=vzalloc((size_t)NT_TAP_RECORD*NT_TAP_CAPACITY);
  if(!next){mutex_unlock(&t->control);return -ENOMEM;}
 }
 spin_lock_irqsave(&t->lock,flags);
 if(command!=0){
  old=t->data;t->data=next;t->count=0;t->total=0;t->dropped=0;t->rejected=0;t->epoch++;
 }
 t->state=command==1?1:command==0?2:0;
 WRITE_ONCE(t->active,command==1);
 spin_unlock_irqrestore(&t->lock,flags);
 vfree(old);mutex_unlock(&t->control);return count;
}
static void nt_tap_reject(struct nt_tap *t)
{
 unsigned long flags;
 if(!READ_ONCE(t->active))return;
 spin_lock_irqsave(&t->lock,flags);
 if(t->active)t->rejected++;
 spin_unlock_irqrestore(&t->lock,flags);
}
/* Called with stream->lock, only after the unchanged algorithm and reporter.
 * Caller supplies the single disabled-path READ_ONCE branch. */
static void nt_tap_record(struct nt_stream *s,const u8 *frame,const struct ta_result *r)
{
 struct nt_tap *t=&s->tap;unsigned long irqflags;u8 *p;u16 flags=0;
 spin_lock_irqsave(&t->lock,irqflags);
 if(!t->active)goto out;
 if(!r->accepted){t->rejected++;goto out;}
 p=t->data+(t->total%NT_TAP_CAPACITY)*NT_TAP_RECORD;
 if(r->mx>=0 && r->my>=0)flags|=1;
 if(r->emitted)flags|=2;
 if(r->ready)flags|=4;
 memcpy(p,"NTAP",4);p[4]=1;p[5]=r->state;nt_tap_put16(p+6,flags);
 nt_tap_put32(p+8,(u32)t->total);nt_tap_put64(p+12,ktime_get_ns());
 nt_tap_put16(p+20,nt_le16(frame+NT_COUNT_OFFSET));
 nt_tap_put16(p+22,flags&1?(u16)s->input_x:65535);
 nt_tap_put16(p+24,flags&1?(u16)s->input_y:65535);
 nt_tap_put16(p+26,(u16)r->amplitude);nt_tap_put16(p+28,(u16)r->size);nt_tap_put16(p+30,0);
 memcpy(p+32,frame,NT_FRAME_SIZE); /* Exactly one raw-frame copy. */
 /* Seven padding bytes stay zero from vzalloc, including overwritten slots. */
#ifdef THP_HOST_TEST
 t->host_copies++;t->host_bytes+=NT_FRAME_SIZE;
#endif
 t->total++;
 if(t->count==NT_TAP_CAPACITY)t->dropped++;else t->count++;
out:
 spin_unlock_irqrestore(&t->lock,irqflags);
}
/* Each callback returns one WHOLE record. Immutable sequence/epoch cursors give
 * a bounded snapshot; overwrite/restart/reader eviction returns -ESTALE. No
 * file private_data or kernfs internals are repurposed; keys are never derefed.
 * Eight simultaneous sequential readers; a ninth evicts the oldest cursor. */
static inline ssize_t nt_tap_read(struct nt_tap *t,const void *key,char *buf,loff_t off,size_t count)
{
 struct nt_tap_cursor *c=NULL;unsigned long flags;unsigned int i;ssize_t ret;u64 seq;
 if(!count)return 0;
 if(off<0 || off%NT_TAP_RECORD || count<NT_TAP_RECORD)return -EINVAL;
 mutex_lock(&t->control);
 spin_lock_irqsave(&t->lock,flags);
 for(i=0;i<NT_TAP_READERS;i++)if(t->readers[i].key==key){c=&t->readers[i];break;}
 if(!off){
  if(!c)c=&t->readers[t->reader_next++%NT_TAP_READERS];
  c->key=key;c->epoch=t->epoch;c->first=t->total-t->count;c->limit=t->count;c->next=0;
 }
 if(!c || c->epoch!=t->epoch || (u64)off/NT_TAP_RECORD!=c->next){ret=-ESTALE;goto out;}
 if(c->next==c->limit){c->key=NULL;ret=0;goto out;}
 seq=c->first+c->next;
 if(!t->data || seq<t->total-t->count || seq>=t->total){ret=-ESTALE;goto out;}
 memcpy(buf,t->data+(seq%NT_TAP_CAPACITY)*NT_TAP_RECORD,NT_TAP_RECORD);
 c->next++;ret=NT_TAP_RECORD;
out:
 spin_unlock_irqrestore(&t->lock,flags);mutex_unlock(&t->control);return ret;
}
#ifndef THP_HOST_TEST
static ssize_t tap_store(struct device *dev,struct device_attribute *attr,const char *buf,size_t count)
{return nt_tap_control(&((struct nt_thp *)dev_get_drvdata(dev))->stream->tap,buf,count);}
static ssize_t tap_status_show(struct device *dev,struct device_attribute *attr,char *buf)
{
 struct nt_tap *t=&((struct nt_thp *)dev_get_drvdata(dev))->stream->tap;unsigned long flags;ssize_t ret;
 static const char * const states[]={"idle","recording","stopped"};
 spin_lock_irqsave(&t->lock,flags);
 ret=sysfs_emit(buf,"state=%s frames=%u dropped=%llu rejected=%llu capacity=2048\n",states[t->state],t->count,t->dropped,t->rejected);
 spin_unlock_irqrestore(&t->lock,flags);return ret;
}
static ssize_t tap_frames_read(struct file *file,struct kobject *kobj,const struct bin_attribute *attr,char *buf,loff_t off,size_t count)
{return nt_tap_read(&((struct nt_thp *)dev_get_drvdata(kobj_to_dev(kobj)))->stream->tap,file,buf,off,count);}
static struct bin_attribute nt_tap_attr={
 .attr={.name="tap_frames",.mode=0400},.size=(size_t)NT_TAP_RECORD*NT_TAP_CAPACITY,.read=tap_frames_read,
};
#endif
