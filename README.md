# DBY-W09 Linux

One-tablet development system: GNOME, display/touch, networking, audio and basic charging have scoped H/R records; suspend, cameras and sensors remain U. **Flashing requires an unlocked bootloader, replaces Recovery and can leave the device unbootable. The installation workflow has not been independently validated by a third party starting from a blank card. Feedback is welcome.**

Run mainline-based Linux and Ubuntu ARM64 from a microSD card on the Huawei MatePad 11 (DBY-W09, Snapdragon 865). Normal power-on keeps HarmonyOS as the default; manual Recovery entry starts U-Boot and the card's system. Everyday boot needs no computer. Initial preparation uses a Linux build host and an owner-unlocked device.

[简体中文](README.zh-CN.md) · [Build and install](docs/install.md) · [Status and evidence](docs/status.md) · [Architecture](docs/architecture.md) · [Versions and hashes](docs/versions.md)

This is a development system for one device/firmware baseline, not a general-purpose tablet installer. Vendor firmware, stock images, panel command data and touch plan data are not distributed. Prepare your own inputs using the linked guides.

## Before starting, you must already have

- An unlocked bootloader and your own stock Recovery image with an independently verified digest and rollback copy.
- A way to read your supported runtime FDT (ADB/root), and your own panel and memory inputs.
- Your matching AFE source and the seven private touch-plan inputs described in the [preparation guide](docs/touch-plan-preparation.md), if enabling touch.
- Firmware obtained by you from your own device, or appropriately licensed upstream replacements as documented in the firmware guide.

This is a development-source workflow, not an out-of-box installer for arbitrary tablets. APT has no frozen snapshot; rootfs output is not guaranteed identical across dates. U-Boot has twelve patches: the default menu has Ubuntu / HarmonyOS reboot / Power off; the optional fourth action is TWRP. That route requires matching TWRP already in `recovery_ramdisk`; this repository does not install it. Consult the U-Boot repository for payload provenance, restrictions and current device-evidence limits.

Historical identifiers in touch-plan fields have no external meaning; readers do not need to find corresponding internal material. Field-value changes are deferred to a separate post-release task, conditional on byte-identical generated plans and nt36523-thp.ko.

## Current baseline: 2026-11-02

Evidence: **H** = recorded device checks reviewed by the maintainer; **R** = user observation; **O** = offline validation; **U** = unverified or not implemented. The table concerns the rebuilt baseline, not the earlier development system. Details and historical results are in [status](docs/status.md).

