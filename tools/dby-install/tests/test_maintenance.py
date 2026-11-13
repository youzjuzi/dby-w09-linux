# SPDX-License-Identifier: GPL-2.0-only
"""P1 transactions on sparse regular files, plus injected target gates."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import io
import os
from pathlib import Path
import runpy
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from dbyinstall import core, image, maintenance as M
from dbyinstall.common import BundleError, MIN_SIZE, P1_SECTORS, P1_START, P2_START, file_record
from dbyinstall.maintenance_io import Backend, DirectoryStore, card_mounts, region_hash, replace_region
from dbyinstall.targets import ImageFileTarget, BlockDeviceTarget, live_probe
from dbybundle import create_manifest, write_manifest
from test_install import bundle, probe_row


def changed_bundle(root, sequence=2, payload=b"new package", rootfs=None):
    bundle(root)
    boot = root / "p1/UBOOT"
    for name in ("Image", "dby-w09.dtb", "initrd.img", "dby-device.img"):
        (boot / name).write_bytes(payload + name.encode())
    (boot / "MANIFEST").write_text("".join(file_record(boot / name)["sha256"] + f"  {name}\n"
                                         for name in ("Image", "dby-w09.dtb", "initrd.img")))
    (root / "manifest.json").unlink()
    write_manifest(root, create_manifest(root, sequence=sequence, created="2026-11-02T00:00:00Z", sources={}, rootfs=rootfs))
    return root


class MaintenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.shared = Path(cls.temporary.name)
        cls.old = bundle(cls.shared / "old", include_test=True)
        cls.new = changed_bundle(cls.shared / "new")
        cls.template = cls.shared / "template.img"
        target = ImageFileTarget(cls.template, MIN_SIZE)
        image.install(target, cls.old, minimal=True, p2_size=1024**3,
                      ask=lambda prompt: str(target.path), emit=lambda message: None)
        # Add private seed bytes and unrelated FAT directories to the template.
        part = cls.shared / "template-p1.img"; image.new_sparse(part, P1_SECTORS * 512)
        image.sparse_copy(cls.template, part, P1_START * 512, 0, P1_SECTORS * 512)
        seed = cls.shared / "seed.conf"; seed.write_bytes(b"synthetic private seed\n")
        note = cls.shared / "keep"; note.write_bytes(b"unrelated data\x00\xff")
        image.run(["mcopy", "-i", part, seed, "::/dby-seed/seed.conf"])
        for folder in ("Android", "LOST.DIR"):
            image.run(["mmd", "-i", part, "::/" + folder])
            image.run(["mcopy", "-i", part, note, "::/" + folder + "/keep"])
        replace_region(part, cls.template, P1_START * 512, P1_SECTORS * 512)
        cls.original_p1 = region_hash(cls.template, P1_START * 512, P1_SECTORS * 512)
        cls.original_disk = region_hash(cls.template, 0, MIN_SIZE)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        disk = self.root / "card.img"; image.new_sparse(disk, MIN_SIZE)
        image.sparse_copy(self.template, disk, 0, 0, MIN_SIZE)
        self.target = ImageFileTarget(disk)
        self.backups = self.root / "backups"
        self.messages = []

    def operate(self, action="update", **kwargs):
        options = {"bundle": self.new, "backup_dir": self.backups} if action == "update" else {}
        options.update(ask=lambda prompt: str(self.target.path), emit=self.messages.append)
        options.update(kwargs)
        return M.operate(action, self.target, **options)

    def assert_original(self):
        self.assertEqual(region_hash(self.target.path, P1_START * 512, P1_SECTORS * 512), self.original_p1)

    def mutate(self, function):
        with Backend(self.target) as backend:
            with backend.locked(), backend.transaction() as store:
                function(store)

    def test_update_roundtrip_backup_seed_p2_and_unrelated_files(self):
        before_p2 = region_hash(self.target.path, P2_START * 512, 1024**3)
        preview = self.operate()
        backup = Path(preview["backup"])
        old, record = M.backup_validate(backup)
        self.assertEqual(old["bundle_id"], "20261102-1")
        self.assertEqual(record["new_bundle_id"], "20261102-2")
        self.assertEqual(record["size_bytes"], MIN_SIZE)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
        self.assertNotIn(str(self.root), (backup / "backup.json").read_text())
        self.assertEqual(region_hash(self.target.path, P2_START * 512, 1024**3), before_p2)
        with Backend(self.target) as backend, backend.snapshot() as (p1, _):
            self.assertEqual(M.managed(p1)["bundle_id"], "20261102-2")
            self.assertEqual((p1 / "dby-seed/seed.conf").read_bytes(), b"synthetic private seed\n")
            self.assertFalse((p1 / "UBOOT.new").exists()); self.assertFalse((p1 / "UBOOT.old").exists())
        # Inspect only these test fixture paths; production never traverses them.
        out = self.root / "other"; out.mkdir()
        for folder in ("Android", "LOST.DIR"):
            image.run(["mcopy", "-i", str(self.target.path) + "@@" + str(P1_START * 512),
                       "::/" + folder + "/keep", out / folder])
            self.assertEqual((out / folder).read_bytes(), b"unrelated data\x00\xff")
        self.assertTrue(any("dby-install rollback --image-file" in line for line in self.messages))

    def test_readonly_preflight_closes_before_confirmation_lock_and_write(self):
        p1 = self.root / "mounted-p1"; p1.mkdir()
        core.populate_p1(p1, self.old)
        owner = self

        class MountLifecycleBackend:
            is_image = True
            size = MIN_SIZE
            identity = {}
            forbidden_roots = ()
            busy = False

            def __init__(self, target, **options):
                self.target = target

            def __enter__(self):
                return self

            def __exit__(self, *error):
                owner.assertFalse(self.busy)

            @contextmanager
            def snapshot(self):
                owner.assertFalse(self.busy)
                self.busy = True
                try:
                    yield p1, {"contract": "unchanged"}
                finally:
                    self.busy = False

            def confirm(self, ask):
                owner.assertFalse(self.busy, "Read-only mounts must close before confirmation.")
                owner.assertEqual(ask("Target: "), str(self.target.path))

            @contextmanager
            def locked(self):
                owner.assertFalse(self.busy, "Target revalidation must see an unmounted card.")
                yield

            @contextmanager
            def transaction(self):
                owner.assertFalse(self.busy, "Writable p1 must not overlap read-only mounts.")
                yield DirectoryStore(p1)

        self.operate(backend_factory=MountLifecycleBackend)
        self.assertEqual(M.managed(p1)["bundle_id"], "20261102-2")

    def test_same_bundle_does_not_write_or_backup_or_confirm(self):
        result = self.operate(bundle=self.old, ask=lambda prompt: self.fail("No-op must not ask to write."))
        self.assertEqual(result["version_decision"], "already up to date")
        self.assertFalse(self.backups.exists()); self.assert_original()
        self.assertIn("already up to date", self.messages)
        self.assertEqual(region_hash(self.target.path, 0, MIN_SIZE), self.original_disk)

    def test_same_id_different_contents_refused(self):
        different = changed_bundle(self.root / "different", sequence=1)
        with self.assertRaisesRegex(BundleError, "E_UPDATE_SAME_ID_DIFFERENT"):
            self.operate(bundle=different)
        self.assertFalse(self.backups.exists()); self.assert_original()

    def test_older_refused_and_explicitly_allowed(self):
        first = self.operate(); backup = first["backup"]
        with self.assertRaisesRegex(BundleError, "E_UPDATE_OLDER"):
            self.operate(bundle=self.old)
        result = self.operate(bundle=self.old, allow_older=True)
        self.assertEqual(result["version_decision"], "older explicitly allowed")
        self.assertTrue(Path(backup).is_dir())

    def test_missing_manifest_and_inconsistent_card_refused(self):
        self.mutate(lambda store: store.remove("manifest.json"))
        before = region_hash(self.target.path, P1_START * 512, P1_SECTORS * 512)
        with self.assertRaisesRegex(BundleError, "E_UPDATE_UNMANAGED"):
            self.operate()
        self.assertEqual(region_hash(self.target.path, P1_START * 512, P1_SECTORS * 512), before)

    def test_inconsistent_file_refused(self):
        changed = self.root / "bad"; changed.write_bytes(b"bad")
        def alter(store):
            store.command("mdel", "::/UBOOT/Image"); store.copy(changed, "UBOOT/Image")
        self.mutate(alter)
        before = region_hash(self.target.path, P1_START * 512, P1_SECTORS * 512)
        with self.assertRaisesRegex(BundleError, "E_UPDATE_CARD_INCONSISTENT"):
            self.operate()
        self.assertEqual(region_hash(self.target.path, P1_START * 512, P1_SECTORS * 512), before)

    def test_leftover_new_refused(self):
        self.mutate(lambda store: store.mkdir("UBOOT.new"))
        with self.assertRaisesRegex(BundleError, "E_UPDATE_LEFTOVER"):
            self.operate()
        self.assertFalse(self.backups.exists())

    def test_leftover_old_without_uboot_gives_recovery_instructions(self):
        self.mutate(lambda store: store.rename("UBOOT", "UBOOT.old"))
        with self.assertRaisesRegex(BundleError, "renaming it to UBOOT.*rollback"):
            self.operate()

    def test_backup_failure_cannot_modify_target(self):
        with patch.object(M, "backup_validate", side_effect=BundleError("E_ROLLBACK_BACKUP_INVALID", "fixture")):
            with self.assertRaisesRegex(BundleError, "E_UPDATE_BACKUP"):
                self.operate()
        self.assert_original()

    def test_verification_failure_rolls_back_exact_image_bytes(self):
        def fail(phase, context):
            if phase == "before_readback":
                raise BundleError("E_FIXTURE_VERIFY", "injected verification failure")
        with self.assertRaisesRegex(BundleError, "E_FIXTURE_VERIFY.*automatic rollback verified.*Backup:.*dby-install rollback"):
            self.operate(hook=fail)
        self.assert_original()
        self.assertEqual(len(list(self.backups.iterdir())), 1)
        self.assertEqual(region_hash(self.target.path, 0, MIN_SIZE), self.original_disk)

    def test_actual_readback_hash_corruption_restores_exact_image(self):
        bad = self.root / "corrupt"; bad.write_bytes(b"corrupt readback")
        def corrupt(phase, backend):
            if phase == "before_readback":
                with backend.transaction() as store:
                    store.command("mdel", "::/UBOOT/Image")
                    store.copy(bad, "UBOOT/Image")
        with self.assertRaisesRegex(BundleError, "E_INSTALL_HASH.*automatic rollback verified"):
            self.operate(hook=corrupt)
        self.assertEqual(region_hash(self.target.path, 0, MIN_SIZE), self.original_disk)

    def test_automatic_recovery_failure_preserves_original_error_and_command(self):
        def fail(phase, context):
            if phase == "before_readback":
                raise BundleError("E_FIRST_FAILURE", "fixture")
        original = M.switch
        calls = []
        def recover_fails(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise BundleError("E_SECOND_FAILURE", "recovery fixture")
            return original(*args, **kwargs)
        with patch.object(M, "switch", side_effect=recover_fails):
            with self.assertRaisesRegex(BundleError, "E_FIRST_FAILURE.*automatic rollback failed.*E_SECOND_FAILURE.*Backup:.*rollback"):
                self.operate(hook=fail)

    def test_failure_after_first_switch_rolls_back(self):
        def fail(phase, context):
            if phase == "after_switch":
                raise OSError("private fixture path must not be exposed")
        with self.assertRaisesRegex(BundleError, "Original failure: OSError.*automatic rollback verified") as caught:
            self.operate(hook=fail)
        self.assertNotIn("private fixture", str(caught.exception)); self.assert_original()

    def test_replaced_image_name_cannot_redirect_recovery_writes(self):
        victim = self.root / "unrelated"; victim.write_bytes(b"keep this unrelated file")
        moved = self.root / "moved.img"
        def replace_name(phase, context):
            if phase == "before_readback":
                self.target.path.rename(moved)
                self.target.path.symlink_to(victim)
        with self.assertRaisesRegex(BundleError, "E_INSTALL_LINK.*automatic rollback failed.*Backup:"):
            self.operate(hook=replace_name)
        self.assertEqual(victim.read_bytes(), b"keep this unrelated file")
        self.assertTrue(moved.is_file())

    def test_rollback_restores_backup_and_interrupted_old_state(self):
        result = self.operate(); backup = result["backup"]
        self.mutate(lambda store: store.rename("UBOOT", "UBOOT.old"))
        self.operate("rollback", backup=backup)
        with Backend(self.target) as backend, backend.snapshot() as (root, _):
            self.assertEqual(M.managed(root)["bundle_id"], "20261102-1")
            self.assertFalse((root / "UBOOT.old").exists())
            self.assertEqual((root / "dby-seed/seed.conf").read_bytes(), b"synthetic private seed\n")

    def test_normal_rollback_cleans_both_reserved_leftovers(self):
        backup = self.operate()["backup"]
        def leftovers(store):
            store.mkdir("UBOOT.new"); store.mkdir("UBOOT.old")
        self.mutate(leftovers)
        self.operate("rollback", backup=backup)
        with Backend(self.target) as backend, backend.snapshot() as (root, _):
            self.assertEqual(M.managed(root)["bundle_id"], "20261102-1")
            self.assertFalse((root / "UBOOT.new").exists()); self.assertFalse((root / "UBOOT.old").exists())
        self.assertEqual(len(list(self.backups.iterdir())), 1)

    def test_rollback_dry_run_does_not_write_or_make_second_backup(self):
        backup = self.operate()["backup"]
        before = region_hash(self.target.path, 0, MIN_SIZE)
        preview = self.operate("rollback", backup=backup, dry_run=True)
        self.assertIn("clear reserved staging", preview["version_decision"])
        self.assertEqual(region_hash(self.target.path, 0, MIN_SIZE), before)
        self.assertEqual(len(list(self.backups.iterdir())), 1)

    def test_rollback_requires_boot_or_old_directory(self):
        backup = self.operate()["backup"]
        self.mutate(lambda store: store.remove("UBOOT"))
        with self.assertRaisesRegex(BundleError, "Neither UBOOT nor UBOOT.old"):
            self.operate("rollback", backup=backup)

    def test_tampered_extra_and_linked_backups_rejected(self):
        result = self.operate(); original = Path(result["backup"])
        for kind in ("byte", "extra", "link"):
            with self.subTest(kind=kind):
                backup = self.root / kind; shutil.copytree(original, backup)
                if kind == "byte":
                    (backup / "UBOOT/Image").write_bytes(b"bad")
                elif kind == "extra":
                    (backup / "extra").write_bytes(b"extra")
                else:
                    (backup / "UBOOT/Image").unlink(); (backup / "UBOOT/Image").symlink_to(original / "UBOOT/Image")
                with self.assertRaisesRegex(BundleError, "E_ROLLBACK_BACKUP_INVALID"):
                    self.operate("rollback", backup=backup)

    def test_dry_run_has_stable_preview_and_no_write(self):
        result = self.operate(dry_run=True)
        self.assertEqual(result["action"], "update"); self.assertTrue(result["p2_unchanged"])
        self.assertTrue(result["seed_unchanged"]); self.assertEqual(len(result["files"]), 5)
        self.assertFalse(self.backups.exists()); self.assert_original()

    def test_confirmation_is_mandatory(self):
        with self.assertRaisesRegex(BundleError, "E_TARGET_CONFIRM"):
            self.operate(ask=lambda prompt: "yes")
        self.assertFalse(self.backups.exists()); self.assert_original()

    def test_backup_path_collision_unwritable_and_inside_target(self):
        stamp = datetime(2026, 11, 2, tzinfo=timezone.utc)
        self.backups.mkdir()
        (self.backups / "p1-20261102T000000000000Z-20261102-1").mkdir()
        with self.assertRaisesRegex(BundleError, "E_UPDATE_BACKUP"):
            self.operate(now=stamp)
        self.backups.chmod(0o500)
        try:
            with self.assertRaisesRegex(BundleError, "E_UPDATE_BACKUP"):
                self.operate()
        finally:
            self.backups.chmod(0o700)
        with self.assertRaisesRegex(BundleError, "E_UPDATE_BACKUP"):
            self.operate(backup_dir=self.target.path / "backups")
        with self.assertRaisesRegex(BundleError, "E_UPDATE_BACKUP"):
            self.operate(backup_dir=self.new / "backups")
        self.assert_original()

    def test_backup_cannot_be_inside_a_target_mount_root(self):
        with Backend(self.target) as backend:
            backend.forbidden_roots = [self.root]
            with self.assertRaisesRegex(BundleError, "E_UPDATE_BACKUP"):
                M.backup_location(self.backups, backend, "20261102-1", datetime.now(timezone.utc))
        self.assert_original()

    def test_rootfs_change_only_warns(self):
        rootfs = {"recipe_sha256": "a" * 64, "packages_sha256": "b" * 64, "package_count": 1}
        new = changed_bundle(self.root / "rootfs-changed", rootfs=rootfs)
        preview = self.operate(bundle=new, dry_run=True)
        self.assertIn("p2 is unchanged", preview["notices"][0]); self.assert_original()

    def test_cli_required_arguments_and_invalid_install_options(self):
        prefix = [sys.executable, "-I", "-B", str(TOOL / "dby-install")]
        cases = [["update"], ["update", "--bundle", str(self.new)], ["rollback"],
                 ["update", "--bundle", str(self.new), "--backup-dir", str(self.backups), "--minimal"],
                 ["update", "--bundle", str(self.new), "--backup-dir", str(self.backups), "--p2-size", "1G"],
                 ["update", "--bundle", str(self.new), "--backup-dir", str(self.backups), "--no-readback"]]
        for args in cases:
            with self.subTest(args=args):
                result = subprocess.run(prefix + args + ["--image-file", str(self.target.path)], capture_output=True)
                self.assertEqual(result.returncode, 2)
        self.assert_original()

    def test_block_target_gate_precedes_any_backend_io(self):
        for row, code in [(probe_row(media="MMC"), "E_TARGET_MEDIA"),
                          (probe_row(mounted=["179:0"]), "E_TARGET_MOUNTED")]:
            target = BlockDeviceTarget(Path("/dev/mmcblk8"), lambda path: row)
            with self.assertRaisesRegex(BundleError, code):
                M.operate("update", target, bundle=self.new, backup_dir=self.backups, dry_run=True)

    def test_cli_device_gates_with_fake_sysfs_and_system_holder(self):
        sysroot = self.root / "sys"; node = sysroot / "class/block/mmcblk8"
        for name, content in (("dev", "179:0"), ("size", str(MIN_SIZE // 512)), ("removable", "0"),
                              ("device/type", "MMC"), ("holders/dm-fixture/dev", "253:0")):
            p = node / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(content)
        mountinfo = self.root / "mountinfo"; mountinfo.write_text("")
        fake = SimpleNamespace(st_mode=stat.S_IFBLK | 0o600, st_rdev=os.makedev(179, 0))
        original_lstat = Path.lstat
        def lstat(path):
            return fake if str(path) == "/dev/mmcblk8" else original_lstat(path)
        def probe(path):
            return live_probe(path, sysroot=sysroot, mountinfo=mountinfo,
                              execute=lambda argv: json.dumps({"blockdevices": [dict(probe_row(), path=str(path))]}).encode())
        arguments = [str(TOOL / "dby-install"), "update", "--device", "/dev/mmcblk8", "--bundle", str(self.new),
                     "--backup-dir", str(self.backups), "--dry-run"]
        for media, mounted, code in (("MMC", "", "E_TARGET_MEDIA"),
                                      ("SD", "1 0 253:0 / / rw - ext4 fixture rw\n", "E_TARGET_MOUNTED")):
            (node / "device/type").write_text(media); mountinfo.write_text(mounted)
            output = io.StringIO()
            with patch("dbyinstall.targets.no_links"), patch.object(Path, "lstat", lstat), \
                    patch("dbyinstall.targets.BlockDeviceTarget", side_effect=lambda path: BlockDeviceTarget(path, probe)), \
                    patch.object(sys, "argv", arguments), patch("sys.stderr", output):
                with self.assertRaises(SystemExit) as caught:
                    runpy.run_path(str(TOOL / "dby-install"), run_name="__main__")
            self.assertEqual(caught.exception.code, 1)
            self.assertIn(code, output.getvalue())

    def test_versions_use_numeric_sequences_and_reject_invalid_dates(self):
        self.assertGreater(M.version("20261102-10"), M.version("20261102-9"))
        for value in ("20261340-1", "20261102-0", "bad", "20261102-01"):
            with self.assertRaisesRegex(BundleError, "E_UPDATE_VERSION"):
                M.version(value)

    def test_device_mount_adapter_never_mounts_p2_writable(self):
        calls, mounted = [], set()
        def execute(argv):
            calls.append(list(map(str, argv)))
            if argv[0] == "mount":
                mounted.add(Path(argv[-1]))
            elif argv[0] == "umount":
                mounted.remove(Path(argv[-1]))
            return b""
        for writable in (False, True):
            work = self.root / str(writable); work.mkdir()
            with patch("dbyinstall.maintenance_io.run", side_effect=execute), \
                    patch("dbyinstall.privileged.mounted_paths", side_effect=lambda: set(mounted)):
                with card_mounts(Path("/dev/mmcblk8"), work, writable=writable):
                    self.assertEqual(len(mounted), 2)
            self.assertFalse(mounted)
        p2_mounts = [row for row in calls if row[0] == "mount" and row[-2] == "/dev/mmcblk8p2"]
        self.assertEqual(len(p2_mounts), 2)
        self.assertTrue(all(row[4].startswith("ro,noload,") for row in p2_mounts))

    def test_device_execution_requires_root_but_preview_does_not(self):
        target = BlockDeviceTarget(Path("/dev/mmcblk8"), lambda path: probe_row())
        with patch("dbyinstall.privileged.require_root", side_effect=BundleError("E_INSTALL_ROOT", "fixture")):
            with Backend(target, dry_run=True):
                pass
            with self.assertRaisesRegex(BundleError, "E_INSTALL_ROOT"):
                with Backend(target):
                    self.fail("Writable backend cannot open without root.")


if __name__ == "__main__":
    unittest.main()
