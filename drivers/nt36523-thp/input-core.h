/* SPDX-License-Identifier: GPL-2.0-only */
/* s->lock held for mapping, algorithm, registration and all MT reporting. */
/* Panel-native pixels; algorithm default stays the immutable landscape oracle. */
static const struct ta_map nt_native_map = {
 0,0,0,3276800,1638400,3355443,1677722,0,1599,0,2559
};
/* Cached under s->lock, never recomputed in the per-frame path. The standard
 * touchscreen helper applies inversion/swap after this pre-property domain. */
static void nt_edge_recalc(struct nt_stream *s)
{
 s32 x,y;unsigned int i,j,a;
 s32 lo[2]={2147483647,2147483647},hi[2]={-2147483647,-2147483647};
 s->edge_max[0]=s->input_dev?(s32)s->prop.max_x:1599;
 s->edge_max[1]=s->input_dev?(s32)s->prop.max_y:2559;
 for(i=0;i<2;i++)for(j=0;j<2;j++){
  s32 v[2];ta_map_point(&s->algo->map,i?31*256:0,j?49*256:0,&x,&y);
  v[0]=x;v[1]=y;
  for(a=0;a<2;a++){if(v[a]<lo[a])lo[a]=v[a];if(v[a]>hi[a])hi[a]=v[a];}
 }
 for(a=0;a<2;a++){
  /* Only the portion reachable inside the advertised ABS domain is stretched. */
  s->edge_lo[a]=ta_clamp(lo[a],0,s->edge_max[a]);
  s->edge_hi[a]=ta_clamp(hi[a],0,s->edge_max[a]);
 }
}
static s32 nt_edge_axis(s32 v,s32 lo,s32 hi,s32 maximum,unsigned int width)
{
 s64 w=width,result=v;
 /* Disabled/degenerate must preserve every bit of the original path. */
 if(hi<=lo || !width)return v;
 if(w>(hi-lo)/2)w=(hi-lo)/2;
 if(!w)return v;
 if(v<=lo+w)result=ta_round(((s64)v-lo)*(lo+w),w);
 else if(v>=hi-w)result=hi-w+ta_round(((s64)v-(hi-w))*(maximum-(hi-w)),w);
 return (s32)ta_clamp(result,0,maximum);
}
static int nt_input_register(struct nt_stream *s)
{
 struct input_dev *d;
 int ret;
 if(s->input_dev)return 0;
#ifdef THP_HOST_TEST
 d=devm_input_allocate_device(NULL);
#else
 d=devm_input_allocate_device(&s->nt->spi->dev);
#endif
 if(!d)return -ENOMEM;
 d->name="nt36523-thp-touch";d->id.bustype=BUS_SPI;
#ifndef THP_HOST_TEST
 d->dev.parent=&s->nt->spi->dev;
#endif
 input_set_abs_params(d,ABS_MT_POSITION_X,0,1599,0,0);
 input_set_abs_params(d,ABS_MT_POSITION_Y,0,2559,0,0);
 input_abs_set_res(d,ABS_MT_POSITION_X,11);input_abs_set_res(d,ABS_MT_POSITION_Y,11);
 touchscreen_parse_properties(d,true,&s->prop);
 ret=input_mt_init_slots(d,TM_MAX,INPUT_MT_DIRECT);
 if(!ret){
  input_abs_set_res(d,ABS_X,11);input_abs_set_res(d,ABS_Y,11);
  ret=input_register_device(d);
 }
 if(ret){
#ifndef THP_HOST_TEST
  input_free_device(d);
#else
  free(d);host_input_devices--;
#endif
  return ret;
 }
 /* INPUT_MT_DIRECT copies ABS ranges and sets INPUT_PROP_DIRECT. */
 s->input_dev=d;nt_edge_recalc(s);return 0;
}
static void nt_input_up(struct nt_stream *s)
{
 unsigned int id;bool changed=false;
 tf_reset(&s->filter_state);tc_reset(&s->confirm_state);tm_init(&s->multi_state);tg_init(&s->guard_state);
 s->confirm_generation=READ_ONCE(confirm_generation);
 s->multi_generation=READ_ONCE(multi_generation);s->multi_mode=READ_ONCE(multi)!=0;
 if(s->multi_mode || s->touches){
  for(id=0;id<TM_MAX;id++)if(s->slot_active[id]){
   input_mt_slot(s->input_dev,id);
   input_mt_report_slot_state(s->input_dev,MT_TOOL_FINGER,false);
   s->slot_active[id]=false;s->input_ups++;changed=true;
  }
 }
 if(s->input_down&&!changed){
  input_mt_slot(s->input_dev,0);
  input_mt_report_slot_state(s->input_dev,MT_TOOL_FINGER,false);
  s->input_ups++;changed=true;
 }
 if(changed){input_mt_sync_frame(s->input_dev);input_sync(s->input_dev);}
 s->input_down=false;s->touches=0;
}
/* Preserve TG20's unfiltered, post-edge/property recording coordinates. This
 * is the pure projection in drivers/input/touchscreen.c:150-162, no events. */
