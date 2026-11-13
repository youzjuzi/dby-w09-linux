# SPDX-License-Identifier: GPL-2.0-only
"""Root-only rootfs execution; plan tests never invoke this module's effects."""

from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import stat

from .builder import publish, tool
from .manifest import BundleError, file_record, require
from .rootfs import (FIRMWARE_ALLOWLIST, RECIPE, RESOLV_CONF_TARGET, inventory_metadata, make_plan,
                     overlay_files, package_manifest, pack_tree)
from .rootfs_build import APT_OPTIONS, progress, run, temporary_mirror, workspace


def inside(root, name, *, final=False):
    pending, done, links = list(PurePosixPath(name).parts), [], 0
    while pending:
        part = pending.pop(0)
        if part in ("/", ".", ""):
            continue
        if part == "..":
            require(done, "E_ROOTFS_PATH", "Target path escapes root.")
            done.pop(); continue
        path = root.joinpath(*done, part)
        if path.is_symlink() and (pending or final):
            links += 1
            require(links <= 40, "E_ROOTFS_PATH", "Target root has a symbolic link cycle.")
            target = os.readlink(path)
            if target.startswith("/"):
                done = []
            pending = list(PurePosixPath(target).parts)[int(target.startswith("/")):] + pending
        else:
            done.append(part)
    return root.joinpath(*done)


def put(root, name, data, mode=0o644):
    path = inside(root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        path.unlink()
    path.write_bytes(data.encode() if isinstance(data, str) else data)
    path.chmod(mode); os.chown(path, 0, 0)


def mounted():
    return {Path(line.split()[4].replace("\\040", " ").replace("\\134", "\\"))
            for line in Path("/proc/self/mountinfo").read_text().splitlines()}


def native_or_binfmt():
    if platform.machine() in ("aarch64", "arm64"):
        return
    path = Path("/proc/sys/fs/binfmt_misc/qemu-aarch64")
    text = path.read_text() if path.is_file() else ""
    require(text.startswith("enabled\n") and any(line.startswith("flags:") and "F" in line for line in text.splitlines()),
            "E_ROOTFS_BINFMT", "Foreign root execution requires enabled qemu-aarch64 binfmt with F flag.")


@contextmanager
def environment(root):
    paths, saved = [], {}
    def snapshot(name):
        path = inside(root, name)
        saved[name] = ("link", os.readlink(path), None) if path.is_symlink() else (
            ("file", path.read_bytes(), path.stat().st_mode & 0o7777) if path.is_file() else ("absent", None, None))
    def mount(name, argv):
        path = inside(root, name); path.mkdir(parents=True, exist_ok=True)
        paths.append(path); run([*argv, path], "rootfs-mount")
    try:
        for name in ("etc/resolv.conf", "usr/sbin/policy-rc.d"):
            snapshot(name)
        put(root, "etc/resolv.conf", Path("/etc/resolv.conf").read_bytes())
        put(root, "usr/sbin/policy-rc.d", "#!/bin/sh\nexit 101\n", 0o755)
        mount("proc", ["mount", "-t", "proc", "-o", "ro,nosuid,nodev,noexec", "proc"])
        mount("sys", ["mount", "-t", "sysfs", "-o", "ro,nosuid,nodev,noexec", "sysfs"])
        mount("dev", ["mount", "-t", "tmpfs", "-o", "mode=0755,nosuid", "tmpfs"])
        for name, major, minor in (("null", 1, 3), ("zero", 1, 5), ("random", 1, 8), ("urandom", 1, 9)):
            path = inside(root, "dev/" + name)
            os.mknod(path, stat.S_IFCHR | 0o666, os.makedev(major, minor)); path.chmod(0o666)
        mount("dev/pts", ["mount", "-t", "devpts", "-o", "newinstance,ptmxmode=0666,mode=0620", "devpts"])
        for name, target in (("ptmx", "pts/ptmx"), ("fd", "/proc/self/fd"), ("stdin", "/proc/self/fd/0"),
                             ("stdout", "/proc/self/fd/1"), ("stderr", "/proc/self/fd/2")):
            inside(root, "dev/" + name).symlink_to(target)
        mount("run", ["mount", "-t", "tmpfs", "-o", "mode=0755,nosuid,nodev", "tmpfs"])
        yield
    finally:
        failed = False
        for path in reversed(paths):
            if path in mounted():
                try:
                    run(["umount", path], "rootfs-unmount")
                except BundleError:
                    failed = True
        for name, (kind, data, mode) in saved.items():
            path = inside(root, name)
            if path.exists() or path.is_symlink():
                path.unlink()
            if kind == "file":
                put(root, name, data, mode)
            elif kind == "link":
                path.symlink_to(data)
        require(not failed and not any(path in mounted() for path in paths),
                "E_ROOTFS_CLEANUP", "Build mounts remain; work directory retained, no archive published.")


def check_special(root, *, allow_dev_special=False):
    """Inspect inode types without following links or opening special files."""
    first = None

    def unreadable(error):
        raise error

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=unreadable):
        if allow_dev_special and Path(directory) == root and "dev" in dirs:
            dirs.remove("dev")
        for name in dirs + files:
            path = Path(directory) / name
            mode = path.lstat().st_mode
            if stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode):
                relative = path.relative_to(root).as_posix()
                if first is None or relative < first:
                    first = relative
    if first is not None:
        raise BundleError("E_ROOTFS_SPECIAL", "Unsupported special file: " + json.dumps(first, ensure_ascii=True) + ".")


