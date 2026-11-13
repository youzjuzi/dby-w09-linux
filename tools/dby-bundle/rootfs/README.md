# Root filesystem recipes

Language: English. Full workflow: [English install](../../../docs/install.md) / [中文安装](../../../docs/install.zh-CN.md). Evidence and known issues: [status](../../../docs/status.md).

`build-rootfs` and `build-bundle --stage rootfs` share the same planner and
executor. Source SHA-256 mismatches stop the build without downloading,
substituting or modifying inputs. Output directories must be fresh and outside
tool sources. Plans use symbolic input/work paths to keep host paths out of logs.

```sh
python3 -I -B tools/dby-bundle/build-rootfs --kind full --iso /path/to/desktop.iso --output /path/to/new-full-output --dry-run
python3 -I -B tools/dby-bundle/build-rootfs --kind test --base /path/to/ubuntu-base.tar.gz --output /path/to/new-test-output --dry-run
python3 -I -B tools/dby-bundle/build-bundle --stage rootfs --kind test --base /path/to/ubuntu-base.tar.gz --output /path/to/new-test-output --dry-run
```

A missing ISO still prints the complete plan and a clear `E_ROOTFS_INPUT` error.
The exact ISO, minimal layer and base hashes are in `sources.json`. The base hash
comes from the supplied official base checksum list; no archive is downloaded.

## Running on a root-capable build machine

Execution requires root, tar/zstd, util-linux mount tools, systemctl and either
native ARM64 or enabled `qemu-aarch64` binfmt with the F flag. Full builds also
need 7z and unsquashfs. APT requires access to the configured upstream repositories.
Keep enough space for the extracted tree and compressed archive. A dry-run does
not enter chroot, create mounts, run APT or create the output directory.

```sh
sudo python3 -I -B tools/dby-bundle/build-rootfs --kind full --iso /path/to/desktop.iso --baseline-packages /path/to/baseline-packages.txt --output /path/to/new-full-output
sudo python3 -I -B tools/dby-bundle/build-rootfs --kind full --iso /path/to/desktop.iso --no-baseline --output /path/to/new-full-output
sudo python3 -I -B tools/dby-bundle/build-rootfs --kind test --base /path/to/ubuntu-base.tar.gz --output /path/to/new-test-output
```

For a network that cannot reach the default repository, choose a temporary
build mirror. Both rootfs entry points accept `--mirror`. It must be an http(s)
URL using the seed mirror validation rules: no credentials, query, fragment,
control characters or parent-directory components.

```bash
set -o pipefail
sudo python3 -I -B tools/dby-bundle/build-rootfs --kind test \
  --base /path/to/ubuntu-base.tar.gz --output /path/to/new-test-output \
  --mirror http://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports \
  2>&1 | tee /path/to/rootfs-build.log
```

`--dry-run` shows the selected build mirror and the APT command options without
connecting to it. The executor temporarily replaces the `URIs:` fields in
`ubuntu.sources`, including continuation lines. It restores the original bytes
and ownership/permissions after APT, including failure and Ctrl-C, before packing.
The completed rootfs keeps the source archive's official default repositories.
No build mirror setting or backup is installed. Repository lists and binary APT
caches are removed as well, so build-mirror URLs and cache filenames do not enter
the archive. The selected mirror value is excluded from recipe digests and output
metadata; it is only a build transport choice. The initial base lacks CA certificates, so the recorded base build used HTTP; HTTPS may fail before certificate packages are installed.

Every build APT call uses command-line-only `Acquire::http::Timeout=30`,
`Acquire::https::Timeout=30` and `Acquire::Retries=3`. These bound network requests
and retries; they are not a total deadline for package installation scripts and
are not written into the rootfs configuration.

Stage names and recognized APT/extraction/systemctl progress stream to stderr as
they arrive, so `tee` can preserve progress while it remains visible. Output is
filtered rather than logged verbatim: repository addresses become `[repository]`,
package actions keep package names, and paths, DNS data, identity text and arbitrary
maintainer-script output are omitted. Errors retain stable codes and generic
diagnostics. The ISO layer's binary stdout goes only to its temporary file.

