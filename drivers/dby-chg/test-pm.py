#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile actual charger IRQ setup/resume functions with host fault injection."""
from pathlib import Path
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).with_name('dby-chg.c')


def function(source, name):
    start = source.index('static ', source.rfind('\n}', 0, source.index(name + '(')) + 2)
    end = source.index('\n}', source.index(name + '(')) + 2
    return source[start:end] + '\n'


STUBS = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#define EPROBE_DEFER 517
#define ARRAY_SIZE(a) ((int)(sizeof(a)/sizeof((a)[0])))
#define IRQF_ONESHOT 1
struct device { void *data; bool wake; int wakeirq; };
struct platform_device { struct device dev; };
struct dby_chg { struct device *dev; int operation_lock, work; bool stopping, edge_pending; unsigned long long edge_ms; };
static int system_freezable_wq;
static int requested, freed, queued, lookup_failure, request_failure;
static int wake_failure, action_failure, attach_failure, initialized;
static unsigned long long now;
static void (*cleanup)(void *);
static void *cleanup_data;
static void *dev_get_drvdata(struct device *d) { return d->data; }
static void mutex_lock(int *l) { assert(!*l); *l=1; }
static void mutex_unlock(int *l) { assert(*l); *l=0; }
static unsigned long long chg_now(struct dby_chg *c) { assert(c->operation_lock); return now; }
static void mod_delayed_work(int wq,int *w,int delay) { assert(wq==system_freezable_wq && w && !delay); queued++; }
static int chg_changed_irq(int irq,void *p) { (void)irq; (void)p; return 0; }
static int chg_bark_irq(int irq,void *p) { (void)irq; (void)p; return 0; }
static const char *const irq_names[]={"chg-state-change","usbin-plugin","wdog-bark"};
static int platform_get_irq_byname(struct platform_device *p,const char *name) {
 (void)p;
 for(int i=0;i<3;i++)if(!strcmp(name,irq_names[i])) {
  if(lookup_failure==i+1)return -ENOENT;
  if(lookup_failure==i+4)return -EPROBE_DEFER;
  return 20+i;
 }
 assert(0); return -EINVAL;
}
static int devm_request_threaded_irq(struct device *d,int irq,void *top,int (*fn)(int,void *),int flags,const char *name,void *data) {
 assert(d && !top && flags==IRQF_ONESHOT && data==d->data);
 assert(!strcmp(name,irq_names[irq-20])); assert(fn==(irq==22?chg_bark_irq:chg_changed_irq));
 if(request_failure==irq)return -EBUSY;
 requested++;return 0;
}
static void devm_free_irq(struct device *d,int irq,void *data) { assert(d && data==d->data && irq>=20 && irq<=22);freed++; }
#define dev_info(...) ((void)0)
#define dev_err_probe(dev,err,...) (err)
static int device_init_wakeup(struct device *d,bool on) {
 if(on) { assert(requested==3);initialized++;if(wake_failure)return -ENOMEM; }
 else assert(!d->wakeirq);
 d->wake=on; return 0;
}
static int devm_add_action_or_reset(struct device *d,void (*fn)(void *),void *data) {
 assert(d->wake && data==d);
 if(action_failure) { fn(data);return -ENOMEM; }
 cleanup=fn;cleanup_data=data;return 0;
}
static int devm_pm_set_wake_irq(struct device *d,int irq) {
 assert(d->wake && irq==21 && cleanup);
 if(attach_failure)return -ENOMEM;
 d->wakeirq=irq;return 0;
}
static void release(struct device *d) {
 /* Model devres LIFO: detach wake IRQ, disable wakeup, free requested IRQs. */
 d->wakeirq=0;
 if(cleanup)cleanup(cleanup_data);
 cleanup=NULL;cleanup_data=NULL;freed=requested;
 assert(!d->wake);
}
static void reset(struct platform_device *p,struct dby_chg *c) {
 memset(p,0,sizeof(*p));memset(c,0,sizeof(*c));p->dev.data=c;c->dev=&p->dev;
 requested=freed=queued=lookup_failure=request_failure=0;
 wake_failure=action_failure=attach_failure=initialized=0;cleanup=NULL;now=12345;
}
'''
MAIN = r'''
int main(void) {
 struct platform_device p; struct dby_chg c;
 reset(&p,&c);assert(chg_request_irqs(&p,&c)==0);
 assert(requested==3 && p.dev.wake && p.dev.wakeirq==21 && !queued);
 assert(chg_resume(&p.dev)==0 && queued==1 && c.edge_pending && c.edge_ms==now);
 now+=100;assert(chg_resume(&p.dev)==0 && queued==2 && c.edge_ms==now);
 c.stopping=true;now+=100;assert(chg_resume(&p.dev)==0 && queued==2 && c.edge_ms==now-100);
 release(&p.dev);
 /* Missing IRQ resources retain polling, without a partial IRQ/wakeup set. */
 for(int i=1;i<=6;i++) {
  reset(&p,&c);lookup_failure=i;
  assert(chg_request_irqs(&p,&c)==(i<=3?0:-EPROBE_DEFER));
  assert(!requested && !initialized && !p.dev.wake && !p.dev.wakeirq);
 }
 for(int i=20;i<=22;i++) {
  reset(&p,&c);request_failure=i;assert(chg_request_irqs(&p,&c)==-EBUSY);
  assert(requested==i-20 && freed==requested && !initialized);
 }
 /* Propagate each wake setup failure and leave devres able to unwind. */
 for(int i=0;i<3;i++) {
  reset(&p,&c);
  wake_failure=i==0;action_failure=i==1;attach_failure=i==2;
  assert(chg_request_irqs(&p,&c)==-ENOMEM);assert(!queued && !p.dev.wakeirq);
  release(&p.dev);
 }
 /* An unrelated later probe failure follows the same successful cleanup. */
 reset(&p,&c);assert(!chg_request_irqs(&p,&c));release(&p.dev);
 puts("PASS: USB-only wake IRQ, polling/defer/request/setup failures, teardown, immediate resume, stopping guard");
 return 0;
}
'''


class ChargerPM(unittest.TestCase):
    def test_actual_irq_and_resume_functions(self):
        source = SOURCE.read_text()
        extracted = ''.join(function(source, name) for name in (
            'chg_queue_sample', 'chg_resume', 'chg_wakeup_disable', 'chg_request_irqs'))
        self.assertIn('DEFINE_SIMPLE_DEV_PM_OPS(chg_pm_ops, NULL, chg_resume)', source)
        self.assertIn('.pm = pm_sleep_ptr(&chg_pm_ops)', source)
        with tempfile.TemporaryDirectory(prefix='dby-chg-pm-') as tmp:
            cfile = Path(tmp) / 'test.c'
            binary = Path(tmp) / 'test'
            cfile.write_text(STUBS + extracted + MAIN)
            subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=undefined', '-fno-sanitize-recover=all',
                            str(cfile), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
