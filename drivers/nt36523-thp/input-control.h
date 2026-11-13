/* SPDX-License-Identifier: GPL-2.0-only */
static ssize_t nt_input_control(struct nt_thp *nt,const char *buf,size_t n)
{
 struct nt_stream *s=nt->stream;bool rebase,off;int ret=0;
 if(nt_auto_active(nt))return -EBUSY;
 if(n==12&&!memcmp(buf,"THP-INPUT 1\n",12))return nt_session_start(nt,buf,n,true);
 off=n==12&&!memcmp(buf,"THP-INPUT 0\n",12);
 rebase=n==15&&!memcmp(buf,"THP-REBASELINE\n",15);
 if(!off&&!rebase)return -EINVAL;
 mutex_lock(&s->life);mutex_lock(&s->lock);
 if(nt_auto_active(nt))ret=-EBUSY;
 else if(s->state==NT_S_RUNNING&&!s->native)ret=-EBUSY;
 else if(rebase){
  mutex_lock(&nt->panel_lock);
  if(nt->state!=NT_DONE)ret=-EPERM;
  else if(READ_ONCE(nt->quiesce)||nt->dead||s->removed)ret=-ESHUTDOWN;
  else {
   struct ta_map map=s->algo->map;
   nt_input_up(s);ta_init(s->algo,8);s->algo->map=map;
  }
  mutex_unlock(&nt->panel_lock);
 }else if(s->native&&s->state==NT_S_RUNNING)nt_stream_stop(s,NT_S_INPUT_OFF);
 mutex_unlock(&s->lock);
 if(!ret&&off&&s->native)nt_stream_join(s);
 mutex_unlock(&s->life);
 return ret?ret:(ssize_t)n;
}
#ifndef THP_HOST_TEST
static ssize_t native_store(struct device *dev,struct device_attribute *attr,const char *buf,size_t n)
{ return nt_input_control(dev_get_drvdata(dev),buf,n); }
static ssize_t map_store(struct device *dev,struct device_attribute *attr,const char *buf,size_t n)
{ return nt_map_write(((struct nt_thp *)dev_get_drvdata(dev))->stream,buf,n); }
static ssize_t map_show(struct device *dev,struct device_attribute *attr,char *buf)
{
 struct nt_stream *s=((struct nt_thp *)dev_get_drvdata(dev))->stream;
 s64 values[11];ssize_t at=0;unsigned int i;
 mutex_lock(&s->lock);memcpy(values,&s->algo->map,sizeof(values));mutex_unlock(&s->lock);
 for(i=0;i<11;i++)at+=sysfs_emit_at(buf,at,"%s=%lld\n",nt_map_keys[i],values[i]);
 return at;
}
static ssize_t native_status_show(struct device *dev,struct device_attribute *attr,char *buf)
{
 struct nt_stream *s=((struct nt_thp *)dev_get_drvdata(dev))->stream;ssize_t ret;
 static const char * const states[]={"off","running","ended","aborted"};
 const char *state;
 mutex_lock(&s->lock);
 state=states[s->input_phase];
 ret=sysfs_emit(buf,"state=%s reason=%s frames=%llu accepted=%llu rejected=%llu down=%llu move=%llu up=%llu baseline=%s proc_count=%llu proc_max_us=%llu proc_over2ms=%llu proc_over6ms=%llu\n",
 state,nt_stream_reason_text(s->input_reason),s->input_frames,s->input_accepted,s->input_rejected,s->input_downs,s->input_moves,s->input_ups,
 s->algo->ready?"ready":"building",s->proc_count,s->proc_max_ns/1000,s->proc_over2ms,s->proc_over6ms);
 ret=nt_auto_status(s->nt,buf,ret);
 /* nt_auto_status terminates the existing single line with a newline. */
 if(ret>0 && buf[ret-1]=='\n')ret--;
 ret+=sysfs_emit_at(buf,ret," restarts=%llu neg_resets=%llu dups=%llu resyncs=%llu recoveries=%llu probe_irqs=%llu probe_frames=%llu last_bad=%s\n",
  s->algo->restarts,s->algo->neg_resets,s->dups,s->resyncs,s->recoveries,
  s->probe_irqs,s->probe_frames,nt_last_bad_text(s->last_bad));
 ret=nt_auto_wake_status(s->nt,buf,ret);
 if(ret>0 && buf[ret-1]=='\n')ret--;
 ret+=sysfs_emit_at(buf,ret," edge_px=%u filter=%u lock_px=%u confirm=%u multi=%u touches=%u guard=%u\n",READ_ONCE(edge_px),READ_ONCE(filter),READ_ONCE(lock_px),READ_ONCE(confirm),READ_ONCE(multi),s->touches,READ_ONCE(guard));
 mutex_unlock(&s->lock);return ret;
}
#endif
