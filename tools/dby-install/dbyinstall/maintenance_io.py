# SPDX-License-Identifier: GPL-2.0-only
"""P1-only maintenance I/O. Image tests never mount or open block devices."""

from contextlib import contextmanager
import errno
import fcntl
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

from .common import (BundleError, CommandError, P1_START, P1_SECTORS, dependencies, file_record,
                     no_links, partition, require, run, verify_root_size)
from .core import card_kind, verify_p2_contract
from . import image, privileged
from .targets import ImageFileTarget
from .p2 import check_journal

RESERVED = ("UBOOT", "UBOOT.new", "UBOOT.old", "manifest.json", "manifest.json.new", "dby-seed")


class PinnedImage:
    """Read/write the locked inode, never reopen an attacker-replaced name."""
    def __init__(self, fd):
        self.fd = fd

    def open(self, mode):
        require(mode in ("rb", "r+b"), "E_IMAGE_PATH", "Unsupported pinned-image access.")
        stream = os.fdopen(os.dup(self.fd), mode)
        # dup shares the file offset. Each sequential reader must start like
        # a fresh Path.open(), rather than inheriting a previous extent seek.
        stream.seek(0)
        return stream


def seed_tree(root):
    """Hash only seed names/types/content; never expose seed values."""
    seed = root / "dby-seed"
    if not seed.exists() and not seed.is_symlink():
        return None
    require(seed.is_dir() and not seed.is_symlink(), "E_UPDATE_VERIFY", "Invalid seed directory.")
    rows = []
    for path in sorted(seed.rglob("*")):
        mode = path.lstat().st_mode
        require(stat.S_ISDIR(mode) or stat.S_ISREG(mode), "E_UPDATE_VERIFY", "Invalid seed entry type.")
        name = path.relative_to(seed).as_posix()
        rows.append((name, "directory" if path.is_dir() else "file",
                     None if path.is_dir() else file_record(path)))
    return hashlib.sha256(repr(rows).encode()).hexdigest()


def extents(path, start, length):
    result = []
    with path.open("rb") as stream:
        at, end = start, start + length
        while at < end:
            try:
                data = os.lseek(stream.fileno(), at, os.SEEK_DATA)
            except OSError as error:
                if error.errno == errno.ENXIO:
                    break
                raise
            if data >= end:
                break
            hole = min(os.lseek(stream.fileno(), data, os.SEEK_HOLE), end)
            result.append((data - start, hole - start)); at = hole
    return result


def replace_region(source, target, offset, length):
    """Copy sparse data AND zero any destination extents absent in the source."""
    ranges = sorted(extents(source, 0, length) + extents(target, offset, length))
    merged = []
    for first, last in ranges:
        if merged and first <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(last, merged[-1][1]))
        else:
            merged.append((first, last))
    with source.open("rb") as src, target.open("r+b") as dst:
        for first, last in merged:
            src.seek(first); dst.seek(offset + first)
            while first < last:
                chunk = src.read(min(1024 * 1024, last - first))
                require(chunk, "E_UPDATE_VERIFY", "Short p1 image read.")
                dst.write(chunk); first += len(chunk)
        dst.flush(); os.fsync(dst.fileno())


def region_hash(path, offset, length):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        stream.seek(offset)
        while length:
            chunk = stream.read(min(length, 4 * 1024 * 1024))
            require(chunk, "E_UPDATE_VERIFY", "Short partition read.")
            digest.update(chunk); length -= len(chunk)
    return digest.hexdigest()


class DirectoryStore:
    def __init__(self, root):
        self.root = root

    def exists(self, name):
        return (self.root / name).exists() or (self.root / name).is_symlink()

    def remove(self, name):
        path = self.root / name
        require(not path.is_symlink(), "E_UPDATE_VERIFY", "Linked maintenance path refused.")
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()

    def mkdir(self, name):
        (self.root / name).mkdir()

    def copy(self, source, name):
        path = self.root / name
        require(not path.exists() and not path.is_symlink(), "E_UPDATE_LEFTOVER", "Staging file already exists.")
        with source.open("rb") as src, path.open("xb") as dst:
            shutil.copyfileobj(src, dst); dst.flush(); os.fsync(dst.fileno())

    def rename(self, source, destination):
        if destination == "manifest.json":
            os.replace(self.root / source, self.root / destination)
        else:
            require(not self.exists(destination), "E_UPDATE_LEFTOVER", "Rename destination already exists.")
            (self.root / source).rename(self.root / destination)

    def export(self, directory, destination):
        shutil.copytree(self.root / directory, destination)

    def read_file(self, name, destination):
        shutil.copyfile(self.root / name, destination)

    def sync(self):
        run(["sync", "-f", self.root])


