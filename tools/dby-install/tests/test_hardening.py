# SPDX-License-Identifier: GPL-2.0-only
"""Journal refusal, used-card checks and concise info on ordinary files only."""

from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from dbyinstall import core, image, maintenance, privileged
from dbyinstall.common import BOOT_FILES, BundleError, FSTAB, MIN_SIZE, P1_SECTORS, P1_START
from dbyinstall.maintenance_io import Backend, dump_contract, region_hash, replace_region
from dbyinstall.p2 import check_journal
from dbyinstall.targets import BlockDeviceTarget, ImageFileTarget
from dbybundle import verify as verify_bundle
import test_maintenance as fixtures
import test_readback as readback_fixtures
from test_install import probe_row


class HardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.MaintenanceTests.setUpClass.__func__(cls)

    def setUp(self):
        fixtures.MaintenanceTests.setUp(self)

    def operate(self, action="update", **kwargs):
        options = {"bundle": self.new, "backup_dir": self.backups} if action == "update" else {}
        options.update(ask=lambda prompt: str(self.target.path), emit=self.messages.append)
        options.update(kwargs)
        return maintenance.operate(action, self.target, **options)

    def cli(self, *arguments):
        return subprocess.run([sys.executable, "-I", "-B", str(TOOL / "dby-install"),
                               *map(str, arguments), "--image-file", str(self.target.path)], capture_output=True)

    def edit_p2(self, *commands):
        offset, size = image.partitions(self.target.path, MIN_SIZE)[1]
        part = self.root / "edited-p2.img"
        if part.exists():
            part.unlink()
        image.new_sparse(part, size)
        image.sparse_copy(self.target.path, part, offset, 0, size)
        for command in commands:
            # Deliberate corruption is confined to this regular test file.
            image.run(["debugfs", "-w", "-R", command, part])
        replace_region(part, self.target.path, offset, size)
        return part

    def edit_p1(self, name, payload):
        part = self.root / "edited-p1.img"
        if part.exists():
            part.unlink()
        image.new_sparse(part, P1_SECTORS * 512)
        image.sparse_copy(self.target.path, part, P1_START * 512, 0, P1_SECTORS * 512)
        source = self.root / "payload"; source.write_bytes(payload)
        image.run(["mcopy", "-o", "-i", part, source, "::/" + name])
        replace_region(part, self.target.path, P1_START * 512, P1_SECTORS * 512)

    def test_dirty_journal_refuses_all_readers_and_maintenance_without_writes(self):
        backup = self.operate()["backup"]
        self.edit_p2("feature needs_recovery")
        before = region_hash(self.target.path, 0, MIN_SIZE)
        commands = [("update", "--bundle", self.new, "--backup-dir", self.backups),
                    ("update", "--bundle", self.new, "--backup-dir", self.backups, "--dry-run"),
                    ("rollback", "--backup", backup), ("rollback", "--backup", backup, "--dry-run"),
                    ("verify", "--bundle", self.new), ("info",), ("info", "--full")]
        for arguments in commands:
            with self.subTest(arguments=arguments):
                result = self.cli(*arguments)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(b"E_INSTALL_P2_RECOVERY", result.stderr)
                self.assertIn(b"not shut down cleanly", result.stderr)
                self.assertIn(b"e2fsck -p", result.stderr)
                self.assertNotIn(str(self.root).encode(), result.stderr)
                self.assertNotIn(b"Type the full", result.stdout)
        self.assertEqual(region_hash(self.target.path, 0, MIN_SIZE), before)
        self.assertEqual(len(list(self.backups.iterdir())), 1)

    def test_clearing_synthetic_recovery_flag_restores_normal_commands(self):
        self.edit_p2("feature needs_recovery", "feature ^needs_recovery")
        image.verify(self.target, self.old)
        self.assertEqual(image.info(self.target)["rootfs_kind"], "test")
        self.assertEqual(self.operate(dry_run=True)["version_decision"], "newer")
        backup = self.operate()["backup"]
        self.operate("rollback", backup=backup)
        image.verify(self.target, self.old)

    def test_used_p2_fails_full_verify_with_hint_but_p1_only_passes(self):
        changed = self.root / "used-file"; changed.write_bytes(b"changed after boot")
        self.edit_p2("rm /usr/bin/fixture", "write " + image.quote(changed) + " /usr/bin/fixture")
        result = self.cli("verify", "--bundle", self.old)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(b"E_INSTALL_HASH", result.stderr)
        self.assertIn(b"p2 files change after boot/use; use --p1-only", result.stderr)
        self.assertNotIn(b"usr/bin/fixture", result.stderr)
        with patch.object(image, "check_journal", side_effect=AssertionError("p2 recovery check forbidden")), \
                patch.object(image, "sparse_copy", wraps=image.sparse_copy) as copies, \
                patch.object(image, "verify_root", side_effect=AssertionError("p2 hashes forbidden")):
            image.verify(self.target, self.old, p1_only=True)
        self.assertTrue(all(call.args[2] == P1_START * 512 for call in copies.call_args_list))
        result = self.cli("verify", "--bundle", self.old, "--p1-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.endswith(b"OK: p1 matches the bundle; p2 not checked.\n"))

    def test_p1_only_skips_journal_but_still_checks_p1_bytes_and_labels(self):
        self.edit_p2("feature needs_recovery")
        image.verify(self.target, self.old, p1_only=True)
        payload = bytearray((self.old / "p1/UBOOT/Image").read_bytes()); payload[0] ^= 1
        self.edit_p1("UBOOT/Image", payload)
        result = self.cli("verify", "--bundle", self.old, "--p1-only")
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"E_INSTALL_HASH", result.stderr)
        self.assertNotIn(b"p2 files change", result.stderr)
        self.edit_p1("UBOOT/Image", (self.old / "p1/UBOOT/Image").read_bytes())
        self.edit_p2("set_super_value volume_name wrong")
        with self.assertRaisesRegex(BundleError, "E_INSTALL_LABEL"):
            image.verify(self.target, self.old, p1_only=True)

    def test_new_options_reject_inapplicable_commands_before_target_access(self):
        for flag, commands in (("--p1-only", ("install", "update", "rollback", "info")),
                               ("--full", ("install", "update", "rollback", "verify"))):
            for command in commands:
                with self.subTest(flag=flag, command=command):
                    result = self.cli(command, flag)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn(flag.encode(), result.stderr)

    def test_info_summary_full_and_seed_privacy_through_cli(self):
        document = verify_bundle(self.old)
        document["sources"] = {
            "repositories": {key: value * 40 for key, value in (("linux", "a"), ("kernel", "b"), ("uboot", "c"))},
            "upstream": [{"name": "linux", "version": "7.2.10", "sha256": "d" * 64}],
            "inputs": {"build-module": "e" * 64},
            "private_inputs": [{"sha256": f"{index:064x}"} for index in range(128)]}
        document["rootfs"] = {"package_count": 185, "recipe_sha256": "f" * 64, "packages_sha256": "a" * 64}
        self.edit_p1("manifest.json", json.dumps(document).encode())
        result = self.cli("info"); complete = self.cli("info", "--full")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(complete.returncode, 0, complete.stderr)
        short, full = json.loads(result.stdout), json.loads(complete.stdout)
        self.assertEqual(set(short), {"bundle_id", "kernel_release", "repositories", "rootfs_kind", "rootfs", "files", "seed", "sources"})
        self.assertEqual(short["repositories"], document["sources"]["repositories"])
        self.assertEqual(short["sources"], {"upstream": [{"name": "linux", "version": "7.2.10"}]})
        self.assertEqual(short["rootfs"], {"package_count": 185, "recipe_sha256": "f" * 16, "packages_sha256": "a" * 16})
        self.assertEqual(set(short["files"]), set(BOOT_FILES))
        for name, row in short["files"].items():
            expected = document["files"]["p1/UBOOT/" + name]
            self.assertEqual(row, {"size": expected["size"], "sha256": expected["sha256"][:16]})
        self.assertEqual(full["manifest"], document)
        self.assertLess(len(result.stdout) * 5, len(complete.stdout))
        self.assertEqual(sum(b'"sha256"' in line and b'"size"' in line for line in result.stdout.splitlines()), 5)
        for forbidden in (b"manifest", b"build-module", b"private_inputs", b"inputs", b"synthetic private seed"):
            self.assertNotIn(forbidden, result.stdout)
        self.assertNotIn(b"synthetic private seed", complete.stdout)
        self.assertTrue(short["seed"]["config_present"])

    def test_rollback_success_and_null_old_id_do_not_imply_interruption(self):
        backup = self.operate()["backup"]
        self.messages.clear()
        preview = self.operate("rollback", backup=backup)
        self.assertIsNone(preview["old_bundle_id"])
        self.assertTrue(any(line.startswith("OK: rollback -> 20261102-1;") for line in self.messages))
        self.assertNotIn("interrupted", "\n".join(self.messages))

    def test_contract_errors_name_the_item_and_reason_without_contents(self):
        for name, code, good in (("etc/dby-rootfs-kind", "E_INSTALL_KIND", b"test\n"),
                                  ("etc/fstab", "E_INSTALL_FSTAB", FSTAB)):
            for kind, reason in (("missing", "is missing"), ("content", "content does not match"),
                                 ("directory", "wrong type"), ("symlink", "wrong type")):
                root = self.root / (Path(name).name + "-" + kind); (root / "etc").mkdir(parents=True)
                (root / "etc/dby-rootfs-kind").write_bytes(b"test\n"); (root / "etc/fstab").write_bytes(FSTAB)
                target = root / name; target.unlink()
                if kind == "content":
                    target.write_bytes(b"\xff\n" if code == "E_INSTALL_KIND" else b"private invalid content")
                elif kind == "directory":
                    target.mkdir()
                elif kind == "symlink":
                    (root / "etc/other").write_bytes(good); target.symlink_to("other")
                with self.subTest(name=name, kind=kind), self.assertRaisesRegex(BundleError, code + ": " + name + ".*" + reason) as caught:
                    core.verify_p2_contract(root, "test")
                self.assertNotIn("private invalid", str(caught.exception))
                if code == "E_INSTALL_KIND" and kind == "content":
                    with self.assertRaisesRegex(BundleError, "E_INSTALL_KIND: etc/dby-rootfs-kind content does not match"):
                        core.card_kind(root)

    def test_ext4_contract_reader_reports_missing_content_and_type_for_each_file(self):
        # Check the actual debugfs stat/dump path used by maintenance previews.
        base = self.edit_p2()
        for name, code in (("etc/dby-rootfs-kind", "E_INSTALL_KIND"), ("etc/fstab", "E_INSTALL_FSTAB")):
            for kind, reason in (("missing", "is missing"), ("content", "content does not match"), ("type", "wrong type")):
                part = self.root / (Path(name).name + "-" + kind + ".img")
                image.new_sparse(part, base.stat().st_size); image.sparse_copy(base, part, 0, 0, base.stat().st_size)
                image.run(["debugfs", "-w", "-R", "rm /" + name, part])
                if kind == "content":
                    source = self.root / "bad-content"
                    source.write_bytes(b"\xff\n" if code == "E_INSTALL_KIND" else b"private bad content")
                    image.run(["debugfs", "-w", "-R", "write " + image.quote(source) + " /" + name, part])
                elif kind == "type":
                    image.run(["debugfs", "-w", "-R", "mkdir /" + name, part])
                root = self.root / (part.stem + "-dump"); root.mkdir()
                with self.subTest(name=name, kind=kind), self.assertRaisesRegex(BundleError, code + ": " + name + ".*" + reason):
                    dump_contract(part, root)

    def test_journal_probe_is_readonly_path_free_and_uses_actual_device_in_advice(self):
        for is_image in (False, True):
            execute = Mock(return_value=b"Localized heading: has_journal needs_recovery extent\n")
            with self.subTest(image=is_image), self.assertRaisesRegex(BundleError, "E_INSTALL_P2_RECOVERY") as caught:
                check_journal(self.root / "private-filesystem", image=is_image,
                              device=None if is_image else Path("/dev/mmcblk8"), execute=execute)
            self.assertNotIn(str(self.root), str(caught.exception))
            self.assertNotIn("-w", execute.call_args.args[0])
            self.assertEqual(execute.call_args.args[0][:3], ["debugfs", "-R", "features"] if is_image else ["dumpe2fs", "-h", self.root / "private-filesystem"])
            if not is_image:
                self.assertIn("e2fsck -p /dev/mmcblk8p2", str(caught.exception))
        with self.assertRaisesRegex(BundleError, "E_INSTALL_GEOMETRY"):
            check_journal(self.root / "absent", execute=lambda command: b"")
        # Exercise actual dumpe2fs and debugfs on the same ordinary ext4 file.
        part = self.edit_p2("feature needs_recovery")
        before = region_hash(part, 0, part.stat().st_size)
        for is_image in (False, True):
            with self.assertRaisesRegex(BundleError, "E_INSTALL_P2_RECOVERY"):
                check_journal(part, image=is_image)
        self.assertEqual(region_hash(part, 0, part.stat().st_size), before)

    def test_block_verify_and_info_reject_recovery_before_any_mount(self):
        target = SimpleNamespace(path=Path("/dev/mmcblk8"), validate=Mock(return_value={"size": MIN_SIZE}))
        with patch.object(privileged, "require_root"), patch.object(privileged, "dependencies"), \
                patch.object(privileged, "check_table"), patch.object(privileged, "geometry"), \
                patch.object(privileged, "check_journal", side_effect=BundleError("E_INSTALL_P2_RECOVERY", "fixture")), \
                patch.object(privileged, "mounts") as mounts:
            for operation in (lambda: privileged.verify(target, self.old), lambda: privileged.info(target)):
                with self.assertRaisesRegex(BundleError, "E_INSTALL_P2_RECOVERY"):
                    operation()
            mounts.assert_not_called()

    def test_block_update_preflight_and_write_adapter_probe_before_mounting(self):
        target = BlockDeviceTarget(Path("/dev/mmcblk8"), lambda path: probe_row())
        with patch("dbyinstall.maintenance_io.dependencies"), patch.object(privileged, "require_root"), \
                patch.object(privileged, "check_table", return_value=1024**3 // 512), \
                patch("dbyinstall.maintenance_io.run", return_value=b"{}"), patch.object(image, "geometry"), \
                patch("dbyinstall.maintenance_io.check_journal", side_effect=BundleError("E_INSTALL_P2_RECOVERY", "fixture")), \
                patch("dbyinstall.maintenance_io.card_mounts") as mounts:
            with Backend(target, dry_run=True) as backend:
                with self.assertRaisesRegex(BundleError, "E_INSTALL_P2_RECOVERY"):
                    with backend.snapshot():
                        self.fail("Dirty snapshot cannot be entered.")
                with self.assertRaisesRegex(BundleError, "E_INSTALL_P2_RECOVERY"):
                    with backend.transaction():
                        self.fail("Dirty write transaction cannot be entered.")
            mounts.assert_not_called()

    def test_block_p1_only_mounts_only_p1_and_never_inspects_rootfs(self):
        p1 = self.root / "p1"; p1.mkdir(); core.populate_p1(p1, self.old)
        target = SimpleNamespace(path=Path("/dev/mmcblk8"), validate=Mock(return_value={"size": MIN_SIZE}))
        options = []
        @contextmanager
        def mounts(device, work, **kwargs):
            options.append(kwargs); yield [p1]
        with patch.object(privileged, "require_root"), patch.object(privileged, "dependencies"), \
                patch.object(privileged, "check_table"), patch.object(privileged, "geometry") as geometry, \
                patch.object(privileged, "check_journal", side_effect=AssertionError("Must not inspect journal")), \
                patch.object(privileged, "mounts", mounts), \
                patch.object(privileged, "verify_root", side_effect=AssertionError("Must not inspect rootfs")):
            privileged.verify(target, self.old, p1_only=True)
        self.assertEqual(options, [{"readonly": True, "p1_only": True}])
        self.assertTrue(geometry.call_args.kwargs["labels_only"])
        calls, mounted = [], set()
        def execute(command):
            calls.append(command)
            (mounted.add if command[0] == "mount" else mounted.remove)(Path(command[-1]))
            return b""
        work = self.root / "mount-work"; work.mkdir()
        with patch.object(privileged, "run", side_effect=execute), patch.object(privileged, "mounted_paths", side_effect=lambda: mounted.copy()):
            with privileged.mounts(target.path, work, readonly=True, p1_only=True):
                pass
        self.assertFalse(mounted)
        self.assertEqual([row[-2] for row in calls if row[0] == "mount"], ["/dev/mmcblk8p1"])

    def test_block_maintenance_preserves_known_kind_gate_with_specific_errors(self):
        p1, p2 = self.root / "p1", self.root / "p2"
        p1.mkdir(); (p2 / "etc").mkdir(parents=True); (p2 / "etc/fstab").write_bytes(FSTAB)
        target = BlockDeviceTarget(Path("/dev/mmcblk8"), lambda path: probe_row())
        @contextmanager
        def mounts(*args, **kwargs):
            yield p1, p2
        with patch("dbyinstall.maintenance_io.dependencies"), patch.object(privileged, "require_root"), \
                patch.object(privileged, "check_table", return_value=1024**3 // 512), \
                patch("dbyinstall.maintenance_io.run", return_value=b"{}"), patch.object(image, "geometry"), \
                patch("dbyinstall.maintenance_io.check_journal"), patch("dbyinstall.maintenance_io.card_mounts", mounts):
            for reason in ("is missing", "content does not match"):
                with Backend(target) as backend:
                    with self.assertRaisesRegex(BundleError, "E_INSTALL_KIND: etc/dby-rootfs-kind.*" + reason):
                        with backend.snapshot():
                            self.fail("Missing/unknown kind must not become a managed card.")
                (p2 / "etc/dby-rootfs-kind").write_bytes(b"unknown\n")

    def test_install_paths_do_not_use_the_new_journal_gate(self):
        for skip in (False, True):
            target = ImageFileTarget(self.root / (str(skip) + "-install.img"), MIN_SIZE)
            with patch.object(image, "check_journal", side_effect=AssertionError("Install must not use recovery gate")):
                image.install(target, self.old, minimal=True, p2_size=1024**3, no_readback=skip,
                              ask=lambda prompt: str(target.path), emit=lambda message: None)
            self.bundle = self.old
            with readback_fixtures.ReadbackTests.fake_sd(self, self.root / (str(skip) + "-fake-sd")) as fake, \
                    patch.object(privileged, "check_journal", side_effect=AssertionError("Install must not use recovery gate")):
                privileged.install(fake.target, self.old, no_readback=skip, emit=fake.output.append)
