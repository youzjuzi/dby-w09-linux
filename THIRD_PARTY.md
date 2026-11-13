# Third-party components / 第三方组件

Language: English details with a Chinese summary below. / 英文细目，文末中文摘要。

| Component | Version | Licence | Use |
|---|---|---|---|
| Linux kernel | 7.2.10 | GPL-2.0-only; file-specific exceptions | Operating system kernel (`dby-w09-kernel`) |
| U-Boot | v2026.10 | GPL-2.0-or-later | First-stage boot (`dby-w09-uboot`) |
| Ubuntu ARM64 packages | 26.04 / desktop ISO 26.04.1 | Various, per package | Root filesystem; not redistributed here |
| linux-firmware | Public comparison: tag 20261019, commit a2dcb924f0897368b0b4f305cbf9808cdda90ce0; original download commit not recorded | Non-GPL, file-specific restricted redistribution terms | Four matching files listed below; no firmware distributed here |

This inventory records the verified comparison scope and the unresolved items below. It does not establish complete licence clearance. Source inclusion and successful device operation do not establish redistribution rights for omitted inputs.

## Added source snapshots

- NT36523 THP module and its local helpers: project development sources, GPL-2.0-only. The touchscreen host helper retains its upstream Linux copyright and SPDX notice. Fixed-plan generator and validator are included; vendor firmware, JSON plan inputs and generated plan table are excluded pending provenance/licence review.
- [Bundle builder](tools/dby-bundle/README.md) and [SD installer](tools/dby-install/README.md): project sources, including initramfs, runtime/rootfs overlays, services and tests. They invoke external system tools and may consume vendor firmware and Ubuntu packages; those binaries are not included here. Test credentials/addresses are synthetic fixtures.
- Source inclusion does not certify the omitted private inputs' redistribution rights or a working combined system. Unresolved provenance and permissions are explicitly listed below; documentation cleanup does not close those gaps.

## Owner-data policy

Firmware, panel command arrays, decompiled board snapshots, identity data and touch plan inputs are not distributed. Owner extraction instructions do not grant redistribution rights. Upstream-derived code retains its original notices and is not claimed as project-original work.

## Touch plan reconstruction tools

`prepare-plan-inputs.py` and `plan-tools/` contain project-authored offline reconstruction logic (GPL-2.0-only), adapted from the protocol audit and bounded-read plan validator. Protocol constants and evidence addresses reflect analysis of the exact owner-supplied AFE version; vendor code, firmware bytes and disassembly are not embedded. The historical `nt36523-format.h` retains its SPDX notice and is a hash-checked parser reference only; its older control table is not used by the current driver. Private outputs require their own redistribution assessment; the tools' licence is not a licence grant for vendor data. See [the preparation guide](docs/touch-plan-preparation.md).

## 中文摘要

本表记录源码、上游与许可边界，不提供厂商固件或原厂镜像。Ubuntu rootfs 配方、bundle 构建器和 SD 安装器见各自 README 与[安装指南](docs/install.zh-CN.md)；新基线触摸已于 2026-11-02 在 guard=0 下通过短时实机检查（H + R），后续四麦／四喇叭已有单机 H + R 记录，自启长期可靠性仍为 R/U，见[状态页](docs/status.md)。离线工具与历史观察不能替代实机验收。面板、内存、触摸计划和固件均由所有者准备，工具许可证不授予这些数据的再分发权。linux-firmware 四文件的固定公开对照版本已记录，但最初下载提交未保存；蓝牙版本／许可、所有者数据授权与部分历史来源仍未核实，不能因某个组合启动成功而视为许可已核清。

## Fixed public firmware comparison (O)

The original download commit was not retained. The earlier audit compared four owner inputs byte-for-byte with the official linux-firmware tag `20261019`, commit `a2dcb924f0897368b0b4f305cbf9808cdda90ce0`. This documentation pass rechecked the local inputs against the four public comparison hashes below and all matched (O). This identifies a matching public reference, not the original acquisition commit. No owner input contents are published.

| Public file | WHENCE version | Public SHA-256 | Licence records |
|---|---|---|---|
| `ath11k/QCA6390/hw2.0/amss.bin` | WLAN.HST.1.0.1-05266-QCAHSTSWPLZ_V2_TO_X86-1 | `8ab2a06422e3989012df423bb6a156fde2c3feb4cd5db4c5d3588f674dc872ff` | LICENSE.QualcommAtheros_ath10k; accompanying Notice.txt |
| `ath11k/QCA6390/hw2.0/m3.bin` | Same WHENCE firmware group as amss.bin | `1921d25edb52f90a7a0acc7275861e14399f904d579aae9f7ab1c4d28533fad6` | LICENSE.QualcommAtheros_ath10k; accompanying Notice.txt |
| `qcom/a650_sqe.fw` | 1.12 | `c863ceac3a823af215205d7803776bfdc68098c2c2a5d2fd05b4aa77cd336b4b` | LICENSE.qcom + NOTICE.qcom |
| `qcom/a650_gmu.bin` | 2.1.8 | `df79fa8482fcc7587bb239b9c9e2b24bce4729893b990fe3f82b30da8175b01c` | LICENSE.qcom + NOTICE.qcom |

