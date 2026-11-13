# SPDX-License-Identifier: GPL-2.0-only
"""Real FAT/ext4 image-file backend; never uses loop devices or mount."""

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import re
import struct
import tempfile

from .common import (BundleError, FSTAB, P1_SECTORS, P1_START, P2_START, dependencies, require, root_sectors, run, verify_bundle, verify_root_size)
from .core import (NO_READBACK_NOTICE, card_info, card_kind, contract_fstab, plan, populate_p1,
                   prepare, extract_rootfs, verify_p1, verify_p2_contract, p2_hash_hint)
from .archive import verify_root
from .p2 import check_journal

DEPENDENCIES = ("sfdisk", "mkfs.vfat", "mkfs.ext4", "mcopy", "mmd", "debugfs", "blkid", "dumpe2fs", "tar", "zstd")


def quote(value):
    value = str(value)
    # libss/debugfs uses double-quoted tokens with literal backslashes, not
    # shell escaping. Refuse unrepresentable quotes instead of changing names.
    require('"' not in value and not any(ord(char) < 32 or ord(char) == 127 for char in value),
            "E_IMAGE_NAME", "Debugfs image paths cannot contain double quotes or control characters.")
    return '"' + value + '"'


def check_archive(entries):
    require(not any(row["xattrs"] for row in entries.values()), "E_IMAGE_XATTR",
            "Image backend cannot preserve archive xattrs; refusing without discarding attributes.")
    require(not any(row.get("acls") for row in entries.values()), "E_IMAGE_ACL",
            "Image backend cannot preserve archive ACLs; refusing without discarding attributes.")
    for name in entries:
        quote("/" + name)


def sparse_copy(source, destination, source_start, destination_start, length):
    with source.open("rb") as src, destination.open("r+b") as dst:
        at, end = source_start, source_start + length
        while at < end:
            try:
                data = os.lseek(src.fileno(), at, os.SEEK_DATA)
            except OSError as error:
                if error.errno == errno.ENXIO:
                    break
                raise
            if data >= end:
                break
            hole = min(os.lseek(src.fileno(), data, os.SEEK_HOLE), end)
            src.seek(data); dst.seek(destination_start + data - source_start)
            while data < hole:
                chunk = src.read(min(1024 * 1024, hole - data))
                require(chunk, "E_IMAGE_SHORT", "Unexpected short image read.")
                dst.write(chunk); data += len(chunk)
            at = hole
        dst.flush(); os.fsync(dst.fileno())


def new_sparse(path, size):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.ftruncate(fd, size)
    finally:
        os.close(fd)


def partitions(path, size):
    with path.open("rb") as stream:
        mbr = stream.read(512)
    require(len(mbr) == 512 and mbr[510:] == b"\x55\xaa", "E_IMAGE_TABLE", "Invalid DOS partition table.")
    result = []
    for index, (expected_start, expected_type) in enumerate(((P1_START, 0x0c), (P2_START, 0x83))):
        row = mbr[446 + index * 16:462 + index * 16]
        start, sectors = struct.unpack_from("<II", row, 8)
        require((start, row[4]) == (expected_start, expected_type), "E_IMAGE_TABLE", "Image partition layout differs from contract.")
        if index == 0:
            require(sectors == P1_SECTORS, "E_IMAGE_TABLE", "Image p1 size differs from contract.")
        else:
            root_sectors(size, minimal=True, p2_size=sectors * 512)
        result.append((start * 512, sectors * 512))
    require(mbr[478:510] == bytes(32), "E_IMAGE_TABLE", "Unexpected extra partitions.")
    return result


def check_label(path, label, kind, *, offset=None, size=None):
    bounds = [] if offset is None else ["-O", str(offset), "-S", str(size)]
    data = dict(line.split("=", 1) for line in run(["blkid", "-p", "-o", "export", *bounds, path]).decode().splitlines() if "=" in line)
    require(data.get("LABEL") == label and data.get("TYPE") == kind, "E_INSTALL_LABEL", "Filesystem label or type differs.")


def geometry(p1, p2, *, labels_only=False):
    for path, label, kind in ((p1, "UBOOT", "vfat"), (p2, "ubuntu-root", "ext4")):
        check_label(path, label, kind)
    if labels_only:
        return
    detail = run(["dumpe2fs", "-h", p2]).decode()
    require(re.search(r"^Block size:\s+4096$", detail, re.M) and re.search(r"^Inode size:\s+256$", detail, re.M),
            "E_INSTALL_GEOMETRY", "Ext4 geometry differs from contract.")


def inode(path, name):
    text = run(["debugfs", "-R", "stat " + quote("/" + name), path]).decode()
    match = re.search(r"Inode:\s+(\d+).*?Mode:\s+0?([0-7]+).*?User:\s+(\d+)\s+Group:\s+(\d+)", text, re.S)
    require(match, "E_IMAGE_READ", "Cannot inspect installed image inode.")
    return tuple(int(value, 8 if index == 1 else 10) for index, value in enumerate(match.groups()))


@contextmanager
def opened(target, *, p1_only=False, check_recovery=False):
    target.validate(existing=True)
    with tempfile.TemporaryDirectory(prefix="dby-image-check-") as temporary:
        work = Path(temporary)
        parts = []
        layout = partitions(target.path, target.size)
        for number, (offset, size) in enumerate(layout[:1] if p1_only else layout, 1):
            path = work / f"p{number}.img"; new_sparse(path, size)
            sparse_copy(target.path, path, offset, 0, size)
            parts.append(path)
        if p1_only:
            check_label(parts[0], "UBOOT", "vfat")
            check_label(target.path, "ubuntu-root", "ext4", offset=layout[1][0], size=layout[1][1])
        else:
            geometry(*parts)
            if check_recovery:
                check_journal(parts[1], image=True)
        p1, p2 = work / "p1", work / "p2"
        p1.mkdir(); p2.mkdir()
        if p1_only:
            for name in ("UBOOT", "manifest.json"):
                run(["mcopy", "-i", parts[0], "-s", "::/" + name, p1])
            yield p1, None, None
        else:
            run(["mcopy", "-i", parts[0], "-s", "::/*", p1])
            run(["debugfs", "-R", "rdump / " + quote(p2), parts[1]])
            yield p1, p2, parts[1]


