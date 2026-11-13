# SPDX-License-Identifier: GPL-2.0-only
"""Synthetic payload gates and full build ordering; no privileged effects."""

from contextlib import ExitStack, contextmanager, nullcontext
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dbybundle import BundleError, rootfs, rootfs_privileged


class PayloadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def tree(self, root):
        (root / "etc").mkdir(parents=True)
        (root / "etc/passwd").write_text("root:x:0:0:fixture:/root:/bin/sh\nnobody:x:65534:65534:fixture:/:/bin/false\n")
        firmware = root / "usr/lib/firmware"; firmware.mkdir(parents=True)
        return firmware

    def test_clean_accepts_only_regdb_and_alsa_topology_data(self):
        firmware = self.tree(self.root)
        for name in ("regulatory.db", "regulatory.db.p7s", "skl_hda_dsp_generic-tplg.bin"):
            (firmware / name).write_bytes(b"synthetic allowed data")
        with patch.object(rootfs_privileged.os, "chown"):
            rootfs_privileged.clean(self.root)
        self.assertEqual(len(list(firmware.iterdir())), 3)

    def test_clean_rejects_vendor_firmware_with_first_relative_path(self):
        firmware = self.tree(self.root)
        for name in ("z-vendor.bin", "intel/sof/x.ri"):
            path = firmware / name; path.parent.mkdir(parents=True, exist_ok=True); path.touch()
        with patch.object(rootfs_privileged.os, "chown"), self.assertRaises(BundleError) as caught:
            rootfs_privileged.clean(self.root)
        message = str(caught.exception)
        self.assertIn("E_ROOTFS_FIRMWARE", message)
        self.assertIn("intel/sof/x.ri", message)
        self.assertNotIn(str(self.root), message)
        self.assertNotIn("z-vendor.bin", message)

    def test_allowlist_is_exact_for_nested_files_and_dangling_links(self):
        for name in ("intel/regulatory.db", "vendor-link"):
            with self.subTest(name=name):
                tree = self.root / name.replace("/", "-")
                firmware = self.tree(tree)
                path = firmware / name; path.parent.mkdir(parents=True, exist_ok=True)
                if name == "vendor-link":
                    path.symlink_to("missing-vendor.bin")
                else:
                    path.touch()
                with patch.object(rootfs_privileged.os, "chown"), self.assertRaises(BundleError) as caught:
                    rootfs_privileged.clean(tree)
                self.assertIn(name, str(caught.exception))

    def test_unexpected_firmware_names_are_escaped_for_diagnostics(self):
        firmware = self.tree(self.root)
        (firmware / "unexpected\nfirmware.bin").touch()
        with patch.object(rootfs_privileged.os, "chown"), self.assertRaises(BundleError) as caught:
            rootfs_privileged.clean(self.root)
        self.assertIn("unexpected\\nfirmware.bin", str(caught.exception))
        self.assertNotIn("\n", str(caught.exception))

    def test_clean_rechecks_payload_if_it_changes_after_the_early_gate(self):
        for kind, code in (("users", "E_ROOTFS_USERS"), ("modules", "E_ROOTFS_MODULES")):
            with self.subTest(kind=kind):
                tree = self.root / kind; self.tree(tree)
                rootfs_privileged.check_payload(tree)
                if kind == "users":
                    with (tree / "etc/passwd").open("a") as stream:
                        stream.write("fixture:x:1000:1000::/:/bin/false\n")
                else:
                    (tree / "usr/lib/modules/unexpected").mkdir(parents=True)
                with patch.object(rootfs_privileged.os, "chown"), self.assertRaisesRegex(BundleError, code):
                    rootfs_privileged.clean(tree)

    def test_full_payload_failures_stop_after_apt_before_overlay_and_services(self):
        cases = (("uid", "E_ROOTFS_USERS"), ("passwd", "E_ROOTFS_USERS"), ("nobody", "E_ROOTFS_USERS"),
                 ("home", "E_ROOTFS_USERS"), ("modules", "E_ROOTFS_MODULES"), ("firmware", "E_ROOTFS_FIRMWARE"))
        for kind, code in cases:
            with self.subTest(kind=kind):
                work = self.root / kind; work.mkdir()
                tree = work / "root"
                commands, events = [], []
                plan = rootfs.make_plan("full", None, self.root / "output")
                plan["missing_inputs"] = []
                recipe = work / "recipe"; recipe.mkdir()
                minimal = b"synthetic minimal layer"
                (recipe / "sources.json").write_text(json.dumps({"minimal_squashfs_sha256": hashlib.sha256(minimal).hexdigest()}))

                def fake_run(argv, stage, **kwargs):
                    commands.append((list(map(str, argv)), stage))
                    if stage == "rootfs-iso":
                        kwargs["stdout"].write(minimal)
                    elif stage == "rootfs-extract":
                        firmware = self.tree(tree)
                        (tree / "var/lib/dpkg").mkdir(parents=True)
                        (tree / "var/lib/dpkg/status").write_text("Package: firmware-sof-signed\nVersion: 1\nArchitecture: arm64\nStatus: install ok installed\n\n")
                        if kind in ("uid", "nobody"):
                            with (tree / "etc/passwd").open("a") as stream:
                                stream.write(("fixture:x:1000:1000::/:/bin/false" if kind == "uid" else "nobody:x:1000:1000::/:/bin/false") + "\n")
                        elif kind == "passwd":
                            (tree / "etc/passwd").write_text("malformed\n")
                        elif kind == "home":
                            (tree / "home/fixture").mkdir(parents=True)
                        elif kind == "modules":
                            (tree / "usr/lib/modules/unexpected").mkdir(parents=True)
                        elif kind == "firmware":
                            (firmware / "intel/sof").mkdir(parents=True)
                            (firmware / "intel/sof/x.ri").touch()
                    elif stage == "rootfs-apt":
                        events.append("apt")

                @contextmanager
                def fake_environment(root):
                    yield
                    events.append("environment-restored")

                overlay, put, pack, publish = Mock(return_value={}), Mock(), Mock(), Mock()
                with ExitStack() as stack:
                    replacements = {"RECIPE": recipe, "make_plan": lambda *a, **k: plan, "tool": lambda *a: None,
                        "native_or_binfmt": lambda: None, "run": fake_run, "workspace": lambda *a: nullcontext(work),
                        "environment": fake_environment, "temporary_mirror": lambda *a: nullcontext(),
                        "overlay_files": overlay, "put": put, "pack_tree": pack, "publish": publish}
                    for name, value in replacements.items():
                        stack.enter_context(patch.object(rootfs_privileged, name, value))
                    stack.enter_context(patch.object(rootfs_privileged.os, "geteuid", return_value=0))
                    stack.enter_context(patch.object(rootfs_privileged.ctypes, "CDLL", return_value=SimpleNamespace(unshare=lambda flags: 0)))
                    with self.assertRaisesRegex(BundleError, code):
                        rootfs_privileged.execute(plan, self.root / "source", self.root / "output")
                apt = [argv for argv, stage in commands if stage == "rootfs-apt"]
                self.assertTrue(any("purge" in argv and "firmware-sof-signed" in argv for argv in apt))
                self.assertIn("install", apt[-1])
                self.assertEqual(events[-1], "environment-restored")
                self.assertFalse(any(stage.startswith("rootfs-enable") for argv, stage in commands))
                for effect in (overlay, put, pack, publish):
                    effect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
