# SPDX-License-Identifier: GPL-2.0-only
"""Plaintext defaults, explicit WPA2-only derivation and no-echo confirmation."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_seed_commands as F
from dbyinstall import seed as S, seed_cli as C, seed_model as M
from dbyinstall.common import BundleError, MIN_SIZE
from dbyinstall.maintenance_io import region_hash


class PlaintextUnitTests(unittest.TestCase):
    def test_plaintext_ssid_can_change_but_raw_and_derived_remain_bound(self):
        state, _, _ = M.edit(F.EMPTY, {'ssid': 'IEEE'}, password=F.PASSPHRASE)
        changed, _, _ = M.edit(state, {'ssid': 'fictional-other'})
        self.assertTrue(M.parse(changed['seed.conf'])[0]['wifi "main"']['psk'] == F.PASSPHRASE, 'plaintext changed')
        for password, options in ((F.VECTOR, {}), (F.PASSPHRASE, {'derived_psk': True})):
            state, _, _ = M.edit(F.EMPTY, dict(ssid='IEEE', **options), password=password)
            with self.assertRaisesRegex(BundleError, 'E_SEED_INVALID'):
                M.edit(state, {'ssid': 'fictional-other'})

    def test_cli_prompt_twice_matching_mismatch_and_empty(self):
        args = ['seed', 'set', '--image-file', '/nonexistent', '--backup-dir', '/nonexistent', '--ssid', 'IEEE', '--psk-prompt']
        for pair, expected in (((F.PASSPHRASE, F.PASSPHRASE), 0), ((F.PASSPHRASE, 'different-fixture'), 1), (('', F.PASSPHRASE), 1)):
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.object(C, 'tty'), patch.object(C.getpass, 'getpass', side_effect=pair) as read, patch.object(C, 'operate') as operate, redirect_stdout(stdout), redirect_stderr(stderr):
                result = C.main(args)
            self.assertEqual(result, expected); self.assertEqual(read.call_count, 2)
            self.assertEqual(operate.call_count, int(expected == 0))
            if expected: self.assertIn('E_SEED_PSK_MISMATCH', stderr.getvalue())
            self.assertFalse(F.PASSPHRASE in stdout.getvalue()+stderr.getvalue(), 'credential leaked')
        with patch.object(C, 'tty'), patch.object(C.getpass, 'getpass', side_effect=['', '']):
            value = C.confirmed_password('fixture: ')
        with self.assertRaisesRegex(BundleError, 'E_SEED_INVALID'):
            M.edit(F.EMPTY, {'ssid': 'IEEE'}, password=value)

    def test_modes_mutually_exclusive_alias_noop_and_wizard_forbids_mode(self):
        base = ['seed', 'set', '--image-file', '/nonexistent', '--backup-dir', '/nonexistent', '--refresh-hz', '60']
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            C.main(base + ['--plaintext-psk', '--derived-psk'])
        self.assertEqual(error.exception.code, 2)
        with patch.object(C, 'operate') as operate:
            self.assertEqual(C.main(base + ['--plaintext-psk']), 0)
            self.assertFalse(operate.call_args.kwargs['changes']['derived_psk'])
        for mode in ('--derived-psk', '--plaintext-psk'):
            with patch.object(C, 'operate') as operate, redirect_stderr(io.StringIO()):
                self.assertEqual(C.main(['seed', 'wizard', '--image-file', '/nonexistent', '--backup-dir', '/nonexistent', mode]), 1)
                operate.assert_not_called()

    def test_wizard_confirmed_plaintext_and_empty_keeps_existing(self):
        old, _, _ = M.edit(F.EMPTY, {'ssid': 'IEEE'}, password=F.PASSPHRASE)
        for pair in ((F.PASSPHRASE, F.PASSPHRASE), ('', '')):
            reads = []
            values = iter(pair)
            def secret(prompt): reads.append(prompt); return next(values)
            with patch.object(C, 'tty'):
                changes, password, replacement, addition = C.wizard(old, ask=lambda _: '', secret=secret, emit=lambda _: None)
            self.assertEqual(len(reads), 2)
            state, _, _ = M.edit(old, changes, password=password)
            self.assertTrue(state == old, 'wizard default changed plaintext')


class PlaintextImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        F.SeedImageTests.setUpClass.__func__(cls)

    def setUp(self):
        F.SeedImageTests.setUp(self)

    state = F.SeedImageTests.state
    operate = F.SeedImageTests.operate

    def cli(self, *args):
        return subprocess.run([sys.executable, '-I', '-B', str(F.TOOL/'dby-install'), *map(str,args), '--image-file', str(self.target.path)],
                              input=(str(self.target.path)+'\n').encode(), capture_output=True, env=dict(os.environ, DBY_TRACE='1'))

    def test_default_derived_alias_cli_disk_boot_plan_and_leak_scan(self):
        source=self.root/'secret'; source.write_text(F.PASSPHRASE); source.chmod(0o600)
        for mode, expected in (([], F.PASSPHRASE), (['--derived-psk'], F.VECTOR), (['--plaintext-psk'], F.PASSPHRASE)):
            result=self.cli('seed','set','--backup-dir',self.backups,'--ssid','IEEE','--psk-file',source,*mode)
            self.assertEqual(result.returncode,0,'seed set failed')
            state=self.state()[1]
            self.assertTrue(M.parse(state['seed.conf'])[0]['wifi "main"']['psk']==expected,'stored credential mismatch')
            plan=F.BOOT.make_plan(self.root,state['seed.conf'])
            self.assertEqual(plan['logs'],[]); self.assertEqual(len(plan['networks']),1)
            self.assertEqual(S.PSK_WARNING.encode() in result.stderr, mode==['--derived-psk'])
            self.assertEqual(S.PSK_WARNING.encode() in result.stdout, mode==['--derived-psk'])
            metadata=''.join((p/'backup.json').read_text() for p in self.backups.iterdir())
            scanned=result.stdout+result.stderr+metadata.encode()
            self.assertFalse(any(secret.encode() in scanned for secret in (F.PASSPHRASE,F.VECTOR)), 'credential leaked')

    def test_derived_and_raw_diff_notices_show_note_and_default_silence(self):
        for password,changes,expected in ((F.PASSPHRASE,{},False),(F.PASSPHRASE,{'derived_psk':True},True),(F.VECTOR,{},True)):
            stderr=io.StringIO(); self.messages.clear()
            with redirect_stderr(stderr):
                preview=self.operate(changes=dict(ssid='IEEE',**changes),password=password,dry_run=True)
            self.assertEqual(preview['notices'],[S.PSK_WARNING] if expected else [])
            self.assertEqual(stderr.getvalue(),S.PSK_WARNING+'\n' if expected else '')
            self.assertFalse(any(s in '\n'.join(self.messages)+stderr.getvalue() for s in (F.PASSPHRASE,F.VECTOR)), 'credential leaked')
        with redirect_stderr(io.StringIO()): self.operate(changes={'ssid':'IEEE'},password=F.VECTOR)
        shown=self.operate('show')
        self.assertEqual(shown['wifi "main"']['note'],'WPA2-only (not usable with WPA3/SAE)')
        self.assertEqual(shown['wifi "main"']['psk'],'<set: derived-psk>')

    def test_wizard_mismatch_changes_neither_disk_nor_backups(self):
        before=region_hash(self.target.path,0,MIN_SIZE)
        def wizard(old):
            values=iter([F.PASSPHRASE,'different-fixture'])
            return C.wizard(old,ask=lambda prompt: 'main' if prompt.startswith('Repeat new') else '',secret=lambda _: next(values),emit=self.messages.append)
        with patch.object(C,'tty'), self.assertRaisesRegex(BundleError,'E_SEED_PSK_MISMATCH'):
            self.operate('wizard',backup_dir=self.backups,wizard=wizard)
        self.assertFalse(self.backups.exists())
        self.assertEqual(region_hash(self.target.path,0,MIN_SIZE),before)

    def test_prompt_empty_and_mismatch_leave_image_and_backup_unchanged(self):
        before=region_hash(self.target.path,0,MIN_SIZE)
        for pair,code in ((['',''],'E_SEED_INVALID'),([F.PASSPHRASE,'different-fixture'],'E_SEED_PSK_MISMATCH')):
            stderr=io.StringIO()
            with patch.object(C,'tty'), patch.object(C.getpass,'getpass',side_effect=pair), redirect_stderr(stderr):
                result=C.main(['seed','set','--image-file',str(self.target.path),'--backup-dir',str(self.backups),'--ssid','IEEE','--psk-prompt'])
            self.assertEqual(result,1); self.assertIn(code,stderr.getvalue())
            self.assertFalse(F.PASSPHRASE in stderr.getvalue(),'credential leaked')
        self.assertFalse(self.backups.exists())
        self.assertEqual(region_hash(self.target.path,0,MIN_SIZE),before)
