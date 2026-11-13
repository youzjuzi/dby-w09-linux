# SPDX-License-Identifier: GPL-2.0-only
"""Build-only network settings and bounded, privacy-filtered progress."""

from contextlib import contextmanager
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit

from .manifest import BundleError, require

APT_OPTIONS = ["-o", "Acquire::http::Timeout=30", "-o", "Acquire::https::Timeout=30", "-o", "Acquire::Retries=3"]


def mirror_url(value):
    """Keep URL rules equivalent to the standalone seed applier."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
        decoded = unquote(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            return None
        if parsed.query or parsed.fragment or not decoded or len(decoded.encode("utf-8")) > 2048:
            return None
        if any(ord(c) < 32 or ord(c) == 127 or c.isspace() for c in decoded) or "\\" in decoded:
            return None
        if any(part in (".", "..") for part in unquote(parsed.path).split("/")) or port is not None and not 1 <= port <= 65535:
            return None
        return value.rstrip("/")
    except (ValueError, UnicodeError):
        return None


@contextmanager
def temporary_mirror(path, mirror):
    if mirror is None:
        yield
        return
    require(mirror_url(mirror) is not None, "E_ROOTFS_MIRROR", "Invalid build mirror URL.")
    try:
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise BundleError("E_ROOTFS_MIRROR", "Build mirror requires a regular ubuntu.sources file.") from None
    with os.fdopen(fd, "r+b") as stream:
        metadata = os.fstat(stream.fileno())
        require(stat.S_ISREG(metadata.st_mode) and metadata.st_size <= 1024 * 1024,
                "E_ROOTFS_MIRROR", "Build source file is not bounded regular data.")
        original = stream.read()
        lines, skipping, count = [], False, 0
        for line in original.splitlines(keepends=True):
            if re.match(br"URIs\s*:", line, re.I):
                ending = b"\r\n" if line.endswith(b"\r\n") else b"\n"
                lines.append(b"URIs: " + mirror.encode() + ending); skipping = True; count += 1
            elif skipping and line.startswith((b" ", b"\t")):
                continue
            else:
                skipping = False; lines.append(line)
        require(count, "E_ROOTFS_MIRROR", "Build source file has no URI field.")
        def replace(data):
            stream.seek(0); stream.write(data); stream.truncate(); stream.flush(); os.fsync(stream.fileno())
        try:
            replace(b"".join(lines))
            yield
        finally:
            # The same inode retains uid/gid and mode; no mirror backup enters rootfs.
            replace(original)
            current = path.lstat() if path.exists() or path.is_symlink() else None
            if current is None or (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
                # A package may atomically replace its configuration during install.
                temporary_fd, temporary_name = tempfile.mkstemp(prefix=".dby-source-", dir=path.parent)
                temporary = Path(temporary_name)
                try:
                    with os.fdopen(temporary_fd, "wb") as restored:
                        restored.write(original); restored.flush(); os.fsync(restored.fileno())
                        owner = os.fstat(restored.fileno())
                        if (owner.st_uid, owner.st_gid) != (metadata.st_uid, metadata.st_gid):
                            os.fchown(restored.fileno(), metadata.st_uid, metadata.st_gid)
                        os.fchmod(restored.fileno(), stat.S_IMODE(metadata.st_mode))
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)


def progress(stage, message=""):
    print(stage + (": " + message if message else ""), file=sys.stderr, flush=True)


def tool_progress(stage, line):
    """Only publish recognized progress fields, never arbitrary child output."""
    if stage == "rootfs-apt":
        match = re.match(r"^(Reading package lists|Building dependency tree|Reading state information|Reading database)(?:[ .]|$)", line)
        if match:
            return match[1] + "..."
        match = re.match(r"^(Unpacking|Setting up|Removing|Purging configuration files for|Processing triggers for|Selecting previously unselected package) ([a-z0-9][a-z0-9+.:~-]*)(?:[ (]|$)", line)
        if match:
            return match[1] + " " + match[2]
        match = re.match(r"^(Hit|Get|Ign|Err):([0-9]+)\s", line)
        if match:
            return match[1] + ":" + match[2] + " [repository]"
        match = re.match(r"^(Fetched|Need to get) ([0-9.]+ [kMG]?B)(?:[ /]|$)", line)
        if match:
            return match[1] + " " + match[2]
        match = re.match(r"^([0-9]+)%\s", line)
        if match:
            return match[1] + "%"
        if line.startswith(("E:", "W:")):
            return "APT reported " + ("an error." if line.startswith("E:") else "a warning.")
    elif stage in ("rootfs-enable", "rootfs-mask", "rootfs-default"):
        for prefix in ("Created symlink", "Removed"):
            if line.startswith(prefix + " "):
                return "systemctl: " + prefix + "."
    elif stage in ("rootfs-extract", "rootfs-iso"):
        match = re.search(r"(?:created|Read checkpoint) ([0-9]+)(?: (files|directories|symlinks|inodes))?", line, re.I)
        if match:
            return "extraction: " + match[1] + (" " + match[2] if match[2] else " records")
        if line.startswith(("tar:", "FATAL ERROR:", "ERROR:")):
            return "Extraction tool reported a diagnostic."
    return None


def run(argv, stage, *, stdout=None, error_code="E_BUILD_COMMAND"):
    progress(stage, "starting")
    environment = os.environ.copy()
    environment.update(LC_ALL="C", TZ="UTC", DEBIAN_FRONTEND="noninteractive", TERM="dumb")
    try:
        child = subprocess.Popen(list(map(str, argv)), env=environment, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE if stdout is None else stdout,
                                 stderr=subprocess.STDOUT if stdout is None else subprocess.PIPE, start_new_session=True)
    except OSError:
        raise BundleError(error_code, f"Cannot execute {stage}; raw command withheld.") from None
    pipe = child.stdout if stdout is None else child.stderr
    pending = b""
    def emit(data):
        message = tool_progress(stage, data.decode("utf-8", "replace"))
        if message:
            progress(stage, message)
    try:
        while chunk := pipe.read1(4096):
            pending += chunk
            while match := re.search(br"[\r\n]", pending):
                emit(pending[:match.start()]); pending = pending[match.end():]
            # Long opaque lines are discarded instead of retained or echoed.
            if len(pending) > 8192:
                pending = b""
        if pending:
            emit(pending)
        status = child.wait()
    finally:
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()
        pipe.close()
    require(status == 0, error_code, f"Stage {stage} failed; raw output withheld.")
    progress(stage, "completed")


@contextmanager
def workspace(parent, mounted):
    work = Path(tempfile.mkdtemp(prefix=".dby-rootfs-", dir=parent)); work.chmod(0o755)
    try:
        yield work
    finally:
        try:
            mount_inventory = mounted()
        except OSError:
            progress("rootfs-cleanup", "Cannot inspect mounts; workspace retained beside requested output: " + work.name)
            raise BundleError("E_ROOTFS_CLEANUP", "Cannot confirm unmount; workspace retained, no recursive removal.") from None
        if any(path == work or path.is_relative_to(work) for path in mount_inventory):
            progress("rootfs-cleanup", "Mount remains; workspace retained beside requested output: " + work.name)
            raise BundleError("E_ROOTFS_CLEANUP", "Build mounts remain; workspace retained, no recursive removal.")
        try:
            shutil.rmtree(work)
        except OSError:
            progress("rootfs-cleanup", "Workspace retained beside requested output: " + work.name)
            raise BundleError("E_ROOTFS_CLEANUP", "Cannot remove build workspace; retained for manual cleanup.") from None
        progress("rootfs-cleanup", "Temporary workspace removed; no build mounts remain.")
