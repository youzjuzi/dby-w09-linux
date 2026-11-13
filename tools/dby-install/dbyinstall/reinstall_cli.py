# SPDX-License-Identifier: GPL-2.0-only
"""Narrow CLI for p2-only destructive reinstallation."""
import argparse
from pathlib import Path
import sys
from .common import BundleError
from .targets import ImageFileTarget, BlockDeviceTarget
from .reinstall import operate


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--device', type=Path); target.add_argument('--image-file', type=Path)
    parser.add_argument('--bundle', required=True, type=Path)
    parser.add_argument('--minimal', action='store_true')
    parser.add_argument('--no-readback', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    try:
        target = ImageFileTarget(args.image_file) if args.image_file else BlockDeviceTarget(args.device)
        operate(target, args.bundle, minimal=args.minimal, no_readback=args.no_readback, dry_run=args.dry_run)
        return 0
    except BundleError as error:
        print(error, file=sys.stderr); return 1
    except (OSError, ValueError, KeyError, TypeError, EOFError, KeyboardInterrupt):
        print('E_INSTALL_IO: Reinstall preflight failed; raw details withheld.', file=sys.stderr); return 1
