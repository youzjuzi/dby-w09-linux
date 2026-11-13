# SPDX-License-Identifier: GPL-2.0-only
"""Explicit build configuration and checked input inventories."""

from datetime import datetime
import json
import os
from pathlib import Path
import re
import stat

from .manifest import BundleError, file_record, relative_path, require, unique_object, valid_digest

HERE = Path(__file__).resolve().parents[1]
TOUCH_MODULE = "kernel/drivers/input/touchscreen/nt36523-thp.ko"
TOUCH_FIRMWARE = "firmware/novatek/nt36523-thp.bin"
TOUCH_CONFIG = "etc/modprobe.d/dby-w09-thp.conf"
VERMAGIC = "7.2.10 SMP preempt mod_unload aarch64"


def unlinked(path):
    require(not any(item.is_symlink() for item in (path, *path.parents)),
            "E_BUILD_LINK", "Input and output paths must not traverse symbolic links.")


def regular(path, *, private=False):
    unlinked(path)
    require(path.is_file(), "E_BUILD_INPUT", "A required input file is missing.")
    if private:
        require(path.stat().st_nlink == 1, "E_BUILD_LINK", "Private inputs must not have hard links.")
    return path


def read_json(path):
    regular(path)
    require(path.stat().st_size <= 1024 * 1024, "E_BUILD_CONFIG", "JSON input exceeds its size limit.")
    try:
        return json.loads(path.read_bytes(), object_pairs_hook=unique_object)
    except (ValueError, UnicodeError):
        raise BundleError("E_BUILD_CONFIG", "Cannot parse build JSON input.") from None


def config_path(base, value):
    require(isinstance(value, str) and value and not any(ord(char) < 32 for char in value),
            "E_BUILD_CONFIG", "Expected a nonempty input path.")
    path = Path(value)
    # Check the original path before lexical normalization of any '..'.
    path = path if path.is_absolute() else base / path
    unlinked(path)
    return Path(os.path.abspath(path))


