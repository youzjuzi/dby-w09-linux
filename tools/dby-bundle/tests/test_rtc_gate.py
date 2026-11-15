# SPDX-License-Identifier: GPL-2.0-only
"""Minimal fail-closed gate coverage; fake sysfs only, no host clock writes."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from test_device_overlay import helper


class RtcGateTests(unittest.TestCase):
    def setUp(self):
        self.module = helper('dby-w09-rtc-gate')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.rtc = self.root / 'rtc'
        (self.rtc / 'device').mkdir(parents=True)
        self.pmic = self.root / 'pmic'
        self.node = self.pmic / 'nvram@b100'
        cell = self.node / 'rtc-offset@bc'
        cell.mkdir(parents=True)
        rtc_node = self.pmic / 'rtc@6000'
        rtc_node.mkdir()
        (self.rtc / 'device/of_node').symlink_to(rtc_node)
        self.devices = self.root / 'nvmem'
        device = self.devices / 'spmi_sdam7'
        device.mkdir(parents=True)
        (device / 'of_node').symlink_to(self.node)
        self.gate = device / 'force_ro'
        self.gate.write_text('1\n')
        for path, data in {
            self.node / 'compatible': b'qcom,spmi-sdam\0',
            self.node / 'reg': bytes.fromhex('0000b100'),
            self.node / 'read-only': b'',
            cell / 'reg': bytes.fromhex('000000bc00000004'),
            cell / 'phandle': bytes.fromhex('00000042'),
            rtc_node / 'nvmem-cell-names': b'offset\0',
            rtc_node / 'nvmem-cells': bytes.fromhex('00000042'),
        }.items():
            path.write_bytes(data)
        (self.rtc / 'since_epoch').write_text('1800000000\n')
        self.locate = lambda: self.module.find_gate(self.rtc, self.devices, self.pmic)

    def run_gate(self, **kwargs):
        args = dict(rtc=self.rtc, locate=self.locate, synced=lambda: True,
                    now=lambda: 1800000000, monotonic=lambda: 0, emit=Mock())
        args.update(kwargs)
        return self.module.run(**args)

    def test_threshold_both_directions(self):
        for difference, expected in [(600, '0'), (-600, '0'), (601, '1'), (-601, '1')]:
            with self.subTest(difference=difference):
                self.gate.write_text('1\n')
                self.run_gate(now=lambda: 1800000000 + difference)
                self.assertEqual(self.gate.read_text().strip(), expected)

    def test_sync_timeout_uses_monotonic(self):
        ticks = [0]
        def sleep(seconds):
            ticks[0] += seconds
        self.run_gate(synced=lambda: False, monotonic=lambda: ticks[0], sleep=sleep)
        self.assertEqual(ticks[0], 600)
        self.assertEqual(self.gate.read_text(), '1\n')

    def test_sync_lost_before_unlock(self):
        self.run_gate(synced=Mock(side_effect=[True, False]))
        self.assertEqual(self.gate.read_text(), '1\n')

    def test_invalid_reading_or_provider_never_unlocks(self):
        (self.rtc / 'since_epoch').write_text('invalid')
        self.run_gate()
        self.assertEqual(self.gate.read_text(), '1\n')
        (self.rtc / 'since_epoch').write_text('1800000000')
        (self.node / 'reg').write_bytes(bytes.fromhex('0000b000'))
        self.run_gate()
        self.assertEqual(self.gate.read_text(), '1\n')

    def test_wrong_cell_reference_or_ambiguous_provider(self):
        ref = self.pmic / 'rtc@6000/nvmem-cells'
        ref.write_bytes(bytes.fromhex('00000043'))
        self.run_gate()
        self.assertEqual(self.gate.read_text(), '1\n')
        ref.write_bytes(bytes.fromhex('00000042'))
        duplicate = self.devices / 'spmi_sdam8'
        duplicate.mkdir()
        (duplicate / 'of_node').symlink_to(self.node)
        self.run_gate()
        self.assertEqual(self.gate.read_text(), '1\n')
