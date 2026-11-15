# Bundle and device-package tools

Language: English. User workflow: [English install](../../docs/install.md) / [中文安装](../../docs/install.zh-CN.md). Current evidence: [H/R/O/U status](../../docs/status.md). This page covers metadata and p1; [rootfs execution](rootfs/README.md) separately requires root.

Schema 1 uses Python 3 and its standard library. The metadata commands do not
build images. The device-package builder uses host tools; no command accesses
a device, mounts a filesystem, or requires root.

```sh
python3 -I -B tools/dby-bundle/dby-bundle verify /path/to/bundle
python3 -I -B tools/dby-bundle/dby-bundle show /path/to/bundle
python3 -I -B -m unittest discover -s tools/dby-bundle/tests
```

Both commands verify every payload before reporting success. Errors have stable
`E_BUNDLE_*` codes on stderr and exit status 1. `show` prints canonical JSON.
Archive contents and hardware compatibility are outside these checks.

The Python API exports `create_manifest(directory, sequence=..., sources=...,
created=..., rootfs=...)`, `encode_manifest(document)`,
`write_manifest(directory, document)`, `read_manifest(directory)` and
`verify(directory)`. Add the tool directory to the caller's import path explicitly
when using isolated Python. `read_manifest` checks metadata only; use `verify`
before trusting payloads. `create_manifest` does not write anything;
`write_manifest` checks payloads again and refuses an existing manifest.

Pass `created` as UTC `YYYY-MM-DDTHH:MM:SSZ` for reproducibility. The default is
the current UTC second. A positive caller-supplied `sequence` produces
`YYYYMMDD-N`. JSON uses sorted keys, two-space indentation and a final newline.

The exact boot payload is `p1/UBOOT/{Image,dby-w09.dtb,initrd.img,dby-device.img,MANIFEST}`.
`MANIFEST` has exactly three SHA-256 lines, in Image, DTB, initrd order, with two
spaces before each basename. `rootfs.tar.zst` and `rootfs-test.tar.zst` are the only
additional payloads. A device-package build can have just p1, with empty rootfs
metadata. This is an intermediate bundle; installers must require their selected
rootfs archive. Each existing archive must appear in `files`.

Source metadata has these optional fields:

```json
{
  "repositories": {"linux": "0000000000000000000000000000000000000000"},
  "upstream": [{"name": "linux", "version": "7.2.10", "sha256": "<64 lowercase hex characters>"}],
  "inputs": {"Image": "<64 lowercase hex characters>"},
  "private_inputs": [{"sha256": "<64 lowercase hex characters>"}]
}
```

Repository roles are `linux`, `kernel`, `uboot`; commits must be full Git hashes.
Public input roles and upstream names/versions are bounded path-free tokens.
The generator discards all private record fields except `sha256`, including
caller-supplied paths or contents. The reader rejects private records containing
extra fields. Unknown provenance fields are rejected. Never place private data
in public metadata fields.

Rootfs metadata is `{}` until available, then contains `recipe_sha256`,
`packages_sha256`, and nonnegative integer `package_count`; a nonempty summary
requires `rootfs.tar.zst`. The kernel release is fixed at `7.2.10`.

Verification rejects missing or extra files, extra directories, symbolic links
(including bundle ancestors), special files, noncanonical paths, unsupported
schemas and malformed metadata. Hashing reads bounded chunks. Keep the directory
unchanged during generation and verification; these operations do not lock out
concurrent writers. A manifest establishes internal consistency, without signing
or authenticating its publisher.

## Device-package build

```sh
python3 -I -B tools/dby-bundle/build-bundle --config /path/to/bundle.json
python3 -I -B tools/dby-bundle/dby-bundle verify /path/to/output
```

Start with [examples/bundle.example.json](examples/bundle.example.json). All input
paths and the output are explicit; relative paths are resolved beside the config.
The example paths and zero commits are placeholders. Fill repository commits with
the actual full hashes and take upstream versions/digests from your source lock.
The builder records these caller-declared provenance values and actual input
digests; it does not authenticate the supplied repository declarations.

