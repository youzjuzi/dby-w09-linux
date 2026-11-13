# SPDX-License-Identifier: GPL-2.0-only
"""Compile and exercise the exact C parser and writer used by initramfs."""

import ctypes
from pathlib import Path
import subprocess
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]


class SafeModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name)
        source = cls.root / "wrapper.c"
        source.write_text('#define _GNU_SOURCE\n#include "safe-mode.h"\n'
                          'int parse(const char *p, size_t n) { return safe_mode_parse(p,n); }\n'
                          'int apply(const char *p, const char *d) { return safe_mode_apply(p,d); }\n')
        library = cls.root / "parser.so"
        subprocess.run(["gcc", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror", "-I", str(TOOL / "initramfs"),
                        str(source), "-o", str(library)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        cls.library = ctypes.CDLL(str(library))
        cls.library.parse.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
        cls.library.apply.argtypes = [ctypes.c_char_p, ctypes.c_char_p]

    def parse(self, value):
        return self.library.parse(value, len(value))

    def test_true_case_whitespace_comments_and_other_sections(self):
        for text in (b"[system]\nsafe_mode=true", b"version = 1\n# comment\n; comment\n[ SyStEm ]\r\n SAFE_MODE = TrUe \r\n",
                     b"[other]\nsafe_mode=false\n[system]\nsafe_mode=true\n[display]\nrefresh_hz=60\n"):
            self.assertEqual(self.parse(text), 1)

    def test_false_missing_malformed_oversized_and_injection(self):
        for text in (b"", b"[other]\nsafe_mode=true", b"[system]\nsafe_mode=false", b"[system]\nsafe_mode=yes",
                     b"[system\nsafe_mode=true", b"[system]\nsafe_mode=true\0", b"[system]\nsafe_mode=true; command",
                     b"[system]\nsafe_mode=true\n" + b"x" * 65536, b"[system]\nsafe_mode=true\n\x01"):
            self.assertEqual(self.parse(text), 0)

    def test_unrecognized_lines_and_last_key_wins(self):
        for text in (b"noise\n[system]\nsafe_mode=true\nbroken", b"[system]\nsafe_mode=false\nSAFE_MODE=TRUE",
                     b"[system]\n[broken\nsafe_mode=true", b"[system]\nsafe_mode=true\nsafe_mode=true"):
            self.assertEqual(self.parse(text), 1)
        self.assertEqual(self.parse(b"[system]\nsafe_mode=true\nsafe_mode=false"), 0)

    def test_writer_only_runtime_file_and_three_modules(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed = root / "seed.conf"
            seed.write_bytes(b"[system]\nsafe_mode=true\n")
            before = seed.read_bytes()
            directory = root / "modprobe.d"
            self.assertEqual(self.library.apply(bytes(seed), bytes(directory)), 1)
            text = (directory / "dby-safe-mode.conf").read_text()
            for name in ("msm", "nt36523_thp", "panel_novatek_nt36523"):
                self.assertIn(f"blacklist {name}\ninstall {name} /bin/false\n", text)
            self.assertEqual(seed.read_bytes(), before)
            self.assertEqual((directory / "dby-safe-mode.conf").stat().st_mode & 0o777, 0o644)

    def test_missing_large_linked_seed_and_unwritable_target_do_not_abort(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); seed = root / "seed.conf"; directory = root / "policy"
            self.assertEqual(self.library.apply(bytes(seed), bytes(directory)), 0)
            seed.write_bytes(b"[system]\nsafe_mode=true\n" + b"x" * 65536)
            self.assertEqual(self.library.apply(bytes(seed), bytes(directory)), 0)
            seed.unlink(); seed.symlink_to(root / "missing")
            self.assertEqual(self.library.apply(bytes(seed), bytes(directory)), 0)
            seed.unlink(); seed.write_bytes(b"[system]\nsafe_mode=true\n")
            directory.write_bytes(b"blocked")
            self.assertEqual(self.library.apply(bytes(seed), bytes(directory)), -1)
            self.assertEqual(directory.read_bytes(), b"blocked")


if __name__ == "__main__":
    unittest.main()
