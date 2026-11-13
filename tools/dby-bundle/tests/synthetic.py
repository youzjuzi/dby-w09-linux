# SPDX-License-Identifier: GPL-2.0-only
"""Small real ELF inputs and a fixture-only modules_install target."""

import json
from pathlib import Path
import subprocess

MODULES = (
    "kernel/drivers/gpu/drm/msm/msm.ko",
    "kernel/drivers/gpu/drm/panel/panel-novatek-nt36523.ko",
    "kernel/drivers/input/touchscreen/nt36523-thp.ko",
    "kernel/drivers/power/supply/dby-fg-readonly.ko",
    "kernel/drivers/pci/pwrctrl/pci-pwrctrl-pwrseq.ko",
)
VERMAGIC = "7.2.10 SMP preempt mod_unload aarch64"
FIRMWARE = "firmware/novatek/nt36523-thp.bin"


def write(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)
    path.chmod(mode)


def elf(path, *, touch=False, gauge=False, vermagic=VERMAGIC, parameters=None):
    metadata = ["license=GPL", "vermagic=" + vermagic]
    if touch:
        metadata += ["alias=spi:nt36523-thp", "alias=of:N*T*Cnovatek,nt36523-thp"]
        if parameters is None:
            parameters = {
                "managed": "bool", "settle_ms": "uint", "boot_wait_ms": "uint", "skip_probe": "bool",
                "edge_px": "uint", "filter": "uint", "lock_px": "uint", "confirm": "uint", "multi": "uint",
                "guard": "uint"}
        metadata += ["parmtype=" + name + ":" + kind for name, kind in parameters.items()]
    if gauge:
        metadata += ["alias=of:N*T*Cdby,pm8150b-fg-readonlyC*"]
    source = path.with_suffix(".fixture.c")
    write(source, "\n".join(f'__attribute__((section(".modinfo"), used)) static const char m{i}[] = {json.dumps(value)};'
                             for i, value in enumerate(metadata)))
    subprocess.run(["aarch64-linux-gnu-gcc", "-c", "-o", str(path), str(source)],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    source.unlink()


def fixture(root):
    kernel, source = root / "kernel", root / "source"
    for name in MODULES:
        target = kernel / name.removeprefix("kernel/")
        target.parent.mkdir(parents=True, exist_ok=True)
        elf(target, touch="nt36523-thp" in name, gauge="dby-fg" in name)
    for name in ("modules.builtin", "modules.builtin.modinfo", "Module.symvers"):
        write(kernel / name, "")
    write(kernel / "include/config/kernel.release", "7.2.10\n")
    write(kernel / ".config", "\n".join("CONFIG_" + option + "=y" for option in (
        "SQUASHFS", "SQUASHFS_ZLIB", "BLK_DEV_LOOP", "VFAT_FS")) + "\n")
    write(kernel / "modules.order", "".join(name.removeprefix("kernel/") + "\n" for name in MODULES))
    image = bytearray(128); image[0x38:0x3c] = b"ARMd"
    write(kernel / "arch/arm64/boot/Image", image)
    write(root / "board.dtb", b"synthetic board data")
    write(root / "modules.list", "\n".join(MODULES) + "\n")
    recipes = ['modules_install:', '\tmkdir -p "$(INSTALL_MOD_PATH)/lib/modules/7.2.10"']
    for name in MODULES:
        recipes += [f'\tmkdir -p "$(INSTALL_MOD_PATH)/lib/modules/7.2.10/{Path(name).parent}"',
                    f'\tcp "$(O)/{name.removeprefix("kernel/")}" "$(INSTALL_MOD_PATH)/lib/modules/7.2.10/{name}"']
    for name in ("modules.builtin", "modules.builtin.modinfo"):
        recipes += [f'\tcp "$(O)/{name}" "$(INSTALL_MOD_PATH)/lib/modules/7.2.10/{name}"']
    write(source / "Makefile", "\n".join(recipes) + "\n")
    for name in ("scripts/Makefile.modinst", "scripts/Kbuild.include", "scripts/depmod.sh"):
        write(source / name, "# Synthetic input\n")
    private = root / "private"
    private.mkdir(mode=0o700)
    write(private / FIRMWARE, b"synthetic firmware")
    import hashlib
    rows = {FIRMWARE: {"mode": 0o644, "sha256": hashlib.sha256(b"synthetic firmware").hexdigest()}}
    write(private / "inputs.json", json.dumps({"schema": 1, "files": rows}))
    data = {"kernel_build_dir": "kernel", "kernel_source_dir": "source", "dtb": "board.dtb",
            "external_modules": {}, "private_inputs": ["private"], "modules_list": "modules.list",
            "toolchain": "aarch64-linux-gnu-", "touch": {"enabled": True}, "output": "bundle",
            "created": "2026-11-02T00:00:00Z", "sequence": 1, "epoch": 0,
            "sources": {"repositories": {role: "a" * 40 for role in ("linux", "kernel", "uboot")},
                        "upstream": [{"name": "linux", "version": "7.2.10", "sha256": "b" * 64}]}}
    write(root / "bundle.json", json.dumps(data))
    return data