Required configuration fields are `kernel_build_dir`, `kernel_source_dir`, `dtb`,
`external_modules` (possibly empty), `private_inputs` (possibly empty when touch
is disabled), `toolchain` (cross compiler prefix), `touch: {"enabled": boolean}`,
`output`, and `sources`. `sources` provides exactly `repositories` (linux, kernel,
uboot) and `upstream` as described above; actual public/private input digests are
added by the builder. `modules_list` defaults to the tool's bundled list, including
the PCI power-sequencing module. `epoch`, `created`, and `sequence` are optional.

`epoch` defaults to `SOURCE_DATE_EPOCH`, or zero. `created` defaults to that epoch
in UTC, rather than the wall clock; set it explicitly for a meaningful bundle
date. Reuse the same epoch, creation time, sequence, toolchain and input bytes to
reproduce the bundle. Output must not exist and its parent must already exist.
The output cannot overlap an input. Linux `renameat2(RENAME_NOREPLACE)` publishes
the fully verified result without replacing a racing directory. Output is mode
0700 because it can contain private firmware and configuration.

Dependencies: make, kmod (`modinfo`, `depmod`), squashfs-tools, and the selected
cross GCC/binutils with static C runtime archives. Tests additionally need native
GCC and `unsquashfs`. Kernel source/build trees must be a matching prebuilt pair;
the builder invokes only `modules_install`, never an Image/DTB/module build.
It checks built-in squashfs/zlib, loop and FAT support, rejects automatic signing
or compression, checks every supplied/built module's exact vermagic, and compares
selected installed modules to their original bytes. Tree modules are then filtered
by the list, external overrides applied, and depmod run with dependency/alias checks.

Private input directories must be mode 0700. Their `inputs.json` uses old schema 1:
`{"schema":1,"files":{"firmware/example.bin":{"sha256":"...","mode":420}}}`.
The optional old `contents_sha256` field is accepted. Each listed file's digest,
mode and inventory are checked, with no links, extra files or extra directories.
Payloads may be under `firmware/` or `rootfs/etc/`; the latter become runtime
configuration via the initramfs. Collisions and the reserved safe-mode policy
filename are refused. Locks themselves are not installed. No historical input
registry or particular firmware digest is required. Verify the origin and trust
of a lock before using it.

Enabled touch requires its selected module, expected parameter exports and aliases,
and exactly one locked touch firmware input. Disabled touch omits the module,
firmware and modprobe options, using the supplied DTB unchanged. Explicit inputs
are still validated, including a touch firmware directory listed while disabled.
Legacy touchscreen modules and other Novatek firmware destinations are refused.

The touch parameter contract is exactly ten exports: `managed:bool`,
`settle_ms:uint`, `boot_wait_ms:uint`, `skip_probe:bool`, `edge_px:uint`,
`filter:uint`, `lock_px:uint`, `confirm:uint`, `multi:uint`, and `guard:uint`.
The bundled options pin `guard=0` because the later palm-transient guard has not
passed acceptance. Its disabled reporting path is intended to retain the earlier
tested module's behavior; equivalence on hardware has not been verified. The
driver source still defaults to `guard=1`, so the bundle must supply the override.
Older nine-parameter modules and modules with unknown extra parameters are
rejected during construction. A module lacking `guard` cannot accept the bundled
`guard=0` option.

The 2026-11-02 device check with `guard=0` recorded touch loading/events (H) and
single-touch, edge/corner and pinch interaction (R); this short check does not
validate three-finger gestures, long-term stability or the guard feature.

The device image contains the selected modules, depmod metadata, locked private
payloads, public runtime overlay and `DEVICE-CONTENTS.txt`. New source copies in
`device-rootfs/` retain USB NCM/network setup, RTC offset services, display watchdog
and managed touch options. It also includes three board-specific tuned profiles
(CPU `schedutil` plus CPU/GPU frequency bounds), the tuned-ppd mapping and systemd
drop-ins exposing `/run/tuned` configuration at `/etc/tuned` inside each service.
TuneD waits up to 20 s for CPU/GPU frequency paths before starting, then proceeds
even if some remain absent (for example, the GPU in safe mode).
Only configuration is bound: the active/base profile state stays writable on p2.
A board-only recommendation selects balanced on first use, avoiding the bundled
generic profiles; tuned-ppd then restores the last user selection. Battery-based
switching is disabled. TuneD 2.25.1 ships with `dynamic_tuning=0`, so no replacement
`tuned-main.conf` is needed. Existing full cards need the owner to install
`tuned-ppd` after the p1 update and boot; new full rootfs builds include it.
tuned starts only after the CPU and GPU frequency interfaces exist (bounded wait).
GNOME switching and reboot persistence passed on one tablet (H + R, 2026-11-15). Service enablement uses relative wants links. The USB
gadget uses kernel-generated addresses and no serial string; its network matches
`usb0` by name. No fixed device identity is embedded in public sources.

