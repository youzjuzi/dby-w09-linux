# SPDX-License-Identifier: GPL-2.0-only
"""Synthetic special-file cleanup and compression tests; no device nodes."""

import io
import os
from pathlib import Path
import random
import stat
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dbybundle import BundleError, rootfs, rootfs_privileged


class SpecialFileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.root = self.work / "root"
        (self.root / "etc").mkdir(parents=True)
        (self.root / "etc/passwd").write_text("root:x:0:0:fixture:/root:/bin/sh\n")

    def file_types(self, modes):
        original = Path.lstat

        def fake_lstat(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            return SimpleNamespace(st_mode=modes[path] | 0o600) if path in modes else result

        return patch.object(Path, "lstat", fake_lstat)

    def test_dev_cleanup_leaves_empty_directory_and_pack_has_no_special_members(self):
        dev = self.root / "dev"; (dev / "pts").mkdir(parents=True)
        os.mkfifo(dev / "fifo")
        (dev / "socket").touch()
        (dev / "regular").write_bytes(b"obsolete dev file")
        external = self.work / "external"; external.mkdir()
        (external / "sentinel").write_bytes(b"outside data")
        (dev / "link").symlink_to(external, target_is_directory=True)
        with self.file_types({dev / "socket": stat.S_IFSOCK}):
            with self.assertRaisesRegex(BundleError, "E_ROOTFS_SPECIAL"):
                rootfs_privileged.check_payload(self.root)
            rootfs_privileged.check_payload(self.root, allow_dev_special=True)
            with patch.object(rootfs_privileged.os, "chown"):
                rootfs_privileged.clean(self.root)
        self.assertTrue(dev.is_dir())
        self.assertEqual(list(dev.iterdir()), [])
        self.assertEqual((external / "sentinel").read_bytes(), b"outside data")
        rootfs_privileged.check_payload(self.root)
        output = self.work / "cleaned.zst"
        with patch.object(rootfs.subprocess, "run", wraps=subprocess.run) as commands:
            rootfs.pack_tree(self.root, output, 123)
        self.assertIn("-T0", commands.call_args.args[0])
        plain = subprocess.run(["zstd", "-q", "-d", "-c", output], capture_output=True, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(plain)) as archive:
            self.assertTrue(archive.getmember("./dev").isdir())
            self.assertFalse(any(member.name.startswith("./dev/") for member in archive))
            self.assertTrue(all(member.isfile() or member.isdir() or member.issym() or member.islnk()
                                for member in archive))

    def test_character_and_block_types_use_lstat_without_creating_devices(self):
        dev = self.root / "dev"; dev.mkdir()
        for kind in (stat.S_IFCHR, stat.S_IFBLK):
            node = dev / "fixture"; node.touch()
            with self.subTest(kind=kind), self.file_types({node: kind}):
                rootfs_privileged.check_payload(self.root, allow_dev_special=True)
                with self.assertRaisesRegex(BundleError, "E_ROOTFS_SPECIAL.*dev/fixture"):
                    rootfs_privileged.check_payload(self.root)
                with patch.object(rootfs_privileged.os, "chown"):
                    rootfs_privileged.clean(self.root)
            self.assertFalse(node.exists())
            outside = self.root / "outside-dev"; outside.touch()
            with self.file_types({outside: kind}):
                for early in (False, True):
                    with self.assertRaisesRegex(BundleError, "E_ROOTFS_SPECIAL.*outside-dev"):
                        rootfs_privileged.check_payload(self.root, allow_dev_special=early)
            outside.unlink()

    def test_special_files_outside_dev_fail_in_both_phases_with_safe_first_path(self):
        directory = self.root / "var/lib"; directory.mkdir(parents=True)
        (directory / "z-socket").touch()
        os.mkfifo(directory / "a\nfifo")
        for early in (False, True):
            with self.subTest(early=early), self.assertRaises(BundleError) as caught:
                rootfs_privileged.check_payload(self.root, allow_dev_special=early)
            message = str(caught.exception)
            self.assertIn("E_ROOTFS_SPECIAL", message)
            self.assertIn("var/lib/a\\nfifo", message)
            self.assertNotIn("\n", message)
            self.assertNotIn(str(self.work), message)
            self.assertNotIn("z-socket", message)
        (directory / "a\nfifo").unlink()
        with self.file_types({directory / "z-socket": stat.S_IFSOCK}), \
                self.assertRaisesRegex(BundleError, "E_ROOTFS_SPECIAL.*var/lib/z-socket"):
            rootfs_privileged.check_payload(self.root, allow_dev_special=True)

    def test_dev_exemption_does_not_match_similar_directory_names(self):
        directory = self.root / "device"; directory.mkdir()
        os.mkfifo(directory / "fifo")
        with self.assertRaisesRegex(BundleError, "E_ROOTFS_SPECIAL.*device/fifo"):
            rootfs_privileged.check_payload(self.root, allow_dev_special=True)

    def test_dev_directory_link_is_replaced_without_traversing_its_target(self):
        external = self.work / "external"; external.mkdir()
        os.mkfifo(external / "fifo")
        (self.root / "dev").symlink_to(external, target_is_directory=True)
        (self.root / "usr").mkdir()
        (self.root / "usr/link").symlink_to(external, target_is_directory=True)
        rootfs_privileged.check_payload(self.root)
        rootfs_privileged.check_payload(self.root, allow_dev_special=True)
        with patch.object(rootfs_privileged.os, "chown"):
            rootfs_privileged.clean(self.root)
        self.assertFalse((self.root / "dev").is_symlink())
        self.assertEqual(list((self.root / "dev").iterdir()), [])
        self.assertTrue(stat.S_ISFIFO((external / "fifo").lstat().st_mode))

    def test_zstd_thread_counts_produce_identical_synthetic_bytes(self):
        block = random.Random(20261102).randbytes(64 * 1024) + b"synthetic records\n" * 4096
        data = block * 16
        outputs = [subprocess.run(["zstd", "-q", flag, "-19", "-c"], input=data,
                                  capture_output=True, check=True).stdout for flag in ("-T1", "-T0")]
        self.assertEqual(outputs[0], outputs[1])
        restored = subprocess.run(["zstd", "-q", "-d", "-c"], input=outputs[1], capture_output=True, check=True).stdout
        self.assertEqual(restored, data)


if __name__ == "__main__":
    unittest.main()
