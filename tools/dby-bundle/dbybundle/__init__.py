# SPDX-License-Identifier: GPL-2.0-only
"""Standard-library bundle metadata API; no build or device operations."""

from .manifest import BundleError, create_manifest, encode_manifest, read_manifest, verify, write_manifest

__all__ = ["BundleError", "create_manifest", "encode_manifest", "read_manifest", "verify", "write_manifest"]