class FatStore:
    def __init__(self, filesystem):
        self.filesystem = filesystem

    def command(self, tool, *args):
        return run([tool, "-i", self.filesystem, *args])

    def exists(self, name):
        try:
            self.command("mdir", "-b", "::/" + name)
            return True
        except CommandError as error:
            if error.returncode == 1:
                return False
            raise

    def remove(self, name):
        if self.exists(name):
            self.command("mdeltree" if name.startswith("UBOOT") else "mdel", "::/" + name)

    def mkdir(self, name):
        self.command("mmd", "::/" + name)

    def copy(self, source, name):
        require(not self.exists(name), "E_UPDATE_LEFTOVER", "Staging file already exists.")
        self.command("mcopy", source, "::/" + name)

    def rename(self, source, destination):
        args = ["-D", "o"] if destination == "manifest.json" else []
        self.command("mren", *args, "::/" + source, "::/" + destination)

    def export(self, directory, destination):
        with tempfile.TemporaryDirectory(prefix="dby-fat-read-") as temporary:
            root = Path(temporary)
            self.command("mcopy", "-s", "::/" + directory, root)
            shutil.copytree(root / directory, destination)

    def read_file(self, name, destination):
        self.command("mcopy", "::/" + name, destination)

    def snapshot(self, root):
        for name in RESERVED:
            if not self.exists(name):
                continue
            self.command("mcopy", "-s", "::/" + name, root)

    def sync(self):
        with self.filesystem.open("rb") as stream:
            os.fsync(stream.fileno())


def dump_contract(filesystem, root):
    (root / "etc").mkdir(parents=True)
    for name, limit in (("etc/dby-rootfs-kind", 5), ("etc/fstab", 128)):
        detail = run(["debugfs", "-R", "stat /" + name, filesystem]).decode()
        size = re.search(r"\bSize:\s+(\d+)", detail)
        code = "E_INSTALL_KIND" if name.endswith("dby-rootfs-kind") else "E_INSTALL_FSTAB"
        require("Inode:" in detail, code, name + " is missing.")
        require("Type: regular" in detail, code, name + " has the wrong type (expected a regular file).")
        require(size and int(size[1]) <= limit, code, name + " content does not match the rootfs contract.")
        run(["debugfs", "-R", "dump /" + name + " " + image.quote(root / name), filesystem])
    kind = card_kind(root)
    verify_p2_contract(root, kind)
    return kind


@contextmanager
def card_mounts(device, work, *, writable=False):
    """Only p1 can be rw; p2 is always ro,noload, including recovery."""
    paths = []
    try:
        for number, filesystem in ((1, "vfat"), (2, "ext4")):
            path = work / f"p{number}"; path.mkdir(); paths.append(path)
            options = ("rw" if writable else "ro") if number == 1 else "ro,noload"
            run(["mount", "-t", filesystem, "-o", options + ",nosuid,nodev,noexec", partition(device, number), path])
        yield paths
    finally:
        failed = False
        for path in reversed(paths):
            if path in privileged.mounted_paths():
                try:
                    run(["umount", path])
                except BundleError:
                    failed = True
        require(not failed and not any(path in privileged.mounted_paths() for path in paths),
                "E_INSTALL_CLEANUP", "Cannot unmount maintenance workspace; inspect retained mounts.")