| Area | Current result | Evidence |
|---|---|---|
| Boot and SD installation | Minimal test and full desktop cards boot through Recovery → U-Boot → Linux → Ubuntu | H, 2026-11-02 |
| Display and GPU | 120 Hz GNOME Wayland session with Adreno acceleration; brightness control observed | H + R, 2026-11-02; intermittent black screen remains unresolved |
| USB network / SSH | Root public-key login over the USB network | H, 2026-11-02 |
| Wi-Fi, DNS and time | Seed-created connection, DNS, HTTP access and time synchronization | H, 2026-11-02 |
| Wi-Fi 6 (HE) | A phone hotspot on 5 GHz (WPA2) negotiated an 80 MHz HE 2×2 link on one tablet; this is the link rate, not measured throughput. Firmware regulatory rules disable 2.4 GHz channels 12–14; the same hotspot on 2.4 GHz was not seen, cause unconfirmed | H + R, 2026-11-03 |
| Bluetooth | Controller comes up and is visible to BlueZ/GNOME with an owner-supplied device address in the DTB (one tablet, bundle 20261102-4, 2026-11-03); a Bluetooth mouse paired and worked (user report, 2026-11-03); reconnection after reboot/sleep, other device types and audio not tested on this baseline | H + R for controller; R for mouse pairing; U for the rest |
| Mouse | USB receiver mouse used on the full desktop | H + R, 2026-11-02; this is not Bluetooth pairing evidence |
| Desktop | GDM and GNOME desktop; first-setup session can hang | H + R, 2026-11-02 |
| Seed settings | SSH key, Wi-Fi, regulatory request, APT mirror, timezone and display-rate settings tested; extra packages have a network timing defect | H, 2026-11-02 |
| Safe mode | Display modules suppressed before system init; SSH/network remain reachable; removal restores normal loading | H, 2026-11-02; screen is intentionally black |
| Touch / stylus | Single touch, edges/four corners and multi-touch pinch work in the short recorded test with `guard=0`; stylus not implemented | H + R, 2026-11-02; three-finger gestures and long-term touch stability U |
| Audio / speakers | Automatic startup via `dby-w09-audio.service`; on one tablet, four consecutive cold boots of `20261102-17` gave three successes and one failure. The startup wait was affected by a wall-clock step (H, cause identified). Package `20261102-18` reads both waits from the monotonic uptime clock (O) and then passed six consecutive cold boots (H + R, 2026-11-08); six boots is too few to call startup reliable (R/U). Four-channel capture and four desktop speakers were validated earlier; no speaker protection algorithm.See [audio notes](docs/audio-capture.md). | Prior capture H + R, 2026-11-06; speakers H + R, 2026-11-07; autostart: `20261102-17` 3 of 4 boots, `20261102-18` 6 of 6 boots (H + R), long-term reliability R/U; safe-mode skip, mapping and long-term speaker use U |
| Power key / cover switch | Tap blanks the screen and the key wakes it; hold opens GNOME's shutdown dialog; closing the magnetic cover blanks the screen and opening wakes it (suspend is never requested; attaching the pen does not blank). One tablet, bundles `20261102-20`–`-24`: 50/50 off/on cycles, 11 cover cycles, no failures; screen-off draw about 0.84–0.91 W with Wi-Fi connected. Suspend, touch wake, login-screen behavior and long-term stability untested | H + R, 2026-11-08 |
| Device-package update / rollback | Same-version no-op, downgrade, upgrade and backup rollback; keeps p2 and seed | H + R, 2026-11-02; one SD / one tablet, bundles `20261102-2` / `20261102-3`, kernel `7.2.10` |
| Seed / safe-mode editor | Validated editing, private backup and restore; p1 only | H: wizard/timezone; H + R: safe-mode, derived-PSK write/read-back and restore; other settings O/U |
| Reinstall system | Reformat existing p2 only; preserve p1/seed; no data backup | H + R, full system, 2026-11-02/03 |
| Installer menu / data backup / restore | Not implemented | U |

Audio autostart (reliability R/U after three successes and one failure in four cold boots; `20261102-18` fix passed six cold boots, long-term reliability R/U; masking and unmasking tested earlier): `sudo systemctl mask dby-w09-audio.service` disables it after reboot; `sudo systemctl unmask dby-w09-audio.service` restores it after reboot. Safe-mode skip is implemented but not device-tested (U); failure requires reboot before retry. See [startup details](docs/audio-capture.md).

Known issues include intermittent black screen, a possible first GNOME setup hang, extra-package seed timing, and power use measured only in a few one-tablet runs plus untested long-term stability. Touch was checked only in a short session; intermittent startup failures and long-term behavior remain U. A successful session does not resolve these issues.

## Repository roles

