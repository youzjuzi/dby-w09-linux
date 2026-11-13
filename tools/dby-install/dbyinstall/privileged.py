# SPDX-License-Identifier: GPL-2.0-only
"""All real block-device writes and mounts; invoked only by the device owner."""

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import time

from .common import (BundleError, CommandError, P1_SECTORS, P1_START, P2_START, dependencies, no_links,
                     partition, require, root_sectors, run, verify_bundle, verify_root_size)
from .core import (NO_READBACK_NOTICE, card_info, card_kind, contract_fstab, plan, populate_p1,
                   prepare, extract_rootfs, verify_p1, verify_p2_contract, p2_hash_hint)
from .archive import verify_root
from .image import geometry
from .p2 import check_journal

DEPENDENCIES = ("sfdisk", "mkfs.vfat", "mkfs.ext4", "tar", "zstd", "sha256sum", "lsblk", "blkid",
                "mount", "umount", "blockdev", "udevadm", "dumpe2fs", "sync")


def require_root():
    require(os.geteuid() == 0, "E_INSTALL_ROOT", "Real SD operations require sudo; use --dry-run for a plan.")


def mounted_paths():
    # Only paths are used internally; no mount contents are printed.
    def decode(value):
        for old, new in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
            value = value.replace(old, new)
        return value
    return {Path(decode(line.split()[4])) for line in Path("/proc/self/mountinfo").read_text().splitlines()}


@contextmanager
def workspace():
    work = Path(tempfile.mkdtemp(prefix="dby-install-"))
    try:
        yield work
    finally:
        require(not any(path == work or path.is_relative_to(work) for path in mounted_paths()),
                "E_INSTALL_CLEANUP", "A mount remains; temporary workspace retained for manual cleanup.")
        shutil.rmtree(work)


@contextmanager
def mounts(device, work, *, readonly=False, p1_only=False):
    paths = []
    try:
        for number, kind in (((1, "vfat"),) if p1_only else ((1, "vfat"), (2, "ext4"))):
            path = work / f"p{number}"; path.mkdir()
            options = ("ro,noload" if number == 2 else "ro") if readonly else "rw"
            paths.append(path)
            run(["mount", "-t", kind, "-o", options + ",nosuid,nodev,noexec", partition(device, number), path])
        yield paths
    finally:
        failed = False
        for path in reversed(paths):
            if path in mounted_paths():
                try:
                    run(["umount", path])
                except BundleError:
                    failed = True
        require(not failed and not any(path in mounted_paths() for path in paths),
                "E_INSTALL_CLEANUP", "Cannot unmount installer workspace; no further action taken.")


def check_table(device, size, *, expected_p2=None, execute=run):
    table = json.loads(execute(["sfdisk", "--json", device]))["partitiontable"]
    parts = table.get("partitions", [])
    require(table.get("label") == "dos" and table.get("sectorsize", 512) == 512 and len(parts) == 2,
            "E_INSTALL_TABLE", "Invalid target partition table.")
    actual_p2 = parts[1].get("size")
    require(type(actual_p2) is int, "E_INSTALL_TABLE", "Invalid p2 sector count.")
    root_sectors(size, minimal=True, p2_size=actual_p2 * 512)
    require(expected_p2 is None or actual_p2 == expected_p2, "E_INSTALL_TABLE", "Written p2 size differs from preview.")
    for row, expected in zip(parts, ((P1_START, P1_SECTORS, "c"), (P2_START, actual_p2, "83"))):
        require((row.get("start"), row.get("size"), row.get("type", "").lower()) == expected,
                "E_INSTALL_TABLE", "Target partition layout differs from contract.")
    for number, row in enumerate(parts, 1):
        path = Path(partition(device, number)); no_links(path)
        require(stat.S_ISBLK(path.stat().st_mode)
                and int(execute(["blockdev", "--getsz", path])) == row["size"],
                "E_INSTALL_PARTITION", "Partition node is absent or has the wrong size.")
    return actual_p2


def reread_partitions(device, size, *, expected_p2, execute=run, wait=time.sleep, emit=None):
    """Treat queue timeouts as advisory; require reread and exact table/nodes."""
    warned = False

    def settle():
        nonlocal warned
        try:
            execute(["udevadm", "settle", "--timeout=30"])
        except CommandError as error:
            if error.command != "udevadm" or error.returncode != 1 or error.category != "timeout":
                raise
            if not warned:
                message = "Notice: udevadm settle timeout; partition reread and table/node checks remain required."
                if emit is None:
                    print(message, file=sys.stderr)
                else:
                    emit(message)
                warned = True

    for attempt in range(6):
        settle()
        try:
            execute(["blockdev", "--rereadpt", device])
            break
        except BundleError:
            if attempt == 5:
                raise BundleError("E_INSTALL_BUSY", "Partition reread failed; stop and inspect target use.") from None
            wait(attempt + 1)
    settle()
    return check_table(device, size, expected_p2=expected_p2, execute=execute)