class Backend:
    def __init__(self, target, *, dry_run=False):
        self.target, self.dry_run = target, dry_run
        self.is_image = isinstance(target, ImageFileTarget)
        self.identity = target.validate(existing=True) if self.is_image else target.validate()
        self.size = int(self.identity["size"])
        self.original = None
        self.forbidden_roots = []
        self.media = self.target.path
        if self.is_image:
            info = self.target.path.stat()
            self.image_identity = (info.st_dev, info.st_ino)

    def __enter__(self):
        if not self.is_image and not self.dry_run:
            privileged.require_root()
            dependencies(("sfdisk", "blockdev", "mount", "umount", "sync", "blkid", "dumpe2fs"))
        else:
            dependencies(("mcopy", "mdir", "debugfs", "blkid", "dumpe2fs"))
        self.temporary = tempfile.TemporaryDirectory(prefix="dby-maintenance-")
        self.work = Path(self.temporary.name)
        return self

    def __exit__(self, *exc):
        # The privileged workspace context retains failed mounts before here.
        self.temporary.cleanup()

    @contextmanager
    def locked(self):
        fd = os.open(self.target.path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            before = self.target.validate(existing=True) if self.is_image else self.target.validate()
            require(before == self.identity, "E_TARGET_CHANGED", "Target changed after confirmation.")
            if self.is_image:
                st = os.fstat(fd)
                require(stat.S_ISREG(st.st_mode) and (st.st_dev, st.st_ino) == self.image_identity
                        and st.st_ino == self.target.path.stat().st_ino,
                        "E_TARGET_CHANGED", "Image identity changed.")
                self.media = PinnedImage(fd)
                self.original = self.work / "original-p1.img"
                image.new_sparse(self.original, P1_SECTORS * 512)
                image.sparse_copy(self.media, self.original, P1_START * 512, 0, P1_SECTORS * 512)
            else:
                require(os.fstat(fd).st_rdev == self.identity["rdev"], "E_TARGET_CHANGED", "Device identity changed.")
            yield
        finally:
            self.media = self.target.path
            os.close(fd)

    def check_image_name(self):
        if self.is_image:
            no_links(self.target.path)
            current = self.target.path.stat()
            require((current.st_dev, current.st_ino) == self.image_identity,
                    "E_TARGET_CHANGED", "Image filename now identifies a different file.")

    def confirm(self, ask):
        require(ask("Type the full target name to replace its device package: ").strip() == str(self.target.path),
                "E_TARGET_CONFIRM", "Full target name confirmation did not match.")

    @contextmanager
    def snapshot(self):
        with tempfile.TemporaryDirectory(prefix="read-", dir=self.work) as temporary:
            work = Path(temporary)
            if self.is_image:
                self.check_image_name()
                layout = image.partitions(self.media, self.size)
                with self.media.open("rb") as stream:
                    table = hashlib.sha256(stream.read(512)).hexdigest()
                filesystems = []
                for index, (offset, size) in enumerate(layout):
                    p = work / f"p{index+1}.img"; image.new_sparse(p, size)
                    image.sparse_copy(self.media, p, offset, 0, size); filesystems.append(p)
            else:
                sectors = privileged.check_table(self.target.path, self.size)
                table = run(["sfdisk", "--json", self.target.path]).decode()
                layout = [(P1_START * 512, P1_SECTORS * 512), (0, sectors * 512)]
                filesystems = [Path(partition(self.target.path, n)) for n in (1, 2)]
            image.geometry(*filesystems)
            check_journal(filesystems[1], image=self.is_image,
                          device=None if self.is_image else self.target.path)
            if self.is_image or self.dry_run:
                p1, p2 = work / "p1", work / "p2"; p1.mkdir(); p2.mkdir()
                FatStore(filesystems[0]).snapshot(p1)
                kind = dump_contract(filesystems[1], p2)
                p2sum = region_hash(self.media, layout[1][0], layout[1][1]) if self.is_image else None
                signature = {"layout": layout, "table": table, "kind": kind, "p2_sha256": p2sum,
                             "fstab": (p2 / "etc/fstab").read_bytes().hex()}
                verify_root_size(self.size, layout[1][1] // 512, kind)
                yield p1, signature
            else:
                with privileged.workspace() as mounted:
                    self.forbidden_roots.append(mounted)
                    with card_mounts(self.target.path, mounted) as (p1, p2):
                        kind = card_kind(p2)
                        verify_p2_contract(p2, kind)
                        require(kind in ("test", "full"), "E_INSTALL_KIND",
                                "etc/dby-rootfs-kind content does not match the rootfs contract.")
                        verify_root_size(self.size, layout[1][1] // 512, kind)
                        signature = {"layout": layout, "table": table, "kind": kind,
                                     "fstab": (p2 / "etc/fstab").read_bytes().hex(),
                                     "blkid": [run(["blkid", "-p", "-o", "export", p]).decode() for p in filesystems]}
                        yield p1, signature

    @contextmanager
    def transaction(self):
        if self.is_image:
            dependencies(("mmd", "mren", "mdel", "mdeltree"))
            with tempfile.TemporaryDirectory(prefix="write-", dir=self.work) as temporary:
                part = Path(temporary) / "p1.img"; image.new_sparse(part, P1_SECTORS * 512)
                self.check_image_name()
                image.sparse_copy(self.media, part, P1_START * 512, 0, P1_SECTORS * 512)
                yield FatStore(part)
                self.check_image_name()
                replace_region(part, self.media, P1_START * 512, P1_SECTORS * 512)
        else:
            check_journal(Path(partition(self.target.path, 2)), device=self.target.path)
            with privileged.workspace() as work:
                self.forbidden_roots.append(work)
                with card_mounts(self.target.path, work, writable=True) as (p1, _):
                    yield DirectoryStore(p1)

    def restore_exact_image(self):
        if self.is_image:
            self.check_image_name()
            replace_region(self.original, self.media, P1_START * 512, P1_SECTORS * 512)
