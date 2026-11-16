# Onboard audio startup and validation

当前方案：内核按设备树 modalias 与延迟探测自动加载板载音频（O，实机待验证）。UCM2、WirePlumber 的 S16LE 规则、Mic 初始化参数和喇叭硬件档均不变。旧脚本方案的六次冷启动通过只属于旧包，不作为本方案成功证据。无喇叭保护算法，长期可靠性和物理麦克风映射仍 U。

The 44-entry onboard-audio blacklist and the two-stage `dby-audio-start` loader
are removed. The blacklist was a conservative bring-up fallback; no device
failure of autoload had been established. APR creates protected audio-service
devices after PDR reports the audio domain up. AFE then creates its clock
controller, and LPASS consumers defer while their providers are absent. All
44 modules have a device alias or symbol-dependency path, including dynamic
RPMsg/auxiliary devices; no extra modules-load list is needed (O).

Two oneshots retain the configuration work:

- `dby-w09-audio-config.service` mounts UCM2 and WirePlumber configuration
  read-only after local filesystems and before `display-manager.service`.
  It performs no module loading or ALSA initialization. Existing mount and
  nonempty-directory checks are retained; a nonempty WirePlumber directory
  is preserved with a warning.
- `dby-w09-audio-ucm.service` is requested by a `change` event for a sound
  `card*` whose `id` is `DBYW09`, with `SOUND_INITIALIZED=1` set by
  `78-sound-card.rules` after child-device processing. Card `add` precedes
  control/PCM registration and is too early. It requires and follows the config unit,
  selects `HiFi` and `Mic` in one `alsaucm` session, triggers sound change
  events, then refreshes already-running desktop WirePlumber sessions.
  It has no display-manager ordering gate, polling, sleeps or retries.

The UCM step remains because a desktop can open the card before Mic routing
is initialized. Removing it needs separate validation. Both units skip when
`/run/modprobe.d/dby-safe-mode.conf` exists; **safe mode does not blacklist
audio, so it does not prevent ADSP or audio modules from starting**.
去掉 blacklist 后，安全模式不再阻止音频模块和 ADSP 启动（旧方案会阻止）；本任务不扩大安全模式范围。
Logs remain in `/run/dby-audio-start.log`, plus
`journalctl -u dby-w09-audio-config.service -u dby-w09-audio-ucm.service`.
The UCM attempt marker still permits only one attempt per boot; a failed or
partial initialization requires reboot. No online module unload is provided.

This loading path is O only. Cold boots, hot reboots, login timing, sound,
USB-headset switching, and absence of AFE vote errors remain U. In particular,
an enabled service that pulls in `systemd-udev-settle` can still affect boot
timing if a driver stalls; asynchronous remoteproc boot is not a universal
bound on hardware waits.

The firmware is stored at `firmware/qcom/dby-w09/adsp.*` inside the package and exposed through the existing `/run/dby-device/firmware` firmware-class search path. It is not copied into `/lib/firmware`. Scripts in the package's `bin/` are executable at `/run/dby-device/bin/`; the package does not install a new `/usr/local/sbin` file.

Historical capture-only stage (superseded by the speaker integration below): package `20261102-11` first verified two-channel capture on the unmodified machine driver (`rms_dbfs` −33.63 / −50.62 on channels 1/2, channels 3/4 zero; H + R, 2026-11-06). Package `20261102-12` adds externally rebuilt `snd-soc-sm8250` with kernel patch `0008` (preserving the negotiated channel interval on `TX_CODEC_DMA_TX_3`), and a 12-second 48 kHz S16_LE 4-channel capture on one tablet (H + R, 2026-11-06) recorded real voice/tap signal simultaneously on all four channels (`rms_dbfs` −31.79 / −47.82 / −29.97 / −32.63); physical microphone-to-channel mapping, desktop UCM integration and speakers remain U. Owner-operated mixer/capture helpers are maintained separately and are not published with this repository.

