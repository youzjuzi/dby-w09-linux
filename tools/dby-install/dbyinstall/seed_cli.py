# SPDX-License-Identifier: GPL-2.0-only
"""Explicit seed CLI; passwords only arrive through a private file or getpass."""
import argparse
import getpass
import sys
import warnings
from pathlib import Path

from .common import BundleError, require
from .seed_io import read_file
from .seed import operate, output
from . import seed_model as model
from .targets import BlockDeviceTarget, ImageFileTarget


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally repeats unrecognized arguments, including accidental passwords.
        self.exit(2, 'E_SEED_INVALID: Invalid or conflicting command options; use --help.\n')


def tty():
    require(sys.stdin.isatty() and sys.stderr.isatty(), 'E_SEED_NO_TTY', 'Interactive input requires a terminal.')


def secret_prompt(prompt):
    tty()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            return getpass.getpass(prompt)
    except (getpass.GetPassWarning, OSError, EOFError):
        raise BundleError('E_SEED_NO_TTY', 'Cannot read a password without echo.') from None


def confirmed_password(prompt, *, secret=None):
    secret = secret or secret_prompt
    first = secret(prompt)
    second = secret('Repeat Wi-Fi passphrase: ')
    require(first == second, 'E_SEED_PSK_MISMATCH', 'Passphrase entries did not match; nothing written.')
    return first


def password_file(path):
    data = read_file(path, 'E_SEED_PSK_SOURCE', secret=True)
    try:
        value = data.decode('utf-8').removesuffix('\n').removesuffix('\r')
    except UnicodeError:
        raise BundleError('E_SEED_PSK_SOURCE', 'Invalid password file encoding.') from None
    require(value and '\n' not in value and '\r' not in value, 'E_SEED_PSK_SOURCE', 'Password file must contain one nonempty line.')
    return value


def wizard(old, *, ask=input, secret=secret_prompt, emit=print):
    tty()
    output(model.redacted(old), [old], emit)
    emit('Enter keeps the current value; - removes a setting. Public keys cannot be cleared.')
    changes, password, replacement, addition = {}, None, None, None
    sections, _ = model.parse(old['seed.conf'] or b'')
    labels = [match[1] for section in sections if (match := model.WIFI.fullmatch(section))]
    emit('Existing Wi-Fi labels:' if labels else 'No existing networks.')
    for label in labels:
        emit(label)
    default = labels[0] if len(labels) == 1 else 'main' if not labels else None
    prompt = 'Wi-Fi label' + (f' [{default}]' if default else '') + ' (existing or new): '
    while True:
        label = ask(prompt) or default
        if not label:
            emit('Enter an existing label or a new label; there is no default.')
            continue
        if label not in labels:
            emit('This will create a new network. Repeat the label to confirm; otherwise choose again.')
            if ask('Repeat new Wi-Fi label: ') != label:
                continue
        break
    changes['wifi'] = label
    ssid = ask('SSID [keep; - removes this network]: ')
    if ssid == '-':
        changes['remove_wifi'] = label
    else:
        if ssid:
            changes['ssid'] = ssid
        value = confirmed_password('Wi-Fi passphrase [keep; - removes this network]: ', secret=secret)
        if value == '-':
            changes['remove_wifi'] = label
        elif value:
            password = value
        country = ask('Country [keep; - removes]: ')
        if country:
            changes['country'] = '' if country == '-' else country
    if changes.get('remove_wifi'):
        changes.pop('ssid', None); changes.pop('country', None); password = None
    for option in model.FIELDS:
        value = ask(option.replace('_', ' ') + ' [keep; - removes]: ')
        if value == '-':
            changes['no_' + option] = True
        elif value:
            changes[option] = value
    value = ask('safe_mode true/false [keep; - disables]: ')
    if value:
        changes['safe_mode'] = 'false' if value == '-' else value
    value = ask('Authorized keys file [keep; replaces all keys]: ')
    if value:
        require(value != '-', 'E_SEED_INVALID', 'Public keys cannot be cleared.')
        replacement = read_file(Path(value))
    return changes, password, replacement, addition


