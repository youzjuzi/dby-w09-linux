# Bundle installer core

Language: English. End-to-end guide: [English](../../docs/install.md) / [中文](../../docs/install.zh-CN.md). Evidence uses [H/R/O/U](../../docs/status.md). Device-package update and rollback were validated on one SD card and one tablet with bundles `20261102-2` / `20261102-3`, both kernel `7.2.10` (H + R, 2026-11-02). The 2026-11-02 owner retest covered info, p1-only verification and dry-run on one SD card (R). Journal preflight/injected abnormal paths and info --full remain O; those paths are not upgraded by the normal-card retest. `seed` derived-PSK writing/read-back and restore have limited H + R evidence; wizard/timezone have H and safe-mode on/off have H + R within the scope below. Menu and user-data backup/restore remain unimplemented (U); full `reinstall-system` has H + R within the scope below.

Python 3 standard library; install, verify, info, update and rollback. Real SD operations are
for the device owner to run with sudo after reviewing a dry-run plan.

```sh
python3 -I -B tools/dby-install/dby-install install --bundle /path/to/bundle --device /dev/mmcblk0 --dry-run
sudo python3 -I -B tools/dby-install/dby-install install --bundle /path/to/bundle --device /dev/mmcblk0
sudo python3 -I -B tools/dby-install/dby-install verify --bundle /path/to/bundle --device /dev/mmcblk0
sudo python3 -I -B tools/dby-install/dby-install info --device /dev/mmcblk0
```

The target must be a whole `/dev/mmcblkN` device with sysfs `device/type` exactly
`SD`, between 8 GiB and 2 TiB, using 512-byte sectors. RM/sysfs removable may be
zero for an MMC reader and is not an acceptance requirement. eMMC (`MMC`), boot
and rpmb nodes, partitions, USB `/dev/sdX` disks and other media remain refused;
USB reader support requires a separate target-safety review.
Partitions, mounted descendant/holder devices, read-only media, inconsistent
identities and symlinks fail closed. System disks are excluded by checking the
target, partitions and recursive holders against mountinfo, including the root
mount; there is no separate heuristic system-disk flag. The tool shows model/capacity/serial in the terminal
preview and requires the full target name; there is no `--yes` override.

Bundle and archive validation precede destructive operations. The DOS/FAT32/ext4
layout, labels, geometry and ordered three-line boot MANIFEST remain fixed.
Ownership, permissions, links and xattrs are preserved on the real SD path, with
read-back after unmounting and reopening read-only (`ro,noload` for ext4).
The card gets `manifest.json` and an empty `dby-seed/` beside `UBOOT/`.

`install --no-readback` optionally skips the p2 per-file read-back after unmounting.
The default still performs full read-back, and the standalone `verify` command
performs the full check by default; `verify --p1-only` checks the device package only. The option applies only to `install`, including
`--minimal`; `verify` and `info` reject it as a usage error. Preview/dry-run plans
show `readback.p1: full` and `readback.p2: metadata` when selected.

The writable-mount checks of p1 and the complete extracted rootfs still run,
followed by sync and unmount. The SD backend revalidates the target, rereads the
partition table and checks its layout and partition-node sizes, then checks
filesystem labels/types and ext4 block/inode sizes. It reopens both partitions
read-only (`ro,noload` for ext4), hashes every p1 boot file including MANIFEST,
checks the card manifest, and reads the exact `/etc/dby-rootfs-kind` and
`/etc/fstab` contracts. Missing or linked contract files fail. The p2 file hashes
and other per-file read-back checks are deferred to a later `verify` run.

Before confirmation and on successful completion, the installer prints:

```text
Read-back of p2 skipped (--no-readback); run "dby-install verify" later to check the card.
OK: install completed; p2 file read-back skipped.
```

For a user-operated install and full verification before the first boot:

```sh
sudo python3 -I -B tools/dby-install/dby-install install --no-readback --bundle /path/to/bundle --device /dev/mmcblk0
sudo python3 -I -B tools/dby-install/dby-install verify --bundle /path/to/bundle --device /dev/mmcblk0
```

The image-file backend accepts the same option and retains its archive metadata,
name and target restrictions. Its existing inspection path still copies sparse
partition extents and extracts files into ordinary temporary directories; skipping
p2 hashes does not remove that image inspection I/O. It uses no real block device
or mount. No successful skipped run is reported as full p2 read-back verification.