On Ctrl-C the active command group is terminated and reaped before cleanup; the
CLI returns 130 with `E_ROOTFS_INTERRUPTED` after unwinding. Temporary mounts are
unmounted before the workspace is removed. Cleanup confirms that no build mounts
remain, or reports `E_ROOTFS_CLEANUP` and the retained temporary directory name
beside the requested output; mounted workspaces are never recursively removed.

For comparison, supply the canonical 1492-package baseline list matching
`sources.json`. It uses sorted `package version architecture` lines. A supplied
baseline must match its locked digest/count. `--no-baseline` explicitly skips
comparison; omitting both baseline options also allows building without one.
In either case, rootfs.json records `compared: false` and identifies packages.txt
and its digest/count as the new baseline. These options are mutually exclusive.
The generated inventory is never forced to the old hash.

The privileged module creates a private mount namespace, a minimal temporary
device tree with no block nodes, read-only proc/sys, temporary DNS and a
service-start suppression policy. It restores temporary files and unmounts before
packing. A remaining mount retains the work directory and prevents recursive
cleanup. The source tree/archive is not modified. Plan-only tests exercise no privileged execution (O). Later 2026-11-02 records cover actual test/full recipe runs (O), followed by device boots (H); they do not establish arbitrary-input compatibility.

After the temporary build environment has restored its files, cleanup sets
`/etc/resolv.conf` to the relative symbolic link
`../run/systemd/resolve/stub-resolv.conf` in both full and test profiles. Existing
regular files or links are replaced, so the base archive's empty file and the
host's temporary DNS contents cannot become the installed resolver configuration.
The link target is provided by systemd-resolved at runtime; it can be absent in
the offline rootfs. Repeating cleanup keeps the same link.

Outputs are the full/test tar.zst, `packages.txt`, and `rootfs.json` with source,
recipe, inventory and archive digests. Optional `--device-bundle` plus
`--bundle-output` creates a new combined bundle; the original p1 bundle stays
unchanged. The full rootfs summary goes into schema-1 rootfs metadata; test-recipe
provenance goes into source input digests. Existing other-kind archives are kept.

## Recipe contents and origins

`purge.list` preserves all 39 names from the old installer source lock and adds
`firmware-sof-signed`, for 40 names in total:

| Group | Count | Reason |
|---|---:|---|
| boot-tools | 1 | Remove flash-kernel hooks for a different boot path |
| cloud-init | 2 | Local first boot does not use cloud provisioning |
| firmware | 20 | Device firmware belongs in p1; also remove Intel SOF, which has no reverse dependencies in the reviewed desktop inventory |
| fwupd | 1 | No system-firmware updater in this SD system |
| kernel | 13 | Kernel and modules are supplied by p1 |
| snap | 2 | Remove the snap service/browser shell from the minimal layer |
| ubuntu-report | 1 | Preserve the existing telemetry cut |

Only installed names are passed to purge. Full additions are openssh-server,
python3, iw and wireless-regdb: SSH, seed execution, regulatory requests and the
public regulatory database. Test additions also include network-manager,
wpasupplicant, rfkill, systemd-resolved, systemd-timesyncd, iproute2 and curl, with no
desktop. wpasupplicant is NetworkManager's Wi-Fi runtime dependency but is only
a package recommendation, so `--no-install-recommends` requires listing it
explicitly. rfkill supplies wireless-block inspection/control; systemd-timesyncd
supplies NTP time synchronization when the hardware RTC starts with an old date.
curl supplies a command-line tool for network and HTTP diagnostics in test images.
The test profile explicitly enables `systemd-timesyncd.service` after package
installation with `systemctl --root`, independently of package post-install
enablement. Synchronization still requires reachable NTP servers at runtime.
The full profile retains its existing chrony service and package recipe.
The test base archive does not provide
the init/device-management stack, so the test recipe explicitly installs systemd,
systemd-sysv (`/sbin/init`), udev (device events and module autoload), kmod
(`modprobe`), dbus, libpam-systemd, ca-certificates, iputils-ping and netbase.
The `systemd-networkd.service` unit is supplied by the systemd package; the recipe
does not request a separate systemd-networkd package. Service enablement follows
APT installation, with `--no-install-recommends --no-remove` retained. Device
firmware must come from p1. Only the following paths directly under
`usr/lib/firmware` are allowed: `regulatory.db`, `regulatory.db.p7s`, and
`skl_hda_dsp_generic-tplg.bin`. The last file is generic ALSA topology data from
`alsa-topology-conf`, which is retained; it is not device firmware. Other files
or links remain refused with `E_ROOTFS_FIRMWARE`, showing the first unexpected
relative path in sorted order. Paths are escaped for display; no host path or
file contents are printed.

