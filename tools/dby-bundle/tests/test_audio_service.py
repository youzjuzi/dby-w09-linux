# SPDX-License-Identifier: GPL-2.0-only
"""Offline startup unit and runtime enablement contract; no services run."""
import configparser
from pathlib import Path
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from dbybundle.build_archive import overlay

ROOT = TOOL / 'device-rootfs'
NAME = 'dby-w09-audio.service'
UNIT = 'etc/systemd/system/' + NAME
LINK = 'etc/systemd/system/multi-user.target.wants/' + NAME


class AudioService(unittest.TestCase):
    def test_unit_contract(self):
        unit = configparser.ConfigParser(interpolation=None)
        unit.optionxform = str
        unit.read(ROOT / UNIT)
        self.assertEqual(set(unit.sections()), {'Unit', 'Service', 'Install'})
        self.assertEqual(dict(unit['Unit']), {
            'Description': 'Initialize DBY-W09 onboard microphones and speakers',
            'After': 'local-fs.target systemd-udevd.service systemd-modules-load.service',
            'ConditionPathExists': '!/run/modprobe.d/dby-safe-mode.conf'})
        self.assertEqual(dict(unit['Service']), {
            'Type': 'oneshot', 'ExecStart': '/run/dby-device/bin/dby-audio-start',
            'RemainAfterExit': 'yes', 'TimeoutStartSec': '90s', 'Restart': 'no'})
        self.assertEqual(dict(unit['Install']), {'WantedBy': 'multi-user.target'})
        self.assertFalse(set(unit['Unit'].get('Before', '').split()) &
                         {'gdm.service', 'display-manager.service', 'graphical.target'})
        script = ROOT / unit['Service']['ExecStart'].removeprefix('/run/dby-device/')
        self.assertTrue(script.is_file())
        self.assertTrue(script.stat().st_mode & 0o111)

    def test_builder_enablement_and_config_entries(self):
        self.assertEqual((ROOT / LINK).readlink().as_posix(), '../' + NAME)
        self.assertEqual((ROOT / 'wants-links.txt').read_text().splitlines().count(LINK[4:]), 1)
        with tempfile.TemporaryDirectory() as directory:
            tree = Path(directory)
            overlay(tree, touch=True)  # validates every wants link against its list
            self.assertEqual((tree / LINK).readlink().as_posix(), '../' + NAME)
            self.assertEqual((tree / UNIT).read_bytes(), (ROOT / UNIT).read_bytes())
            configs = [p.relative_to(tree/'etc').as_posix() for p in (tree/'etc').rglob('*')
                       if p.is_file() or p.is_symlink()]
            self.assertEqual(sorted(p for p in configs if NAME in p), [UNIT[4:], LINK[4:]])


if __name__ == '__main__':
    unittest.main()