def check_payload(root, *, allow_dev_special=False):
    """Read-only gates after APT and again before packaging."""
    check_special(root, allow_dev_special=allow_dev_special)
    passwd = inside(root, "etc/passwd", final=True)
    for line in passwd.read_text().splitlines():
        fields = line.split(":")
        require(len(fields) == 7 and fields[2].isdigit(), "E_ROOTFS_USERS", "Malformed system account table.")
        require(int(fields[2]) < 1000 or fields[0] == "nobody" and fields[2] == "65534",
                "E_ROOTFS_USERS", "Preinstalled user accounts are forbidden.")
    require(not any(inside(root, "home", final=True).iterdir()) if inside(root, "home", final=True).exists() else True,
            "E_ROOTFS_USERS", "Preinstalled home data is forbidden.")
    modules = inside(root, "usr/lib/modules", final=True)
    require(not modules.exists() or not any(modules.iterdir()),
            "E_ROOTFS_MODULES", "Rootfs must not carry another module tree.")
    firmware = inside(root, "usr/lib/firmware", final=True)
    unexpected = sorted(path.relative_to(firmware).as_posix() for path in firmware.rglob("*")
                        if (path.is_file() or path.is_symlink())
                        and path.relative_to(firmware).as_posix() not in FIRMWARE_ALLOWLIST)
    if unexpected:
        raise BundleError("E_ROOTFS_FIRMWARE", "Vendor firmware must be supplied by the p1 device package; "
                          "unexpected firmware path: " + json.dumps(unexpected[0], ensure_ascii=True) + ".")


def clean(root):
    def discard(name):
        path = inside(root, name)
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()
    for name in ("var/cache/apt/archives", "var/cache/apt/pkgcache.bin", "var/cache/apt/srcpkgcache.bin",
                 "var/lib/apt/lists", "var/log", "var/lib/systemd/random-seed", "root/.ssh",
                 "etc/hostname", "etc/timezone", "etc/localtime", "etc/NetworkManager/system-connections",
                 "etc/apt/sources.list.d/cdrom.sources"):
        discard(name)
    for name in ("var/cache/apt/archives/partial", "var/lib/apt/lists/partial", "var/log", "etc/NetworkManager/system-connections"):
        inside(root, name).mkdir(parents=True, exist_ok=True)
    dev = inside(root, "dev")
    if dev.is_symlink() or dev.exists() and not dev.is_dir():
        dev.unlink()
    dev.mkdir(mode=0o755, exist_ok=True)
    for path in dev.iterdir():
        if not path.is_symlink() and path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    for path in inside(root, "etc/ssh", final=True).glob("ssh_host_*"):
        if path.is_file() or path.is_symlink():
            path.unlink()
    put(root, "etc/machine-id", b"")
    discard("var/lib/dbus/machine-id")
    inside(root, "var/lib/dbus").mkdir(parents=True, exist_ok=True)
    inside(root, "var/lib/dbus/machine-id").symlink_to("/etc/machine-id")
    put(root, "etc/hosts", "127.0.0.1 localhost\n::1 localhost ip6-localhost ip6-loopback\n")
    # Apply the runtime resolver contract after temporary build DNS is restored.
    resolv = inside(root, "etc/resolv.conf")
    resolv.unlink(missing_ok=True)
    resolv.symlink_to(RESOLV_CONF_TARGET)
    check_payload(root)