`dbyinstall/privileged.py` handles existing install/verify/info mounts and writes;
the separate `maintenance_io.py` adapter handles update/rollback with only p1
writable and p2 always read-only. A failed step stops the operation; cleanup attempts all owned
mounts. If unmounting fails, the temporary workspace is retained, never recursively
deleted while mounted. A failed card install can leave a partial card and requires
inspection/reinstallation; no claim of transactional rollback is made.

Dependencies: sfdisk, mkfs.vfat, mkfs.ext4, tar, zstd, sha256sum, lsblk, blkid,
mount, umount, blockdev, udevadm, dumpe2fs, sync. Missing dependencies are listed.

Partition rereads allow six attempts with 1, 2, 3, 4 and 5 second retry delays
(15 seconds total). Each `udevadm settle` uses `--timeout=30`; a queue timeout
prints one notice per install and allows the reread to proceed. Other settle
failures remain fatal. Formatting still requires a successful kernel partition
reread, the expected partition table and matching block-node sizes. At most seven
settle calls occur, with at most 210 seconds of queue waiting plus the retry
delays; this is not an overall deadline for the other commands.

Command failures report only the executable basename, exit status and a fixed
category under `E_INSTALL_COMMAND`. A command that cannot start has no exit status
and is reported as `not-started`; signal termination reports a negative status.
`timeout` identifies `udevadm settle` exit 1, and `reread-failed` identifies a
failed `blockdev --rereadpt`, without inferring its cause from private output.
Set `DBY_TRACE=1` to print each command basename and status to stderr, including
successful commands. Arguments, input data and captured stdout/stderr are never
included in these diagnostics. For a user-operated SD install, use:

```sh
sudo env DBY_TRACE=1 python3 -I -B tools/dby-install/dby-install install --bundle /path/to/bundle --device /dev/mmcblk0
```

## Unprivileged image test path

Explicit `--image-file` selects a separate backend and cannot select a block node.
Install requires a new ordinary filename, an existing parent and an explicit image
size. Maintenance instead requires an existing installed image, with no image-size
option. The full filename is confirmed before writing. Dry-run changes neither
the image nor the backup destination.

```sh
python3 -I -B tools/dby-install/dby-install install --bundle /path/to/synthetic-bundle --image-file /path/to/card.img --image-size 8G --dry-run
python3 -I -B -m unittest discover -s tools/dby-install/tests
```

The actual image test runs sfdisk and both mkfs programs on sparse regular files;
mtools writes FAT, mkfs.ext4 populates ext4, and debugfs sets archive owners and
reads back inodes/files. Partition extents are copied sparsely into the full disk
image. No loop device or mount is used. This backend requires mtools/debugfs and
refuses archives with xattrs or ACLs; it does not emulate privileged ACL/xattr extraction,
kernel partition rereads or a physical card. These limitations do not relax the
real target checks. Tests include an actual filesystem round trip and tamper check.

Rootfs tar paths, link topology, special file types and fstab are validated before
installation. A build-written `/etc/dby-rootfs-kind` is displayed by info when
present; missing old markers show `unknown`. Info only reports seed-file presence,
never values.

Rootfs member names use their own POSIX rules: leading `./` is removed, while
absolute paths, empty/interior dot components and NUL/control bytes (including
DEL) are rejected. Colon and literal backslash names are accepted for dpkg
multiarch metadata and systemd units. Bundle manifest paths remain stricter.
The tar root directory entry and conventional directory slash are handled
separately; duplicate members and unsafe link traversal still fail.

The real SD backend gives GNU tar the entire archive as an argv-selected input;
member names never become shell commands or tar command options. The unprivileged
image backend passes only fixed p1 names to mtools. Its debugfs tokens preserve
literal backslashes inside double quotes: colon, spaces, `@` and single quotes
round-trip. Names containing a double quote cannot be represented by this backend
and are explicitly refused with `E_IMAGE_NAME` before confirmation or image
creation; the same names remain valid for GNU tar. Newlines/control characters
are never allowed in command scripts. Image paths used as debugfs arguments have
the same restriction.

An archive with xattrs or ACLs remains valid for the real SD plan. Image creation
refuses it with `E_IMAGE_XATTR`/`E_IMAGE_ACL`, preserving the archive rather than
dropping metadata to make an offline test pass. A successful dry-run validates
layout and archive safety; it does not imply the image backend supports every
metadata feature of that archive.

## Minimal test cards

