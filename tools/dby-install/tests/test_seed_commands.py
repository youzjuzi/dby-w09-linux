# SPDX-License-Identifier: GPL-2.0-only
"""Seed differential, privacy and transactional tests on synthetic regular files."""
from contextlib import contextmanager, redirect_stdout, redirect_stderr
import base64
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from dbyinstall import seed as S, seed_model as M, seed_rules as R, seed_io as IO, seed_cli as CLI, core, image
from dbyinstall.common import BundleError, MIN_SIZE, P1_START, P1_SECTORS, P2_START
from dbyinstall.maintenance_io import region_hash, replace_region
from dbyinstall.targets import ImageFileTarget, BlockDeviceTarget, live_probe
import test_maintenance as fixtures
import test_hardening as hardening
from test_install import probe_row

loader = importlib.machinery.SourceFileLoader('seed_apply_reference', str(TOOL.parent / 'dby-bundle/rootfs/overlay/usr/lib/dby/dby-seed-apply'))
spec = importlib.util.spec_from_loader(loader.name, loader)
BOOT = importlib.util.module_from_spec(spec); loader.exec_module(BOOT)
EMPTY = {'seed.conf': None, 'authorized_keys': None}
# Public IEEE 802.11i test vector; assertions never print credentials on failure.
PASSPHRASE = 'password'
VECTOR = 'f42c6fc52df0ebef9ebb4b90b38a5f902e83fe1b135a70e23aed762e9710a12e'


def key(kind='ssh-ed25519'):
    def field(value):
        return struct.pack('>I', len(value)) + value
    values = {'ssh-ed25519': [bytes(range(32))], 'ssh-rsa': [b'\x01\x00\x01', b'\x01' * 128],
              'ecdsa-sha2-nistp256': [b'nistp256', b'\x04' + bytes(64)]}[kind]
    return (kind + ' ' + base64.b64encode(b''.join(field(v) for v in [kind.encode(), *values])).decode() + '\n').encode()