耳机检测基础设施已初始化；3.5 mm 接口是否存在未验证。Specifically, when the codec/card probes, the unmodified codec probe configures headphone DAC gain/HD2, reference-buffer and surge-protection registers. Its MBHC initialization registers interrupts, and machine-driver jack setup starts detection including bias activity. This does not establish a physical headset interface or successful playback. No playback route, speaker amplifier, WSA macro, or user-operated headphone/ANC/HDR control is added. Do not describe this as an absence of headphone activity.

Validated on one tablet (2026-11-06, H + R): default boot left ADSP and onboard audio modules stopped, explicit `/run/dby-device/bin/dby-audio-start` brought ADSP/APR and `sm8250 - DBY-W09` up, all four analog microphone channels recorded real signal, and reboot left ADSP stopped again. If display, input, networking or headphone-related behavior changes, reboot and restore the prior validated package.

## Four-microphone capture (H + R, 2026-11-06)

Package `20261102-12` contains the externally rebuilt `snd-soc-sm8250`
module with kernel patch `0008`. It removes the forced two-channel
override only for `TX_CODEC_DMA_TX_3`, allowing standard ASoC channel
negotiation; 48 kHz/S16_LE and other backends are unchanged. Image, DTB,
the other 104 modules, firmware, startup script and modprobe rules are
unchanged from `20261102-11` (O). On one tablet (H + R, 2026-11-06),
explicit startup and a 12-second 48 kHz S16_LE 4-channel capture
recorded real simultaneous signal across channels 1–4 (`rms_dbfs`
−31.79 / −47.82 / −29.97 / −32.63), and reboot restored the stopped
default. Desktop UCM/PipeWire integration and speakers remain U.

## Desktop capture UCM2 integration (H + R, 2026-11-06)

Package `20261102-13` adds capture-only UCM2 for DBY-W09. Explicit
`dby-audio-start` installs a read-only runtime bind mount over the existing
`/usr/share/alsa/ucm2/conf.d/sm8250` directory before the capture card loads.
It selects HiFi and Mic in one `alsaucm -c hw:DBYW09` session, requests a
sound-device change event, and restarts WirePlumber for active desktop sessions
so PipeWire discovers the 4-channel `Internal Microphone` source after all
28 capture controls are set. No playback configuration is added.

The destination directory must already exist. Missing UCM files, destination
or alsaucm leave the original capture-card startup available without claiming
desktop initialization. Mount or UCM initialization failure requires reboot.
A pre-existing mount is reused. No files are installed into the root filesystem;
the runtime mount disappears on reboot. This older package used opt-in startup;
the current boot service is described above and below.

Host parser tests use a virtual card with a fixture-only CardId of 0;
the shipped configuration keeps `hw:${CardId},0`.

## Hardware card lookup and desktop reconnect (H + R, 2026-11-06)

Startup uses `alsaucm -c hw:DBYW09 set _verb HiFi set _enadev Mic` so
ALSA resolves the hardware card and its driver/long-name UCM configuration.
After routing and the sound change event, it restarts WirePlumber for each
resolved user with an existing `pipewire-0` path under `/run/user`.
The existence check also accepts an ordinary file; it does not prove a live session.
This lets a logged-in session retry capture after an earlier card-registration
race. Restart failure is nonfatal; ready still indicates capture-card startup,
not successful desktop reconnection. Restarting WirePlumber may briefly
interrupt that user's existing audio session, including USB audio.

Validated on one tablet (H + R, 2026-11-06): running `/run/dby-device/bin/dby-audio-start`
from a clean boot mounted the read-only UCM2 directory, initialized `HiFi`/`Mic`,
and automatically exposed the 4-channel `Internal Microphone` source
(`alsa_input.platform-sound.HiFi__Mic__source`, `capture_FL/FR/RL/RR`) in
PipeWire and GNOME Settings without manual commands; GNOME Sound Recorder
recorded clean audio, USB-C digital headset switching coexisted cleanly, and
reboot removed the runtime mount and returned to the stopped default. Physical
microphone-to-channel mapping and speakers remain U.

