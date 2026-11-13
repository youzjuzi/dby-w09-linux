# SPDX-License-Identifier: GPL-2.0-only
"""Explicit device-package backup, update and recovery; never update p2."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import tempfile

from .common import BOOT_FILES, BundleError, file_record, no_links, read_json, require, validate_metadata, verify_bundle
from .core import verify_p1
from .maintenance_io import Backend, seed_tree

BACKUP_FILES = {"UBOOT/" + name for name in BOOT_FILES} | {"manifest.json"}
BACKUP_FIELDS = {"schema", "created", "old_bundle_id", "new_bundle_id", "files", "kernel_release", "size_bytes"}


def version(identifier):
    match = re.fullmatch(r"([0-9]{8})-([1-9][0-9]*)", identifier or "")
    require(match, "E_UPDATE_VERSION", "Invalid bundle identifier.")
    try:
        datetime.strptime(match[1], "%Y%m%d")
    except ValueError:
        raise BundleError("E_UPDATE_VERSION", "Invalid bundle date.") from None
    return int(match[1]), int(match[2])


def managed(root):
    if not (root / "manifest.json").exists():
        raise BundleError("E_UPDATE_UNMANAGED", "Card manifest is missing; use install or repair manually.")
    try:
        document = read_json(root / "manifest.json"); validate_metadata(document)
        version(document["bundle_id"]); verify_p1(root, document)
        return document
    except (BundleError, OSError, ValueError, KeyError):
        raise BundleError("E_UPDATE_CARD_INCONSISTENT", "Card differs from its manifest; repair manually or use install.") from None


def leftovers(root):
    names = [name for name in ("UBOOT.new", "UBOOT.old", "manifest.json.new")
             if (root / name).exists() or (root / name).is_symlink()]
    if names:
        message = "Interrupted update staging exists; do not delete it automatically."
        if "UBOOT.old" in names and not (root / "UBOOT").exists():
            message += " Restore UBOOT.old by renaming it to UBOOT, or run rollback with a verified host backup."
        raise BundleError("E_UPDATE_LEFTOVER", message)


def compatible(old, new):
    messages = []
    if old["kernel_release"] != new["kernel_release"]:
        messages.append("Kernel release changes; check module compatibility.")
    if any(old["rootfs"].get(key) != new["rootfs"].get(key) for key in ("recipe_sha256", "packages_sha256")):
        messages.append("p2 is unchanged; rootfs changes in the new bundle will not be applied.")
    return messages


def backup_location(path, backend, identifier, stamp):
    path = Path(os.path.abspath(path)); no_links(path)
    require(not path.is_relative_to(backend.target.path) and path != backend.target.path,
            "E_UPDATE_BACKUP", "Backup directory overlaps the target.")
    require(not any(path == p or path.is_relative_to(p) for p in backend.forbidden_roots),
            "E_UPDATE_BACKUP", "Backup must be outside target mount points.")
    parent = path if path.exists() else path.parent
    require(parent.is_dir() and bool(parent.stat().st_mode & 0o222) and os.access(parent, os.W_OK),
            "E_UPDATE_BACKUP", "Backup directory is not writable or its parent is missing.")
    if not backend.is_image:
        from .common import partition
        require(parent.stat().st_dev not in {Path(partition(backend.target.path, n)).stat().st_rdev for n in (1, 2)},
                "E_UPDATE_BACKUP", "Backup must not be on the target card filesystem.")
    require(not path.exists() or path.is_dir(), "E_UPDATE_BACKUP", "Backup destination is not a directory.")
    child = path / ("p1-" + stamp.strftime("%Y%m%dT%H%M%S%fZ") + "-" + identifier)
    require(not child.exists() and not child.is_symlink(), "E_UPDATE_BACKUP", "Timestamped backup already exists; never overwrite it.")
    return child


def backup_validate(path):
    try:
        path = Path(os.path.abspath(path)); no_links(path)
        require(path.is_dir(), "E_ROLLBACK_BACKUP_INVALID", "Backup directory is missing.")
        actual = set()
        for p in path.rglob("*"):
            mode = p.lstat().st_mode
            require(not stat.S_ISLNK(mode), "E_ROLLBACK_BACKUP_INVALID", "Backup contains a symbolic link.")
            if stat.S_ISDIR(mode):
                require(p.relative_to(path).as_posix() == "UBOOT", "E_ROLLBACK_BACKUP_INVALID", "Unexpected backup directory.")
            else:
                require(stat.S_ISREG(mode), "E_ROLLBACK_BACKUP_INVALID", "Invalid backup entry type.")
                actual.add(p.relative_to(path).as_posix())
        require(actual == BACKUP_FILES | {"backup.json"}, "E_ROLLBACK_BACKUP_INVALID", "Backup inventory is not complete and exact.")
        metadata = read_json(path / "backup.json")
        require(metadata.keys() == BACKUP_FIELDS and type(metadata["schema"]) is int and metadata["schema"] == 1,
                "E_ROLLBACK_BACKUP_INVALID", "Unsupported backup metadata.")
        require(isinstance(metadata["created"], str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", metadata["created"]),
                "E_ROLLBACK_BACKUP_INVALID", "Invalid backup time.")
        datetime.strptime(metadata["created"], "%Y-%m-%dT%H:%M:%SZ")
        version(metadata["old_bundle_id"]); version(metadata["new_bundle_id"])
        require(type(metadata["size_bytes"]) is int and metadata["size_bytes"] > 0,
                "E_ROLLBACK_BACKUP_INVALID", "Invalid backup capacity.")
        require(metadata["files"].keys() == BACKUP_FILES, "E_ROLLBACK_BACKUP_INVALID", "Invalid backup file records.")
        for name in BACKUP_FILES:
            require(file_record(path / name) == metadata["files"][name], "E_ROLLBACK_BACKUP_INVALID", "Backup file hash differs.")
        document = read_json(path / "manifest.json"); validate_metadata(document); verify_p1(path, document)
        require(document["bundle_id"] == metadata["old_bundle_id"] and document["kernel_release"] == metadata["kernel_release"],
                "E_ROLLBACK_BACKUP_INVALID", "Backup manifest differs from backup metadata.")
        return document, metadata
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise BundleError("E_ROLLBACK_BACKUP_INVALID", "Backup is invalid; refusing recovery without changing the target.") from None


def make_backup(root, destination, old, new, size, stamp):
    try:
        no_links(destination)
        destination.parent.mkdir(mode=0o700, exist_ok=True)
        destination.mkdir(mode=0o700)
        shutil.copytree(root / "UBOOT", destination / "UBOOT")
        shutil.copyfile(root / "manifest.json", destination / "manifest.json")
        metadata = {"schema": 1, "created": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "old_bundle_id": old["bundle_id"], "new_bundle_id": new["bundle_id"],
                    "kernel_release": old["kernel_release"], "size_bytes": size,
                    "files": {name: file_record(root / name) for name in sorted(BACKUP_FILES)}}
        (destination / "backup.json").write_text(json.dumps(metadata, sort_keys=True, indent=2) + "\n")
        for p in destination.rglob("*"):
            if p.is_file():
                with p.open("rb") as stream:
                    os.fsync(stream.fileno())
        for folder in (destination / "UBOOT", destination, destination.parent):
            fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        backup_validate(destination)
    except (OSError, ValueError, KeyError):
        raise BundleError("E_UPDATE_BACKUP", "Backup creation or re-read verification failed; target unchanged.") from None


def check_stage(store, document, *, active=False):
    with tempfile.TemporaryDirectory(prefix="dby-stage-check-") as temporary:
        root = Path(temporary)
        store.export("UBOOT" if active else "UBOOT.new", root / "UBOOT")
        if active:
            store.read_file("manifest.json", root / "manifest.json")
        else:
            (root / "manifest.json").write_text(json.dumps(document))
        verify_p1(root, document)


def switch(backend, source, document, *, recovering, mark, hook):
    with backend.transaction() as store:
        if recovering:
            store.remove("UBOOT.new"); store.remove("manifest.json.new")
            if store.exists("UBOOT"):
                store.remove("UBOOT.old")
        else:
            require(not any(store.exists(n) for n in ("UBOOT.new", "UBOOT.old", "manifest.json.new")),
                    "E_UPDATE_LEFTOVER", "Staging appeared after preview; target unchanged.")
        store.mkdir("UBOOT.new")
        for name in BOOT_FILES:
            store.copy(source / "UBOOT" / name, "UBOOT.new/" + name)
        store.sync(); check_stage(store, document)
        mark()
        if store.exists("UBOOT"):
            store.rename("UBOOT", "UBOOT.old")
        store.rename("UBOOT.new", "UBOOT")
        hook("after_switch", store)
        store.copy(source / "manifest.json", "manifest.json.new")
        store.sync(); store.rename("manifest.json.new", "manifest.json"); store.sync()
        hook("before_cleanup", store)
        check_stage(store, document, active=True)
        # The staged files were re-read above; final verification follows an
        # unmount/reopen. On a later failure the verified host backup is used.
        store.remove("UBOOT.old"); store.sync()


def final_check(backend, document, seed, signature):
    with backend.snapshot() as (root, actual):
        verify_p1(root, document)
        require(seed_tree(root) == seed and actual == signature,
                "E_UPDATE_VERIFY", "Seed or read-only p2 contract changed.")
        require(not any((root / name).exists() for name in ("UBOOT.new", "UBOOT.old", "manifest.json.new")),
                "E_UPDATE_VERIFY", "Maintenance staging remains.")


def rollback_command(target, backup, image):
    return "dby-install rollback " + ("--image-file " if image else "--device ") + shlex.quote(str(target)) + " --backup " + shlex.quote(str(backup))


def operate(action, target, *, bundle=None, backup_dir=None, backup=None, dry_run=False, allow_older=False,
            ask=input, emit=print, now=None, hook=lambda phase, context: None, backend_factory=Backend):
    """Public entry point; hooks inject faults in host-only tests."""
    require(action in ("update", "rollback"), "E_UPDATE_ACTION", "Unsupported maintenance action.")
    stamp = now or datetime.now(timezone.utc)
    new = verify_bundle(Path(bundle).absolute()) if action == "update" else None
    if action == "rollback":
        backup = Path(os.path.abspath(backup)); new, backup_meta = backup_validate(backup)
    with backend_factory(target, dry_run=dry_run) as io:
        with io.snapshot() as (root, signature):
            seed = seed_tree(root)
            if action == "update":
                leftovers(root); old = managed(root)
                old_version, new_version = version(old["bundle_id"]), version(new["bundle_id"])
                equal_files = all(old["files"]["p1/UBOOT/" + name] == new["files"]["p1/UBOOT/" + name] for name in BOOT_FILES)
                if old_version == new_version:
                    require(equal_files, "E_UPDATE_SAME_ID_DIFFERENT", "Same bundle ID has different p1 files.")
                    decision = "already up to date"
                else:
                    require(new_version > old_version or allow_older, "E_UPDATE_OLDER", "Older bundle refused; explicitly use --allow-older if intended.")
                    decision = "newer" if new_version > old_version else "older explicitly allowed"
                destination = backup_location(backup_dir, io, old["bundle_id"], stamp) if decision != "already up to date" else None
                require(destination is None or not destination.is_relative_to(Path(bundle).absolute()),
                        "E_UPDATE_BACKUP", "Backup must be outside the input bundle.")
                notices = compatible(old, new)
            else:
                require(any((root / n).is_dir() and not (root / n).is_symlink() for n in ("UBOOT", "UBOOT.old")),
                        "E_UPDATE_CARD_INCONSISTENT", "Neither UBOOT nor UBOOT.old exists; cannot recover this target.")
                require(not any(backup == p or backup.is_relative_to(p) for p in io.forbidden_roots),
                        "E_ROLLBACK_BACKUP_INVALID", "Backup must be on the host, outside target mounts.")
                old = None; destination = backup; decision = "restore verified backup; clear reserved staging"
                notices = ["p2 and dby-seed are unchanged; no second backup is created."]
            preview = {"action": action, "target": str(target.path), "kind": "image" if io.is_image else "block-device",
                       "old_bundle_id": old["bundle_id"] if old else None, "new_bundle_id": new["bundle_id"],
                       "kernel_release": new["kernel_release"], "old_kernel_release": old["kernel_release"] if old else None,
                       "backup": str(destination) if destination else None,
                       "version_decision": decision, "p2_unchanged": True, "seed_unchanged": True,
                       "confirmation": "full target name required; no automatic yes", "notices": notices,
                       "files": {name: {"status": "same" if old and old["files"]["p1/UBOOT/" + name] == new["files"]["p1/UBOOT/" + name] else "replace",
                                        "sha256_prefix": new["files"]["p1/UBOOT/" + name]["sha256"][:16]} for name in BOOT_FILES}}
            if not io.is_image:
                preview.update(model=io.identity.get("model", ""), serial=io.identity.get("serial", ""))
            if dry_run:
                return preview
            if decision == "already up to date":
                for notice in notices:
                    emit("Notice: " + notice)
                emit("already up to date"); return preview
        emit(json.dumps(preview, sort_keys=True, indent=2))
        io.confirm(ask)
        with io.locked():
            # Repeat read-only preflight after the confirmation delay.
            with io.snapshot() as (current, current_signature):
                require(current_signature == signature and seed_tree(current) == seed,
                        "E_TARGET_CHANGED", "Target contents changed after preview.")
                if action == "update":
                    leftovers(current)
                    require(managed(current) == old, "E_TARGET_CHANGED", "Card manifest changed after preview.")
                    require(verify_bundle(Path(bundle).absolute()) == new, "E_INSTALL_CHANGED", "Bundle changed after preview.")
                    make_backup(current, destination, old, new, io.size, stamp)
            if action == "update":
                # Present source in the same layout as a validated backup.
                with tempfile.TemporaryDirectory(prefix="dby-update-source-") as temporary:
                    source = Path(temporary)
                    shutil.copytree(Path(bundle) / "p1/UBOOT", source / "UBOOT")
                    shutil.copyfile(Path(bundle) / "manifest.json", source / "manifest.json")
                    _execute(io, source, new, old, destination, seed, signature, action, hook)
            else:
                require(backup_validate(backup)[0] == new, "E_ROLLBACK_BACKUP_INVALID", "Backup changed after preview.")
                _execute(io, backup, new, None, destination, seed, signature, action, hook)
    change = f"{preview['old_bundle_id']} ->" if action == "update" else "->"
    emit(f"OK: {action} {change} {new['bundle_id']}; kernel {new['kernel_release']}; backup {destination}")
    emit(rollback_command(target.path, destination, preview["kind"] == "image"))
    return preview


def _execute(io, source, new, old, destination, seed, signature, action, hook):
    switched = False
    def mark():
        nonlocal switched
        switched = True
    try:
        switch(io, source, new, recovering=action == "rollback", mark=mark, hook=hook)
        hook("before_readback", io)
        final_check(io, new, seed, signature)
    except (Exception, KeyboardInterrupt) as error:
        if not switched:
            raise
        original = str(error) if isinstance(error, BundleError) else type(error).__name__
        recovery = "not attempted"
        if action == "update":
            try:
                restored, _ = backup_validate(destination)
                switch(io, destination, restored, recovering=True, mark=lambda: None, hook=lambda *args: None)
                io.restore_exact_image()
                final_check(io, old, seed, signature)
                recovery = "automatic rollback verified"
            except (Exception, KeyboardInterrupt) as failure:
                recovery = "automatic rollback failed (" + (failure.code if isinstance(failure, BundleError) else type(failure).__name__) + ")"
        command = rollback_command(io.target.path, destination, io.is_image)
        raise BundleError("E_UPDATE_VERIFY", f"Original failure: {original}; {recovery}. Backup: {destination}. Recovery: {command}") from None
