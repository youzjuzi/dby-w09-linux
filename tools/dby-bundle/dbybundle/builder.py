# SPDX-License-Identifier: GPL-2.0-only
"""Offline, unprivileged construction of the device-package bundle stage."""

import ctypes
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from .build_inputs import (HERE, TOUCH_CONFIG, TOUCH_FIRMWARE, TOUCH_MODULE, VERMAGIC,
                           load_config, private_inventory, regular, selected_modules, unlinked)
from .build_archive import contents, initramfs, kernel_image, merge_private, overlay, put, squash
from .manifest import (BundleError, create_manifest, file_record, io_errors, redact_sources,
                       relative_path, require, verify, write_manifest)


def run(argv, stage):
    """Keep command output private: tools can echo input paths or identities."""
    environment = os.environ.copy()
    for key in list(environment):
        if key.startswith(("KBUILD_", "INSTALL_MOD_", "KCONFIG_")) or key in {
            "SOURCE_DATE_EPOCH", "MAKEFLAGS", "MFLAGS", "GNUMAKEFLAGS", "MAKEFILES",
            "M", "O", "ARCH", "CROSS_COMPILE", "KERNELRELEASE", "MODLIB", "CDPATH"}:
            environment.pop(key)
    environment.update(LC_ALL="C", TZ="UTC")
    try:
        result = subprocess.run(list(map(str, argv)), env=environment, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    except OSError:
        raise BundleError("E_BUILD_COMMAND", f"Cannot execute {stage}; tool output withheld.") from None
    require(result.returncode == 0, "E_BUILD_COMMAND", f"Stage {stage} failed; tool output withheld.")
    return result.stdout


def tool(name, label):
    found = shutil.which(name)
    require(found is not None, "E_BUILD_DEPENDENCY", f"Missing build dependency: {label}.")
    return Path(found).resolve()


def tree_inputs():
    paths = [HERE / "build-bundle", HERE / "modules.list"]
    paths += sorted((HERE / "dbybundle").glob("*.py"))
    paths += sorted((HERE / "initramfs").glob("*"))
    paths += sorted(path for path in (HERE / "device-rootfs").rglob("*") if not path.is_dir())
    return paths


def snapshot(paths):
    result = {}
    for path in sorted(set(paths)):
        if path.is_symlink():
            result[path] = {"sha256": hashlib.sha256(os.readlink(path).encode()).hexdigest(), "mode": "link"}
        else:
            regular(path)
            result[path] = {**file_record(path), "mode": path.stat().st_mode & 0o777}
    return result


def public_summary(paths):
    records = snapshot(paths)
    # Host checkout permission bits do not affect normalized overlay output.
    data = [(path.relative_to(HERE).as_posix(), record["sha256"]) for path, record in records.items()]
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def touch_parameters(module, execute):
    expected = {"managed": "bool", "settle_ms": "uint", "boot_wait_ms": "uint", "skip_probe": "bool",
                "edge_px": "uint", "filter": "uint", "lock_px": "uint", "confirm": "uint", "multi": "uint",
                "guard": "uint"}
    lines = execute(["modinfo", "-F", "parmtype", module], "module-parameters").decode().splitlines()
    require(len(lines) == len(expected) and all(line.count(":") == 1 for line in lines)
            and dict(line.split(":") for line in lines) == expected,
            "E_BUILD_TOUCH", "Touch module does not implement the configured parameter contract.")


def check_dependencies(mod, modules):
    lines = (mod / "modules.dep").read_text().splitlines()
    entries = set()
    for line in lines:
        require(line.count(":") == 1, "E_BUILD_DEPMOD", "Malformed module dependencies.")
        name, dependencies = line.split(":")
        require(name not in entries, "E_BUILD_DEPMOD", "Duplicate module dependency record.")
        entries.add(name)
        for item in (name, *dependencies.split()):
            relative_path(item)
            require(item in modules and (mod / item).is_file(),
                    "E_BUILD_DEPMOD", "Unresolved module dependency.")
    require(entries == set(modules), "E_BUILD_DEPMOD", "Selected modules are missing from depmod output.")
    aliases = set((mod / "modules.alias").read_text().splitlines())
    if TOUCH_MODULE in modules:
        require({"alias spi:nt36523-thp nt36523_thp", "alias of:N*T*Cnovatek,nt36523-thp nt36523_thp"} <= aliases,
                "E_BUILD_TOUCH", "Touch module aliases are missing.")
    if "kernel/drivers/power/supply/dby-fg-readonly.ko" in modules:
        require("alias of:N*T*Cdby,pm8150b-fg-readonlyC* dby_fg_readonly" in aliases,
                "E_BUILD_DEPMOD", "Fuel gauge module alias is missing.")


def publish(source, destination):
    """Linux no-replace directory rename keeps an existing output untouched."""
    library = ctypes.CDLL(None, use_errno=True)
    require(hasattr(library, "renameat2"), "E_BUILD_PUBLISH", "No-replace directory publication is unavailable.")
    rename = library.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1):
        number = ctypes.get_errno()
        code = "E_BUILD_EXISTS" if number in (errno.EEXIST, errno.ENOTEMPTY) else "E_BUILD_PUBLISH"
        raise BundleError(code, "Cannot publish bundle without replacing existing data.")


