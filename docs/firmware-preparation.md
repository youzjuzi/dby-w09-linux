# 固件提取与准备 / Firmware extraction and preparation

语言：简体中文详细教程；English summary below. Full English installation workflow: [install.md](install.md). [中文安装入口](install.zh-CN.md)。

本教程帮助设备所有者准备 DBY-W09 设备包所需的固件和私有配置：取得文件、校验内容，再整理成构建工具要求的目录。教程和源码可公开，提取出的原厂文件、身份配置及日志保留在自己的私有工作区。

## 适用范围与使用须知

- **适用基线：** DBY-W09、HarmonyOS 4.2.0.213 的既有记录。其他版本的路径、内容和摘要可能不同，不能直接套用触摸固件的固定偏移。
- **验证情况：** 配方仅经过离线文档、语法和合成文件检查，未按本教程重新连接设备提取，也未验证所得组合的运行效果。
- **操作责任：** 请先理解命令的作用，确认文件来源和现有读取权限，再自行决定是否执行并承担相应操作风险。本教程按现状提供，不保证适用于其他设备或版本，也不保证构建、启动或运行成功。
- **使用与保管：** 提取文件不等于取得再分发许可，本教程不授予任何固件或原厂库的再分发权利。请分别核实来源与使用条件，妥善保管固件、校准数据、设备身份信息和日志。

本文设备命令仅作为所有者手动读取的示例。操作前应已具备所需权限；权限不足时停止，不通过修改设备文件或权限继续。也可以从已有原厂文件副本准备输入。

## 阅读顺序

