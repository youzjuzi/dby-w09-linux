# SPDX-License-Identifier: GPL-2.0-only
"""Command privacy and partition rereads, with no block-device access."""

from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dbyinstall import archive, common, privileged


class CommandDiagnosticsTests(unittest.TestCase):
    def test_failure_reports_basename_exit_and_category_without_private_output(self):
        argv = ["/home/private-user/bin/udevadm", "settle", "--timeout=30", "private-serial"]
        result = subprocess.CompletedProcess(argv, 1, b"private-seed", b"private-stderr")
        with patch.object(common.subprocess, "run", return_value=result):
            with self.assertRaises(common.BundleError) as caught:
                common.run(argv, data=b"private-input")
        self.assertEqual(str(caught.exception), "E_INSTALL_COMMAND: udevadm exited 1 (timeout).")
        for secret in ("private-user", "private-serial", "private-seed", "private-stderr", "private-input"):
            self.assertNotIn(secret, str(caught.exception))

    def test_other_failures_keep_the_error_code_and_use_fixed_categories(self):
        for argv, code, category in ((["mkfs.ext4"], 7, "command-failed"),
                                     (["udevadm", "trigger"], 1, "command-failed"),
                                     (["blockdev", "--rereadpt", "private-device"], 1, "reread-failed"),
                                     (["tar"], -15, "signal")):
            with self.subTest(argv=argv), patch.object(common.subprocess, "run", return_value=
                    subprocess.CompletedProcess(argv, code, b"", b"private-stderr")):
                with self.assertRaises(common.BundleError) as caught:
                    common.run(argv)
                self.assertEqual(str(caught.exception), f"E_INSTALL_COMMAND: {argv[0]} exited {code} ({category}).")

    def test_start_failure_hides_oserror_paths_and_marks_absent_exit_status(self):
        with patch.object(common.subprocess, "run", side_effect=OSError("/home/private-user/private-seed")):
            with self.assertRaises(common.BundleError) as caught:
                common.run(["/home/private-user/bin/udevadm", "private-serial"])
        self.assertEqual(str(caught.exception), "E_INSTALL_COMMAND: udevadm could not start (not-started; no exit code).")

    def test_command_names_cannot_inject_control_characters_into_diagnostics(self):
        with patch.object(common.subprocess, "run", side_effect=OSError("private-stderr")):
            with self.assertRaises(common.BundleError) as caught:
                common.run(["/private/path/unsafe\nname"])
        self.assertEqual(str(caught.exception), "E_INSTALL_COMMAND: command could not start (not-started; no exit code).")

    def test_trace_reports_every_command_status_without_arguments_or_output(self):
        stream = io.StringIO()
        results = [subprocess.CompletedProcess([], 0, b"private-stdout", b"private-stderr"),
                   subprocess.CompletedProcess([], 7, b"private-stdout", b"private-stderr")]
        with patch.dict(common.os.environ, {"DBY_TRACE": "1"}), redirect_stderr(stream), \
                patch.object(common.subprocess, "run", side_effect=results):
            self.assertEqual(common.run(["/home/private-user/bin/lsblk", "private-serial"]), b"private-stdout")
            with self.assertRaises(common.BundleError):
                common.run(["tar", "private-seed"])
        self.assertEqual(stream.getvalue(), "[trace] lsblk -> exit 0\n[trace] tar -> exit 7\n")

    def test_trace_reports_start_failure_without_oserror_text(self):
        stream = io.StringIO()
        with patch.dict(common.os.environ, {"DBY_TRACE": "1"}), redirect_stderr(stream), \
                patch.object(common.subprocess, "run", side_effect=OSError("private-stderr")):
            with self.assertRaises(common.BundleError):
                common.run(["tar", "private-seed"])
        self.assertEqual(stream.getvalue(), "[trace] tar -> not started\n")

    def test_trace_is_opt_in(self):
        stream = io.StringIO()
        with patch.dict(common.os.environ, {"DBY_TRACE": "0"}), redirect_stderr(stream), \
                patch.object(common.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"ok", b"")):
            self.assertEqual(common.run(["lsblk"]), b"ok")
        self.assertEqual(stream.getvalue(), "")


class ArchiveTraceTests(unittest.TestCase):
    def test_streaming_decoder_reports_success_and_failure_without_archive_paths(self):
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w") as tar:
            tar.addfile(tarfile.TarInfo("fixture"))
        for code in (0, 1):
            stream = io.StringIO()
            process = SimpleNamespace(stdout=io.BytesIO(payload.getvalue()), returncode=code,
                                      wait=Mock(return_value=code), poll=Mock(return_value=code))
            with self.subTest(code=code), patch.dict(common.os.environ, {"DBY_TRACE": "1"}), \
                    redirect_stderr(stream), patch.object(archive.subprocess, "Popen", return_value=process):
                if code:
                    with self.assertRaisesRegex(common.BundleError, "E_ROOTFS_READ"):
                        archive.inspect_archive(Path("/home/private-user/private-seed.tar.zst"))
                else:
                    self.assertIn("fixture", archive.inspect_archive(Path("/home/private-user/private-seed.tar.zst"))[0])
            self.assertEqual(stream.getvalue(), f"[trace] zstd -> exit {code}\n")

    def test_streaming_decoder_start_failure_keeps_archive_error_code(self):
        stream = io.StringIO()
        with patch.dict(common.os.environ, {"DBY_TRACE": "1"}), redirect_stderr(stream), \
                patch.object(archive.subprocess, "Popen", side_effect=OSError("private-stderr")):
            with self.assertRaisesRegex(common.BundleError, "E_ROOTFS_READ"):
                archive.inspect_archive(Path("/home/private-user/private-seed.tar.zst"))
        self.assertEqual(stream.getvalue(), "[trace] zstd -> not started\n")