`install --minimal` uses the same complete p1 and target-safety checks, selecting
`rootfs-test.tar.zst` instead of `rootfs.tar.zst`. The test archive must be included
in the bundle manifest and contain `/etc/dby-rootfs-kind` with `test` content.
Its absence or a different marker fails before writing the target. Bundles may
contain either or both rootfs archives.

The default test p2 is 4 GiB; `--p2-size` accepts M/G/T sizes, with a 1 GiB minimum
and a maximum of the remaining target capacity. The rest stays unallocated. The
whole target still must be at least 8 GiB. DOS, p1, filesystem labels, ext4 geometry,
fstab and the ordered boot MANIFEST do not change. A normal full install continues
to use all remaining target capacity and rejects `--p2-size`.

```sh
python3 -I -B tools/dby-install/dby-install install --minimal --bundle /path/to/bundle --device /dev/mmcblk0 --dry-run
sudo python3 -I -B tools/dby-install/dby-install install --minimal --p2-size 4G --bundle /path/to/bundle --device /dev/mmcblk0
python3 -I -B tools/dby-install/dby-install install --minimal --p2-size 1G --bundle /path/to/synthetic-bundle --image-file /path/to/new-test.img --image-size 8G --dry-run
```

`verify` reads the installed kind and automatically checks the corresponding
archive and p2 layout; no `--minimal` option is needed. Full systems require p2
to reach the target end; test systems accept the bounded custom p2. Both paths
verify installed file hashes, permissions, ownership and link topology. The 2026-11-02 records cover owner-operated test/full SD installation and boot (H), with the full desktop installed using --no-readback. A later full reinstall and full verify passed within the scope recorded below. Image tests remain O and cannot establish those device results; see [status](../../docs/status.md).

## Update the device package and roll back

`update` replaces only the five `UBOOT/` files and the card's `manifest.json`.
It never installs the new bundle's rootfs, writes p2 or changes `dby-seed/`.
Use Ubuntu's package manager for installed system software. Other p1 directories
such as Android/LOST.DIR are not traversed or edited by the file transaction.
H + R, 2026-11-02: on one SD card / one tablet, bundles `20261102-2` and
`20261102-3` (kernel `7.2.10`) passed same-version no-op, explicit downgrade,
upgrade and two backup-based rollbacks. Boots after updates worked, including
restored touch after upgrading. The owner checked MANIFEST/device-image hashes,
no old/new leftovers and preservation of seed, other p1 directories and p2.
This scope does not validate real-card interruption/power-loss recovery (only O
simulation), cross-kernel updates (the format accepts only `7.2.10`) or p2 updates
(update never applies them). Menu and
user-data backup/restore commands remain unimplemented.

```sh
python3 -I -B tools/dby-install/dby-install update --device /dev/mmcblk0 \
  --bundle /path/to/new-bundle --backup-dir /path/to/private-host-backups --dry-run
sudo python3 -I -B tools/dby-install/dby-install update --device /dev/mmcblk0 \
  --bundle /path/to/new-bundle --backup-dir /path/to/private-host-backups
python3 -I -B tools/dby-install/dby-install rollback --device /dev/mmcblk0 \
  --backup /path/to/private-host-backups/p1-TIMESTAMP-BUNDLE_ID --dry-run
sudo python3 -I -B tools/dby-install/dby-install rollback --device /dev/mmcblk0 \
  --backup /path/to/private-host-backups/p1-TIMESTAMP-BUNDLE_ID
```

Replace paths and device names. `--backup-dir` is mandatory for update, has no
default, and must be on the host, outside the target/mounts. The parent must exist;
the selected backup root may be new. Each backup gets a new mode-0700
`p1-<UTC timestamp>-<old bundle_id>/` directory; an existing name is never reused.
Writes require root for SD and the complete device/image name typed at the
prompt. There is no `--yes` or `--force`. Both commands reject `--minimal`,
`--p2-size`, `--no-readback` and `--image-size`.

Dry-run requires no root check and prints JSON: action/target, old/new bundle IDs,
kernel release, per-file same/replace status and hash prefixes, planned backup,
version decision, compatibility notices and p2/seed preservation. Rollback sets
`old_bundle_id: null` because it deliberately does not trust or require the
current card manifest; null does not mean the card was interrupted. A device preview
still needs permission to read the raw SD and uses read-only mtools/debugfs without
mounting; an unreadable card cannot be inspected. Real execution checks the same
whole-SD identity, table, labels and geometry as verify, mounts p2 `ro,noload`,
and rechecks the target after confirmation. Before removing the card from the
tablet, shut it down cleanly. The journal preflight below runs before any p2 mount,
including update/rollback dry-run inspection.

