/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef THP_FILTER_CORE_H
#define THP_FILTER_CORE_H
/* Direct integer translation of filter_model.py; Linux/host supplies s32/s64/u64.
 * Positions fit mapped u16 pixels converted to internal units. All squares and
 * fixed-point products widen BEFORE multiplication. No allocation or I/O. */
struct tf_core {
 s32 anchor[2], iir[2], frac[2], fin[2], off[2], m;
 bool unlocked, far, offset_mode, first, active;
};
static inline s64 tf_abs(s64 v) { return v < 0 ? -v : v; }
static inline u64 tf_sqrt(u64 n)
{
 u64 r=0,b=1ULL<<30;
 /* Deliberately the same starting bit as the pinned Python model. */
 while(b>n)b>>=2;
 while(b){
  if(n>=r+b){n-=r+b;r=(r>>1)+b;}else r>>=1;
  b>>=2;
 }
 return r;
}
static inline void tf_reset(struct tf_core *f) { memset(f,0,sizeof(*f)); }
static inline void tf_down(struct tf_core *f,s32 x,s32 y)
{
 tf_reset(f);f->anchor[0]=f->iir[0]=f->fin[0]=x;
 f->anchor[1]=f->iir[1]=f->fin[1]=y;
 f->offset_mode=true;f->first=true;f->active=true;
}
static inline void tf_step(struct tf_core *f,s32 x,s32 y,unsigned int lock_units)
{
 s32 raw[2]={x,y},rp[2],next[2],fr[2],out[2];
 s64 dx=(s64)x-f->anchor[0],dy=(s64)y-f->anchor[1];
 u64 sq=dx*dx+dy*dy,dist;
 bool far_prev=f->far,small[2]={false,false};
 unsigned int a; s32 c,m;
 if(!lock_units){f->unlocked=true;f->offset_mode=false;}
 if(f->first){
  f->first=false;
  if(sq>500){f->unlocked=true;f->offset_mode=false;}
 }
 if(sq>(u64)lock_units*lock_units)f->unlocked=true;
 if(sq>307U*307U)f->far=true;
 if(!f->unlocked){
  for(a=0;a<2;a++)f->off[a]=raw[a]-f->iir[a];
  return;
 }
 for(a=0;a<2;a++){
  s64 v=(s64)raw[a]-f->off[a];
  rp[a]=f->offset_mode?(v>0?(s32)v:0):raw[a];
 }
 dx=(s64)rp[0]-f->iir[0];dy=(s64)rp[1]-f->iir[1];
 dist=tf_sqrt(dx*dx+dy*dy);
 c=dist>=384?32:(s32)((85*dist)>>10);
 if(!c&&dist)c=1;
 if(far_prev&&c<8)c=8;
 if(!f->m)f->m=c;
 if(f->m+1<c)f->m=c-1;
 else if(c+1<f->m)f->m=c+1;
 m=f->m;
 for(a=0;a<2;a++){
  s32 res=a?12800:8192;
  if((rp[a]==res-1||!rp[a])&&tf_abs((s64)rp[a]-f->iir[a])>49){
   next[a]=rp[a];fr[a]=0;
  }else{
   s64 o8=((32-m)*((s64)f->iir[a]*256+f->frac[a])+(s64)m*rp[a]*256)/32;
   next[a]=(s32)(o8>>8);fr[a]=(s32)(o8&255);
  }
  out[a]=next[a];
 }
 if(f->offset_mode){
  for(a=0;a<2;a++){
   s64 delta=tf_abs((s64)next[a]-f->iir[a])>>2;
   s32 step=delta<15?(s32)delta:15,o=f->off[a];
   if(o<step){
    if(o<=-step){out[a]=next[a]<step?0:next[a]-step;f->off[a]=step+o;}
    else small[a]=true;
   }else{out[a]=next[a]+step;f->off[a]=o-step;}
  }
  if(small[0]&&small[1])f->offset_mode=false;
 }
 for(a=0;a<2;a++){f->iir[a]=out[a];f->frac[a]=fr[a];}
 dx=(s64)out[0]-f->fin[0];dy=(s64)out[1]-f->fin[1];
 sq=dx*dx+dy*dy;dist=tf_sqrt(sq);
 if(dist&&sq>30U*30U){
  for(a=0;a<2;a++){
   s64 dd=(s64)out[a]-f->fin[a],u=dd*30/(s64)dist;
   if(tf_abs(u)<6){u=dd;if(tf_abs(dd)>=6)u=dd*6/tf_abs(dd);}
   f->fin[a]=out[a]-(s32)u;
  }
 }
}
/* Native pixel adapter: input conversion truncates; output rounds nearest,
 * halves away from zero, like ta_round. No state/algorithm result mutation. */
static inline s32 tf_round(s64 n,s64 d)
{ return (s32)(n>=0?(n+d/2)/d:-((-n+d/2)/d)); }
static inline void tf_pixels(struct tf_core *f,bool down,s32 x,s32 y,
 unsigned int lock_px,s32 *ox,s32 *oy)
{
 s32 ux=(s32)((s64)x*128/25),uy=y*5;
 if(down||!f->active)tf_down(f,ux,uy);
 else tf_step(f,ux,uy,(unsigned int)((u64)lock_px*65536/12800));
 *ox=tf_round((s64)f->fin[0]*25,128);*oy=tf_round(f->fin[1],5);
}
#endif
