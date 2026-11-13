# SPDX-License-Identifier: GPL-2.0-only
"""Synthetic bundles, injected block probes and real unprivileged filesystems."""

import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from dbyinstall import core, image, privileged
from dbyinstall.archive import inspect_archive
from dbyinstall.common import (BundleError, DEFAULT_P2_SIZE, FSTAB, MIN_P2_SIZE, MIN_SIZE, P2_START,
                               file_record, partition_script, root_sectors, verify_root_size)
from dbyinstall.targets import BlockDeviceTarget, ImageFileTarget, live_probe
from dbybundle import create_manifest, write_manifest


def archive(path, *, malicious=False, wrong_fstab=False, pax=None, kind="full"):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as tar:
        for name in ("etc", "usr", "usr/bin"):
            entry = tarfile.TarInfo(name); entry.type = tarfile.DIRTYPE; entry.mode = 0o755
            tar.addfile(entry)
        for name, payload in (("etc/fstab", b"wrong\n" if wrong_fstab else FSTAB), ("etc/dby-rootfs-kind", (kind + "\n").encode()),
                              ("../escape" if malicious else "usr/bin/fixture", b"synthetic payload\n")):
            entry = tarfile.TarInfo(name); entry.size = len(payload); entry.mode = 0o755 if name.endswith("fixture") else 0o644
            if name.endswith("fixture"):
                entry.uid, entry.gid = 1234, 45
                entry.pax_headers = pax or {}
            tar.addfile(entry, io.BytesIO(payload))
        link = tarfile.TarInfo("bin"); link.type = tarfile.SYMTYPE; link.linkname = "/usr/bin"; link.mode = 0o777
        tar.addfile(link)
        if not malicious:
            link = tarfile.TarInfo("usr/bin/shared"); link.type = tarfile.LNKTYPE; link.linkname = "usr/bin/fixture"
            link.uid, link.gid, link.mode = 1234, 45, 0o755; tar.addfile(link)
    result = subprocess.run(["zstd", "-q", "-c"], input=stream.getvalue(), capture_output=True, check=True)
    path.write_bytes(result.stdout)


def bundle(root, *, include_test=False):
    boot = root / "p1/UBOOT"; boot.mkdir(parents=True)
    for name in ("Image", "dby-w09.dtb", "initrd.img", "dby-device.img"):
        (boot / name).write_bytes(("synthetic " + name).encode())
    (boot / "MANIFEST").write_text("".join(file_record(boot / name)["sha256"] + f"  {name}\n"
                                         for name in ("Image", "dby-w09.dtb", "initrd.img")))
    archive(root / "rootfs.tar.zst")
    if include_test:
        archive(root / "rootfs-test.tar.zst", kind="test")
    write_manifest(root, create_manifest(root, sequence=1, created="2026-11-02T00:00:00Z", sources={}))
    return root


def probe_row(**changes):
    row = {"block": True, "type": "disk", "partition": False, "media": "SD", "rm": True, "ro": False,
           "size": MIN_SIZE, "sys_size": MIN_SIZE, "log-sec": 512, "sys_number": "179:0", "maj:min": "179:0",
           "rdev": 0, "mounted": [], "model": "fixture", "serial": "synthetic"}
    row.update(changes)
    return row


class InstallerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bundle = bundle(self.root / "bundle")

    def test_layout_bytes_and_labels(self):
        expected = ("label: dos\nunit: sectors\n\nstart=2048, size=2097152, type=c\n"
                    "start=2099200, size=14678016, type=83\n")
        self.assertEqual(partition_script(MIN_SIZE).encode(), expected.encode())
        target = ImageFileTarget(self.root / "disk.img", MIN_SIZE)
        plan = core.plan(target, self.bundle, image=True)[0]
        self.assertEqual(plan["p1"]["label"], "UBOOT")
        self.assertEqual(plan["p2"]["label"], "ubuntu-root")
        self.assertEqual((plan["p2"]["block_size"], plan["p2"]["inode_size"]), (4096, 256))
        self.assertFalse(target.path.exists())

    def test_block_rejections_and_confirmation(self):
        cases = [({"block": False}, "E_TARGET_TYPE"), ({"partition": True}, "E_TARGET_PARTITION"),
                 ({"media": "MMC", "rm": False, "sys_removable": False}, "E_TARGET_MEDIA"),
                 ({"media": "SDIO"}, "E_TARGET_MEDIA"), ({"media": ""}, "E_TARGET_MEDIA"),
                 ({"ro": True}, "E_TARGET_READONLY"), ({"mounted": ["179:1"]}, "E_TARGET_MOUNTED"),
                 ({"size": MIN_SIZE - 1}, "E_TARGET_SIZE"),
                 ({"size": 2 * 1024**4 + 512}, "E_TARGET_SIZE"), ({"log-sec": 4096}, "E_TARGET_GEOMETRY"),
                 ({"sys_size": MIN_SIZE + 512}, "E_TARGET_GEOMETRY"),
                 ({"sys_number": "179:8"}, "E_TARGET_IDENTITY")]
        for changes, code in cases:
            with self.subTest(code=code), self.assertRaisesRegex(BundleError, code):
                BlockDeviceTarget(Path("/dev/mmcblk8"), lambda p: probe_row(**changes)).validate()
        target = BlockDeviceTarget(Path("/dev/mmcblk8"), lambda p: probe_row())
        identity = target.validate()
        with self.assertRaisesRegex(BundleError, "E_TARGET_CONFIRM"):
            target.confirm(identity, lambda prompt: "yes")
        target.confirm(identity, lambda prompt: str(target.path))
        target.probe = lambda p: probe_row(model="changed")
        with self.assertRaisesRegex(BundleError, "E_TARGET_CHANGED"):
            target.confirm(identity, lambda prompt: str(target.path))

    def test_sd_without_removable_flags_is_accepted(self):
        target = BlockDeviceTarget(Path("/dev/mmcblk8"), lambda path: probe_row(rm=False, sys_removable=False))
        identity = target.validate()
        self.assertEqual(identity["media"], "SD")
        self.assertFalse(identity["rm"])
        self.assertFalse(identity["sys_removable"])
        target.confirm(identity, lambda prompt: str(target.path))

    def test_emmc_special_nodes_partitions_and_usb_are_rejected_before_probe(self):
        for name in ("/dev/mmcblk8p1", "/dev/mmcblk8boot0", "/dev/mmcblk8boot1", "/dev/mmcblk8rpmb", "/dev/sda", "/dev/sda1"):
            with self.subTest(name=name), self.assertRaisesRegex(BundleError, "E_TARGET_MEDIA"):
                BlockDeviceTarget(Path(name), lambda path: self.fail("Unsupported node must fail before probing.")).validate()

    def test_sysfs_holder_mount_is_detected(self):
        sysroot = self.root / "sys"
        node = sysroot / "class/block/mmcblk8"
        for name, text in (("dev", "179:0"), ("size", str(MIN_SIZE // 512)), ("removable", "0"),
                           ("device/type", "SD"), ("holders/dm-fixture/dev", "253:0")):
            path = node / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text)
        mounts = self.root / "mountinfo"; mounts.write_text("1 0 253:0 / / rw - ext4 fixture rw\n")
        info = SimpleNamespace(st_mode=stat.S_IFBLK | 0o600, st_rdev=os.makedev(179, 0))
        original = Path.lstat
        def fake(path):
            return info if str(path) == "/dev/mmcblk8" else original(path)
        with patch("dbyinstall.targets.no_links"), patch.object(Path, "lstat", fake):
            row = live_probe(Path("/dev/mmcblk8"), sysroot=sysroot, mountinfo=mounts,
                             execute=lambda argv: json.dumps({"blockdevices": [dict(probe_row(rm=False), path="/dev/mmcblk8")]}).encode())
        self.assertEqual(row["mounted"], ["253:0"])
        with self.assertRaisesRegex(BundleError, "E_TARGET_MOUNTED"):
            BlockDeviceTarget(Path("/dev/mmcblk8"), lambda path: row).validate()

    def test_image_must_be_explicit_and_fresh(self):
        existing = self.root / "existing"; existing.write_bytes(b"keep")
        with self.assertRaisesRegex(BundleError, "E_IMAGE_EXISTS"):
            ImageFileTarget(existing, MIN_SIZE).validate()
        alias = self.root / "alias"; alias.symlink_to(existing)
        with self.assertRaisesRegex(BundleError, "E_INSTALL_LINK"):
            ImageFileTarget(alias, MIN_SIZE).validate()
        with self.assertRaisesRegex(BundleError, "E_TARGET_MEDIA"):
            BlockDeviceTarget(existing, lambda p: probe_row()).validate()

    def test_archive_path_and_fstab_fail_before_target_creation(self):
        path = self.root / "bad.tar.zst"
        archive(path, malicious=True)
        with self.assertRaisesRegex(BundleError, "E_ROOTFS_PATH"):
            inspect_archive(path)
        archive(path, wrong_fstab=True)
        with self.assertRaisesRegex(BundleError, "E_ROOTFS_FSTAB"):
            inspect_archive(path)

    def test_archive_rejects_symlink_parent_and_escaping_link(self):
        for target, child in (("/../../escape", False), ("/tmp/fixture", True)):
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w") as tar:
                link = tarfile.TarInfo("escape"); link.type = tarfile.SYMTYPE; link.linkname = target; tar.addfile(link)
                if child:
                    entry = tarfile.TarInfo("escape/file"); entry.size = 1; tar.addfile(entry, io.BytesIO(b"x"))
            path = self.root / "links.zst"
            path.write_bytes(subprocess.run(["zstd", "-q", "-c"], input=stream.getvalue(), capture_output=True, check=True).stdout)
            with self.assertRaisesRegex(BundleError, "E_ROOTFS_LINK"):
                inspect_archive(path)

    def test_real_image_rootfs_tamper_is_detected(self):
        target = ImageFileTarget(self.root / "rootfs-tamper.img", MIN_SIZE)
        image.install(target, self.bundle, ask=lambda prompt: str(target.path))
        part = self.root / "p2.img"; size = MIN_SIZE - 2099200 * 512
        image.new_sparse(part, size)
        image.sparse_copy(target.path, part, 2099200 * 512, 0, size)
        changed = self.root / "changed"; changed.write_bytes(b"tampered rootfs")
        image.run(["debugfs", "-w", "-R", "rm /usr/bin/fixture", part])
        image.run(["debugfs", "-w", "-R", "write " + image.quote(changed) + " /usr/bin/fixture", part])
        image.sparse_copy(part, target.path, 0, 2099200 * 512, size)
        with self.assertRaisesRegex(BundleError, "E_INSTALL_HASH"):
            image.verify(target, self.bundle)

    def test_image_refuses_unsupported_extended_metadata(self):
        for key, value, code in (("SCHILY.xattr.user.fixture", "fixture", "E_IMAGE_XATTR"),
                                 ("SCHILY.acl.access", "user::rwx,group::r-x,other::r-x", "E_IMAGE_ACL")):
            with self.subTest(key=key):
                archive(self.bundle / "rootfs.tar.zst", pax={key: value})
                (self.bundle / "manifest.json").unlink()
                write_manifest(self.bundle, create_manifest(self.bundle, sequence=1, created="2026-11-02T00:00:00Z", sources={}))
                target = ImageFileTarget(self.root / "unsupported.img", MIN_SIZE)
                with self.assertRaisesRegex(BundleError, code):
                    image.install(target, self.bundle, ask=lambda prompt: str(target.path))
                self.assertFalse(target.path.exists())

    def test_bundle_tamper_and_missing_rootfs(self):
        path = self.bundle / "p1/UBOOT/Image"
        path.write_bytes(b"changed")
        with self.assertRaises(BundleError):
            core.prepare(self.bundle)
        path.write_bytes(b"synthetic Image")
        (self.bundle / "manifest.json").unlink()
        (self.bundle / "rootfs.tar.zst").unlink()
        write_manifest(self.bundle, create_manifest(self.bundle, sequence=1, created="2026-11-02T00:00:00Z", sources={}))
        with self.assertRaisesRegex(BundleError, "E_INSTALL_ROOTFS"):
            core.prepare(self.bundle)

    def test_installed_manifest_is_required(self):
        p1 = self.root / "installed-p1"; p1.mkdir()
        core.populate_p1(p1, self.bundle)
        document = core.prepare(self.bundle)[0]
        core.verify_p1(p1, document)
        (p1 / "manifest.json").unlink()
        with self.assertRaisesRegex(BundleError, "E_INSTALL_MANIFEST"):
            core.verify_p1(p1, document)

    def test_dry_run_cli_no_files_written(self):
        target = self.root / "preview.img"
        result = subprocess.run([sys.executable, "-I", "-B", str(TOOL / "dby-install"), "install",
                                 "--image-file", str(target), "--image-size", "8G", "--bundle", str(self.bundle), "--dry-run"],
                                capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["partition_script"], partition_script(MIN_SIZE))
        self.assertFalse(target.exists())

    def test_image_confirmation_cannot_be_bypassed(self):
        target = ImageFileTarget(self.root / "refused.img", MIN_SIZE)
        with self.assertRaisesRegex(BundleError, "E_TARGET_CONFIRM"):
            image.install(target, self.bundle, ask=lambda prompt: "yes")
        self.assertFalse(target.path.exists())

    def test_card_info_never_returns_seed_contents(self):
        p1, p2 = self.root / "p1", self.root / "p2"
        p1.mkdir(); (p2 / "etc").mkdir(parents=True)
        (p1 / "dby-seed").mkdir()
        (p1 / "dby-seed/seed.conf").write_text("secret-fixture-value")
        (p2 / "etc/dby-rootfs-kind").write_text("full\n")
        result = core.card_info(p1, p2)
        self.assertTrue(result["seed"]["config_present"])
        self.assertNotIn("manifest", result)
        self.assertNotIn("secret-fixture-value", json.dumps(result))
        complete = core.card_info(p1, p2, full=True)
        self.assertIn("manifest", complete)
        self.assertNotIn("secret-fixture-value", json.dumps(complete))

    def test_privilege_and_mount_cleanup_refuse_unsafe_removal(self):
        with patch("dbyinstall.privileged.os.geteuid", return_value=1000):
            with self.assertRaisesRegex(BundleError, "E_INSTALL_ROOT"):
                privileged.require_root()
        workspace = self.root / "mounted-work"; workspace.mkdir()
        with patch("dbyinstall.privileged.tempfile.mkdtemp", return_value=str(workspace)), \
                patch("dbyinstall.privileged.mounted_paths", return_value={workspace / "p2"}):
            with self.assertRaisesRegex(BundleError, "E_INSTALL_CLEANUP"):
                with privileged.workspace():
                    pass
        self.assertTrue(workspace.exists())

    def test_real_image_create_verify_info_and_tamper(self):
        target = ImageFileTarget(self.root / "card.img", MIN_SIZE)
        image.install(target, self.bundle, ask=lambda prompt: str(target.path))
        image.verify(target, self.bundle)
        self.assertEqual(image.info(target)["rootfs_kind"], "full")
        # Change one p1 file through a temporary FAT image, then write it back.
        with tempfile.TemporaryDirectory() as temporary:
            part = Path(temporary) / "p1.img"; image.new_sparse(part, 2097152 * 512)
            image.sparse_copy(target.path, part, 2048 * 512, 0, 2097152 * 512)
            changed = Path(temporary) / "Image"; changed.write_bytes(b"tampered")
            image.run(["mcopy", "-o", "-i", part, changed, "::/UBOOT/Image"])
            image.sparse_copy(part, target.path, 0, 2048 * 512, 2097152 * 512)
        with self.assertRaisesRegex(BundleError, "E_INSTALL_HASH"):
            image.verify(target, self.bundle)


class MinimalInstallerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bundle = bundle(self.root / "bundle", include_test=True)

    def refresh_manifest(self):
        (self.bundle / "manifest.json").unlink()
        write_manifest(self.bundle, create_manifest(self.bundle, sequence=1, created="2026-11-02T00:00:00Z", sources={}))

    def test_minimal_dry_run_layout_bytes_and_unused_capacity(self):
        target = self.root / "minimal.img"
        result = subprocess.run([sys.executable, "-I", "-B", str(TOOL / "dby-install"), "install", "--minimal",
                                 "--image-file", str(target), "--image-size", "8G", "--bundle", str(self.bundle), "--dry-run"], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        preview = json.loads(result.stdout)
        self.assertEqual(preview["partition_script"].encode(),
                         b"label: dos\nunit: sectors\n\nstart=2048, size=2097152, type=c\nstart=2099200, size=8388608, type=83\n")
        self.assertEqual(preview["rootfs_archive"], "rootfs-test.tar.zst")
        self.assertEqual(preview["rootfs_kind"], "test")
        self.assertEqual(preview["p2"]["size_bytes"], DEFAULT_P2_SIZE)
        self.assertEqual(preview["unallocated_bytes"], MIN_SIZE - P2_START * 512 - DEFAULT_P2_SIZE)
        self.assertIn("p2 4 GiB; remaining space unallocated", preview["summary"])
        self.assertFalse(target.exists())

    def test_p2_size_boundaries_and_full_mode_guard(self):
        available = MIN_SIZE - P2_START * 512
        for value in (MIN_P2_SIZE, DEFAULT_P2_SIZE, available):
            self.assertEqual(root_sectors(MIN_SIZE, minimal=True, p2_size=value), value // 512)
        for value in (MIN_P2_SIZE - 512, MIN_P2_SIZE + 1, available + 512, 0, True):
            with self.subTest(value=value), self.assertRaisesRegex(BundleError, "E_INSTALL_P2_SIZE"):
                root_sectors(MIN_SIZE, minimal=True, p2_size=value)
        with self.assertRaisesRegex(BundleError, "E_INSTALL_P2_MODE"):
            root_sectors(MIN_SIZE, p2_size=DEFAULT_P2_SIZE)
        with self.assertRaisesRegex(BundleError, "E_INSTALL_TABLE"):
            verify_root_size(MIN_SIZE, DEFAULT_P2_SIZE // 512, "full")
        verify_root_size(MIN_SIZE, DEFAULT_P2_SIZE // 512, "test")

    def test_missing_test_archive_and_marker_mismatch(self):
        (self.bundle / "rootfs-test.tar.zst").unlink(); self.refresh_manifest()
        target = ImageFileTarget(self.root / "missing.img", MIN_SIZE)
        with self.assertRaisesRegex(BundleError, "E_INSTALL_TEST_ROOTFS"):
            core.plan(target, self.bundle, image=True, minimal=True)
        archive(self.bundle / "rootfs-test.tar.zst", kind="full"); self.refresh_manifest()
        with self.assertRaisesRegex(BundleError, "E_INSTALL_KIND"):
            core.plan(target, self.bundle, image=True, minimal=True)
        self.assertFalse(target.path.exists())

    def test_optional_test_archive_must_be_in_manifest(self):
        path = self.bundle / "manifest.json"; document = json.loads(path.read_bytes())
        del document["files"]["rootfs-test.tar.zst"]
        path.write_text(json.dumps(document))
        with self.assertRaises(BundleError):
            core.prepare(self.bundle, minimal=True)

    def test_real_minimal_image_detects_kind_and_preserves_inode_owners(self):
        target = ImageFileTarget(self.root / "minimal.img", MIN_SIZE)
        preview = image.install(target, self.bundle, minimal=True, ask=lambda prompt: str(target.path))
        image.verify(target, self.bundle)
        self.assertEqual(image.info(target)["rootfs_kind"], "test")
        parts = image.partitions(target.path, MIN_SIZE)
        self.assertEqual(parts[1], (P2_START * 512, DEFAULT_P2_SIZE))
        with image.opened(target) as (p1, p2, filesystem):
            core.verify_p1(p1, core.prepare(self.bundle, minimal=True)[0])
            self.assertEqual((p2 / "etc/dby-rootfs-kind").read_bytes(), b"test\n")
            self.assertEqual(image.inode(filesystem, "usr/bin/fixture")[2:], (1234, 45))
        with target.path.open("rb") as stream:
            stream.seek(P2_START * 512 + DEFAULT_P2_SIZE)
            self.assertEqual(stream.read(4096), bytes(4096))
        self.assertGreater(preview["unallocated_bytes"], 0)

    def test_test_only_bundle_custom_size_cli_and_auto_verify(self):
        (self.bundle / "rootfs.tar.zst").unlink(); self.refresh_manifest()
        target = self.root / "custom.img"
        result = subprocess.run([sys.executable, "-I", "-B", str(TOOL / "dby-install"), "install", "--minimal",
                                 "--p2-size", "1G", "--image-file", str(target), "--image-size", "8G", "--bundle", str(self.bundle)],
                                input=(str(target) + "\n").encode(), capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(image.partitions(target, MIN_SIZE)[1][1], MIN_P2_SIZE)
        result = subprocess.run([sys.executable, "-I", "-B", str(TOOL / "dby-install"), "verify",
                                 "--image-file", str(target), "--bundle", str(self.bundle)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(image.info(ImageFileTarget(target))["rootfs_kind"], "test")

    def test_cli_rejects_p2_size_without_minimal_and_minimal_on_verify(self):
        for command, options in (("install", ["--p2-size", "1G"]), ("verify", ["--minimal"])):
            result = subprocess.run([sys.executable, "-I", "-B", str(TOOL / "dby-install"), command, *options,
                                     "--image-file", str(self.root / "refused.img"), "--bundle", str(self.bundle), "--dry-run"], capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertFalse((self.root / "refused.img").exists())


if __name__ == "__main__":
    unittest.main()
