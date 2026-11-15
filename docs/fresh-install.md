# Fresh rebuild and a separate card / 从零重建与新卡安装

2026-11-15, R4. The full bundle `20261115-1` passed host verification (O), with the owner-completed new full rootfs; rebuild evidence is recorded in [versions](versions.md); new-card installation and first boot remain **Pending** until the owner returns results. There is still no independent third-party blank-card reproduction. Existing private inputs are reused; their extraction is not revalidated by this rebuild. / 本轮复用所有者已有私有输入，提取流程不计为从零复现；新卡与首启待用户操作，不能把离线构建写成 H。

## Host inputs / 主机输入

Use clean clones of all three repositories at the full commits recorded in the bundle manifest. Check each local main against the public remote before cloning. Extract a new kernel source tree from the archive; do not reuse an extracted upstream tree, compiled kernel or module. Keep all output outside the checkouts. Follow the [kernel build recipe](https://github.com/youzjuzi/dby-w09-kernel/blob/main/docs/build.md) and [install guide](install.md).

Downloads can be reused. From the workspace directory, verify these exact names and versions before use; stop on any mismatch:

```sh
cd downloads
sha256sum -c <<'SUMS'
2feaa70453e91f541be6a5c9078d8168e081af45a3d9718054850538cff2ebf3  linux-7.2.10.tar.xz
f943a88389c8fd70a6b0d061a88267465e143c7ca6ea2b75837ab20f5b60c739  u-boot-2026.10.tar.gz
b2b46a37324ea1954e93f293fe6d7c2241daf2fc298c4022e6e4caceeed74cab  ubuntu-base-26.04-base-arm64.tar.gz
c54d196489d3c867975fb3bbb72ca52ec2e137456e305481f81096304e4d2517  ubuntu-26.04.1-desktop-arm64.iso
SUMS
python3 -I - <<'PY'
from pathlib import Path
import hashlib
for listing, name in [
    ('linux-kernel-v7.x-sha256sums.asc', 'linux-7.2.10.tar.xz'),
    ('ubuntu-base-26.04-SHA256SUMS', 'ubuntu-base-26.04-base-arm64.tar.gz'),
]:
    rows = [line.split() for line in Path(listing).read_text().splitlines()]
    hashes = [r[0] for r in rows if len(r) == 2 and r[1].lstrip('*') == name]
    with Path(name).open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if hashes != [actual]:
        raise SystemExit('checksum-list mismatch: ' + name)
    print(name + ': checksum-list PASS')
PY
cd ..
```

The checksum script requires Python 3.11+. `linux-kernel-v7.x-sha256sums.asc` is the locally named upstream `sha256sums.asc`; `ubuntu-base-26.04-SHA256SUMS` is the Base release's `SHA256SUMS`. Source links and pinned values are in [versions](versions.md). U-Boot's GitHub tag archive is locally named `u-boot-2026.10.tar.gz` (its upstream URL ends in `v2026.10.tar.gz`). R4 checks the U-Boot and ISO repository pins; their separate upstream checksum/signature files are not present in the reused downloads. Comparing a clear-signed checksum entry is **not** OpenPGP signature verification. No signing-key provenance or signature authentication result is claimed here. / 文件摘要与校验清单均需核对，但摘要匹配不等于来源签名认证。

Reuse the owner's matching panel header, current memory include (including the Venus reservation), Bluetooth address, touch plan header, private TFA tables and locked radio/GPU/touch/ADSP/Venus firmware directories. Record their digests privately. Do not put private files or absolute private paths in a public report. If inputs do not exist, use the corresponding public preparation guides and retain their original evidence limits. An old memory include without the Venus reservation is not the current input. / 已有输入可以复用；不要为本轮重复从设备提取，也不要拿旧版内存布局冒充当前输入。

Apply all **12** kernel patches, 0001–0012, with zero fuzz. Retain the `olddefconfig` result: the documented `PCI_PWRCTRL_PWRSEQ=y` to `m` change is expected. Build Image and all modules, then all five external drivers against that same build. Generate the board DTB using the matching private memory include and `--bd-address-file`. The [bundle example](../tools/dby-bundle/examples/bundle.example.json) requires real paths and source commits; it is not ready to run unchanged. Include the charger/TFA mappings and locked ADSP/Venus inputs as well as radio/GPU/touch. Keep `ff-memless.ko` and `gpio-vibra.ko` in the selected in-tree modules. Shared-SDAM RTC is reverted; time uses the existing p2 offset services.

Existing working Recovery/U-Boot is retained for this SD-only rebuild. Cloning its source and checking its archive does not mean rebuilding or flashing it. Skip the Recovery packing/flashing steps when retaining that working boot path. Rootfs is a new full build from the checked ISO; the owner executes the root command in [install §4](install.md#4-build-test-or-full-rootfs), then the rootfs is attached and the resulting full bundle verified before writing a card. APT is not frozen, so retain the actual package inventory and do not expect cross-date byte identity.

## Owner card preparation / 用户写卡

Use a **different 16 GB card**. Preserve the existing working card unchanged for rollback; this procedure does not require a whole-card backup. The selected new card will be completely erased. Commands below are owner-only, on the host.

1. Shut down the tablet normally, remove the working card and keep it away from the writer. Before inserting the new card, run `lsblk -o NAME,PATH,SIZE,TYPE,MODEL,TRAN,MOUNTPOINTS`. Insert only the new card, rerun the same command and identify the newly appeared whole-disk row. A marketed 16 GB card normally shows about 14.9 GiB; capacity alone is insufficient—compare appearance/disappearance, model/transport and parent/partition rows. Never select the host system disk or a partition.
2. Set `CARD` to the verified **whole device**, then inspect it again. If the new device is not unambiguous, stop. Unmount any automatically mounted partitions of that card before installation; recheck after each reconnect because device names can change.

```sh
# Replace only after the before/after lsblk comparison. This placeholder must fail.
CARD=/dev/REPLACE_WITH_CONFIRMED_NEW_CARD
lsblk -b -o NAME,PATH,SIZE,TYPE,MODEL,TRAN,MOUNTPOINTS "$CARD"
BUNDLE=/path/to/verified/bundle-full
INSTALLER=/path/to/dby-w09-linux/tools/dby-install/dby-install
python3 -I -B "$INSTALLER" install --bundle "$BUNDLE" --device "$CARD" --dry-run
sudo python3 -I -B "$INSTALLER" install --bundle "$BUNDLE" --device "$CARD"
sudo python3 -I -B "$INSTALLER" verify --bundle "$BUNDLE" --device "$CARD"
sudo python3 -I -B "$INSTALLER" info --device "$CARD"
```

Read the displayed target/capacity and type its full device name only if it is the new card. Full p2 uses the remaining space; use full read-back before first boot. Do not use `--minimal`, `update` or `reinstall-system` for this blank card. A 16 GB capacity is the selected target, not proof of sufficient free space: the final rootfs/install size checks must also pass.

Install the owner's public key and Wi-Fi seed using the [seed instructions](install.md#7-public-key-and-seed). The wizard can add a private network interactively without putting the password into shell history:

```sh
sudo python3 -I -B "$INSTALLER" seed set --device "$CARD" \
  --backup-dir /path/to/private/seed-backups --authorized-keys /path/to/own-key.pub
sudo python3 -I -B "$INSTALLER" seed wizard --device "$CARD" \
  --backup-dir /path/to/private/seed-backups
```

Use the correct private timezone/network settings; keep seed values and logs private. Finish/unmount cleanly, insert the new card with the tablet off, manually enter the existing Recovery/U-Boot and choose Ubuntu. Complete the first-user wizard. To roll back, shut down normally and reinsert the preserved working card.

## One-page first-boot checklist / 一页首启清单

Record PASS/FAIL/Pending, date, bundle ID and H/R/O/U for each row. The owner performs interactions; the reviewer may inspect read-only SSH output. Loading modules, decoding, recording or changing settings are owner actions. Keep raw network/identity output private. No row is pre-marked successful.

| Check / 检查 | Owner action and read-only evidence / 用户操作及只读证据 |
|---|---|
| Boot/desktop / 启动桌面 | New user wizard completes; GNOME desktop appears; `uname -r` is 7.2.10; inspect bundle identity. |
| Wi-Fi seed / 自动联网 | Connects without re-entering credentials; inspect seed/NetworkManager journals privately; check DNS separately. |
| Touch/120 Hz / 触摸刷新率 | Touch corners/edges and short gestures; confirm 120 Hz in Settings; no stylus/long-term claim. |
| Screen/brightness / 开关屏亮度 | 10 screen off/on cycles, **at least 3 s between operations**, brightness low/high and recovery; record any black screen/stripe. Rapid toggles still have known stripes; do not broaden the test. |
| Audio / 音频自启 | Confirm `dby-w09-audio.service` starts without manual restart; low-volume speaker playback and microphone recording; USB-C headset if available. See [audio checks](audio-capture.md). |
| Bluetooth / 蓝牙 | Controller appears; pair/use an available device, otherwise mark that interaction Pending. Do not publish its address. |
| Power/cover / 电源键合盖 | One normal power-key screen cycle and cover close/open; confirm display resumes; not a suspend test. |
| Charging / 充电 | Observe charging state and battery level; inspect charger service/driver status; no register writes by reviewer. |
| Vibration / 震动 | Owner uses the existing short FF_RUMBLE test; inspect gpio-vibrator/ff-memless presence. |
| Time / 时间 | Compare wall clock before network sync and after sync with a known clock; inspect `timedatectl` and p2 RTC-offset service status. A fresh p2 has no prior saved offset: record the first offline time, then check saved-offset behavior after a normal synced boot. Never interpret the reverted SDAM value as HarmonyOS time. |
| Venus / 视频 | Owner manually loads `venus_core` and performs one decode using the short Venus procedure below; reviewer inspects resulting output. If no test stream/tool exists, Pending, not PASS. |
| Health / 服务 | `systemctl --failed --no-pager` shows **0 failed units**; inspect boot warnings and record exceptions. |

DP3's earlier one-device result improved normal-paced screen cycling; rapid-toggle stripes and long-term reliability remain unresolved. Earlier VR1 vibration/RTC-rollback observations belong to the old card. New-card results and third-party reproduction remain separate evidence.

### One short Venus decode / 用户单次 Venus 解码

Run on the tablet as the owner after checking the new desktop. This is a proposed R4 check, not an executed result. It uses a short synthetic VP9 profile-0 stream and avoids the known H.264 end-of-stream defect. If the required GStreamer elements are missing, record Pending and resolve tool installation separately; do not substitute a software decoder and report hardware success.

```sh
sudo modprobe venus_core
gst-inspect-1.0 v4l2vp9dec
gst-inspect-1.0 vp9enc
VIDEO=$(mktemp --suffix=.webm)
gst-launch-1.0 -e videotestsrc num-buffers=60 ! \
  video/x-raw,format=I420,width=1280,height=720,framerate=30/1 ! \
  vp9enc deadline=1 ! webmmux ! filesink location="$VIDEO"
gst-launch-1.0 -e filesrc location="$VIDEO" ! matroskademux ! \
  v4l2vp9dec ! fakesink sync=false
rm -- "$VIDEO"
```

Record the decoder pipeline's exit status/EOS and inspect the kernel log for Venus, firmware and IOMMU errors. Module loading or a video node alone does not prove decoding. This does not test browser integration, long playback or suspend.