static void nt_tap_unfiltered_position(struct nt_stream *s,const struct ta_result *r)
{
 unsigned int width=READ_ONCE(edge_px);u32 x,y,temp;
 if(!r->emitted||(r->state!=TA_DOWN&&r->state!=TA_MOVE))return;
 x=nt_edge_axis(r->mx,s->edge_lo[0],s->edge_hi[0],s->edge_max[0],width);
 y=nt_edge_axis(r->my,s->edge_lo[1],s->edge_hi[1],s->edge_max[1],width);
 if(s->prop.invert_x)x=s->prop.max_x-x;
 if(s->prop.invert_y)y=s->prop.max_y-y;
 if(s->prop.swap_x_y){temp=x;x=y;y=temp;}
 s->tap_input_x=(s32)x;s->tap_input_y=(s32)y;
}
/* Already tracked stable IDs are the slots: input_mt_assign_slots would
 * introduce a second matcher, potentially replacing the core's identity. */
static void nt_input_multi(struct nt_stream *s,const struct ta_result *r)
{
 struct tm_result result;bool present[TM_MAX]={false},fresh[TM_MAX]={false},changed=false;
 unsigned int i,id,width=READ_ONCE(edge_px);
 /* Bad frames are not disappearance observations; building is not ready. */
 if(!r->accepted || !s->algo->ready)return;
 s->multi_state.config=(struct tm_config){READ_ONCE(confirm)!=0,READ_ONCE(filter)!=0,
  (unsigned int)((u64)READ_ONCE(lock_px)*65536/12800),&s->algo->map};
 tm_step(&s->multi_state,s->algo->signal,&result);
 tg_apply(&s->guard_state,&s->multi_state,&result,READ_ONCE(guard)!=0);
 for(i=0;i<(unsigned int)result.count;i++){present[result.points[i].id]=true;fresh[result.points[i].id]=result.points[i].age==1;}
 for(id=0;id<TM_MAX;id++)if(s->slot_active[id]&&(!present[id]||fresh[id])){
  input_mt_slot(s->input_dev,id);input_mt_report_slot_state(s->input_dev,MT_TOOL_FINGER,false);
  s->slot_active[id]=false;s->input_ups++;changed=true;
 }
 for(i=0;i<(unsigned int)result.count;i++){
  const struct tm_point *p=&result.points[i];
  s32 x=nt_edge_axis(p->final_x,s->edge_lo[0],s->edge_hi[0],s->edge_max[0],width);
  s32 y=nt_edge_axis(p->final_y,s->edge_lo[1],s->edge_hi[1],s->edge_max[1],width);
  id=p->id;
  if(s->slot_active[id]&&x==s->slot_x[id]&&y==s->slot_y[id])continue;
  input_mt_slot(s->input_dev,id);input_mt_report_slot_state(s->input_dev,MT_TOOL_FINGER,true);
  touchscreen_report_pos(s->input_dev,&s->prop,x,y,true);
  if(s->slot_active[id])s->input_moves++;else s->input_downs++;
  s->slot_active[id]=true;s->slot_x[id]=x;s->slot_y[id]=y;changed=true;
  s->input_x=input_mt_get_value(&s->input_dev->mt->slots[id],ABS_MT_POSITION_X);
  s->input_y=input_mt_get_value(&s->input_dev->mt->slots[id],ABS_MT_POSITION_Y);
 }
 s->touches=result.count;
 if(changed){input_mt_sync_frame(s->input_dev);input_sync(s->input_dev);}
}
static void nt_input_emit(struct nt_stream *s,const struct ta_result *r)
{
 struct ta_result confirmed_result;
 s32 x=r->mx,y=r->my;unsigned int width;
 unsigned int generation=READ_ONCE(confirm_generation);
 bool enabled=READ_ONCE(filter)!=0,do_confirm=READ_ONCE(confirm)!=0;
 if(s->multi_generation!=READ_ONCE(multi_generation)){
  nt_input_up(s);
 }
 if(s->confirm_generation!=generation){
  /* If changing mode mid-contact, terminate the old reported contact once. */
  nt_input_up(s);s->confirm_generation=generation;
 }

 if(s->multi_mode){nt_tap_unfiltered_position(s,r);nt_input_multi(s,r);return;}

 if(r->state==TA_UP){tc_reset(&s->confirm_state);tf_reset(&s->filter_state);if(r->emitted)nt_input_up(s);return;}
 if(r->accepted && r->state==TA_IDLE)tc_reset(&s->confirm_state);
 if((r->state!=TA_DOWN&&r->state!=TA_MOVE)||x<0||y<0)return;
 nt_tap_unfiltered_position(s,r);
 if(do_confirm){
  bool was_confirmed=s->confirm_state.confirmed;
  /* Bad transport frames are not new signal observations. Signal absence
   * resets confirmation immediately; the existing algorithm still owns UP. */
  if(!r->accepted)return;
  if(!tc_step(&s->confirm_state,tc_signal_sum(s->algo,r)))return;
  if(!was_confirmed){
   confirmed_result=*r;confirmed_result.state=TA_DOWN;confirmed_result.emitted=1;
   r=&confirmed_result;
  }
 }
 if(enabled)tf_pixels(&s->filter_state,r->state==TA_DOWN,x,y,READ_ONCE(lock_px),&x,&y);
 /* Filter consumes EVERY positioned frame, even algorithm-deduplicated MOVE.
  * Bypass keeps the original emitted gate and reporter byte-for-byte behavior. */
 if(!enabled&&!r->emitted)return;
 width=READ_ONCE(edge_px);
 x=nt_edge_axis(x,s->edge_lo[0],s->edge_hi[0],s->edge_max[0],width);
 y=nt_edge_axis(y,s->edge_lo[1],s->edge_hi[1],s->edge_max[1],width);
 if(r->state==TA_MOVE&&s->input_down&&s->input_mapped_x==x&&s->input_mapped_y==y)return;
 input_mt_slot(s->input_dev,0);
 input_mt_report_slot_state(s->input_dev,MT_TOOL_FINGER,true);
 touchscreen_report_pos(s->input_dev,&s->prop,x,y,true);
 input_mt_sync_frame(s->input_dev);input_sync(s->input_dev);
 if(s->input_down)s->input_moves++;else s->input_downs++;
 s->input_down=true;s->touches=1;s->input_mapped_x=x;s->input_mapped_y=y;
 s->input_x=input_mt_get_value(&s->input_dev->mt->slots[0],ABS_MT_POSITION_X);
 s->input_y=input_mt_get_value(&s->input_dev->mt->slots[0],ABS_MT_POSITION_Y);
}
static void nt_input_process(struct nt_stream *s,const u8 *frame)
{
 struct ta_result result;
 u64 started=ktime_get_ns(),elapsed;
 ta_process(s->algo,frame,3257,&result);
#ifdef THP_HOST_TEST
 if(host_tap_result)host_tap_result(&result);
#endif
 if(result.reason==TA_BACKWARD && s->native)s->algo->have_counter=false;
 nt_input_emit(s,&result);
 elapsed=ktime_get_ns()-started;s->proc_count++;
 if(elapsed>s->proc_max_ns)s->proc_max_ns=elapsed;
 if(elapsed>2000000)s->proc_over2ms++;
 if(elapsed>6000000)s->proc_over6ms++;
 if(result.accepted)s->input_accepted++;else s->input_rejected++;
 if(result.accepted && s->managed)nt_auto_frame(s->nt);
 if(READ_ONCE(s->tap.active)){
  s32 reported_x=s->input_x,reported_y=s->input_y;
  /* stream->lock excludes observers; do not change tap format or algorithm r. */
  s->input_x=s->tap_input_x;s->input_y=s->tap_input_y;
  nt_tap_record(s,frame,&result);
  s->input_x=reported_x;s->input_y=reported_y;
 }
}
static const char * const nt_map_keys[]={"swap_axes","mirror_x","mirror_y","slope_x_q16","intercept_x_q16",
 "slope_y_q16","intercept_y_q16","minimum_x","maximum_x","minimum_y","maximum_y"};