def install(target, bundle, *, ask=input, emit=print, minimal=False, p2_size=None, no_readback=False):
    require_root(); dependencies(DEPENDENCIES)
    preview, identity, document, entries = plan(target, bundle, minimal=minimal, p2_size=p2_size, no_readback=no_readback)
    emit(json.dumps(preview, sort_keys=True, indent=2, ensure_ascii=True))
    if no_readback:
        emit(NO_READBACK_NOTICE)
    target.confirm(identity, ask)
    fd = os.open(target.path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(os.fstat(fd).st_rdev == identity["rdev"] and target.validate() == identity,
                "E_TARGET_CHANGED", "Target changed immediately before partitioning.")
        # Validate the source again after the human confirmation delay.
        require(prepare(bundle, minimal=minimal)[0] == document, "E_INSTALL_CHANGED", "Bundle changed after preview.")
        run(["sfdisk", "--no-reread", "--no-tell-kernel", "--wipe", "always", target.path],
            data=preview["partition_script"].encode())
        reread_partitions(target.path, identity["size"], expected_p2=preview["p2"]["sectors"])
        run(["mkfs.vfat", "-F", "32", "-n", "UBOOT", partition(target.path, 1)])
        format_rootfs(partition(target.path, 2))
        geometry(Path(partition(target.path, 1)), Path(partition(target.path, 2)))
        with workspace() as work:
            with mounts(target.path, work) as (p1, p2):
                populate_p1(p1, bundle)
                extract_rootfs(bundle, preview["rootfs_archive"], p2, ownership=True, execute=run)
                verify_p1(p1, document); verify_root(p2, entries)
                run(["sync", "-f", p1]); run(["sync", "-f", p2])
        if no_readback:
            readback_metadata(target, bundle, expected_kind=preview["rootfs_kind"], expected_p2=preview["p2"]["sectors"])
            emit(NO_READBACK_NOTICE)
        else:
            verify(target, bundle, _check_recovery=False)
    finally:
        os.close(fd)


def readback_metadata(target, bundle, *, expected_kind, expected_p2):
    require_root(); dependencies(DEPENDENCIES)
    identity = target.validate()
    document = verify_bundle(bundle)
    sectors = reread_partitions(target.path, int(identity["size"]), expected_p2=expected_p2)
    geometry(Path(partition(target.path, 1)), Path(partition(target.path, 2)))
    with workspace() as work:
        with mounts(target.path, work, readonly=True) as (p1, p2):
            verify_p1(p1, document)
            verify_p2_contract(p2, expected_kind)
            verify_root_size(int(identity["size"]), sectors, expected_kind)
    return document


def verify(target, bundle, *, p1_only=False, _check_recovery=True):
    require_root(); dependencies(DEPENDENCIES)
    identity = target.validate()
    document = verify_bundle(bundle)
    sectors = check_table(target.path, int(identity["size"]))
    if p1_only:
        geometry(Path(partition(target.path, 1)), Path(partition(target.path, 2)), labels_only=True)
        with workspace() as work:
            with mounts(target.path, work, readonly=True, p1_only=True) as (p1,):
                verify_p1(p1, document)
        return document
    geometry(Path(partition(target.path, 1)), Path(partition(target.path, 2)))
    if _check_recovery:
        check_journal(Path(partition(target.path, 2)), device=target.path)
    with workspace() as work:
        with mounts(target.path, work, readonly=True) as (p1, p2):
            kind = card_kind(p2)
            verify_root_size(int(identity["size"]), sectors, kind)
            document, entries, _ = prepare(bundle, minimal=kind == "test")
            verify_p1(p1, document)
            with p2_hash_hint(enabled=_check_recovery):
                verify_root(p2, entries)
    return document


def info(target, *, full=False):
    require_root(); dependencies(DEPENDENCIES)
    target.validate()
    check_journal(Path(partition(target.path, 2)), device=target.path)
    with workspace() as work:
        with mounts(target.path, work, readonly=True) as (p1, p2):
            return card_info(p1, p2, full=full)


def format_rootfs(filesystem):
    run(["mkfs.ext4", "-F", "-b", "4096", "-I", "256", "-L", "ubuntu-root", filesystem])
