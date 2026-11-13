# SPDX-License-Identifier: GPL-2.0-only
"""Pure rules mirrored from dby-seed-apply; differential tests guard drift."""
import base64
import re
import struct
from urllib.parse import unquote, urlsplit

LIMIT = 64 * 1024
SPACE = " \t\r\n\v\f"


def parse(data):
    """Same scanner semantics as initramfs: skip junk, last assignment wins."""
    if len(data) > LIMIT or any(b < 32 and b not in (9, 10, 13) or b == 127 for b in data):
        return {}, ["Invalid bounded seed input ignored."]
    sections, section, warnings = {"": {}}, "", []
    for raw in data.decode("utf-8", "surrogateescape").split("\n"):
        line = raw.strip(SPACE)
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("["):
            if len(line) < 3 or not line.endswith("]"):
                warnings.append("Unrecognized seed line ignored."); continue
            name = line[1:-1].strip(SPACE)
            if not name or "[" in name or "]" in name:
                warnings.append("Unrecognized seed line ignored."); continue
            section = name.lower() if not name.lower().startswith('wifi "') else name
            sections.setdefault(section, {})
            continue
        if "=" not in line:
            warnings.append("Unrecognized seed line ignored."); continue
        key, value = (part.strip(SPACE) for part in line.split("=", 1))
        if not key:
            warnings.append("Unrecognized seed line ignored."); continue
        sections.setdefault(section, {})[key.lower()] = value
    return sections, warnings


def text_value(value, maximum):
    try:
        return value and len(value.encode("utf-8")) <= maximum and all(ord(c) >= 32 and ord(c) != 127 for c in value)
    except UnicodeError:
        return False


def mirror_url(value):
    try:
        parsed = urlsplit(value)
        port = parsed.port
        decoded = unquote(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            return None
        if parsed.query or parsed.fragment or not text_value(decoded, 2048) or any(c.isspace() for c in decoded) or "\\" in decoded:
            return None
        if any(part in (".", "..") for part in unquote(parsed.path).split("/")) or port is not None and not 1 <= port <= 65535:
            return None
        return value.rstrip("/")
    except ValueError:
        return None


def public_key(line):
    fields = line.split()
    if len(fields) < 2:
        return None
    kind, encoded = fields[:2]
    try:
        raw = base64.b64decode(encoded, validate=True)
        position = 0
        def field():
            nonlocal position
            if position + 4 > len(raw):
                raise ValueError("short key")
            length = struct.unpack_from(">I", raw, position)[0]; position += 4
            if length > len(raw) - position:
                raise ValueError("short key")
            data = raw[position:position + length]; position += length
            return data
        if field() != kind.encode():
            return None
        if kind == "ssh-ed25519":
            valid = len(field()) == 32
        elif kind == "ssh-rsa":
            valid = int.from_bytes(field(), "big") > 1 and len(field()) >= 128
        elif re.fullmatch(r"ecdsa-sha2-nistp(?:256|384|521)", kind):
            curve = kind.removeprefix("ecdsa-sha2-")
            valid = field() == curve.encode() and len(field()) == {"nistp256": 65, "nistp384": 97, "nistp521": 133}[curve]
        elif kind == "sk-ssh-ed25519@openssh.com":
            valid = len(field()) == 32 and bool(field())
        else:
            return None
        return kind + " " + encoded + "\n" if valid and position == len(raw) else None
    except (ValueError, struct.error, UnicodeError):
        return None
