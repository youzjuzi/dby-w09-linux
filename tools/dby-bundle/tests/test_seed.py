# SPDX-License-Identifier: GPL-2.0-only
"""Seed parsing and whole temporary-root application without host side effects."""

import base64
import ctypes
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

TOOL = Path(__file__).resolve().parents[1]
SCRIPT = TOOL / "rootfs/overlay/usr/lib/dby/dby-seed-apply"
loader = importlib.machinery.SourceFileLoader("seed_apply", str(SCRIPT))
specification = importlib.util.spec_from_loader(loader.name, loader)
S = importlib.util.module_from_spec(specification)
loader.exec_module(S)


def public_key():
    def field(value):
        return struct.pack(">I", len(value)) + value
    return "ssh-ed25519 " + base64.b64encode(field(b"ssh-ed25519") + field(bytes(range(32)))).decode()


class SeedTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "root"; self.root.mkdir()
        self.seed = Path(temporary.name) / "seed"; self.seed.mkdir()
        source = self.root / "etc/apt/sources.list.d/ubuntu.sources"
        source.parent.mkdir(parents=True)
        self.defaults = b"Types: deb\nURIs: https://fixture.invalid/default\nSuites: fixture fixture-updates\nComponents: main\n"
        source.write_bytes(self.defaults)
        self.timezone = "Fixture/Clock"
        zone = self.root / "usr/share/zoneinfo" / self.timezone
        zone.parent.mkdir(parents=True); zone.write_bytes(b"synthetic zone")

    def make(self, data, keys=None):
        return S.make_plan(self.root, data, keys)

    def run_plan(self, data, **kwargs):
        return S.apply_plan(self.root, self.make(data), **kwargs)

    def test_every_section_and_key_permissions(self):
        country = chr(90) * 2
        data = (f'version=1\n[wifi "fixture"]\nssid=fixture-net\npsk=fixture-secret\ncountry={country}\n'
                '[apt]\nmirror=https://fixture.invalid/ports\n[display]\nrefresh_hz=60\n'
                f'[system]\ntimezone={self.timezone}\nsafe_mode=true\n[packages]\ninstall=python3 fixture+tool\n').encode()
        plan = self.make(data, (public_key() + " comment\n").encode())
        log = S.apply_plan(self.root, plan)
        network = self.root / plan["networks"][0]
        self.assertEqual(network.stat().st_mode & 0o777, 0o600)
        self.assertIn("psk=fixture-secret", network.read_text())
        self.assertIn("URIs: https://fixture.invalid/ports", (self.root / "etc/apt/sources.list.d/ubuntu.sources").read_text())
        self.assertEqual(os.readlink(self.root / "etc/localtime"), "../usr/share/zoneinfo/" + self.timezone)
        self.assertIn("refresh_hz=60", (self.root / "etc/modprobe.d/dby-panel.conf").read_text())
        self.assertEqual((self.root / "root/.ssh/authorized_keys").read_text(), public_key() + "\n")
        self.assertEqual((self.root / "root/.ssh").stat().st_mode & 0o777, 0o700)
        self.assertNotIn("fixture-secret", "\n".join(log))
        self.assertFalse((self.root / "run/modprobe.d/dby-safe-mode.conf").exists())

    def test_idempotence_and_deletion_restores_only_owned_files(self):
        data = b'[wifi "fixture"]\nssid=fixture-net\npsk=fixture-secret\n[apt]\nmirror=https://fixture.invalid/ports\n[display]\nrefresh_hz=120\n'
        self.run_plan(data)
        files = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.rglob("*") if p.is_file() and not p.is_symlink()}
        self.run_plan(data)
        self.assertEqual(files, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files})
        network = self.root / "etc/NetworkManager/system-connections/dby-wifi-99.nmconnection"
        network.write_bytes(b"user-managed")
        persistent = self.root / "etc/modprobe.d/dby-safe-mode.conf"; persistent.write_text(S.MANAGED + "legacy\n")
        manual = self.root / "etc/modprobe.d/dby-w09-msm-off.conf"; manual.write_bytes(b"user fallback")
        self.run_plan(b"[system]\nsafe_mode=false\n")
        self.assertFalse(persistent.exists())
        self.assertEqual(manual.read_bytes(), b"user fallback")
        self.assertEqual(network.read_bytes(), b"user-managed")
        self.assertFalse(network.with_name("dby-wifi-0.nmconnection").exists())
        self.assertFalse((self.root / "etc/modprobe.d/dby-panel.conf").exists())
        self.assertEqual((self.root / "etc/apt/sources.list.d/ubuntu.sources").read_bytes(), self.defaults)

    def test_no_network_and_no_key_file_preserves_existing_keys(self):
        key = self.root / "root/.ssh/authorized_keys"; key.parent.mkdir(parents=True); key.write_bytes(b"existing")
        S.apply_plan(self.root, self.make(b""))
        self.assertEqual(key.read_bytes(), b"existing")
        self.assertFalse((self.root / "etc/NetworkManager/system-connections").exists())

    def test_invalid_items_do_not_affect_valid_settings_or_leak(self):
        data = (b'[wifi "bad"]\nssid=fixture\npsk=short\n[apt]\nmirror=https://fixture.invalid/../bad\n'
                b'[system]\ntimezone=../../escape\nnoise\nsafe_mode=false\n[packages]\ninstall=valid --option bad;command\n'
                b'[display]\nrefresh_hz=60\n[unknown]\nsecret=fixture-secret\n')
        plan = self.make(data, b"not-a-key\nssh-ed25519 invalid\n")
        self.assertEqual(plan["packages"], ["valid"])
        self.assertFalse(plan["networks"])
        self.assertIsNone(plan["timezone"])
        logs = S.apply_plan(self.root, plan)
        self.assertIn("refresh_hz=60", (self.root / "etc/modprobe.d/dby-panel.conf").read_text())
        self.assertNotIn("fixture-secret", "\n".join(logs))
        self.assertNotIn("bad;command", "\n".join(logs))

    def test_key_wire_format_options_and_invalid_base64(self):
        self.assertEqual(S.public_key(public_key() + " comment"), public_key() + "\n")
        for line in ('command="bad" ' + public_key(), "ssh-rsa invalid", "ssh-ed25519 AAAAAA==", "PRIVATE KEY"):
            self.assertIsNone(S.public_key(line))

    def test_invalid_global_input_does_not_remove_previous_settings(self):
        self.run_plan(b"[display]\nrefresh_hz=60\n")
        path = self.root / "etc/modprobe.d/dby-panel.conf"; before = path.read_bytes()
        for data in (b"x" * 65537, b"\0", b"\x01", b"\x7f"):
            self.run_plan(data)
            self.assertEqual(path.read_bytes(), before)
        S.apply_plan(self.root, self.make(b"\0", public_key().encode()))
        self.assertTrue((self.root / "root/.ssh/authorized_keys").is_file())

    def test_seed_cannot_be_a_write_destination_via_root_link(self):
        p1 = self.root / "run/dby-p1/dby-seed"; p1.mkdir(parents=True)
        (self.root / "root").symlink_to("/run/dby-p1/dby-seed")
        logs = S.apply_plan(self.root, self.make(b"", public_key().encode()))
        self.assertFalse((p1 / ".ssh").exists())
        self.assertTrue(logs)

    def test_removed_managed_network_requests_disconnect_and_reload(self):
        self.run_plan(b'[wifi "fixture"]\nssid=fixture-net\npsk=fixture-secret\n')
        calls = []
        self.run_plan(b"", effects=True, execute=lambda argv, **kwargs: calls.append(argv) or True)
        self.assertEqual(calls, [["nmcli", "connection", "down", "id", "dby-wifi-0"], ["nmcli", "connection", "reload"]])

    def test_packages_once_network_and_bounded_retries(self):
        calls = []
        def succeed(argv, **kwargs):
            calls.append(argv); return True
        data = b"[packages]\ninstall=fixture-package\n"
        self.run_plan(data, effects=True, execute=succeed, connected=lambda mirror: False)
        self.assertEqual(calls, [])
        self.run_plan(data, effects=True, execute=succeed, connected=lambda mirror: True)
        self.run_plan(data, effects=True, execute=succeed, connected=lambda mirror: True)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], ["apt-get", "update"])
        self.assertIn("--", calls[1])
        failed = []
        def fail(argv, **kwargs):
            failed.append(argv); return False
        data = b"[packages]\ninstall=other-fixture\n"
        for _ in range(6):
            self.run_plan(data, effects=True, execute=fail, connected=lambda mirror: True)
        self.assertEqual(len(failed), 3)

    def test_cli_full_flow_exit_zero_no_seed_writes_or_secret_logs(self):
        (self.seed / "seed.conf").write_bytes(b'[wifi "fixture"]\nssid=fixture-net\npsk=fixture-secret\nnoise\n[system]\nsafe_mode=true\n')
        before = (self.seed / "seed.conf").read_bytes()
        result = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "--root", str(self.root), "--seed-dir", str(self.seed)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.seed / "seed.conf").read_bytes(), before)
        self.assertNotIn(b"fixture-secret", result.stdout + result.stderr + (self.root / "var/log/dby-seed.log").read_bytes())
        (self.seed / "seed.conf").write_bytes(b"\0")
        result = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "--root", str(self.root), "--seed-dir", str(self.seed)], capture_output=True)
        self.assertEqual(result.returncode, 0)

    def test_c_and_python_safe_mode_scanners_agree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "wrapper.c"
            source.write_text('#define _GNU_SOURCE\n#include "safe-mode.h"\nint parse(const char*p,size_t n){return safe_mode_parse(p,n);}\n'
                              'int apply(const char*p,const char*d){return safe_mode_apply(p,d);}\n')
            library = root / "parser.so"
            subprocess.run(["gcc", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror", "-I", str(TOOL / "initramfs"),
                            str(source), "-o", str(library)], check=True, capture_output=True)
            native = ctypes.CDLL(str(library)); native.parse.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
            corpus = [b"", b"noise\n[system]\nsafe_mode=true\nbroken", b"[SYSTEM]\nSAFE_MODE=TRUE\nsafe_mode=false",
                      b"[system]\nsafe_mode=false\nsafe_mode=true", b"[system]\n[broken\nsafe_mode=true",
                      b"[other]\nsafe_mode=true", b"[system]\nsafe_mode=true\n\xff", b"[system]\nsafe_mode=true\0",
                      b"[system]\nsafe_mode=true\n\x7f", b"[ system ]\r\n SAFE_MODE = True \r\n", b"x" * 65537]
            for data in corpus:
                self.assertEqual(bool(native.parse(data, len(data))), S.safe_mode(data), repr(data[:80]))


if __name__ == "__main__":
    unittest.main()
