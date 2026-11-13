# SPDX-License-Identifier: GPL-2.0-only
"""Small recursive parser for the SPA-JSON subset used by our template.

Supports objects, arrays, quoted/bare tokens, = or :, optional commas,
# comments, booleans and finite numbers. Not a full PipeWire validator.
"""
import json
import math
import re

TOKEN = re.compile(r'\s+|\#[^\n]*|"(?:[^"\\]|\\.)*"|[{}\[\]=:,]|[^\s{}\[\]=:,"#]+')


def parse(text):
    tokens = []
    pos = 0
    while pos < len(text):
        match = TOKEN.match(text, pos)
        if not match:
            raise ValueError('invalid SPA-JSON token')
        token = match.group()
        pos = match.end()
        if not token.isspace() and not token.startswith('#'):
            tokens.append(token)
    index = 0

    def atom(token):
        if token.startswith('"'):
            return json.loads(token)
        if token in ('true', 'false', 'null'):
            return {'true': True, 'false': False, 'null': None}[token]
        if re.fullmatch(r'[+-]?[0-9]+', token):
            return int(token)
        try:
            number = float(token)
        except ValueError:
            return token
        if not math.isfinite(number):
            raise ValueError('non-finite number')
        return number

    def value():
        nonlocal index
        if index >= len(tokens):
            raise ValueError('unexpected end')
        token = tokens[index]
        index += 1
        if token in ('{', '['):
            obj = {} if token == '{' else []
            close = '}' if token == '{' else ']'
            while index < len(tokens) and tokens[index] != close:
                if tokens[index] == ',':
                    index += 1
                    continue
                if token == '{':
                    key = tokens[index]
                    index += 1
                    if key in '{}[]=:,':
                        raise ValueError('bad key')
                    key = atom(key)
                    if not isinstance(key, str) or key in obj:
                        raise ValueError('duplicate or non-string key')
                    if index >= len(tokens) or tokens[index] not in ('=', ':'):
                        raise ValueError('missing key separator')
                    index += 1
                    obj[key] = value()
                else:
                    obj.append(value())
            if index >= len(tokens):
                raise ValueError('unclosed container')
            index += 1
            return obj
        if token in ('}', ']', '=', ':', ','):
            raise ValueError('unexpected punctuation')
        return atom(token)

    result = value()
    if index != len(tokens):
        raise ValueError('trailing content')
    return result