class FakeCommands:
    def __init__(self, *, settle_code=1, reread_failures=0, wrong_table=False, wrong_node=False):
        self.calls = []
        self.settle_code, self.reread_failures = settle_code, reread_failures
        self.wrong_table, self.wrong_node = wrong_table, wrong_node

    def __call__(self, argv):
        argv = list(map(str, argv))
        self.calls.append(argv)
        if argv[:2] == ["udevadm", "settle"]:
            if self.settle_code:
                raise common.CommandError(argv, self.settle_code)
            return b""
        if argv[:2] == ["blockdev", "--rereadpt"]:
            if self.reread_failures:
                self.reread_failures -= 1
                raise common.CommandError(argv, 1)
            return b""
        if argv[:2] == ["sfdisk", "--json"]:
            return json.dumps({"partitiontable": {"label": "gpt" if self.wrong_table else "dos", "sectorsize": 512,
                "partitions": [{"start": common.P1_START, "size": common.P1_SECTORS, "type": "c"},
                               {"start": common.P2_START, "size": common.DEFAULT_P2_SIZE // 512, "type": "83"}]}}).encode()
        if argv[:2] == ["blockdev", "--getsz"]:
            sectors = common.P1_SECTORS if argv[2].endswith("1") else common.DEFAULT_P2_SIZE // 512
            return str(sectors - int(self.wrong_node)).encode()
        raise AssertionError("Unexpected command in partition-reread test")


class PartitionRereadTests(unittest.TestCase):
    def run_reread(self, execute, waits, notices):
        # Only synthetic partition nodes exist in this test; no host /dev access.
        with patch.object(privileged, "no_links"), \
                patch.object(Path, "stat", return_value=SimpleNamespace(st_mode=stat.S_IFBLK)):
            return privileged.reread_partitions(Path("/fixture/card"), common.MIN_SIZE,
                expected_p2=common.DEFAULT_P2_SIZE // 512, execute=execute, wait=waits.append, emit=notices.append)

    def test_settle_timeouts_continue_through_reread_table_and_node_checks(self):
        execute, waits, notices = FakeCommands(), [], []
        self.assertEqual(self.run_reread(execute, waits, notices), common.DEFAULT_P2_SIZE // 512)
        self.assertEqual(execute.calls, [["udevadm", "settle", "--timeout=30"],
            ["blockdev", "--rereadpt", "/fixture/card"], ["udevadm", "settle", "--timeout=30"],
            ["sfdisk", "--json", "/fixture/card"], ["blockdev", "--getsz", "/fixture/card1"],
            ["blockdev", "--getsz", "/fixture/card2"]])
        self.assertEqual(waits, [])
        self.assertEqual(len(notices), 1)
        self.assertIn("timeout", notices[0])

    def test_repeated_busy_rereads_stop_with_original_code_and_bounded_waits(self):
        execute, waits, notices = FakeCommands(reread_failures=6), [], []
        with self.assertRaisesRegex(common.BundleError, "E_INSTALL_BUSY"):
            self.run_reread(execute, waits, notices)
        self.assertEqual(waits, [1, 2, 3, 4, 5])
        self.assertEqual(sum(waits), 15)
        self.assertEqual(sum(argv[:2] == ["blockdev", "--rereadpt"] for argv in execute.calls), 6)
        self.assertTrue(all(argv[0] in ("udevadm", "blockdev") for argv in execute.calls))
        self.assertEqual(len(notices), 1)

    def test_retry_can_recover_before_the_limit(self):
        execute, waits, notices = FakeCommands(settle_code=0, reread_failures=2), [], []
        self.run_reread(execute, waits, notices)
        self.assertEqual(waits, [1, 2])
        self.assertEqual(sum(argv[:2] == ["blockdev", "--rereadpt"] for argv in execute.calls), 3)
        self.assertEqual(notices, [])

    def test_non_timeout_settle_failure_is_still_fatal(self):
        execute, waits, notices = FakeCommands(settle_code=2), [], []
        with self.assertRaisesRegex(common.BundleError, "E_INSTALL_COMMAND: udevadm exited 2"):
            self.run_reread(execute, waits, notices)
        self.assertEqual(execute.calls, [["udevadm", "settle", "--timeout=30"]])
        self.assertEqual((waits, notices), ([], []))

    def test_timeout_does_not_bypass_partition_table_or_node_size_gates(self):
        for changes, code in (({"wrong_table": True}, "E_INSTALL_TABLE"),
                              ({"wrong_node": True}, "E_INSTALL_PARTITION")):
            with self.subTest(code=code), self.assertRaisesRegex(common.BundleError, code):
                self.run_reread(FakeCommands(**changes), [], [])


if __name__ == "__main__":
    unittest.main()