## A3a bounded speaker bring-up (O; device validation U)

At the historical A3a stage, Stage B explicitly loaded `dby-tfa9874` before the machine driver; the new
module is blacklisted at boot. The package has 106 modules. UCM2 HiFi remains
unchanged and exposes only the microphone, so this stage adds no desktop Speaker.
The kernel-side TDM transport patches and four matching external modules must
travel together in the bundle.

The amplifier's card-level Safe Enable Switch defaults off, Attenuation Step 15,
Input Atten 6dB on, and Active Mask 15. The allowed step range is 9..15; both
initialization and playback use the minimum gain by default. Mask bits select
chips 0..3. No proprietary thermal/excursion protection is present. An I2C failure
revokes the arm and attempts every chip's shutdown sequence; failed I2C cannot
prove that physical shutdown occurred.

The owner's private helper adds `spk-status`, `spk-route-off` and
`spk-test-tone [--duration 1..5] [--mask 1..15] [--step 9..15]`. Defaults are two
seconds, all speakers, minimum gain, plus 6 dB input attenuation. The stereo test
is 48 kHz S16_LE, 440 Hz, -24 dBFS with 50 ms fades. EXIT/INT/TERM clean up both
safe enable and the route; emergency route-off does not wait for the tone lock.
A stale shared tone file is refused rather than overwritten. Capture helpers
retain their existing hold and route transactions.

These are host-tested controls only (O). Probe, clocks, sound quality, temperature,
interrupt behavior and preservation of four-channel capture are U until the owner
tests the device. This stage does not claim working speakers or add A3b desktop
integration. See the [driver notes](../drivers/dby-tfa9874/README.md).

## A3b desktop playback and capture separation (O)

The A3b candidate assigns Speaker to MultiMedia1 (`hw:${CardId},0`, stereo) and
Mic to MultiMedia2 (`hw:${CardId},1`, four channels). This supersedes A3a's
microphone-only UCM policy. The three UCM files remain
present; both master files and the analog microphone sequences are unchanged.

HiFi Verb enable first locks the speaker off, selects step 9, disables the extra
6 dB input attenuation, selects all four chips, and installs both independent FE
routes before PCM probing. Speaker enable reasserts step 9 and mask 15 before
arming; disable disarms. Verb disable disarms and removes both routes. This avoids
sharing q6routing's single frontend port slot between capture and playback.
The existing audio startup script and its Mic initialization behavior are unchanged.

The driver accepts steps 9..15 but still initializes at 15; UCM defaults to 9.
Steps 7 and 8 are rejected. The bounded diagnostic tone still
defaults to 15 and now accepts 9..15. No protection algorithm was added and no
fixed ODM initialization register or TDM format was changed. Host tests and the
virtual ALSA parser are O only: the virtual fixture substitutes CardId with zero;
real-card expansion, simultaneous desktop recording/playback, volume, temperature,
stop-to-power-down behavior and USB audio switching require device validation.

## Speaker S16 format and step 9 policy (H + R, 2026-11-07)

The device package includes `wireplumber/wireplumber.conf.d/60-dby-speaker-s16.conf`.
It matches only `alsa_output.platform-sound.HiFi__Speaker__sink` and sets only
`audio.format = "S16LE"`. On this device, the measured S24_LE frontend path was
about 48 dB quieter (H); this observation does not establish the ADSP's internal
format interpretation. The rule adds no volume, priority, gain or microphone
properties. TDM remains S16_LE in 32-bit slots, and no kernel format change or
software loudness chain is included.

After the UCM2 mount and before desktop WirePlumber restart,
`dby-w09-audio-config` binds the packaged rule directory read-only at
`/etc/wireplumber/wireplumber.conf.d`. If absent, it creates this empty directory
and any missing parent with mode 0755. **Creating this empty directory tree is
the only persistent root-filesystem change added by this feature.** Configuration
files stay in the device image; the bind mount disappears at reboot. This is
separate from the existing UCM2 destination, which must already exist.