class SeedModelTests(unittest.TestCase):
    def test_scanner_matches_boot_for_malformed_and_case_inputs(self):
        cases = [b'', b'junk\n# comment\n; comment\nversion=1\n[ APT ]\nMIRROR=a\nmirror=b\n',
                 b'[wifi "X"]\nSSID=a\npsk=b\n[WiFi "x"]\nssid=c\n', b'[bad\nx=y\n[[bad]]\nx=z',
                 b'[SYSTEM]\nsafe_mode=TRUE\n', b'\xff=a\n', b'\x00', b'x' * (R.LIMIT + 1),
                 b'[ wifi " X " ]\nssid=a\n', b'[wifi "X"]\na=b\n[wifi "X"]\na=c\n']
        for data in cases:
            self.assertTrue(R.parse(data) == BOOT.parse(data), 'scanner mismatch')

    def test_generated_seed_matches_boot_plan_without_warnings(self):
        state, _, _ = M.edit(EMPTY, {'ssid': 'IEEE', 'derived_psk': True, 'country': chr(90)*2, 'apt_mirror': 'https://fixture.invalid/ports/',
                            'refresh_hz': '60', 'timezone': 'Fixture/Clock', 'safe_mode': 'true', 'packages': 'tree curl tree'},
                            password=PASSPHRASE, replacement=key())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); zone = root / 'usr/share/zoneinfo/Fixture/Clock'; zone.parent.mkdir(parents=True); zone.touch()
            plan = BOOT.make_plan(root, state['seed.conf'], state['authorized_keys'])
        self.assertEqual(plan['logs'], [])
        self.assertEqual(plan['mirror'], 'https://fixture.invalid/ports')
        self.assertEqual(plan['timezone'], 'Fixture/Clock'); self.assertTrue(plan['safe_mode'])
        self.assertEqual(plan['packages'], ['curl', 'tree']); self.assertEqual(plan['country'], chr(90)*2)
        self.assertEqual(len(plan['networks']), 1)
        self.assertTrue(VECTOR in plan['files'][plan['networks'][0]][0], 'derived key mismatch')
        self.assertIn('refresh_hz=60', plan['files']['etc/modprobe.d/dby-panel.conf'][0])
        self.assertTrue(R.parse(state['seed.conf']) == BOOT.parse(state['seed.conf']), 'parse mismatch')

    def test_psk_vector_plaintext_raw_and_ssid_binding(self):
        state, _, _ = M.edit(EMPTY, {'ssid': 'IEEE', 'derived_psk': True}, password=PASSPHRASE)
        self.assertTrue(R.parse(state['seed.conf'])[0]['wifi "main"']['psk'] == VECTOR, 'standard vector mismatch')
        for password, plain in ((PASSPHRASE, True), (VECTOR.upper(), False)):
            result, _, _ = M.edit(EMPTY, {'ssid': 'IEEE', 'plaintext_psk': plain}, password=password)
            self.assertTrue(R.parse(result['seed.conf'])[0]['wifi "main"']['psk'] == password, 'raw/plain key changed')
        with self.assertRaisesRegex(BundleError, 'SSID change for a derived/raw PSK requires'):
            M.edit(state, {'ssid': 'changed'})
        M.edit(state, {'ssid': 'changed'}, password=PASSPHRASE)

    def test_delete_options_multinetwork_and_conflict(self):
        state, _, _ = M.edit(EMPTY, {'ssid': 'IEEE', 'country': 'zz', 'apt_mirror': 'http://fixture.invalid',
                            'refresh_hz': 120, 'timezone': 'Fixture/Clock', 'packages': 'tree'}, password=PASSPHRASE)
        state, _, _ = M.edit(state, {'wifi': 'second', 'ssid': 'synthetic-other', 'country': 'ZZ'}, password=PASSPHRASE)
        with self.assertRaisesRegex(BundleError, 'conflicting wifi country'):
            M.edit(state, {'wifi': 'second', 'country': 'XX'})
        state, _, _ = M.edit(state, dict(remove_wifi='main', **{'no_' + k: True for k in M.FIELDS}))
        parsed = R.parse(state['seed.conf'])[0]
        self.assertNotIn('wifi "main"', parsed); self.assertIn('wifi "second"', parsed)
        self.assertFalse(any(s in parsed for s in ('apt', 'display', 'system', 'packages')))

    def test_invalid_fields_and_duplicate_labels_are_specific(self):
        cases = [({'ssid': ''}, 'ssid'), ({'ssid': 'x'*33}, 'ssid'), ({'country': 'XYZ'}, 'country'),
                 ({'refresh_hz': '90'}, 'refresh_hz'), ({'timezone': '../bad'}, 'timezone'),
                 ({'packages': '--option'}, 'packages'), ({'safe_mode': 'maybe'}, 'safe_mode'),
                 ({'wifi': 'bad"label'}, 'label'), ({'ssid': 'line\nfeed'}, 'ssid')]
        for change, item in cases:
            with self.subTest(item=item), self.assertRaisesRegex(BundleError, item):
                M.edit(EMPTY, dict({'ssid': 'IEEE'}, **change), password=PASSPHRASE)
        with self.assertRaisesRegex(BundleError, 'duplicate wifi label'):
            M.load(b'[wifi "X"]\n[WiFi "X"]\n')

    def test_mirror_validators_match_boot(self):
        for value in ('http://fixture.invalid:80/ports/', 'ftp://fixture.invalid', 'http://x:99999',
                      'http://a:b@x', 'https://x/?q=1', 'https://x/#x', 'http://x/%2e%2e/',
                      'http://x/a%20b', 'http://x/\\bad', 'http://x:bad', 'https://x/' + 'a'*2048):
            self.assertEqual(R.mirror_url(value), BOOT.mirror_url(value))
            if not BOOT.mirror_url(value):
                with self.assertRaisesRegex(BundleError, 'apt mirror'):
                    M.edit(EMPTY, {'apt_mirror': value})

    def test_keys_structure_replace_merge_deduplicate_and_fingerprints(self):
        all_keys = b''.join(key(k) for k in ('ssh-ed25519', 'ssh-rsa', 'ecdsa-sha2-nistp256'))
        state, _, _ = M.edit(EMPTY, {}, replacement=all_keys)
        state2, _, _ = M.edit(state, {}, addition=key())
        self.assertTrue(state2['authorized_keys'] == state['authorized_keys'], 'key merge differs')
        for line in all_keys.decode().splitlines():
            self.assertEqual(R.public_key(line), BOOT.public_key(line))
        state, _, _ = M.edit(state, {}, replacement=key()[:-1] + b' synthetic-comment@example.invalid\n')
        shown = json.dumps(M.redacted(state))
        self.assertIn('SHA256:', shown); self.assertNotIn('example.invalid', shown)
        for bad in (b'', b'bad\n', key() + b'bad\n', key().replace(b'ssh-ed25519 ', b'ssh-rsa ', 1)):
            with self.assertRaisesRegex(BundleError, 'authorized_keys'):
                M.keys(bad)

    def test_normalization_discards_names_and_preserves_last_assignment(self):
        raw = b'# keep in backup\nversion=1\n[apt]\nmirror=http://first.invalid\nmirror=http://second.invalid\nsecret=hidden\n[unknown]\nkey=hidden\n'
        state, discarded, junk = M.edit(dict(EMPTY, **{'seed.conf': raw}), {'refresh_hz': '60'})
        self.assertEqual(discarded, ['apt/secret', 'unknown']); self.assertTrue(junk)
        self.assertNotIn(b'hidden', state['seed.conf'])
        self.assertEqual(R.parse(state['seed.conf'])[0]['apt']['mirror'], 'http://second.invalid')

    def test_private_psk_file_and_cli_no_echo(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'psk'; path.write_text(PASSPHRASE + '\n'); path.chmod(0o600)
            self.assertTrue(CLI.password_file(path) == PASSPHRASE, 'private file mismatch')
            path.chmod(0o640)
            with self.assertRaisesRegex(BundleError, 'E_SEED_PSK_SOURCE'):
                CLI.password_file(path)
            for data in (b'', b'a\nb\n', b'\xff'):
                path.chmod(0o600); path.write_bytes(data)
                with self.assertRaisesRegex(BundleError, 'E_SEED_PSK_SOURCE'):
                    CLI.password_file(path)
        for args in (['seed', 'set', '--psk', PASSPHRASE], ['seed', 'wizard'], ['seed', 'set', '--psk-prompt']):
            result = subprocess.run([sys.executable, '-I', '-B', str(TOOL / 'dby-install'), *args,
                                     '--image-file', '/nonexistent', '--backup-dir', '/nonexistent'], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(PASSPHRASE.encode() in result.stdout + result.stderr, 'credential leaked')

    def test_wizard_keeps_defaults_and_uses_secret_prompt(self):
        state, _, _ = M.edit(EMPTY, {'ssid': 'IEEE'}, password=PASSPHRASE)
        with patch.object(CLI, 'tty'):
            changes, password, replacement, addition = CLI.wizard(state, ask=lambda _: '', secret=lambda _: '', emit=lambda _: None)
        result, _, _ = M.edit(state, changes, password=password, replacement=replacement, addition=addition)
        self.assertTrue(result == state, 'wizard defaults changed state')


class SeedImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.MaintenanceTests.setUpClass.__func__(cls)

    def setUp(self):
        fixtures.MaintenanceTests.setUp(self)

    def operate(self, action='set', **kwargs):
        options = dict(changes={'refresh_hz': '60'}, backup_dir=self.backups) if action in ('set', 'on', 'off') else {}
        options.update(ask=lambda _: str(self.target.path), emit=self.messages.append)
        options.update(kwargs)
        return S.operate(action, self.target, **options)

    def state(self):
        with IO.SeedBackend(self.target) as backend, backend.snapshot() as (root, _):
            return IO.inspect(root)

    def mutate(self, function):
        with IO.SeedBackend(self.target) as backend, backend.locked(), backend.transaction() as store:
            function(store)

    def test_write_backup_permissions_immutable_regions_and_unrelated_files(self):
        old_doc, old, boot = self.state()
        p2 = region_hash(self.target.path, P2_START*512, 1024**3)
        result = self.operate(changes={'ssid': 'IEEE', 'refresh_hz': '60', 'derived_psk': True}, password=PASSPHRASE, replacement=key())
        backup = Path(result['backup']); restored, metadata = S.validate_backup(backup)
        self.assertTrue(restored == old, 'backup raw bytes differ')
        self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in backup.iterdir()))
        doc, state, new_boot = self.state()
        self.assertEqual(doc, old_doc); self.assertEqual(boot, new_boot)
        self.assertEqual(region_hash(self.target.path, P2_START*512, 1024**3), p2)
        for name in ('Android', 'LOST.DIR'):
            dest = self.root / name
            image.run(['mcopy', '-i', str(self.target.path) + '@@' + str(P1_START*512), '::/' + name + '/keep', dest])
            self.assertEqual(dest.read_bytes(), b'unrelated data\x00\xff')
        for secret in (PASSPHRASE, VECTOR):
            self.assertFalse(secret in '\n'.join(self.messages) + json.dumps(metadata), 'credential leaked')
        self.assertTrue(VECTOR.encode() in state['seed.conf'], 'derived PSK absent')

    def test_dry_run_cli_trace_never_writes_or_leaks(self):
        source = self.root / 'secret'; source.write_text(PASSPHRASE); source.chmod(0o600)
        before = region_hash(self.target.path, 0, MIN_SIZE)
        result = subprocess.run([sys.executable, '-I', '-B', str(TOOL / 'dby-install'), 'seed', 'set', '--image-file', str(self.target.path),
                                 '--backup-dir', str(self.backups), '--ssid', 'IEEE', '--psk-file', str(source), '--dry-run'],
                                capture_output=True, env=dict(os.environ, DBY_TRACE='1'))
        self.assertEqual(result.returncode, 0, 'seed CLI failed')
        for secret in (PASSPHRASE, VECTOR):
            self.assertFalse(secret.encode() in result.stdout + result.stderr, 'credential leaked')
        self.assertEqual(region_hash(self.target.path, 0, MIN_SIZE), before)
        self.assertFalse(self.backups.exists()); self.assertNotIn(b'Type the full', result.stdout)

    def test_no_options_rejected_and_same_content_no_backup(self):
        with self.assertRaisesRegex(BundleError, 'E_SEED_INVALID'):
            self.operate(changes={})
        self.operate()
        count = len(list(self.backups.iterdir())); before = region_hash(self.target.path, 0, MIN_SIZE)
        self.operate(ask=lambda _: self.fail('no-change requested confirmation'))
        self.assertIn('no change', self.messages)
        self.assertEqual(len(list(self.backups.iterdir())), count)
        self.assertEqual(region_hash(self.target.path, 0, MIN_SIZE), before)

    def test_confirmation_requires_full_name(self):
        with self.assertRaisesRegex(BundleError, 'E_TARGET_CONFIRM'):
            self.operate(ask=lambda _: self.target.path.name)
        self.assertFalse(self.backups.exists())
        self.assertEqual(region_hash(self.target.path, P1_START*512, P1_SECTORS*512), self.original_p1)

    def test_backup_failure_prevents_card_write(self):
        with patch.object(S, 'validate_backup', side_effect=BundleError('E_SEED_RESTORE_INVALID', 'synthetic')):
            with self.assertRaisesRegex(BundleError, 'E_SEED_BACKUP'):
                self.operate()
        self.assertEqual(region_hash(self.target.path, P1_START*512, P1_SECTORS*512), self.original_p1)

    def test_failure_after_switch_rolls_back_exact_raw_bytes(self):
        for phase in ('after_switch', 'before_readback'):
            def hook(at, context):
                if at == phase:
                    raise BundleError('E_SYNTHETIC', 'Do not echo arbitrary failure details.')
            with self.assertRaisesRegex(BundleError, 'E_SEED_VERIFY.*E_SYNTHETIC.*Automatic rollback verified'):
                self.operate(hook=hook)
            self.assertEqual(region_hash(self.target.path, P1_START*512, P1_SECTORS*512), self.original_p1)

    def test_seed_directory_created_and_leftovers_refused(self):
        self.mutate(lambda store: store.command('mdeltree', '::/dby-seed'))
        self.operate()
        self.assertIn(b'refresh_hz = 60', self.state()[1]['seed.conf'])
        source = self.root / 'pending'; source.write_bytes(b'pending')
        self.mutate(lambda store: store.copy(source, 'dby-seed/unexpected.new'))
        with self.assertRaisesRegex(BundleError, 'E_SEED_LEFTOVER'):
            self.operate()

    def test_restore_raw_and_absence_deletion(self):
        old = self.state()[1]
        backup = self.operate(replacement=key())['backup']
        self.operate('restore', backup=backup)
        self.assertTrue(self.state()[1] == old, 'raw restore differs')
        self.assertIsNone(self.state()[1]['authorized_keys'])
        self.mutate(lambda store: store.remove('dby-seed/seed.conf'))
        backup = self.operate()['backup']
        self.operate('restore', backup=backup)
        self.assertIsNone(self.state()[1]['seed.conf'])

    def test_restore_tampered_extra_link_and_schema_rejected(self):
        backup = Path(self.operate()['backup'])
        original = (backup / 'seed.conf').read_bytes()
        cases = ['tamper', 'extra', 'link', 'schema']
        for case in cases:
            path = backup / ('extra' if case == 'extra' else 'seed.conf')
            if case == 'tamper': path.write_bytes(b'tampered')
            elif case == 'extra': path.write_bytes(b'extra')
            elif case == 'link': path.unlink(); path.symlink_to(self.root / 'missing')
            else:
                meta = backup / 'backup.json'; saved = meta.read_bytes(); meta.write_text('{}')
            with self.assertRaisesRegex(BundleError, 'E_SEED_RESTORE_INVALID'):
                self.operate('restore', backup=backup)
            if case == 'schema': meta.write_bytes(saved)
            elif case == 'extra': path.unlink()
            else:
                path.unlink(); path.write_bytes(original)

    def test_safe_mode_status_on_off_preserves_other_settings(self):
        self.operate(changes={'ssid': 'IEEE', 'derived_psk': True}, password=PASSPHRASE)
        self.operate('on'); self.assertTrue(self.operate('status')['safe_mode'])
        self.assertTrue(any('screen will remain black' in s for s in self.messages))
        self.assertTrue(any('safe-mode off' in s for s in self.messages))
        self.operate('off'); self.assertFalse(self.operate('status')['safe_mode'])
        parsed = R.parse(self.state()[1]['seed.conf'])[0]
        self.assertTrue(parsed['wifi "main"']['psk'] == VECTOR, 'safe mode changed credentials')
        self.assertEqual(parsed['system']['safe_mode'], 'false')
        self.assertEqual(BOOT.safe_mode(self.state()[1]['seed.conf']), False)

    def test_unmanaged_and_inconsistent_card_rejected(self):
        self.mutate(lambda store: store.remove('UBOOT/Image'))
        with self.assertRaisesRegex(BundleError, 'E_SEED_CARD_INCONSISTENT'):
            self.operate()
        self.mutate(lambda store: store.remove('manifest.json'))
        with self.assertRaisesRegex(BundleError, 'E_SEED_UNMANAGED'):
            self.operate()

    def test_dirty_p2_does_not_block_seed_and_is_unchanged(self):
        hardening.HardeningTests.edit_p2(self, 'feature needs_recovery')
        before = region_hash(self.target.path, P2_START*512, 1024**3)
        self.operate()
        self.assertEqual(region_hash(self.target.path, P2_START*512, 1024**3), before)

    def test_missing_backup_and_wizard_no_tty_before_target_access(self):
        for action, code in [('set', 'E_SEED_BACKUP'), ('wizard', 'E_SEED_NO_TTY')]:
            result = subprocess.run([sys.executable, '-I', '-B', str(TOOL / 'dby-install'), 'seed', action,
                                     '--image-file', str(self.target.path), *(['--refresh-hz', '60'] if action == 'set' else [])], capture_output=True)
            self.assertEqual(result.returncode, 1); self.assertIn(code.encode(), result.stderr)

    def test_p1_mount_adapter_never_mounts_p2(self):
        calls, mounted = [], set()
        def run(argv):
            calls.append(list(map(str, argv)))
            if argv[0] == 'mount': mounted.add(Path(argv[-1]))
            elif argv[0] == 'umount': mounted.remove(Path(argv[-1]))
            return b''
        for writable in (False, True):
            with patch.object(IO, 'run', side_effect=run), patch('dbyinstall.privileged.mounted_paths', side_effect=lambda: set(mounted)):
                with IO.p1_mount(Path('/dev/mmcblk8'), writable=writable):
                    self.assertEqual(len(mounted), 1)
        mounts = [c for c in calls if c[0] == 'mount']
        self.assertEqual(len(mounts), 2)
        self.assertTrue(all(c[-2] == '/dev/mmcblk8p1' for c in mounts))

    def test_fake_sysfs_rejects_non_sd_system_and_mounted_targets(self):
        sysroot = self.root / 'sys'; node = sysroot / 'class/block/mmcblk8'
        for name, content in [('dev', '179:0'), ('size', str(MIN_SIZE//512)), ('removable', '0'),
                              ('device/type', 'MMC'), ('holders/dm-fixture/dev', '253:0')]:
            p = node / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(content)
        mountinfo = self.root / 'mountinfo'; mountinfo.write_text('')
        original = Path.lstat
        def lstat(path):
            return SimpleNamespace(st_mode=stat.S_IFBLK | 0o600, st_rdev=os.makedev(179, 0)) if str(path) == '/dev/mmcblk8' else original(path)
        def probe(path):
            return live_probe(path, sysroot=sysroot, mountinfo=mountinfo,
                              execute=lambda _: json.dumps({'blockdevices': [dict(probe_row(), path=str(path))]}).encode())
        for media, mounts, code in [('MMC', '', 'E_TARGET_MEDIA'), ('SD', '1 0 253:0 / / rw - ext4 fixture rw\n', 'E_TARGET_MOUNTED'),
                                     ('SD', '1 0 179:0 / /media/test rw - vfat fixture rw\n', 'E_TARGET_MOUNTED')]:
            (node / 'device/type').write_text(media); mountinfo.write_text(mounts)
            with patch('dbyinstall.targets.no_links'), patch.object(Path, 'lstat', lstat):
                with self.assertRaisesRegex(BundleError, code):
                    S.operate('set', BlockDeviceTarget(Path('/dev/mmcblk8'), probe), changes={'refresh_hz': '60'}, backup_dir=self.backups, dry_run=True)

    def test_device_write_requires_root(self):
        target = BlockDeviceTarget(Path('/dev/mmcblk8'), lambda _: probe_row())
        with patch('dbyinstall.privileged.require_root', side_effect=BundleError('E_INSTALL_ROOT', 'synthetic')):
            with self.assertRaisesRegex(BundleError, 'E_INSTALL_ROOT'):
                with IO.SeedBackend(target): pass
            with IO.SeedBackend(target, dry_run=True): pass

    def test_plaintext_show_trace_and_backup_metadata_do_not_leak(self):
        self.operate(changes={'ssid': 'IEEE', 'plaintext_psk': True}, password=PASSPHRASE)
        captured = io.StringIO()
        with patch.dict(os.environ, DBY_TRACE='1'), redirect_stdout(captured), redirect_stderr(captured):
            shown = self.operate('show')
            result = self.operate(changes={'refresh_hz': '120'})
        backup = Path(result['backup'])
        scanned = captured.getvalue() + '\n'.join(self.messages) + (backup / 'backup.json').read_text()
        self.assertFalse(any(s in scanned for s in (PASSPHRASE, VECTOR)), 'credential leaked')
        self.assertTrue(PASSPHRASE.encode() in (backup / 'seed.conf').read_bytes(), 'raw backup lost plaintext')
        self.assertEqual(shown['wifi "main"']['psk'], '<set: plaintext>')

    def test_restore_failure_recovers_displaced_seed(self):
        backup = self.operate()['backup']
        self.operate(changes={'refresh_hz': '120'})
        before = self.state()[1]
        def fail(phase, context):
            if phase == 'before_readback': raise RuntimeError('synthetic')
        with self.assertRaisesRegex(BundleError, 'E_SEED_VERIFY.*Automatic rollback verified'):
            self.operate('restore', backup=backup, hook=fail)
        self.assertTrue(self.state()[1] == before, 'restore failure did not recover displaced seed')

    def test_changed_seed_after_confirmation_is_rejected(self):
        def confirm(_):
            source = self.root / 'changed'; source.write_bytes(b'version=1\n')
            self.mutate(lambda store: (store.remove('dby-seed/seed.conf'), store.copy(source, 'dby-seed/seed.conf')))
            return str(self.target.path)
        with self.assertRaisesRegex(BundleError, 'E_TARGET_CHANGED'):
            self.operate(ask=confirm)
        self.assertFalse(self.backups.exists())

    def test_fake_sd_full_transaction_unmounts_before_confirm_and_reopens(self):
        root = self.root / 'fake-p1'; root.mkdir(); core.populate_p1(root, self.old)
        # This target is an ordinary file; every filesystem probe/mount is injected.
        fake_target = self.root / 'fake-card'; fake_target.touch()
        identity = {'size': MIN_SIZE, 'rdev': fake_target.stat().st_rdev}
        target = SimpleNamespace(path=fake_target, validate=lambda: identity)
        active, events = set(), []
        @contextmanager
        def mount(device, *, writable=False):
            self.assertFalse(active, 'overlapping mounts')
            active.add(root); events.append('rw' if writable else 'ro')
            yield root
            active.remove(root); events.append('unmount')
        def confirm(_):
            self.assertFalse(active, 'confirmation with live mount')
            return str(fake_target)
        # Avoid block-device stat only in the host backup location check.
        def location(path, backend, identifier, stamp):
            self.assertFalse(active, 'backup location checked with live mount')
            return Path(path) / ('p1-fixture-' + identifier)
        with patch.object(IO, 'p1_mount', side_effect=mount), patch.object(IO, 'run', return_value=b'synthetic-table'), \
             patch('dbyinstall.privileged.require_root'), patch('dbyinstall.privileged.check_table'), \
             patch('dbyinstall.image.geometry'), patch.object(S, 'backup_location', side_effect=location):
            result = S.operate('set', target, changes={'refresh_hz': '60'}, backup_dir=self.backups, ask=confirm, emit=self.messages.append)
        self.assertFalse(active)
        self.assertEqual(events.count('rw'), 1); self.assertGreaterEqual(events.count('ro'), 3)
        self.assertIn(b'refresh_hz = 60', (root / 'dby-seed/seed.conf').read_bytes())
        self.assertTrue(Path(result['backup']).is_dir())


class SeedBoundaryTests(unittest.TestCase):
    def test_standard_vector_independent_hmac_expansion(self):
        import hmac
        # Independent RFC 8018 F expansion, not hashlib.pbkdf2_hmac.
        def prf(value): return hmac.digest(PASSPHRASE.encode(), value, 'sha1')
        blocks = []
        for number in (1, 2):
            value = prf(b'IEEE' + struct.pack('>I', number)); mixed = int.from_bytes(value, 'big')
            for _ in range(4095):
                value = prf(value); mixed ^= int.from_bytes(value, 'big')
            blocks.append(mixed.to_bytes(20, 'big'))
        self.assertTrue(b''.join(blocks)[:32].hex() == VECTOR, 'independent vector mismatch')

    def test_no_echo_fallback_is_refused(self):
        import warnings
        def fallback(_):
            warnings.warn('synthetic no-echo failure', CLI.getpass.GetPassWarning)
            self.fail('getpass fallback was allowed')
        with patch.object(CLI, 'tty'), patch.object(CLI.getpass, 'getpass', side_effect=fallback):
            with self.assertRaisesRegex(BundleError, 'E_SEED_NO_TTY'):
                CLI.secret_prompt('secret: ')

    def test_unknown_names_and_other_fields_cannot_echo_known_psk(self):
        raw = ('[wifi "main"]\nssid=IEEE\npsk=' + VECTOR + '\n[' + VECTOR + ']\nx=y\n').encode()
        old = dict(EMPTY, **{'seed.conf': raw})
        new, discarded, junk = M.edit(old, {'refresh_hz': '60'})
        lines = []
        S.output(M.preview(old, new, discarded, junk), [old, new], lines.append)
        self.assertFalse(VECTOR in ''.join(lines), 'credential leaked in unknown name')

    def test_seed_limits_and_input_types(self):
        for raw in (b'\x00', b'\xff', b'a' * (R.LIMIT+1)):
            with self.assertRaisesRegex(BundleError, 'E_SEED_INVALID'):
                M.edit(dict(EMPTY, **{'seed.conf': raw}), {'refresh_hz': '60'})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); link = root / 'link'; link.symlink_to(root / 'missing')
            with self.assertRaisesRegex(BundleError, 'E_SEED_PSK_SOURCE'):
                CLI.password_file(link)
            fifo = root / 'fifo'; os.mkfifo(fifo)
            with self.assertRaisesRegex(BundleError, 'E_SEED_INVALID'):
                IO.read_file(fifo)

    def test_unused_label_and_empty_country_are_rejected(self):
        for changes in ({'remove_wifi': 'bad"label'}, {'wifi': 'bad"label', 'refresh_hz': '60'}):
            with self.assertRaisesRegex(BundleError, 'wifi label'):
                M.edit(EMPTY, changes)
        captured = io.StringIO()
        with redirect_stderr(captured):
            result = CLI.main(['seed', 'set', '--image-file', '/nonexistent', '--backup-dir', '/nonexistent', '--country', ''])
        self.assertEqual(result, 1); self.assertIn('Invalid wifi country', captured.getvalue())