@io_errors
def build(config_file, *, execute=run, emit=print):
    config = load_config(config_file)
    touch = config["touch"]["enabled"]
    modules = selected_modules(config["modules_list"], touch)
    external = config["external_modules"]
    require(not external.keys() - (set(modules) | ({TOUCH_MODULE} if not touch else set())),
            "E_BUILD_MODULES", "External module destination is not selected.")
    kernel, source = config["kernel_build_dir"], config["kernel_source_dir"]
    for directory in (kernel, source):
        unlinked(directory)
        require(directory.is_dir(), "E_BUILD_INPUT", "Kernel input directory is missing.")
    required = [kernel / name for name in (".config", "include/config/kernel.release", "modules.order",
                "modules.builtin", "modules.builtin.modinfo", "Module.symvers", "arch/arm64/boot/Image")]
    required += [source / "Makefile", config["dtb"], config["modules_list"], *external.values()]
    required += [source / name for name in ("scripts/Makefile.modinst", "scripts/Kbuild.include", "scripts/depmod.sh")]
    for path in required:
        regular(path)
    require((kernel / "include/config/kernel.release").read_text().strip() == "7.2.10",
            "E_BUILD_RELEASE", "Kernel release must be 7.2.10.")
    kernel_config = (kernel / ".config").read_text().splitlines()
    require(not {"CONFIG_MODULE_SIG_ALL=y", "CONFIG_MODULE_COMPRESS_ALL=y"} & set(kernel_config),
            "E_BUILD_CONFIG", "Module signing and compression during installation must be disabled.")
    require({"CONFIG_SQUASHFS=y", "CONFIG_SQUASHFS_ZLIB=y", "CONFIG_BLK_DEV_LOOP=y", "CONFIG_VFAT_FS=y"} <= set(kernel_config),
            "E_BUILD_CONFIG", "Kernel lacks a built-in device-package filesystem feature.")
    inventories = [private_inventory(path) for path in config["private_inputs"]]
    firmware_count = sum(TOUCH_FIRMWARE in files for files, _ in inventories)
    require(not touch or firmware_count == 1, "E_BUILD_TOUCH", "Enabled touch requires exactly one locked firmware input.")
    source_metadata = redact_sources(config["sources"])
    require(source_metadata.keys() == {"repositories", "upstream"}
            and source_metadata["repositories"].keys() == {"linux", "kernel", "uboot"}
            and source_metadata["upstream"], "E_BUILD_CONFIG", "Provide all repository commits and upstream provenance.")
    tools = [tool(name, name) for name in ("make", "modinfo", "depmod", "mksquashfs")]
    compiler = tool(config["toolchain"] + "gcc", "cross compiler")
    tools.append(compiler)
    tools += [tool(config["toolchain"] + name, "cross binutils") for name in ("as", "ld")]
    for library in ("libc.a", "libgcc.a", "libgcc_eh.a", "crt1.o", "crti.o", "crtn.o", "crtbeginT.o", "crtend.o"):
        path = Path(execute([compiler, "-print-file-name=" + library], "compiler-runtime").decode().strip()).resolve()
        tools.append(regular(path))
    all_modules = sorted(kernel.rglob("*.ko"))
    sources = {name: external.get(name, kernel / name.removeprefix("kernel/")) for name in modules}
    for path in set([*all_modules, *sources.values(), *external.values()]):
        regular(path)
        magic = execute(["modinfo", "-F", "vermagic", path], "module-vermagic").decode().strip()
        require(magic == VERMAGIC, "E_BUILD_VERMAGIC", "Module vermagic does not match the kernel contract.")
    if touch:
        touch_parameters(sources[TOUCH_MODULE], execute)
    tracked = required + all_modules + list(sources.values()) + tools + tree_inputs()
    for files, lock in inventories:
        tracked += [lock, *(row["path"] for row in files.values())]
    before = snapshot(tracked)
    epoch = config["epoch"]
    created = config.get("created", datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    # Export role names and digests, never source paths or private contents.
    source_metadata["inputs"] = {"build-options": hashlib.sha256(json.dumps(
        {"epoch": epoch, "touch": touch, "vermagic": VERMAGIC}, sort_keys=True).encode()).hexdigest()}
    source_metadata["inputs"]["builder"] = public_summary(tree_inputs())
    roles = {"kernel-config": kernel / ".config", "kernel-release": kernel / "include/config/kernel.release",
             "kernel-image": kernel / "arch/arm64/boot/Image", "dtb": config["dtb"],
             "kernel-symbols": kernel / "Module.symvers", "modules-list": config["modules_list"],
             "modules-order": kernel / "modules.order", "modules-builtin": kernel / "modules.builtin",
             "modules-builtin-modinfo": kernel / "modules.builtin.modinfo"}
    roles.update({f"source-rule-{index}": source / name for index, name in enumerate(
        ("Makefile", "scripts/Makefile.modinst", "scripts/Kbuild.include", "scripts/depmod.sh"))})
    roles.update({f"tool-{index:02d}": path for index, path in enumerate(tools)})
    roles.update({f"build-module-{index:04d}": path for index, path in enumerate(all_modules)})
    roles.update({f"selected-module-{index:04d}": sources[name] for index, name in enumerate(modules)})
    source_metadata["inputs"].update({role: before[path]["sha256"] for role, path in roles.items()})
    source_metadata["private_inputs"] = [{"sha256": before[path]["sha256"]}
        for files, lock in inventories for path in (lock, *(row["path"] for row in files.values()))]
    emit("Checked inputs; building device-package stage.")
    output = config["output"]
    with tempfile.TemporaryDirectory(prefix=".dby-build-", dir=output.parent) as temporary:
        work = Path(temporary)
        bundle = work / "bundle"
        boot = bundle / "p1/UBOOT"
        boot.mkdir(parents=True)
        bundle.chmod(0o700)
        installed = work / "install"
        execute(["make", "-C", source, "O=" + str(kernel), "ARCH=arm64", "CROSS_COMPILE=" + config["toolchain"],
                 "modules_install", "INSTALL_MOD_PATH=" + str(installed), "INSTALL_MOD_STRIP=", "DEPMOD=true",
                 "CONFIG_MODULE_SIG_ALL=", "CONFIG_MODULE_COMPRESS_ALL="], "modules-install")
        installed_modules = installed / "lib/modules/7.2.10"
        if not installed_modules.is_dir():
            installed_modules = installed / "usr/lib/modules/7.2.10"
        tree = work / "device-root"
        mod = tree / "modules/7.2.10"
        mod.mkdir(parents=True)
        for name in modules:
            original = sources[name]
            path = external.get(name, installed_modules / name)
            regular(path)
            require(file_record(path) == file_record(original), "E_BUILD_MODULE_CHANGED", "Installed module differs from its input.")
            put(mod / name, path.read_bytes())
        for name in ("modules.builtin", "modules.builtin.modinfo"):
            path = regular(installed_modules / name)
            require(file_record(path) == file_record(kernel / name), "E_BUILD_MODULE_CHANGED", "Installed module metadata changed.")
            put(mod / name, path.read_bytes())
        put(mod / "modules.order", "".join(name + "\n" for name in modules))
        overlay(tree, touch)
        merge_private(tree, inventories, touch)
        for path in tree.rglob("*"):
            if path.is_dir() and not path.is_symlink():
                path.chmod(0o755)
        depbase = work / "depmod"
        (depbase / "lib").mkdir(parents=True)
        (depbase / "lib/modules").symlink_to(tree / "modules")
        execute(["depmod", "-b", depbase, "7.2.10"], "depmod")
        check_dependencies(mod, modules)
        put(tree / "DEVICE-CONTENTS.txt", contents(tree))
        squash(tree, boot / "dby-device.img", epoch, execute)
        initramfs(tree, boot, work, compiler, execute)
        kernel_image(kernel / "arch/arm64/boot/Image", boot / "Image")
        shutil.copyfile(config["dtb"], boot / "dby-w09.dtb")
        put(boot / "MANIFEST", "".join(file_record(boot / name)["sha256"] + f"  {name}\n"
                                        for name in ("Image", "dby-w09.dtb", "initrd.img")))
        document = create_manifest(bundle, sequence=config["sequence"], created=created, sources=source_metadata)
        write_manifest(bundle, document)
        verify(bundle)
        require(snapshot(tracked) == before and sorted(kernel.rglob("*.ko")) == all_modules,
                "E_BUILD_CHANGED", "A build input changed during construction.")
        for root in config["private_inputs"]:
            private_inventory(root)
        publish(bundle, output)
    emit("OK: device-package bundle built and verified.")
    return document