After APT purge/add and restoration of the temporary build environment, read-only
payload checks run before applying overlays or enabling services: valid system
accounts only (UID below 1000, with the existing nobody/65534 exception), empty
`/home`, no rootfs module tree, and the firmware allowlist. Cleanup repeats these
checks before inventory and packaging. This exposes leftover desktop payloads
earlier without weakening the final gates or changing the test package list.

The same payload gates reject character devices, block devices, FIFOs and sockets
with `E_ROOTFS_SPECIAL`, reporting the first relative path in JSON-escaped form.
Scanning uses inode types without following symbolic links. The early check only
exempts entries under `dev/`, because cleanup removes all of that directory's
contents, including nested directories and links, while retaining an empty `dev`
directory. The final check has no exemption. Runtime `/dev` is supplied by
devtmpfs; static nodes from the ISO must not enter the archive. The existing
`proc`, `sys` and `run` directory setup and installer special-file rejection stay
unchanged.

| Overlay file | Origin and reason | Test profile |
|---|---|---|
| etc/apt/apt.conf.d/99-dby-no-auto-upgrade.conf | Old policy: manual updates, no periodic APT upgrade | Keep |
| etc/ssh/sshd_config.d/00-dby-publickey.conf | Old root public-key-only SSH mechanism | Keep |
| etc/systemd/system/ssh.service.d/20-dby-hostkeys.conf | Old first-boot host-key generation and config check | Keep |
| etc/NetworkManager/conf.d/91-dby-usb-unmanaged.conf | Old split: networkd owns usb0, NM owns wireless | Keep |
| etc/UPower/UPower.conf.d/90-dby-battery-policy.conf | Old policy for the read-only gauge's unknown state | Omit, no desktop power stack |
| etc/xdg/monitors.xml | Old GDM/initial-setup orientation, scale and 120 Hz mode | Omit, no GUI |
| etc/fstab | Existing ubuntu-root volume contract | Keep |
| usr/lib/dby/dby-seed-apply | Stage 5 bounded seed applier | Keep |
| usr/lib/systemd/system/dby-seed.service | Stage 5 asynchronous unit, no wait-online | Keep |
| test/overlay/etc/systemd/network/50-dby-usb0.network | Explicit usb0 address and DHCP server, matching the p1 gadget subnet; no router/DNS advertisement | Test only |

No old Bluetooth helper, Xfce/LightDM scripts, user preferences, mDNS publisher
override or personal settings are added. The test network uses a fixed device
address and DHCP server so a cable-attached host can obtain an address without
depending on an external DHCP server. This is a network configuration only;
booting remains independent of a host computer.

The builder enables the relevant standard services, masks networkd wait-online,
creates the empty module bind parent and writes `/etc/dby-rootfs-kind` as `full`
or `test`. Cleanup removes generated keys/IDs, caches/logs, preset hostname,
timezone and network profiles; it refuses preinstalled regular user/home data.

## Reproducibility and pending checks

GNU tar uses sorted names, fixed mtime and numeric ownership, preserving the
tree's real uid/gid, with volatile PAX times removed; zstd uses automatic
multithreading (`-19 -T0`). Thread count did not change the output in the tested
`-T1`/`-T0` comparisons with zstd 1.5.7, including a 512 MiB synthetic sample.
Synthetic packing is byte-stable despite source mtime changes. APT package
versions are not frozen to a repository snapshot, so archive determinism does not
establish cross-date dependency reproducibility. Exact manifests record the result.

The retained full desktop monitor profile is 120 Hz. A seed selecting 60 Hz can
make Mutter discard that saved profile; device validation must cover the resulting
orientation/mode behavior. No claim is made that the desktop/runtime contracts
have been verified by a dry-run.

The 2026-11-02 recorded test/full builds are O evidence; resulting card boots are H, with user observations R for desktop interaction; see [status](../../../docs/status.md). Current pending work includes the GNOME first-setup hang, extra-package seed network timing, full-desktop 60 Hz profile behavior and long-term display/power stability. The full install used --no-readback, so boot success does not prove post-unmount p2 per-file verification. A dry-run alone remains O.