def main(argv):
    parser = Parser(description='Edit installed-card seeds; never mount p2. Credentials never belong in arguments.', allow_abbrev=False)
    parser.add_argument('command', choices=('seed', 'safe-mode'))
    parser.add_argument('action')
    targets = parser.add_mutually_exclusive_group(required=True)
    targets.add_argument('--device', type=Path); targets.add_argument('--image-file', type=Path)
    parser.add_argument('--backup-dir', type=Path); parser.add_argument('--backup', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--wifi'); parser.add_argument('--ssid'); parser.add_argument('--country')
    parser.add_argument('--remove-wifi')
    passwords = parser.add_mutually_exclusive_group()
    passwords.add_argument('--psk-prompt', action='store_true'); passwords.add_argument('--psk-file', type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--derived-psk', action='store_true', help='Explicit WPA2-only PSK derivation; incompatible with WPA3/SAE')
    modes.add_argument('--plaintext-psk', action='store_true', help=argparse.SUPPRESS)
    for option in model.FIELDS:
        pair = parser.add_mutually_exclusive_group()
        pair.add_argument('--' + option.replace('_', '-'))
        pair.add_argument('--no-' + option.replace('_', '-'), action='store_true')
    keys = parser.add_mutually_exclusive_group()
    keys.add_argument('--authorized-keys', type=Path); keys.add_argument('--add-authorized-keys', type=Path)
    args = parser.parse_args(argv)
    try:
        allowed = ('show', 'set', 'wizard', 'restore') if args.command == 'seed' else ('status', 'on', 'off')
        require(args.action in allowed, 'E_SEED_INVALID', 'Invalid subcommand.')
        options = {k: v for k, v in vars(args).items() if k not in
                   ('command', 'action', 'device', 'image_file', 'backup_dir', 'backup', 'dry_run')}
        require(args.action == 'set' or not any(options.values()), 'E_SEED_INVALID', 'Setting options apply only to seed set.')
        require(args.action == 'restore' or args.backup is None, 'E_SEED_INVALID', '--backup applies only to restore.')
        require(args.action not in ('show', 'status', 'restore') or args.backup_dir is None,
                'E_SEED_INVALID', '--backup-dir applies only to editing commands.')
        require(not args.derived_psk or args.psk_prompt or args.psk_file, 'E_SEED_PSK_SOURCE', '--derived-psk requires a password source.')
        require(not args.remove_wifi or not any((args.ssid is not None, args.country is not None, args.psk_prompt, args.psk_file)),
                'E_SEED_INVALID', 'Remove a network in a separate invocation.')
        require(args.country is None or model.re.fullmatch('[A-Za-z]{2}', args.country), 'E_SEED_INVALID', 'Invalid wifi country.')
        if args.action == 'wizard' or args.psk_prompt:
            tty()
        password = confirmed_password('Wi-Fi passphrase: ') if args.psk_prompt else password_file(args.psk_file) if args.psk_file else None
        replacement = read_file(args.authorized_keys) if args.authorized_keys else None
        addition = read_file(args.add_authorized_keys) if args.add_authorized_keys else None
        target = ImageFileTarget(args.image_file) if args.image_file else BlockDeviceTarget(args.device)
        operate(args.action, target, changes=options, password=password, replacement=replacement, addition=addition,
                backup_dir=args.backup_dir, backup=args.backup, dry_run=args.dry_run, wizard=wizard)
        return 0
    except BundleError as error:
        print(error, file=sys.stderr); return 1
    except (OSError, ValueError, KeyError, TypeError, EOFError, KeyboardInterrupt):
        print('E_SEED_INVALID: Seed operation failed; raw details withheld.', file=sys.stderr); return 1
