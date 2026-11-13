# SPDX-License-Identifier: GPL-2.0-only
"""Destructive p2-only reinstall with immutable p1 and partition-table checks."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import tempfile

from .common import BOOT_FILES, BundleError, P1_START, P1_SECTORS, dependencies, partition, require, run
from . import core, image, privileged
from .archive import verify_root
from .maintenance import managed, leftovers
from .maintenance_io import FatStore, dump_contract, region_hash, replace_region
from .seed_io import SeedBackend

LOSS = 'p2 will be reformatted. All user data, including every home directory, installed software and SSH host keys, will be lost.'
NO_BACKUP = 'No user-data backup is made; back up anything you need yourself before continuing.'


@contextmanager
def root_mount(device, *, readonly=False):
    with privileged.workspace() as work:
        path = work / 'p2'; path.mkdir()
        try:
            run(['mount', '-t', 'ext4', '-o', ('ro,noload' if readonly else 'rw') + ',nosuid,nodev,noexec', partition(device, 2), path])
            yield path
        finally:
            if path in privileged.mounted_paths():
                run(['umount', path])
            require(path not in privileged.mounted_paths(), 'E_INSTALL_CLEANUP', 'Cannot unmount p2 workspace.')


class Backend(SeedBackend):
    def immutable(self):
        self.check_image_name()
        with self.media.open('rb') as stream:
            table = hashlib.sha256(stream.read(512)).hexdigest()
        return {'table_sha256': table, 'p1_sha256': region_hash(self.media, P1_START * 512, P1_SECTORS * 512)}

    @contextmanager
    def snapshot(self):
        with super().snapshot() as (root, _):
            if self.is_image:
                layout = image.partitions(self.media, self.size)
                part = root.parent / 'p2-inspect.img'; image.new_sparse(part, layout[1][1])
                image.sparse_copy(self.media, part, layout[1][0], 0, layout[1][1])
                image.geometry(root.parent / 'p1.img', part)
                store = FatStore(root.parent / 'p1.img')
                require(not any(store.exists(n) for n in ('UBOOT.new', 'UBOOT.old', 'manifest.json.new')),
                        'E_UPDATE_LEFTOVER', 'Interrupted device-package staging exists; repair it before reinstalling.')
            else:
                sectors = privileged.check_table(self.target.path, self.size)
                layout = [(P1_START * 512, P1_SECTORS * 512), ((P1_START + P1_SECTORS) * 512, sectors * 512)]
                image.geometry(*(Path(partition(self.target.path, n)) for n in (1, 2)))
                if self.dry_run:
                    store = FatStore(Path(partition(self.target.path, 1)))
                    require(not any(store.exists(n) for n in ('UBOOT.new', 'UBOOT.old', 'manifest.json.new')),
                            'E_UPDATE_LEFTOVER', 'Interrupted device-package staging exists; repair it before reinstalling.')
                else:
                    leftovers(root)
            yield root, dict(self.immutable(), layout=layout)

    def rewrite(self, bundle, preview, entries, *, hook):
        if self.is_image:
            dependencies(image.DEPENDENCIES)
            with tempfile.TemporaryDirectory(dir=self.work) as temporary:
                work = Path(temporary); part = work / 'p2.img'; root = work / 'root'; root.mkdir()
                image.new_sparse(part, preview['p2']['size_bytes'])
                # Publish an empty filesystem first: old data is gone even if extraction fails.
                privileged.format_rootfs(part)
                self.check_image_name()
                replace_region(part, self.media, preview['p2']['start'] * 512, preview['p2']['size_bytes'])
                hook('after_format', root)
                core.extract_rootfs(bundle, preview['rootfs_archive'], root, ownership=False, execute=run)
                hook('after_extract', root)
                core.verify_p2_contract(root, preview['rootfs_kind'])
                verify_root(root, entries, ownership=False)
                image.format_rootfs(part, root, entries, work)
                self.check_image_name()
                replace_region(part, self.media, preview['p2']['start'] * 512, preview['p2']['size_bytes'])
        else:
            privileged.format_rootfs(partition(self.target.path, 2))
            with root_mount(self.target.path) as root:
                hook('after_format', root)
                core.extract_rootfs(bundle, preview['rootfs_archive'], root, ownership=True, execute=run)
                hook('after_extract', root)
                core.verify_p2_contract(root, preview['rootfs_kind'])
                verify_root(root, entries)
                run(['sync', '-f', root])

    def readback(self, preview, entries, *, no_readback):
        if self.is_image:
            with tempfile.TemporaryDirectory(dir=self.work) as temporary:
                work = Path(temporary); part = work / 'p2.img'; root = work / 'root'; root.mkdir()
                layout = image.partitions(self.media, self.size)
                require(layout[1] == (preview['p2']['start'] * 512, preview['p2']['size_bytes']), 'E_INSTALL_TABLE', 'p2 size changed.')
                image.new_sparse(part, layout[1][1]); image.sparse_copy(self.media, part, layout[1][0], 0, layout[1][1])
                # Geometry helper expects a FAT source; use a private p1 copy, read only.
                fat = work / 'p1.img'; image.new_sparse(fat, P1_SECTORS * 512)
                image.sparse_copy(self.media, fat, P1_START * 512, 0, P1_SECTORS * 512)
                image.geometry(fat, part)
                if no_readback:
                    dump_contract(part, root)
                else:
                    run(['debugfs', '-R', 'rdump / ' + image.quote(root), part])
                    verify_root(root, entries, ownership=False, xattrs=False, permissions=False, hardlinks=False)
                    image.verify_inodes(part, entries)
                core.verify_p2_contract(root, preview['rootfs_kind'])
        else:
            privileged.check_table(self.target.path, self.size, expected_p2=preview['p2']['sectors'])
            image.geometry(*(Path(partition(self.target.path, n)) for n in (1, 2)))
            with root_mount(self.target.path, readonly=True) as root:
                core.verify_p2_contract(root, preview['rootfs_kind'])
                if not no_readback:
                    verify_root(root, entries)


def card(root, document):
    try:
        current = managed(root)
    except BundleError as error:
        code = 'E_REINSTALL_UNMANAGED' if error.code == 'E_UPDATE_UNMANAGED' else 'E_REINSTALL_CARD_INCONSISTENT'
        raise BundleError(code, 'An installed card with a valid manifest and matching boot files is required.') from None
    seed = root / "dby-seed"
    require(not seed.is_dir() or not any(p.name.lower().endswith(".new") for p in seed.iterdir()),
            "E_SEED_LEFTOVER", "Seed staging exists; repair it before reinstalling.")
    require(current['bundle_id'] == document['bundle_id'] and all(current['files']['p1/UBOOT/' + n] == document['files']['p1/UBOOT/' + n] for n in BOOT_FILES),
            'E_REINSTALL_BUNDLE_MISMATCH', 'Use update to install this bundle device package first, then rerun reinstall-system.')


def operate(target, bundle, *, minimal=False, no_readback=False, dry_run=False, ask=input, emit=print,
            backend_factory=Backend, hook=lambda *args: None):
    bundle = Path(bundle).absolute()
    document, entries, total = core.prepare(bundle, minimal=minimal)
    require(entries.get("etc/dby-rootfs-kind", {}).get("content") == (b"test\n" if minimal else b"full\n"),
            "E_INSTALL_KIND", "Selected rootfs must contain the matching kind contract.")
    with backend_factory(target, dry_run=dry_run) as io:
        if io.is_image:
            image.check_archive(entries); image.quote(io.work)
        dependencies(('mkfs.ext4', 'tar', 'zstd', 'sync'))
        with io.snapshot() as (root, before):
            card(root, document)
        offset, size = before['layout'][1]
        require(size >= int(total * 1.2) + 256 * 1024**2, 'E_REINSTALL_P2_TOO_SMALL',
                'Rootfs plus free-space margin does not fit existing p2; use install to repartition for a larger system.')
        preview = {'action': 'reinstall-system', 'target': str(target.path), 'kind': 'image' if io.is_image else 'block-device',
                   'rootfs_kind': 'test' if minimal else 'full', 'rootfs_archive': 'rootfs-test.tar.zst' if minimal else 'rootfs.tar.zst',
                   'p2': {'start': offset // 512, 'sectors': size // 512, 'size_bytes': size}, 'bundle_id': document['bundle_id'],
                   'warning': LOSS, 'backup_notice': NO_BACKUP, 'preserved': 'p1 (device package and dby-seed) and partition table remain unchanged.',
                   'confirmation': str(target.path) + ' p2', 'readback': {'p1': 'whole partition hash', 'p2': 'metadata' if no_readback else 'full'}}
        if not io.is_image:
            preview.update(model=io.identity.get('model', ''), serial=io.identity.get('serial', ''))
        emit(json.dumps(preview, sort_keys=True, indent=2, ensure_ascii=True))
        if dry_run:
            return preview
        require(ask('Type the full target name followed by p2 to destroy its system/data: ').strip() == preview['confirmation'],
                'E_TARGET_CONFIRM', 'Confirmation must be the full target name followed by p2.')
        with io.locked():
            with io.snapshot() as (root, actual):
                require(actual == before, 'E_TARGET_CHANGED', 'Target changed after preview.')
                card(root, document)
            require(core.prepare(bundle, minimal=minimal)[0] == document, 'E_INSTALL_CHANGED', 'Bundle changed after preview.')
            failure = None
            try:
                io.rewrite(bundle, preview, entries, hook=hook)
                hook('before_readback', io)
                io.readback(preview, entries, no_readback=no_readback)
            except (Exception, KeyboardInterrupt) as error:
                failure = error.code if isinstance(error, BundleError) else type(error).__name__
            try:
                unchanged = io.immutable() == {k: before[k] for k in ('table_sha256', 'p1_sha256')}
            except (Exception, KeyboardInterrupt):
                unchanged = False
            if failure or not unchanged:
                status = 'Device package and seed are unaffected; p1 and table hashes verified.' if unchanged else 'Cannot confirm p1/table preservation; inspect them before retrying.'
                raise BundleError('E_REINSTALL_FAILED', 'p2 is incomplete. ' + status + ' Correct the cause and rerun reinstall-system (or install). Original failure: ' + (failure or 'immutable-region check failed') + '.') from None
        if no_readback:
            emit(core.NO_READBACK_NOTICE)
        emit('OK: reinstalled ' + preview['rootfs_kind'] + ' rootfs from bundle ' + document['bundle_id'] + '; p1 and partition table unchanged.')
        emit('First boot will run setup again and regenerate SSH host keys; update client known_hosts after verifying the device.')
        return preview