1. [准备条件](#1-准备条件)与[文件清单](#2-要准备哪些文件)：确认所需材料。
2. [读取或复制文件](#3-从自己的设备读取或从本地原厂副本复制)：选择适合自己的来源路线。
3. [提取触摸固件](#4-从匹配-afe-库离线提取触摸固件)与[准备 Wi-Fi 身份配置](#5-本机-wi-fi-身份输入)。
4. [生成私有目录与锁](#6-生成设备包要求的私有目录与锁)，最后检查[剩余依赖](#7-准备完固件以后还缺什么)。

## 1. 准备条件

- 使用自己的设备或已经合法取得、解包好的原厂文件副本。
- 走设备读取路线时，事先具备 ADB 和所需文件的读取权限。本文不包含解锁、提权、刷机或修改设备权限的操作。
- 主机具备 Python 3、ADB（仅设备读取路线需要）、`sha256sum`。
- 以下提取示例在主仓库根目录的被忽略 local/ 中操作，用全新的工作目录，已有文件不要覆盖。也可在公开仓库外准备等价目录并在 bundle 配置中显式指定；示例目录不是所有者真实路径，不要强制添加私有内容。

本文所有设备命令都只是供所有者手动执行的读取示例；不启动、重启、挂载或写入平板，不加载所提取的程序。也可以完全不用 ADB，从已有只读原厂文件副本准备相同输入。

## 2. 要准备哪些文件

“原厂位置”来自此固件版本的历史记录，不是针对任意版本的自动识别规则。公开固件应从官方 linux-firmware 项目或发行版相应固件包获得，记录所选版本、包名/提交与许可证；文件名相同不代表版本相同。

| 功能 | 来源或原厂位置 | 主机暂存名 | 设备包内的相对路径 |
|---|---|---|---|
| Wi-Fi 主固件 | 公开固件的 ath11k/QCA6390/hw2.0/amss.bin | amss.bin | firmware/ath11k/QCA6390/hw2.0/amss.bin |
| Wi-Fi M3 | 与 amss 配套的 m3.bin | m3.bin | firmware/ath11k/QCA6390/hw2.0/m3.bin |
| 本机 Wi-Fi 板级数据 | /vendor/etc/wifi/dby_fem_lx9_bdwlan.bin | board.bin | firmware/ath11k/QCA6390/hw2.0/board.bin |
| 蓝牙主固件 | /vendor/bt_firmware/image/htbtfw20.tlv | htbtfw20.tlv | firmware/qca/htbtfw20.tlv |
| 本机蓝牙参数 | /vendor/bt_firmware/image/dby_fem_htnv20.bin | htnv20.bin | firmware/qca/htnv20.bin |
| GPU SQE | 公开固件的 qcom/a650_sqe.fw | a650_sqe.fw | firmware/qcom/a650_sqe.fw |
| GPU GMU | 公开固件的 qcom/a650_gmu.bin | a650_gmu.bin | firmware/qcom/a650_gmu.bin |
| 本机 GPU ZAP | /vendor/firmware/a650_zap.elf | a650_zap.elf | firmware/qcom/sm8250/huawei/dby-w09/a650_zap.elf |
| THP 触摸 | 匹配的 vendor/lib64/AFE 库内嵌数据，见第4节 | nt36523-thp.bin | firmware/novatek/nt36523-thp.bin |

不要把同目录的 5g/vi 板级文件、其他型号的蓝牙参数或签名 GPU 固件当作替代。此设备包使用 board.bin；不能把任意 board-2.bin 直接改名使用。原厂 modem 内的 amss20.bin 也不等于已经验证适用于当前 ath11k 的 amss.bin。原厂 SQE 曾不满足此内核的版本要求，不能简单用整套原厂 GPU 固件替换公开版本。

音频 DSP 与视频编解码（SM8250 由 `venus` 驱动）不在当前构建器的默认固件清单内，本文不提供其部署流程。视频硬件解码已在一台设备上用所有者自己提取的固件验证（见[状态页](status.md)），固件来源与再分发许可门禁未关闭。触摸下载计划所需的 JSON 也不是固件文件，见第7节。

## 3. 从自己的设备读取，或从本地原厂副本复制

以下命令均从主仓库 `dby-w09-linux` 根目录执行。先确认 `local/private/firmware-raw` 尚不存在，再创建暂存目录；这里只写主机文件。若目录已存在，请先保留旧资料并准备独立的工作副本，不要直接重复运行后面的 `adb pull` 或重定向命令，以免覆盖已有文件：

```sh
umask 077
mkdir -p local/private/firmware-raw
```

### 路线 A：已有原厂文件副本

按上表位置复制所需普通文件到暂存目录，并记录原始路径、大小和 SHA-256。符号链接要核实其实际目标，不直接把链接放入固件锁目录。保留来源记录与许可证在暂存目录中。

### 路线 B：从自己的设备读取

设备读取示例：

```sh
adb pull /vendor/etc/wifi/dby_fem_lx9_bdwlan.bin local/private/firmware-raw/board.bin
adb pull /vendor/bt_firmware/image/dby_fem_htnv20.bin local/private/firmware-raw/htnv20.bin
adb pull /vendor/firmware/a650_zap.elf local/private/firmware-raw/a650_zap.elf
```

#### 已有 root 读取权限时的传输示例

若文件无法由普通 ADB 读取，但所有者已有 root 读取权限，可用以下示例在设备上编码后传输。不要先复制到设备 /data 或改权限：

```sh
adb exec-out su -c 'base64 /vendor/bt_firmware/image/htbtfw20.tlv' > local/private/firmware-raw/htbtfw20.tlv.b64
adb exec-out su -c 'sha256sum /vendor/bt_firmware/image/htbtfw20.tlv' > local/private/firmware-raw/htbtfw20.device-sha256.txt
python3 -B -I - <<'PY'
import base64
import hashlib
from pathlib import Path
raw = Path('local/private/firmware-raw')
encoded = b''.join((raw / 'htbtfw20.tlv.b64').read_bytes().split())
data = base64.b64decode(encoded, validate=True)
expected = (raw / 'htbtfw20.device-sha256.txt').read_text().split()[0]
if hashlib.sha256(data).hexdigest() != expected:
    raise SystemExit('传输摘要不一致，停止使用此副本')
with (raw / 'htbtfw20.tlv').open('xb') as stream:
    stream.write(data)
PY
```

本机历史采集中，直接用 adb exec-out su -c cat 传二进制曾出现 LF 变 CRLF 的损坏。二进制优先用 adb pull，或使用上面的 base64 路线，并核对设备端与主机端的摘要。命令报错、额外文字、文件缺失或摘要不同都应停下来核实，不修补字节、不改锁伪装成成功。

公开固件也放在 firmware-raw/，使用表中的暂存名。保留其原始版本与许可记录；本教程不自动下载或选择最新版本。

## 4. 从匹配 AFE 库离线提取触摸固件

触摸固件不是整个 .so，也不是 TSA 算法库。先从自己的原厂 vendor/lib64/ 获取与本机项目匹配的 AFE 库，在主机暂存为 firmware-raw/matching-afe.so。不要运行、加载或 import 这个库。

下面仅适用于整文件 SHA-256 为 `4d3e05edc9a69769e1af20e1560ff88458229898191d90f33bd90d6eaff17bb0`、大小 624352 字节的历史输入。离线记录定位到 NT36523_THP_BIN 对象，文件偏移 `0x2a5d4`、长度 `0x30000`。先核对整库，再取固定区间，最后核对固件摘要：

```sh
python3 -B -I - <<'PY'
import hashlib
from pathlib import Path
raw = Path('local/private/firmware-raw')
data = (raw / 'matching-afe.so').read_bytes()
parent = '4d3e05edc9a69769e1af20e1560ff88458229898191d90f33bd90d6eaff17bb0'
expected = 'b7633e1ef2a271348d2142950e6235238bb4997b98b010bae0df6bc4a4af5b6b'
if len(data) != 624352 or hashlib.sha256(data).hexdigest() != parent:
    raise SystemExit('AFE库不匹配；不能套用此偏移')
firmware = data[0x2a5d4:0x2a5d4 + 0x30000]
if len(firmware) != 196608 or hashlib.sha256(firmware).hexdigest() != expected:
    raise SystemExit('提取结果不匹配')
with (raw / 'nt36523-thp.bin').open('xb') as stream:
    stream.write(firmware)
PY
```

库摘要不同时，本教程没有该版本的提取配方；不要只搜索 NVT 字符串截取，也不要修改预期摘要跳过检查。这一步只提取主机文件，不给触摸芯片下载固件。

## 5. 本机 Wi-Fi 身份输入

若需要保留自己的 Wi-Fi 地址，可选地准备身份配置。它不是固件，也不应向别人索要或公开。把自己的 Wi-Fi 地址写入暂存目录的 own-wifi.link；下面只是格式，必须替换占位符。不需要覆盖地址时，省略此文件即可；新 bundle 构建器不要求固定身份输入。

```ini
[Match]
Driver=ath11k_pci

[Link]
MACAddress=YOUR_OWN_WIFI_MAC
```

地址取自自己的原厂系统记录，不用蓝牙地址、USB gadget 示例地址或其他人的配置。不要把 Wi-Fi 名称、密码、国家代码、设备序列号或私钥放进这个文件。

## 6. 生成设备包要求的私有目录与锁

确认每份文件来源和传输摘要后，再生成锁。锁用于发现后续变化，不证明文件可信或硬件兼容；不能用“本地算出一个哈希”替代来源核对。

下面示例准备无线、GPU 和触摸三组输入，暂存目录应具备上表全部 9 份固件；own-wifi.link 为可选项。目标三个目录必须不存在；所有目标都在主机 local/private/ 下，未涉及设备。配方会创建文件工作副本并设定权限，不改源文件。目录名仅为示例，由 bundle 配置的 `private_inputs` 显式选择；关闭触摸时可省略触摸组及其输入。

```sh
python3 -B -I - <<'PY'
import hashlib
import json
import os
from pathlib import Path
os.umask(0o077)
base = Path('local/private')
raw = base / 'firmware-raw'
groups = {
    'radio': {
        'firmware/ath11k/QCA6390/hw2.0/amss.bin': 'amss.bin',
        'firmware/ath11k/QCA6390/hw2.0/m3.bin': 'm3.bin',
        'firmware/ath11k/QCA6390/hw2.0/board.bin': 'board.bin',
        'firmware/qca/htbtfw20.tlv': 'htbtfw20.tlv',
        'firmware/qca/htnv20.bin': 'htnv20.bin',
    },
    'gpu': {
        'firmware/qcom/a650_sqe.fw': 'a650_sqe.fw',
        'firmware/qcom/a650_gmu.bin': 'a650_gmu.bin',
        'firmware/qcom/sm8250/huawei/dby-w09/a650_zap.elf': 'a650_zap.elf',
    },
    'touch': {
        'firmware/novatek/nt36523-thp.bin': 'nt36523-thp.bin',
    },
}
if (raw / 'own-wifi.link').exists():
    if 'YOUR_OWN_WIFI_MAC' in (raw / 'own-wifi.link').read_text():
        raise SystemExit('Wi-Fi地址占位符尚未替换')
    groups['radio']['rootfs/etc/systemd/network/10-wlan-mac.link'] = 'own-wifi.link'
for group, entries in groups.items():
    destination = base / group
    if destination.exists() or destination.is_symlink():
        raise SystemExit('目标已存在，请使用全新的私有工作区')
    for source in entries.values():
        p = raw / source
        if p.is_symlink() or not p.is_file() or p.stat().st_size == 0:
            raise SystemExit('缺失、链接或空文件：' + source)
for group, entries in groups.items():
    destination = base / group
    destination.mkdir(mode=0o700)
    lock = {'schema': 1, 'files': {}}
    for target, source in entries.items():
        data = (raw / source).read_bytes()
        p = destination / target
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open('xb') as stream:
            stream.write(data)
        p.chmod(0o644)
        lock['files'][target] = {
            'sha256': hashlib.sha256(data).hexdigest(), 'mode': 420,
        }
    p = destination / 'inputs.json'
    with p.open('x') as stream:
        stream.write(json.dumps(lock, indent=2) + '\n')
    p.chmod(0o600)
PY
```

其中 `420` 是 JSON 中的十进制权限值，等于八进制 `0644`。每个目录的 inputs.json 描述该目录里的相对文件名、SHA-256和权限。不要在锁目录内放许可证、日志、AFE原库、备份或额外文件；这些保存在 firmware-raw/ 或其他私有目录。构建器会检查清单、摘要与权限，GPU和触摸路径还拒绝多余目录/文件及链接。

将这些目录加入 [bundle 配置](../tools/dby-bundle/README.md)的 `private_inputs` 数组，路径相对 bundle.json 所在目录解析。可参照 [bundle.example.json](../tools/dby-bundle/examples/bundle.example.json)替换路径；没有目录名默认值。已有的锁定目录也可显式选择，只要内容布局、清单、摘要和权限符合要求，无需重命名或覆盖它们。

新构建器直接读取锁定的输入目录，不提供从旧设备包 seed 提取私有输入的流程。安装器在卡上创建的 `dby-seed/` 用于启动配置，`seed.conf` 与 `authorized_keys` 的用法见 [bundle seed 说明](../tools/dby-bundle/README.md#boot-seed-applier)和 [SD 安装器](../tools/dby-install/README.md)；它不替代固件输入与 `inputs.json`。

## 7. 准备完固件以后还缺什么

- 触摸编译仍需要私有计划JSON及生成的 thp-plan-table.h；提取固件不会自动产生它们；继续按[触摸计划准备教程](touch-plan-preparation.md)用完整匹配 AFE 库离线生成，再查看[触摸源码说明](../drivers/nt36523-thp/README.md)。
- 新基线已用匹配输入构建 guard=0 触摸包，并于 2026-11-02 通过短时设备／交互检查（H + R）；其他输入、三指手势、笔、长期表现及 guard 功能仍未验证，音频后续单机证据见状态页，本文不提供完整 ADSP 准备流程。
- 仍需匹配内核、模块、DTB、Ubuntu根系统和工具要求的输入。固件目录齐全不表示整个系统可构建或启动。
- 不要把这些目录、原厂库、Wi-Fi身份文件、哈希对应日志或生成表作为 GitHub Release 附件。公开内容应是教程、源码、格式说明与经核实可公开的来源信息。

完成后应得到以下三个私有目录，每个目录都有自己的 `inputs.json`：

```text
local/private/
├── radio/
├── gpu/
└── touch/
```

暂存文件与来源记录继续保留在 `local/private/firmware-raw/`。接下来阅读 [bundle 工具说明](../tools/dby-bundle/README.md)，核对其余输入；完成本教程仅表示私有材料已整理，不表示设备包已经构建或通过实机验证。

## 面板命令表同样由所有者准备

内核仓库的 `docs/panel-data.md` 与 `tools/extract-panel-data.py` 说明如何从自己的原厂运行时 DTB 生成私有面板头文件。这个表不随补丁分发，不能用固件目录代替；原厂 DTB 也不能直接作为主线 Linux 的板级 DTB。反编译设备树快照不再公开提供；内核仓库 `dts/README.md` 另有独立板级源码和私有内存提取／编译教程。提取配方为 O；2026-11-02 匹配输入组合的启动为 H，其他输入仍为 U，见[状态页](status.md)。

## English summary

This detailed extraction guide is primarily Chinese. It documents an **O** owner-run read-only procedure for the recorded stock baseline; extraction was not rerun for the documentation. Obtain appropriately licensed upstream files and your own stock board/BT/ZAP data, verify transfer hashes, and use only the exact supported AFE digest for the fixed touch slice. Never run vendor libraries or substitute another device's firmware.

The recipe creates locked radio/GPU/touch directories with schema-1 `inputs.json`; the directory is 0700, payload modes match the lock and extra files/links are rejected. The Wi-Fi identity override is optional. Select directories explicitly in `private_inputs`, with paths relative to your bundle configuration. Existing directory names are not defaults or compatibility evidence. Startup `dby-seed/` is configuration, not a firmware extraction mechanism. A matching guard=0 touch bundle passed a short 2026-11-02 device/interaction check (H + R); other inputs, three-finger gestures, stylus, long-term behavior and guard remain unverified, and later audio device evidence is in status; this guide does not prepare the complete ADSP set. See [status](status.md) and [optional touch setup](install.md#31-enable-touch-optional).

## Speaker configuration tables / 喇叭配置表

Speaker builds also require the owner's original audio amplifier configuration files, separate from firmware. Generate a private header following the [TFA9874 instructions](../drivers/dby-tfa9874/README.md); the public tool pins normalized fingerprints and refuses unsupported inputs. Keep derived headers and modules private. The tool grants no rights to the data.

喇叭模块除固件外，还需要所有者自己设备上的原厂音频功放配置文件。按上面链接生成私有头文件，工具固定校验规范化摘要、不支持的输入一律拒绝。生成头和模块保持私有；工具不授予这些数据的任何权利。
