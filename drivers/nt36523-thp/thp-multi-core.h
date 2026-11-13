/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef THP_MULTI_CORE_H
#define THP_MULTI_CORE_H
/* Requires the immutable ta/tc/tf headers. Caller owns all storage.
 * No input/SPI, allocation, floating point, VLA or large per-frame stack. */
#define TM_MAX 5
#define TM_LOW 300
#define TM_HIGH 600
#define TM_MIN_SIZE 3
#define TM_PEAK_CAP 32
#define TM_SPLIT_DISTANCE2 9
#define TM_SADDLE_PERCENT 70
#define TM_PALM_AREA 28
#define TM_PALM_LONG 9
#define TM_PALM_LONG_AREA 20
#define TM_MATCH_Q8 1024
#define TM_MERGE_HOLD 120
#define TM_MERGE_MIN_AREA 11
#define TM_LOCK_UNITS 102
#define TM_MAX_AGE 2147483647
#define TM_CENTROID_RADIUS 2
#define TM_SUM_NUM 33
#define TM_SUM_DEN 32
#define TM_PREDICT_MAX 256
#define TM_WORDS 224
static const struct ta_map tm_native_map={0,0,0,3276800,1638400,3355443,1677722,0,1599,0,2559};
struct tm_block {s32 area,left,right,top,bottom;bool palm;};
struct tm_peak {s32 i,x,y,value,label;bool eligible;};
struct tm_track {
 bool live;s32 age,hold,cx,cy,vx,vy,raw_x,raw_y,final_x,final_y,area,amplitude;
 u32 sum;struct tc_core confirm;struct tf_core filter;
};
struct tm_point {s32 id,raw_x,raw_y,final_x,final_y,sum,area,amplitude,cx,cy,held,age;};
struct tm_result {s32 count,peaks,palm_components,ignored;struct tm_point points[TM_MAX];};
struct tm_config {bool confirm,filter;unsigned int lock_units;const struct ta_map *map;};
struct tm_core {
 struct tm_config config;
 struct tm_track tracks[TM_MAX];
 /* Workspace is persistent caller storage, never a kernel stack local. */
 short labels[1600];u16 queue[1600];struct tm_block blocks[1600];
 struct tm_peak peaks[TM_PEAK_CAP];s32 block_count,peak_count,palm_peaks,overflow;
};
static inline void tm_init(struct tm_core *c){memset(c,0,sizeof(*c));c->config=(struct tm_config){true,true,TM_LOCK_UNITS,NULL};}
static inline s64 tm_distance(s64 x,s64 y,s64 a,s64 b)
{s64 dx=x-a,dy=y-b;return dx*dx+dy*dy;}
static inline s32 tm_abs(s32 v){return v<0?-v:v;}
static inline void tm_components(struct tm_core *c,const s32 *s)
{
 int i,x,y,xx,yy,n,head,tail,label;c->block_count=0;
 for(i=0;i<1600;i++)c->labels[i]=-1;
 for(i=0;i<1600;i++){
  struct tm_block *b;
  if(s[i]<TM_LOW||c->labels[i]>=0)continue;
  label=c->block_count++;b=&c->blocks[label];x=i&31;y=i>>5;
  *b=(struct tm_block){0,x,x,y,y,false};head=0;tail=1;c->queue[0]=(u16)i;c->labels[i]=(short)label;
  while(head<tail){
   n=c->queue[head++];x=n&31;y=n>>5;b->area++;
   if(x<b->left)b->left=x;
   if(x>b->right)b->right=x;
   if(y<b->top)b->top=y;
   if(y>b->bottom)b->bottom=y;
   for(yy=y-1;yy<=y+1;yy++)for(xx=x-1;xx<=x+1;xx++){
    if(xx<0||xx>=32||yy<0||yy>=50)continue;
    n=(yy<<5)+xx;if(s[n]<TM_LOW||c->labels[n]>=0)continue;
    c->labels[n]=(short)label;c->queue[tail++]=(u16)n;
   }
  }
  b->palm=b->area>=TM_PALM_AREA || (b->area>=TM_PALM_LONG_AREA &&
   (b->right-b->left+1>=TM_PALM_LONG||b->bottom-b->top+1>=TM_PALM_LONG));
 }
}
static inline void tm_candidates(struct tm_core *c,const s32 *s)
{
 int i,x,y,xx,yy,j,k,size,n;bool maximum;c->peak_count=c->palm_peaks=c->overflow=0;
 for(i=0;i<1600;i++){
  struct tm_peak p;
  if(s[i]<TM_HIGH)continue;
  x=i&31;y=i>>5;maximum=true;size=0;
  for(yy=y-1;yy<=y+1;yy++)for(xx=x-1;xx<=x+1;xx++){
   if(xx<0||xx>=32||yy<0||yy>=50)continue;
   n=(yy<<5)+xx;if(s[n]>=TM_LOW)size++;
   if(s[n]>s[i]||(s[n]==s[i]&&n<i))maximum=false;
  }
  if(!maximum||size<TM_MIN_SIZE)continue;
  if(c->blocks[c->labels[i]].palm){c->palm_peaks++;continue;}
  p=(struct tm_peak){i,x,y,s[i],c->labels[i],true};j=0;
  while(j<c->peak_count && (c->peaks[j].value>p.value ||
        (c->peaks[j].value==p.value&&c->peaks[j].i<p.i)))j++;
  if(c->peak_count==TM_PEAK_CAP)c->overflow++;
  else c->peak_count++;
  if(j>=TM_PEAK_CAP)continue;
  /* Explicit bounded stores prevent GCC synthesizing a new memmove import.
   * Only this private scratch copy is volatile; detection/order are unchanged. */
  for(k=c->peak_count-1;k>j;k--)*(volatile struct tm_peak *)&c->peaks[k]=c->peaks[k-1];
  c->peaks[j]=p;
 }
 for(j=0;j<c->peak_count;j++)for(i=0;i<j;i++){
  struct tm_peak *a=&c->peaks[i],*b=&c->peaks[j];s32 dx,dy,steps,saddle,minimum;
  if(!a->eligible||a->label!=b->label)continue;
  dx=b->x-a->x;dy=b->y-a->y;steps=tm_abs(dx)>tm_abs(dy)?tm_abs(dx):tm_abs(dy);
  saddle=b->value;minimum=a->value<b->value?a->value:b->value;
  if(steps)for(k=0;k<=steps;k++){
   xx=a->x+(s32)ta_round((s64)dx*k,steps);yy=a->y+(s32)ta_round((s64)dy*k,steps);
   n=s[(yy<<5)+xx];if(n<saddle)saddle=n;
  }
  if((s64)dx*dx+(s64)dy*dy<TM_SPLIT_DISTANCE2 ||
     (s64)saddle*100>(s64)minimum*TM_SADDLE_PERCENT){b->eligible=false;break;}
 }
}
static inline void tm_step(struct tm_core *c,const s32 signal[1600],struct tm_result *out)
{
 int assignment[TM_MAX],id,j,k,iteration,x,y,xx,yy,label,free_id;
 bool used[TM_PEAK_CAP]={false},old[TM_MAX],held[TM_MAX]={false};
 memset(out,0,sizeof(*out));tm_components(c,signal);tm_candidates(c,signal);
 out->peaks=c->peak_count;out->ignored=c->overflow;
 for(j=0;j<c->block_count;j++)if(c->blocks[j].palm)out->palm_components++;
 for(id=0;id<TM_MAX;id++){assignment[id]=-1;old[id]=c->tracks[id].live;}
 for(iteration=0;iteration<TM_MAX;iteration++){
  s64 best=(s64)TM_MATCH_Q8*TM_MATCH_Q8+1;int best_id=-1,best_peak=-1;
  for(id=0;id<TM_MAX;id++){
   struct tm_track *t=&c->tracks[id];
   if(!t->live||assignment[id]>=0)continue;
   for(j=0;j<c->peak_count;j++){
    struct tm_peak *p=&c->peaks[j];s64 d;
    if(used[j])continue;
    d=tm_distance((p->x<<8)+128,(p->y<<8)+128,(s64)t->cx+t->vx+128,(s64)t->cy+t->vy+128);
    if(d<best){best=d;best_id=id;best_peak=j;}
   }
  }
  if(best_id<0)break;
  assignment[best_id]=best_peak;used[best_peak]=true;
 }
 for(id=0;id<TM_MAX;id++){
  struct tm_track *t=&c->tracks[id];bool shared=false;
  if(!t->live||assignment[id]>=0)continue;
  x=ta_clamp(ta_round(t->cx,256),0,31);y=ta_clamp(ta_round(t->cy,256),0,49);label=c->labels[(y<<5)+x];
  if(label>=0&&!c->blocks[label].palm&&c->blocks[label].area>=TM_MERGE_MIN_AREA)
   for(j=0;j<c->peak_count;j++)if(used[j]&&c->peaks[j].label==label)shared=true;
  if(shared&&t->hold<TM_MERGE_HOLD){held[id]=true;t->hold++;if(t->age<TM_MAX_AGE)t->age++;}
  else memset(t,0,sizeof(*t));
 }
 for(j=0;j<c->peak_count;j++){
  if(used[j]||!c->peaks[j].eligible)continue;
  free_id=-1;for(id=0;id<TM_MAX;id++)if(!c->tracks[id].live){free_id=id;break;}
  if(free_id<0){out->ignored++;continue;}
  c->tracks[free_id].live=true;old[free_id]=false;assignment[free_id]=j;used[j]=true;
 }
 for(id=0;id<TM_MAX;id++){
  struct tm_track *t=&c->tracks[id];struct tm_peak *p;s64 weight=0,sx=0,sy=0,sum=0;bool confirmed;
  s32 area=0,cx,cy; j=assignment[id];if(j<0)continue;p=&c->peaks[j];
  for(yy=p->y-TM_CENTROID_RADIUS;yy<=p->y+TM_CENTROID_RADIUS;yy++)for(xx=p->x-TM_CENTROID_RADIUS;xx<=p->x+TM_CENTROID_RADIUS;xx++){
   int owner=-1;s64 best=0; s32 value;
   if(xx<0||xx>=32||yy<0||yy>=50)continue;
   for(k=0;k<c->peak_count;k++)if(used[k]){
    s64 d=tm_distance(xx,yy,c->peaks[k].x,c->peaks[k].y);
    if(owner<0||d<best){owner=k;best=d;}
   }
   if(owner!=j)continue;
   value=signal[(yy<<5)+xx];
   if(value>TM_LOW){weight+=value;sx+=(s64)xx*value;sy+=(s64)yy*value;area++;}
   if(value>0&&tm_abs(xx-p->x)+tm_abs(yy-p->y)<=TM_CENTROID_RADIUS)sum+=value;
  }
  if(!weight)continue;
  cx=(s32)(((sx*65536/weight)+128)>>8);cy=(s32)(((sy*65536/weight)+128)>>8);
  if(old[id]){t->vx=ta_clamp(cx-t->cx,-TM_PREDICT_MAX,TM_PREDICT_MAX);t->vy=ta_clamp(cy-t->cy,-TM_PREDICT_MAX,TM_PREDICT_MAX);}
  else t->vx=t->vy=0;
  t->cx=cx;t->cy=cy;ta_map_point(c->config.map?c->config.map:&tm_native_map,cx,cy,&t->raw_x,&t->raw_y);
  t->hold=0;if(t->age<TM_MAX_AGE)t->age++;t->area=area;t->sum=(u32)((sum*TM_SUM_NUM+TM_SUM_DEN/2)/TM_SUM_DEN);t->amplitude=p->value;confirmed=t->confirm.confirmed;
  if(!c->config.confirm||tc_step(&t->confirm,t->sum)){
   if(c->config.filter){
   if((c->config.confirm&&!confirmed)||!t->filter.active)tf_down(&t->filter,(s32)((s64)t->raw_x*128/25),(s32)((s64)t->raw_y*5));
   else tf_step(&t->filter,(s32)((s64)t->raw_x*128/25),(s32)((s64)t->raw_y*5),c->config.lock_units);
   t->final_x=tf_round((s64)t->filter.fin[0]*25,128);t->final_y=tf_round(t->filter.fin[1],5);
   }else{t->final_x=t->raw_x;t->final_y=t->raw_y;}
  }
 }
 for(id=0;id<TM_MAX;id++){
  struct tm_track *t=&c->tracks[id];
  if(t->live&&(!c->config.confirm||t->confirm.confirmed))out->points[out->count++]=(struct tm_point){id,t->raw_x,t->raw_y,t->final_x,t->final_y,(s32)t->sum,t->area,t->amplitude,t->cx,t->cy,held[id],t->age};
 }
}
/* Stable host wire format includes every persistent tracking/confirm/filter word. */
static inline void tm_words(const struct tm_core *c,const struct tm_result *r,s32 words[TM_WORDS])
{
 int id,a,n=0;words[n++]=r->count;words[n++]=r->peaks;words[n++]=r->palm_components;words[n++]=r->ignored;
 for(id=0;id<TM_MAX;id++){
  const struct tm_point *p=&r->points[id];
  words[n++]=p->id;words[n++]=p->raw_x;words[n++]=p->raw_y;words[n++]=p->final_x;words[n++]=p->final_y;words[n++]=p->sum;
  words[n++]=p->area;words[n++]=p->amplitude;words[n++]=p->cx;words[n++]=p->cy;words[n++]=p->held;words[n++]=p->age;
 }
 for(id=0;id<TM_MAX;id++){
  const struct tm_track *t=&c->tracks[id];const struct tf_core *f=&t->filter;
  words[n++]=t->live;words[n++]=t->age;words[n++]=t->hold;words[n++]=t->cx;words[n++]=t->cy;words[n++]=t->vx;words[n++]=t->vy;
  words[n++]=t->raw_x;words[n++]=t->raw_y;words[n++]=t->final_x;words[n++]=t->final_y;words[n++]=t->area;words[n++]=t->amplitude;words[n++]=(s32)t->sum;words[n++]=(s32)t->confirm.prev_sum;words[n++]=t->confirm.confirmed;
  for(a=0;a<2;a++)words[n++]=f->anchor[a];
  for(a=0;a<2;a++)words[n++]=f->iir[a];
  for(a=0;a<2;a++)words[n++]=f->frac[a];
  for(a=0;a<2;a++)words[n++]=f->fin[a];
  for(a=0;a<2;a++)words[n++]=f->off[a];
  words[n++]=f->m;words[n++]=f->unlocked;words[n++]=f->far;words[n++]=f->offset_mode;words[n++]=f->first;words[n++]=f->active;
 }
}
#endif
