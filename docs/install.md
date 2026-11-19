<!-- SPDX-License-Identifier: GPL-2.0-only -->

# Build and install

[简体中文](install.zh-CN.md) · [Home](../README.md) · [Architecture](architecture.md) · [Status](status.md)

This guide records the **2026-11-15 R4 maintainer fresh installation from a blank 16 GB card following the public documentation: passed (H + R), bundle `20261115-1`**. The supplied report distinguishes Claude’s SSH checks (H) from the user’s interactions (R); see [status](status.md#r4-fresh-rebuild--r4-从零重建) for their scope. Clean verified main clones and a newly extracted kernel were used. Existing owner private inputs were reused; extraction was not repeated and retains its prior evidence level. The working U-Boot/Recovery was retained. Independent third-party blank-card reproduction remains **U**. See the [fresh-card guide](fresh-install.md) for download checks and the separate 16 GB card checklist. Card writes and sudo steps are owner operations.

## 1. Inputs and workspace

Use a Linux build host. Cross builds need `aarch64-linux-gnu-` GCC/binutils (recorded version 15.2), native build tools, Python 3, kmod and squashfs-tools. Rootfs execution needs root, tar/zstd, mount tools, systemctl and native ARM64 or enabled qemu-aarch64 binfmt with the F flag; full rootfs also needs 7z/unsquashfs. Installation dependencies are listed in the [installer README](../tools/dby-install/README.md).

An example workspace, outside the Git checkouts:

```text
workspace/
  dby-w09-linux/  dby-w09-kernel/  dby-w09-uboot/
  downloads/     # pinned upstream archives, Ubuntu ISO/base
  inputs/        # your own firmware, panel/memory inputs, stock image
  config/        # your own bundle.json and seed.conf
  build/         # separate source/build/output directories
```

Commands below run from `workspace/` unless stated otherwise. Create `build/` and `config/` first. Every tool output directory must be new, with an existing parent. Keep all owner data, bundles, images and build logs outside the public repositories. Replace `/path/to/...` placeholders before use.

Obtain Linux 7.2.10, U-Boot v2026.10, the Ubuntu 26.04.1 ARM64 desktop ISO and/or Ubuntu Base 26.04 ARM64 from their upstream publishers. Verify the [pinned SHA-256 values](versions.md) before using them; the rootfs source lock is [sources.json](../tools/dby-bundle/rootfs/sources.json). A mismatch is a stop condition, not a reason to edit the lock.

Prepare your own [firmware and locks](firmware-preparation.md). In the kernel repository, follow [panel extraction](https://github.com/youzjuzi/dby-w09-kernel/blob/main/docs/panel-data.md) and [private memory extraction](https://github.com/youzjuzi/dby-w09-kernel/blob/main/dts/README.md). These tools require matching owner stock inputs; they do not make vendor data redistributable. Both touch-disabled and touch-enabled combinations have device records; enabling touch additionally requires your own exact matching AFE library and the procedure below. The historical input recipe below does not prepare ADSP firmware; current onboard audio needs separately locked owner inputs as explained at the end of this guide.

## 2. Build U-Boot, kernel, DTB and modules

For an SD-only rebuild with a working Recovery/U-Boot, retain it and skip the U-Boot build and Recovery flash steps. R4 follows this scope. / 已有可用引导链时，新卡重建保留 Recovery/U-Boot，不重编或重刷。

Run the U-Boot repository's pinned build in new directories:

```sh
(cd dby-w09-uboot && JOBS=8 sh build.sh \
  ../downloads/u-boot-2026.10.tar.gz ../build/uboot-src ../build/uboot-out)
```

The script checks its upstream hash, applies thirteen patches with fuzz=0 and builds `build/uboot-out/u-boot.bin`. See its [English](https://github.com/youzjuzi/dby-w09-uboot/blob/main/README.md) / [Chinese](https://github.com/youzjuzi/dby-w09-uboot/blob/main/README.zh-CN.md) instructions for dependencies and stock-image repacking.

For the kernel, follow the complete [pinned build recipe](https://github.com/youzjuzi/dby-w09-kernel/blob/main/docs/build.md) ([中文](https://github.com/youzjuzi/dby-w09-kernel/blob/main/docs/build.zh-CN.md)): check the archive, extract to a new source directory, apply twenty-one patches with fuzz=0, copy your generated panel header, then run `olddefconfig` and `Image modules` with a separate `O=` directory. Record the actual final configuration and release. `PCI_PWRCTRL_PWRSEQ` becomes a module; the bundle module list includes it. Do not force its configuration or change digest gates to hide differences.

Build the board DTB separately (the memory include is owner-generated):

```sh
python3 -I -B dby-w09-kernel/tools/build-board-dtb.py \
  --kernel /path/to/linux-7.2.10 \
  --memory /path/to/own-memory-include.dtsi --output build/dtb
```

Build `rt8555-backlight` and `dby-fg-readonly` in copied module directories with `make M=`, against that same kernel source/build pair; the kernel recipe supplies the complete commands. For touch, first prepare its seven plan inputs using the [offline procedure](touch-plan-preparation.md), then follow [optional touch setup](#31-enable-touch-optional). The resulting `guard=0` combination has a short H + R record; that does not validate other inputs or long-term behavior.

The U-Boot default menu has three actions: Ubuntu, HarmonyOS reboot and power off. An optional TWRP payload adds a fourth action and requires matching TWRP already in `recovery_ramdisk`; this repository does not install it. Follow the U-Boot repository for payload provenance, binary-distribution restrictions and current build/device-evidence limits.

## 3. Build the device bundle (p1)

```sh
cp dby-w09-linux/tools/dby-bundle/examples/bundle.example.json config/bundle.json
# Edit config/bundle.json before running the builder.
python3 -I -B dby-w09-linux/tools/dby-bundle/build-bundle --config config/bundle.json
python3 -I -B dby-w09-linux/tools/dby-bundle/dby-bundle verify build/bundle-p1
```

The entry point is the `build-bundle` file, not a `dby-bundle build-bundle` subcommand. Edit **all** example paths relative to `config/`, including `kernel_source_dir`, `kernel_build_dir` (the actual `O=` directory), `dtb`, `external_modules`, `private_inputs` and `output`. Set `output` to `../build/bundle-p1` for the commands above. Fill `sources.repositories` with the actual full commits you built, not the example zeros or the release placeholders in [versions](versions.md). Record the pinned upstream digests, epoch, creation time and sequence.

Before running the builder, choose either combination. For no touch, set `touch.enabled=false`, omit its external module mapping and touch firmware directory, and list the wireless/GPU locks you use. For touch, prepare the module and inputs in the next subsection and set `touch.enabled=true`. For onboard audio, supply the separate owner ADSP set; the historical no-audio recipe does not prepare it. Directory names are arbitrary; every selected directory must pass inventory, hash and mode checks, even for a disabled feature. The [bundle README](../tools/dby-bundle/README.md) defines the contract.

This produces `manifest.json` and `p1/UBOOT/` with five boot files. It is a p1-only intermediate bundle; installation also needs the selected rootfs archive.

### 3.1 Enable touch (optional)

First follow [touch plan preparation](touch-plan-preparation.md) with your own exact supported AFE library. Require `VERIFIED_OFFLINE`, all seven PINS matches and the generated `thp-plan-table.h`; a firmware BIN alone is insufficient. The guide explains the private ignored output root; it may point to private storage outside the checkout. Do not use handwritten tables or other tablets' firmware; borrowed Xiaomi elish/nabu firmware must not be loaded.

From the workspace, copy only the public module sources/build file and your generated header to a new directory. Replace the three input paths with the same prepared target source/build pair used for Image and your private plan output:

```sh
set -eu
KERNEL_SOURCE=/path/to/prepared-linux-7.2.10
KERNEL_BUILD=/path/to/prepared-kernel-build
TOUCH_PLAN=/path/to/private-touch-plan/owner-a
TOUCH_BUILD="$PWD/build/nt36523-thp"
test ! -e "$TOUCH_BUILD"
mkdir "$TOUCH_BUILD"
cp dby-w09-linux/drivers/nt36523-thp/*.c \
   dby-w09-linux/drivers/nt36523-thp/*.h \
   dby-w09-linux/drivers/nt36523-thp/Makefile "$TOUCH_BUILD/"
cp "$TOUCH_PLAN/generated/thp-plan-table.h" "$TOUCH_BUILD/"
make -C "$KERNEL_SOURCE" O="$KERNEL_BUILD" ARCH=arm64 \
  CROSS_COMPILE=aarch64-linux-gnu- M="$TOUCH_BUILD" modules
modinfo -F vermagic "$TOUCH_BUILD/nt36523-thp.ko"
```

Merge these entries into `config/bundle.json` before its first build; retain the backlight/gauge mappings, wireless/GPU private directories and other required fields. Paths below are relative to `config/`:

```json
{
  "touch": {"enabled": true},
  "external_modules": {
    "kernel/drivers/input/touchscreen/nt36523-thp.ko": "../build/nt36523-thp/nt36523-thp.ko"
  },
  "private_inputs": ["../inputs/touch-firmware"]
}
```

Prepare `inputs/touch-firmware/` as a mode-0700 locked input directory containing `inputs.json` and `firmware/novatek/nt36523-thp.bin`, following the [firmware guide](firmware-preparation.md#4-从匹配-afe-库离线提取触摸固件); payload permissions must match its lock. The module destination above is below `modules/7.2.10/` in the device image. The selected `modules_list` must contain that module path; the supplied list already does.

The builder checks vermagic `7.2.10 SMP preempt mod_unload aarch64`, the exact [ten parameter names/types](../tools/dby-bundle/README.md), and the OF/SPI aliases `of:N*T*Cnovatek,nt36523-thp` / `spi:nt36523-thp`. Missing or extra parameters are rejected. The public source defaults to `guard=1`, an unaccepted palm-transient suppression feature; the bundle fixes `guard=0` to retain the earlier reporting behavior according to code review (O). Equivalence to the earlier module has not been separately verified on hardware. Keep the bundled options unchanged:

```text
options nt36523_thp managed=1 settle_ms=100 skip_probe=1 edge_px=50 filter=1 lock_px=12 confirm=1 multi=1 guard=0
```

The public board source already contains the `novatek,nt36523-thp` node at 16 MHz, without reset/regulator properties. No DTB or kernel change is needed for this touch configuration. Run the p1 builder/verify commands above with the completed config; if a bundle already exists, choose a fresh output and increment sequence rather than overwrite it. Then continue with rootfs. Post-boot checks are [read-only](#touch-missing); touch and display share the same controller.

## 4. Build test or full rootfs

Start with a plan by adding `--dry-run` to either command below and omitting `sudo`. The selected archive must already exist and match its source hash even for a successful plan. Execution uses root only on the build host, with APT connectivity and enough temporary space (the recorded workflow allowed about 15 GB). Time/space figures are observations, not guaranteed limits.

```bash
set -o pipefail
sudo python3 -I -B dby-w09-linux/tools/dby-bundle/build-rootfs \
  --kind test --base downloads/ubuntu-base-26.04-base-arm64.tar.gz \
  --no-baseline --mirror http://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports \
  --output build/rootfs-test 2>&1 | tee build/rootfs-test.log
```

```bash
set -o pipefail
sudo python3 -I -B dby-w09-linux/tools/dby-bundle/build-rootfs \
  --kind full --iso downloads/ubuntu-26.04.1-desktop-arm64.iso \
  --no-baseline --mirror http://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports \
  --output build/rootfs-full 2>&1 | tee build/rootfs-full.log
```

Test uses Ubuntu Base plus init, SSH and networking, without GNOME. Full extracts the pinned ISO minimal layer, purges the recipe's listed packages and applies the desktop overlay. Both produce `rootfs.json`, `packages.txt` and their respective `rootfs-test.tar.zst` / `rootfs.tar.zst` archives. If necessary, give the completed output directory back to your host account with `sudo chown -R "$(id -u):$(id -g)" build/rootfs-test` (or `build/rootfs-full`), only after a successful build and cleanup.

If you have the matching 1492-package reference inventory, replace `--no-baseline` with `--baseline-packages /path/to/verified-baseline-packages.txt`. Its digest/count must match [sources.json](../tools/dby-bundle/rootfs/sources.json). Do not supply both flags. Without that inventory, explicit `--no-baseline` records the new actual inventory; omitting both also permits a build. The old private inventory is not a prerequisite for a new owner.

`--mirror` is a build-time transport choice; official sources are restored before packing. The recorded base build used HTTP because the initial base lacks CA certificates. HTTPS can fail before certificate packages are installed. APT package versions are not frozen to a repository snapshot: manifests describe the actual result, and cross-date byte-identical rootfs reproduction is not promised. See [rootfs details](../tools/dby-bundle/rootfs/README.md).

## 5. Attach rootfs to a new bundle

There is no standalone attach subcommand yet. Use the existing Python `attach_rootfs` API:

```sh
python3 -I -B - <<'PY'
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path('dby-w09-linux/tools/dby-bundle').resolve()))
from dbybundle.rootfs import attach_rootfs
source_bundle = 'build/bundle-p1'
rootfs_output = Path('build/rootfs-test')
combined_bundle = 'build/bundle-test'
metadata = json.loads((rootfs_output / 'rootfs.json').read_text())
archive = 'rootfs-test.tar.zst' if metadata['kind'] == 'test' else 'rootfs.tar.zst'
attach_rootfs(source_bundle, combined_bundle, rootfs_output / archive, metadata)
PY
python3 -I -B dby-w09-linux/tools/dby-bundle/dby-bundle verify build/bundle-test
```

For full-only, change the three paths to `build/bundle-p1`, `build/rootfs-full`, `build/bundle-full`. To keep both rootfs kinds, attach full to `build/bundle-test` instead. Output must be new; the function checks the archive against `rootfs.json`, preserves the other rootfs kind and writes a new verified manifest. Select `build/bundle-test` for `--minimal`, or your full/both bundle for a full install below.

## 6. Install to the card (owner operation)

**This erases the whole selected card.** Use the built-in reader's whole `/dev/mmcblkN` SD device (8 GiB–2 TiB, 512-byte sectors). USB `/dev/sdX`, eMMC, partitions, read-only/system/mounted targets are refused. Some MMC readers report removable=0; SD type and the other safety checks determine acceptance.

Use `lsblk` to identify the card and unmount its mounted partitions before installation. Replace `/dev/mmcblk0` with the actual whole card. Review a dry-run first:

```sh
python3 -I -B dby-w09-linux/tools/dby-install/dby-install install \
  --bundle build/bundle-full --device /dev/mmcblk0 --dry-run
sudo python3 -I -B dby-w09-linux/tools/dby-install/dby-install install \
  --bundle build/bundle-full --device /dev/mmcblk0
```

For a minimal test card, use the test/both bundle and add `--minimal` to both commands. Test p2 defaults to 4 GiB, with the remainder unallocated; `--p2-size 4G` can select a supported test size. Full p2 occupies the remaining card. The terminal shows the target and plan and requires its **complete device name** typed as confirmation. There is no automatic `--yes` or menu. Device-package update/rollback are separate commands below; user-data backup remains unimplemented.

Optional `install --no-readback` skips only p2's post-unmount per-file read-back. Write-time checks, p1 full read-back and p2 metadata/contract checks still run. It is not full p2 verification. The recorded full-desktop install used this option (H); a later full `verify` is a separate check:

```sh
sudo python3 -I -B dby-w09-linux/tools/dby-install/dby-install verify --bundle build/bundle-full --device /dev/mmcblk0
sudo python3 -I -B dby-w09-linux/tools/dby-install/dby-install info --device /dev/mmcblk0
```

`verify` detects the installed rootfs kind; do not add `--minimal` or `--no-readback`. Full verify is intended for a freshly installed card before first boot, since normal system use changes p2 files. For a used card, run `verify --p1-only` as shown below. Default `info` is a compact metadata/seed-presence summary; `info --full` includes the original complete manifest. Neither output includes seed values.

### 6.1 Update the device package without reinstalling (H + R)

`update`/`rollback` were validated on 2026-11-02 on **one SD card and one tablet**,
with bundles **`20261102-2` / `20261102-3`**, both kernel **`7.2.10`** (H + R).
Update applies only to cards installed by this tool: table/labels/rootfs
contracts must match, and the card's own manifest must match exactly five UBOOT
files. It replaces those files and p1's manifest, preserves `dby-seed/` and p2,
and does not apply a new rootfs. Use `apt` inside Ubuntu for system software.

**Shut the tablet down normally before removing the SD card.** Review the preview
with a newer verified bundle and an explicit private **host**
backup directory, outside the card/mount points. Raw-device read permission is
needed even though dry-run does not require root or change the card/backup path:

```sh
python3 -I -B dby-w09-linux/tools/dby-install/dby-install update \
  --device /dev/mmcblk0 --bundle /path/to/new-bundle \
  --backup-dir /path/to/private-host-backups --dry-run
sudo python3 -I -B dby-w09-linux/tools/dby-install/dby-install update \
  --device /dev/mmcblk0 --bundle /path/to/new-bundle \
  --backup-dir /path/to/private-host-backups
```

The backup root may be new if its parent exists. A new 0700 timestamped child
contains the old five files, exact card manifest and size/hash metadata; it is
re-read before changing p1 and may contain private firmware. Never publish it.
The complete device name must be typed. Same-ID/same-boot-content updates are a
no-op; same-ID/different-content is rejected. Older IDs need `--allow-older`.
Rootfs recipe/package changes only produce a notice, since p2 stays unchanged.

The tool stages/hashes `UBOOT.new`, renames the old/new directories, replaces the
manifest via a temporary name and verifies after reopening read-only. **FAT
renames are not atomic across files or power loss.** A failure after switching
attempts automatic recovery and always returns nonzero, preserving the original
error plus the backup path/manual command. Do not assume recovery succeeded.

Use the printed backup path for a deliberate rollback, including an interrupted
card whose current manifest is inconsistent:

```sh
python3 -I -B dby-w09-linux/tools/dby-install/dby-install rollback \
  --device /dev/mmcblk0 --backup /path/to/verified-p1-backup --dry-run
sudo python3 -I -B dby-w09-linux/tools/dby-install/dby-install rollback \
  --device /dev/mmcblk0 --backup /path/to/verified-p1-backup
```

Rollback validates the backup, requires UBOOT or UBOOT.old, clears reserved
staging and restores without a second backup. Update instead refuses leftovers
without deleting them. If UBOOT is missing and UBOOT.old exists, rename it back
to UBOOT or use verified rollback. Both commands reject `--minimal`, `--p2-size`,
`--no-readback` and `--image-size`. They do not format or write p2, edit seeds,
back up user data or reinstall the system. See [rules/error codes](../tools/dby-install/README.md#update-the-device-package-and-roll-back).

For a card that has booted, validate the installed device package without
comparing changed p2 files against the original rootfs archive:

```sh
sudo python3 -I -B dby-w09-linux/tools/dby-install/dby-install verify --p1-only \
  --bundle /path/to/installed-bundle --device /dev/mmcblk0
```

This checks the table/labels and five boot files/manifests, never mounts p2 and
ends with `OK: p1 matches the bundle; p2 not checked.` It does not validate p2's
journal or system files. Default full verify still checks the rootfs and adds a
p1-only hint to p2 hash failures after normal system use. The 2026-11-02 owner retest covered info, verify --p1-only and dry-run on one SD card (R). Info --full and injected abnormal paths remain O.

**Unclean shutdown:** one recorded removal without shutdown left p2 with
`needs_recovery`; `ro,noload` showed an old view and the previous tool reported a
misleading kind error. The owner repaired unmounted p2 with `e2fsck -p`, then
retried successfully. New read-only preflight now returns `E_INSTALL_P2_RECOVERY`
before p2 mount (also for maintenance dry-run). Boot and shut down normally, or
check the actual unmounted p2 target and run the suggested repair yourself:

```sh
sudo e2fsck -p /dev/mmcblk0p2
```

The installer never runs this repair, replays the journal or writes p2 during
update/rollback/verify/info. The new detection/message has O coverage.

**Validated usage (H + R, scope above):** same-version no-op created no backup;
explicit `--allow-older` downgrade and normal upgrade booted; touch worked again
after upgrading. Two rollbacks used two distinct host backups, with manual
MANIFEST/device-image hash checks and no old/new leftovers. Seed, other p1
folders and p2 were preserved. Rollback preview's `old_bundle_id: null` means the
current manifest is not trusted; it does not imply an interrupted card.

Real-card power loss/interruption recovery remains U (image simulation only).
Cross-kernel updates remain unverified and unsupported by the current
`7.2.10`-only format. Update does not apply p2 updates. Menu and user-data backup/restore commands are still
unimplemented; FAT renames do not make switching power-loss safe.

### 6.2 Reinstall the system (p2 only, H + R)

Use `reinstall-system` for a broken package/configuration setup while keeping p1's device package and Wi-Fi/public-key seed. **Every home directory, installed package, system setting and SSH host key on p2 is lost. No user-data backup is made.** Use only a reinstallable card, never the only copy of needed data. Full-system reinstall has H + R evidence on 2026-11-02/03, limited to one SD card, one tablet, bundle `20261102-3` and kernel `7.2.10`: preview/execution succeeded, independent whole-p1 SHA256 comparison matched, and subsequent full `verify` passed. The new system booted with old users/data gone and regenerated SSH host keys; the preserved plaintext Wi-Fi seed connected automatically and touch worked. Shared install formatting/extraction code was exercised on the card. First setup did not hang in this run; the intermittent defect remains unresolved. `--minimal`, `--no-readback` and mid-operation failures remain O only; real-card interruption/power loss and cross-kernel updates remain U. Menu and user-data backup/restore remain unimplemented (U).

Shut down cleanly, remove the card, unmount all its partitions and confirm the target and bundle. The card's bundle ID and all five boot files must match the input bundle. If different, run `update` as described in §6.1 first, then preview:

```sh
sudo tools/dby-install/dby-install reinstall-system --device /dev/mmcblk0 --bundle /path/to/bundle --dry-run
sudo tools/dby-install/dby-install reinstall-system --device /dev/mmcblk0 --bundle /path/to/bundle
```

Type the complete target followed by ` p2`, for example `/dev/mmcblk0 p2`; the device name alone is refused. Add `--minimal` for the test rootfs; full is the default. `--no-readback` skips post-unmount per-file checks but retains contracts, geometry, whole-p1 hashing and table comparison. No repartitioning/resizing occurs and neither `--p2-size` nor `--image-size` is accepted. If the selected archive plus margin does not fit existing p2, use `install` to repartition.

p1 stays read-only and seeds remain intact. An unreplayed p2 journal does not prevent reformatting, but any mounted target partition is refused. Old data cannot be recovered after formatting; a failure leaves p2 incomplete. Fix the cause before rerunning this command or install. If p1/table hashes cannot be checked or change, the error says so; do not assume preservation.

First boot runs setup again and regenerates SSH host keys. Verify the device identity before updating the client's `known_hosts`. See [contracts and error codes](../tools/dby-install/README.md#reinstall-system-p2-only-h--r).

## 7. Public key and seed

Use the installer's `seed` commands to validate and edit your public keys and seed. Evidence for wizard/timezone (H) and safe-mode/write/restore (H + R) is limited to the scope below; remaining settings are O/U. Shut down cleanly, remove the card, confirm the whole-card target on the host and unmount its partitions. Only p1 is mounted, after checking the manifest and five boot files; an unreplayed p2 journal does not block seed editing. Keep your private SSH key on your client and provide only the **public** key.

```sh
sudo tools/dby-install/dby-install seed show --device /dev/mmcblk0
sudo tools/dby-install/dby-install seed set --device /dev/mmcblk0 --backup-dir ./seed-backups --wifi example --ssid example-wifi --psk-prompt --dry-run
sudo tools/dby-install/dby-install seed set --device /dev/mmcblk0 --backup-dir ./seed-backups --wifi example --ssid example-wifi --psk-prompt
sudo tools/dby-install/dby-install seed set --device /dev/mmcblk0 --backup-dir ./seed-backups --authorized-keys /path/to/own-ssh-public-key.pub
sudo tools/dby-install/dby-install seed wizard --device /dev/mmcblk0 --backup-dir ./seed-backups
sudo tools/dby-install/dby-install seed restore --device /dev/mmcblk0 --backup ./seed-backups/SELECTED_BACKUP
```

The network name is fictional. The no-echo prompt asks twice and stores plaintext by default (8–63 bytes). Mismatch returns `E_SEED_PSK_MISMATCH` without writes. Explicit `--derived-psk` derives an SSID-bound PSK; 64-hex input is preserved as a raw PSK. Both PSK forms are WPA2-only, cannot be used with WPA3/SAE or WPA2/WPA3 mixed networks, and may prevent connection; use only on confirmed WPA2-only networks. Only derived/raw PSK entries require a new password source when changing SSID; plaintext entries do not. A PSK is also a credential. **Backups contain Wi-Fi credentials; keep them private** (directory 0700, files 0600). Restore saves the displaced seed beside the selected backup and deletes files recorded as previously absent.

`seed set` changes only supplied settings. In the wizard, Enter keeps and `-` removes; public keys cannot be cleared. Writes show a masked diff and require the complete target name; dry-run creates no backup and makes no writes. Normalization discards comments, junk and unknown sections/keys, lists discarded names in the diff, and preserves originals in the backup. Timezone syntax is checked without consulting p2's tzdata. See [complete seed command rules](../tools/dby-install/README.md#seed-and-safe-mode-commands).

Manual copying remains an alternative **without the validation, backup and automatic recovery above**. After confirming the partition:

```sh
sudo mkdir -p /mnt/dby-card
sudo mount /dev/mmcblk0p1 /mnt/dby-card
sudo cp /path/to/own-ssh-public-key.pub /mnt/dby-card/dby-seed/authorized_keys
sudo cp /path/to/own-seed.conf /mnt/dby-card/dby-seed/seed.conf
sudo sync
sudo umount /mnt/dby-card
```

Minimal settings example; enter Wi-Fi credentials through `--psk-prompt` above:

```ini
version = 1
[system]
timezone = Etc/UTC
safe_mode = false
```

Optional sections are `[apt] mirror=...`, `[display] refresh_hz=60` or `120`, and `[packages] install=tree`. Add `country` only inside your private Wi-Fi section, using the correct regulatory setting for your location; no value is supplied here. Passwords may be 8–63 bytes or a 64-hex PSK. A PSK is also a credential. FAT does not protect either from someone who can read the card: do not publish seeds, bundles or resulting logs containing identity data.

The seed is read-only on the tablet. SSH keys apply to root; ordinary users/hostname are created by the full desktop's first-run wizard. Missing timezone leaves the existing setting; deleting the APT mirror restores its saved original. Display-rate changes take effect on the following boot; removing the setting also needs a later boot to restore 120 Hz. Extra-package requests can miss Wi-Fi readiness; after connectivity, `sudo systemctl restart dby-seed` succeeded in the recorded test (H). Automatic retry on network arrival is not implemented.

For display recovery, `[system] safe_mode=true` is read in initramfs before system init. It writes a temporary `/run/modprobe.d/dby-safe-mode.conf` policy denying msm/panel/touch module loading. A black screen is expected; USB/SSH/Wi-Fi remained reachable in the recorded test (H). Remove the setting and reboot to restore normal loading. The installer on/off commands below were validated in the recorded scope (H + R); they do not fix the black-screen cause. `off` writes `safe_mode=false`.

```sh
sudo tools/dby-install/dby-install safe-mode status --device /dev/mmcblk0
sudo tools/dby-install/dby-install safe-mode on --device /dev/mmcblk0 --backup-dir ./seed-backups
# To restore normal display/module loading on the next boot:
sudo tools/dby-install/dby-install safe-mode off --device /dev/mmcblk0 --backup-dir ./seed-backups
```

On 2026-11-02, `seed set` wrote a derived PSK and passed its own read-back check, and `seed restore` restored the original seed on one SD card / one tablet (H + R). On the one tested router, the correctly derived PSK could not connect; restoring the original plaintext passphrase connected immediately. WPA3/SAE or mixed-mode negotiation is a suspected cause, not verified on the router. On 2026-11-02/03, one SD card and one tablet with bundle `20261102-3` and kernel `7.2.10` provided these additional results: `seed wizard` rejected mismatched passphrases in dry-run without writing, and a real timezone-only edit showed only that change, passed read-back and created a backup (H). `seed set --timezone` was written and confirmed after boot with `timedatectl` for two timezones (H). A mistyped target confirmation was refused without writing (H). `safe-mode on/off` blocked then restored msm, panel and touch modules across boots; while active the backlight was lit without an image and USB networking/SSH worked (H + R). These results do not establish other devices or networks. Other `seed set` settings (mirror/rate/packages/replacement or added public keys) still need real-card retesting; derived-PSK connectivity on confirmed WPA2-only networks remains U (command logic O). The new wizard label-selection rules are tested offline (O), awaiting real-card retest.

The wizard lists existing Wi-Fi labels one per line. One label becomes the default; multiple labels have no default and empty input asks again; no networks defaults to `main`. A new label always requires an exact second entry, including the empty-card default; mismatch returns to label selection. Other prompts retain Enter-to-keep, `-` removal, double passphrase confirmation and non-clearable public keys.

## 8. Repack/flash Recovery and first boot (owner operation)

Use your own verified stock Recovery for this device/firmware and an already-unlocked bootloader. Verify the U-Boot output and keep a checked stock copy for rollback. Packing never accesses a device:

```sh
python3 -I -B dby-w09-uboot/tools/pack-recovery.py \
  --base /path/to/own-stock-recovery.img --kernel build/uboot-out/u-boot.bin \
  --out build/recovery.img --expect-base-sha256 YOUR_VERIFIED_STOCK_SHA256
```

Replace the hash placeholder with your independently verified 64-hex stock digest. The tool self-checks stock repacking and preserves ramdisk, DTB, DTBO and the AVB tail; those retained descriptors do not authenticate the replaced kernel. Acceptance is established only for the recorded unlocked device baseline, not arbitrary firmware.

The owner then runs:

```sh
fastboot flash recovery build/recovery.img
fastboot reboot
```

Use plain `fastboot reboot`, not `fastboot reboot recovery`. Power off, insert the prepared card and manually enter Recovery. U-Boot offers Ubuntu / reboot to HarmonyOS / power off; volume keys move and power confirms, with Ubuntu as the timed default. Booting after preparation needs no host computer. To roll back, flash only your verified original stock Recovery, then use plain reboot. Restore stock Recovery before factory reset; no other internal partition should be flashed.

## 9. First checks and known problems

SSH to `root@172.16.42.1` over the USB network, or to the tablet's privately determined Wi-Fi address. Reinstallation regenerates SSH host keys; verify the expected device before removing its old known-host entry. As read-only checks, inspect `uname -r`, `systemctl --failed`, `ip -brief address`, `getent hosts ports.ubuntu.com`, `timedatectl` and the journals for NetworkManager, dby-seed and the display. Full desktop checks include GDM/GNOME and hardware-rendering diagnostics. Keep raw output private; public reports should give sanitized results and H/R/O/U evidence labels.

- **GNOME first setup hangs:** the recorded first session had a busy dconf worker; root cause is unknown. As the newly created desktop user, create `~/.config/gnome-initial-setup-done`, then restart GDM with administrative rights. Restarting GDM ends graphical sessions. This restored the recorded session (H + R), not a general fix.
- **Display:** kernel patches `0017` + `0018` (`2026-11-18`, one tablet H + R on bundle `20261118-5`) resolved wake black screens and rapid-toggle stripes (`0/46` ESD resets across 46 wakes/switches including 38 rapid `13–68 ms` presses); kernel alarm-IRQ recovery (`0013`) remains active and multi-day long-term reliability is unverified. Use the documented safe-mode/SSH path for diagnosis; do not substitute touch firmware or change hardware defaults speculatively.
- **Wi-Fi works but DNS/time do not:** the current recipe supplies the resolved stub link and time service. Check link state, DNS and time separately; do not infer them from Wi-Fi association alone.
- **Install command failed:** retain the stable error code; `DBY_TRACE=1` adds command names/status without raw arguments. A `udevadm settle` timeout is bounded and may be nonfatal, but partition-table/node checks must still pass. See the [installer diagnostics](../tools/dby-install/README.md).
- **A build fails or is interrupted:** inspect its filtered log and cleanup result. A reported retained mounted workspace needs inspection; do not delete it blindly. A new attempt needs a fresh output directory.
- **Sound or Bluetooth pairing missing:** these remain U on the rebuilt baseline. Installer menu and user-data backup/restore are also not implemented; device-package update/rollback have H + R within the scope of §6.1; new hardening options are O.

### Touch missing

First check module loading, the locked firmware input and `firmware/novatek/nt36523-thp.bin` in the device image, then read the module parameters and kernel log. Run these read-only commands on the booted tablet; keep their output private:

```sh
lsmod | grep nt36523
for parameter in /sys/module/nt36523_thp/parameters/*; do
  printf '%s: ' "${parameter##*/}"
  cat "$parameter"
done
dmesg | grep -i nt36523
```

Compare the parameters with the options line above, especially `guard=0`. The recorded boot had `state=done steps=46 error=0`, followed by `state=running` and `fw=loaded` (H); this is a comparison point, not a guarantee for other input combinations. Do not write sysfs to change guard or recovery settings as a troubleshooting step. Three-finger gestures, stylus, long-term/startup reliability and guard itself remain U. Do not substitute another tablet's firmware when touch or display behaves abnormally.

## Current source versus the historical recipe

The kernel recipe now applies twelve patches. The current default module list also requires `dby_chg.ko` and `dby-tfa9874.ko`: build them in separate copies using the kernel guide and add destinations `kernel/drivers/power/supply/dby_chg.ko` and `kernel/sound/soc/codecs/dby-tfa9874.ko` to `external_modules`. The example includes these mappings; replace every placeholder path and source commit. Include an owner-prepared locked Venus directory for the current full module/board combination. Replace its ADSP entry with an actual owner-prepared locked directory. If omitting onboard audio, remove that private input and supply an explicitly reduced module list; the full list is not the minimal recipe.

Current onboard audio consumes a matching owner-supplied ADSP set, locked per file at package paths `firmware/qcom/dby-w09/adsp.*` with hashes and modes. Acquisition and the complete current feature combination have not been independently reproduced from scratch. The historical radio/GPU/touch guide does not constitute complete audio/video deployment instructions. Existing Venus scope and defects are in status; this review adds no firmware deployment promise.

The installation workflow has not been independently validated by a third party starting from a blank card; feedback is welcome. Raw-device dry-run also needs read access to the device node. If the ordinary user lacks it, the owner must use sudo for the same preview after checking the target; a preview still accesses the selected device.
