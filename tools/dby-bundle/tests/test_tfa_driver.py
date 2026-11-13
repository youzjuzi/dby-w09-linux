"""Exercise the real sequence engine using SYNTHETIC tables, not vendor data."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

DRIVER = Path(__file__).resolve().parents[3] / 'drivers/dby-tfa9874'


def compile_run(text=None, source=None):
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        shutil.copyfile(DRIVER/'tests/synthetic-tables.h', p/'dby-tfa9874-tables-private.h')
        if text is not None:
            source = p/'test.c'; source.write_text(text)
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2',
                        '-fsanitize=undefined', '-fno-sanitize-recover=all',
                        '-I', str(DRIVER), '-I', str(p), str(source), '-o', str(p/'test')], check=True)
        subprocess.run([str(p/'test')], check=True)


class TfaDriver(unittest.TestCase):
    def test_control_range(self):
        source = (DRIVER/'dby-tfa9874.c').read_text()
        self.assertIn('u->value.integer.min = k->private_value == 1 ? 9 : 0;', source)
        self.assertIn('if (val < (k->private_value == 1 ? 9 : 0)', source)
        self.assertIn('step < 9 || step > 15', (DRIVER/'dby-tfa9874-core.h').read_text())

    def test_core_invariants(self):
        compile_run(source=DRIVER/'test-core.c')

    def test_private_header_untracked(self):
        repo = DRIVER.parents[1]
        names = subprocess.check_output(['git', '-C', str(repo), 'ls-files'], text=True)
        self.assertNotIn('dby-tfa9874-tables-private.h', [Path(n).name for n in names.splitlines()])
        result = subprocess.run(['git', '-C', str(repo), 'check-ignore', '-q',
                                 'drivers/dby-tfa9874/dby-tfa9874-tables-private.h'])
        self.assertEqual(result.returncode, 0)

    def test_no_embedded_tables(self):
        core = (DRIVER/'dby-tfa9874-core.h').read_text()
        self.assertNotIn('dby_tfa_init[] =', core)
        self.assertNotIn('dby_tfa_off[] =', core)
        self.assertNotIn('seq[DBY_TFA_ON_COUNT] = {', core)
        self.assertIn('DBY_TFA_ON_INITIALIZER(', core)

    def test_missing_tables_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); (p/'test.c').write_text('#include "dby-tfa9874-core.h"\n')
            result = subprocess.run(['cc', '-I', str(DRIVER), '-c', str(p/'test.c'),
                                     '-o', str(p/'test.o')], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Missing private TFA tables', result.stderr)
        result = subprocess.run(['make', '-f', str(DRIVER/'Makefile')], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('TFA_TABLES_DIR', result.stderr)

    def test_synthetic_kernel_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(['cc', '-D__KERNEL__', '-x', 'c', '-E',
                                     str(DRIVER/'tests/synthetic-tables.h'), '-o', str(Path(tmp)/'out')],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('SYNTHETIC tables cannot', result.stderr)

    def test_regmap_fail_closed(self):
        source = (DRIVER/'dby-tfa9874.c').read_text()
        engine = source[source.index('struct dby_tfa9874_priv {'):source.index('static int control_info')]
        header = r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "dby-tfa9874-core.h"
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define BIT(n) (1U<<(n))
#define DEFINE_MUTEX(x) int x __attribute__((unused))
#define dev_info_ratelimited(...) ((void)0)
#define dev_dbg(...) ((void)0)
struct device { int unused; };
struct regmap { unsigned regs[256], calls, fail, off_attempts; };
static int fault(struct regmap *m) { return ++m->calls==m->fail ? -5 : 0; }
static int regmap_write(struct regmap *m,unsigned r,unsigned v) {
 for(unsigned i=0;i<DBY_TFA_OFF_COUNT;i++)if(r==dby_tfa_off[i].reg)m->off_attempts++;
 int ret=fault(m); if(!ret)m->regs[r]=v; return ret;
}
static int regmap_read(struct regmap *m,unsigned r,unsigned *v) {
 int ret=fault(m); if(!ret)*v=m->regs[r]; return ret;
}
static int regmap_update_bits(struct regmap *m,unsigned r,unsigned mask,unsigned v) {
 return regmap_write(m,r,(m->regs[r]&~mask)|(v&mask));
}
static void msleep(unsigned n) { assert(n<=5); }
'''
        main = r'''
static void off_values(struct regmap *m) {
 for(unsigned i=0;i<DBY_TFA_OFF_COUNT;i++)assert(m->regs[dby_tfa_off[i].reg]==dby_tfa_off[i].val);
}
int main(void) {
 struct regmap maps[4]={0}; struct dby_tfa9874_priv chips[4]={0};
 struct dby_tfa9874_op init_ops[DBY_TFA_INIT_COUNT];
 for(unsigned i=0;i<4;i++) { chips[i].map=&maps[i]; chips[i].pa_index=i;
  chips[i].channels=2; chips[i].unmuted=true; g_chips[i]=&chips[i]; }
 assert(!safe_enable && attenuation_step==15 && input_atten_6db && active_mask==15);
 for(unsigned i=0;i<4;i++) {
  assert(dby_tfa9874_build_init(i,init_ops,DBY_TFA_INIT_COUNT)==DBY_TFA_INIT_COUNT);
  assert(execute(&chips[i],init_ops,DBY_TFA_INIT_COUNT,false)==0);
  for(unsigned j=0;j<DBY_TFA_INIT_COUNT;j++)assert(maps[i].regs[init_ops[j].reg]==init_ops[j].val);
  assert(apply_state(&chips[i])==0); off_values(&maps[i]);
 }
 /* SYNTHETIC operations exercise each engine branch independently of tables. */
 struct dby_tfa9874_op ops[]={
  {240,0x55,241,0,DBY_TFA_OP_REG_RXORW},
  {242,0x0f,0x05,0,DBY_TFA_OP_REG_WRITE},
  {243,0,0,0,DBY_TFA_OP_REG_READ},
  {244,0,0,5,DBY_TFA_OP_DELAY_MS}};
 maps[0].regs[241]=0x1234; maps[0].regs[242]=0xf0;
 assert(execute(&chips[0],ops,4,false)==0);
 assert(maps[0].regs[240]==(0x1234^0x55) && maps[0].regs[242]==0xf5);
 ops[0].type=255; assert(execute(&chips[0],ops,1,false)==-EINVAL);
 for(unsigned fail=1;fail<=DBY_TFA_ON_COUNT+2;fail++) {
  memset(maps,0,sizeof(maps)); maps[0].fail=fail; safe_enable=true;
  assert(apply_state(&chips[0])==-5); fail_closed(); assert(!safe_enable);
  for(unsigned i=0;i<4;i++){off_values(&maps[i]);assert(maps[i].off_attempts==DBY_TFA_OFF_COUNT);}
 }
 for(unsigned fail=1;fail<=DBY_TFA_OFF_COUNT;fail++) {
  memset(maps,0,sizeof(maps)); maps[0].fail=fail;
  assert(power_off(&chips[0])==-5);
  assert(maps[0].calls==DBY_TFA_OFF_COUNT && maps[0].off_attempts==DBY_TFA_OFF_COUNT);
  for(unsigned i=0;i<DBY_TFA_OFF_COUNT;i++)if(i+1!=fail)
   assert(maps[0].regs[dby_tfa_off[i].reg]==dby_tfa_off[i].val);
 }
 memset(maps,0,sizeof(maps)); safe_enable=true; active_mask=1;
 assert(apply_state(&chips[0])==0);
 assert(maps[0].regs[0x61]==((15<<6)|DBY_TFA_GAIN_LOW_BITS));
 assert(apply_state(&chips[1])==0);off_values(&maps[1]);
 chips[0].unmuted=false;assert(apply_state(&chips[0])==0);off_values(&maps[0]);
 reset_done=true;assert(reset_done);
 puts("PASS: synthetic engine operations, fault revocation and best-effort shutdown");
}
'''
        compile_run(text=header+engine+main)
        self.assertIn('if (ret) fail_closed();', source)
        self.assertIn('if (index && !reset_done)', source)


if __name__ == '__main__':
    unittest.main()