Update requires a valid card manifest and exactly five boot files matching that
manifest. Bundle IDs compare as `(YYYYMMDD, numeric sequence)`. The same ID with
identical boot hashes returns `already up to date`, without confirmation, backup
or target writes; the same ID with different content is rejected. Older bundles
require explicit `--allow-older`; this does not permit same-ID content changes.
Changes in rootfs recipe/package hashes only produce a notice: p2 remains as it
was. The existing bundle schema still fixes the supported kernel release.

The backup contains `UBOOT/{Image,dby-w09.dtb,initrd.img,dby-device.img,MANIFEST}`,
the exact old `manifest.json`, and schema-1 `backup.json`. The latter records UTC
creation time, old/new IDs, each file's size/SHA-256, kernel release and target
capacity; it contains no host path, model, serial or seed contents. Firmware may
be inside the backed-up device image, so keep the backup private. The files are
synced and re-read/hash-checked before changing the card.

The transaction copies all five files to `UBOOT.new`, syncs and hashes them,
renames `UBOOT` to `UBOOT.old` and the staged directory to `UBOOT`, then writes
and renames a temporary manifest. After checking the active files, it removes
`UBOOT.old`, unmounts and reopens read-only for full p1 hash verification. Seed
names/types/content are hashed before/after. SD checks retain the partition table,
filesystem identity, rootfs kind and fstab; image tests additionally hash the
entire p2 region. The image backend uses mtools on an isolated p1 filesystem copy
and publishes only p1 extents. It retains an original p1 image for exact-byte
automatic recovery; the SD backend uses kernel mounts and file-level recovery.

**FAT directory renaming is not an atomic transaction across files or power loss.**
After a switch failure the tool attempts recovery from the verified host backup,
always returns nonzero, preserves the original error, and prints the backup path
and manual rollback command even if automatic recovery fails. Power loss/process
termination can leave a partial state. Do not assume automatic recovery ran.

Update refuses `UBOOT.new`, `UBOOT.old` or temporary-manifest leftovers without
deleting them. If UBOOT is missing but UBOOT.old exists, restore its original name
or run `rollback` with the verified host backup. Rollback requires valid table,
labels/rootfs contracts and UBOOT or UBOOT.old, but does not trust the current card
manifest. It validates the backup's exact inventory, hashes and three-line boot
MANIFEST, stages the backup, clears reserved leftovers and verifies after reopening.
It leaves p2/seed unchanged and does not make a second backup.

| Code | Meaning / action |
|---|---|
| `E_INSTALL_P2_RECOVERY` | Unreplayed p2 journal; cleanly shut down or repair unmounted p2 as described below |
| `E_UPDATE_UNMANAGED` | Missing card manifest; use install or repair manually |
| `E_UPDATE_CARD_INCONSISTENT` | Card differs from its manifest, or rollback has neither boot directory |
| `E_UPDATE_LEFTOVER` | Interrupted staging; inspect it or use verified rollback |
| `E_UPDATE_VERSION` | Invalid bundle ID/date |
| `E_UPDATE_OLDER` | Older bundle needs explicit allow-older |
| `E_UPDATE_SAME_ID_DIFFERENT` | Same ID has different boot files; cannot override |
| `E_UPDATE_BACKUP` | Unsafe/unwritable/colliding backup path or backup re-read failure; card unchanged |
| `E_UPDATE_VERIFY` | Switched transaction failed; inspect original error and recovery result |
| `E_ROLLBACK_BACKUP_INVALID` | Backup inventory, types, metadata or hashes invalid; no recovery writes |

Existing target/command errors and `DBY_TRACE=1` basename/status diagnostics remain
in use. Raw external-tool output and seed values are not printed. Explicit host
backup paths are printed so the owner can recover. Full `verify` still compares
the selected rootfs archive too; after p1-only update to a bundle with different
rootfs contents, that full check may report p2 differences, since update does not
apply them.


## Used-card verification and info