def load_config(path):
    path = Path(os.path.abspath(path))
    data = read_json(path)
    required = {"kernel_build_dir", "kernel_source_dir", "dtb", "external_modules",
                "private_inputs", "toolchain", "touch", "output", "sources"}
    optional = {"modules_list", "epoch", "created", "sequence"}
    require(isinstance(data, dict) and required <= data.keys() and not data.keys() - required - optional,
            "E_BUILD_CONFIG", "Missing or unsupported build configuration fields.")
    result = dict(data)
    for key in ("kernel_build_dir", "kernel_source_dir", "dtb", "output"):
        result[key] = config_path(path.parent, data[key])
    result["modules_list"] = config_path(path.parent, data["modules_list"]) if "modules_list" in data else HERE / "modules.list"
    require(isinstance(data["external_modules"], dict), "E_BUILD_CONFIG", "Invalid external module mapping.")
    result["external_modules"] = {relative_path(key): config_path(path.parent, value)
                                  for key, value in data["external_modules"].items()}
    require(isinstance(data["private_inputs"], list), "E_BUILD_CONFIG", "Private inputs must be a directory list.")
    result["private_inputs"] = [config_path(path.parent, value) for value in data["private_inputs"]]
    require(len(set(result["private_inputs"])) == len(result["private_inputs"]),
            "E_BUILD_CONFIG", "Duplicate private input directory.")
    require(isinstance(data["touch"], dict) and data["touch"].keys() == {"enabled"}
            and type(data["touch"]["enabled"]) is bool,
            "E_BUILD_CONFIG", "Touch must provide a boolean enabled value.")
    require(isinstance(data["toolchain"], str) and data["toolchain"]
            and not any(char.isspace() for char in data["toolchain"]),
            "E_BUILD_CONFIG", "Invalid cross compiler prefix.")
    if "/" in data["toolchain"]:
        result["toolchain"] = str(config_path(path.parent, data["toolchain"]))
    try:
        result["epoch"] = data["epoch"] if "epoch" in data else int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    except ValueError:
        raise BundleError("E_BUILD_CONFIG", "Invalid source date epoch.") from None
    require(type(result["epoch"]) is int and 0 <= result["epoch"] <= 0xffffffff,
            "E_BUILD_CONFIG", "Source date epoch is outside the supported range.")
    result["sequence"] = data.get("sequence", 1)
    require(type(result["sequence"]) is int and result["sequence"] > 0,
            "E_BUILD_CONFIG", "Sequence must be a positive integer.")
    if "created" in data:
        require(isinstance(data["created"], str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", data["created"]),
                "E_BUILD_CONFIG", "Creation time must use UTC second precision.")
        try:
            datetime.strptime(data["created"], "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            raise BundleError("E_BUILD_CONFIG", "Invalid creation time.") from None
    output = result["output"]
    require(not output.exists(), "E_BUILD_EXISTS", "Output must not already exist.")
    require(output.parent.is_dir(), "E_BUILD_OUTPUT", "Output parent directory must already exist.")
    inputs = [path, HERE, result["kernel_build_dir"], result["kernel_source_dir"], result["dtb"],
              result["modules_list"], *result["private_inputs"], *result["external_modules"].values()]
    require(not any(output.is_relative_to(item) or item.is_relative_to(output) for item in inputs),
            "E_BUILD_OVERLAP", "Output must not overlap an input.")
    return result


def selected_modules(path, touch):
    regular(path)
    modules = [line.strip() for line in path.read_text().splitlines()
               if line.strip() and not line.lstrip().startswith("#")]
    require(modules and len(modules) == len(set(modules)), "E_BUILD_MODULES", "Invalid module list.")
    for name in modules:
        relative_path(name)
        require(name.startswith("kernel/") and name.endswith(".ko")
                and re.fullmatch(r"[A-Za-z0-9_./+-]+", name) and not forbidden_touch(name),
                "E_BUILD_MODULES", "Unsupported module destination.")
    if touch:
        require(TOUCH_MODULE in modules, "E_BUILD_TOUCH", "Enabled touch requires its module in the list.")
    return [name for name in modules if touch or name != TOUCH_MODULE]


def forbidden_touch(name):
    lower = name.lower()
    if "nt36523-ts" in lower or "nt36523_ts" in lower:
        return True
    if "nt36523-thp" in lower:
        return name not in (TOUCH_MODULE, TOUCH_FIRMWARE)
    return "firmware/novatek" in lower and name != TOUCH_FIRMWARE


def private_inventory(root):
    """Read old schema-1 locks without trusting any historical hash registry."""
    unlinked(root)
    require(root.is_dir() and stat.S_IMODE(root.stat().st_mode) == 0o700,
            "E_BUILD_PRIVATE", "Private input directories must have mode 0700.")
    lock = root / "inputs.json"
    regular(lock, private=True)
    document = read_json(lock)
    require(isinstance(document, dict) and document.get("schema") == 1
            and type(document.get("schema")) is int and isinstance(document.get("files"), dict)
            and document["files"] and not document.keys() - {"schema", "files", "contents_sha256"},
            "E_BUILD_PRIVATE", "Invalid private input lock.")
    if "contents_sha256" in document:
        require(valid_digest(document["contents_sha256"]), "E_BUILD_PRIVATE", "Invalid private lock summary.")
    directories, files = set(), {}
    for name, record in document["files"].items():
        relative_path(name)
        require(re.fullmatch(r"[A-Za-z0-9_./+-]+", name)
                and (name.startswith("firmware/") or name.startswith("rootfs/etc/"))
                and not forbidden_touch(name) and name != "rootfs/etc/modprobe.d/dby-safe-mode.conf",
                "E_BUILD_PRIVATE", "Unsupported locked payload destination.")
        require(isinstance(record, dict) and record.keys() == {"sha256", "mode"}
                and valid_digest(record["sha256"]) and type(record["mode"]) is int
                and 0 <= record["mode"] <= 0o777,
                "E_BUILD_PRIVATE", "Invalid private payload record.")
        source = regular(root / name, private=True)
        require(file_record(source)["sha256"] == record["sha256"],
                "E_BUILD_PRIVATE_HASH", "Private payload SHA-256 does not match its lock.")
        require(stat.S_IMODE(source.stat().st_mode) == record["mode"],
                "E_BUILD_PRIVATE_MODE", "Private payload permissions do not match its lock.")
        files[name] = {"path": source, **record}
        directories.update(parent.as_posix() for parent in Path(name).parents if parent.parts)
    actual = set()
    for path in root.rglob("*"):
        require(not path.is_symlink(), "E_BUILD_LINK", "Private input links are forbidden.")
        name = path.relative_to(root).as_posix()
        if path.is_dir():
            require(name in directories, "E_BUILD_PRIVATE", "Unexpected private input directory.")
        else:
            regular(path, private=True)
            actual.add(name)
    require(actual == files.keys() | {"inputs.json"}, "E_BUILD_PRIVATE", "Unexpected private input file.")
    return files, lock
