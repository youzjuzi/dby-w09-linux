# SPDX-License-Identifier: GPL-2.0-only
"""Real host-tool integration with small synthetic inputs, without root."""

from contextlib import contextmanager
import gzip
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from dbybundle import BundleError, verify
from dbybundle import builder as B
from dbybundle.build_inputs import load_config, private_inventory
from synthetic import FIRMWARE, MODULES, elf, fixture, write


class BuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.base.cleanup)
        cls.original = Path(cls.base.name) / "inputs"
        cls.original.mkdir()
        fixture(cls.original)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "inputs"
        shutil.copytree(self.original, self.root)
        self.config_path = self.root / "bundle.json"
        self.data = json.loads(self.config_path.read_bytes())
        self.output = self.root / "bundle"

    def config(self):
        self.config_path.write_text(json.dumps(self.data))
        return self.config_path

    @contextmanager
    def error(self, code):
        with self.assertRaises(BundleError) as caught:
            yield
        self.assertEqual(caught.exception.code, code)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob(".dby-build-*")))

    def build(self, **kwargs):
        return B.build(self.config(), emit=lambda message: None, **kwargs)

    def unpack(self, name="unpacked"):
        destination = self.root / name
        subprocess.run(["unsquashfs", "-no-progress", "-d", str(destination),
                        str(self.output / "p1/UBOOT/dby-device.img")], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return destination

    def test_real_tools_pipeline_contract_contents_and_repeatability(self):
        first = self.build()
        self.assertEqual(verify(self.output), first)
        self.assertEqual(set(path.name for path in (self.output / "p1/UBOOT").iterdir()), set(BootNames))
        tree = self.unpack()
        self.assertTrue((tree / FIRMWARE).is_file())
        self.assertTrue((tree / "modules/7.2.10" / MODULES[2]).is_file())
        options = (tree / "etc/modprobe.d/dby-w09-thp.conf").read_text().splitlines()
        self.assertEqual([line for line in options if line.startswith("options ")], [
            "options nt36523_thp managed=1 settle_ms=100 skip_probe=1 edge_px=50 "
            "filter=1 lock_px=12 confirm=1 multi=1 guard=0"])
        self.assertIn("pci-pwrctrl-pwrseq.ko", (tree / "modules/7.2.10/modules.dep").read_text())
        self.assertTrue((tree / "etc/systemd/system/multi-user.target.wants/dby-w09-usb-gadget.service").is_symlink())
        self.assertEqual(stat.S_IMODE((tree / "bin/dby-w09-rtc-offset").stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((tree / FIRMWARE).stat().st_mode), 0o644)
        self.assertEqual((tree / "DEVICE-CONTENTS.txt").read_bytes(), B.contents(tree))
        initrd = gzip.decompress((self.output / "p1/UBOOT/initrd.img").read_bytes())
        self.assertTrue(initrd.startswith(b"070701"))
        self.assertIn(b"SAFE MODE: runtime module policy installed", initrd)
        for name in (
            "tuned/profiles/dby-w09-balanced/tuned.conf",
            "tuned/profiles/dby-w09-powersave/tuned.conf",
            "tuned/profiles/dby-w09-performance/tuned.conf",
            "tuned/ppd.conf", "tuned/recommend.conf",
            "systemd/system/tuned.service.d/50-dby-w09.conf",
            "systemd/system/tuned-ppd.service.d/50-dby-w09.conf",
        ):
            self.assertEqual((tree / "etc" / name).read_bytes(),
                             (TOOL / "device-rootfs/etc" / name).read_bytes())
            self.assertIn(name.encode(), initrd)
        self.assertIn(first["files"]["p1/UBOOT/dby-device.img"]["sha256"].encode(), initrd)
        self.assertEqual(gzip.decompress((self.output / "p1/UBOOT/Image").read_bytes()),
                         (self.root / "kernel/arch/arm64/boot/Image").read_bytes())
        self.assertNotIn(str(self.root), json.dumps(first))
        self.assertNotIn("synthetic firmware", json.dumps(first))
        self.data["output"] = "bundle-again"
        second = self.build()
        self.assertEqual(first, second)
        for path in self.output.rglob("*"):
            if path.is_file():
                self.assertEqual(path.read_bytes(), (self.root / "bundle-again" / path.relative_to(self.output)).read_bytes())

    def test_touch_off_same_dtb_omits_module_firmware_and_configuration(self):
        self.data["touch"]["enabled"] = False
        self.build()
        tree = self.unpack()
        for name in (FIRMWARE, "modules/7.2.10/" + MODULES[2], "etc/modprobe.d/dby-w09-thp.conf"):
            self.assertFalse((tree / name).exists())
        self.assertEqual((self.output / "p1/UBOOT/dby-w09.dtb").read_bytes(), (self.root / "board.dtb").read_bytes())

    def test_release_and_kernel_feature_guards(self):
        release = self.root / "kernel/include/config/kernel.release"
        release.write_text("wrong\n")
        with self.error("E_BUILD_RELEASE"):
            self.build()
        release.write_text("7.2.10\n")
        config = self.root / "kernel/.config"
        config.write_text(config.read_text() + "CONFIG_MODULE_SIG_ALL=y\n")
        with self.error("E_BUILD_CONFIG"):
            self.build()

    def test_wrong_vermagic_selected_and_unselected(self):
        for name in ("kernel/drivers/extra.ko", MODULES[0]):
            target = self.root / "kernel" / name.removeprefix("kernel/")
            target.parent.mkdir(parents=True, exist_ok=True)
            elf(target, vermagic="wrong")
            with self.error("E_BUILD_VERMAGIC"):
                self.build()
            if "extra" in name:
                target.unlink()

    def test_touch_missing_firmware_and_module(self):
        self.data["private_inputs"] = []
        with self.error("E_BUILD_TOUCH"):
            self.build()
        self.data["private_inputs"] = ["private"]
        (self.root / "kernel" / MODULES[2].removeprefix("kernel/")).unlink()
        with self.error("E_BUILD_INPUT"):
            self.build()

    def test_private_hash_and_mode_rejected(self):
        path = self.root / "private" / FIRMWARE
        path.write_bytes(b"tampered")
        with self.error("E_BUILD_PRIVATE_HASH"):
            self.build()
        path.write_bytes(b"synthetic firmware")
        path.chmod(0o600)
        with self.error("E_BUILD_PRIVATE_MODE"):
            self.build()

    def test_private_inventory_extra_links_and_traversal(self):
        private = self.root / "private"
        extra = private / "extra"
        extra.mkdir()
        with self.error("E_BUILD_PRIVATE"):
            private_inventory(private)
        extra.rmdir()
        payload = private / FIRMWARE
        payload.unlink(); payload.symlink_to(self.root / "board.dtb")
        with self.error("E_BUILD_LINK"):
            private_inventory(private)
        payload.unlink(); write(payload, b"synthetic firmware")
        lock = private / "inputs.json"
        document = json.loads(lock.read_bytes())
        document["files"]["../escape"] = document["files"].pop(FIRMWARE)
        lock.write_text(json.dumps(document))
        with self.error("E_BUNDLE_PATH"):
            private_inventory(private)

    def test_config_errors_and_output_guards(self):
        for key, value in (("epoch", -1), ("sequence", True), ("touch", {"enabled": 1}),
                           ("unknown", "value"), ("external_modules", {"../escape": "board.dtb"})):
            original = dict(self.data)
            self.data[key] = value
            with self.assertRaises(BundleError):
                load_config(self.config())
            self.data = original
        self.data["output"] = "kernel/nested"
        with self.assertRaisesRegex(BundleError, "E_BUILD_OVERLAP"):
            load_config(self.config())
        self.data["output"] = "bundle"
        self.output.mkdir()
        write(self.output / "keep", b"keep")
        with self.assertRaisesRegex(BundleError, "E_BUILD_EXISTS"):
            self.build()
        self.assertEqual((self.output / "keep").read_bytes(), b"keep")

    def test_epoch_precedence_and_invalid_creation_time(self):
        with patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "invalid"}):
            self.assertEqual(load_config(self.config())["epoch"], 0)
        self.data.pop("epoch")
        with patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "42"}):
            self.assertEqual(load_config(self.config())["epoch"], 42)
        self.data["created"] = "2026-02-30T00:00:00Z"
        with self.error("E_BUILD_CONFIG"):
            self.build()

    def test_private_hardlinks_reserved_policy_and_collision(self):
        payload = self.root / "private" / FIRMWARE
        os.link(payload, self.root / "extra-link")
        with self.error("E_BUILD_LINK"):
            self.build()
        (self.root / "extra-link").unlink()
        lock = self.root / "private/inputs.json"
        document = json.loads(lock.read_bytes())
        row = document["files"].pop(FIRMWARE)
        document["files"]["rootfs/etc/modprobe.d/dby-safe-mode.conf"] = row
        lock.write_text(json.dumps(document))
        with self.error("E_BUILD_PRIVATE"):
            self.build()

    def test_touch_parameter_and_depmod_alias_guards(self):
        module = self.root / "kernel" / MODULES[2].removeprefix("kernel/")
        original = module.read_bytes()
        elf(module)
        with self.error("E_BUILD_TOUCH"):
            self.build()
        module.write_bytes(original)
        def missing_alias(argv, stage):
            result = B.run(argv, stage)
            if stage == "depmod":
                base = Path(argv[2])
                (base / "lib/modules/7.2.10/modules.alias").write_text("")
            return result
        with self.error("E_BUILD_TOUCH"):
            self.build(execute=missing_alias)

    def touch_parameter_types(self):
        module = self.root / "kernel" / MODULES[2].removeprefix("kernel/")
        lines = subprocess.check_output(["modinfo", "-F", "parmtype", str(module)], text=True).splitlines()
        return module, dict(line.split(":") for line in lines)

    def test_touch_exact_ten_parameters_with_guard_pass(self):
        module, parameters = self.touch_parameter_types()
        self.assertEqual(len(parameters), 10)
        self.assertEqual(parameters["guard"], "uint")
        B.touch_parameters(module, B.run)

    def test_touch_nine_parameters_without_guard_rejected(self):
        module, parameters = self.touch_parameter_types()
        del parameters["guard"]
        elf(module, touch=True, parameters=parameters)
        with self.error("E_BUILD_TOUCH"):
            self.build()

    def test_touch_unknown_extra_parameter_rejected(self):
        module, parameters = self.touch_parameter_types()
        parameters["unknown"] = "uint"
        elf(module, touch=True, parameters=parameters)
        with self.error("E_BUILD_TOUCH"):
            self.build()

    def test_unresolved_dependencies_and_installed_module_changes(self):
        def unresolved(argv, stage):
            result = B.run(argv, stage)
            if stage == "depmod":
                path = Path(argv[2]) / "lib/modules/7.2.10/modules.dep"
                path.write_text(MODULES[0] + ": missing.ko\n")
            return result
        with self.error("E_BUILD_DEPMOD"):
            self.build(execute=unresolved)
        def altered(argv, stage):
            result = B.run(argv, stage)
            if stage == "modules-install":
                base = Path(next(str(item).split("=", 1)[1] for item in argv if str(item).startswith("INSTALL_MOD_PATH=")))
                (base / "lib/modules/7.2.10" / MODULES[0]).write_bytes(b"altered")
            return result
        with self.error("E_BUILD_MODULE_CHANGED"):
            self.build(execute=altered)

    def test_external_module_override(self):
        replacement = self.root / "replacement.ko"
        elf(replacement)
        self.data["external_modules"] = {MODULES[0]: "replacement.ko"}
        self.build()
        tree = self.unpack()
        self.assertEqual((tree / "modules/7.2.10" / MODULES[0]).read_bytes(), replacement.read_bytes())

    def test_failed_stage_and_changed_input_never_publish(self):
        def fail(argv, stage):
            if stage == "squashfs":
                raise BundleError("E_BUILD_COMMAND", "Synthetic stage failure.")
            return B.run(argv, stage)
        with self.error("E_BUILD_COMMAND"):
            self.build(execute=fail)
        def change(argv, stage):
            result = B.run(argv, stage)
            if stage == "initramfs-compile":
                with (self.root / "board.dtb").open("ab") as stream:
                    stream.write(b"changed")
            return result
        with self.error("E_BUILD_CHANGED"):
            self.build(execute=change)

    def test_no_replace_publish_preserves_racing_directory(self):
        source = self.root / "staged"; source.mkdir()
        self.output.mkdir()
        write(self.output / "keep", b"keep")
        with self.assertRaisesRegex(BundleError, "E_BUILD_EXISTS"):
            B.publish(source, self.output)
        self.assertTrue(source.is_dir())
        self.assertEqual((self.output / "keep").read_bytes(), b"keep")

    def test_tool_error_output_does_not_leak_input_paths(self):
        with self.assertRaises(BundleError) as caught:
            B.run([sys.executable, "-I", "-B", "-c", "import sys; print(sys.argv[1]); sys.exit(1)", str(self.root)], "fixture")
        self.assertNotIn(str(self.root), str(caught.exception))

    def test_cli_build_and_missing_config(self):
        command = [sys.executable, "-I", "-B", str(TOOL / "build-bundle"), "--config", str(self.config())]
        result = subprocess.run(command, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(str(self.root).encode(), result.stdout + result.stderr)
        result = subprocess.run(command[:-1] + [str(self.root / "absent")], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"E_BUILD_INPUT", result.stderr)
        self.assertNotIn(str(self.root).encode(), result.stderr)


BootNames = ("Image", "dby-w09.dtb", "initrd.img", "dby-device.img", "MANIFEST")