Full `verify` is intended for a freshly installed card **before its first boot**.
Booting and using Ubuntu changes rootfs files such as keys, machine-id and user
state. After use, choose `verify --p1-only`; a p2 digest mismatch in full verify
now explains this choice without printing a filename. The new mode checks the
partition table and filesystem labels/types, the exact five UBOOT files, ordered
three-line MANIFEST and the card manifest against the selected verified bundle.
It never mounts p2, checks its journal, reads its rootfs files or compares p2 with the
rootfs archive. The supplied bundle itself is still verified. Reading the p2 label does not establish rootfs health. Success ends with
`OK: p1 matches the bundle; p2 not checked.` The flag applies only to verify.

```sh
sudo python3 -I -B tools/dby-install/dby-install verify --p1-only \
  --bundle /path/to/installed-bundle --device /dev/mmcblk0
sudo python3 -I -B tools/dby-install/dby-install info --device /dev/mmcblk0
sudo python3 -I -B tools/dby-install/dby-install info --full --device /dev/mmcblk0
```

Default info is a concise JSON summary with stable fields: `bundle_id`,
`kernel_release`, full `repositories` commit IDs, `rootfs_kind`, `rootfs`
(package_count and 16-character recipe/packages hash prefixes), `files` (the five
names with size and 16-character SHA-256 prefix), seed presence booleans and
`sources.upstream` names/versions. It excludes the full manifest, source input
records and private-input records. Missing manifest metadata is represented by
null values or empty inventories. Rootfs summary fields come from the manifest,
not a fresh inventory of the running system. `info --full` retains the original complete
output including `manifest`. Neither mode includes seed values in its output. `--full`
applies only to info.

## Unreplayed p2 journal

**Shut the tablet down normally before removing its SD card.** An observed
unclean removal left ext4 `needs_recovery`; a `ro,noload` view then lacked current
rootfs contract files. The earlier generic kind error obscured the cause. The
owner ran `e2fsck -p` on unmounted p2 and the update then worked (H + R,
2026-11-02); automated detection and the revised message have O tests.

Update, rollback, default verify and info now refuse this state before mounting
or inspecting rootfs files, with `E_INSTALL_P2_RECOVERY`. SD uses `dumpe2fs -h`;
ordinary image partitions use `debugfs -R features` without `-w`. Both inspect
ext4 features read-only, with the existing e2fsprogs dependencies. The tool does
not repair p2, replay its journal or invoke e2fsck. Install is unaffected. The
p1-only mode skips this check because it never opens p2's rootfs; it cannot say
whether p2 is clean. Existing verify/info dry-run only prints a plan; maintenance
dry-run actually inspects the card and refuses a dirty journal before preview.

Boot the tablet and shut it down cleanly, or, after checking the actual p2 target
and ensuring it is unmounted, run the suggested repair yourself, then retry:

```sh
sudo e2fsck -p /dev/mmcblk0p2
```

The error substitutes the actual SD p2 name. For image files it prints no host
path: extract p2 to an ordinary file before any owner-operated image repair;
do not run a partition filesystem repair on the whole disk-image container.
Rootfs contract errors retain `E_INSTALL_KIND` / `E_INSTALL_FSTAB` and name only
`etc/dby-rootfs-kind` / `etc/fstab` plus missing, wrong type or content mismatch;
they never print the file contents.

## Seed and safe-mode commands

`seed show`, `seed set`, `seed wizard`, `seed restore` and `safe-mode
status|on|off` edit an already installed card. These commands have offline image/fake-device tests (O), plus the limited device evidence below. Select
exactly one whole `--device /dev/mmcblk0` or existing `--image-file card.img`.
The existing SD media, capacity, system-disk/mounted-descendant and identity
gates apply. Device writes require root. They validate the partition table,
labels, installed manifest and all five UBOOT files, then access only p1.
**They never mount p2**, inspect its files or replay its journal; a dirty p2
journal does not block seed editing. This does not repair or validate p2.

```sh
sudo tools/dby-install/dby-install seed show --device /dev/mmcblk0
sudo tools/dby-install/dby-install seed set --device /dev/mmcblk0 --backup-dir ./seed-backups --wifi example --ssid example-wifi --psk-prompt --dry-run
sudo tools/dby-install/dby-install seed set --device /dev/mmcblk0 --backup-dir ./seed-backups --wifi example --ssid example-wifi --psk-prompt
sudo tools/dby-install/dby-install seed set --device /dev/mmcblk0 --backup-dir ./seed-backups --authorized-keys /path/to/own-ssh-public-key.pub
sudo tools/dby-install/dby-install seed wizard --device /dev/mmcblk0 --backup-dir ./seed-backups
sudo tools/dby-install/dby-install seed restore --device /dev/mmcblk0 --backup ./seed-backups/SELECTED_BACKUP
sudo tools/dby-install/dby-install safe-mode status --device /dev/mmcblk0
sudo tools/dby-install/dby-install safe-mode on --device /dev/mmcblk0 --backup-dir ./seed-backups
sudo tools/dby-install/dby-install safe-mode off --device /dev/mmcblk0 --backup-dir ./seed-backups
```

