# SPDX-License-Identifier: GPL-2.0-only
"""Bounded seed editing and credential-free presentation."""
import base64
import hashlib
import re

from .common import require
from .seed_rules import LIMIT, SPACE, parse, text_value, mirror_url, public_key

ALLOWED = {'': {'version'}, 'apt': {'mirror'}, 'display': {'refresh_hz'},
           'system': {'timezone', 'safe_mode'}, 'packages': {'install'}}
WIFI = re.compile(r'wifi "([^"\r\n]{1,128})"', re.I)
HEX = re.compile(r'[0-9a-fA-F]{64}')
FIELDS = {'apt_mirror': ('apt', 'mirror'), 'refresh_hz': ('display', 'refresh_hz'),
          'timezone': ('system', 'timezone'), 'packages': ('packages', 'install')}


def invalid(ok, item):
    require(ok, 'E_SEED_INVALID', 'Invalid ' + item + '.')


def load(data):
    data = data or b''
    sections, warnings = parse(data)
    invalid(sections, 'seed input (encoding, controls or size)')
    try:
        data.decode('utf-8')
    except UnicodeError:
        invalid(False, 'seed UTF-8')
    seen = set()
    for raw in data.decode().split('\n'):
        line = raw.strip(SPACE)
        if line.startswith('[') and line.endswith(']'):
            name = line[1:-1].strip(SPACE)
            m = WIFI.fullmatch(name)
            if m:
                invalid(m[1] not in seen, 'duplicate wifi label')
                seen.add(m[1])
    clean, discarded = {'': {'version': '1'}}, []
    for section, values in sections.items():
        m = WIFI.fullmatch(section)
        allowed = {'ssid', 'psk', 'country'} if m else ALLOWED.get(section)
        if allowed is None:
            discarded.append(section)
            continue
        canonical = 'wifi "' + m[1] + '"' if m else section
        clean.setdefault(canonical, {})
        for key, value in values.items():
            if key not in allowed:
                discarded.append(section + '/' + key)
            else:
                clean[canonical][key] = value
    invalid(clean[''].get('version', '1') == '1', 'version')
    return clean, sorted(discarded), bool(warnings) or any(l.strip().startswith(('#', ';')) for l in data.decode().splitlines())


def keys(data):
    invalid(len(data) <= LIMIT, 'authorized_keys size')
    try:
        lines = data.decode('utf-8').splitlines()
    except UnicodeError:
        invalid(False, 'authorized_keys UTF-8')
    result = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        key = public_key(line)
        invalid(key, 'authorized_keys line ' + str(number))
        if key not in result:
            result.append(key)
    invalid(result, 'authorized_keys (no valid keys)')
    return ''.join(result).encode()


def validate(sections):
    countries = set()
    for section, values in sections.items():
        if WIFI.fullmatch(section):
            invalid(text_value(values.get('ssid', ''), 32), 'wifi ssid')
            psk = values.get('psk', '')
            invalid(text_value(psk, 64) and (HEX.fullmatch(psk) or 8 <= len(psk.encode()) <= 63), 'wifi psk')
            country = values.get('country')
            if country is not None:
                invalid(re.fullmatch('[A-Za-z]{2}', country), 'wifi country')
                values['country'] = country.upper(); countries.add(country.upper())
            # Scanner strips surrounding whitespace: never silently change a credential.
            invalid(all(v == v.strip(SPACE) for v in values.values()), 'wifi surrounding whitespace')
    invalid(len(countries) <= 1, 'conflicting wifi country')
    if 'mirror' in sections.get('apt', {}):
        value = mirror_url(sections['apt']['mirror'])
        invalid(value, 'apt mirror'); sections['apt']['mirror'] = value
    if 'refresh_hz' in sections.get('display', {}):
        invalid(sections['display']['refresh_hz'] in ('60', '120'), 'refresh_hz')
    system = sections.get('system', {})
    if 'timezone' in system:
        invalid(re.fullmatch(r'[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*', system['timezone']), 'timezone')
    if 'safe_mode' in system:
        invalid(system['safe_mode'].lower() in ('true', 'false'), 'safe_mode')
        system['safe_mode'] = system['safe_mode'].lower()
    if 'install' in sections.get('packages', {}):
        packages = sections['packages']['install'].split()
        invalid(packages and all(re.fullmatch(r'[a-z0-9][a-z0-9+.-]{0,127}', p) for p in packages), 'packages')
        sections['packages']['install'] = ' '.join(sorted(set(packages)))


