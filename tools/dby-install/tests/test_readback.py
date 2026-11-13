# SPDX-License-Identifier: GPL-2.0-only
"""Read-back policy with fake SD operations and real ordinary image files."""

from contextlib import ExitStack, contextmanager, nullcontext
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from dbyinstall import core, image, privileged
from dbyinstall.common import BundleError, FSTAB, MIN_P2_SIZE, MIN_SIZE, root_sectors
from dbyinstall.targets import ImageFileTarget
from test_install import bundle

NOTICE = 'Read-back of p2 skipped (--no-readback); run "dby-install verify" later to check the card.'


class ReadbackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bundle = bundle(self.root / "bundle", include_test=True)

    def cli(self, *args, data=None):
        return subprocess.run([sys.executable, "-I", "-B", str(TOOL / "dby-install"), *map(str, args)],
                              input=data, capture_output=True)

    def test_preview_and_dry_run_describe_only_the_optional_p2_skip(self):
        for skip in (False, True):
            path = self.root / ("skip.img" if skip else "default.img")
            target = ImageFileTarget(path, MIN_SIZE)
            preview = core.plan(target, self.bundle, image=True, no_readback=skip)[0]
            self.assertEqual(preview["readback"], {"p1": "full", "p2": "metadata" if skip else "full"})
            self.assertEqual(NOTICE in json.dumps(preview).replace('\\"', '"'), skip)
            result = self.cli("install", "--image-file", path, "--image-size", "8G", "--bundle", self.bundle,
                              "--dry-run", *(["--no-readback"] if skip else []))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), preview)
            self.assertFalse(path.exists())

    def test_verify_and_info_reject_no_readback_as_usage_error_before_target_access(self):
        for command in ("verify", "info"):
            result = self.cli(command, "--image-file", self.root / "absent.img", "--no-readback")
            self.assertEqual(result.returncode, 2)
            self.assertIn(b"--no-readback", result.stderr)

    @contextmanager
    def fake_sd(self, directory):
        directory.mkdir()
        p1, p2 = directory / "p1", directory / "p2"
        p1.mkdir(); (p2 / "etc").mkdir(parents=True)
        (p2 / "etc/fstab").write_bytes(FSTAB)
        (p2 / "etc/dby-rootfs-kind").write_bytes(b"full\n")
        identity = {"size": MIN_SIZE, "rdev": 17}
        target = SimpleNamespace(path=directory / "synthetic-card", validate=Mock(return_value=identity), confirm=Mock())
        events, output = [], []
        state = {"readonly": False}

        def run(argv, **kwargs):
            if argv[0] == "sync":
                events.append("sync")
            return b""

        @contextmanager
        def mounts(device, work, *, readonly=False):
            state["readonly"] = readonly
            events.append("mount-ro" if readonly else "mount-rw")
            yield p1, p2
            events.append("unmount-ro" if readonly else "unmount-rw")

        def p1_check(*args):
            events.append("p1-ro" if state["readonly"] else "p1-rw")
            return core.verify_p1(*args)

        def p2_check(*args, **kwargs):
            events.append("p2-ro" if state["readonly"] else "p2-rw")

        original_open, original_fstat, original_close = privileged.os.open, privileged.os.fstat, privileged.os.close
        fake_fd = 999999
        with ExitStack() as stack:
            replacements = {"require_root": Mock(), "dependencies": Mock(), "run": Mock(side_effect=run),
                "workspace": lambda: nullcontext(directory), "mounts": mounts,
                "geometry": Mock(side_effect=lambda *a: events.append("geometry")),
                "reread_partitions": Mock(side_effect=lambda *a, **k: (events.append("reread"), root_sectors(MIN_SIZE))[1]),
                "check_table": Mock(side_effect=lambda *a, **k: (events.append("table"), root_sectors(MIN_SIZE))[1]),
                "verify_p1": Mock(side_effect=p1_check), "verify_root": Mock(side_effect=p2_check)}
            for name, replacement in replacements.items():
                stack.enter_context(patch.object(privileged, name, replacement))
            stack.enter_context(patch.object(privileged.os, "open", side_effect=lambda path, *a, **k:
                fake_fd if Path(path) == target.path else original_open(path, *a, **k)))
            stack.enter_context(patch.object(privileged.os, "fstat", side_effect=lambda fd:
                SimpleNamespace(st_rdev=17) if fd == fake_fd else original_fstat(fd)))
            stack.enter_context(patch.object(privileged.os, "close", side_effect=lambda fd:
                None if fd == fake_fd else original_close(fd)))
            lock = stack.enter_context(patch.object(privileged.fcntl, "flock"))
            yield SimpleNamespace(target=target, p1=p1, p2=p2, events=events, output=output,
                                  mocks=replacements, lock=lock)

    def test_sd_default_and_skip_keep_prewrite_checks_sync_unmount_and_p1_readback(self):
        for skip in (False, True):
            with self.subTest(skip=skip), self.fake_sd(self.root / str(skip)) as fake:
                privileged.install(fake.target, self.bundle, ask=lambda prompt: "synthetic", emit=fake.output.append,
                                   no_readback=skip)
                fake.target.confirm.assert_called_once()
                fake.lock.assert_called_once()
                self.assertGreaterEqual(fake.target.validate.call_count, 3)
                self.assertEqual(fake.mocks["verify_root"].call_count, 1 if skip else 2)
                self.assertEqual(fake.events.count("p1-rw"), 1)
                self.assertEqual(fake.events.count("p1-ro"), 1)
                self.assertEqual(fake.events.count("sync"), 2)
                self.assertLess(fake.events.index("sync"), fake.events.index("unmount-rw"))
                self.assertLess(fake.events.index("unmount-rw"), fake.events.index("mount-ro"))
                self.assertIn("p2-rw", fake.events)
                self.assertEqual("p2-ro" in fake.events, not skip)
                if skip:
                    self.assertEqual(fake.mocks["reread_partitions"].call_count, 2)
                    self.assertEqual(fake.mocks["reread_partitions"].call_args.kwargs["expected_p2"], root_sectors(MIN_SIZE))
                    self.assertGreater(fake.events.index("reread", fake.events.index("unmount-rw")), fake.events.index("unmount-rw"))
                    self.assertEqual(fake.output[-1], NOTICE)
                    self.assertIn(NOTICE, fake.output)
                else:
                    self.assertIn("table", fake.events)
                    self.assertNotIn(NOTICE, fake.output)

    def test_metadata_readback_still_rejects_p1_kind_and_fstab_errors(self):
        cases = (("p1", "E_INSTALL_HASH"), ("kind", "E_INSTALL_KIND"), ("fstab", "E_INSTALL_FSTAB"),
                 ("missing-kind", "E_INSTALL_KIND"), ("linked-kind", "E_INSTALL_KIND"),
                 ("missing-fstab", "E_INSTALL_FSTAB"), ("linked-fstab", "E_INSTALL_FSTAB"))
        for kind, code in cases:
            with self.subTest(kind=kind), self.fake_sd(self.root / kind) as fake:
                core.populate_p1(fake.p1, self.bundle)
                if kind == "p1":
                    (fake.p1 / "UBOOT/Image").write_bytes(b"tampered")
                elif kind == "kind":
                    (fake.p2 / "etc/dby-rootfs-kind").write_bytes(b"test\n")
                elif kind in ("missing-kind", "linked-kind"):
                    path = fake.p2 / "etc/dby-rootfs-kind"; path.unlink()
                    if kind == "linked-kind":
                        (fake.p2 / "etc/other").write_bytes(b"full\n")
                        path.symlink_to("other")
                else:
                    path = fake.p2 / "etc/fstab"
                    if kind == "fstab":
                        path.write_bytes(b"wrong\n")
                    else:
                        path.unlink()
                        if kind == "linked-fstab":
                            (fake.p2 / "etc/other").write_bytes(FSTAB)
                            path.symlink_to("other")
                with self.assertRaisesRegex(BundleError, code):
                    privileged.readback_metadata(fake.target, self.bundle, expected_kind="full", expected_p2=root_sectors(MIN_SIZE))
                fake.mocks["verify_root"].assert_not_called()
                if kind == "missing-fstab":
                    self.assertFalse((fake.p2 / "etc/fstab").exists())

    def test_metadata_readback_keeps_partition_and_filesystem_gates(self):
        for name, code in (("reread_partitions", "E_INSTALL_TABLE"), ("reread_partitions", "E_INSTALL_PARTITION"),
                           ("geometry", "E_INSTALL_LABEL"), ("geometry", "E_INSTALL_GEOMETRY")):
            with self.subTest(name=name, code=code), self.fake_sd(self.root / code) as fake:
                fake.mocks[name].side_effect = BundleError(code, "Synthetic rejection.")
                with self.assertRaisesRegex(BundleError, code):
                    privileged.readback_metadata(fake.target, self.bundle, expected_kind="full", expected_p2=root_sectors(MIN_SIZE))
                self.assertNotIn("mount-ro", fake.events)

    def test_skip_does_not_bypass_confirmation_or_prewrite_identity(self):
        for code in ("E_TARGET_CONFIRM", "E_TARGET_CHANGED"):
            with self.subTest(code=code), self.fake_sd(self.root / code) as fake:
                if code == "E_TARGET_CONFIRM":
                    fake.target.confirm.side_effect = BundleError(code, "Synthetic rejection.")
                else:
                    identity = fake.target.validate.return_value
                    fake.target.validate.side_effect = [identity, {**identity, "rdev": 18}]
                with self.assertRaisesRegex(BundleError, code):
                    privileged.install(fake.target, self.bundle, no_readback=True, emit=fake.output.append)
                fake.mocks["run"].assert_not_called()
                self.assertEqual(fake.output.count(NOTICE), 1)

    def test_image_skip_omits_p2_readback_hash_and_later_verify_remains_full(self):
        for minimal in (False, True):
            target = ImageFileTarget(self.root / (str(minimal) + ".img"), MIN_SIZE)
            output = []
            with self.subTest(minimal=minimal), patch.object(image, "verify_root", wraps=image.verify_root) as root_check, \
                    patch.object(image, "verify_p1", wraps=image.verify_p1) as boot_check:
                image.install(target, self.bundle, ask=lambda prompt: str(target.path), no_readback=True, emit=output.append,
                              minimal=minimal, p2_size=MIN_P2_SIZE if minimal else None)
                self.assertEqual(root_check.call_count, 1)
                self.assertEqual(boot_check.call_count, 1)
                image.verify(target, self.bundle)
                self.assertEqual(root_check.call_count, 2)
                self.assertEqual(boot_check.call_count, 2)
            self.assertEqual(output[-1], NOTICE)

    def test_cli_skip_success_message_is_explicit_and_verify_still_runs(self):
        target = self.root / "cli.img"
        result = self.cli("install", "--image-file", target, "--image-size", "8G", "--bundle", self.bundle,
                          "--no-readback", data=(str(target) + "\n").encode())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(result.stdout.decode().count(NOTICE), 2)
        self.assertIn(b"OK: install completed; p2 file read-back skipped.", result.stdout)
        self.assertNotIn(b"install and read-back verification completed", result.stdout)
        verified = self.cli("verify", "--image-file", target, "--bundle", self.bundle)
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertIn(b"installed files match the bundle", verified.stdout)


if __name__ == "__main__":
    unittest.main()
