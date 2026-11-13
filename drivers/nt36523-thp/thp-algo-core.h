/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef THP_ALGO_CORE_H
#define THP_ALGO_CORE_H
/* Types supplied by Linux or the host adapter. No allocation and no VLA. */
#define TA_N 1600
#define TA_WORDS 19
enum ta_reason { TA_TOUCH, TA_NO_PEAK, TA_JUMP, TA_LENGTH, TA_ZERO, TA_FF,
 TA_MAIN, TA_PEN, TA_REPEAT, TA_BACKWARD, TA_GAP, TA_UNSTABLE_STICKY,
 TA_UNSTABLE, TA_READY, TA_BUILDING, TA_REASONS };
enum ta_state { TA_IDLE, TA_DOWN, TA_MOVE, TA_UP };
static const char * const ta_reasons[] = { "touch", "no_peak", "jump_rejected",
 "length", "all_zero", "all_ff", "main_checksum", "pen_checksum",
 "counter_repeat", "counter_backward", "counter_gap", "baseline_unstable_sticky",
 "baseline_unstable", "baseline_ready", "baseline_building" };
/* Fixed signed 32-bit wire result for host differential tests. -1 is null. */
struct ta_result {
 s32 accepted, reason, counter, emitted, state;
 s32 peak, column, row, amplitude, size, cx, cy, px, py, mx, my;
 s32 followed, ready, unstable;
};
struct ta_map { s64 swap_axes, mirror_x, mirror_y, slope_x_q16, intercept_x_q16,
 slope_y_q16, intercept_y_q16, minimum_x, maximum_x, minimum_y, maximum_y; };