Reference: [WHENCE at the fixed commit](https://kernel.googlesource.com/pub/scm/linux/kernel/git/firmware/linux-firmware/+/a2dcb924f0897368b0b4f305cbf9808cdda90ce0/WHENCE). These are non-GPL licences with restricted redistribution terms, including applicable hardware and notice conditions; they are not covered by this repository's GPL. This project does not distribute firmware.

Bluetooth `htbtfw20.tlv`, `htnv20.bin` and related parameters were not included in that comparison: version and licence records remain to be supplied (U). Do not infer their provenance from the four files above. / 蓝牙上述文件不在四文件对照范围，版本与许可记录待补（U），不得猜测。

## Touch helper attribution and project tools

`drivers/nt36523-thp/touchscreen-host-api.h` adapts Linux `drivers/input/touchscreen.c` for host tests, retaining GPL-2.0-only and Copyright (c) 2014 Sebastian Reichel. Linux 7.2.10 is the locally checked source comparison version, not proof of the original import revision. After omitting upstream includes, export declarations and module metadata, the helper function bodies match the local Linux 7.2.10 file. Kernel includes/export plumbing is adapted for the host harness. The axis inversion/swap projection in `input-core.h` also identifies that upstream helper; it follows the same GPL-2.0-only attribution. These helpers are not claimed as wholly project-original code.

The other THP helpers and fixed-plan tools are project development sources under their file notices. `thp-plan.py` is the offline symbolic-plan validator; the module Makefile is project build glue. Their added GPL-2.0-only headers match this component. Exact historical authorship/import revision and a complete third-party derivation audit for those two files have not been independently established; no new third-party source claim is made. The protocol/AFE analysis boundary described above still applies.

Ubuntu packages carry their individual licences and copyright records; consult each package's `/usr/share/doc/<package>/copyright` and source package. The top-level GPL does not replace these terms. APT is not frozen to a snapshot, so rootfs dependency versions and bytes can change across dates.

## Unresolved scope

- Original linux-firmware acquisition commit is missing; the fixed public match does not recover it.
- Bluetooth version/licence records remain U; owner board/calibration data, ZAP, panel/memory data and AFE-derived firmware/plan outputs have no blanket redistribution clearance here.
- Complete historical THP input/derivation attribution, including exact helper import revision and the two files noted above, remains unverified.
- Ubuntu package licences remain per-package; a recipe does not certify every future package set.

These gaps remain open after this documentation change. No statement of complete licence clearance is made.

## TFA9874 board bring-up

The driver and extraction tool are project GPL-2.0-only code. The current source
tree does not include original amplifier register sequences. The public tool
reads the owner's own original audio amplifier configuration files, checks fixed
normalized fingerprints, and generates a private build header. Gain limits,
TDM routing and Safe Enable policy remain project code. See
[preparing private tables](drivers/dby-tfa9874/README.md#preparing-configuration-tables--准备配置表私有输入).

Owner data is not distributed. This boundary does not establish complete
provenance or redistribution rights for the inputs, generated header, or compiled
modules containing them. The tool grants none of those rights. Original XML,
proprietary algorithms and firmware are not included. There is no speaker
protection algorithm.

驱动与生成工具为项目 GPL-2.0-only 代码，当前源码树不含原厂功放寄存器序列。
公开工具从所有者自己的原厂音频功放配置文件生成私有构建头，并固定核对规范化摘要；
增益限制、槽位和安全开关规则仍为项目代码。所有者数据不分发，不代表输入、生成头
或含这些数据的模块来源及再分发许可已经核清，工具不授予相关权利。原始 XML、
专有算法与固件均不包含。没有喇叭保护算法。

## Incremental integration inventory / 增量集成清单

- `drivers/dby-chg`: project charger policy, thermistor model, host tests and Linux API glue with GPL-2.0-only notices. Register/board parameters were developed from stock/downstream hardware descriptions and project observations/controlled experiments. Exact historical derivation remains incompletely audited; a register address is not a licence grant for a source file. The driver writes three charger register addresses and the standard ADC provider has its own operations; see its README for the bounded write surface and unverified cases.
- `dby-w09-powerkey`, its service, exact-device uaccess rule and logind drop-in: project GPL-2.0-only integration using standard input, GNOME/GIO and systemd APIs. No original stock FDT is distributed. Hall GPIO/supply provenance is recorded in the kernel repository.
- The three DBY-W09 UCM2 files and the S16LE WirePlumber rule are board integration configuration under this repository's default licence unless another notice applies; they have no individual SPDX or copyright header. Local comparison with the installed alsa-ucm-conf SM8250/RB5 files found no identical file, only common UCM syntax/structure. That does not establish independent authorship or the original import revision. Any actual upstream derivation still requires its applicable licence and notices; complete historical attribution remains U. This inventory does not relabel upstream material as project-original work.
- Venus video decoding: the repository contains the board DTS nodes, the module list and a blacklist rule; it contains no firmware. The tested firmware was extracted by the owner from the owner's own stock modem image. It is a private build input and is not distributed; its version, provenance and redistribution terms have not been independently cleared. / Venus 视频解码：仓库只含板级 DTS 节点、模块清单与黑名单规则，不含固件。实测固件由所有者从自有原厂 modem 镜像提取，是不分发的私有构建输入，版本、来源与再分发条款未独立核清。
- Onboard ADSP firmware remains owner-supplied and excluded. The four-file public firmware comparison above does not cover ADSP or board-specific inputs. Working audio does not establish redistribution rights.

充电逻辑、温度模型及测试沿用各文件 GPL-2.0-only，参数来自原厂／下游描述与项目观测／受控实验，完整历史推导仍未核清。电源键守护进程、精确 uaccess 规则和 logind 集成为项目 GPL-2.0-only 标准接口适配，霍尔来源见内核仓库。三份 UCM2 与 S16LE 规则没有逐文件声明；本地 SM8250／RB5 对照未发现整文件相同，仅有标准语法／结构相似，不能由此证明原创或精确导入版本，实际派生材料仍须保留相应许可与署名。ADSP 固件由所有者提供，不包含在四文件公开固件对照中；出声不证明固件再分发许可。
