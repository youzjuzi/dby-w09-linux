#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Extract private build inputs; never distribute the generated header.

Only the pinned, normalized owner configuration is accepted. No override flag.
This tool grants no rights to the input files or to their derived data.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

FINGERPRINTS = (
    'e600667f7d86d458b39c11b7e6bb61387311d1983bc62f1083ec8e63b2e3b157',
    'ba82ae2e4353c347f839451373aa78accfc434060eddc7208b38c6fdcee8ae89',
    'c7526a3cd3ad1d674c1a1d16e2027887f1126e8aad77ad23cd6b012f24764da1',
    '68888a0da0b0e7bdb626dbad29f2cd8a2154dce0d732cc94741fe53ba3cd6865',
)
HEADER_NAME = 'dby-tfa9874-tables-private.h'
BANNER = 'PRIVATE OWNER DATA: DO NOT DISTRIBUTE'


class InvalidInput(ValueError):
    """A deliberately value-free diagnostic."""


def parse(data):
    """Normalize the entire XML tree, including all scenes and metadata.

    Formatting, XML attribute order, numeric spelling and hash-delimited comments
    do not affect the digest. Every element, attribute and operation does.
    """
    if len(data) > 1024 * 1024 or re.search(br'<!\s*(?:DOCTYPE|ENTITY)', data, re.I):
        raise InvalidInput('unsupported XML size or declarations')
    try:
        text = data.decode('utf-8')
        if '\x00' in text:
            raise ValueError
        root = ET.fromstring(text)
    except (ET.ParseError, ValueError):
        raise InvalidInput('invalid XML') from None
    scenes = {}

    def visit(node):
        if node.tag not in ('NV_CATEGORY', 'PA_STATUS', 'SCENCE'):
            raise InvalidInput('unsupported XML element')
        if (node.text or '').strip() or (node.tail or '').strip():
            raise InvalidInput('unexpected XML text')
        attrs = dict(node.attrib)
        if node.tag == 'SCENCE':
            if set(attrs) != {'NAME', 'CONTROL_MASK', 'VALUE'} or len(node):
                raise InvalidInput('invalid scene structure')
            name = attrs['NAME']
            if name in scenes:
                raise InvalidInput('duplicate scene')
            value = re.sub(r'#.*?#', '', attrs['VALUE'], flags=re.S)
            tokens = [s.strip(' {}\n\r\t') for s in value.split(',')]
            if tokens and not tokens[-1]:
                tokens.pop()
            if not tokens or len(tokens) % 5 or any(
                    not re.fullmatch(r'0[xX][0-9a-fA-F]+|[0-9]+', s)
                    for s in tokens):
                raise InvalidInput('invalid operation encoding')
            try:
                nums = [int(s, 16 if s.lower().startswith('0x') else 10) for s in tokens]
            except ValueError:
                raise InvalidInput('invalid numeric encoding') from None
            rows = [tuple(nums[i:i + 5]) for i in range(0, len(nums), 5)]
            for reg, mask, val, delay, kind in rows:
                if not (0 <= reg <= 255 and 0 <= mask <= 65535 and
                        0 <= val <= 65535 and 0 <= delay <= 65535 and
                        kind in (0, 2, 4, 5)):
                    raise InvalidInput('operation out of range')
            scenes[name] = rows
            attrs['VALUE'] = rows
            try:
                attrs['CONTROL_MASK'] = int(attrs['CONTROL_MASK'], 0)
            except ValueError:
                raise InvalidInput('invalid scene control') from None
        return [node.tag, sorted(attrs.items()), [visit(child) for child in node]]

    normalized = visit(root)
    canonical = json.dumps(normalized, ensure_ascii=True, separators=(',', ':'))
    return scenes, hashlib.sha256(canonical.encode('ascii')).hexdigest()


def load_inputs(directory):
    result = []
    for index, expected in enumerate(FINGERPRINTS):
        try:
            data = (directory / f'tfa9874_{index}_configs.xml').read_bytes()
        except OSError:
            raise InvalidInput('cannot read configuration input') from None
        scenes, digest = parse(data)
        if digest != expected:
            raise InvalidInput('configuration fingerprint mismatch')
        result.append(scenes)
    if len(result) != 4:
        raise InvalidInput('four pinned inputs required')
    return result


def build_tables(inputs):
    """Apply the project's reset, attenuation and interrupt policies.

    The shared template comes from input zero after all four inputs are checked.
    Per-chip TDM routing remains in the public core. Duplicate the unlock pair
    after reset, wait 2 ms, clamp INIT gain to step 15, select attenuated input,
    and finish power-on with interrupts disabled. All other data comes from XML.
    """
    source = inputs[0]
    init = list(source['INIT_CHIP'])
    off = list(source['POWER_OFF_L'])
    on = list(source['POWER_ON_L'])
    if (len(init), len(off), len(on)) != (42, 6, 9):
        raise InvalidInput('unsupported scene layout')
    reset = list(init[2]); reset[3] = 2
    init = init[:2] + [tuple(reset)] + init[:2] + init[3:]
    for i, op in enumerate(init):
        reg, mask, val, delay, kind = op
        if reg == 0x61:
            val = (15 << 6) | (val & 0x3f)
        if reg == 0x02:
            val = 0x22d8  # Project initialization INPLEV policy.
        init[i] = (reg, mask, val, delay, kind)
    # These are positions in the pinned layout, not copied register data.
    on = [init[5], init[39]] + on
    on[-1] = init[41]  # Preserve the project's disabled-interrupt policy.
    return init, off, on


def render(inputs):
    init, off, on = build_tables(inputs)
    def row(op, value=None):
        reg, mask, val, delay, kind = op
        v = f'0x{val:04x}' if value is None else value
        return f' {{0x{reg:02x}, 0x{mask:04x}, {v}, {delay}, {kind}}},'
    lines = [f'/* {BANNER} */',
             '/* Generated from owner inputs; no redistribution license granted. */',
             '#ifndef DBY_TFA9874_TABLES_PRIVATE_H',
             '#define DBY_TFA9874_TABLES_PRIVATE_H']
    for name, table in [('init', init), ('off', off)]:
        lines.append(f'static const struct dby_tfa9874_op dby_tfa_{name}[] = {{')
        lines.extend(row(op) for op in table)
        lines.append('};')
    lines.append(f'#define DBY_TFA_GAIN_LOW_BITS 0x{on[2][2] & 0x3f:02x}')
    lines.append('#define DBY_TFA_ON_INITIALIZER(inplev, slot, gain) { \\')
    for i, op in enumerate(on):
        lines.append(row(op, ['(inplev)', '(slot)', '(gain)'][i] if i < 3 else None) + ' \\')
    lines.extend(['}', '#endif', ''])
    return '\n'.join(lines)


class QuietParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes unknown arguments, which can contain secrets.
        self.print_usage(sys.stderr)
        self.exit(2, 'error: invalid arguments; see --help\n')


def main(argv=None):
    parser = QuietParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        content = render(load_inputs(args.input_dir))
        # Exclusive create also refuses symlinks; input files are never opened writable.
        if args.output.name != HEADER_NAME:
            raise InvalidInput('output must use the documented private header name')
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='ascii') as stream:
            stream.write(content)
    except (InvalidInput, OSError, KeyError, IndexError):
        print('error: private table generation refused; check inputs, fingerprint and output', file=sys.stderr)
        return 1
    print('Generated private tables: 44 init, 6 off, 11 on operations; do not distribute.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
