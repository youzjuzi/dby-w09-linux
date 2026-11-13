# SPDX-License-Identifier: GPL-2.0-only
"""Fail-closed target validation; live probes are injectable for host tests."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat

from .common import BundleError, MAX_SIZE, MIN_SIZE, no_links, partition_script, require, run


def live_probe(path, *, sysroot=Path("/sys"), mountinfo=Path("/proc/self/mountinfo"), execute=run):
    no_links(path)
    info = path.lstat()
    require(stat.S_ISBLK(info.st_mode), "E_TARGET_TYPE", "Target must be a whole block device.")
    node = sysroot / "class/block" / path.name
    require(not (node / "partition").exists(), "E_TARGET_PARTITION", "A partition is not a whole device.")
    rows = json.loads(execute(["lsblk", "--json", "--bytes", "--output",
        "PATH,TYPE,SIZE,RM,RO,MODEL,SERIAL,MAJ:MIN,LOG-SEC"]))["blockdevices"]
    matches = []
    def walk(items):
        for item in items:
            if item.get("path") == str(path):
                matches.append(item)
            walk(item.get("children", []))
    walk(rows)
    require(len(matches) == 1, "E_TARGET_IDENTITY", "Cannot identify one unique target.")
    row = matches[0]
    ids = set()
    def collect(folder):
        number = (folder / "dev").read_text().strip()
        if number in ids:
            return
        ids.add(number)
        for child in folder.iterdir():
            if (child / "partition").exists():
                collect(child)
        for child in (folder / "holders").glob("*"):
            collect(child)
    collect(node)
    mounts = []
    for line in mountinfo.read_text().splitlines():
        fields = line.split()
        require(len(fields) >= 6, "E_TARGET_PROBE", "Malformed mount inventory.")
        if fields[2] in ids:
            mounts.append(fields[2])
    media = (node / "device/type").read_text().strip() if (node / "device/type").is_file() else ""
    require(f"{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}" == (node / "dev").read_text().strip(),
            "E_TARGET_IDENTITY", "Device node does not match its sysfs identity.")
    row.update(block=True, partition=False, media=media, mounted=sorted(set(mounts)),
               sys_size=int((node / "size").read_text()) * 512,
               sys_removable=(node / "removable").read_text().strip() == "1",
               rdev=info.st_rdev, sys_number=(node / "dev").read_text().strip())
    return row


@dataclass
class BlockDeviceTarget:
    path: Path
    probe: object = live_probe

    def validate(self):
        self.path = Path(self.path)
        # MMC readers may report RM=0 for SD cards. Exact SD media type, not
        # the removable flag, excludes eMMC; USB-reader support stays separate.
        require(re.fullmatch(r"/dev/mmcblk[0-9]+", str(self.path)),
                "E_TARGET_MEDIA", "Only whole mmc SD media are supported.")
        row = self.probe(self.path)
        require(row.get("block") is True and row.get("type") == "disk",
                "E_TARGET_TYPE", "Target is not a whole block device.")
        require(not row.get("partition"), "E_TARGET_PARTITION", "Partitions cannot be selected.")
        require(row.get("media") == "SD", "E_TARGET_MEDIA", "Target is not SD media.")
        require(row.get("ro") in (False, 0), "E_TARGET_READONLY", "Target is read-only.")
        require(not row.get("mounted"), "E_TARGET_MOUNTED", "Mounted target/descendant device IDs: "
                + ", ".join(row.get("mounted", [])) + "; unmount manually.")
        size = int(row["size"])
        partition_script(size)
        require(row.get("sys_size") == size and int(row.get("log-sec", 0)) == 512,
                "E_TARGET_GEOMETRY", "Target geometry is inconsistent or unsupported.")
        require(row.get("sys_number") == row.get("maj:min"), "E_TARGET_IDENTITY", "Target identities disagree.")
        return row

    def confirm(self, before, ask=input):
        require(ask("Type the full target device name to erase it: ").strip() == str(self.path),
                "E_TARGET_CONFIRM", "Target name confirmation did not match.")
        require(self.validate() == before, "E_TARGET_CHANGED", "Target identity changed after confirmation.")


@dataclass
class ImageFileTarget:
    path: Path
    size: int = 0

    def validate(self, *, existing=False):
        self.path = Path(os.path.abspath(self.path))
        no_links(self.path)
        require(not self.path.is_relative_to(Path("/dev")), "E_IMAGE_PATH", "Image target must not be under /dev.")
        if existing:
            require(self.path.is_file(), "E_IMAGE_MISSING", "Image file is missing.")
            self.size = self.path.stat().st_size
        else:
            require(not self.path.exists(), "E_IMAGE_EXISTS", "Image output must not already exist.")
            require(self.path.parent.is_dir(), "E_IMAGE_PATH", "Image parent directory must exist.")
        partition_script(self.size)
        return {"size": self.size, "path": str(self.path), "kind": "image"}


def parse_size(value):
    match = re.fullmatch(r"([1-9][0-9]*)([MGT])", value)
    require(match is not None, "E_IMAGE_SIZE", "Image size must use M, G or T.")
    return int(match[1]) * 1024 ** {"M": 2, "G": 3, "T": 4}[match[2]]