/* No allocation/string conversion; bounded strict ASCII decimal parser. */
static int nt_map_parse(const char *buf,size_t n,struct ta_map *out)
{
 s64 values[11];unsigned int mask=0,key;size_t pos=0,start;bool negative;u64 v;
 if(!n||n>1024||buf[n-1]!='\n')return -EINVAL;
 memcpy(values,out,sizeof(values));
 while(pos<n){
  start=pos;
  while(pos<n&&buf[pos]!='='){
   if((unsigned char)buf[pos]<33||(unsigned char)buf[pos]>126||buf[pos]=='\n')return -EINVAL;
   pos++;
  }
  if(pos==n)return -EINVAL;
  for(key=0;key<11;key++)if(strlen(nt_map_keys[key])==pos-start&&!memcmp(buf+start,nt_map_keys[key],pos-start))break;
  if(key==11||(mask&(1U<<key)))return -EINVAL;
  mask|=1U<<key;pos++;negative=pos<n&&buf[pos]=='-';if(negative)pos++;
  start=pos;v=0;
  while(pos<n&&buf[pos]!='\n'){
   if(buf[pos]<'0'||buf[pos]>'9'||v>2147483648ULL)return -EINVAL;
   v=v*10+buf[pos++]-'0';
  }
  if(pos==start||pos==n||v>2147483648ULL)return -EINVAL;
  values[key]=negative?-(s64)v:(s64)v;pos++;
 }
 for(key=0;key<3;key++)if(values[key]<0||values[key]>1)return -EINVAL;
 for(key=3;key<7;key++)if(values[key]<-2147483648LL||values[key]>2147483647LL)return -EINVAL;
 for(key=7;key<11;key++)if(values[key]<0||values[key]>65535)return -EINVAL;
 if(values[7]>=values[8]||values[9]>=values[10])return -EINVAL;
 memcpy(out,values,sizeof(values));return 0;
}
static ssize_t nt_map_write(struct nt_stream *s,const char *buf,size_t n)
{
 struct ta_map candidate;int ret;
 mutex_lock(&s->lock);candidate=s->algo->map;
 ret=nt_map_parse(buf,n,&candidate);
 if(!ret){s->algo->map=candidate;nt_edge_recalc(s);}
 mutex_unlock(&s->lock);return ret?ret:(ssize_t)n;
}