Network names above are fictional. Passwords are accepted only by the no-echo
`--psk-prompt` or a one-line `--psk-file FILE` inaccessible to group/other;
there is no password argument. Interactive input asks twice, both without echo;
`E_SEED_PSK_MISMATCH` refuses mismatching entries without writing. Two empty
entries are invalid in `seed set`; in the wizard they retain the existing value.
File input needs no second entry. The default stores the original 8–63-byte
plaintext passphrase. Only explicit `seed set --derived-psk` uses
PBKDF2-HMAC-SHA1, 4096 iterations, 32 bytes. A 64-hex input is already a raw PSK
and is preserved unchanged. Derived/raw PSKs are **WPA2-PSK only, cannot be used
with WPA3 (SAE) or WPA2/WPA3 mixed networks**, and may prevent connection; use
only when the network is confirmed WPA2-only. A fixed warning appears in diff
`notices` and stderr when deriving or accepting a raw PSK, with no extra
confirmation key. `seed show` adds a WPA2-only note to these entries.

Only derived/raw PSKs are bound to the SSID: changing their SSID requires a new
password source. Plaintext entries can change SSID without re-entry. The wizard
has no derivation option and defaults to plaintext. Credentials remain sensitive
in either form and are never printed by show/diff, errors or command tracing.

`seed set` changes only supplied settings; at least one change is required.
Options:

| Setting | Set | Remove |
|---|---|---|
| Wi-Fi | `--wifi LABEL` (default `main`), `--ssid S`, password source, `--country CC` | `--remove-wifi LABEL` (separate invocation) |
| APT mirror | `--apt-mirror URL` | `--no-apt-mirror` |
| Panel rate | `--refresh-hz 60` or `120` | `--no-refresh-hz` |
| Timezone | `--timezone Z` | `--no-timezone` |
| Packages | `--packages "tree curl"` | `--no-packages` |
| Root public keys | `--authorized-keys FILE` replaces; `--add-authorized-keys FILE` merges/deduplicates | No clear-keys option |

SSID, credentials, mirror URL, rate, timezone syntax, package names and public-key
wire structures are validated against the boot rules. Invalid public-key lines
report only their line number; comments are stripped. Conflicting regulatory
codes or duplicate network labels are rejected. Keys cannot be empty. Package
names are sorted/deduplicated. Country codes are uppercased. Timezones cannot be
checked against the card's tzdata because p2 is never mounted. Scanner whitespace
cannot be preserved in credentials; surrounding whitespace is refused rather
than silently stripped.

The writer normalizes `seed.conf`: UTF-8, LF, at most 64 KiB, version 1, sorted
Wi-Fi labels, then apt/display/system/packages. **Comments, junk and unknown
sections/keys are discarded**; preview lists discarded names without values.
Repeated assignments keep the last value, matching boot parsing. Original bytes
are preserved in the backup. `seed wizard` requires a terminal, shows current
values with credential masks, and uses Enter to keep / `-` to remove; a public-key
file replaces keys, and keys cannot be cleared. It edits one selected network
per invocation; repeat for other labels. `safe-mode off` writes `safe_mode=false`.
Enabling safe mode warns that the screen will stay black, touch/display modules
are blocked, and SSH/Wi-Fi remain available; it prints the equivalent off command.

Every change displays a masked before/after diff and requires the **full target
name** typed back. There is no `--yes` or `--force`. `--dry-run` reads and validates
but does not confirm, create a backup or write the card. Identical final bytes
print `no change` and perform no write/backup. `seed show` prints settings and only
public-key types/SHA256 fingerprints, never key comments or credential values.

