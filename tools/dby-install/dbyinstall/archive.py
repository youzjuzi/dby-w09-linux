# SPDX-License-Identifier: GPL-2.0-only
"""Validate archives before writes and verify all installed member payloads."""

import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import tarfile

from .common import BundleError, FSTAB, file_record, inside, require, trace_command


def archive_name(name, *, directory=False):
    require(isinstance(name, str) and name and not name.startswith("/")
            and not any(ord(char) < 32 or ord(char) == 127 for char in name),
            "E_ROOTFS_PATH", "Rootfs member names must be relative and contain no control characters.")
    while name.startswith("./"):
        name = name[2:]
    if directory and name in ("", "."):
        return ""
    if directory and name.endswith("/"):
        name = name[:-1]
    require(name and all(part not in ("", ".", "..") for part in name.split("/")),
            "E_ROOTFS_PATH", "Rootfs member names must have canonical relative components.")
    return name


def confined_link(name, target, hard=False):
    require(target and not any(ord(char) < 32 or ord(char) == 127 for char in target), "E_ROOTFS_LINK", "Invalid archive link.")
    parts = [] if target.startswith("/") or hard else list(PurePosixPath(name).parent.parts)
    for part in PurePosixPath(target).parts:
        if part in ("/", ".", ""):
            continue
        if part == "..":
            require(parts, "E_ROOTFS_LINK", "Archive link escapes root.")
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)


def inspect_archive(path):
    entries = {}
    total = 0
    argv = ["zstd", "-q", "-d", "-c", str(path)]
    try:
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        trace_command(argv, None)
        raise BundleError("E_ROOTFS_READ", "Cannot decompress rootfs archive.") from None
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
            for item in archive:
                name = archive_name(item.name, directory=item.isdir())
                if not name:
                    require(item.isdir(), "E_ROOTFS_PATH", "Invalid root archive entry.")
                    continue
                require(name not in entries and not any(ord(c) < 32 or ord(c) == 127 for c in name),
                        "E_ROOTFS_PATH", "Duplicate or unsafe archive member.")
                require(item.isfile() or item.isdir() or item.issym() or item.islnk(),
                        "E_ROOTFS_TYPE", "Rootfs archive contains unsupported special files.")
                row = {"mode": item.mode & 0o7777, "uid": item.uid, "gid": item.gid,
                       "kind": "file" if item.isfile() else "dir" if item.isdir() else "symlink" if item.issym() else "hardlink",
                       "xattrs": {key.removeprefix("SCHILY.xattr."): value for key, value in item.pax_headers.items()
                                  if key.startswith("SCHILY.xattr.")},
                       "acls": {key: value for key, value in item.pax_headers.items()
                                if key.startswith("SCHILY.acl.")}}
                require(item.uid >= 0 and item.gid >= 0 and item.size <= 1024**4,
                        "E_ROOTFS_METADATA", "Invalid rootfs metadata.")
                if item.isfile():
                    digest = hashlib.sha256()
                    stream = archive.extractfile(item)
                    content = bytearray() if name in ("etc/fstab", "etc/dby-rootfs-kind") else None
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                        if content is not None:
                            require(len(content) + len(chunk) <= 4096, "E_ROOTFS_CONTRACT", "Rootfs contract file is too large.")
                            content.extend(chunk)
                    row.update(sha256=digest.hexdigest(), size=item.size)
                    total += item.size
                    if content is not None:
                        row["content"] = bytes(content)
                elif item.issym() or item.islnk():
                    row["target"] = item.linkname
                    row["resolved"] = confined_link(name, item.linkname, item.islnk())
                entries[name] = row
        # Drain any tar padding; otherwise a successful decoder can see SIGPIPE.
        while process.stdout.read(1024 * 1024):
            pass
        require(process.wait() == 0, "E_ROOTFS_READ", "Rootfs decompression failed.")
    except BundleError:
        raise
    except (tarfile.TarError, EOFError, ValueError):
        raise BundleError("E_ROOTFS_READ", "Cannot parse rootfs tar archive.") from None
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.terminate(); process.wait()
        trace_command(argv, process.returncode)
    for name, row in entries.items():
        for parent in PurePosixPath(name).parents:
            if str(parent) != "." and str(parent) in entries:
                require(entries[str(parent)]["kind"] == "dir", "E_ROOTFS_LINK", "An archive member traverses a non-directory.")
        if row["kind"] == "hardlink":
            require(row["resolved"] in entries and entries[row["resolved"]]["kind"] == "file",
                    "E_ROOTFS_LINK", "Hard link target must be an archived regular file.")
    require(entries, "E_ROOTFS_EMPTY", "Rootfs archive is empty.")
    if "etc/fstab" in entries:
        require(entries["etc/fstab"].get("content") == FSTAB, "E_ROOTFS_FSTAB", "Rootfs fstab violates the volume contract.")
    if "etc/dby-rootfs-kind" in entries:
        require(entries["etc/dby-rootfs-kind"].get("content") in (b"full\n", b"test\n"),
                "E_ROOTFS_KIND", "Invalid rootfs kind marker.")
    return entries, total


def verify_root(root, entries, *, ownership=True, xattrs=True, permissions=True, hardlinks=True):
    for name, row in entries.items():
        path = inside(root, name, follow_final=False)
        require(path.exists() or path.is_symlink(), "E_INSTALL_MISSING", "Installed rootfs member is missing.")
        metadata = path.lstat()
        if row["kind"] == "dir":
            require(stat.S_ISDIR(metadata.st_mode), "E_INSTALL_TYPE", "Installed directory type changed.")
        elif row["kind"] == "symlink":
            require(path.is_symlink() and os.readlink(path) == row["target"], "E_INSTALL_LINK", "Installed symbolic link changed.")
        else:
            require(stat.S_ISREG(metadata.st_mode), "E_INSTALL_TYPE", "Installed file is not regular.")
            expected = row if row["kind"] == "file" else entries[row["resolved"]]
            require(file_record(path) == {key: expected[key] for key in ("sha256", "size")},
                    "E_INSTALL_HASH", "Installed rootfs file digest does not match.")
            if row["kind"] == "hardlink" and hardlinks:
                require(metadata.st_ino == inside(root, row["resolved"]).stat().st_ino,
                        "E_INSTALL_LINK", "Installed hard link is not shared.")
        if row["kind"] != "symlink" and permissions:
            require(stat.S_IMODE(metadata.st_mode) == row["mode"], "E_INSTALL_MODE", "Installed file permissions changed.")
        if ownership:
            require((metadata.st_uid, metadata.st_gid) == (row["uid"], row["gid"]),
                    "E_INSTALL_OWNER", "Installed ownership changed.")
        if xattrs:
            for key, value in row["xattrs"].items():
                require(os.getxattr(path, key, follow_symlinks=False) == value.encode("utf-8", "surrogateescape"),
                        "E_INSTALL_XATTR", "Installed extended attribute changed.")
    require(inside(root, "etc/fstab").read_bytes() == FSTAB, "E_INSTALL_FSTAB", "Installed fstab violates the contract.")