The charging indicator is driven by `dby-chg-red` / `dby-chg-green` kernel
LED triggers bound to the upstream PM8150L LPG LEDs. The userspace helper,
udev rule, timer and shutdown services have been removed. Unknown fuel-gauge
data preserves the previous light state; remove/shutdown extinguish both LEDs.
The IRQ/kernel-trigger revision is offline-only (O), pending device validation.
See [charger policy and hardware ownership](../../drivers/dby-chg/README.md).

The initramfs retains the read-only p1/device image and module binding contract.
After chroot, before `/sbin/init`, it reads the bounded seed file on read-only p1.
Case-insensitive `[system] safe_mode=true` writes only the tmpfs
`/run/modprobe.d/dby-safe-mode.conf`: blacklist plus install-false for msm, touch
and panel modules. The parser accepts surrounding whitespace and comment lines;
unrecognized lines are skipped and duplicate keys use the last value. Missing,
unreadable, oversized, NUL/control-containing input means false.
It does not execute the seed or call modprobe, and failures never stop system init.
The 2026-11-02 recorded device check (H) confirmed msm/panel suppression, continued SSH/network access and restoration after removing the seed. That early check did not cover the later touch-enabled bundle. Subsequent short safe-mode on/off validation on 2026-11-02/03 covered suppression and restoration of msm, panel and touch on one tablet, with SSH/network available (H + R); it does not establish long-term or other-device behavior. See [status](../../docs/status.md).

Commands emit stable stage/error messages without raw external-tool output, which
may contain private paths or identity data. Input digests are rechecked before
publication. A failure leaves the requested output absent or its previous contents
untouched. Build in an otherwise unchanged input tree; concurrent writers are not
locked out. This stage produces a p1-only intermediate bundle and no rootfs archive.

## Boot seed applier

`rootfs/overlay/usr/lib/dby/dby-seed-apply` is a standalone Python script with a
nonblocking systemd service. It reads seed files only, emits value-free journal
and file logs, always exits successfully for seed errors, and accepts only the
documented version-1 whitelist. Its bounded INI scanner shares the C initramfs
rules, with a direct C/Python agreement test. `--root DIR --seed-dir DIR` applies
to a temporary root with network/system-command effects disabled.

The applier validates SSH wire-format public keys and retains the old root
`/root/.ssh/authorized_keys` mechanism (0700 directory, 0600 file). No key file
preserves existing keys. Networks use NetworkManager keyfiles (0600), escaped
values and managed-file headers; only its own numbered files are removed when a
network disappears. A regulatory request uses `iw reg set` through cfg80211;
firmware or a self-managed driver can still constrain the effective domain.
No regulatory value is present by default or recorded in logs.

APT mirrors modify only the default deb822 source after saving its original
bytes privately; removing the mirror restores those bytes. Invalid values or
external edits are preserved for manual inspection. Panel refresh uses the
existing 60/120 `refresh_hz` module parameter. Timezones use installed zoneinfo
and an `/etc/localtime` link. Optional packages require connectivity, refresh
APT lists, install through argv, and get a success marker; failures have at most
three attempts per package list on later boots. The service does not wait-online.

Safe-mode truth comes from the initramfs runtime file. The applier logs missing
runtime support and never creates a blocking policy. Only known, self-marked old
persistent safe-mode files are cleaned when the requested value is false; manual
display fallback files remain untouched. The recorded 2026-11-02 device checks (H) cover keys, Wi-Fi, regulatory request, mirror restoration, timezone, refresh-rate delay and display safe mode; see [status](../../docs/status.md). Extra packages still miss Wi-Fi readiness at boot and required a service restart after connection. Temporary-root tests are O and do not establish compatibility with other inputs or replace the separate touch device check.