**Backups contain Wi-Fi credentials; keep them private.** Editing requires an
explicit `--backup-dir`. A new `seed-<UTC timestamp>-<bundle id>/` directory has
mode 0700 and files mode 0600. It stores original `seed.conf`/`authorized_keys`
bytes and schema-1 `backup.json` (time, bundle ID, existence, sizes and hashes;
no credentials, card identities or host paths). Missing files are recorded as
absent. Backup files are fsynced and re-read before card writes. `seed restore`
rejects symlinks, extra/missing entries and invalid metadata/hashes, then restores
raw bytes, deleting files recorded absent. It first backs up displaced contents
beside the selected backup, so that directory must be writable; the old raw seed
may contain legacy comments/invalid entries and is restored verbatim.

Writes stage `.new` files, sync/read back, rename, sync, then reopen p1 read-only
and verify exact seed bytes/parser results plus unchanged UBOOT/manifest hashes.
Other p1 directories are not traversed. Existing `dby-seed/*.new` causes refusal;
leftovers are never silently deleted. A failure after switching triggers recovery
from the verified backup and still returns nonzero, with the original error code,
backup location and manual restore command. An ordinary image uses a private p1
copy and restores exact original p1 bytes after a failed transaction. This is
**not a power-loss guarantee** on FAT; a damaged card or failed recovery may need
manual repair before its manifest can pass the managed-card gate.

| Code | Meaning |
|---|---|
| `E_SEED_UNMANAGED` | Missing installed-card manifest |
| `E_SEED_CARD_INCONSISTENT` | Invalid manifest or mismatching boot files |
| `E_SEED_INVALID` | Invalid setting, key line, input or option combination |
| `E_SEED_LEFTOVER` | Seed `.new` staging already exists |
| `E_SEED_BACKUP` | Missing/invalid backup destination or failed backup verification |
| `E_SEED_VERIFY` | Switching/read-back failed; see recovery status |
| `E_SEED_PSK_MISMATCH` | The two no-echo entries differ; nothing written |
| `E_SEED_PSK_SOURCE` | Missing, empty, non-private or invalid password source |
| `E_SEED_RESTORE_INVALID` | Invalid restore inventory/schema/hashes |
| `E_SEED_NO_TTY` | Interactive/no-echo input unavailable |

Existing `E_TARGET_*`, filesystem/partition and dependency errors still apply.
Menu and user-data backup/restore remain unimplemented (U).

On 2026-11-02, `seed set` wrote a derived PSK and passed its own read-back check, and `seed restore` restored the original seed on one SD card / one tablet (H + R). On the one tested router, the correctly derived PSK could not connect; restoring the original plaintext passphrase connected immediately. WPA3/SAE or mixed-mode negotiation is a suspected cause, not verified on the router. On 2026-11-02/03, one SD card and one tablet with bundle `20261102-3` and kernel `7.2.10` provided these additional results: `seed wizard` rejected mismatched passphrases in dry-run without writing, and a real timezone-only edit showed only that change, passed read-back and created a backup (H). `seed set --timezone` was written and confirmed after boot with `timedatectl` for two timezones (H). A mistyped target confirmation was refused without writing (H). `safe-mode on/off` blocked then restored msm, panel and touch modules across boots; while active the backlight was lit without an image and USB networking/SSH worked (H + R). These results do not establish other devices or networks. Other `seed set` settings (mirror/rate/packages/replacement or added public keys) still need real-card retesting; derived-PSK connectivity on confirmed WPA2-only networks remains U (command logic O). The new wizard label-selection rules are tested offline (O), awaiting real-card retest.

The wizard lists existing Wi-Fi labels one per line. One label becomes the default; multiple labels have no default and empty input asks again; no networks defaults to `main`. A new label always requires an exact second entry, including the empty-card default; mismatch returns to label selection. Other prompts retain Enter-to-keep, `-` removal, double passphrase confirmation and non-clearable public keys.

## Reinstall-system (p2 only, H + R)

`reinstall-system (--device D | --image-file F) --bundle DIR [--minimal]
[--no-readback] [--dry-run]` replaces the root filesystem in **existing p2**.
Full-system reinstall has H + R evidence on 2026-11-02/03, limited to one SD card, one tablet, bundle `20261102-3` and kernel `7.2.10`: preview/execution succeeded, independent whole-p1 SHA256 comparison matched, and subsequent full `verify` passed. The new system booted with old users/data gone and regenerated SSH host keys; the preserved plaintext Wi-Fi seed connected automatically and touch worked. Shared install formatting/extraction code was exercised on the card. First setup did not hang in this run; the intermittent defect remains unresolved. `--minimal`, `--no-readback` and mid-operation failures remain O only; real-card interruption/power loss and cross-kernel updates remain U. Menu and user-data backup/restore remain unimplemented (U).
It preserves p1 (device package, manifest, seeds and unrelated files) and the
partition table. It never writes p1, changes partition sizes or resizes ext4.

