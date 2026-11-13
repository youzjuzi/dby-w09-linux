# SPDX-License-Identifier: GPL-2.0-only
"""Rootfs member syntax is independent of strict manifest file paths."""

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from dbyinstall import core, image
from dbyinstall.archive import archive_name, inspect_archive, verify_root
from dbyinstall.common import BundleError, FSTAB, MIN_SIZE, relative_path
from dbyinstall.targets import BlockDeviceTarget, ImageFileTarget
from dbybundle import create_manifest, write_manifest
from test_install import bundle, probe_row

NAMES = ("var/lib/dpkg/info/libx:arm64.list", r"usr/lib/systemd/system/system-systemd\x2dmute\x2dconsole.slice",
         "usr/share/space name", "usr/share/at@name", "usr/share/single'quote", "usr/share/semi;colon",
         "usr/share/$(literal)", "usr/share/<42>")


def archive(path, names=NAMES):
    payloads = {"etc/fstab": FSTAB, "etc/dby-rootfs-kind": b"full\n"}
    payloads.update({name: ("fixture " + str(index)).encode() for index, name in enumerate(names)})
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as content:
        for name, data in payloads.items():
            member = tarfile.TarInfo("./" + name); member.mode = 0o644; member.size = len(data)
            member.uid, member.gid = os.getuid(), os.getgid()
            content.addfile(member, io.BytesIO(data))
    path.write_bytes(subprocess.run(["zstd", "-q", "-c"], input=buffer.getvalue(), capture_output=True, check=True).stdout)


class ArchiveMemberTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bundle = bundle(self.root / "bundle")

    def replace_archive(self, names=NAMES):
        archive(self.bundle / "rootfs.tar.zst", names)
        (self.bundle / "manifest.json").unlink()
        write_manifest(self.bundle, create_manifest(self.bundle, sequence=1, created="2026-11-02T00:00:00Z", sources={}))

    def test_archive_and_manifest_have_distinct_name_rules(self):
        for name in (*NAMES, 'usr/share/double"quote'):
            with self.subTest(name=name):
                self.assertEqual(archive_name("./" + name), name)
        for name in ("../x", "/absolute", "a//b", "a/./b", "a/../b", "a\ncommand", "a\0b", "a\x01b", "a\x7fb", ""):
            with self.subTest(invalid=name), self.assertRaisesRegex(BundleError, "E_ROOTFS_PATH"):
                archive_name(name)
        for name in ("var/lib/dpkg/info/libx:arm64.list", r"usr/lib/systemd/system/name\x2d.slice"):
            with self.subTest(manifest=name), self.assertRaisesRegex(BundleError, "E_BUNDLE_PATH"):
                relative_path(name)

    def test_real_sd_plan_and_gnu_tar_readback_accept_literal_names(self):
        self.replace_archive((*NAMES, 'usr/share/double"quote'))
        target = BlockDeviceTarget(Path("/dev/mmcblk8"), lambda path: probe_row())
        preview, _, _, entries = core.plan(target, self.bundle)
        self.assertEqual(preview["rootfs_archive"], "rootfs.tar.zst")
        root = self.root / "extracted"; root.mkdir()
        subprocess.run(["tar", "--zstd", "--extract", "--file", str(self.bundle / "rootfs.tar.zst"),
                        "--directory", str(root), "--numeric-owner", "--no-same-owner", "--same-permissions",
                        "--xattrs", "--xattrs-include=*", "--acls"], capture_output=True, check=True)
        verify_root(root, entries)
        for name in (*NAMES, 'usr/share/double"quote'):
            self.assertTrue((root / name).is_file())

    def test_real_image_readback_handles_backslash_space_colon_and_at(self):
        self.replace_archive()
        target = ImageFileTarget(self.root / "literal.img", MIN_SIZE)
        image.install(target, self.bundle, ask=lambda prompt: str(target.path))
        image.verify(target, self.bundle)
        with image.opened(target) as (_, root, filesystem):
            for name in NAMES:
                self.assertTrue((root / name).is_file(), name)
                self.assertEqual(image.inode(filesystem, name)[2:], (os.getuid(), os.getgid()))
        self.assertEqual(image.info(target)["rootfs_kind"], "full")

    def test_image_rejects_double_quotes_before_confirmation_or_writes(self):
        self.replace_archive(('usr/share/double"quote',))
        target = ImageFileTarget(self.root / "refused.img", MIN_SIZE)
        with self.assertRaisesRegex(BundleError, "E_IMAGE_NAME"):
            image.install(target, self.bundle, ask=lambda prompt: self.fail("Unsafe name must fail before confirmation."))
        self.assertFalse(target.path.exists())

    def test_unsafe_member_names_and_duplicates_remain_rejected(self):
        for name in ("../x", "/absolute", "a//b", "a/./b", "a/../b", "a\ncommand", "a\x01b", "a\x7fb"):
            path = self.root / "bad.zst"
            archive(path, (name,))
            with self.subTest(name=name), self.assertRaisesRegex(BundleError, "E_ROOTFS_PATH"):
                inspect_archive(path)
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as content:
            for name in ("same", "./same"):
                member = tarfile.TarInfo(name); content.addfile(member)
        path.write_bytes(subprocess.run(["zstd", "-q", "-c"], input=buffer.getvalue(), capture_output=True, check=True).stdout)
        with self.assertRaisesRegex(BundleError, "E_ROOTFS_PATH"):
            inspect_archive(path)

    def test_debugfs_argument_refuses_control_and_quote_injection(self):
        for name in ('/"\nset_inode_field /etc/fstab uid 1234', '/double"quote', '/path\x7f'):
            with self.subTest(name=name), self.assertRaisesRegex(BundleError, "E_IMAGE_NAME"):
                image.quote(name)

    def test_special_files_and_escaping_links_remain_rejected(self):
        for kind, target, code in ((tarfile.FIFOTYPE, "", "E_ROOTFS_TYPE"),
                                   (tarfile.CHRTYPE, "", "E_ROOTFS_TYPE"),
                                   (tarfile.SYMTYPE, "../../escape", "E_ROOTFS_LINK"),
                                   (tarfile.LNKTYPE, "../../escape", "E_ROOTFS_LINK")):
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w") as content:
                member = tarfile.TarInfo("unsafe"); member.type = kind; member.linkname = target
                content.addfile(member)
            path = self.root / "unsafe.zst"
            path.write_bytes(subprocess.run(["zstd", "-q", "-c"], input=buffer.getvalue(), capture_output=True, check=True).stdout)
            with self.subTest(kind=kind), self.assertRaisesRegex(BundleError, code):
                inspect_archive(path)

    def test_pax_path_with_embedded_nul_is_rejected(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as content:
            member = tarfile.TarInfo("placeholder"); member.pax_headers = {"path": "bad\0name"}
            content.addfile(member)
        path = self.root / "nul.zst"
        path.write_bytes(subprocess.run(["zstd", "-q", "-c"], input=buffer.getvalue(), capture_output=True, check=True).stdout)
        with self.assertRaisesRegex(BundleError, "E_ROOTFS_PATH"):
            inspect_archive(path)


if __name__ == "__main__":
    unittest.main()
