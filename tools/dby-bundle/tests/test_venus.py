# SPDX-License-Identifier: GPL-2.0-only
"""SM8250 Venus packaging policy and private-input contract using synthetic bytes."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from dbybundle import BundleError
from dbybundle.build_inputs import private_inventory
from dbybundle.build_archive import merge_private


class VenusTests(unittest.TestCase):
    def test_module_policy(self):
        entries = [line for line in (TOOL / 'modules.list').read_text().splitlines()
                   if line and not line.startswith('#')]
        self.assertEqual(len(entries), len(set(entries)))
        for name in ('core', 'dec', 'enc'):
            self.assertIn('kernel/drivers/media/platform/qcom/venus/venus-' + name + '.ko', entries)
        self.assertFalse(any(Path(line).name == 'qcom-iris.ko' for line in entries))

    def test_manual_only_modprobe_policy(self):
        path = TOOL / 'device-rootfs/etc/modprobe.d/11-dby-venus.conf'
        rules = [line.split() for line in path.read_text().splitlines()
                 if line.strip() and not line.startswith('#')]
        self.assertEqual(rules, [['blacklist', 'venus_core']])
        self.assertIn('modprobe venus_core', path.read_text())
        self.assertNotIn('install venus_core', path.read_text())
        self.assertNotIn('qcom_iris', path.read_text())

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'private'
        self.root.mkdir(mode=0o700)
        self.names = ['venus.mdt'] + [f'venus.b{i:02d}' for i in (*range(11), 19)]
        self.document = {'schema': 1, 'files': {}}
        for name in self.names:
            relative = 'firmware/qcom/dby-w09/' + name
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            data = ('synthetic fixture: ' + name).encode()
            path.write_bytes(data)
            path.chmod(0o644)
            self.document['files'][relative] = {
                'sha256': hashlib.sha256(data).hexdigest(), 'mode': 0o644}
        self.lock = self.root / 'inputs.json'
        self.lock.write_text(json.dumps(self.document))

    def test_schema_one_thirteen_files_merge(self):
        inventory = private_inventory(self.root)
        self.assertEqual(len(inventory[0]), 13)
        self.assertTrue(all(n.startswith('firmware/qcom/dby-w09/') for n in inventory[0]))
        tree = self.root.parent / 'device'
        copied = merge_private(tree, [inventory], touch=True)
        self.assertEqual(copied, set(self.document['files']))
        for name in copied:
            self.assertEqual((tree / name).read_bytes(), (self.root / name).read_bytes())

    def test_wrong_schema_rejected(self):
        self.document['schema'] = 2
        self.lock.write_text(json.dumps(self.document))
        with self.assertRaises(BundleError):
            private_inventory(self.root)

    def test_missing_segment_rejected(self):
        (self.root / 'firmware/qcom/dby-w09/venus.b19').unlink()
        with self.assertRaises(BundleError):
            private_inventory(self.root)

    def test_extra_segment_rejected(self):
        (self.root / 'firmware/qcom/dby-w09/venus.b20').write_bytes(b'synthetic extra')
        with self.assertRaises(BundleError):
            private_inventory(self.root)

    def test_changed_segment_rejected(self):
        (self.root / 'firmware/qcom/dby-w09/venus.b02').write_bytes(b'synthetic corruption')
        with self.assertRaises(BundleError):
            private_inventory(self.root)


if __name__ == '__main__':
    unittest.main()