A missing packaged rule directory skips this step. An existing mount is reused.
An existing nonempty directory produces a warning and is left visible: startup
continues, possibly retaining the quieter speaker format, rather than hiding
owner configuration. Bind or read-only remount failure stops startup and requires
reboot. The config service retains these checks.

Both UCM speaker enable sequences select step 9. The amplifier control and core
sequence builder accept only 9..15; driver initialization remains step 15 and
`0x61=0x03fc`. There is **no speaker excursion or thermal protection algorithm**.
This restriction does not establish safe acoustic output.

On one tablet with package `20261102-16` (H + R, 2026-11-07): the default boot
loaded no onboard audio; explicit `dby-audio-start` completed without warnings and
mounted both the UCM2 and WirePlumber directories read-only; desktop playback used
`S16_LE`; the step control read 9 and a write of 8 did not take effect. The owner
reported clearly louder playback, with 30% volume already loud and no distortion
at 60%. During 60 seconds of music at the owner's listening level, all four
amplifiers stayed at 26–34 °C with no fault or clip flags, and powered down a few
seconds after playback stopped. GNOME Sound Recorder captured clearly and USB-C
headset switching worked. After reboot, no audio started and no mount remained
(only the empty directory); a second explicit startup behaved the same. Long-term
playback, high-volume endurance and speaker protection remain U.

## Historical scripted startup (`20261102-17`: 3 of 4 boots; `20261102-18`: 6 of 6 boots; long-term reliability R/U)

`dby-w09-audio.service` is a oneshot wanted by `multi-user.target`, ordered after
local filesystems, udev and module loading. It has no ordering dependency on GDM
or the display manager. It can delay `multi-user.target` (and the final
`graphical.target` activation), but does not add an ordering gate to GDM startup.
On the tested tablet, GDM started about 6.4 s after boot while the audio service ran from about 4.8 s to 10.1 s, so the login screen was not delayed. The 90-second overall startup budget allows
two 15-second readiness waits plus 60 seconds for normal module loading and setup;
it is not the sum of every module's individual timeout. The service never retries.
Failure or timeout requires reboot before another attempt; do not unload modules
or restart the script in the same boot. Logs: `journalctl -u dby-w09-audio.service`
and `/run/dby-audio-start.log`.

The old service skipped startup in safe mode and supported service masking.
Those controls applied to the old scripted loader only. Use the current
instructions below for kernel autoload.

Earlier observations (H + R, one tablet, package `20261102-17`, 2026-11-08): the first
three of four consecutive cold boots each brought up the sound card, both read-only mounts and
the desktop `Internal Speakers` / `Internal Microphone` with no manual command;
the service exited 0 with a clean log and the kernel log showed no amplifier,
q6 or ADSP errors. Playback used S16_LE at 60% without distortion. A manual second
run was refused with "already attempted" and left the sound devices intact.
After `systemctl mask` and a reboot no audio started and the desktop was normal;
after `unmask` and a reboot audio returned. Safe-mode skip is not device-tested
(U), and the evidence is limited to short sessions on one tablet. It does not
establish long-term reliability or provide a speaker protection algorithm.

The fourth cold boot failed with `APR readiness timeout; stage B skipped` before
15 seconds had elapsed. The cause was identified as the startup wait reacting to
a wall-clock step during early boot (H, 2026-11-08). The ADSP/APR services became
ready just after the premature timeout. The four-boot record is three successes
and one failure; it does not establish reliable automatic startup (R/U).

