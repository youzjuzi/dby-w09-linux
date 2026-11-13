# SPDX-License-Identifier: GPL-2.0-only
"""Deterministic payload staging, squashfs and initramfs packaging."""

import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat

from .manifest import file_record, relative_path, require
from .build_inputs import HERE, TOUCH_CONFIG, TOUCH_FIRMWARE, forbidden_touch, regular


def put(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)
    path.chmod(mode)


def overlay(tree, touch):
    source = HERE / "device-rootfs"
    expected_links = set((source / "wants-links.txt").read_text().splitlines())
    actual_links = set()
    for path in sorted(source.rglob("*")):
        name = path.relative_to(source).as_posix()
        relative_path(name)
        if name == "wants-links.txt" or (not touch and name == TOUCH_CONFIG):
            continue
        target = tree / name
        if path.is_symlink():
            require(name.startswith("etc/") and name[4:] in expected_links
                    and os.readlink(path) == "../" + path.name,
                    "E_BUILD_OVERLAY", "Unsupported public overlay link.")
            actual_links.add(name[4:])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(os.readlink(path))
            require(target.is_file(), "E_BUILD_OVERLAY", "Overlay service link target is missing.")
        elif path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            regular(path)
            put(target, path.read_bytes(), 0o755 if name.startswith("bin/") else 0o644)
    require(actual_links == expected_links, "E_BUILD_OVERLAY", "Service enablement links do not match their list.")


def merge_private(tree, inventories, touch):
    seen = set()
    for files, _ in inventories:
        for name, row in sorted(files.items()):
            if not touch and name == TOUCH_FIRMWARE:
                continue
            destination = name.removeprefix("rootfs/")
            target = tree / destination
            require(destination not in seen and not target.exists() and not target.is_symlink()
                    and not any(parent.is_symlink() or (parent.exists() and not parent.is_dir())
                                for parent in target.parents),
                    "E_BUILD_COLLISION", "Private overlay collides with another payload.")
            seen.add(destination)
            put(target, row["path"].read_bytes(), row["mode"])
    return seen


def contents(tree):
    lines = []
    for path in sorted(tree.rglob("*")):
        name = path.relative_to(tree).as_posix()
        if name == "DEVICE-CONTENTS.txt":
            continue
        mode = stat.S_IMODE(path.lstat().st_mode)
        digest = (hashlib.sha256(os.readlink(path).encode()).hexdigest() if path.is_symlink()
                  else file_record(path)["sha256"] if path.is_file() else hashlib.sha256(b"").hexdigest())
        lines.append(f"{name}\t{mode:04o}\t{digest}\n")
    return "".join(lines).encode()


def squash(tree, output, epoch, run):
    paths = sorted(path.relative_to(tree).as_posix() for path in tree.rglob("*"))
    require(not any(any(char.isspace() for char in name) for name in paths),
            "E_BUILD_PATH", "Squashfs paths must not contain whitespace.")
    order = output.parent / "squash.sort"
    put(order, "".join(f"{name} {len(paths)-index}\n" for index, name in enumerate(paths)))
    run(["mksquashfs", tree, output, "-comp", "gzip", "-noappend", "-all-root", "-no-xattrs",
         "-processors", "1", "-mkfs-time", str(epoch), "-all-time", str(epoch),
         "-sort", order, "-no-progress"], "squashfs")
    order.unlink()


def newc(template, init):
    require(isinstance(template, list), "E_BUILD_INITRAMFS", "Invalid initramfs entry template.")
    output = bytearray()
    names = []
    for index, row in enumerate(template, 1):
        require(isinstance(row, list) and len(row) == 4 and isinstance(row[0], str)
                and all(type(value) is int and 0 <= value <= 0xffffffff for value in row[1:]),
                "E_BUILD_INITRAMFS", "Invalid initramfs entry.")
        name, mode, major, minor = row
        if name not in (".", "TRAILER!!!"):
            relative_path(name)
        require(name not in names, "E_BUILD_INITRAMFS", "Duplicate initramfs entry.")
        names.append(name)
        data = init if name == "init" else b""
        fields = [index, mode, 0, 0, 1, 0, len(data), 0, 0, major, minor, len(name.encode()) + 1, 0]
        output += b"070701" + "".join(f"{value:08x}" for value in fields).encode() + name.encode() + b"\0"
        output += b"\0" * (-len(output) % 4)
        output += data
        output += b"\0" * (-len(output) % 4)
    require(names.count("init") == 1 and names[-1:] == ["TRAILER!!!"],
            "E_BUILD_INITRAMFS", "Template requires one init and a final trailer.")
    return bytes(output)


def initramfs(tree, boot, work, compiler, run):
    configs = sorted(path.relative_to(tree / "etc").as_posix() for path in (tree / "etc").rglob("*")
                     if path.is_file() or path.is_symlink())
    header = ('#define DEVICE_FILE "UBOOT/dby-device.img"\n#define DEVICE_SHA256 "'
              + file_record(boot / "dby-device.img")["sha256"] + '"\n')
    header += f"#define DEVICE_CONFIG_COUNT {len(configs)}\nstatic const char *const device_configs[] = {{\n"
    header += "".join(json.dumps(name) + ",\n" for name in configs) + "};\n"
    put(work / "device-pack.h", header)
    run([compiler, "-static", "-Os", "-Wall", "-Wextra", "-Werror",
         "-Wl,--build-id=none", "-I", work, "-o", work / "init", HERE / "initramfs/init.c"], "initramfs-compile")
    template = json.loads((HERE / "initramfs/entries.json").read_bytes())
    put(boot / "initrd.img", gzip.compress(newc(template, (work / "init").read_bytes()), mtime=0))


def kernel_image(source, destination):
    with source.open("rb") as stream:
        prefix = stream.read(64)
        require(prefix[0x38:0x3c] == b"ARMd" and not prefix.startswith(b"\x1f\x8b"),
                "E_BUILD_IMAGE", "Expected an uncompressed ARM64 Image.")
        stream.seek(0)
        with destination.open("wb") as output:
            with gzip.GzipFile(filename="", mode="wb", fileobj=output, compresslevel=9, mtime=0) as zipped:
                shutil.copyfileobj(stream, zipped, length=1024 * 1024)