struct ta_core {
 u16 raw[TA_N], baseline[TA_N], minima[TA_N], maxima[TA_N];
 u32 sums[TA_N];
 s32 signal[TA_N];
 struct ta_map map;
 unsigned int k, count, missing;
 bool ready, unstable, have_counter, active, have_position;
 u16 counter;
 s32 x, y;
 u64 rejected[TA_REASONS];
 u64 restarts, neg_resets;
};
/* Positive divisor; callers exclude INT64_MIN and results stay in signed range. */
static inline s64 ta_floor_div(s64 a, s64 b)
{
 s64 q = a / b, rem = a % b;
 return q - (rem < 0);
}
static inline s64 ta_round(s64 a, s64 b)
{
 return a >= 0 ? ta_floor_div(a + b / 2, b) : -ta_floor_div(-a + b / 2, b);
}
static inline s64 ta_clamp(s64 a, s64 lo, s64 hi)
{
 return a < lo ? lo : (a > hi ? hi : a);
}
static inline void ta_default_map(struct ta_map *m)
{
 *m = (struct ta_map){ 1,0,0,3291218,4299162,-3303014,103271629,0,2559,0,1599 };
}
static inline void ta_init(struct ta_core *c, unsigned int k)
{
 unsigned int i;
 memset(c,0,sizeof(*c)); c->k=k;
 for(i=0;i<TA_N;i++)c->minima[i]=65535;
 ta_default_map(&c->map);
}
static inline u16 ta_le16(const u8 *p) { return (u16)(p[0] | ((u16)p[1]<<8)); }
static inline u16 ta_crc(const u8 *p, unsigned int n)
{
 u32 sum=0; unsigned int i;
 for(i=0;i<n;i+=2)sum+=ta_le16(p+i);
 return (u16)(0U-sum);
}
static inline void ta_map_point(const struct ta_map *m, s32 px, s32 py, s32 *mx, s32 *my)
{
 s64 x=m->swap_axes ? py : px, y=m->swap_axes ? px : py;
 /* Multiplication, not shifting negative signed coefficients (C undefined). */
 x=ta_round(m->slope_x_q16*x+m->intercept_x_q16*256,16777216);
 y=ta_round(m->slope_y_q16*y+m->intercept_y_q16*256,16777216);
 if(m->mirror_x)x=m->minimum_x+m->maximum_x-x;
 if(m->mirror_y)y=m->minimum_y+m->maximum_y-y;
 *mx=ta_clamp(x,m->minimum_x,m->maximum_x);
 *my=ta_clamp(y,m->minimum_y,m->maximum_y);
}
static inline void ta_process(struct ta_core *c, const u8 *data, unsigned int n, struct ta_result *r)
{
 unsigned int i; int x,y,nx,ny,bx=0,by=0,size=0,bsize=0;
 s32 best=-1; bool zero=true,ff=true,freeze=false; u16 count,delta;
 s64 weight=0,sx=0,sy=0; int error=-1;
 memset(r,0,sizeof(*r)); r->counter=-1; r->state=c->active ? TA_MOVE : TA_IDLE;
 r->cx=r->cy=r->px=r->py=r->mx=r->my=-1; r->followed=-1;
 if(n!=3257){error=TA_LENGTH;goto reject;}
 for(i=0;i<3257;i++){zero=zero && data[i]==0;ff=ff && data[i]==255;}
 if(zero){error=TA_ZERO;goto reject;}
 if(ff){error=TA_FF;goto reject;}
 if(ta_crc(data+6,3214)!=ta_le16(data+3220)){error=TA_MAIN;goto reject;}
 if(ta_crc(data+3222,30)!=ta_le16(data+3252)){error=TA_PEN;goto reject;}
 count=ta_le16(data+3218);
 if(c->have_counter){
  delta=(u16)(count-c->counter);
  if(!delta){error=TA_REPEAT;goto reject;}
  if(delta>=32768){error=TA_BACKWARD;goto reject;}
  if(delta!=1){c->counter=count;error=TA_GAP;goto reject;}
 }
 c->counter=count;c->have_counter=true;r->counter=count;
 for(i=0;i<TA_N;i++)c->raw[i]=ta_le16(data+6+2*i);
 if(!c->ready){
  unsigned int pp=0;
  c->count++;
  for(i=0;i<TA_N;i++){
   u16 v=c->raw[i];unsigned int diff;
   c->sums[i]+=v;
   if(v<c->minima[i])c->minima[i]=v;
   if(v>c->maxima[i])c->maxima[i]=v;
   diff=(u32)c->maxima[i]-c->minima[i];if(diff>pp)pp=diff;
  }
  if(pp>300){
   /* Discard the old window; retain this valid frame as the new first frame. */
   c->count=1;c->restarts++;
   for(i=0;i<TA_N;i++){
    c->sums[i]=c->raw[i];c->minima[i]=c->raw[i];c->maxima[i]=c->raw[i];
   }
   r->reason=TA_UNSTABLE;goto finish;
  }
  r->accepted=1;
  if(c->count==c->k){
   for(i=0;i<TA_N;i++)c->baseline[i]=ta_round(c->sums[i],c->count);
   c->ready=true;r->reason=TA_READY;
  }else r->reason=TA_BUILDING;
  goto finish;
 }
 for(i=0;i<TA_N;i++){
  c->signal[i]=(s32)c->raw[i]-(s32)c->baseline[i];
  if(c->signal[i]<=-300){
   c->baseline[i]=c->raw[i];c->signal[i]=0;c->neg_resets++;
  }
  if(c->signal[i]>=300)freeze=true;
 }
 for(y=0;y<50;y++)for(x=0;x<32;x++){
  s32 value=c->signal[y*32+x];bool maximum=true;
  if(value<600)continue;
  size=0;
  for(ny=y-1;ny<=y+1;ny++)for(nx=x-1;nx<=x+1;nx++){
   s32 v;
   if(nx<0||nx>=32||ny<0||ny>=50)continue;
   v=c->signal[ny*32+nx];if(v>=300)size++;
   if(nx==x&&ny==y)continue;
   if(v>value||(v==value&&(ny<y||(ny==y&&nx<x))))maximum=false;
  }
  /* Scanning order is row then column; equal best does not replace. */
  if(maximum&&size>=3&&value>best){best=value;bx=x;by=y;bsize=size;}
 }
 r->accepted=1;r->reason=TA_NO_PEAK;
 if(best>=0){
  for(ny=by-2;ny<=by+2;ny++)for(nx=bx-2;nx<=bx+2;nx++){
   s32 w;
   if(nx<0||nx>=32||ny<0||ny>=50)continue;
   w=c->signal[ny*32+nx];if(w<=300)continue;
   weight+=w;sx+=(s64)nx*w;sy+=(s64)ny*w;
  }
  r->peak=1;r->column=bx;r->row=by;r->amplitude=best;r->size=bsize;
  r->cx=((sx*65536/weight)+128)>>8;r->cy=((sy*65536/weight)+128)>>8;
  r->reason=TA_TOUCH;
  if(c->active){
   s64 dx=r->cx-c->x,dy=r->cy-c->y;
   if(dx*dx+dy*dy>2359296)r->reason=TA_JUMP;
  }
 }
 if(r->reason==TA_TOUCH){
  r->state=c->active ? TA_MOVE : TA_DOWN;
  c->x=r->cx;c->y=r->cy;c->have_position=true;c->active=true;c->missing=0;r->emitted=1;
 }else if(c->active){
  if(++c->missing>=2){r->state=TA_UP;c->active=false;c->missing=0;r->emitted=1;}
  else r->state=TA_MOVE;
 }else r->state=TA_IDLE;
 if(c->have_position&&(c->active||r->state==TA_UP)){
  r->px=c->x;r->py=c->y;ta_map_point(&c->map,c->x,c->y,&r->mx,&r->my);
 }
 r->followed=!freeze;
 if(!freeze)for(i=0;i<TA_N;i++){
  if(c->raw[i]>c->baseline[i])c->baseline[i]++;
  else if(c->raw[i]<c->baseline[i])c->baseline[i]--;
 }
 goto finish;
reject:
 c->rejected[error]++;r->reason=error;
finish:
 r->ready=c->ready;r->unstable=c->unstable;
}
#endif
