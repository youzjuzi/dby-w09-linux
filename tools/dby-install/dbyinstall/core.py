# SPDX-License-Identifier: GPL-2.0-only
"""Shared install preview and read-back checks, independent of mount backend."""

from contextlib import contextmanager
import hashlib
import json
import shutil

from .archive import inspect_archive, verify_root
from .common import (BOOT_FILES, BundleError, FSTAB, P1_SECTORS, P1_START, P2_START, file_record, inside,
                     partition_script, read_json, require, root_sectors, validate_metadata, verify_bundle)

NO_READBACK_NOTICE = 'Read-back of p2 skipped (--no-readback); run "dby-install verify" later to check the card.'


def prepare(bundle, *, minimal=False):
    bundle = bundle.absolute()
    document = verify_bundle(bundle)
    archive = "rootfs-test.tar.zst" if minimal else "rootfs.tar.zst"
    require(archive in document["files"], "E_INSTALL_TEST_ROOTFS" if minimal else "E_INSTALL_ROOTFS",
            "Bundle has no " + archive + ".")
    entries, total = inspect_archive(bundle / archive)
    if "etc/fstab" not in entries:
        entries["etc/fstab"] = {"kind": "file", "mode": 0o644, "uid": 0, "gid": 0, "xattrs": {},
                                 "content": FSTAB, "size": len(FSTAB), "sha256": hashlib.sha256(FSTAB).hexdigest()}
    expected = b"test\n" if minimal else b"full\n"
    require(entries.get("etc/dby-rootfs-kind", {}).get("content", b"" if minimal else b"full\n") == expected,
            "E_INSTALL_KIND", "Rootfs kind marker does not match the selected installation.")
    return document, entries, total


def plan(target, bundle, *, image=False, minimal=False, p2_size=None, no_readback=False):
    document, entries, total = prepare(bundle, minimal=minimal)
    identity = target.validate()
    size = identity["size"] = int(identity["size"])
    sectors = root_sectors(size, minimal=minimal, p2_size=p2_size)
    require(sectors * 512 >= int(total * 1.2) + 256 * 1024**2,
            "E_INSTALL_CAPACITY", "Rootfs plus free-space margin does not fit p2.")
    layout = partition_script(size, minimal=minimal, p2_size=p2_size)
    preview = {"action": "erase entire target and install " + ("test system" if minimal else "full system"), "target": str(target.path),
        "rootfs_kind": "test" if minimal else "full", "rootfs_archive": "rootfs-test.tar.zst" if minimal else "rootfs.tar.zst",
        "kind": "image" if image else "block-device", "size_bytes": size,
        "confirmation": "full target name required; no automatic yes", "partition_script": layout,
        "p1": {"start": P1_START, "sectors": P1_SECTORS, "filesystem": "FAT32", "label": "UBOOT"},
        "p2": {"start": P2_START, "sectors": sectors, "size_bytes": sectors * 512, "filesystem": "ext4", "label": "ubuntu-root",
               "block_size": 4096, "inode_size": 256},
        "steps": ["verify bundle and archive", "recheck identity and confirm full name", "sfdisk", "reread and validate partitions",
                  "mkfs.vfat -F 32 -n UBOOT", "mkfs.ext4 -b 4096 -I 256 -L ubuntu-root", "copy five UBOOT files",
                  "copy manifest and create empty dby-seed", "extract rootfs preserving metadata and attributes",
                  "validate or create contract fstab", "sync and unmount", "reopen read-only and hash every source file"],
        "bundle_id": document["bundle_id"]}
    preview["unallocated_bytes"] = size - (P2_START + sectors) * 512
    preview["readback"] = {"p1": "full", "p2": "metadata" if no_readback else "full"}
    if no_readback:
        preview["notice"] = NO_READBACK_NOTICE
        preview["steps"][-1] = "reopen read-only; hash p1 and check p2 geometry, kind and fstab; skip p2 file hashes"
    preview["summary"] = (f"Test card: p2 {sectors * 512 / 1024**3:g} GiB; remaining space unallocated."
                          if minimal else "Full system: p2 uses the remaining target capacity.")
    if not image:
        preview.update(model=identity.get("model", ""), serial=identity.get("serial", ""))
    return preview, identity, document, entries


def populate_p1(root, bundle):
    boot = root / "UBOOT"
    boot.mkdir()
    for name in BOOT_FILES:
        shutil.copyfile(bundle / "p1/UBOOT" / name, boot / name)
    shutil.copyfile(bundle / "manifest.json", root / "manifest.json")
    (root / "dby-seed").mkdir()


def contract_fstab(root):
    path = inside(root, "etc/fstab", follow_final=False)
    if path.exists() or path.is_symlink():
        require(not path.is_symlink() and path.read_bytes() == FSTAB,
                "E_INSTALL_FSTAB", "Existing fstab violates the root volume contract.")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(FSTAB); path.chmod(0o644)


