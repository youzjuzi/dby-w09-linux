# SPDX-License-Identifier: GPL-2.0-only
"""Read-only journal preflight; no replay or repair operations."""

from .common import partition, require, run


def check_journal(filesystem, *, image=False, device=None, execute=run):
    # debugfs without -w and dumpe2fs -h only inspect the superblock. Feature
    # names are ext4 tokens, independent of localized field headings.
    command = ["debugfs", "-R", "features", filesystem] if image else ["dumpe2fs", "-h", filesystem]
    features = execute(command).split()
    require(features, "E_INSTALL_GEOMETRY", "Cannot inspect p2 ext4 features.")
    repair = "e2fsck -p" + (" " + partition(device, 2) if device is not None else "")
    require(b"needs_recovery" not in features, "E_INSTALL_P2_RECOVERY",
            "p2 has an unreplayed ext4 journal (the card was not shut down cleanly). "
            "Boot the tablet and shut it down cleanly, or run `" + repair +
            "` on the unmounted p2 partition, then retry.")