def render(sections):
    validate(sections)
    lines = ['version = 1']
    order = sorted(s for s in sections if WIFI.fullmatch(s)) + ['apt', 'display', 'system', 'packages']
    for section in order:
        if sections.get(section):
            lines += ['', '[' + section + ']']
            lines += [key + ' = ' + value for key, value in sorted(sections[section].items())]
    result = ('\n'.join(lines) + '\n').encode()
    invalid(len(result) <= LIMIT, 'seed size')
    # Includes label delimiters and scanner whitespace edge cases.
    invalid(parse(result)[0] == {s: v for s, v in sections.items() if v}, 'seed round-trip')
    return result


def edit(old, changes, *, password=None, replacement=None, addition=None):
    for option in ('wifi', 'remove_wifi'):
        label = changes.get(option)
        if label is not None:
            invalid(WIFI.fullmatch('wifi \"' + label + '\"') and text_value(label, 512), 'wifi label')
    sections, discarded, junk = load(old['seed.conf'])
    result = dict(old)
    if changes.get('remove_wifi') is not None:
        sections.pop('wifi "' + changes['remove_wifi'] + '"', None)
    if any(changes.get(k) is not None for k in ('ssid', 'country')) or password is not None:
        label = changes.get('wifi') or 'main'
        invalid(WIFI.fullmatch('wifi "' + label + '"') and text_value(label, 512), 'wifi label')
        values = sections.setdefault('wifi "' + label + '"', {})
        if changes.get('ssid') is not None:
            invalid(not HEX.fullmatch(values.get('psk', '')) or values.get('ssid') == changes['ssid'] or password is not None,
                    'SSID change for a derived/raw PSK requires a new psk source')
            values['ssid'] = changes['ssid']
        if password is not None:
            invalid(text_value(password, 64) and (HEX.fullmatch(password) or 8 <= len(password.encode()) <= 63), 'wifi psk')
            invalid(text_value(values.get('ssid', ''), 32), 'wifi ssid')
            values['psk'] = password if HEX.fullmatch(password) or not changes.get('derived_psk') else hashlib.pbkdf2_hmac(
                'sha1', password.encode(), values['ssid'].encode(), 4096, 32).hex()
        if changes.get('country') is not None:
            if changes['country'] == '':
                values.pop('country', None)
            else:
                values['country'] = changes['country']
    for option, (section, key) in FIELDS.items():
        if changes.get('no_' + option):
            sections.setdefault(section, {}).pop(key, None)
        elif changes.get(option) is not None:
            sections.setdefault(section, {})[key] = str(changes[option])
    if changes.get('safe_mode') is not None:
        sections.setdefault('system', {})['safe_mode'] = changes['safe_mode']
    result['seed.conf'] = render(sections)
    if replacement is not None:
        result['authorized_keys'] = keys(replacement)
    elif addition is not None:
        result['authorized_keys'] = keys((old['authorized_keys'] or b'') + b'\n' + keys(addition))
    elif old['authorized_keys'] is not None:
        keys(old['authorized_keys'])
    return result, discarded, junk


def redacted(state):
    sections, _ = parse(state['seed.conf'] or b'')
    result = {}
    for section, values in sections.items():
        m = WIFI.fullmatch(section)
        if m:
            result[section] = {k: v for k, v in values.items() if k in ('ssid', 'country')}
            result[section]['psk'] = '<set: derived-psk>' if HEX.fullmatch(values.get('psk', '')) else '<set: plaintext>' if values.get('psk') else '<unset>'
            if HEX.fullmatch(values.get('psk', '')):
                result[section]['note'] = 'WPA2-only (not usable with WPA3/SAE)'
        elif section in ALLOWED:
            result[section] = {k: v for k, v in values.items() if k in ALLOWED[section]}
    result['authorized_keys'] = []
    if state['authorized_keys']:
        for line in state['authorized_keys'].decode('utf-8', 'replace').splitlines():
            key = public_key(line)
            if key:
                kind, encoded = key.split()
                fingerprint = base64.b64encode(hashlib.sha256(base64.b64decode(encoded)).digest()).decode().rstrip('=')
                result['authorized_keys'].append(kind + ' SHA256:' + fingerprint)
    return result


def preview(old, new, discarded=(), junk=False):
    before, after = redacted(old), redacted(new)
    for section, values in after.items():
        if WIFI.fullmatch(section) and parse(old['seed.conf'] or b'')[0].get(section, {}).get('psk') == parse(new['seed.conf'] or b'')[0].get(section, {}).get('psk'):
            before.get(section, {})['psk'] = values['psk'] = '<unchanged>'
    # Only names of unknown content, never values.
    return {'before': before, 'after': after, 'discarded_names': list(discarded),
            'comments_or_junk_discarded': junk, 'timezone_notice': 'Timezone not checked against tzdata.',
            'files': {n: 'unchanged' if old[n] == new[n] else 'delete' if new[n] is None else 'replace' for n in old}}
