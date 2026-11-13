# SPDX-License-Identifier: GPL-2.0-only
"""Seed backup, preview, confirmation and verified recovery."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import sys

from .common import BundleError, no_links, read_json, require
from .maintenance import backup_location, version
from .seed_io import NAMES, SeedBackend, inspect, read_file, switch
from . import seed_model as model

WARNING = 'Backup contains Wi-Fi credentials; keep it private.'
PSK_WARNING = ('Derived/raw PSK is WPA2-PSK only: it cannot be used with WPA3 (SAE) or WPA2/WPA3 mixed networks '
               'and may prevent connection. Use it only when the network is confirmed to be WPA2-only.')


def record(data):
    return {'exists': data is not None, 'size': len(data) if data is not None else None,
            'sha256': hashlib.sha256(data).hexdigest() if data is not None else None}


def validate_backup(path):
    try:
        path = Path(path).absolute(); no_links(path)
        require(path.is_dir(), 'E_SEED_RESTORE_INVALID', 'Backup directory is missing.')
        entries = list(path.iterdir())
        require(all(stat.S_ISREG(p.lstat().st_mode) for p in entries), 'E_SEED_RESTORE_INVALID', 'Backup entries must be regular files.')
        meta = read_json(path / 'backup.json')
        require(set(meta) == {'schema', 'created', 'bundle_id', 'files'} and type(meta['schema']) is int and meta['schema'] == 1,
                'E_SEED_RESTORE_INVALID', 'Invalid backup schema.')
        version(meta['bundle_id']); datetime.strptime(meta['created'], '%Y-%m-%dT%H:%M:%SZ')
        require(set(meta['files']) == set(NAMES), 'E_SEED_RESTORE_INVALID', 'Invalid file inventory.')
        state = {}
        for name in NAMES:
            row = meta['files'][name]
            require(type(row['exists']) is bool, 'E_SEED_RESTORE_INVALID', 'Invalid existence record.')
            require(set(row) == {'exists', 'size', 'sha256'} and
                    (type(row['size']) is int and isinstance(row['sha256'], str) if row['exists'] else row['size'] is None and row['sha256'] is None),
                    'E_SEED_RESTORE_INVALID', 'Invalid file record types.')
            state[name] = read_file(path / name, 'E_SEED_RESTORE_INVALID') if row['exists'] else None
            require(row == record(state[name]), 'E_SEED_RESTORE_INVALID', 'Backup hash or size differs.')
        require({p.name for p in entries} == {'backup.json'} | {n for n in NAMES if state[n] is not None},
                'E_SEED_RESTORE_INVALID', 'Backup inventory is not exact.')
        return state, meta
    except (BundleError, OSError, ValueError, KeyError, TypeError, AttributeError):
        raise BundleError('E_SEED_RESTORE_INVALID', 'Backup schema, inventory or hashes are invalid.') from None


def save_backup(destination, state, bundle_id, stamp):
    try:
        no_links(destination)
        destination.parent.mkdir(mode=0o700, exist_ok=True)
        destination.mkdir(mode=0o700)
        os.chmod(destination, 0o700)
        meta = {'schema': 1, 'created': stamp.strftime('%Y-%m-%dT%H:%M:%SZ'),
                'bundle_id': bundle_id, 'files': {n: record(state[n]) for n in NAMES}}
        files = dict(state, **{'backup.json': (json.dumps(meta, sort_keys=True, indent=2) + '\n').encode()})
        for name, data in files.items():
            if data is None:
                continue
            fd = os.open(destination / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
        for folder in (destination, destination.parent):
            fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        require(validate_backup(destination)[0] == state, 'E_SEED_BACKUP', 'Backup re-read differs.')
    except (BundleError, OSError, ValueError):
        raise BundleError('E_SEED_BACKUP', 'Backup creation or verification failed; card unchanged.') from None


def command(target, backup, image):
    return 'dby-install seed restore ' + ('--image-file ' if image else '--device ') + shlex.quote(str(target)) + ' --backup ' + shlex.quote(str(backup))


def check(io, desired, boot, signature):
    with io.snapshot() as (root, actual):
        _, state, current_boot = inspect(root)
        require(state == desired and current_boot == boot and actual == signature,
                'E_SEED_VERIFY', 'Seed or immutable card content differs after reopen.')
        # Exact bytes imply identical scanner semantics, including raw restored input.
        require(model.parse(state['seed.conf'] or b'') == model.parse(desired['seed.conf'] or b''),
                'E_SEED_VERIFY', 'Seed parsing differs after reopen.')


def output(value, states, emit, secrets=()):
    text = json.dumps(value, indent=2, ensure_ascii=True) if not isinstance(value, str) else value
    # Defence in depth if another field or unknown key happens to contain a PSK.
    secrets = set(secrets) | {v.get('psk') for state in states for v in model.parse(state['seed.conf'] or b'')[0].values() if v.get('psk')}
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, '<redacted>').replace(json.dumps(secret)[1:-1], '<redacted>')
    emit(text)


def operate(action, target, *, changes=None, password=None, replacement=None, addition=None,
            backup_dir=None, backup=None, dry_run=False, ask=input, emit=print,
            wizard=None, backend_factory=SeedBackend, hook=lambda *args: None):
    changes = changes or {}
    require(action in ('show', 'status', 'set', 'wizard', 'restore', 'on', 'off'), 'E_SEED_INVALID', 'Unknown seed operation.')
    writing = action not in ('show', 'status')
    require(not writing or action == 'restore' or backup_dir is not None, 'E_SEED_BACKUP', '--backup-dir is required.')
    require(action != 'restore' or backup is not None, 'E_SEED_RESTORE_INVALID', '--backup is required.')
    if action == 'set':
        require(any(v is not None and v is not False for k, v in changes.items() if k not in ('wifi', 'plaintext_psk', 'derived_psk')) or
                password is not None or replacement is not None or addition is not None,
                'E_SEED_INVALID', 'seed set requires at least one change.')
    restored = validate_backup(backup)[0] if action == 'restore' else None
    stamp = datetime.now(timezone.utc)
    with backend_factory(target, dry_run=dry_run or not writing) as io:
        with io.snapshot() as (root, signature):
            doc, old, boot = inspect(root)
        if not writing:
            view = model.redacted(old) if action == 'show' else {'safe_mode': model.parse(old['seed.conf'] or b'')[0].get('system', {}).get('safe_mode', '').lower() == 'true'}
            output(view, [old], emit); return view
        if action == 'wizard':
            require(wizard is not None, 'E_SEED_NO_TTY', 'wizard requires a terminal.')
            changes, password, replacement, addition = wizard(old)
        if action in ('on', 'off'):
            changes = {'safe_mode': 'true' if action == 'on' else 'false'}
        if action == 'restore':
            new, discarded, junk = restored, [], False
        else:
            new, discarded, junk = model.edit(old, changes, password=password, replacement=replacement, addition=addition)
        preview = model.preview(old, new, discarded, junk)
        preview['notices'] = [PSK_WARNING] if changes.get('derived_psk') or password is not None and model.HEX.fullmatch(password) else []
        for notice in preview['notices']:
            print(notice, file=sys.stderr)
        output(preview, [old, new], emit, secrets=(password,) if password else ())
        emit(WARNING)
        if password is not None and model.HEX.fullmatch(password):
            emit('Input is already a raw 64-digit PSK; stored unchanged.')
        if model.parse(new['seed.conf'] or b'')[0].get('system', {}).get('safe_mode', '').lower() == 'true':
            emit('WARNING: screen will remain black; SSH/Wi-Fi remain available; touch/display modules are blocked.')
            emit('Disable: dby-install safe-mode off ' + ('--image-file ' if io.is_image else '--device ') + shlex.quote(str(target.path)) + ' --backup-dir BACKUP_DIR')
        if old == new:
            emit('no change'); return preview
        if dry_run:
            return preview
        require(ask('Type the full target name to change its seed: ').strip() == str(target.path),
                'E_TARGET_CONFIRM', 'Full target name confirmation did not match.')
        # Restore also backs up the displaced seed, beside its source backup.
        host = Path(backup).absolute().parent if action == 'restore' else backup_dir
        try:
            candidate = backup_location(host, io, doc['bundle_id'], stamp)
            destination = candidate.with_name('seed-' + candidate.name.removeprefix('p1-'))
        except (BundleError, OSError):
            raise BundleError('E_SEED_BACKUP', 'Invalid host backup destination.') from None
        with io.locked():
            with io.snapshot() as (root, current_signature):
                current_doc, current, current_boot = inspect(root)
                require(current_doc == doc and current == old and current_boot == boot and current_signature == signature,
                        'E_TARGET_CHANGED', 'Card contents changed after preview.')
                if action == 'restore':
                    require(validate_backup(backup)[0] == new, 'E_SEED_RESTORE_INVALID', 'Backup changed after preview.')
                save_backup(destination, old, doc['bundle_id'], stamp)
            switched = False
            def mark():
                nonlocal switched
                switched = True
            try:
                switch(io, new, mark=mark, hook=hook)
                hook('before_readback', io)
                check(io, new, boot, signature)
            except (Exception, KeyboardInterrupt) as error:
                if not switched:
                    raise
                original = error.code if isinstance(error, BundleError) else type(error).__name__
                try:
                    state, _ = validate_backup(destination)
                    switch(io, state, mark=lambda: None, recovering=True)
                    io.restore_exact_image()
                    check(io, old, boot, signature)
                    recovery = 'Automatic rollback verified.'
                except (Exception, KeyboardInterrupt):
                    recovery = 'Automatic rollback failed; manual recovery required.'
                raise BundleError('E_SEED_VERIFY', 'Original failure: ' + original + '. ' + recovery + ' Backup: ' + str(destination) + '. ' + command(target.path, destination, io.is_image)) from None
        emit('OK: seed changed and verified. Backup: ' + str(destination))
        emit(command(target.path, destination, io.is_image))
        preview['backup'] = str(destination)
        return preview
