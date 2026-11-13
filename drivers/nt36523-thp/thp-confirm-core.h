/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef THP_CONFIRM_CORE_H
#define THP_CONFIRM_CORE_H
/* Fixed reference thresholds; prev_sum==0 means no prior positive sample. */
struct tc_core {u32 prev_sum;bool confirmed;};
static inline void tc_reset(struct tc_core *c)
{c->prev_sum=0;c->confirmed=false;}
static inline bool tc_step(struct tc_core *c,u32 sum)
{
 if(!sum){tc_reset(c);return false;}
 if(!c->confirmed&&sum>=3000U &&
    (sum>=8192U || (c->prev_sum && (u64)sum*4U<=(u64)c->prev_sum*5U)))
  c->confirmed=true;
 c->prev_sum=sum;return c->confirmed;
}
/* Read the existing algorithm signal, which retains its pre-follow baseline
 * difference. 13 points inside the radius-2 Manhattan diamond; strictly >0.
 * No algorithm state/output is changed. Round 33/32 to nearest, halves up. */
static inline u32 tc_signal_sum(const struct ta_core *c,const struct ta_result *r)
{
 int x,y;u32 sum=0;
 if(!r->accepted||!r->peak||r->reason!=TA_TOUCH)return 0;
 for(y=-2;y<=2;y++)for(x=-2;x<=2;x++){
  int xx=r->column+x,yy=r->row+y;s32 value;
  if((x<0?-x:x)+(y<0?-y:y)>2||xx<0||xx>=32||yy<0||yy>=50)continue;
  value=c->signal[yy*32+xx];if(value>0)sum+=(u32)value;
 }
 return (sum*33U+16U)/32U;
}
#endif
