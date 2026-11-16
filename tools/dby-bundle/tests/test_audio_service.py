# SPDX-License-Identifier: GPL-2.0-only
"""Offline audio autoload and service wiring; never activates host services."""
import configparser
from pathlib import Path
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from dbybundle.build_archive import overlay

ROOT = TOOL / 'device-rootfs'
CONFIG = 'dby-w09-audio-config.service'
UCM = 'dby-w09-audio-ucm.service'
LINK = 'etc/systemd/system/multi-user.target.wants/' + CONFIG


def unit(name):
    result = configparser.ConfigParser(interpolation=None)
    result.optionxform = str
    result.read(ROOT / 'etc/systemd/system' / name)
    return result


class AudioService(unittest.TestCase):
    def test_configuration_precedes_login_without_hardware_work(self):
        config = unit(CONFIG)
        self.assertEqual(config['Unit']['After'], 'local-fs.target')
        self.assertEqual(config['Unit']['Before'], 'display-manager.service')
        self.assertEqual(config['Install']['WantedBy'], 'multi-user.target')
        self.assertEqual(config['Service']['ExecStart'], '/run/dby-device/bin/dby-w09-audio-config')
        text = (ROOT/'bin/dby-w09-audio-config').read_text()
        for command in ('modprobe ', 'alsaucm ', 'udevadm ', 'systemctl ', 'sleep ', '/sys/'):
            self.assertNotIn(command, text)

    def test_ucm_only_triggered_by_card(self):
        ucm = unit(UCM)
        self.assertEqual(ucm['Unit']['After'], CONFIG)
        self.assertEqual(ucm['Unit']['Requires'], CONFIG)
        self.assertNotIn('Install', ucm)
        self.assertNotIn('Before', ucm['Unit'])
        self.assertNotIn('WantedBy', (ROOT/'etc/systemd/system'/UCM).read_text())
        self.assertEqual(ucm['Service']['ExecStart'], '/run/dby-device/bin/dby-w09-audio-ucm')
        rules = (ROOT/'etc/udev/rules.d/90-dby-audio.rules').read_text().splitlines()
        self.assertEqual([r for r in rules if r and not r.startswith('#')], [
            'ACTION=="change", SUBSYSTEM=="sound", KERNEL=="card*", '
            'ENV{SOUND_INITIALIZED}=="1", ATTR{id}=="DBYW09", '
            'TAG+="systemd", ENV{SYSTEMD_WANTS}+="dby-w09-audio-ucm.service"'])
        text = (ROOT/'bin/dby-w09-audio-ucm').read_text()
        for command in ('modprobe ', 'sleep ', 'mount ', 'until ', 'while '):
            self.assertNotIn(command, text)

    def test_conditions_and_no_restart(self):
        for name in (CONFIG, UCM):
            config = unit(name)
            self.assertEqual(config['Unit']['ConditionPathExists'], '!/run/modprobe.d/dby-safe-mode.conf')
            self.assertEqual(config['Service']['Type'], 'oneshot')
            self.assertEqual(config['Service']['RemainAfterExit'], 'yes')
            self.assertEqual(config['Service']['Restart'], 'no')
            script = ROOT / config['Service']['ExecStart'].removeprefix('/run/dby-device/')
            self.assertTrue(script.is_file())
            self.assertTrue(script.stat().st_mode & 0o111)

    def test_removed_manual_loader_and_blacklist(self):
        for path in ('bin/dby-audio-start', 'etc/modprobe.d/10-dby-audio.conf',
                     'etc/systemd/system/dby-w09-audio.service',
                     'etc/systemd/system/multi-user.target.wants/dby-w09-audio.service'):
            self.assertFalse((ROOT/path).exists())
            self.assertFalse((ROOT/path).is_symlink())
        # All modules have modalias or dependency entrances; no ordering list.
        self.assertFalse((ROOT/'etc/modules-load.d/dby-w09-audio.conf').exists())

    def test_builder_enablement_and_config_entries(self):
        self.assertEqual((ROOT / LINK).readlink().as_posix(), '../' + CONFIG)
        self.assertEqual((ROOT / 'wants-links.txt').read_text().splitlines().count(LINK[4:]), 1)
        with tempfile.TemporaryDirectory() as directory:
            tree = Path(directory)
            overlay(tree, touch=True)  # checks all wants links against the list
            self.assertEqual((tree / LINK).readlink().as_posix(), '../' + CONFIG)
            configs = {p.relative_to(tree/'etc').as_posix() for p in (tree/'etc').rglob('*')
                       if p.is_file() or p.is_symlink()}
            self.assertEqual({p for p in configs if 'audio' in p}, {
                'systemd/system/' + CONFIG, 'systemd/system/' + UCM,
                LINK[4:], 'udev/rules.d/90-dby-audio.rules'})
            self.assertNotIn('systemd/system/multi-user.target.wants/' + UCM, configs)


if __name__ == '__main__':
    unittest.main()
