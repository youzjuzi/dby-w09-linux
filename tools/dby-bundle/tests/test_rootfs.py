# SPDX-License-Identifier: GPL-2.0-only
"""Plan/list/inventory/packing tests only; never run privileged rootfs steps."""

from contextlib import ExitStack, nullcontext
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from dbybundle import BundleError
from dbybundle import rootfs_privileged
from dbybundle.rootfs import (RECIPE, compare_packages, inventory_metadata, make_plan, overlay_files, package_list,
                              package_manifest, pack_tree, recipe_summary)


class RootfsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.rules = json.loads((RECIPE / "sources.json").read_bytes())
        self.source = self.root / "synthetic-source"; self.source.write_bytes(b"synthetic source bytes")
        for key in ("iso", "base"):
            self.rules[key]["sha256"] = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def test_lists_preserve_cut_packages_and_reject_bad_tokens(self):
        purge = package_list(RECIPE / "purge.list")
        self.assertEqual(len(purge), 41)
        self.assertIn("flash-kernel", purge)
        self.assertIn("linux-firmware", purge)
        self.assertIn("firmware-sof-signed", purge)
        self.assertIn("power-profiles-daemon", purge)
        self.assertIn("tuned-ppd", package_list(RECIPE / "add.list"))
        self.assertNotIn("alsa-topology-conf", purge)
        self.assertIn("openssh-server", package_list(RECIPE / "add.list"))
        path = self.root / "list"
        for data in ("--option\n", "name;command\n", "pkg\npkg\n"):
            path.write_text(data)
            with self.assertRaisesRegex(BundleError, "E_ROOTFS_PACKAGE"):
                package_list(path)
        path.write_text("# comment\nfixture=1:2.0-1 # fixed\n")
        self.assertEqual(package_list(path), ["fixture=1:2.0-1"])

    def test_full_plan_hashes_order_overlay_and_no_side_effects(self):
        output = self.root / "full-out"
        plan = make_plan("full", self.source, output, rules=self.rules)
        stages = [row["stage"] for row in plan["stages"]]
        self.assertLess(stages.index("verify-minimal"), stages.index("extract-root"))
        self.assertLess(stages.index("purge"), stages.index("add"))
        self.assertLess(stages.index("add"), stages.index("check-payload"))
        self.assertLess(stages.index("check-payload"), stages.index("overlay"))
        self.assertLess(stages.index("check-payload"), stages.index("system-units"))
        check = next(row for row in plan["stages"] if row["stage"] == "check-payload")
        self.assertEqual(set(check["firmware_allowlist"]), {"regulatory.db", "regulatory.db.p7s", "skl_hda_dsp_generic-tplg.bin"})
        self.assertLess(stages.index("clean"), stages.index("pack"))
        self.assertEqual(plan["marker"]["content"], "full\n")
        self.assertIn("usr/lib/dby/dby-seed-apply", overlay_files("full"))
        self.assertIn("etc/xdg/monitors.xml", overlay_files("full"))
        self.assertFalse(output.exists())
        clean = next(row for row in plan["stages"] if row["stage"] == "clean")
        self.assertEqual(clean["resolv_conf"], "../run/systemd/resolve/stub-resolv.conf")
        self.assertIn("static dev entries", clean["remove"])
        self.assertTrue(check["allow_dev_special"])
        argv = next(row for row in plan["stages"] if row["stage"] == "pack")["argv"]
        self.assertIn("--numeric-owner", argv)
        self.assertFalse(any(arg.startswith(("--owner", "--group")) for arg in argv))
        self.assertIn("-T0", next(row for row in plan["stages"] if row["stage"] == "pack")["compress"])

    def test_test_plan_minimal_network_runtime_and_marker(self):
        plan = make_plan("test", self.source, self.root / "test-out", rules=self.rules)
        self.assertFalse(plan["purge"])
        self.assertIn("python3", plan["add"])
        self.assertIn("network-manager", plan["add"])
        self.assertNotIn("systemd-networkd", plan["add"])
        required = {"openssh-server", "python3", "network-manager", "systemd-resolved", "iproute2", "iw", "wireless-regdb",
                    "systemd", "systemd-sysv", "udev", "kmod", "dbus", "libpam-systemd", "ca-certificates", "iputils-ping", "netbase",
                    "wpasupplicant", "rfkill", "systemd-timesyncd", "curl"}
        self.assertEqual(set(plan["add"]), required)
        self.assertEqual(plan["artifact"], "rootfs-test.tar.zst")
        self.assertEqual(plan["marker"]["content"], "test\n")
        self.assertNotIn("etc/xdg/monitors.xml", overlay_files("test"))
        self.assertIn("etc/systemd/network/50-dby-usb0.network", overlay_files("test"))
        unit = next(row for row in plan["stages"] if row["stage"] == "system-units")
        self.assertIn("systemd-networkd", unit["enable"])
        self.assertIn("systemd-timesyncd", unit["enable"])
        stages = [row["stage"] for row in plan["stages"]]
        self.assertLess(stages.index("add"), stages.index("system-units"))
        install = next(row for row in plan["stages"] if row["stage"] == "add")["argv"]
        self.assertIn("--no-install-recommends", install)
        self.assertIn("--no-remove", install)
        self.assertEqual(unit["default"], "multi-user.target")
        self.assertNotIn("gdm", unit["enable"])
        clean = next(row for row in plan["stages"] if row["stage"] == "clean")
        self.assertEqual(clean["resolv_conf"], "../run/systemd/resolve/stub-resolv.conf")

    def test_test_service_commands_follow_the_plan_after_package_install(self):
        plan = make_plan("test", self.source, self.root / "test-out", rules=self.rules)
        work = self.root / "fake-work"; work.mkdir()
        root = work / "root"
        commands = []

        def fake_run(argv, stage):
            commands.append((list(map(str, argv)), stage))
            if stage == "rootfs-extract":
                (root / "etc/apt/sources.list.d").mkdir(parents=True)
                (root / "var/lib/dpkg").mkdir(parents=True)
                (root / "var/lib/dpkg/status").write_text("")
                (root / "etc/passwd").write_text("root:x:0:0:fixture:/root:/bin/sh\n")
                (root / "dev").mkdir()
                os.mkfifo(root / "dev/static-fixture")

        # Replace every privileged effect; execute only ordinary-file orchestration.
        with ExitStack() as stack:
            replacements = {"make_plan": lambda *a, **k: plan, "tool": lambda *a: None,
                "native_or_binfmt": lambda: None, "run": fake_run,
                "workspace": lambda *a: nullcontext(work), "environment": lambda *a: nullcontext(),
                "temporary_mirror": lambda *a: nullcontext(), "overlay_files": lambda *a: {},
                "put": lambda *a: None, "clean": lambda *a: None, "publish": lambda *a: None,
                "pack_tree": lambda tree, archive, epoch: archive.write_bytes(b"synthetic archive")}
            for name, replacement in replacements.items():
                stack.enter_context(patch.object(rootfs_privileged, name, replacement))
            stack.enter_context(patch.object(rootfs_privileged.os, "geteuid", return_value=0))
            stack.enter_context(patch.object(rootfs_privileged.ctypes, "CDLL", return_value=SimpleNamespace(unshare=lambda flags: 0)))
            rootfs_privileged.execute(plan, self.source, self.root / "test-out")
        units = next(row for row in plan["stages"] if row["stage"] == "system-units")
        enabled = [argv for argv, stage in commands if stage == "rootfs-enable"]
        self.assertIn(["systemctl", "--root", str(root), "enable", "systemd-timesyncd.service"], enabled)
        self.assertEqual(enabled, [["systemctl", "--root", str(root), "enable", unit + ".service"] for unit in units["enable"]])
        install_index = next(i for i, (argv, stage) in enumerate(commands) if stage == "rootfs-apt" and "install" in argv)
        self.assertLess(install_index, next(i for i, (argv, stage) in enumerate(commands) if stage == "rootfs-enable"))
        full = make_plan("full", self.source, self.root / "full-out", rules=self.rules)
        full_units = next(row for row in full["stages"] if row["stage"] == "system-units")
        self.assertIn("chrony", full_units["enable"])
        self.assertNotIn("systemd-timesyncd", full_units["enable"])
        self.assertEqual(full["add"], ["openssh-server", "python3", "iw", "wireless-regdb", "tuned-ppd"])

    def test_source_hash_gate_and_missing_input_plan(self):
        missing = make_plan("full", None, self.root / "missing-out")
        self.assertEqual(missing["missing_inputs"], ["desktop ISO"])
        with self.assertRaisesRegex(BundleError, "E_ROOTFS_SOURCE_HASH"):
            make_plan("full", self.source, self.root / "wrong-out")
        self.assertFalse((self.root / "wrong-out").exists())

    def test_recipe_hashes_stable_and_profile_specific(self):
        self.assertEqual(recipe_summary("full"), recipe_summary("full"))
        self.assertNotEqual(recipe_summary("full")["recipe_sha256"], recipe_summary("test")["recipe_sha256"])
        self.assertIn("purge.list", recipe_summary("full")["files"])

    def test_test_package_edit_changes_only_its_recipe_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            recipe = Path(temporary) / "recipe"
            shutil.copytree(RECIPE, recipe)
            with patch("dbybundle.rootfs.RECIPE", recipe):
                original = recipe_summary("test")
                full_before = recipe_summary("full")
                with (recipe / "test/add.list").open("ab") as stream:
                    stream.write(b"fixture-package\n")
                changed = recipe_summary("test")
                self.assertNotEqual(changed["files"]["add.list"], original["files"]["add.list"])
                self.assertNotEqual(changed["recipe_sha256"], original["recipe_sha256"])
                self.assertEqual(recipe_summary("full"), full_before)

    def test_package_inventory_and_real_differences(self):
        status = ("Package: old\nVersion: 1\nArchitecture: arm64\nStatus: install ok installed\n\n"
                  "Package: absent\nStatus: deinstall ok config-files\n\n")
        before = package_manifest(status)
        self.assertEqual(before, b"old 1 arm64\n")
        changed = compare_packages(before + b"removed 2 arm64\n", b"old 3 arm64\nadded 1 all\n")
        self.assertEqual(changed["added"], ["added 1 all"])
        self.assertEqual(changed["removed"], ["removed 2 arm64"])
        self.assertEqual(changed["changed"][0]["after"], "3")
        with self.assertRaisesRegex(BundleError, "E_ROOTFS_PACKAGES"):
            package_manifest(status + status)

    def test_deterministic_tar_zstd_normalizes_time_preserves_owner(self):
        tree = self.root / "tree"; tree.mkdir()
        (tree / "etc").mkdir(); (tree / "etc/fixture").write_bytes(b"synthetic file")
        (tree / "alias").symlink_to("etc/fixture")
        first, second = self.root / "a.zst", self.root / "b.zst"
        pack_tree(tree, first, 123)
        os.utime(tree / "etc/fixture", (999999, 999999))
        pack_tree(tree, second, 123)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        plain = subprocess.run(["zstd", "-q", "-d", "-c", str(first)], capture_output=True, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(plain)) as archive:
            for item in archive:
                original = (tree / item.name).lstat()
                self.assertEqual((item.uid, item.gid, item.mtime), (original.st_uid, original.st_gid, 123))
                self.assertEqual((item.uname, item.gname), ("", ""))

    def test_optional_baseline_and_explicit_waiver(self):
        path = self.root / "baseline.txt"; data = b"old 1 arm64\n"; path.write_bytes(data)
        rules = dict(self.rules, baseline_packages_sha256=hashlib.sha256(data).hexdigest(), baseline_package_count=1)
        plan = make_plan("full", self.source, self.root / "compared", baseline=path, rules=rules)
        self.assertEqual(plan["baseline"]["mode"], "compare")
        for waiver in (False, True):
            plan = make_plan("full", self.source, self.root / "new", no_baseline=waiver, rules=rules)
            self.assertEqual(plan["baseline"], {"mode": "new", "explicit_no_baseline": waiver})
            self.assertNotIn("baseline_required_for_full_execution", plan)
        with self.assertRaisesRegex(BundleError, "E_ROOTFS_BASELINE"):
            make_plan("full", self.source, self.root / "conflict", baseline=path, no_baseline=True, rules=rules)
        path.write_bytes(b"changed 1 arm64\n")
        with self.assertRaisesRegex(BundleError, "E_ROOTFS_BASELINE"):
            make_plan("full", self.source, self.root / "changed", baseline=path, rules=rules)

    def test_inventory_metadata_records_comparison_or_new_baseline(self):
        actual = b"old 2 arm64\n"
        result = inventory_metadata(actual)
        difference = result["package_difference"]
        self.assertFalse(difference["compared"])
        self.assertEqual(difference["new_baseline"], {"file": "packages.txt", "sha256": hashlib.sha256(actual).hexdigest(), "package_count": 1})
        difference = inventory_metadata(actual, b"old 1 arm64\n")["package_difference"]
        self.assertTrue(difference["compared"])
        self.assertEqual(difference["changed"], [{"package": "old", "architecture": "arm64", "before": "1", "after": "2"}])

    def test_cli_missing_iso_and_bundle_rootfs_entry(self):
        for command in ([str(TOOL / "build-rootfs")], [str(TOOL / "build-bundle"), "--stage", "rootfs"]):
            output = self.root / ("cli-" + str(len(command)))
            result = subprocess.run([sys.executable, "-I", "-B", *command, "--kind", "full", "--output", str(output), "--dry-run"], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"E_ROOTFS_INPUT", result.stderr)
            self.assertEqual(json.loads(result.stdout)["missing_inputs"], ["desktop ISO"])
            self.assertFalse(output.exists())
        result = subprocess.run([sys.executable, "-I", "-B", str(TOOL / "build-rootfs"), "--kind", "full",
                                 "--output", str(self.root / "waived"), "--no-baseline", "--dry-run"], capture_output=True)
        self.assertEqual(json.loads(result.stdout)["baseline"], {"mode": "new", "explicit_no_baseline": True})
        self.assertIn(b"E_ROOTFS_INPUT", result.stderr)


if __name__ == "__main__":
    unittest.main()
