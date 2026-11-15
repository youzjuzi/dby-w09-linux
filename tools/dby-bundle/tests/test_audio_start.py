"""Exercise startup decisions with fake sysfs and a fake modprobe, never ALSA."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'device-rootfs/bin/dby-audio-start'

class AudioStart(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ('run', 'sys', 'proc/asound', 'bin'):
            (self.root / name).mkdir(parents=True)
        self.log = self.root / 'calls'
        self.log.touch()
        (self.root/'proc/uptime').write_text('7.14 0.00\n')
        sleep = self.root/'bin/sleep'
        sleep.write_text('''#!/usr/bin/env python3
from pathlib import Path
import os, sys
root = Path(os.environ['MOCK_ROOT'])
assert sys.argv[1:] == ['0.2']
path = root/'proc/uptime'
seconds, fraction = path.read_text().split()[0].split('.')
ticks = int(seconds)*100 + int(fraction) + 20
path.write_text(f'{ticks//100}.{ticks%100:02d} 0.00\\n')
counter = root/'sleeps'
count = int(counter.read_text())+1 if counter.exists() else 1
counter.write_text(str(count))
if count == int(os.environ.get('APR_AT', '0')):
    for svc in (3, 4, 7, 8):
        (root/f'sys/bus/aprbus/devices/aprsvc:service:4:{svc}/driver').symlink_to(root/'sys')
if count == int(os.environ.get('CARD_AT', '0')):
    (root/'proc/asound/cards').write_text(' 2 [DBYW09 ]: sm8250 - DBY-W09\\n')
if os.environ.get('BAD_UPTIME'):
    path.write_text('invalid\\n')
''')
        sleep.chmod(0o755)
        mock = self.root / 'bin/modprobe'
        mock.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> "$MOCK_ROOT/calls"\n'
                        '[ "$1" != "$FAIL_MODULE" ] || exit 7\n')
        mock.chmod(0o755)
        uid = self.root / 'bin/id'
        uid.write_text('#!/bin/sh\nif [ "$1" = -nu ]; then\n'
                       '  [ "$2" != 1999 ] || exit 1\n'
                       '  echo testuser$2\nelse echo 0; fi\n'); uid.chmod(0o755)
        su = self.root/'bin/su'
        su.write_text('#!/bin/sh\necho "su $*" >> "$MOCK_ROOT/calls"\n'
                      'exit "${FAIL_SU:-0}"\n')
        su.chmod(0o755)
        text = SCRIPT.read_text()
        self.assertEqual(text.count('deadline+1500'), 2)
        self.assertIn('--kill-after=2 15 modprobe', text)
        for key, value in [('RUN', 'run'), ('SYS', 'sys'), ('PROC', 'proc')]:
            text = text.replace(key+'=/'+value, key+'='+str(self.root/value))
        text = text.replace('UCM_DST=/usr/share/alsa/ucm2/conf.d/sm8250',
                            'UCM_DST='+str(self.root/'ucm-dst'))
        text = text.replace('WP_DST=/etc/wireplumber/wireplumber.conf.d',
                            'WP_DST='+str(self.root/'etc/wireplumber/wireplumber.conf.d'))
        text = text.replace('PATH=/usr/sbin:', 'PATH='+str(self.root/'bin')+':/usr/sbin:')
        text = text.replace('deadline+1500', 'deadline+100') # one fake second
        self.script = self.root / 'start'
        self.script.write_text(text)
        self.env = os.environ | {'MOCK_ROOT': str(self.root), 'FAIL_MODULE': ''}

    def ucm(self, mounted=False):
        src = self.root/'run/dby-device/ucm2/conf.d/sm8250'
        dst = self.root/'ucm-dst'
        src.mkdir(parents=True); dst.mkdir()
        (src/'DBY-W09.conf').write_text('mock')
        if mounted:
            (dst/'DBY-W09.conf').write_text('mock')
        (self.root/'proc/mounts').write_text(
            f'fake {dst} none ro 0 0\n' if mounted else '')
        for name, body in {
            'mount': '[ "$1" != "$FAIL_MOUNT" ] || exit 8\n'
                     'case "$*" in *wireplumber.conf.d*) [ "$1" != "$FAIL_WP_MOUNT" ] || exit 8;; esac\n'
                     'if [ "$1" = --bind ]; then cp "$2/"* "$3/"; fi\n',
            'alsaucm': 'for arg in "$@"; do [ "$arg" != "$FAIL_UCM" ] || exit 9; done\n',
            'udevadm': 'exit "${FAIL_UDEV:-0}"\n',
        }.items():
            mock = self.root/'bin'/name
            mock.write_text('#!/bin/sh\necho "'+name+' $*" >> "$MOCK_ROOT/calls"\n'+body)
            mock.chmod(0o755)
        self.env.update(FAIL_MOUNT='', FAIL_UCM='', FAIL_WP_MOUNT='')
        self.services()
        (self.root/'proc/asound/cards').write_text(' 2 [DBYW09 ]: sm8250 - DBY-W09\n')

    def run_start(self, ok=False):
        result = subprocess.run(['bash', str(self.script)], env=self.env,
                                text=True, capture_output=True, timeout=8)
        self.assertEqual(result.returncode == 0, ok, result.stdout+result.stderr)
        return result.stdout

    def services(self, bound=True):
        for svc in (3, 4, 7, 8):
            p = self.root / f'sys/bus/aprbus/devices/aprsvc:service:4:{svc}'
            p.mkdir(parents=True)
            if bound:
                (p/'driver').symlink_to(self.root/'sys')

    def test_success_and_repeat(self):
        self.services()
        (self.root/'proc/asound/cards').write_text(' 2 [DBYW09 ]: sm8250 - DBY-W09\n')
        self.run_start(True)
        self.assertTrue((self.root/'run/dby-audio-ready').exists())
        before = self.log.read_text()
        self.assertLess(before.index('qcom_q6v5_pas'), before.index('pinctrl-sm8250-lpass-lpi'))
        self.run_start()
        self.assertEqual(before, self.log.read_text())

    def test_apr_absent(self):
        self.assertIn('stage B skipped', self.run_start())
        self.assertEqual((self.root/'sleeps').read_text(), '5')
        self.assertEqual((self.root/'proc/uptime').read_text(), '8.14 0.00\n')
        self.assertNotIn('pinctrl', self.log.read_text())
        self.assertTrue((self.root/'run/dby-audio-attempted').exists())

    def test_apr_unbound(self):
        self.services(False)
        self.assertIn('stage B skipped', self.run_start())

    def test_module_failure_latches_attempt(self):
        self.env['FAIL_MODULE'] = 'q6afe'
        self.run_start()
        before = self.log.read_text()
        self.assertNotIn('qcom_q6v5_pas', before)
        self.run_start()
        self.assertEqual(before, self.log.read_text())

    def test_speaker_failure_prevents_card(self):
        self.services()
        self.env['FAIL_MODULE'] = 'dby-tfa9874'
        self.run_start()
        self.assertIn('dby-tfa9874', self.log.read_text())
        self.assertNotIn('snd-soc-sm8250', self.log.read_text())
        self.assertFalse((self.root/'run/dby-audio-ready').exists())

    def test_speaker_packaging(self):
        root = SCRIPT.parents[2]
        modules = (root/'modules.list').read_text().splitlines()
        entries = [m for m in modules if m and not m.startswith('#')]
        self.assertEqual(len(entries), 118)
        self.assertIn('kernel/sound/soc/codecs/dby-tfa9874.ko', entries)
        self.assertIn('blacklist dby_tfa9874',
                      (root/'device-rootfs/etc/modprobe.d/10-dby-audio.conf').read_text())
        self.assertIn('SectionDevice."Speaker"',
                         (root/'device-rootfs/ucm2/conf.d/sm8250/HiFi.conf').read_text())

    def test_pas_already_loaded(self):
        (self.root/'sys/module/qcom_q6v5_pas').mkdir(parents=True)
        self.run_start()
        self.assertEqual('', self.log.read_text())

    def test_remoteproc_running(self):
        p = self.root/'sys/class/remoteproc/remoteproc0'; p.mkdir(parents=True)
        (p/'name').write_text('17300000.remoteproc:adsp\n')
        (p/'state').write_text('running\n')
        self.run_start()
        self.assertEqual('', self.log.read_text())

    def test_missing_card(self):
        self.services()
        self.assertIn('Onboard audio card timeout', self.run_start())
        self.assertEqual((self.root/'sleeps').read_text(), '5')
        self.assertFalse((self.root/'run/dby-audio-ready').exists())

    def test_no_wall_clock_deadlines(self):
        self.assertNotIn('SECONDS', SCRIPT.read_text())
        # This is not a host clock-step simulation: forbid date and advance only
        # fake uptime to exercise both delayed readiness paths without privileges.
        date = self.root/'bin/date'
        date.write_text('#!/bin/sh\necho unexpected-date >> "$MOCK_ROOT/calls"\nexit 99\n')
        date.chmod(0o755)
        self.services(False)
        self.env.update(APR_AT='3', CARD_AT='5')
        self.run_start(True)
        self.assertEqual((self.root/'sleeps').read_text(), '5')
        self.assertEqual((self.root/'proc/uptime').read_text(), '8.14 0.00\n')
        self.assertNotIn('unexpected-date', self.log.read_text())

    def test_mono_centiseconds_are_decimal(self):
        source = SCRIPT.read_text()
        function = source[source.index('mono() {'):source.index('\n}', source.index('mono() {'))+2]
        for value, expected in [('7.14', '714'), ('0.00', '0'), ('08.09', '809'),
                                ('123456.78', '12345678')]:
            with self.subTest(value=value):
                (self.root/'proc/uptime').write_text(value+' 0.00\n')
                result = subprocess.run(['bash', '-c', 'PROC=$1\n'+function+'\nmono',
                                         'fixture', str(self.root/'proc')],
                                        capture_output=True, text=True, check=True)
                self.assertEqual(result.stdout.strip(), expected)

    def test_missing_uptime_fails_closed(self):
        (self.root/'proc/uptime').unlink()
        self.assertIn('Invalid monotonic uptime', self.run_start())
        self.assertNotIn('pinctrl', self.log.read_text())
        self.assertFalse((self.root/'run/dby-audio-ready').exists())

    def test_malformed_uptime_fails_closed(self):
        for value in ['', '7.1 0.00\n', '-1.00 0.00\n', '7.14 bad\n',
                      '99999999999999999999.00 0.00\n', '1+1.00 0.00\n']:
            with self.subTest(value=value):
                (self.root/'run/dby-audio-attempted').unlink(missing_ok=True)
                (self.root/'proc/uptime').write_text(value)
                self.assertIn('Invalid monotonic uptime', self.run_start())
                self.assertNotIn('pinctrl', self.log.read_text())
                self.assertFalse((self.root/'run/dby-audio-ready').exists())

    def test_uptime_failure_during_apr_wait(self):
        self.env['BAD_UPTIME'] = '1'
        self.assertIn('Invalid monotonic uptime', self.run_start())
        self.assertNotIn('pinctrl', self.log.read_text())
        self.assertFalse((self.root/'run/dby-audio-ready').exists())

    def test_uptime_failure_during_card_wait(self):
        self.services()
        self.env['BAD_UPTIME'] = '1'
        self.assertIn('Invalid monotonic uptime', self.run_start())
        self.assertFalse((self.root/'run/dby-audio-ready').exists())

    def test_lock(self):
        import fcntl
        with (self.root/'run/dby-audio-start.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.run_start()
        self.assertEqual('', self.log.read_text())

    def test_ucm_mount_before_stage_b_and_initialize(self):
        self.ucm()
        self.run_start(True)
        calls = self.log.read_text()
        events = ['qcom_q6v5_pas', 'mount --bind',
                  'mount -o remount,bind,ro,nosuid,nodev',
                  'pinctrl-sm8250-lpass-lpi', 'dby-tfa9874', 'snd-soc-sm8250',
                  'alsaucm -c hw:DBYW09 set _verb HiFi set _enadev Mic',
                  'udevadm trigger --action=change --subsystem-match=sound']
        self.assertEqual(sorted(calls.index(e) for e in events),
                         [calls.index(e) for e in events])

    def test_ucm_already_mounted(self):
        self.ucm(mounted=True)
        self.run_start(True)
        self.assertNotIn('mount ', self.log.read_text())
        self.assertIn('set _enadev Mic', self.log.read_text())

    def test_ucm_verb_failure(self):
        self.ucm()
        self.env['FAIL_UCM'] = '_verb'
        self.assertIn('UCM2 Mic initialization failed', self.run_start())
        self.assertNotIn('udevadm ', self.log.read_text())
        self.assertFalse((self.root/'run/dby-audio-ready').exists())

    def test_ucm_device_failure(self):
        self.ucm()
        self.env['FAIL_UCM'] = '_enadev'
        self.assertIn('UCM2 Mic initialization failed', self.run_start())
        self.assertNotIn('udevadm ', self.log.read_text())
        self.assertFalse((self.root/'run/dby-audio-ready').exists())

    def test_ucm_bind_failure(self):
        self.ucm()
        self.env['FAIL_MOUNT'] = '--bind'
        self.assertIn('UCM2 bind mount failed', self.run_start())
        self.assertNotIn('pinctrl-sm8250', self.log.read_text())
        self.assertFalse((self.root/'run/dby-audio-ready').exists())

    def test_ucm_remount_failure(self):
        self.ucm()
        self.env['FAIL_MOUNT'] = '-o'
        self.assertIn('UCM2 ro remount failed', self.run_start())
        self.assertNotIn('pinctrl-sm8250', self.log.read_text())
        self.assertFalse((self.root/'run/dby-audio-ready').exists())

    def test_ucm_udev_failure_nonfatal(self):
        self.ucm()
        self.env['FAIL_UDEV'] = '1'
        self.run_start(True)
        self.assertTrue((self.root/'run/dby-audio-ready').exists())

    def desktop_path(self, uid='1000'):
        directory = self.root/'run/user'/uid
        directory.mkdir(parents=True)
        (directory/'pipewire-0').touch()
        return directory

    def test_wireplumber_restart_after_ucm(self):
        self.ucm()
        directory = self.desktop_path()
        self.run_start(True)
        calls = self.log.read_text()
        command = f'su - testuser1000 -c XDG_RUNTIME_DIR={directory} systemctl --user restart wireplumber'
        self.assertIn(command, calls)
        self.assertLess(calls.index('set _enadev Mic'), calls.index(command))
        self.assertLess(calls.index('udevadm trigger'), calls.index(command))
        self.assertTrue((self.root/'run/dby-audio-ready').exists())

    def test_wireplumber_restart_failure_nonfatal(self):
        self.ucm()
        self.desktop_path()
        self.env['FAIL_SU'] = '1'
        self.run_start(True)
        self.assertIn('su - testuser1000', self.log.read_text())
        self.assertTrue((self.root/'run/dby-audio-ready').exists())

    def test_wireplumber_skips_invalid_or_missing_user(self):
        self.ucm()
        self.desktop_path('invalid')
        self.desktop_path('1999')
        directory = self.root/'run/user/1000'
        directory.mkdir(parents=True)
        # No PipeWire path for this otherwise valid user.
        self.run_start(True)
        self.assertNotIn('su ', self.log.read_text())

    def test_wireplumber_not_restarted_after_ucm_failure(self):
        self.ucm()
        self.desktop_path()
        self.env['FAIL_UCM'] = '_enadev'
        self.run_start()
        self.assertNotIn('su ', self.log.read_text())

    def wp(self, exists=False, mounted=False, nonempty=False):
        src = self.root/'run/dby-device/wireplumber/wireplumber.conf.d'
        dst = self.root/'etc/wireplumber/wireplumber.conf.d'
        src.mkdir(parents=True)
        (src/'60-dby-speaker-s16.conf').write_text('fixture')
        if exists or mounted or nonempty:
            dst.mkdir(parents=True)
        if nonempty:
            (dst/'.owner-config').write_text('keep')
        if mounted:
            with (self.root/'proc/mounts').open('a') as f:
                f.write(f'fake {dst} none ro 0 0\n')
        return src, dst

    def test_wp_missing_source_skips_without_creating_directory(self):
        self.ucm(mounted=True)
        self.run_start(True)
        self.assertFalse((self.root/'etc/wireplumber').exists())
        self.assertNotIn('mount ', self.log.read_text())

    def test_wp_creates_0755_directory_and_mounts_before_restart(self):
        self.ucm(mounted=True)
        src, dst = self.wp()
        self.desktop_path()
        self.run_start(True)
        self.assertEqual(dst.stat().st_mode & 0o777, 0o755)
        self.assertEqual(dst.parent.stat().st_mode & 0o777, 0o755)
        calls = self.log.read_text()
        events = [f'mount --bind {src} {dst}',
                  f'mount -o remount,bind,ro,nosuid,nodev {dst}', 'su - testuser1000']
        self.assertEqual([calls.index(e) for e in events], sorted(calls.index(e) for e in events))

    def test_wp_existing_empty_directory_mounts(self):
        self.ucm(mounted=True)
        src, dst = self.wp(exists=True)
        self.run_start(True)
        self.assertIn(f'mount --bind {src} {dst}', self.log.read_text())

    def test_wp_existing_mount_skips_even_when_nonempty(self):
        self.ucm(mounted=True)
        self.wp(mounted=True, nonempty=True)
        self.run_start(True)
        self.assertNotIn('mount ', self.log.read_text())

    def test_wp_nonempty_warns_without_hiding_owner_config(self):
        self.ucm(mounted=True)
        src, dst = self.wp(nonempty=True)
        self.desktop_path()
        self.assertIn('WARNING: WirePlumber configuration directory is not empty', self.run_start(True))
        self.assertNotIn('mount ', self.log.read_text())
        self.assertIn('su - testuser1000', self.log.read_text())
        self.assertEqual((dst/'.owner-config').read_text(), 'keep')
        self.assertFalse((dst/'60-dby-speaker-s16.conf').exists())

    def test_wp_bind_failure_stops_before_restart(self):
        self.ucm(mounted=True)
        self.wp()
        self.desktop_path()
        self.env['FAIL_WP_MOUNT'] = '--bind'
        self.assertIn('WirePlumber bind mount failed', self.run_start())
        self.assertNotIn('su ', self.log.read_text())
        self.assertFalse((self.root/'run/dby-audio-ready').exists())

    def test_wp_remount_failure_stops_before_restart(self):
        self.ucm(mounted=True)
        self.wp()
        self.desktop_path()
        self.env['FAIL_WP_MOUNT'] = '-o'
        self.assertIn('WirePlumber ro remount failed', self.run_start())
        self.assertNotIn('su ', self.log.read_text())
        self.assertFalse((self.root/'run/dby-audio-ready').exists())

if __name__ == '__main__':
    unittest.main()