Package `20261102-18` reads both 15-second readiness deadlines from `/proc/uptime`
(integer centiseconds; a missing or malformed value fails startup and never falls
back to wall-clock time). Each wait is still 15 seconds with 0.2-second polling;
the service unit, 90-second overall budget, per-module timeouts, module order,
one-attempt rule and no-unload policy are unchanged, and no RTC-service ordering
or automatic retry was added. On the same tablet it then passed six consecutive
cold boots (H + R, 2026-11-08): each brought up the sound card, both read-only
mounts and the desktop `Internal Speakers` / `Internal Microphone`, in a boot
window where the RTC service also steps the clock. Six boots (of a defect that
showed once in four) is limited evidence, so long-term reliability stays R/U. If
the sound card is missing after boot, reboot once; do not rerun the script in the
same boot.

## Reading the stage history / 历史阶段读法

Sections naming packages `20261102-11` through `-18` describe those historical stages only. Their “speakers remain U”, capture-only and opt-in startup statements do not describe the later S16LE desktop speaker/autostart combination. The current kernel-autoload path is described at the top and remains device-unverified; the six cold boots used the historical scripted loader. Prolonged loud playback may damage the unprotected speakers; gain limits do not replace a protection algorithm.

`-11` 至 `-18` 描述历史阶段；当前内核自动加载方案见页首，实机尚未验证。旧方案六次冷启动证据不能沿用，长期可靠性未知。高音量长时间播放可能损伤无保护算法的喇叭，限增益不等于保护。

## Disable or restore kernel autoload / 关闭与恢复

To skip only configuration, the owner can run the following on the tablet and
reboot. This does **not** stop module loading, codec probe or ADSP boot:

```sh
sudo systemctl mask dby-w09-audio-config.service dby-w09-audio-ucm.service
```

To prevent automatic onboard-audio loading on the next boot, also create
`/etc/modprobe.d/90-dby-audio-disabled.conf` with the following complete contents.
This intentionally restores the former broad blacklist, including shared
support modules; USB audio core modules remain available through dependencies.
Blacklists control alias loading and do not prohibit an explicit manual
modprobe. Do not run the old loader or manually load audio modules while disabled.

```conf
blacklist apr
blacklist pdr_interface
blacklist pinctrl_lpass_lpi
blacklist pinctrl_sm8250_lpass_lpi
blacklist q6adm
blacklist q6afe
blacklist q6afe_clocks
blacklist q6afe_dai
blacklist q6asm
blacklist q6asm_dai
blacklist q6core
blacklist q6routing
blacklist qcom_common
blacklist qcom_glink_smem
blacklist qcom_pd_mapper
blacklist qcom_pdr_msg
blacklist qcom_pil_info
blacklist qcom_q6v5
blacklist qcom_q6v5_pas
blacklist qcom_sysmon
blacklist regmap_sdw
blacklist slimbus
blacklist snd_compress
blacklist snd_pcm_dmaengine
blacklist snd_q6dsp_common
blacklist snd_soc_core
blacklist snd_soc_lpass_macro_common
blacklist snd_soc_lpass_rx_macro
blacklist snd_soc_lpass_tx_macro
blacklist snd_soc_lpass_va_macro
blacklist snd_soc_qcom_common
blacklist snd_soc_qcom_offload_utils
blacklist snd_soc_qcom_sdw
blacklist snd_soc_sm8250
blacklist snd_soc_wcd938x
blacklist snd_soc_wcd938x_sdw
blacklist snd_soc_wcd_classh
blacklist snd_soc_wcd_common
blacklist snd_soc_wcd_mbhc
blacklist soc_usb
blacklist soundwire_bus
blacklist soundwire_qcom
blacklist qrtr_smd
blacklist dby_tfa9874
```

Reboot; masking or creating this file does not unload currently active modules.
To restore, remove only this owner-created file, run
`sudo systemctl unmask dby-w09-audio-config.service dby-w09-audio-ucm.service`,
and reboot. The persistent `/etc/systemd/system` masks override runtime units.
Do not use the former `dby-w09-audio.service` mask for the new package: that unit
has been removed. These current disable/restore instructions are O, device U.
If the candidate fails, restore the prior validated device package from the
host with the tablet shut down; no device action is performed by the agent.
