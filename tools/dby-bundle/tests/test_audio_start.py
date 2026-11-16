# SPDX-License-Identifier: GPL-2.0-only
"""Run split configuration steps with mocked mount/ALSA/desktop commands."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'device-rootfs'

class AudioConfiguration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ('run', 'proc', 'bin'):
            (self.root / name).mkdir(parents=True)
        self.log = self.root / 'calls'
        self.log.touch()
        (self.root/'proc/mounts').touch()
        self.env = os.environ | {'MOCK_ROOT': str(self.root)}
        self.scripts = []
        for name in ('config', 'ucm'):
            text = (ROOT/'bin'/('dby-w09-audio-'+name)).read_text()
            for key, value in [('RUN', 'run'), ('PROC', 'proc')]:
                text = text.replace(key+'=/'+value, key+'='+str(self.root/value))
            text = text.replace('UCM_DST=/usr/share/alsa/ucm2/conf.d/sm8250',
                                'UCM_DST='+str(self.root/'ucm-dst'))
            text = text.replace('WP_DST=/etc/wireplumber/wireplumber.conf.d',
                                'WP_DST='+str(self.root/'etc/wireplumber/wireplumber.conf.d'))
            text = text.replace('PATH=/usr/sbin:', 'PATH='+str(self.root/'bin')+':/usr/sbin:')
            script = self.root/name
            script.write_text(text)
            self.scripts.append(script)
        self.mock('id', 'if [ "$1" = -nu ]; then [ "$2" != 1999 ] || exit 1; echo testuser$2; else echo 0; fi\n')
        self.mock('su', 'echo "su $*" >> "$MOCK_ROOT/calls"; exit "${FAIL_SU:-0}"\n')
        for name in ('modprobe', 'sleep'):
            self.mock(name, 'echo "FORBIDDEN $0" >> "$MOCK_ROOT/calls"; exit 99\n')

    def mock(self, name, body):
        path = self.root/'bin'/name
        path.write_text('#!/bin/sh\n'+body)
        path.chmod(0o755)

    def run_step(self, index):
        return subprocess.run(['bash', str(self.scripts[index])], env=self.env,
                              text=True, capture_output=True, timeout=8)

    def run_start(self, ok=False):
        # Model systemd Requires/After: failed config prevents UCM activation.
        config = self.run_step(0)
        result = self.run_step(1) if config.returncode == 0 else config
        self.assertEqual(result.returncode == 0, ok, result.stdout+result.stderr)
        self.assertNotIn('FORBIDDEN', self.log.read_text())
        return config.stdout + (result.stdout if result is not config else '')

    def test_config_does_not_touch_hardware(self):
        self.ucm()
        self.wp()
        self.desktop_path()
        self.assertEqual(self.run_step(0).returncode, 0)
        calls = self.log.read_text().splitlines()
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(line.startswith('mount ') for line in calls))
        self.assertFalse((self.root/'run/dby-audio-attempted').exists())

    def test_ucm_works_with_pas_already_loaded(self):
        self.ucm(mounted=True)
        (self.root/'sys/module/qcom_q6v5_pas').mkdir(parents=True)
        self.assertEqual(self.run_step(1).returncode, 0)
        self.assertIn('alsaucm -c hw:DBYW09 set _verb HiFi set _enadev Mic', self.log.read_text())
        self.assertNotIn('mount ', self.log.read_text())

    def test_second_ucm_attempt_refused(self):
        self.ucm(mounted=True)
        self.assertEqual(self.run_step(1).returncode, 0)
        before = self.log.read_text()
        self.assertNotEqual(self.run_step(1).returncode, 0)
        self.assertEqual(self.log.read_text(), before)

    def test_mount_and_initialization_order(self):
        self.ucm()
        self.wp()
        self.desktop_path()
        self.run_start(True)
        calls = self.log.read_text()
        events = ['mount --bind', 'mount -o remount,bind,ro,nosuid,nodev',
                  'alsaucm -c hw:DBYW09 set _verb HiFi set _enadev Mic',
                  'udevadm trigger --action=change --subsystem-match=sound',
                  'su - testuser1000']
        self.assertEqual([calls.index(e) for e in events], sorted(calls.index(e) for e in events))

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
        self.assertNotIn('alsaucm ', self.log.read_text())
        self.assertFalse((self.root/'run/dby-audio-ready').exists())

    def test_ucm_remount_failure(self):
        self.ucm()
        self.env['FAIL_MOUNT'] = '-o'
        self.assertIn('UCM2 ro remount failed', self.run_start())
        self.assertNotIn('alsaucm ', self.log.read_text())
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
