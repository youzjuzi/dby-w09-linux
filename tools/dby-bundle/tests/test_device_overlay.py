# SPDX-License-Identifier: GPL-2.0-only
"""Focused regressions for copied runtime helpers with fake roots and calls."""

import importlib.machinery
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

OVERLAY = Path(__file__).resolve().parents[1] / "device-rootfs"


def helper(name):
    path = OVERLAY / "bin" / name
    loader = importlib.machinery.SourceFileLoader("fixture_helper", str(path))
    specification = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(specification)
    loader.exec_module(module)
    return module


class DeviceOverlayTests(unittest.TestCase):
    def test_usb_fake_configfs_create_repeat_and_refuse_unknown_gadget(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config"; (config / "usb_gadget").mkdir(parents=True)
            controllers = root / "controllers"; controllers.mkdir()
            (controllers / "dummy").mkdir()
            environment = dict(os.environ, DBY_CONFIGFS_ROOT=str(config), DBY_UDC_ROOT=str(controllers))
            def run():
                return subprocess.run(["sh", str(OVERLAY / "bin/dby-w09-usb-gadget")],
                                      env=environment, capture_output=True)
            first = run()
            self.assertEqual(first.returncode, 0, first.stderr)
            gadget = config / "usb_gadget/dby-w09"
            self.assertFalse((gadget / "strings/0x409/serialnumber").exists())
            self.assertFalse((gadget / "functions/ncm.usb0/dev_addr").exists())
            repeated = run()
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            (gadget / "idProduct").write_text("unknown")
            self.assertNotEqual(run().returncode, 0)
            self.assertEqual((gadget / "idProduct").read_text(), "unknown")

    def test_rtc_offset_save_uses_only_temporary_state(self):
        module = helper("dby-w09-rtc-offset")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "since_epoch").write_text("1000")
            saved = Mock()
            result = module.run("save", rtc=root, offset=root / "offset", now=lambda: module.LOW,
                                synced=lambda: True, write=saved, emit=lambda message: None)
            self.assertEqual(result, 0)
            saved.assert_called_once_with(root / "offset", module.LOW - 1000)
            saved.reset_mock()
            result = module.run("save", rtc=root, offset=root / "offset", now=lambda: module.LOW,
                                synced=lambda: False, write=saved, emit=lambda message: None)
            self.assertEqual(result, 0)
            saved.assert_not_called()

    def test_watchdog_is_manual_only_and_retained(self):
        from dbybundle.build_archive import overlay
        name = 'systemd/system/multi-user.target.wants/dby-w09-display-watchdog.service'
        self.assertNotIn(name, (OVERLAY / 'wants-links.txt').read_text().splitlines())
        with tempfile.TemporaryDirectory() as temporary:
            tree = Path(temporary)
            overlay(tree, True)
            self.assertFalse((tree / 'etc' / name).is_symlink())
            self.assertFalse((tree / 'etc' / name).exists())
            self.assertEqual((tree / 'etc/systemd/system/dby-w09-display-watchdog.service').read_bytes(),
                             (OVERLAY / 'etc/systemd/system/dby-w09-display-watchdog.service').read_bytes())
            self.assertTrue(os.access(tree / 'bin/dby-w09-display-watchdog', os.X_OK))

    def test_watchdog_retries_display_on_without_accessing_devices(self):
        module = helper("dby-w09-display-watchdog")
        answers = [Mock(returncode=0), Mock(returncode=1), Mock(returncode=0)]
        with patch.object(module.glob, "glob", return_value=["/run/user/1000/wayland-0"]), \
                patch.object(module.os, "stat", return_value=Mock(st_uid=1000)), \
                patch.object(module.pwd, "getpwuid", return_value=Mock(pw_name="fixture")), \
                patch.object(module.subprocess, "run", side_effect=answers) as run, \
                patch.object(module.time, "sleep"):
            self.assertEqual(module.wayland_dpms(), ("mutter PowerSaveMode", True))
            self.assertEqual([call.args[0][-1] for call in run.call_args_list],
                             ["<int32 3>", "<int32 0>", "<int32 0>"])