def execute(plan, source, output, *, baseline=None):
    progress("rootfs-inputs", "checking source, baseline and dependencies")
    require(os.geteuid() == 0, "E_ROOTFS_ROOT", "Rootfs execution requires sudo; use --dry-run without root.")
    require(not plan["missing_inputs"], "E_ROOTFS_INPUT", "Required rootfs source is missing.")
    # Check named source hashes again immediately before privileged work.
    make_plan(plan["kind"], source, output, epoch=plan["epoch"], baseline=baseline,
              no_baseline=plan["baseline"]["explicit_no_baseline"], mirror=plan["mirror"])
    for name in ("tar", "zstd", "mount", "umount", "chroot", "systemctl") + (("7z", "unsquashfs") if plan["kind"] == "full" else ()):
        tool(name, name)
    native_or_binfmt()
    library = ctypes.CDLL(None, use_errno=True)
    require(library.unshare(0x00020000) == 0, "E_ROOTFS_NAMESPACE", "Cannot create a private mount namespace.")
    run(["mount", "--make-rprivate", "/"], "rootfs-private-mounts")
    output = Path(output).absolute()
    with workspace(output.parent, mounted) as work:
        root = work / "root"
        if plan["kind"] == "full":
            minimal = work / "minimal.squashfs"
            with minimal.open("xb") as stream:
                run(["7z", "e", "-so", str(source), "casper/minimal.squashfs"], "rootfs-iso", stdout=stream, error_code="E_ROOTFS_ISO")
            rules = json.loads((RECIPE / "sources.json").read_bytes())
            require(file_record(minimal)["sha256"] == rules["minimal_squashfs_sha256"],
                    "E_ROOTFS_MINIMAL_HASH", "Minimal layer SHA-256 differs; stop without substitution.")
            run(["unsquashfs", "-no-progress", "-d", root, minimal], "rootfs-extract")
        else:
            root.mkdir()
            run(["tar", "-xpf", source, "--numeric-owner", "--same-owner", "--xattrs", "--acls", "--checkpoint=10000", "-C", root], "rootfs-extract")
        inside(root, "etc/apt/sources.list.d/cdrom.sources").unlink(missing_ok=True)
        def apt(*arguments):
            return run(["chroot", root, "/usr/bin/env", "DEBIAN_FRONTEND=noninteractive", "LC_ALL=C", "apt-get", *APT_OPTIONS, *arguments], "rootfs-apt")
        with environment(root), temporary_mirror(inside(root, "etc/apt/sources.list.d/ubuntu.sources"), plan["mirror"]):
            current = package_manifest(inside(root, "var/lib/dpkg/status", final=True).read_text())
            installed = {line.split()[0] for line in current.decode().splitlines()}
            purge = [name for name in plan["purge"] if name in installed]
            if purge:
                apt("purge", "-y", "--", *purge)
            apt("update")
            apt("install", "-y", "--no-install-recommends", "--no-remove", "--", *plan["add"])
        progress("rootfs-build-environment", "original sources and temporary files restored; build mounts removed")
        progress("rootfs-check", "checking special files, accounts, home, modules and firmware")
        check_payload(root, allow_dev_special=True)
        progress("rootfs-overlay", "applying profile")
        for name, path in overlay_files(plan["kind"]).items():
            put(root, name, path.read_bytes(), 0o755 if name == "usr/lib/dby/dby-seed-apply" else 0o644)
        put(root, "etc/dby-rootfs-kind", plan["kind"] + "\n")
        for name in ("dev", "proc", "sys", "run", "usr/lib/modules"):
            inside(root, name, final=True).mkdir(parents=True, exist_ok=True)
        units = next(stage for stage in plan["stages"] if stage["stage"] == "system-units")
        for unit in units["enable"]:
            run(["systemctl", "--root", root, "enable", unit + ".service"], "rootfs-enable")
        run(["systemctl", "--root", root, "mask", "systemd-networkd-wait-online.service"], "rootfs-mask")
        run(["systemctl", "--root", root, "set-default", units["default"]], "rootfs-default")
        progress("rootfs-clean", "removing generated identity and cache data")
        clean(root)
        progress("rootfs-packages", "recording actual inventory")
        packages = package_manifest(inside(root, "var/lib/dpkg/status", final=True).read_text())
        metadata = {"kind": plan["kind"], "recipe_sha256": plan["recipe"]["recipe_sha256"],
                    "source_sha256": plan["source_sha256"], "recipe_inputs": plan["recipe"]["files"]}
        metadata.update(inventory_metadata(packages, Path(baseline).read_bytes() if baseline is not None else None))
        result = work / "result"; result.mkdir(mode=0o700)
        (result / "packages.txt").write_bytes(packages)
        pack_tree(root, result / plan["artifact"], plan["epoch"])
        metadata["archive_sha256"] = file_record(result / plan["artifact"])["sha256"]
        (result / "rootfs.json").write_text(json.dumps(metadata, sort_keys=True, indent=2) + "\n")
        progress("rootfs-publish", "publishing new output")
        publish(result, output)
        return metadata