| Repository | Responsibility |
|---|---|
| [dby-w09-linux](https://github.com/youzjuzi/dby-w09-linux) | This hub: out-of-tree drivers, bundle/rootfs builders, SD installer, runtime configuration and user guides |
| [dby-w09-kernel](https://github.com/youzjuzi/dby-w09-kernel) | Ten patches and configuration for Linux 7.2.10, board DTS, owner-data extraction and build recipes |
| [dby-w09-uboot](https://github.com/youzjuzi/dby-w09-uboot) | Twelve patches for U-Boot v2026.10, configuration, build script and stock-Recovery repacking tool |

## Build → bundle → card → boot → check

Follow the [installation guide](docs/install.md) in order:

1. Obtain pinned sources and your own [firmware/private inputs](docs/firmware-preparation.md); build U-Boot, kernel, DTB and modules.
2. Build p1 with the [bundle tools](tools/dby-bundle/README.md), build a test or full [rootfs](tools/dby-bundle/rootfs/README.md), then attach it to a new bundle.
3. Review the [SD installer's](tools/dby-install/README.md) dry-run plan, install to the whole card in a built-in MMC reader, and place your public key/seed on p1.
4. Repack your own stock Recovery image, flash only Recovery, insert the card and enter Recovery manually.
5. Check boot, services, display and networking. Record the evidence level and keep identifying data private.

The installer accepts whole `/dev/mmcblkN` SD cards and requires the complete device name as confirmation. `install` erases the entire card; `update`/`rollback` replace the p1 device package with host backup/recovery while keeping the system and seed (H + R within the scope above). `seed` and `safe-mode` provide validated editing and private seed backup/restore (H + R for the limited scope below; otherwise O/U), without mounting p2. USB `/dev/sdX` readers are not supported. Menu and user-data backup/restore commands remain future work. `reinstall-system` replaces only p2, preserving p1 and seeds (full system H + R); it destroys all system/user data without making a backup.

Seed passphrases are stored as plaintext by default, with two no-echo entries. Explicit `--derived-psk` is WPA2-only and unsuitable for WPA3/SAE or WPA2/WPA3 mixed networks. On 2026-11-02, `seed set` wrote a derived PSK and passed its own read-back check, and `seed restore` restored the original seed on one SD card / one tablet (H + R). On the one tested router, the correctly derived PSK could not connect; restoring the original plaintext passphrase connected immediately. WPA3/SAE or mixed-mode negotiation is a suspected cause, not verified on the router. On 2026-11-02/03, one SD card and one tablet with bundle `20261102-3` and kernel `7.2.10` provided these additional results: `seed wizard` rejected mismatched passphrases in dry-run without writing, and a real timezone-only edit showed only that change, passed read-back and created a backup (H). `seed set --timezone` was written and confirmed after boot with `timedatectl` for two timezones (H). A mistyped target confirmation was refused without writing (H). `safe-mode on/off` blocked then restored msm, panel and touch modules across boots; while active the backlight was lit without an image and USB networking/SSH worked (H + R). These results do not establish other devices or networks. Other `seed set` settings (mirror/rate/packages/replacement or added public keys) still need real-card retesting; derived-PSK connectivity on confirmed WPA2-only networks remains U (command logic O). The new wizard label-selection rules are tested offline (O), awaiting real-card retest.

Shut down cleanly before removing the card; an unreplayed p2 journal is refused with `E_INSTALL_P2_RECOVERY`. Use `verify --p1-only` after the system has been used. The 2026-11-02 owner retest covered `info`, `verify --p1-only` and dry-run on one SD card (R). `info --full`, journal diagnostics and injected abnormal paths remain O; normal-card retesting does not validate those paths. Real-card power-loss/interruption recovery and cross-kernel updates remain unverified; the bundle format currently accepts only `7.2.10`, and update does not change p2. FAT renames are not power-loss safe; see the [validated workflow and journal recovery](docs/install.md#61-update-the-device-package-without-reinstalling-h--r).

Full-system reinstall has H + R evidence on 2026-11-02/03, limited to one SD card, one tablet, bundle `20261102-3` and kernel `7.2.10`: preview/execution succeeded, independent whole-p1 SHA256 comparison matched, and subsequent full `verify` passed. The new system booted with old users/data gone and regenerated SSH host keys; the preserved plaintext Wi-Fi seed connected automatically and touch worked. Shared install formatting/extraction code was exercised on the card. First setup did not hang in this run; the intermittent defect remains unresolved. `--minimal`, `--no-readback` and mid-operation failures remain O only; real-card interruption/power loss and cross-kernel updates remain U. Menu and user-data backup/restore remain unimplemented (U).

## More information

- [Architecture and card contracts](docs/architecture.md)
- [Source inventory and external inputs](docs/source-scope.md)
- [Firmware inventory](docs/firmware.md) and [owner preparation](docs/firmware-preparation.md)
- [Touch plan reconstruction](docs/touch-plan-preparation.md) and [optional touch setup](docs/install.md#31-enable-touch-optional): prepare your own matching AFE input
- Driver notes: [backlight](drivers/rt8555-backlight/README.md), [read-only battery gauge](drivers/dby-fg-readonly/README.md), [charging management](drivers/dby-chg/README.md), [touch development snapshot](drivers/nt36523-thp/README.md)
- [Contributing](CONTRIBUTING.md), [security](SECURITY.md), [third-party materials](THIRD_PARTY.md)

## Related projects

- [CoWave-Fall/linux-on-arm-matepad11](https://github.com/CoWave-Fall/linux-on-arm-matepad11): an independent mainline Linux port for the same device (Arch Linux ARM + KDE Plasma). According to its documentation it covers areas this project does not yet (sensors, stylus, magnetic keyboard, suspend to RAM), while this project covers others (onboard audio, hardware video decoding, SD installer). The two projects are independent and share no code; this repository makes no statement about the accuracy of the other project's claims.

## Safety and limits

Evidence comes from one DBY-W09 with HarmonyOS 4.2.0.213 and an unlocked bootloader. Other units, firmware versions and input combinations are U. Keep a verified copy of your own stock Recovery image for rollback. The only intended internal-storage change during installation is Recovery; U-Boot has a narrowly checked exception to clear the Recovery command in `misc`. Linux runs from the card. Factory reset requires restoring stock Recovery first.

The display and touch share a TDDI controller. Do not substitute another tablet's touch firmware. Native charging works on one tablet (H + R, 2026-11-05): the charger driver autoloads, sustained charging and the desktop indicator were observed, but it has not run for days, so long-term risk is unknown. Natural FULL/recharge, suspend, long-term charging and multiple temperatures remain unverified; after a watchdog cutoff USB must be replugged. There is no fast charging; a PC USB port supplies roughly 500 mA and may not cover the load while the screen is on. See [charging notes](drivers/dby-chg/README.md) and [status](docs/status.md). Cameras and sensors are not done. Hardware video decoding (Venus, loaded manually) works for HEVC, VP9 and H.264 on one tablet (H, 2026-11-10), but H.264 hits a firmware error at end of stream or seek and recovers after about 3 s, and browsers cannot use it; see [status](docs/status.md).

This is an independent, unofficial device-porting project, not affiliated with, endorsed by or sponsored by Huawei, Canonical/Ubuntu, Qualcomm or TeamWin. Names and trademarks belong to their respective owners. Vendor firmware is not provided; other-device/firmware compatibility is not guaranteed.

An already unlocked bootloader is required. Flashing replaces Recovery, and the modified image does not match the original AVB digest. An incorrect image or operation can make the device unbootable or Recovery unusable. SD installation erases the selected card. Keep and verify your own stock rollback image, and restore stock Recovery before a factory reset. Software is provided as is, without warranty.

Unless a file states otherwise, source and documentation are GPL-2.0-only; see [LICENSE](LICENSE) and individual notices. A tool's licence does not grant redistribution rights to owner-extracted data.

Power-key tap/hold and cover (lid) screen blanking were validated on one tablet (H + R, 2026-11-08); suspend is neither used nor validated. See [power key, lid and rollback](docs/power-key.md).

## Current operating limits

- The speakers have no excursion or thermal protection algorithm. Gain limits are not speaker protection; prolonged loud playback may damage them. Audio autostart passed only six consecutive cold boots on one tablet (owner report R, 2026-11-08; maintainer-reviewed evidence in status); long-term reliability is unknown.
- `dby-chg` writes PMIC charger registers `0x1070`, `0x1643` and `0x1042`, affecting charging behavior. Natural FULL/recharge, suspend, multi-temperature calibration and long-term behavior remain U. There is no fast charging; a PC USB port can leave the battery discharging with the screen on.
- s2idle is unverified. When the power-key daemon stops and restores GNOME settings, the usual lid action is `suspend`; the exact restored value is the user's saved setting. See the power-key notes before stopping it.
- Bluetooth evidence covers the controller and one mouse only. A2DP and reconnection remain U; provide your own private `local-bd-address` during DTB generation.
- Venus requires manual `modprobe venus_core`. H.264 end-of-stream/seek can crash the firmware, with about 3 s recovery and lost tail frames; browsers cannot use this path. Firmware is an owner private input, not distributed. See the status page for the existing provenance limits.

This is an independent project with no affiliation to Huawei, HarmonyOS, MatePad, Qualcomm or Ubuntu; names remain their owners' trademarks. The detailed dates and evidence boundaries in the status page apply to each feature.

Speaker builds require owner-supplied private configuration tables; see [preparation and build instructions](drivers/dby-tfa9874/README.md). The source tree contains no original amplifier sequences; generated headers and modules containing the data are not for redistribution.

### GPIO vibrator candidate (O)

The device pack includes upstream `gpio-vibra.ko` and its dependency closure.
The matching board DT describes TLMM GPIO72; OF modalias loading exposes a
`gpio-vibrator` evdev device with FF_RUMBLE. Motor operation awaits device
validation (U). Idea from CoWave-Fall's linux-on-arm-matepad11; reimplemented, no code copied.
