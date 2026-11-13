/* SPDX-License-Identifier: GPL-2.0-only */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "dby-chg-core.h"
static struct dby_chg_sample good(void)
{
 struct dby_chg_sample s = {0};
 s.reg[CHG_ENABLE]=1; s.reg[CHG_FLOAT]=0x50; s.reg[CHG_USB]=1;
 s.temp_decic=250; s.voltage_uv=4400000; s.current_ua=100000; s.capacity=98;
 return s;
}
static enum dby_chg_action plan(struct dby_chg_control *c,
 struct dby_chg_sample *s, struct dby_chg_state *st, unsigned long long now)
{
 unsigned int reason;
 return dby_chg_plan(c,s,st,120,420,now,&reason);
}
static void commit(struct dby_chg_control *c, struct dby_chg_sample *s,
 enum dby_chg_action a, unsigned long long now)
{
 assert(a!=CHG_KEEP);
 dby_chg_toggle_attempt(c,now);
 unsigned int value=a==CHG_ON;
 assert(!dby_chg_toggle_result(c,a,0,value));
 s->reg[CHG_ENABLE]=value;
}
int main(void)
{
 struct dby_chg_sample s=good(); struct dby_chg_state st={0};
 struct dby_chg_control c={0}; unsigned int reason;
 for(unsigned int v=0;v<256;v++) {
  memset(&c,0,sizeof(c));s=good();s.reg[CHG_ENABLE]=v;
  assert(dby_chg_enable_probe(&c,0,v)==(v<2?0:-EINVAL));
  assert(dby_chg_enable_probe(&c,-EIO,v)==-EIO);
  enum dby_chg_action a=plan(&c,&s,&st,2000);
  assert(a==(v==0?CHG_ON:CHG_KEEP));
  assert(!!c.stopped==(v>1));
  if(v>1) assert(dby_chg_decide(&s,&st,2000,0,0)&CHG_BAD_ENABLE);
 }
 /* Each failed read/identity prohibits enable writes AND pets. */
 for(int i=0;i<CHG_NREG+5;i++) {
  c=(struct dby_chg_control){.initial_off=1};s=good();s.reg[CHG_ENABLE]=0;
  int *errors[]={&s.identity_error,&s.temp_error,&s.voltage_error,&s.current_error,&s.capacity_error};
  if(i<CHG_NREG)s.error[i]=-EIO;else *errors[i-CHG_NREG]=-EIO;
  assert(plan(&c,&s,&st,2000)==CHG_KEEP);assert(!c.want_enable);
  assert(dby_chg_decide(&s,&st,2000,0,0));assert(!c.count);
 }
 /* Policy gates: hardware temperature, float, USB, temperature, voltage, FULL. */
 for(int i=0;i<6;i++) {
  c=(struct dby_chg_control){0};s=good();st=(struct dby_chg_state){0};
  if(i==0)s.reg[CHG_HW_TEMP]=1;
  if(i==1)s.reg[CHG_FLOAT]=0x55;
  if(i==2)s.reg[CHG_USB]=0;
  if(i==3)s.temp_decic=421;
  if(i==4)s.voltage_uv=4430001;
  if(i==5)st.full=1;
  assert(plan(&c,&s,&st,2000)==(i==2?CHG_KEEP:CHG_OFF));assert(!c.want_enable);
  assert(!!dby_chg_decide(&s,&st,2000,0,0)==(i==1||i==2));
 }
 st=(struct dby_chg_state){0};
 int temperatures[]={120,129,130,420,411,410};
 for(unsigned int i=0;i<sizeof(temperatures)/sizeof(*temperatures);i++) {
  c=(struct dby_chg_control){0};s=good();s.temp_decic=temperatures[i];
  assert(plan(&c,&s,&st,2000)==CHG_KEEP&&c.want_enable);
  c.owned_off=1;s.reg[CHG_ENABLE]=0;
  assert(plan(&c,&s,&st,2000)==((i==2||i==5)?CHG_ON:CHG_KEEP));
 }
 int volts[]={4430000,4410000,4400000};
 for(int i=0;i<3;i++) {
  c=(struct dby_chg_control){0};s=good();s.voltage_uv=volts[i];
  assert(plan(&c,&s,&st,2000)==CHG_KEEP&&c.want_enable);
  c.owned_off=1;s.reg[CHG_ENABLE]=0;
  assert(plan(&c,&s,&st,2000)==(i==2?CHG_ON:CHG_KEEP));
 }
 /* USB unplug/replug does not consume runtime flips or change offline ownership. */
 for(int fast=0;fast<2;fast++) {
  c=(struct dby_chg_control){0};s=good();
  assert(plan(&c,&s,&st,0)==CHG_KEEP&&c.want_enable);
  for(unsigned int i=1;i<=50;i++) {
   unsigned long long now=i*(fast?2000:60000);
   struct dby_chg_control saved=c;
   s.reg[CHG_USB]=0;
   assert(dby_chg_policy(&c,&s,&st,120,420)&CHG_ABSENT);
   assert(plan(&c,&s,&st,now)==CHG_KEEP);
   assert(!memcmp(&c,&saved,sizeof(c)));
   s.reg[CHG_USB]=1;assert(plan(&c,&s,&st,now+1000)==CHG_KEEP);
   assert(!c.count&&!c.stopped&&!c.have_toggle&&!c.last_toggle_ms);
  }
 }
 /* Nontrivial ownership and desired state survive even contradictory offline bytes. */
 for(int unknown=0;unknown<2;unknown++)for(int enabled=0;enabled<2;enabled++) {
  c=(struct dby_chg_control){.owned_off=1,.initial_off=1,.want_enable=1,
   .have_toggle=1,.last_toggle_ms=1000,.count=1,.toggles={1000}};
  struct dby_chg_control saved=c;s=good();s.reg[CHG_ENABLE]=enabled;
  s.reg[CHG_USB]=0;s.error[CHG_USB]=unknown?-EIO:0;
  for(int i=0;i<50;i++) {
   assert(dby_chg_policy(&c,&s,&st,120,420)&CHG_ABSENT);
   assert(plan(&c,&s,&st,2000+i*1000)==CHG_KEEP);
   assert(!memcmp(&c,&saved,sizeof(c)));
   assert(!dby_chg_remove_allowed(&c,dby_chg_online(&s)==1,0));
  }
 }
 c=(struct dby_chg_control){0};s=good();s.temp_decic=421;
 assert(plan(&c,&s,&st,1000)==CHG_OFF);commit(&c,&s,CHG_OFF,1000);
 s.reg[CHG_USB]=0;s.temp_decic=250;
 assert(plan(&c,&s,&st,2000)==CHG_KEEP&&c.owned_off&&!c.want_enable);
 s.reg[CHG_USB]=1;
 assert(plan(&c,&s,&st,30999)==CHG_KEEP&&c.want_enable&&c.count==1);
 assert(plan(&c,&s,&st,31000)==CHG_ON);commit(&c,&s,CHG_ON,31000);
 /* On reconnect all three unsafe policy cases still disable on first evaluation. */
 for(int gate=0;gate<3;gate++) {
  c=(struct dby_chg_control){0};s=good();st=(struct dby_chg_state){0};
  s.reg[CHG_USB]=0;assert(plan(&c,&s,&st,1000)==CHG_KEEP);
  if(gate==0)s.temp_decic=421;
  if(gate==1)s.voltage_uv=4430001;
  if(gate==2)st.full=1;
  s.reg[CHG_USB]=1;assert(plan(&c,&s,&st,2000)==CHG_OFF);
 }
 st=(struct dby_chg_state){0};
 /* Cooldown, then same sample becomes eligible at exactly 30 seconds. */
 c=(struct dby_chg_control){0};s=good();s.temp_decic=421;
 assert(plan(&c,&s,&st,1000)==CHG_OFF);commit(&c,&s,CHG_OFF,1000);
 assert(!dby_chg_remove_allowed(&c,1,0)); /* just disabled: latest disallows */
 s.temp_decic=250;
 assert(plan(&c,&s,&st,30999)==CHG_KEEP&&c.want_enable);
 assert(dby_chg_remove_allowed(&c,1,0)); /* allowed, ON delayed by runtime cooldown */
 assert(!dby_chg_remove_allowed(&c,1,1)); /* shutdown */
 assert(!dby_chg_remove_allowed(&c,0,0));
 c.remove_attempted=1;assert(!dby_chg_remove_allowed(&c,1,0));c.remove_attempted=0;
 assert(plan(&c,&s,&st,31000)==CHG_ON);commit(&c,&s,CHG_ON,31000);
 assert(!dby_chg_remove_allowed(&c,1,0)); /* already enabled / no OFF ownership */
 /* All write errors and all incorrect readbacks are terminal, cannot retry. */
 for(int a=CHG_OFF;a<=CHG_ON;a++)for(unsigned int v=0;v<256;v++) {
  c=(struct dby_chg_control){.owned_off=1,.want_enable=1};
  int ret=dby_chg_toggle_result(&c,a,0,v);
  assert(!!ret==(v!=(unsigned int)(a==CHG_ON)));
  if(ret){s=good();assert(plan(&c,&s,&st,3600000)==CHG_KEEP);assert(c.stopped);assert(!dby_chg_remove_allowed(&c,1,0));}
  c=(struct dby_chg_control){.owned_off=1,.want_enable=1};
  assert(dby_chg_toggle_result(&c,a,-EIO,v));assert(c.stopped);
  assert(!dby_chg_remove_allowed(&c,1,0));
 }
 /* Fresh old-value checks used by the actual kernel adapter. */
 for(int a=CHG_OFF;a<=CHG_ON;a++)for(unsigned int v=0;v<256;v++) {
  c=(struct dby_chg_control){.want_enable=1};
  assert(!!dby_chg_prewrite(&c,a,0,v)==(v!=(unsigned int)(a==CHG_OFF)));
  c=(struct dby_chg_control){.want_enable=1};
  assert(dby_chg_prewrite(&c,a,-EIO,v));assert(!c.want_enable&&!c.stopped);
 }
 /* Seeing enabled ends initial takeover ownership even without a driver write. */
 c=(struct dby_chg_control){.initial_off=1};s=good();
 assert(plan(&c,&s,&st,1000)==CHG_KEEP&&!c.initial_off);
 s.reg[CHG_ENABLE]=0;assert(plan(&c,&s,&st,2000)==CHG_KEEP&&c.stopped==CHG_UNOWNED);
 /* Sliding budget: twelve allowed attempts, no thirteenth, even after recovery. */
 c=(struct dby_chg_control){0};s=good();
 for(unsigned int i=0;i<12;i++) {
  s.temp_decic=(i%2)?250:421;
  enum dby_chg_action a=plan(&c,&s,&st,1000+i*30000);
  assert(a==((i%2)?CHG_ON:CHG_OFF));commit(&c,&s,a,1000+i*30000);
 }
 assert(c.count==12);struct dby_chg_control saved=c;
 s.temp_decic=421;assert(plan(&c,&s,&st,361000)==CHG_KEEP&&c.stopped==CHG_BUDGET);
 assert(plan(&c,&s,&st,7200000)==CHG_KEEP&&c.stopped);
 c=saved;dby_chg_prune(&c,3600999);assert(c.count==12);
 dby_chg_prune(&c,3601000);assert(c.count==11);
 assert(plan(&c,&s,&st,3601000)==CHG_OFF);commit(&c,&s,CHG_OFF,3601000);
 assert(c.count==12);
 c=saved;dby_chg_prune(&c,0);assert(c.count==12); /* clock reversal */
 /* Exit exception is independent of runtime interval and budget; last policy matters. */
 c=saved;c.owned_off=1;c.want_enable=1;assert(dby_chg_remove_allowed(&c,1,0));
 assert(!dby_chg_remove_allowed(&c,1,1));c.want_enable=0;assert(!dby_chg_remove_allowed(&c,1,0));
 c.want_enable=1;c.owned_off=0;assert(!dby_chg_remove_allowed(&c,1,0));
 /* Unowned OFF only accepted at probe; external OFF is not silently acquired. */
 c=(struct dby_chg_control){0};s=good();s.reg[CHG_ENABLE]=0;
 assert(plan(&c,&s,&st,0)==CHG_KEEP&&c.stopped==CHG_UNOWNED);
 /* FULL -> OFF with continued pets; voltage clear and observed replug -> ON. */
 for(int mode=0;mode<2;mode++) {
  c=(struct dby_chg_control){0};s=good();st=(struct dby_chg_state){0};
  for(int i=0;i<=18;i++)dby_chg_update(&st,&s,2000+i*10000);
  assert(st.full);assert(plan(&c,&s,&st,182000)==CHG_OFF);commit(&c,&s,CHG_OFF,182000);
  assert(!dby_chg_decide(&s,&st,182000,0,0));
  assert(dby_chg_status(&s,&st,1)==CHG_STATUS_FULL);
  if(mode==0)s.voltage_uv=4249999;
  else {s.reg[CHG_USB]=0;dby_chg_update(&st,&s,192000);s.reg[CHG_USB]=1;}
  dby_chg_update(&st,&s,212000);assert(!st.full);
  assert(plan(&c,&s,&st,212000)==CHG_ON);commit(&c,&s,CHG_ON,212000);
  assert(dby_chg_status(&s,&st,1)==CHG_CHARGING);
 }
 st=(struct dby_chg_state){0};s=good();s.reg[CHG_ENABLE]=0;
 for(int i=0;i<100;i++)dby_chg_update(&st,&s,i*10000);
 assert(!st.qualifying&&!st.full);assert(dby_chg_status(&s,&st,1)==CHG_NOT_CHARGING);
 /* Every independent parameter boundary plus incompatible windows. */
 assert(dby_chg_limits_valid(120,420));assert(dby_chg_limits_valid(120,150));
 assert(dby_chg_limits_valid(350,420));assert(dby_chg_limits_valid(350,370));
 assert(!dby_chg_limits_valid(119,420));assert(!dby_chg_limits_valid(351,420));
 assert(!dby_chg_limits_valid(120,149));assert(!dby_chg_limits_valid(120,421));
 assert(!dby_chg_limits_valid(350,369));assert(!dby_chg_limits_valid(350,150));
 c=(struct dby_chg_control){0};s=good();s.temp_decic=310;
 assert(dby_chg_plan(&c,&s,&st,120,300,1000,&reason)==CHG_OFF);
 commit(&c,&s,CHG_OFF,1000);
 assert(!dby_chg_remove_allowed(&c,1,0));
 assert(dby_chg_plan(&c,&s,&st,120,420,31000,&reason)==CHG_ON);
 puts("PASS enable byte sweep, read faults, gates, hysteresis, cooldown, sliding budget, terminal write faults, ownership, FULL/recharge/replug, parameters, bounded remove and shutdown");
}