def install(target, bundle, *, ask=input, minimal=False, p2_size=None, no_readback=False, emit=print):
    preview, identity, document, entries = plan(target, bundle, image=True, minimal=minimal, p2_size=p2_size, no_readback=no_readback)
    dependencies(DEPENDENCIES)
    check_archive(entries)
    quote(target.path.parent)
    if no_readback:
        emit(NO_READBACK_NOTICE)
    require(ask("Type the full image filename to create it: ").strip() == str(target.path),
            "E_TARGET_CONFIRM", "Image filename confirmation did not match.")
    target.validate()
    with tempfile.TemporaryDirectory(prefix=".dby-image-", dir=target.path.parent) as temporary:
        work = Path(temporary)
        disk = work / "disk.img"; new_sparse(disk, target.size)
        run(["sfdisk", "--no-reread", "--no-tell-kernel", disk], data=preview["partition_script"].encode())
        p1image, p2image = work / "p1.img", work / "p2.img"
        new_sparse(p1image, P1_SECTORS * 512); new_sparse(p2image, preview["p2"]["size_bytes"])
        p1, p2 = work / "p1", work / "p2"; p1.mkdir(); p2.mkdir()
        populate_p1(p1, bundle)
        extract_rootfs(bundle, preview["rootfs_archive"], p2, ownership=False, execute=run)
        verify_root(p2, entries, ownership=False)
        run(["mkfs.vfat", "-F", "32", "-n", "UBOOT", p1image])
        run(["mcopy", "-i", p1image, "-s", p1 / "UBOOT", "::/"])
        run(["mcopy", "-i", p1image, p1 / "manifest.json", "::/manifest.json"])
        run(["mmd", "-i", p1image, "::/dby-seed"])
        format_rootfs(p2image, p2, entries, work)
        geometry(p1image, p2image)
        sparse_copy(p1image, disk, 0, P1_START * 512, P1_SECTORS * 512)
        sparse_copy(p2image, disk, 0, P2_START * 512, preview["p2"]["size_bytes"])
        # Hard-link publication refuses a racing filename without replacing it.
        from .targets import ImageFileTarget
        staged = ImageFileTarget(disk)
        if no_readback:
            readback_metadata(staged, bundle, expected_kind=preview["rootfs_kind"], expected_p2=preview["p2"]["sectors"])
        else:
            verify(staged, bundle, _check_recovery=False)
        os.link(disk, target.path)
    if no_readback:
        emit(NO_READBACK_NOTICE)
    return preview


def readback_metadata(target, bundle, *, expected_kind, expected_p2):
    document = verify_bundle(bundle)
    dependencies(DEPENDENCIES)
    with opened(target) as (p1, p2, filesystem):
        sectors = filesystem.stat().st_size // 512
        require(sectors == expected_p2, "E_IMAGE_TABLE", "Written p2 size differs from preview.")
        verify_root_size(target.size, sectors, expected_kind)
        verify_p1(p1, document)
        verify_p2_contract(p2, expected_kind)
    return document


def verify(target, bundle, *, p1_only=False, _check_recovery=True):
    document = verify_bundle(bundle)
    dependencies(DEPENDENCIES)
    with opened(target, p1_only=p1_only, check_recovery=_check_recovery and not p1_only) as (p1, p2, filesystem):
        if p1_only:
            verify_p1(p1, document)
            return document
        kind = card_kind(p2)
        verify_root_size(target.size, filesystem.stat().st_size // 512, kind)
        document, entries, _ = prepare(bundle, minimal=kind == "test")
        check_archive(entries)
        verify_p1(p1, document)
        with p2_hash_hint(enabled=_check_recovery):
            verify_root(p2, entries, ownership=False, xattrs=False, permissions=False, hardlinks=False)
        verify_inodes(filesystem, entries)
    return document


def info(target, *, full=False):
    dependencies(DEPENDENCIES)
    with opened(target, check_recovery=True) as (p1, p2, _):
        return card_info(p1, p2, full=full)


def format_rootfs(p2image, p2, entries, work):
    run(["mkfs.ext4", "-F", "-b", "4096", "-I", "256", "-L", "ubuntu-root",
         "-E", "lazy_itable_init=1,lazy_journal_init=1", "-d", p2, p2image])
    commands = work / "inode.commands"
    commands.write_text("".join(f"set_inode_field {quote('/' + name)} uid {row['uid']}\n"
                                f"set_inode_field {quote('/' + name)} gid {row['gid']}\n" for name, row in entries.items()))
    run(["debugfs", "-w", "-f", commands, p2image])


def verify_inodes(filesystem, entries):
    for name, row in entries.items():
        number, mode, uid, gid = inode(filesystem, name)
        require((uid, gid) == (row["uid"], row["gid"]) and (row["kind"] == "symlink" or mode == row["mode"]),
                "E_IMAGE_METADATA", "Installed image ownership or permissions differ.")
        if row["kind"] == "hardlink":
            require(number == inode(filesystem, row["resolved"])[0], "E_INSTALL_LINK", "Image hard link changed.")