def verify_p1(root, document):
    boot = root / "UBOOT"
    require(boot.is_dir() and not boot.is_symlink() and {p.name for p in boot.iterdir()} == set(BOOT_FILES),
            "E_INSTALL_P1", "Installed UBOOT inventory is not the five-file contract.")
    for name in BOOT_FILES:
        path = boot / name
        require(not path.is_symlink() and path.is_file(), "E_INSTALL_P1", "Invalid installed boot file.")
        require(file_record(path) == document["files"]["p1/UBOOT/" + name],
                "E_INSTALL_HASH", "Installed boot file hash does not match bundle.")
    expected = "".join(document["files"]["p1/UBOOT/" + name]["sha256"] + f"  {name}\n" for name in BOOT_FILES[:3])
    require((boot / "MANIFEST").read_text() == expected, "E_INSTALL_MANIFEST", "Installed boot MANIFEST is invalid.")
    metadata = root / "manifest.json"
    require(metadata.is_file() and not metadata.is_symlink(), "E_INSTALL_MANIFEST", "Installed card manifest is missing or invalid.")
    require(read_json(metadata) == document, "E_INSTALL_MANIFEST", "Card manifest differs from bundle.")


def card_kind(p2):
    marker = inside(p2, "etc/dby-rootfs-kind", follow_final=False)
    if not marker.exists() and not marker.is_symlink():
        return "unknown"
    kind = contract_file(p2, "etc/dby-rootfs-kind", "E_INSTALL_KIND").read_bytes().strip()
    require(kind in (b"full", b"test", b"unknown"), "E_INSTALL_KIND",
            "etc/dby-rootfs-kind content does not match the rootfs contract.")
    return kind.decode("ascii")


def contract_file(root, name, code):
    path = inside(root, name, follow_final=False)
    require(path.exists() or path.is_symlink(), code, name + " is missing.")
    require(not path.is_symlink() and path.is_file(), code,
            name + " has the wrong type (expected a regular file).")
    return path


def verify_p2_contract(root, expected_kind):
    """Check small installed contract files without creating or rewriting them."""
    for name, content, code in (("etc/dby-rootfs-kind", (expected_kind + "\n").encode(), "E_INSTALL_KIND"),
                                ("etc/fstab", FSTAB, "E_INSTALL_FSTAB")):
        path = contract_file(root, name, code)
        require(path.stat().st_size == len(content) and path.read_bytes() == content, code,
                name + " content does not match the rootfs contract.")


@contextmanager
def p2_hash_hint(*, enabled=True):
    try:
        yield
    except BundleError as error:
        if enabled and error.code == "E_INSTALL_HASH":
            raise BundleError(error.code, "Installed rootfs file digest does not match. "
                              "p2 files change after boot/use; use --p1-only to check only the device package.") from None
        raise


def card_info(p1, p2, *, full=False):
    metadata = p1 / "manifest.json"
    document = read_json(metadata) if metadata.is_file() and not metadata.is_symlink() else None
    if document is not None:
        validate_metadata(document)
    kind = card_kind(p2)
    seed = p1 / "dby-seed"
    result = {"bundle_id": document["bundle_id"] if document else None,
            "kernel_release": document["kernel_release"] if document else None,
            "repositories": document["sources"].get("repositories", {}) if document else {},
            "rootfs_kind": kind, "seed": {"config_present": (seed / "seed.conf").is_file(),
                                          "authorized_keys_present": (seed / "authorized_keys").is_file()}}
    if full:
        return dict(result, manifest=document)
    rootfs = document["rootfs"] if document else {}
    sources = document["sources"] if document else {}
    result["rootfs"] = {"package_count": rootfs.get("package_count"),
                        **{key: rootfs[key][:16] if key in rootfs else None
                           for key in ("recipe_sha256", "packages_sha256")}}
    result["files"] = {name: {"size": document["files"]["p1/UBOOT/" + name]["size"],
                              "sha256": document["files"]["p1/UBOOT/" + name]["sha256"][:16]}
                       for name in BOOT_FILES} if document else {}
    result["sources"] = {"upstream": [{key: row[key] for key in ("name", "version")}
                                      for row in sources.get("upstream", [])]}
    return result


def extract_rootfs(bundle, archive, root, *, ownership, execute):
    """Shared tar/contract path; retain each install backend's metadata policy."""
    flags = ["--numeric-owner", "--same-owner", "--same-permissions", "--xattrs", "--xattrs-include=*", "--acls"] if ownership else ["--numeric-owner", "--no-same-owner", "--same-permissions"]
    execute(["tar", "--zstd", "--extract", "--file", bundle / archive, "--directory", root, *flags])
    contract_fstab(root)