**All user data on p2 is destroyed, including every home directory, installed
software, system configuration and SSH host keys. No user-data backup is made.**
Back up needed data yourself first; do not use a card containing its only copy.

```sh
sudo tools/dby-install/dby-install reinstall-system --device /dev/mmcblk0 --bundle /path/to/bundle --dry-run
sudo tools/dby-install/dby-install reinstall-system --device /dev/mmcblk0 --bundle /path/to/bundle
```

The required confirmation is the **complete target name plus ` p2`**, e.g.
`/dev/mmcblk0 p2`; for an image, its complete filename plus ` p2`. There is no
`--yes` or `--force`. Dry-run prints the same JSON preview without confirming or
writing: action, target/kind, rootfs kind/archive, existing p2 start/sectors/size,
bundle ID, destruction/backup warnings, preservation promise and read-back policy.
SD preview follows install's model/serial display convention; keep it private.

Preflight verifies the bundle/archive, exact partition layout and labels, ext4
geometry, installed manifest and five boot files. The card bundle ID and all five
file hashes must match the supplied bundle. **Run `update` first if they differ**;
reinstall never updates p1 for you. Unfinished `UBOOT.new`, `UBOOT.old`,
`manifest.json.new` or `dby-seed/*.new` staging causes refusal, not cleanup.
The archive's unpacked total × 1.2 + 256 MiB must fit actual p2. Full is the default;
`--minimal` selects the test archive. No `--p2-size` or `--image-size` is supported.
Use `install` to repartition when a larger system does not fit.

The existing whole-SD, capacity, unmounted/system-descendant and root-write gates
apply. A dirty p2 journal is irrelevant to reformatting and is not checked, but
p2 must be unmounted. After confirmation, identity, source and card contents are
checked again under the target lock. SD p1 is mounted only read-only for preflight;
p2 alone is formatted, mounted rw, extracted, synced and unmounted. Tar and ext4
creation reuse install helpers, including backend-specific metadata handling.

The default reopens p2 read-only for file/inode checks. `--no-readback` skips only
this post-unmount per-file pass, retains contracts and geometry and prints a
`verify` reminder, as install does. Extraction-time verification remains enabled.
Before and after the operation, the complete p1 region and partition table are
hashed; this comparison also runs on execution failure. Kind/fstab contracts are
required. There is no journal replay or write access to p1.

A format/extract/read-back failure returns `E_REINSTALL_FAILED`: **p2 is incomplete**,
fix the cause and rerun `reinstall-system` (or `install`). It never claims rollback.
The error confirms preservation only when p1/table hashes match; otherwise it
reports that preservation cannot be confirmed. Mount cleanup is attempted even
on failure; if unmount itself fails, the existing workspace policy retains the
mounted workspace for manual inspection rather than deleting mounted content.

The ordinary-file backend uses private ext4 images, never loop devices/mounts.
It publishes an empty p2 filesystem before extraction, then builds/publishes the
populated image, zeroing old extents as well as copying new data. Thus extraction
failure also removes old image data; it is not a transactional backup. Image
xattrs/ACL limitations remain those of install. A sufficiently small full archive
can fit a short test-card p2; reinstallation retains that size. Existing standalone
full `verify` expects full-kind p2 to extend to the card end and may reject that
layout; reinstall's own read-back checks the retained size. Use normal full-size
install when that layout/verification contract is needed.

Success identifies rootfs kind and bundle ID. First boot runs setup again and
regenerates SSH host keys; verify the device before updating client `known_hosts`.

| Code | Meaning |
|---|---|
| `E_REINSTALL_UNMANAGED` | Installed manifest absent |
| `E_REINSTALL_CARD_INCONSISTENT` | Invalid manifest or nonmatching boot files |
| `E_REINSTALL_BUNDLE_MISMATCH` | Run update to this bundle before reinstalling |
| `E_REINSTALL_P2_TOO_SMALL` | Archive plus margin exceeds existing p2; use install to repartition |
| `E_REINSTALL_FAILED` | Execution failed; p2 incomplete; inspect preservation status |

Existing target/install/bundle and staging errors retain their stable codes.
Menu and user-data backup/restore remain unimplemented (U).
