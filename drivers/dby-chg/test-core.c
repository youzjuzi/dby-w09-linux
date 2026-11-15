/* SPDX-License-Identifier: GPL-2.0-only */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "dby-chg-core.h"
static struct dby_chg_sample good(void)
{
 struct dby_chg_sample s = {0};
 s.reg[CHG_ENABLE] = 1; s.reg[CHG_FLOAT] = 0x50; s.reg[CHG_USB] = 1;
 s.temp_decic = 250; s.voltage_uv = 4400000; s.current_ua = 100000; s.capacity = 98;
 return s;
}
static unsigned int decision(struct dby_chg_sample s)
{
 return dby_chg_conditions(&s);
}
static struct dby_chg_state full(void)
{
 struct dby_chg_sample s = good(); struct dby_chg_state st = {0};
 dby_chg_update(&st, &s, 0);
 for (int i=1; i<18; i++) { dby_chg_update(&st, &s, i*10000); assert(!st.full); }
 dby_chg_update(&st, &s, 180000); assert(st.full);
 return st;
}
int main(void)
{
 struct dby_chg_sample s = good(); struct dby_chg_state st = {0};
 assert(!decision(s));
 for (unsigned int v=0; v<256; v++) {
  int a=dby_chg_float_action(0,v);
  assert(a==(v==0x55?1:v==0x50?0:-EINVAL));
  assert(dby_chg_float_action(-EIO,v)==-EIO);
  if(a==1) assert(dby_chg_float_action(0,0x50)==0);
 }
 assert(dby_chg_identity(0x51,0x20,1,0,0));
 assert(dby_chg_identity(0x51,0x20,2,0,0));
 assert(!dby_chg_identity(0x51,0x20,3,0,0));
 assert(!dby_chg_identity(0x50,0x20,1,0,0));
 assert(!dby_chg_identity(0x51,0x21,1,0,0));
 assert(!dby_chg_identity(0x51,0x20,1,1,0));
 assert(!dby_chg_identity(0x51,0x20,1,0,1));
 for (int i=0;i<CHG_NREG;i++) { s=good();s.error[i]=-EIO;assert(decision(s)&CHG_READ); }
 for (int i=0;i<5;i++) {
  s=good();
  int *errors[]={&s.identity_error,&s.temp_error,&s.voltage_error,&s.current_error,&s.capacity_error};
  *errors[i]=-EIO; assert(decision(s));
 }
 s=good();s.reg[CHG_FLOAT]=0x55;assert(decision(s)&CHG_BAD_FLOAT);
 s=good();s.reg[CHG_USB]=0;assert(decision(s)&CHG_ABSENT);
 s=good();s.reg[CHG_HW_TEMP]=1;assert(decision(s)&CHG_BAD_HW_TEMP);
 int temps[]={119,120,420,421};
 for(int i=0;i<4;i++){s=good();s.temp_decic=temps[i];assert(!!decision(s)==(i==0||i==3));}
 int volts[]={-1,0,4430000,4430001};
 for(int i=0;i<4;i++){s=good();s.voltage_uv=volts[i];assert(!!decision(s)==(i!=2));}
 s=good();
 assert(dby_chg_decide(&s,&st,30999,1,1000)&CHG_TOO_SOON);
 assert(!dby_chg_decide(&s,&st,31000,1,1000));
 assert(dby_chg_decide(&s,&st,999,1,1000)&CHG_TOO_SOON);
 st=full();assert(!dby_chg_decide(&s,&st,180000,0,0)); /* FULL now disables via 1042, pets continue. */
 s.voltage_uv=4250000;dby_chg_update(&st,&s,190000);assert(st.full);
 s.voltage_uv=4249999;dby_chg_update(&st,&s,200000);assert(!st.full&&!decision(s));
 st=full();s=good();s.error[CHG_USB]=-EIO;dby_chg_update(&st,&s,190000);
 s=good();dby_chg_update(&st,&s,200000);assert(st.full);
 s.reg[CHG_USB]=0;dby_chg_update(&st,&s,210000);assert(st.full);
 s.reg[CHG_USB]=1;dby_chg_update(&st,&s,220000);assert(!st.full);
 for(int scenario=0;scenario<5;scenario++) {
  memset(&st,0,sizeof(st));s=good();
  if(scenario==0)s.current_ua=-1;
  if(scenario==1)s.current_ua=200000;
  if(scenario==2)s.voltage_uv=4379999;
  if(scenario==3)s.current_error=-EIO;
  if(scenario==4)s.temp_decic=421;
  for(int i=0;i<100;i++)dby_chg_update(&st,&s,i*10000);
  assert(!st.full&&!st.qualifying);
 }
 memset(&st,0,sizeof(st));s=good();dby_chg_update(&st,&s,0);
 for(int i=1;i<18;i++)dby_chg_update(&st,&s,i*10000);
 dby_chg_update(&st,&s,170000);assert(!st.full); /* duplicate */
 dby_chg_update(&st,&s,300000);assert(!st.full&&st.qualifying==1); /* suspend gap */
 s.current_error=-EIO;dby_chg_update(&st,&s,310000);assert(!st.qualifying);
 memset(&st,0,sizeof(st));s=good();s.current_ua=0;
 for(int i=0;i<=18;i++)dby_chg_update(&st,&s,i*10000);
 assert(st.full); /* zero current qualifies */
 st.full=0;s=good();assert(dby_chg_status(&s,&st,1)==CHG_CHARGING);
 assert(dby_chg_status(&s,&st,0)==CHG_NOT_CHARGING);
 s.current_ua=0;assert(dby_chg_status(&s,&st,1)==CHG_NOT_CHARGING);
 s.current_ua=-1;assert(dby_chg_status(&s,&st,1)==CHG_NOT_CHARGING);
 st.full=1;assert(dby_chg_status(&s,&st,0)==CHG_STATUS_FULL);
 s.capacity=97;assert(dby_chg_status(&s,&st,0)==CHG_NOT_CHARGING);
 s.capacity=98;s.capacity_error=-EIO;assert(dby_chg_status(&s,&st,0)==CHG_NOT_CHARGING);
 s.reg[CHG_USB]=0;assert(dby_chg_status(&s,&st,0)==CHG_DISCHARGING);
 s.error[CHG_USB]=-EIO;assert(dby_chg_status(&s,&st,0)==CHG_NOT_CHARGING);
 /* Earlier first work must not borrow time from before first observation. */
 memset(&st,0,sizeof(st));s=good();
 unsigned long long first=DBY_CHG_FIRST_MS;
 assert(first==2000);
 assert(!dby_chg_decide(&s,&st,first,0,0));
 assert(dby_chg_decide(&s,&st,first+29999,1,first)&CHG_TOO_SOON);
 assert(!dby_chg_decide(&s,&st,first+30000,1,first));
 dby_chg_update(&st,&s,first);
 for(int i=1;i<18;i++)dby_chg_update(&st,&s,first+i*10000);
 assert(!st.full);
 dby_chg_update(&st,&s,180000);assert(!st.full);
 dby_chg_update(&st,&s,first+180000);assert(st.full);
 /* FG missing at boot inhibits petting; subsequent good samples recover. */
 memset(&st,0,sizeof(st));s=good();
 s.voltage_error=s.current_error=s.capacity_error=-ENODEV;assert(decision(s));
 s=good();assert(!decision(s));
 /* Diagnostic accumulation, pause/resume, unknown reads, replug reset. */
 struct dby_chg_pause pause={0};
 dby_chg_pause_update(&pause,-1,0,0);assert(!dby_chg_pause_ms(&pause,2000));
 dby_chg_pause_update(&pause,1,0,2000);assert(dby_chg_pause_ms(&pause,12000)==10000);
 dby_chg_pause_update(&pause,1,1,12000);assert(dby_chg_pause_ms(&pause,22000)==10000);
 dby_chg_pause_update(&pause,1,0,22000);
 dby_chg_pause_update(&pause,-1,0,32000);assert(dby_chg_pause_ms(&pause,42000)==30000);
 dby_chg_pause_update(&pause,1,1,42000);assert(dby_chg_pause_ms(&pause,52000)==30000);
 dby_chg_pause_update(&pause,0,0,52000);assert(!dby_chg_pause_ms(&pause,62000));
 dby_chg_pause_update(&pause,1,0,62000);assert(!dby_chg_pause_ms(&pause,61000));
 assert(dby_chg_pause_ms(&pause,72000)==10000);
 assert(!dby_chg_event(1,1,1,1,1,0,0,0,0));
 assert(dby_chg_event(0,1,1,1,1,0,0,0,0));
 assert(dby_chg_event(1,1,0,1,1,0,0,0,0));
 assert(dby_chg_event(1,1,-1,1,1,0,0,0,0));
 assert(dby_chg_event(1,1,1,1,0,0,0,0,0));
 assert(dby_chg_event(1,1,1,1,1,0,1,0,0));
 assert(dby_chg_event(1,1,1,1,1,0,0,0,CHG_READ));
 puts("PASS identity, 256 initial float values, every read-error/gate, temperature/voltage boundaries, first-work spacing/180-second FULL, FG load order, stopped-time accounting, logging events, FULL dwell/reset/replug, status mapping");
 return 0;
}
