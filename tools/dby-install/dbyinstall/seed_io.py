# SPDX-License-Identifier: GPL-2.0-only
"""Seed-only transactions. Never mount p2, even during recovery."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat
import tempfile

from .common import (BOOT_FILES, BundleError, P1_START, P1_SECTORS, dependencies,
                     file_record, partition, require, run)
from . import image, privileged
from .maintenance import managed
from .maintenance_io import Backend, DirectoryStore, FatStore, replace_region
from .seed_rules import LIMIT

NAMES = ('seed.conf', 'authorized_keys')


def read_file(path, code='E_SEED_INVALID', *, secret=False):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(fd, 'rb') as stream:
            st = os.fstat(stream.fileno())
            require(stat.S_ISREG(st.st_mode) and st.st_size <= LIMIT and
                    (not secret or not st.st_mode & 0o077), code, 'Invalid file type, size or private permissions.')
            data = stream.read(LIMIT + 1)
            require(len(data) <= LIMIT, code, 'Input exceeds size limit.')
            return data
    except OSError:
        raise BundleError(code, 'Cannot read the requested input file.') from None


def inspect(root):
    try:
        doc = managed(root)
    except BundleError as error:
        code = 'E_SEED_UNMANAGED' if error.code == 'E_UPDATE_UNMANAGED' else 'E_SEED_CARD_INCONSISTENT'
        raise BundleError(code, 'A valid installed card with matching boot files is required.') from None
    seed = root / 'dby-seed'
    require(not seed.is_symlink() and (not seed.exists() or seed.is_dir()), 'E_SEED_INVALID', 'Invalid seed directory.')
    require(not seed.exists() or not any(p.name.lower().endswith('.new') for p in seed.iterdir()),
            'E_SEED_LEFTOVER', 'Seed staging exists; inspect it manually before retrying.')
    state = {}
    for name in NAMES:
        path = seed / name
        state[name] = read_file(path) if path.exists() or path.is_symlink() else None
    boot = {name: file_record(root / name) for name in ['manifest.json', *('UBOOT/' + n for n in BOOT_FILES)]}
    return doc, state, boot


class SeedDirectoryStore(DirectoryStore):
    def rename(self, source, destination):
        require(destination in ('dby-seed/' + n for n in NAMES), 'E_SEED_VERIFY', 'Invalid seed destination.')
        os.replace(self.root / source, self.root / destination)


class SeedFatStore(FatStore):
    def rename(self, source, destination):
        self.command('mren', '-D', 'o', '::/' + source, '::/' + destination)

    def snapshot(self, root):
        # Never traverse unrelated FAT directories or seed subdirectories.
        for name in ('UBOOT', 'manifest.json'):
            if self.exists(name):
                self.command('mcopy', '-s', '::/' + name, root)
        if self.exists('dby-seed'):
            names = self.command('mdir', '-b', '::/dby-seed/').decode('utf-8', 'replace').splitlines()
            require(not any(n.rstrip('/').lower().endswith('.new') for n in names),
                    'E_SEED_LEFTOVER', 'Seed staging exists; inspect it manually before retrying.')
            (root / 'dby-seed').mkdir()
            for name in NAMES:
                if self.exists('dby-seed/' + name):
                    self.read_file('dby-seed/' + name, root / 'dby-seed' / name)


@contextmanager
def p1_mount(device, *, writable=False):
    with privileged.workspace() as work:
        path = work / 'p1'; path.mkdir()
        try:
            run(['mount', '-t', 'vfat', '-o', ('rw' if writable else 'ro') + ',nosuid,nodev,noexec', partition(device, 1), path])
            yield path
        finally:
            if path in privileged.mounted_paths():
                run(['umount', path])
            require(path not in privileged.mounted_paths(), 'E_INSTALL_CLEANUP', 'Cannot unmount seed workspace.')


class SeedBackend(Backend):
    def __enter__(self):
        if not self.is_image and not self.dry_run:
            privileged.require_root()
            dependencies(('sfdisk', 'blockdev', 'mount', 'umount', 'sync', 'blkid'))
        else:
            dependencies(('mcopy', 'mdir', 'blkid'))
        self.temporary = tempfile.TemporaryDirectory(prefix='dby-seed-')
        self.work = Path(self.temporary.name)
        return self

    @contextmanager
    def snapshot(self):
        with tempfile.TemporaryDirectory(dir=self.work) as temporary:
            work = Path(temporary)
            if self.is_image:
                self.check_image_name()
                layout = image.partitions(self.media, self.size)
                # blkid probes only the p2 superblock: no journal replay/mount.
                image.check_label(self.target.path, 'ubuntu-root', 'ext4', offset=layout[1][0], size=layout[1][1])
                part = work / 'p1.img'; image.new_sparse(part, P1_SECTORS * 512)
                image.sparse_copy(self.media, part, P1_START * 512, 0, P1_SECTORS * 512)
                image.check_label(part, 'UBOOT', 'vfat')
                with self.media.open('rb') as stream:
                    signature = hashlib.sha256(stream.read(512)).hexdigest()
            else:
                privileged.check_table(self.target.path, self.size)
                image.geometry(*(Path(partition(self.target.path, n)) for n in (1, 2)), labels_only=True)
                signature = run(['sfdisk', '--json', self.target.path])
                part = Path(partition(self.target.path, 1))
            if self.is_image or self.dry_run:
                root = work / 'p1'; root.mkdir()
                SeedFatStore(part).snapshot(root)
                yield root, signature
            else:
                with p1_mount(self.target.path) as root:
                    self.forbidden_roots.append(root)
                    yield root, signature

    @contextmanager
    def transaction(self):
        if self.is_image:
            dependencies(('mmd', 'mren', 'mdel'))
            with tempfile.TemporaryDirectory(dir=self.work) as temporary:
                part = Path(temporary) / 'p1.img'; image.new_sparse(part, P1_SECTORS * 512)
                self.check_image_name()
                image.sparse_copy(self.media, part, P1_START * 512, 0, P1_SECTORS * 512)
                yield SeedFatStore(part)
                self.check_image_name()
                replace_region(part, self.media, P1_START * 512, P1_SECTORS * 512)
        else:
            with p1_mount(self.target.path, writable=True) as root:
                self.forbidden_roots.append(root)
                yield SeedDirectoryStore(root)


def switch(io, state, *, mark, hook=lambda *args: None, recovering=False):
    with tempfile.TemporaryDirectory(dir=io.work) as temporary, io.transaction() as store:
        work = Path(temporary)
        if not store.exists('dby-seed'):
            store.mkdir('dby-seed')
        for name in NAMES:
            stage = 'dby-seed/' + name + '.new'
            if recovering:
                store.remove(stage)
            require(not store.exists(stage), 'E_SEED_LEFTOVER', 'Seed staging appeared after preview.')
            if state[name] is not None:
                source = work / name
                fd = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(state[name])
                store.copy(source, stage)
                store.sync()
                check = work / (name + '.check'); store.read_file(stage, check)
                require(read_file(check) == state[name], 'E_SEED_VERIFY', 'Staged seed read-back differs.')
        mark()
        for name in NAMES:
            dest = 'dby-seed/' + name
            if state[name] is None:
                store.remove(dest)
            else:
                store.rename(dest + '.new', dest)
            hook('after_switch', store)
        store.sync()
