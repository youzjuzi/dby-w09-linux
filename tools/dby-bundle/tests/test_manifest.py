# SPDX-License-Identifier: GPL-2.0-only
"""Synthetic schema and CLI checks, independent of hardware and private inputs."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from dbybundle import BundleError, create_manifest, encode_manifest, read_manifest, verify, write_manifest
from dbybundle import manifest as library

CREATED = "2026-11-02T01:00:00Z"
DIGEST = "a" * 64


class ManifestTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name)
        self.root = self.parent / "bundle"
        self.boot = self.root / "p1/UBOOT"
        self.boot.mkdir(parents=True)
        for name in library.BOOT_FILES[:-1]:
            (self.boot / name).write_bytes(("synthetic " + name).encode())
        self.boot_manifest()
        (self.root / "rootfs.tar.zst").write_bytes(b"synthetic archive")

    def boot_manifest(self):
        text = "".join(hashlib.sha256((self.boot / name).read_bytes()).hexdigest() + f"  {name}\n"
                       for name in library.BOOT_FILES[:3])
        (self.boot / "MANIFEST").write_text(text, encoding="ascii")

    def create(self, **kwargs):
        return create_manifest(self.root, created=CREATED, sequence=1, sources=kwargs.pop("sources", {}), **kwargs)

    def publish(self):
        document = self.create()
        write_manifest(self.root, document)
        return document

    def replace_document(self, document):
        (self.root / "manifest.json").write_text(json.dumps(document), encoding="utf-8")

    @contextmanager
    def error(self, code):
        with self.assertRaises(BundleError) as caught:
            yield
        self.assertEqual(caught.exception.code, code)

    def test_valid_bundle_and_read(self):
        document = self.publish()
        self.assertEqual(document, verify(self.root))
        self.assertEqual(document, read_manifest(self.root))
        self.assertEqual(document["bundle_id"], "20261102-1")

    def test_p1_only_and_optional_test_archive(self):
        (self.root / "rootfs.tar.zst").unlink()
        self.publish()
        verify(self.root)
        (self.root / "rootfs-test.tar.zst").write_bytes(b"test archive")
        with self.error("E_BUNDLE_EXTRA"):
            verify(self.root)
        (self.root / "manifest.json").unlink()
        document = self.publish()
        self.assertIn("rootfs-test.tar.zst", document["files"])
        verify(self.root)

    def test_missing_payload(self):
        self.publish()
        (self.boot / "Image").unlink()
        with self.error("E_BUNDLE_MISSING"):
            verify(self.root)

    def test_extra_payload(self):
        self.publish()
        (self.root / "unlisted").write_bytes(b"extra")
        with self.error("E_BUNDLE_EXTRA"):
            verify(self.root)

    def test_size_mismatch(self):
        self.publish()
        (self.boot / "Image").write_bytes(b"short")
        with self.error("E_BUNDLE_SIZE"):
            verify(self.root)

    def test_hash_mismatch(self):
        self.publish()
        path = self.boot / "Image"
        path.write_bytes(b"!" * path.stat().st_size)
        with self.error("E_BUNDLE_HASH"):
            verify(self.root)

    def test_path_traversal_and_noncanonical_paths(self):
        document = self.publish()
        for name in ("../escape", "/absolute", "p1/../escape", "p1//Image", "./Image", "", "p1/", "C:/file", "p1\\file", "bad\nfile"):
            with self.subTest(name=name):
                modified = dict(document, files={**document["files"], name: {"size": 0, "sha256": DIGEST}})
                self.replace_document(modified)
                with self.error("E_BUNDLE_PATH"):
                    verify(self.root)

    def test_symbolic_file_and_dangling_link(self):
        self.publish()
        target = self.boot / "Image"
        target.unlink()
        for destination in ("dby-w09.dtb", "absent"):
            target.symlink_to(destination)
            with self.error("E_BUNDLE_SYMLINK"):
                verify(self.root)
            target.unlink()

    def test_symbolic_directory_and_bundle_root(self):
        self.publish()
        linked = self.parent / "linked"
        linked.symlink_to(self.root, target_is_directory=True)
        with self.error("E_BUNDLE_SYMLINK"):
            verify(linked)
        (self.root / "linked").symlink_to(self.parent, target_is_directory=True)
        with self.error("E_BUNDLE_SYMLINK"):
            verify(self.root)

    def test_symbolic_ancestor_and_manifest(self):
        self.publish()
        linked = self.parent / "linked"
        linked.symlink_to(self.parent, target_is_directory=True)
        with self.error("E_BUNDLE_SYMLINK"):
            verify(linked / "bundle")
        metadata = self.root / "manifest.json"
        metadata.rename(self.parent / "saved.json")
        metadata.symlink_to(self.parent / "saved.json")
        with self.error("E_BUNDLE_SYMLINK"):
            verify(self.root)

    def test_special_file_and_empty_extra_directory(self):
        self.publish()
        pipe = self.root / "pipe"
        os.mkfifo(pipe)
        with self.error("E_BUNDLE_TYPE"):
            verify(self.root)
        pipe.unlink()
        (self.root / "extra").mkdir()
        with self.error("E_BUNDLE_LAYOUT"):
            verify(self.root)

    def test_boot_order_hash_spacing_and_extra_line(self):
        document = self.publish()
        original = (self.boot / "MANIFEST").read_text()
        for text in ("".join(reversed(original.splitlines(keepends=True))),
                     "0" * 64 + original[64:], original.replace("  Image", " Image"), original + "\n"):
            (self.boot / "MANIFEST").write_text(text)
            document["files"]["p1/UBOOT/MANIFEST"] = library.file_record(self.boot / "MANIFEST")
            self.replace_document(document)
            with self.error("E_BUNDLE_BOOT_MANIFEST"):
                verify(self.root)

    def test_exact_five_boot_files(self):
        (self.boot / "other").write_bytes(b"extra")
        with self.error("E_BUNDLE_EXTRA"):
            self.create()
        (self.boot / "other").unlink()
        (self.boot / "dby-device.img").unlink()
        with self.error("E_BUNDLE_LAYOUT"):
            self.create()

    def test_schema_versions_and_types(self):
        document = self.publish()
        for schema in (0, 2, "1", True, 1.0, None):
            self.replace_document(dict(document, schema=schema))
            with self.error("E_BUNDLE_SCHEMA"):
                verify(self.root)

    def test_invalid_json_duplicate_keys_and_missing_manifest(self):
        with self.error("E_BUNDLE_MISSING"):
            verify(self.root)
        for raw in (b"{", b"[]", b'{"schema":1,"schema":1}', b'{"schema":NaN}', b"\xff"):
            (self.root / "manifest.json").write_bytes(raw)
            with self.error("E_BUNDLE_JSON"):
                verify(self.root)

    def test_invalid_metadata(self):
        document = self.publish()
        for changes, code in (({"created": "2026-02-30T01:00:00Z"}, "E_BUNDLE_METADATA"),
                              ({"created": "2026-11-02T01:00:00+09:00"}, "E_BUNDLE_METADATA"),
                              ({"bundle_id": "20261101-1"}, "E_BUNDLE_METADATA"),
                              ({"kernel_release": "other"}, "E_BUNDLE_RELEASE"),
                              ({"rootfs": {"package_count": True}}, "E_BUNDLE_METADATA"),
                              ({"unknown": "value"}, "E_BUNDLE_METADATA")):
            self.replace_document(dict(document, **changes))
            with self.error(code):
                verify(self.root)
        for record in ({"size": True, "sha256": DIGEST}, {"size": -1, "sha256": DIGEST},
                       {"size": 0, "sha256": "invalid"}, {"size": 0}):
            self.replace_document(dict(document, files={**document["files"], "rootfs.tar.zst": record}))
            with self.error("E_BUNDLE_METADATA"):
                verify(self.root)

    def test_stable_canonical_bytes_and_sources(self):
        sources = {"repositories": {"linux": "f" * 40, "kernel": "e" * 40, "uboot": "d" * 40},
                   "upstream": [{"name": "linux", "version": "7.2.10", "sha256": DIGEST}],
                   "inputs": {"Image": DIGEST}}
        first = self.create(sources=sources)
        second = self.create(sources=dict(reversed(list(sources.items()))))
        self.assertEqual(encode_manifest(first), encode_manifest(second))
        self.assertEqual(encode_manifest(first), (json.dumps(first, sort_keys=True, indent=2) + "\n").encode())
        write_manifest(self.root, first)
        verify(self.root)

    def test_redact_private_paths_and_contents(self):
        private = str(self.parent / "owner-private" / "input.bin")
        document = self.create(sources={"private_inputs": [
            {"path": private, "sha256": DIGEST, "content": "private-content", "label": private}]})
        data = encode_manifest(document)
        self.assertNotIn(private.encode(), data)
        self.assertNotIn(b"private-content", data)
        self.assertEqual(document["sources"]["private_inputs"], [{"sha256": DIGEST}])
        write_manifest(self.root, document)
        verify(self.root)
        document["sources"]["private_inputs"][0]["path"] = private
        self.replace_document(document)
        with self.error("E_BUNDLE_METADATA"):
            verify(self.root)
        for sources in ({"unexpected": private}, {"inputs": {private: DIGEST}},
                        {"upstream": [{"name": private, "version": "1", "sha256": DIGEST}]},
                        {"repositories": {"linux": private}}):
            with self.error("E_BUNDLE_METADATA"):
                self.create(sources=sources)

    def test_full_rootfs_summary(self):
        rootfs = {"recipe_sha256": DIGEST, "packages_sha256": DIGEST, "package_count": 1}
        document = self.create(rootfs=rootfs)
        write_manifest(self.root, document)
        verify(self.root)
        (self.root / "manifest.json").unlink()
        (self.root / "rootfs.tar.zst").unlink()
        with self.error("E_BUNDLE_LAYOUT"):
            self.create(rootfs=rootfs)

    def test_sequence_and_time_errors(self):
        for sequence in (0, -1, True, "1"):
            with self.error("E_BUNDLE_METADATA"):
                create_manifest(self.root, sequence=sequence, sources={})
        with self.error("E_BUNDLE_METADATA"):
            create_manifest(self.root, sequence=1, sources={}, created=42)
        with self.error("E_BUNDLE_METADATA"):
            self.create(rootfs=[])

    def test_missing_directory_and_metadata_size_limit(self):
        with self.error("E_BUNDLE_DIR"):
            verify(self.parent / "absent")
        (self.root / "manifest.json").write_bytes(b" " * (library.MAX_METADATA + 1))
        with self.error("E_BUNDLE_JSON"):
            verify(self.root)

    def test_streaming_payload_hash(self):
        payload = self.root / "rootfs.tar.zst"
        payload.write_bytes(b"x" * (3 * 1024 * 1024 + 1))
        original = library.regular_open
        reads = []

        @contextmanager
        def bounded_open(path):
            with original(path) as stream:
                class Bounded:
                    def read(inner, size=-1):
                        self.assertGreater(size, 0)
                        self.assertLessEqual(size, 1024 * 1024)
                        reads.append(size)
                        return stream.read(size)
                yield Bounded()

        with patch.object(library, "regular_open", bounded_open):
            actual = library.file_record(payload)
        self.assertGreater(len(reads), 3)
        self.assertEqual(actual["size"], payload.stat().st_size)
        self.assertEqual(actual["sha256"], hashlib.sha256(payload.read_bytes()).hexdigest())

    def test_failed_write_preserves_existing_state(self):
        document = self.publish()
        before = (self.root / "manifest.json").read_bytes()
        with self.error("E_BUNDLE_EXISTS"):
            write_manifest(self.root, document)
        self.assertEqual(before, (self.root / "manifest.json").read_bytes())
        (self.root / "manifest.json").unlink()
        (self.boot / "Image").write_bytes(b"tampered")
        with self.error("E_BUNDLE_HASH"):
            write_manifest(self.root, document)
        self.assertFalse((self.root / "manifest.json").exists())
        self.assertFalse(list(self.root.glob(".manifest-*")))

    def test_cli_verify_show_and_tamper(self):
        self.publish()
        def command(name):
            return subprocess.run([sys.executable, "-I", "-B", str(TOOL / "dby-bundle"), name, str(self.root)],
                                  capture_output=True, check=False)
        success = command("verify")
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertIn(b"OK 20261102-1", success.stdout)
        shown = command("show")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(shown.stdout, (self.root / "manifest.json").read_bytes())
        (self.boot / "Image").write_bytes(b"tampered")
        for name in ("verify", "show"):
            failure = command(name)
            self.assertNotEqual(failure.returncode, 0)
            self.assertIn(b"E_BUNDLE_SIZE", failure.stderr)
            self.assertEqual(failure.stdout, b"")
            self.assertNotIn(str(self.root).encode(), failure.stderr)


if __name__ == "__main__":
    unittest.main()
