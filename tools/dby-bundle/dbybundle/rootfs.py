# SPDX-License-Identifier: GPL-2.0-only
"""Rootfs recipes, checked plans, inventories and deterministic packaging."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from .manifest import BundleError, create_manifest, file_record, io_errors, require, verify, write_manifest
from .build_inputs import HERE, regular, unlinked
from .builder import publish, run
from .rootfs_build import APT_OPTIONS, mirror_url, progress

RECIPE = HERE / "rootfs"
RESOLV_CONF_TARGET = "../run/systemd/resolve/stub-resolv.conf"
# alsa-topology-conf supplies generic ALSA topology data, not device firmware.
FIRMWARE_ALLOWLIST = ("regulatory.db", "regulatory.db.p7s", "skl_hda_dsp_generic-tplg.bin")
FULL_ONLY = {"etc/xdg/monitors.xml", "etc/UPower/UPower.conf.d/90-dby-battery-policy.conf"}


def package_list(path):
    result = []
    for line in path.read_text().splitlines():
        value = line.split("#", 1)[0].strip()
        if not value:
            continue
        require(re.fullmatch(r"[a-z0-9][a-z0-9+.-]*(?:=[A-Za-z0-9.+:~_-]+)?", value),
                "E_ROOTFS_PACKAGE", "Invalid package recipe entry.")
        require(value.split("=", 1)[0] not in {old.split("=", 1)[0] for old in result},
                "E_ROOTFS_PACKAGE", "Duplicate package recipe entry.")
        result.append(value)
    return result


def package_manifest(status):
    rows, keys = [], set()
    for block in status.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line and not line.startswith((" ", "\t")))
        if fields.get("Status") != "install ok installed":
            continue
        row = tuple(fields.get(name, "") for name in ("Package", "Version", "Architecture"))
        require(all(value and not any(c.isspace() for c in value) for value in row),
                "E_ROOTFS_PACKAGES", "Malformed installed package record.")
        require((row[0], row[2]) not in keys, "E_ROOTFS_PACKAGES", "Duplicate installed package.")
        keys.add((row[0], row[2])); rows.append(" ".join(row))
    return ("".join(line + "\n" for line in sorted(rows, key=lambda value: value.encode()))).encode()


def compare_packages(before, after):
    def parse(data):
        rows = {}
        for line in data.decode().splitlines():
            fields = line.split()
            require(len(fields) == 3 and (fields[0], fields[2]) not in rows,
                    "E_ROOTFS_PACKAGES", "Invalid package inventory list.")
            rows[(fields[0], fields[2])] = fields[1]
        return rows
    old, new = parse(before), parse(after)
    return {"added": [f"{key[0]} {new[key]} {key[1]}" for key in sorted(new.keys() - old.keys())],
            "removed": [f"{key[0]} {old[key]} {key[1]}" for key in sorted(old.keys() - new.keys())],
            "changed": [{"package": key[0], "architecture": key[1], "before": old[key], "after": new[key]}
                        for key in sorted(old.keys() & new.keys()) if old[key] != new[key]]}


def inventory_metadata(packages, baseline=None):
    digest = hashlib.sha256(packages).hexdigest()
    count = len(packages.splitlines())
    difference = {"compared": baseline is not None, "baseline_available": baseline is not None,
                  "actual_inventory": "packages.txt"}
    if baseline is not None:
        difference.update(compare_packages(baseline, packages))
        difference["baseline_sha256"] = hashlib.sha256(baseline).hexdigest()
    else:
        difference.update(reason="No baseline selected; actual inventory becomes the new baseline.",
                          new_baseline={"file": "packages.txt", "sha256": digest, "package_count": count})
    return {"packages_sha256": digest, "package_count": count, "package_difference": difference}


def overlay_files(kind):
    result = {}
    for path in sorted((RECIPE / "overlay").rglob("*")):
        if path.is_file():
            name = path.relative_to(RECIPE / "overlay").as_posix()
            if kind == "full" or name not in FULL_ONLY:
                result[name] = path
    if kind == "test":
        for path in sorted((RECIPE / "test/overlay").rglob("*")):
            if path.is_file():
                result[path.relative_to(RECIPE / "test/overlay").as_posix()] = path
    return result


def recipe_summary(kind):
    inputs = {"sources.json": RECIPE / "sources.json", "purge.list": RECIPE / "purge.list",
              "add.list": RECIPE / ("add.list" if kind == "full" else "test/add.list")}
    inputs.update({"overlay/" + name: path for name, path in overlay_files(kind).items()})
    inputs.update({name: HERE / "dbybundle" / name for name in ("rootfs.py", "rootfs_privileged.py", "rootfs_build.py")})
    hashes = {name: file_record(path)["sha256"] for name, path in sorted(inputs.items())}
    return {"recipe_sha256": hashlib.sha256(json.dumps({"kind": kind, "files": hashes}, sort_keys=True).encode()).hexdigest(),
            "files": hashes}


@io_errors
def make_plan(kind, source, output, *, epoch=0, baseline=None, no_baseline=False, mirror=None, rules=None):
    require(kind in ("full", "test"), "E_ROOTFS_KIND", "Unknown rootfs kind.")
    require(type(epoch) is int and 0 <= epoch <= 0xffffffff, "E_ROOTFS_EPOCH", "Invalid packaging epoch.")
    require(not (baseline is not None and no_baseline), "E_ROOTFS_BASELINE", "Choose a baseline or --no-baseline, not both.")
    if mirror is not None:
        mirror = mirror_url(mirror)
        require(mirror is not None, "E_ROOTFS_MIRROR", "Invalid build mirror URL.")
    output = Path(os.path.abspath(output)); unlinked(output)
    require(not output.exists() and output.parent.is_dir(), "E_ROOTFS_OUTPUT", "Use a fresh output with an existing parent.")
    require(not output.is_relative_to(HERE) and not HERE.is_relative_to(output),
            "E_ROOTFS_OVERLAP", "Build output must not overlap tool sources.")
    rules = json.loads((RECIPE / "sources.json").read_bytes()) if rules is None else rules
    expected = rules["iso" if kind == "full" else "base"]["sha256"]
    missing = []
    source = Path(os.path.abspath(source)) if source else None
    if source is None or not source.is_file():
        missing.append("desktop ISO" if kind == "full" else "ARM64 base tarball")
    else:
        regular(source)
        require(file_record(source)["sha256"] == expected, "E_ROOTFS_SOURCE_HASH", "Source SHA-256 differs; stop without substitution.")
        require(not output.is_relative_to(source) and not source.is_relative_to(output),
                "E_ROOTFS_OVERLAP", "Source overlaps output.")
    if baseline is not None:
        regular(Path(baseline))
        require(file_record(Path(baseline))["sha256"] == rules["baseline_packages_sha256"]
                and len(Path(baseline).read_bytes().splitlines()) == rules["baseline_package_count"],
                "E_ROOTFS_BASELINE", "Baseline package inventory does not match its source lock.")
        compare_packages(Path(baseline).read_bytes(), b"")
    purge = package_list(RECIPE / "purge.list") if kind == "full" else []
    additions = package_list(RECIPE / ("add.list" if kind == "full" else "test/add.list"))
    stages = [{"stage": "verify-source", "sha256": expected, "root": False}]
    if kind == "full":
        stages += [{"stage": "extract-minimal", "argv": ["7z", "e", "-so", "@ISO@", "casper/minimal.squashfs"], "root": False},
                   {"stage": "verify-minimal", "sha256": rules["minimal_squashfs_sha256"], "root": False},
                   {"stage": "extract-root", "argv": ["unsquashfs", "-no-progress", "-d", "@ROOT@", "@MINIMAL@"], "root": True}]
    else:
        stages += [{"stage": "extract-root", "argv": ["tar", "-xpf", "@BASE@", "--numeric-owner", "--same-owner",
                    "--xattrs", "--acls", "-C", "@ROOT@"], "root": True}]
    stages += [{"stage": "foreign-runtime", "action": "require native ARM64 or enabled F-flag binfmt; enter private mount namespace", "root": True},
               {"stage": "temporary-build-environment", "action": "private proc/dev/sys/run; temporary DNS and policy-rc.d; always restore", "root": True},
               {"stage": "build-mirror", "mirror": mirror, "action": "temporarily replace ubuntu.sources URIs; restore original bytes before packing", "root": True}]
    if purge:
        stages.append({"stage": "purge", "argv": ["chroot", "@ROOT@", "apt-get", *APT_OPTIONS, "purge", "-y", "--", *purge],
                       "only_installed": True, "root": True})
    stages += [{"stage": "apt-index", "argv": ["chroot", "@ROOT@", "apt-get", *APT_OPTIONS, "update"], "root": True},
               {"stage": "add", "argv": ["chroot", "@ROOT@", "apt-get", *APT_OPTIONS, "install", "-y", "--no-install-recommends", "--no-remove", "--", *additions], "root": True},
               {"stage": "restore-build-mirror", "action": "restore ubuntu.sources bytes; no build mirror in rootfs", "root": True},
               {"stage": "check-payload", "checks": ["no special files", "system accounts", "empty home", "empty module tree"],
                "allow_dev_special": True,
                "firmware_allowlist": list(FIRMWARE_ALLOWLIST), "root": True},
               {"stage": "overlay", "files": sorted(overlay_files(kind)), "root": True},
               {"stage": "system-units", "enable": ["ssh", "NetworkManager", "systemd-networkd", "systemd-resolved", "dby-seed"] +
                (["bluetooth", "chrony", "gdm"] if kind == "full" else ["systemd-timesyncd"]), "mask": ["systemd-networkd-wait-online.service"],
                "default": "graphical.target" if kind == "full" else "multi-user.target", "root": True},
               {"stage": "clean", "remove": ["APT archive/list/binary caches", "logs", "machine ID", "SSH host/authorized keys", "random seed", "preset hostname", "network profiles", "static dev entries"],
                "resolv_conf": RESOLV_CONF_TARGET, "allow_dev_special": False, "root": True},
               {"stage": "packages", "baseline_count": rules["baseline_package_count"] if baseline is not None else None,
                "baseline_sha256": rules["baseline_packages_sha256"] if baseline is not None else None,
                "action": "record actual inventory and package differences; never force equality" if baseline is not None
                          else "record actual inventory as new baseline; comparison not performed", "root": False},
               {"stage": "pack", "argv": ["tar", "--sort=name", "--numeric-owner", f"--mtime=@{epoch}",
                "--format=pax", "--xattrs", "--acls", "-C", "@ROOT@", "-cf", "-", "."],
                "compress": ["zstd", "-q", "-T0", "-19"], "root": False}]
    return {"kind": kind, "source_sha256": expected, "epoch": epoch, "missing_inputs": missing, "purge": purge, "add": additions,
            "recipe": recipe_summary(kind), "stages": stages, "mirror": mirror,
            "baseline": {"mode": "compare" if baseline is not None else "new", "explicit_no_baseline": no_baseline},
            "marker": {"path": "/etc/dby-rootfs-kind", "content": kind + "\n"}, "artifact": "rootfs.tar.zst" if kind == "full" else "rootfs-test.tar.zst"}


def pack_tree(root, output, epoch):
    require(not output.exists(), "E_ROOTFS_OUTPUT", "Archive output already exists.")
    progress("rootfs-pack", "packing tar/zstd")
    argv = ["tar", "--sort=name", "--numeric-owner", f"--mtime=@{epoch}",
            "--format=pax", "--pax-option=delete=atime,delete=ctime,exthdr.name=%d/PaxHeaders/%f",
            "--xattrs", "--acls", "-C", str(root), "-cf", "-", "."]
    with output.open("xb") as stream:
        producer = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            compressor = subprocess.run(["zstd", "-q", "-T0", "-19", "-c"], stdin=producer.stdout, stdout=stream, stderr=subprocess.DEVNULL)
            producer.stdout.close()
            require(producer.wait() == 0 and compressor.returncode == 0, "E_ROOTFS_PACK", "Deterministic packing failed.")
        finally:
            if producer.poll() is None:
                producer.terminate(); producer.wait()
    progress("rootfs-pack", "completed")


def attach_rootfs(device_bundle, output, archive, metadata):
    device_bundle, output, archive = (Path(os.path.abspath(path)) for path in (device_bundle, output, archive))
    unlinked(output)
    require(not output.is_relative_to(device_bundle) and not device_bundle.is_relative_to(output)
            and not archive.is_relative_to(output), "E_ROOTFS_OVERLAP", "Combined bundle output overlaps an input.")
    original = verify(device_bundle)
    require(metadata.get("kind") in ("full", "test") and file_record(archive)["sha256"] == metadata.get("archive_sha256"),
            "E_ROOTFS_ARCHIVE", "Rootfs archive does not match its build metadata.")
    require(not output.exists() and output.parent.is_dir(), "E_ROOTFS_OUTPUT", "Combined bundle output must be new.")
    with tempfile.TemporaryDirectory(prefix=".dby-combine-", dir=output.parent) as temporary:
        staged = Path(temporary) / "bundle"; staged.mkdir(mode=0o700)
        shutil.copytree(device_bundle / "p1", staged / "p1")
        kind = metadata["kind"]
        if kind == "test" and "rootfs.tar.zst" in original["files"]:
            shutil.copyfile(device_bundle / "rootfs.tar.zst", staged / "rootfs.tar.zst")
        if kind == "full" and "rootfs-test.tar.zst" in original["files"]:
            shutil.copyfile(device_bundle / "rootfs-test.tar.zst", staged / "rootfs-test.tar.zst")
        shutil.copyfile(archive, staged / ("rootfs.tar.zst" if kind == "full" else "rootfs-test.tar.zst"))
        sources = original["sources"]
        sources.setdefault("inputs", {})["rootfs-" + kind + "-recipe"] = metadata["recipe_sha256"]
        rootfs = {key: metadata[key] for key in ("recipe_sha256", "packages_sha256", "package_count")} if kind == "full" else original["rootfs"]
        document = create_manifest(staged, sequence=int(original["bundle_id"].split("-")[1]), created=original["created"], sources=sources, rootfs=rootfs)
        write_manifest(staged, document); verify(staged); publish(staged, output)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("full", "test"), required=True)
    parser.add_argument("--iso", type=Path)
    parser.add_argument("--base", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    baseline_choice = parser.add_mutually_exclusive_group()
    baseline_choice.add_argument("--baseline-packages", type=Path)
    baseline_choice.add_argument("--no-baseline", action="store_true", help="Record actual packages as a new baseline without comparison")
    parser.add_argument("--epoch", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mirror", help="Build-only http(s) mirror; original sources restored before packaging")
    parser.add_argument("--device-bundle", type=Path)
    parser.add_argument("--bundle-output", type=Path)
    args = parser.parse_args(argv)
    try:
        require(bool(args.device_bundle) == bool(args.bundle_output), "E_ROOTFS_CONFIG", "Specify both bundle input and new output.")
        source = args.iso if args.kind == "full" else args.base
        plan = make_plan(args.kind, source, args.output, epoch=args.epoch, baseline=args.baseline_packages,
                         no_baseline=args.no_baseline, mirror=args.mirror)
        if args.dry_run:
            print(json.dumps(plan, sort_keys=True, indent=2))
            require(not plan["missing_inputs"], "E_ROOTFS_INPUT", "Missing required input: " + ", ".join(plan["missing_inputs"]) + ".")
        else:
            from .rootfs_privileged import execute
            metadata = execute(plan, source, args.output, baseline=args.baseline_packages)
            if args.device_bundle:
                attach_rootfs(args.device_bundle, args.bundle_output, args.output / plan["artifact"], metadata)
            print("OK: rootfs archive and recipe metadata published.")
        return 0
    except KeyboardInterrupt:
        print("E_ROOTFS_INTERRUPTED: Build interrupted; cleanup completed or retention reported above.", file=sys.stderr)
        return 130
    except BundleError as error:
        print(error, file=sys.stderr); return 1
    except (OSError, ValueError, KeyError, TypeError):
        print("E_ROOTFS_IO: Rootfs input or operation failed; raw details withheld.", file=sys.stderr); return 1
