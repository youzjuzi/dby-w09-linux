# SPDX-License-Identifier: GPL-2.0-only
"""Build-only network and logging tests; no apt, chroot, mounts or root."""

from contextlib import redirect_stderr
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from dbybundle import BundleError
from dbybundle import rootfs
from dbybundle import rootfs_privileged

MIRROR = "https://mirror.example/ubuntu-ports"
ORIGINAL = (b"# official source\r\nTypes: deb\r\nURIs: http://ports.ubuntu.com/ubuntu-ports\r\n"
            b" http://ports.ubuntu.com/extra\r\nSuites: fixture\r\n\r\n"
            b"Types: deb\r\nURIs: http://ports.ubuntu.com/ubuntu-ports\r\nSuites: fixture-security\r\n")


class BuildNetworkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "etc/apt/sources.list.d/ubuntu.sources"
        self.source.parent.mkdir(parents=True); self.source.write_bytes(ORIGINAL); self.source.chmod(0o640)

    def helpers(self):
        self.assertIsNotNone(importlib.util.find_spec("dbybundle.rootfs_build"), "Build-only helpers must exist.")
        from dbybundle import rootfs_build
        return rootfs_build

    def test_plan_has_mirror_and_bounded_apt_without_changing_recipe(self):
        plain = rootfs.make_plan("full", None, self.root / "output")
        plan = rootfs.make_plan("full", None, self.root / "output", mirror=MIRROR)
        self.assertEqual(plan["mirror"], MIRROR)
        self.assertEqual(plan["recipe"], plain["recipe"])
        stages = {stage["stage"]: stage for stage in plan["stages"]}
        for stage in ("purge", "apt-index", "add"):
            argv = stages[stage]["argv"]
            for option in ("Acquire::http::Timeout=30", "Acquire::https::Timeout=30", "Acquire::Retries=3"):
                index = argv.index(option)
                self.assertEqual(argv[index - 1], "-o")
        self.assertLess(list(stages).index("restore-build-mirror"), list(stages).index("pack"))
        self.assertFalse((self.root / "output").exists())

    def test_mirror_validation_agrees_with_seed(self):
        helper = self.helpers()
        seed = runpy.run_path(str(TOOL / "rootfs/overlay/usr/lib/dby/dby-seed-apply"))
        values = [MIRROR, MIRROR + "/", "http://mirror.example:8080/ports", "https://[::1]/ports",
                  "file:///tmp/repo", "ftp://mirror.example", "https://user:secret@mirror.example/",
                  "https://mirror.example/a/../b", "https://mirror.example/%2e%2e/", "https://mirror.example/?secret=1",
                  "https://mirror.example/#fragment", "https://mirror.example:0/", "https://mirror.example:65536/",
                  "https://mirror.example/\\escape", "https://mirror.example/%0aInjected", "https://mirror.example/\n", ""]
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(helper.mirror_url(value), seed["mirror_url"](value))
                if seed["mirror_url"](value) is None:
                    with self.assertRaisesRegex(BundleError, "E_ROOTFS_MIRROR"):
                        rootfs.make_plan("full", None, self.root / "invalid", mirror=value)

    def test_temporary_mirror_restores_bytes_metadata_and_pack_excludes_url(self):
        helper = self.helpers(); before = self.source.stat()
        with helper.temporary_mirror(self.source, MIRROR):
            current = self.source.read_bytes()
            self.assertEqual(current.count(MIRROR.encode()), 2)
            self.assertNotIn(b"http://ports.ubuntu.com/extra", current)
        self.assertEqual(self.source.read_bytes(), ORIGINAL)
        after = self.source.stat()
        self.assertEqual((after.st_mode, after.st_uid, after.st_gid, after.st_ino),
                         (before.st_mode, before.st_uid, before.st_gid, before.st_ino))
        self.assertEqual(list(self.source.parent.iterdir()), [self.source])
        archive = self.root / "packed.zst"
        rootfs.pack_tree(self.root / "etc", archive, 123)
        plain = subprocess.run(["zstd", "-q", "-d", "-c", str(archive)], capture_output=True, check=True).stdout
        self.assertNotIn(MIRROR.encode(), plain)
        with tarfile.open(fileobj=io.BytesIO(plain)) as content:
            self.assertEqual(content.extractfile("./apt/sources.list.d/ubuntu.sources").read(), ORIGINAL)

    def test_temporary_mirror_restores_on_failure_and_ctrl_c(self):
        helper = self.helpers()
        for failure in (RuntimeError, KeyboardInterrupt):
            with self.subTest(failure=failure), self.assertRaises(failure):
                with helper.temporary_mirror(self.source, MIRROR):
                    raise failure()
            self.assertEqual(self.source.read_bytes(), ORIGINAL)

    def test_atomic_source_replacement_during_apt_is_also_restored(self):
        helper = self.helpers(); before = self.source.stat()
        with helper.temporary_mirror(self.source, MIRROR):
            replacement = self.source.with_suffix(".replacement")
            replacement.write_text("URIs: " + MIRROR + "\n")
            replacement.replace(self.source)
        self.assertEqual(self.source.read_bytes(), ORIGINAL)
        after = self.source.stat()
        self.assertEqual((after.st_mode, after.st_uid, after.st_gid), (before.st_mode, before.st_uid, before.st_gid))

    def test_missing_uri_or_symlink_fails_without_mutating_source(self):
        helper = self.helpers()
        self.source.write_bytes(b"Types: deb\nSuites: fixture\n")
        before = self.source.read_bytes()
        with self.assertRaisesRegex(BundleError, "E_ROOTFS_MIRROR"):
            with helper.temporary_mirror(self.source, MIRROR):
                self.fail("Missing URI field must fail before entering apt.")
        self.assertEqual(self.source.read_bytes(), before)
        self.source.unlink(); self.source.symlink_to("original.sources")
        with self.assertRaisesRegex(BundleError, "E_ROOTFS_MIRROR"):
            with helper.temporary_mirror(self.source, MIRROR):
                self.fail("Linked source must not be modified.")
        with helper.temporary_mirror(self.root / "absent", None):
            pass

    def test_cli_dry_run_exposes_mirror_and_invalid_url_is_private(self):
        for command in ([str(TOOL / "build-rootfs")], [str(TOOL / "build-bundle"), "--stage", "rootfs"]):
            result = subprocess.run([sys.executable, "-I", "-B", *command, "--kind", "full", "--output", str(self.root / "cli"),
                                     "--mirror", MIRROR, "--dry-run"], capture_output=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(json.loads(result.stdout)["mirror"], MIRROR)
            self.assertIn(b"E_ROOTFS_INPUT", result.stderr)
        result = subprocess.run([sys.executable, "-I", "-B", str(TOOL / "build-rootfs"), "--kind", "full",
                                 "--output", str(self.root / "cli"), "--mirror", "https://user:secret@mirror.example/", "--dry-run"], capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"E_ROOTFS_MIRROR", result.stderr)
        self.assertNotIn(b"secret", result.stderr)

    def test_runner_streams_before_child_exit_and_omits_identity_text(self):
        marker = self.root / "progress-seen"
        class Observer(io.StringIO):
            def write(self, value):
                if "Reading package lists" in value:
                    marker.touch()
                return super().write(value)
        script = ("import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); "
                  "print('Reading package lists... Done',flush=True); "
                  "print('nameserver secret-resolver',flush=True); "
                  "print('hostname=secret-host username=secret-user /home/secret-user',flush=True); "
                  "print('Created symlink /home/secret-user/unit -> /private-path',flush=True); "
                  "print('Get:1 https://secret:credential@host/path fixture',flush=True); "
                  "exec('for i in range(100):\\n if p.exists(): break\\n time.sleep(.01)'.replace('\\\\n','\\n')); "
                  "sys.exit(0 if p.exists() else 7)")
        output = Observer()
        with redirect_stderr(output):
            rootfs_privileged.run([sys.executable, "-I", "-B", "-c", script, str(marker)], "rootfs-apt")
        self.assertTrue(marker.exists(), "Progress must arrive before child exit.")
        text = output.getvalue()
        self.assertIn("rootfs-apt", text)
        for secret in ("secret-resolver", "secret-host", "secret-user", "credential", "/private-path"):
            self.assertNotIn(secret, text)

    def test_runner_failure_keeps_code_without_echoing_command(self):
        output = io.StringIO()
        with redirect_stderr(output), self.assertRaisesRegex(BundleError, "E_BUILD_COMMAND"):
            rootfs_privileged.run([sys.executable, "-I", "-B", "-c", "print('secret-fixture');raise SystemExit(9)"], "rootfs-apt")
        self.assertNotIn("secret-fixture", output.getvalue())
        self.assertIn("rootfs-apt", output.getvalue())

    def test_workspace_cleanup_and_retained_mount_notice(self):
        helper = self.helpers()
        for failure in (RuntimeError, KeyboardInterrupt):
            output = io.StringIO()
            with redirect_stderr(output), self.assertRaises(failure):
                with helper.workspace(self.root, lambda: set()) as work:
                    (work / "payload").write_bytes(b"fixture")
                    raise failure()
            self.assertFalse(work.exists())
            self.assertIn("no build mounts remain", output.getvalue())
        output = io.StringIO()
        with redirect_stderr(output), self.assertRaisesRegex(BundleError, "E_ROOTFS_CLEANUP"):
            with helper.workspace(self.root, lambda: {retained / "root/proc"}) as retained:
                pass
        self.assertTrue(retained.is_dir())
        self.assertIn(retained.name, output.getvalue())
        self.assertNotIn(str(self.root), output.getvalue())

    def test_cleanup_discards_build_repository_cache_before_pack(self):
        self.helpers()
        (self.root / "etc/passwd").write_text("root:x:0:0:fixture:/root:/bin/sh\n")
        cache = self.root / "var/lib/apt/lists/mirror.example_ubuntu-ports_fixture"
        cache.parent.mkdir(parents=True); cache.write_text(MIRROR)
        other = self.root / "var/cache/apt/pkgcache.bin"
        other.parent.mkdir(parents=True); other.write_text(MIRROR)
        with patch.object(rootfs_privileged.os, "chown"):
            rootfs_privileged.clean(self.root)
        archive = self.root / "cleaned.zst"
        rootfs.pack_tree(self.root / "var", archive, 123)
        plain = subprocess.run(["zstd", "-q", "-d", "-c", str(archive)], capture_output=True, check=True).stdout
        self.assertFalse(MIRROR.encode() in plain, "Build mirror URL must not enter the packed APT cache.")
        self.assertFalse(b"mirror.example" in plain, "Build mirror host must not enter archive filenames.")

    def test_fake_mount_environment_unwinds_and_restores_files_on_ctrl_c(self):
        self.helpers(); active = set()
        resolv = self.root / "etc/resolv.conf"; resolv.write_bytes(b"nameserver synthetic-resolver\n")
        policy = self.root / "usr/sbin/policy-rc.d"; policy.parent.mkdir(parents=True); policy.write_bytes(b"original policy\n")
        def fake_command(argv, stage):
            if argv[0] == "mount":
                active.add(Path(argv[-1]))
            elif argv[0] == "umount":
                active.remove(Path(argv[-1]))
            else:
                raise AssertionError("Unexpected effect in environment test")
        with patch.object(rootfs_privileged, "run", side_effect=fake_command), \
                patch.object(rootfs_privileged, "mounted", side_effect=lambda: set(active)), \
                patch.object(rootfs_privileged.os, "mknod", side_effect=lambda path, *args: Path(path).touch()), \
                patch.object(rootfs_privileged.os, "chown"):
            with self.assertRaises(KeyboardInterrupt):
                with rootfs_privileged.environment(self.root):
                    self.assertEqual(len(active), 5)
                    raise KeyboardInterrupt()
        self.assertFalse(active)
        self.assertEqual(resolv.read_bytes(), b"nameserver synthetic-resolver\n")
        self.assertEqual(policy.read_bytes(), b"original policy\n")

    def test_cleanup_normalizes_resolver_files_links_and_missing_path_idempotently(self):
        target = "../run/systemd/resolve/stub-resolv.conf"
        external = self.root / "external-resolver"; external.write_bytes(b"outside fixture\n")
        for kind in ("empty", "file", "correct-link", "dangling-link", "external-link", "absent"):
            with self.subTest(kind=kind):
                tree = self.root / kind
                (tree / "etc").mkdir(parents=True)
                (tree / "etc/passwd").write_text("root:x:0:0:fixture:/root:/bin/sh\n")
                resolv = tree / "etc/resolv.conf"
                if kind in ("empty", "file"):
                    resolv.write_bytes(b"" if kind == "empty" else b"nameserver synthetic-build-dns\n")
                elif kind != "absent":
                    resolv.symlink_to({"correct-link": target, "dangling-link": "../missing-resolver",
                                       "external-link": str(external)}[kind])
                with patch.object(rootfs_privileged.os, "chown"):
                    rootfs_privileged.clean(tree)
                    rootfs_privileged.clean(tree)
                self.assertTrue(resolv.is_symlink())
                self.assertEqual(os.readlink(resolv), target)
                self.assertEqual(external.read_bytes(), b"outside fixture\n")

    def test_environment_restore_then_clean_packs_resolver_link_without_build_dns(self):
        resolv = self.root / "etc/resolv.conf"; resolv.write_bytes(b"")
        (self.root / "etc/passwd").write_text("root:x:0:0:fixture:/root:/bin/sh\n")
        build_dns = b"nameserver synthetic-host-resolver\nsearch synthetic-build-domain\n"
        active = set()
        original_read = Path.read_bytes

        def read_bytes(path):
            return build_dns if path == Path("/etc/resolv.conf") else original_read(path)

        def fake_command(argv, stage):
            if argv[0] == "mount":
                active.add(Path(argv[-1]))
            elif argv[0] == "umount":
                active.remove(Path(argv[-1]))
            else:
                raise AssertionError("Unexpected effect in resolver environment test")

        with patch.object(Path, "read_bytes", read_bytes), \
                patch.object(rootfs_privileged, "run", side_effect=fake_command), \
                patch.object(rootfs_privileged, "mounted", side_effect=lambda: set(active)), \
                patch.object(rootfs_privileged.os, "mknod", side_effect=lambda path, *args: Path(path).touch()), \
                patch.object(rootfs_privileged.os, "chown"):
            with rootfs_privileged.environment(self.root):
                self.assertEqual(resolv.read_bytes(), build_dns)
                self.assertFalse(resolv.is_symlink())
            self.assertFalse(active)
            self.assertEqual(resolv.read_bytes(), b"")
            rootfs_privileged.clean(self.root)
        self.assertTrue(resolv.is_symlink())
        self.assertEqual(os.readlink(resolv), "../run/systemd/resolve/stub-resolv.conf")
        # Pack real ordinary files after the fake build environment has unwound.
        archive = self.root.parent / (self.root.name + "-resolved.tar.zst")
        self.addCleanup(archive.unlink, missing_ok=True)
        rootfs.pack_tree(self.root, archive, 123)
        plain = subprocess.run(["zstd", "-q", "-d", "-c", archive], capture_output=True, check=True).stdout
        self.assertNotIn(b"synthetic-host-resolver", plain)
        self.assertNotIn(b"synthetic-build-domain", plain)
        with tarfile.open(fileobj=io.BytesIO(plain)) as content:
            member = content.getmember("./etc/resolv.conf")
            self.assertTrue(member.issym())
            self.assertEqual(member.linkname, "../run/systemd/resolve/stub-resolv.conf")
            self.assertEqual(member.size, 0)

    def test_interrupted_runner_reaps_its_child(self):
        self.helpers()
        pidfile = self.root / "child.pid"
        child_code = "import os,pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); print('Reading package lists... Done',flush=True); time.sleep(30)"
        worker_code = ("import sys; sys.path.insert(0,sys.argv[1]); from dbybundle.rootfs_privileged import run\n"
                       "try: run([sys.executable,'-I','-B','-c',sys.argv[2],sys.argv[3]],'rootfs-apt')\n"
                       "except KeyboardInterrupt: raise SystemExit(130)\n")
        worker = subprocess.Popen([sys.executable, "-I", "-B", "-c", worker_code, str(TOOL), child_code, str(pidfile)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        child_pid = None
        try:
            while line := worker.stderr.readline():
                if b"Reading package lists" in line:
                    break
            self.assertIn(b"Reading package lists", line)
            child_pid = int(pidfile.read_text())
            worker.send_signal(signal.SIGINT)
            worker.communicate(timeout=10)
            self.assertEqual(worker.returncode, 130)
            with self.assertRaises(ProcessLookupError):
                os.kill(child_pid, 0)
        finally:
            if worker.poll() is None:
                worker.kill(); worker.communicate()
            if child_pid:
                try:
                    os.kill(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_cli_interrupt_returns_130_without_traceback(self):
        output = io.StringIO()
        with patch.object(rootfs, "make_plan", return_value={}), \
                patch.object(rootfs_privileged, "execute", side_effect=KeyboardInterrupt), redirect_stderr(output):
            status = rootfs.main(["--kind", "full", "--output", str(self.root / "interrupt")])
        self.assertEqual(status, 130)
        self.assertIn("E_ROOTFS_INTERRUPTED", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())

    def test_unreadable_mount_inventory_retains_workspace_with_notice(self):
        helper = self.helpers(); output = io.StringIO()
        def unavailable():
            raise OSError("synthetic private-path")
        with redirect_stderr(output), self.assertRaisesRegex(BundleError, "E_ROOTFS_CLEANUP"):
            with helper.workspace(self.root, unavailable) as retained:
                pass
        self.assertTrue(retained.exists())
        self.assertIn(retained.name, output.getvalue())
        self.assertNotIn("private-path", output.getvalue())

    def test_binary_stdout_and_tool_paths_never_enter_progress(self):
        helper = self.helpers(); output = io.StringIO(); payload = self.root / "layer"
        with redirect_stderr(output), payload.open("wb") as stream:
            helper.run([sys.executable, "-I", "-B", "-c", "import sys;sys.stdout.buffer.write(b'opaque-binary-fixture');print('ERROR: secret-path',file=sys.stderr)"],
                       "rootfs-iso", stdout=stream)
        self.assertEqual(payload.read_bytes(), b"opaque-binary-fixture")
        self.assertIn("diagnostic", output.getvalue())
        self.assertNotIn("opaque-binary", output.getvalue())
        self.assertNotIn("secret-path", output.getvalue())
        for stage, line, expected in [("rootfs-extract", "tar: Read checkpoint 10000", "10000"),
                                      ("rootfs-enable", "Created symlink /home/fixture-user/unit -> /private-unit", "Created symlink")]:
            output = io.StringIO()
            with redirect_stderr(output):
                helper.run([sys.executable, "-I", "-B", "-c", "import sys;print(sys.argv[1],flush=True)", line], stage)
            self.assertIn(expected, output.getvalue())
            self.assertNotIn("fixture-user", output.getvalue())
            self.assertNotIn("private-unit", output.getvalue())


if __name__ == "__main__":
    unittest.main()
