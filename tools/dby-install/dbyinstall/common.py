# SPDX-License-Identifier: GPL-2.0-only
"""Shared contracts and target-root-confined file access."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys

BUNDLE_TOOL = Path(__file__).resolve().parents[2] / "dby-bundle"
sys.path.insert(0, str(BUNDLE_TOOL))
from dbybundle import BundleError, verify as verify_bundle
from dbybundle.manifest import BOOT_FILES, file_record, relative_path, require, unique_object, validate_metadata

SECTOR = 512
P1_START, P1_SECTORS = 2048, 2097152
P2_START = P1_START + P1_SECTORS
MIN_SIZE, MAX_SIZE = 8 * 1024**3, 2 * 1024**4
MIN_P2_SIZE, DEFAULT_P2_SIZE = 1024**3, 4 * 1024**3
FSTAB = b"LABEL=ubuntu-root / ext4 defaults 0 1\n"


def command_name(argv):
    name = Path(argv[0]).name if argv else "command"
    return name if re.fullmatch(r"[A-Za-z0-9_.+-]+", name) else "command"


class CommandError(BundleError):
    """Structured status without command arguments or captured output."""

    def __init__(self, argv, returncode):
        self.command = command_name(argv)
        self.returncode = returncode
        if returncode is None:
            self.category = "not-started"
            message = f"{self.command} could not start (not-started; no exit code)."
        else:
            self.category = "command-failed"
            if returncode < 0:
                self.category = "signal"
            elif self.command == "udevadm" and argv[1:2] == ["settle"] and returncode == 1:
                self.category = "timeout"
            elif self.command == "blockdev" and argv[1:2] == ["--rereadpt"]:
                self.category = "reread-failed"
            message = f"{self.command} exited {returncode} ({self.category})."
        super().__init__("E_INSTALL_COMMAND", message)


def trace_command(argv, returncode):
    if os.environ.get("DBY_TRACE") == "1":
        status = "not started" if returncode is None else f"exit {returncode}"
        print(f"[trace] {command_name(argv)} -> {status}", file=sys.stderr)


def run(argv, *, data=None):
    argv = list(map(str, argv))
    try:
        result = subprocess.run(argv, input=data, stdin=None if data is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    except OSError:
        trace_command(argv, None)
        raise CommandError(argv, None) from None
    trace_command(argv, result.returncode)
    if result.returncode != 0:
        raise CommandError(argv, result.returncode)
    return result.stdout


def dependencies(names):
    missing = [name for name in names if shutil.which(name) is None]
    require(not missing, "E_INSTALL_DEPENDENCY", "Missing dependencies: " + ", ".join(missing) + ".")


def no_links(path):
    require(not any(p.is_symlink() for p in (path, *path.parents)),
            "E_INSTALL_LINK", "Symbolic links in host paths are forbidden.")


def inside(root, name, *, follow_final=True):
    """Resolve merged-/usr and absolute symlinks inside the target, never host /."""
    root = Path(root).absolute()
    parts = list(PurePosixPath(name.lstrip("/")).parts)
    done, links = [], 0
    while parts:
        part = parts.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            require(done, "E_ROOT_PATH", "Path escapes the target root.")
            done.pop()
            continue
        path = root.joinpath(*done, part)
        if path.is_symlink() and (parts or follow_final):
            links += 1
            require(links <= 40, "E_ROOT_PATH", "Too many target-root symbolic links.")
            target = os.readlink(path)
            if target.startswith("/"):
                done = []
            parts = list(PurePosixPath(target).parts)[int(target.startswith("/")):] + parts
        else:
            done.append(part)
    return root.joinpath(*done)


def root_sectors(size, *, minimal=False, p2_size=None):
    require(type(size) is int and MIN_SIZE <= size <= MAX_SIZE and size % SECTOR == 0,
            "E_TARGET_SIZE", "Target capacity must be aligned and between 8 GiB and 2 TiB.")
    require(minimal or p2_size is None, "E_INSTALL_P2_MODE", "--p2-size requires --minimal.")
    available = size // SECTOR - P2_START
    if not minimal:
        return available
    chosen = DEFAULT_P2_SIZE if p2_size is None else p2_size
    require(type(chosen) is int and MIN_P2_SIZE <= chosen <= available * SECTOR and chosen % SECTOR == 0,
            "E_INSTALL_P2_SIZE", "Test p2 must be aligned, at least 1 GiB, and fit the target.")
    return chosen // SECTOR


def partition_script(size, *, minimal=False, p2_size=None):
    sectors = root_sectors(size, minimal=minimal, p2_size=p2_size)
    return ("label: dos\nunit: sectors\n\nstart=2048, size=2097152, type=c\n"
            f"start=2099200, size={sectors}, type=83\n")


def verify_root_size(size, sectors, kind):
    root_sectors(size, minimal=True, p2_size=sectors * SECTOR)
    require(kind == "test" or sectors == root_sectors(size), "E_INSTALL_TABLE", "Full rootfs must use p2 to the target end.")


def partition(device, number):
    return str(device) + ("p" if str(device)[-1].isdigit() else "") + str(number)


def read_json(path):
    try:
        require(path.stat().st_size <= 1024 * 1024, "E_INSTALL_JSON", "Installed metadata exceeds its size limit.")
        return json.loads(path.read_bytes(), object_pairs_hook=unique_object)
    except BundleError:
        raise
    except (ValueError, UnicodeError):
        raise BundleError("E_INSTALL_JSON", "Cannot parse installed metadata.") from None
