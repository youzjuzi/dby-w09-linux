/* SPDX-License-Identifier: GPL-2.0-only */
/* Host invariants against SYNTHETIC tables, not vendor data. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "dby-tfa9874-core.h"
_Static_assert(sizeof(dby_tfa_init)/sizeof(dby_tfa_init[0]) == DBY_TFA_INIT_COUNT, "init length");
_Static_assert(sizeof(dby_tfa_off)/sizeof(dby_tfa_off[0]) == DBY_TFA_OFF_COUNT, "off length");
static void equal(const struct dby_tfa9874_op *a, const struct dby_tfa9874_op *b)
{
 assert(a->reg==b->reg && a->mask==b->mask && a->val==b->val);
 assert(a->delay_ms==b->delay_ms && a->type==b->type);
}
int main(void)
{
 struct dby_tfa9874_op out[DBY_TFA_INIT_COUNT];
 unsigned count=0;
 for (unsigned chip=0;chip<4;chip++) {
  assert(dby_tfa9874_build_init(chip,out,DBY_TFA_INIT_COUNT)==DBY_TFA_INIT_COUNT);
  for (unsigned i=0;i<DBY_TFA_INIT_COUNT;i++) {
   struct dby_tfa9874_op expected=dby_tfa_init[i];
   if(expected.reg==0x26)expected.val=dby_tfa_slot4[chip];
   if(expected.reg==0x27)expected.val=dby_tfa_reg27[chip];
   equal(&out[i],&expected);
  }
  for (unsigned ch=0;ch<=5;ch++)
   for (unsigned step=0;step<=16;step++)
    for (unsigned atten=0;atten<2;atten++)
     for (unsigned safe=0;safe<2;safe++)
      for (unsigned mask=0;mask<=16;mask++) {
       int n=dby_tfa9874_build_power_on_seq(chip,ch,step,atten,safe,mask,out,DBY_TFA_INIT_COUNT);
       if (!ch || ch>4 || step<9 || step>15 || mask>15) assert(n==-EINVAL);
       else if (!safe || !(mask&(1U<<chip))) {
        assert(n==DBY_TFA_OFF_COUNT);
        for(int i=0;i<n;i++)equal(&out[i],&dby_tfa_off[i]);
       } else {
        const struct dby_tfa9874_op template[DBY_TFA_ON_COUNT]=DBY_TFA_ON_INITIALIZER(0,0,0);
        assert(n==DBY_TFA_ON_COUNT);
        assert(out[0].val==(atten?0x22d8:0x22c8));
        assert(out[1].val==(ch<=2?dby_tfa_slot2[chip]:dby_tfa_slot4[chip]));
        assert(out[2].val==((step<<6)|DBY_TFA_GAIN_LOW_BITS));
        assert(((out[2].val>>6)&15)>=9);
        for(int i=0;i<n;i++) {
         struct dby_tfa9874_op expected=template[i];
         if(i<3)expected.val=out[i].val;
         equal(&out[i],&expected);
        }
       }
       count++;
      }
 }
 assert(dby_tfa9874_build_init(4,out,DBY_TFA_INIT_COUNT)==-EINVAL);
 assert(dby_tfa9874_build_init(0,out,DBY_TFA_INIT_COUNT-1)==-EINVAL);
 assert(dby_tfa9874_build_init(0,NULL,DBY_TFA_INIT_COUNT)==-EINVAL);
 assert(dby_tfa9874_build_power_on_seq(4,2,15,1,1,15,out,DBY_TFA_INIT_COUNT)==-EINVAL);
 assert(dby_tfa9874_build_power_on_seq(0,2,15,1,1,15,NULL,DBY_TFA_INIT_COUNT)==-EINVAL);
 assert(dby_tfa9874_build_power_on_seq(0,2,15,1,1,15,out,DBY_TFA_ON_COUNT-1)==-EINVAL);
 assert(dby_tfa9874_build_power_on_seq(0,2,15,1,0,15,out,DBY_TFA_OFF_COUNT-1)==-EINVAL);
 puts("PASS: synthetic safety, slots, masks and capacity checks");
 assert(count==4*6*17*2*2*17);
}
