<!-- SPDX-License-Identifier: GPL-2.0-only -->

# 构建与安装

[English](install.md) · [首页](../README.zh-CN.md) · [体系结构](architecture.md) · [状态](status.md)

本指南登记 **2026-11-15 R4 维护者从空白 16 GB 新卡照公开文档全新安装通过（H + R），包 `20261115-1`**。用户提供的结果区分 Claude SSH 核对（H）与用户交互观察（R），逐项范围见[状态页](status.md#r4-fresh-rebuild--r4-从零重建)。本轮使用已核对的公开 main 干净克隆、重新解压内核与已有所有者私有输入；输入提取未重做、保持原证据级别，U-Boot/Recovery 保持现状。第三方从空白卡独立复现仍为 **U**。下载校验和另一张 16 GB 卡的操作清单见[新卡手册](fresh-install.md)。写卡和 sudo 步骤仅由所有者执行。

## 1. 输入与工作目录

使用 Linux 构建主机。交叉构建需要 `aarch64-linux-gnu-` GCC/binutils（记录版本为 15.2）、原生构建工具、Python 3、kmod 和 squashfs-tools。rootfs 实跑需要 root、tar/zstd、mount 工具、systemctl，以及原生 ARM64 或带 F 标志的 qemu-aarch64 binfmt；完整 rootfs 另需 7z／unsquashfs。安装依赖见[安装器说明](../tools/dby-install/README.md)。

建议在 Git 仓库外准备以下布局：

```text
workspace/
  dby-w09-linux/  dby-w09-kernel/  dby-w09-uboot/
  downloads/     # 锁定的上游压缩包、Ubuntu ISO/base
  inputs/        # 自己的固件、面板/内存输入与原厂镜像
  config/        # 自己的 bundle.json 和 seed.conf
  build/         # 分开的源码、编译和产物目录
```

除特别说明外，下面从 `workspace/` 执行；先创建 `build/`、`config/`。工具输出目录必须尚不存在、父目录已存在。所有者数据、bundle、镜像和构建日志放在公开仓库外。运行前替换 `/path/to/...` 占位路径。

从各上游发布方取得 Linux 7.2.10、U-Boot v2026.10、Ubuntu 26.04.1 ARM64 桌面 ISO 和／或 Ubuntu Base 26.04 ARM64。使用前按[版本页](versions.md)核对 SHA-256；rootfs 锁定值见 [sources.json](../tools/dby-bundle/rootfs/sources.json)。摘要不同就停止，不通过改锁继续。

按[固件教程](firmware-preparation.md)准备自己的文件与锁。在内核仓库按[面板提取](https://github.com/youzjuzi/dby-w09-kernel/blob/main/docs/panel-data.md)和[内存布局提取](https://github.com/youzjuzi/dby-w09-kernel/blob/main/dts/README.md)准备私有输入。工具要求匹配的原厂输入，不授予数据再分发权。关闭和启用触摸的组合都有实机记录；启用还需所有者自备精确匹配的 AFE 库并完成下述流程。历史无音频配方不准备 ADSP 固件；当前板载音频需要另行准备所有者锁定输入，见本文末节。

## 2. 构建 U-Boot、内核、DTB 与模块

For an SD-only rebuild with a working Recovery/U-Boot, retain it and skip the U-Boot build and Recovery flash steps. R4 follows this scope. / 已有可用引导链时，新卡重建保留 Recovery/U-Boot，不重编或重刷。

在新目录中调用 U-Boot 仓库的锁定构建：

```sh
(cd dby-w09-uboot && JOBS=8 sh build.sh \
  ../downloads/u-boot-2026.10.tar.gz ../build/uboot-src ../build/uboot-out)
```

脚本校验上游摘要，以 fuzz=0 应用十三个补丁，生成 `build/uboot-out/u-boot.bin`。依赖与原厂镜像重打包说明见该仓库 [English](https://github.com/youzjuzi/dby-w09-uboot/blob/main/README.md)／[中文](https://github.com/youzjuzi/dby-w09-uboot/blob/main/README.zh-CN.md)。

内核按完整的[锁定配方](https://github.com/youzjuzi/dby-w09-kernel/blob/main/docs/build.zh-CN.md)（[English](https://github.com/youzjuzi/dby-w09-kernel/blob/main/docs/build.md)）操作：校验压缩包、解包到新目录、fuzz=0 应用十八个补丁、复制自己生成的面板头文件，再用独立 `O=` 目录执行 `olddefconfig` 和 `Image modules`。保留最终配置与 release。`PCI_PWRCTRL_PWRSEQ` 会成为模块，bundle 的模块列表已包含它；不要强行修改配置或摘要门禁来掩盖差异。

独立构建设备树，内存 include 由所有者生成：

```sh
python3 -I -B dby-w09-kernel/tools/build-board-dtb.py \
  --kernel /path/to/linux-7.2.10 \
  --memory /path/to/own-memory-include.dtsi --output build/dtb
```

将 `rt8555-backlight`、`dby-fg-readonly` 复制到独立模块目录，用同一对内核源码／构建树执行 `make M=`；内核配方有完整命令。触摸先按[离线流程](touch-plan-preparation.md)准备七项计划输入，再按[可选接入步骤](#31-启用触摸可选)构建。所得 guard=0 组合有短时 H + R 记录，不证明其他输入或长时间表现。

U-Boot 默认菜单为 Ubuntu／回鸿蒙／关机三项；可选 TWRP 载荷增加第四项，要求 recovery_ramdisk 中已存在匹配 TWRP，本仓库不安装它。载荷来源、二进制发布限制及构建／设备证据范围见 U-Boot 仓库。

## 3. 构建设备 bundle（p1）

```sh
cp dby-w09-linux/tools/dby-bundle/examples/bundle.example.json config/bundle.json
# 先编辑 config/bundle.json，再运行构建器。
python3 -I -B dby-w09-linux/tools/dby-bundle/build-bundle --config config/bundle.json
python3 -I -B dby-w09-linux/tools/dby-bundle/dby-bundle verify build/bundle-p1
```

入口是 `build-bundle` 文件，不是 `dby-bundle build-bundle` 子命令。相对 `config/` 修改示例的**全部**路径：`kernel_source_dir`、`kernel_build_dir`（实际 `O=` 目录）、`dtb`、`external_modules`、`private_inputs`、`output`。上例的输出填 `../build/bundle-p1`。`sources.repositories` 填实际构建的完整提交，不能保留零值，也不能填[版本页](versions.md)的待定占位符。记录上游摘要、epoch、创建时间与 sequence。

运行构建器前选择一种组合：关闭触摸时设置 `touch.enabled=false`，省略触摸模块映射及固件目录，只列出实际使用的无线／GPU 锁；启用时先按下一小节准备模块与输入，再设为 `touch.enabled=true`。历史无音频配方不准备 ADSP 固件；当前板载音频需要另行准备所有者锁定输入，见本文末节。目录名自由指定，每个所选目录均须通过清单、摘要与权限检查；关闭功能不免除显式输入的校验。契约见 [bundle 说明](../tools/dby-bundle/README.md)。

产物含 `manifest.json` 和 `p1/UBOOT/` 的五个启动文件。此时仅有 p1，是中间 bundle；安装还需要选定的 rootfs 归档。

### 3.1 启用触摸（可选）

先按[触摸计划教程](touch-plan-preparation.md)使用所有者自己的、受支持的精确 AFE 库，确认 `VERIFIED_OFFLINE`、七项 PINS 均匹配并生成 `thp-plan-table.h`；仅有固件 BIN 不够。教程说明私有的 Git 忽略输出根，可将它指向仓库外的私有存储。不使用手写表或其他平板固件，借用的小米 elish/nabu 固件禁止加载。

从工作区把公开模块源码／构建文件和生成头文件复制到新目录。三个输入路径分别替换为构建 Image 所用的同一对目标内核源码／构建树，以及自己的计划输出目录：

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

在首次构建前将下列内容合进 `config/bundle.json`，保留背光／电量计映射、无线／GPU 私有目录及其他必需字段。路径相对 `config/`：

```json
{
  "touch": {"enabled": true},
  "external_modules": {
    "kernel/drivers/input/touchscreen/nt36523-thp.ko": "../build/nt36523-thp/nt36523-thp.ko"
  },
  "private_inputs": ["../inputs/touch-firmware"]
}
```

按[固件教程](firmware-preparation.md#4-从匹配-afe-库离线提取触摸固件)准备权限 0700 的锁定输入目录 `inputs/touch-firmware/`，包含 `inputs.json` 和 `firmware/novatek/nt36523-thp.bin`；载荷权限必须与锁一致。上面的模块目的路径位于设备镜像 `modules/7.2.10/` 下。所选 `modules_list` 须含该模块路径，附带清单已经包含。

构建器检查 vermagic `7.2.10 SMP preempt mod_unload aarch64`、精确的[十个参数名称／类型](../tools/dby-bundle/README.md)以及 OF／SPI 别名 `of:N*T*Cnovatek,nt36523-thp`／`spi:nt36523-thp`，缺项或多项均拒绝。公开源码默认 `guard=1`，该手掌短触点抑制功能未通过验收；设备包固定 `guard=0`，按读码判断应保留较早的上报行为（O），但没有单独实机等价验证。保留设备包选项：

```text
options nt36523_thp managed=1 settle_ms=100 skip_probe=1 edge_px=50 filter=1 lock_px=12 confirm=1 multi=1 guard=0
```

公开板级源码已包含 16 MHz 的 `novatek,nt36523-thp` 节点，无 reset／regulator 属性；这个触摸配置不需改 DTB 或内核。完成配置后运行上方 p1 构建／verify 命令；若已有 bundle，则另选新输出并递增 sequence，不覆盖旧结果，再继续 rootfs。上机后的[检查只读](#触摸缺失)，触摸与显示共用同一控制器。

## 4. 构建测试或完整 rootfs

可先给下列命令加 `--dry-run` 并去掉 `sudo` 查看计划；成功计划也要求输入归档已存在、摘要正确。实跑只在构建主机使用 root，需要 APT 网络和临时空间（既有流程预留约 15 GB）。耗时和空间是观察值，不是保证上限。

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

test 从 Ubuntu Base 安装 init、SSH 和网络组件，不含 GNOME；full 从锁定 ISO 的 minimal 层裁剪并应用桌面覆盖层。产物含 `rootfs.json`、`packages.txt` 和对应的 `rootfs-test.tar.zst`／`rootfs.tar.zst`。构建和清理成功后，如需交回主机账户，可运行 `sudo chown -R "$(id -u):$(id -g)" build/rootfs-test`（完整系统改为 `build/rootfs-full`），仅操作刚完成的输出目录。

若持有匹配的 1492 包参考清单，将 `--no-baseline` 替换为 `--baseline-packages /path/to/verified-baseline-packages.txt`，摘要／数量必须符合 [sources.json](../tools/dby-bundle/rootfs/sources.json)，两参数互斥。没有参考清单时，显式 `--no-baseline` 记录本次实际清单作为新基线；两者均省略也允许构建。旧私有清单不是新所有者的前置条件。

`--mirror` 仅影响构建传输，打包前恢复官方源。既有 base 构建使用 HTTP，因为初始 base 缺少 CA 证书，HTTPS 可能在安装证书前失败。APT 尚未固定仓库快照；manifest 记录实际结果，不能承诺跨日期生成逐字节相同的 rootfs。详见 [rootfs 说明](../tools/dby-bundle/rootfs/README.md)。

## 5. 将 rootfs 附到新 bundle

目前没有独立 attach 子命令，使用既有 Python `attach_rootfs` 函数：

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

仅完整系统时，三个路径改成 `build/bundle-p1`、`build/rootfs-full`、`build/bundle-full`。同时保留两种 rootfs 时，把 full 附到 `build/bundle-test`。输出必须是新目录；函数核对归档与 `rootfs.json`，保留另一种 rootfs，重写并验证 manifest。下文 `--minimal` 选择测试／双归档 bundle，普通安装选择完整／双归档 bundle。

## 6. 写卡（所有者操作）

**会清空所选整张卡。** 仅使用内置读卡口整盘 `/dev/mmcblkN` SD 设备（8 GiB–2 TiB、512 字节扇区）。USB `/dev/sdX`、eMMC、分区节点、只读／系统盘／已挂载目标均被拒绝。有些 MMC 读卡口显示 removable=0，判断以 SD 类型和其他安全检查为准。

先用 `lsblk` 确认卡并卸载其已挂载分区。将 `/dev/mmcblk0` 替换为实际整卡，先审阅 dry-run：

```sh
python3 -I -B dby-w09-linux/tools/dby-install/dby-install install \
  --bundle build/bundle-full --device /dev/mmcblk0 --dry-run
sudo python3 -I -B dby-w09-linux/tools/dby-install/dby-install install \
  --bundle build/bundle-full --device /dev/mmcblk0
```

最小测试卡改用测试／双归档 bundle，并给两条命令加 `--minimal`；默认 p2 为 4 GiB，余下未分配，可用 `--p2-size 4G` 指定受支持的测试分区大小。完整系统 p2 用尽卡剩余空间。终端显示目标和计划，必须手输**完整设备名**确认。没有自动 `--yes` 或菜单；设备包更新／回滚使用下方独立命令，用户数据备份仍未实现。

可选 `install --no-readback` 只跳过卸载后 p2 的逐文件回读，写入时检查、p1 全回读和 p2 元数据／契约检查仍执行；它不等于 p2 完整验证。记录中的完整桌面安装使用了该选项（H）；以后可单独补全量 `verify`：

```sh
sudo python3 -I -B dby-w09-linux/tools/dby-install/dby-install verify --bundle build/bundle-full --device /dev/mmcblk0
sudo python3 -I -B dby-w09-linux/tools/dby-install/dby-install info --device /dev/mmcblk0
```

`verify` 自动判断 rootfs 类型，不加 `--minimal` 或 `--no-readback`。完整 verify 适合刚装好、尚未开机的卡；系统用过后 p2 文件会变化，改用下方 `verify --p1-only`。默认 `info` 是精简元数据与种子存在性摘要，`info --full` 保留原完整 manifest 输出；两者都不输出种子内容。

### 6.1 更新设备包（不重装，H + R）

`update`／`rollback` 已在 **2026-11-02、一张 SD 卡、一台平板、两个 bundle
`20261102-2`／`20261102-3`、相同内核 `7.2.10`** 的范围内通过实测（H + R）。
更新只接受本工具安装的卡：分区表、标签、根系统契约符合要求，卡自己的 manifest
与 UBOOT 五文件逐项一致。只换五文件及 p1 的 manifest，保留 `dby-seed/` 和 p2，
不会应用新 rootfs；系统软件在 Ubuntu 内用 `apt` 更新。

**从平板取卡前先正常关机。** 再用已校验的较新 bundle 和明确指定的私有**主机**备份目录查看预览，备份不能位于卡或其挂载点内。
dry-run 不要求 root、不改卡或备份路径，但读取裸 SD 仍需相应读权限：

```sh
python3 -I -B dby-w09-linux/tools/dby-install/dby-install update \
  --device /dev/mmcblk0 --bundle /path/to/new-bundle \
  --backup-dir /path/to/private-host-backups --dry-run
sudo python3 -I -B dby-w09-linux/tools/dby-install/dby-install update \
  --device /dev/mmcblk0 --bundle /path/to/new-bundle \
  --backup-dir /path/to/private-host-backups
```

备份根目录可尚不存在，但其父目录必须存在。新建的 0700 时间戳子目录包含旧五文件、卡原始 manifest
与大小／哈希元数据，读回核对后才改卡；其中可能含私有固件，不上传。必须输入完整设备名。
同号且启动内容相同则不写、不备份；同号不同内容拒绝，旧版本需显式 `--allow-older`。
rootfs 配方／包摘要不同只提示，p2 保持原样。

工具暂存并校验 `UBOOT.new`，重命名新旧目录，经临时名更换 manifest，再重新只读打开验证。
**FAT 重命名不是跨文件或断电原子事务。** 切换后失败会尝试自动恢复，并始终返回非 0，
保留原始错误、备份路径与手动命令；不能假定恢复成功。

按输出的备份路径主动回滚，也可用于卡上 manifest 已不自洽的中断状态：

```sh
python3 -I -B dby-w09-linux/tools/dby-install/dby-install rollback \
  --device /dev/mmcblk0 --backup /path/to/verified-p1-backup --dry-run
sudo python3 -I -B dby-w09-linux/tools/dby-install/dby-install rollback \
  --device /dev/mmcblk0 --backup /path/to/verified-p1-backup
```

回滚先校验备份，要求 UBOOT 或 UBOOT.old 存在，按备份恢复并清理保留的暂存项，不做二次备份。
update 则拒绝残留，不自动删除；若 UBOOT 缺失但有 UBOOT.old，可改回原名或使用已核验备份 rollback。
两命令均拒绝 `--minimal`、`--p2-size`、`--no-readback`、`--image-size`；不格式化／写入 p2，
不编辑种子、不备份用户数据或重装系统。细节见[规则与错误码](../tools/dby-install/README.md#update-the-device-package-and-roll-back)。

开机用过的卡只检查设备包，避免将已变化的 p2 与原 rootfs 压缩包比较：

```sh
sudo python3 -I -B dby-w09-linux/tools/dby-install/dby-install verify --p1-only \
  --bundle /path/to/installed-bundle --device /dev/mmcblk0
```

此模式检查分区表／标签、五启动文件与两种 manifest，不挂载 p2；成功末尾明确输出
`OK: p1 matches the bundle; p2 not checked.`，不能据此判断 p2 日志或系统文件健康。
默认完整 verify 仍比较 rootfs，p2 哈希失败时补充 p1-only 提示。2026-11-02 一张真卡的用户复验记录覆盖 info、verify --p1-only 与 dry-run（R）；info --full 及注入异常路径仍为 O。

**未干净关机**：本次直接拔卡留下 p2 `needs_recovery`，`ro,noload` 只能看到旧视图，
旧工具误报 kind 合同缺失；用户卸载 p2 后手动 `e2fsck -p`，重试恢复。
现在只读预检会在挂载 p2 前报 `E_INSTALL_P2_RECOVERY`（含维护命令 dry-run）。
应让平板正常启动并正常关机，或确认实际 p2 未挂载、目标正确后由用户运行建议的修复：

```sh
sudo e2fsck -p /dev/mmcblk0p2
```

工具不会自行执行修复、重放日志或在 update／rollback／verify／info 中写 p2。
新检测与提示目前只有 O 证据。

**已验证的用法（上述范围 H + R）**：同版本不写不备份；显式 `--allow-older` 降级、
普通升级后均可启动，升级后触摸恢复；用两份不同主机备份分别 rollback，人工检查
MANIFEST 与 device image 哈希正确、无 old/new 残留，种子、其他 p1 目录和 p2 保留。
回滚预览 `old_bundle_id: null` 表示不信任卡当前 manifest，并不表示卡曾中断。

真实卡断电／中途打断后的恢复仍为 U（只有镜像故障模拟）；跨内核版本更新未验证，
当前格式也只接受 `7.2.10`；update 不应用 p2 更新。菜单与用户数据备份恢复命令仍未实现，FAT 重命名不保证掉电安全。

### 6.2 重装系统（只重做 p2，H + R）

当软件包冲突或系统配置混乱时，`reinstall-system` 可保留 p1 的设备包、Wi-Fi／公钥种子，只重做现有 p2。**所有用户 home、已安装软件、系统设置和 SSH 主机密钥都会丢失；命令不做用户数据备份。** 仅在可重装的卡上使用，不能把唯一数据副本交给本命令。完整系统重装已有 H + R 证据（2026-11-02/03），限一张 SD 卡、一台平板、bundle `20261102-3`、内核 `7.2.10`：预览与执行成功，独立比较整个 p1 的 SHA256 前后相同，随后完整 `verify` 通过。全新系统启动，旧用户数据消失、SSH 主机密钥重新生成；保留的明文 Wi-Fi 种子自动联网，触摸正常。共享的 install 格式化／解压代码也在该卡运行。本次首次向导未卡住，不能据此认定偶发缺陷已解决。`--minimal`、`--no-readback` 和中途失败仅 O；真卡断电／中断、跨内核更新仍 U。菜单与用户数据备份恢复仍未实现（U）。

正常关机取卡并卸载其全部分区，确认目标与 bundle。卡上的 bundle_id 和五个启动文件必须与输入 bundle 一致；如不同，先按 §6.1 执行 `update`，再预览：

```sh
sudo tools/dby-install/dby-install reinstall-system --device /dev/mmcblk0 --bundle /path/to/bundle --dry-run
sudo tools/dby-install/dby-install reinstall-system --device /dev/mmcblk0 --bundle /path/to/bundle
```

必须手输完整目标加 ` p2`，例如 `/dev/mmcblk0 p2`；仅输入设备名不会通过。最小系统加 `--minimal`；`--no-readback` 仅跳过卸载后逐文件回读，仍检查合同、几何、p1 整区哈希与分区表。默认完整系统。命令不重新分区、不 resize、不接受 `--p2-size`／`--image-size`；现有 p2 放不下归档加余量时，须使用 `install` 重新分区。

p1 全程只读，种子保留；p2 的未恢复日志不阻止重新格式化，但任何分区处于挂载状态都会拒绝。格式化后无法恢复旧数据；失败时按“不完整 p2”处理，先修复原因再重跑该命令或 install。p1／表哈希检查无法完成或发生变化时会另报，不能假定其完好。

首次启动会重新进入向导并生成 SSH 主机密钥；确认设备身份后更新客户端 `known_hosts`。详细[合同与错误码](../tools/dby-install/README.md#reinstall-system-p2-only-h--r)。

## 7. 公钥与种子

推荐使用安装器 `seed` 命令校验并修改公钥与种子；下述向导／时区范围为 H，安全模式／写入恢复为 H + R，其余设置仍为 O/U。先正常关机、取卡，在主机确认整卡目标并卸载其分区。仅挂载 p1，校验 manifest 与五个启动文件，不挂载 p2；未恢复的 p2 日志不阻止种子编辑。私钥留在客户端，只传 SSH **公钥**。

```sh
sudo tools/dby-install/dby-install seed show --device /dev/mmcblk0
sudo tools/dby-install/dby-install seed set --device /dev/mmcblk0 --backup-dir ./seed-backups --wifi example --ssid example-wifi --psk-prompt --dry-run
sudo tools/dby-install/dby-install seed set --device /dev/mmcblk0 --backup-dir ./seed-backups --wifi example --ssid example-wifi --psk-prompt
sudo tools/dby-install/dby-install seed set --device /dev/mmcblk0 --backup-dir ./seed-backups --authorized-keys /path/to/own-ssh-public-key.pub
sudo tools/dby-install/dby-install seed wizard --device /dev/mmcblk0 --backup-dir ./seed-backups
sudo tools/dby-install/dby-install seed restore --device /dev/mmcblk0 --backup ./seed-backups/SELECTED_BACKUP
```

网络名是虚构示例。口令通过两次无回显提示输入，默认保存明文（8–63 字节）；不一致返回 `E_SEED_PSK_MISMATCH`，不写卡。显式 `--derived-psk` 才派生 PSK，64 位十六进制输入仍作为原始 PSK 保留。两种 PSK 均仅适用于纯 WPA2，不能用于 WPA3/SAE 或 WPA2/WPA3 混合网络，可能导致无法联网，只建议在确认纯 WPA2 时使用。修改 SSID 时只有派生／原始 PSK 项需要重新给出口令；明文项无需重输。PSK 同样属于凭据。**备份含 Wi-Fi 凭据，私下保管**；备份目录 0700、文件 0600。恢复前会在原备份旁保存当前种子，记录原来不存在的文件会从卡上删除。

`seed set` 只改给出的项目；向导回车保持，`-` 删除，公钥不能清空。写前显示口令掩码 diff，要求手输完整目标名；dry-run 不写、不备份。规范化重写会丢弃注释、垃圾行和未知段／键，diff 列出丢弃名称，备份保留原文。时区只校验格式，不检查 p2 的 tzdata。完整选项见[种子命令规则](../tools/dby-install/README.md#seed-and-safe-mode-commands)。

手动方式仍可作为备选，**没有上述校验、备份与自动回滚**。确认目标分区后，自行放入文件：

```sh
sudo mkdir -p /mnt/dby-card
sudo mount /dev/mmcblk0p1 /mnt/dby-card
sudo cp /path/to/own-ssh-public-key.pub /mnt/dby-card/dby-seed/authorized_keys
sudo cp /path/to/own-seed.conf /mnt/dby-card/dby-seed/seed.conf
sudo sync
sudo umount /mnt/dby-card
```

最小设置示例；Wi-Fi 凭据请通过上方 `--psk-prompt` 输入：

```ini
version = 1
[system]
timezone = Etc/UTC
safe_mode = false
```

可选 `[apt] mirror=...`、`[display] refresh_hz=60` 或 `120`、`[packages] install=tree`。法规 `country` 只在自己的私有 Wi-Fi 节中按所在地填写，这里不提供值。密码接受 8–63 字节或 64 位十六进制 PSK，PSK 同样属于凭据。FAT 不能阻止持卡者读取它们，不公开种子、bundle 或含身份信息的日志。

种子在平板上只读。公钥用于 root；普通用户／主机名由完整桌面首次向导创建。不写时区就保持已有设置；删掉 APT 镜像项会恢复保存的原始源。刷新率在下次启动生效，删除设置后也需再启动才能回到 120 Hz。额外包可能错过 Wi-Fi 就绪，既有测试在联网后 `sudo systemctl restart dby-seed` 安装成功（H）；尚未实现网络到来时自动重试。

显示恢复可用 `[system] safe_mode=true`：initramfs 在系统 init 前写临时 `/run/modprobe.d/dby-safe-mode.conf`，拒绝 msm／面板／触摸模块加载。黑屏是预期结果；既有实测中 USB／SSH／Wi-Fi 仍可达（H）。移除设置后重启恢复正常加载。下面的安装器 on/off 命令已在上述范围内实机验证（H + R）；`off` 写入 `safe_mode=false`，不会修复偶发黑屏根因。

```sh
sudo tools/dby-install/dby-install safe-mode status --device /dev/mmcblk0
sudo tools/dby-install/dby-install safe-mode on --device /dev/mmcblk0 --backup-dir ./seed-backups
# To restore normal display/module loading on the next boot:
sudo tools/dby-install/dby-install safe-mode off --device /dev/mmcblk0 --backup-dir ./seed-backups
```

2026-11-02，一张 SD 卡／一台平板上，`seed set` 已写入派生 PSK 并通过工具自校验，`seed restore` 已恢复原种子（H + R）。测试者的一个路由器上，计算正确的派生 PSK 无法连接，恢复原明文口令后立即连上。原因推测为 WPA3/SAE 或混合模式协商，未在路由器侧核实。2026-11-02/03，一张 SD 卡、一台平板、bundle `20261102-3`、内核 `7.2.10` 新增以下证据：`seed wizard` 在 dry-run 下拒绝两次不一致的口令且未写入；真实向导只改时区，预览仅该项变化，写入自检通过并生成备份（H）。`seed set --timezone` 两次写入后均由开机后的 `timedatectl` 确认（H）。误敲目标确认串被拒绝且未写入（H）。`safe-mode on/off` 跨启动阻止再恢复 msm、面板和触摸模块；开启时背光亮但无画面，USB 网卡与 SSH 正常（H + R）。结论不推广到其他设备或网络。`seed set` 其他项（镜像源／刷新率／软件包／替换或追加公钥）仍需真卡复验；纯 WPA2 网络上的派生 PSK 联网仍为 U（命令逻辑 O）。本次向导标签选择修复为 O，待真卡复验。

向导逐行列出现有 Wi-Fi 标签：只有一个时默认选它；多个时无默认值，空输入会重新询问；没有网络时默认 `main`。任何新标签（含空卡默认值）都需再次准确输入确认，不一致则返回标签选择。其余问题仍为回车保持、`-` 删除、口令两次确认，公钥不可清空。

## 8. 重打包／刷写 Recovery 与首次启动（所有者操作）

使用适配本机／固件、已校验的自有原厂 Recovery，以及已解锁的 Bootloader。先核对 U-Boot 产物，并保留校验过的原厂副本用于回退。打包不访问设备：

```sh
python3 -I -B dby-w09-uboot/tools/pack-recovery.py \
  --base /path/to/own-stock-recovery.img --kernel build/uboot-out/u-boot.bin \
  --out build/recovery.img --expect-base-sha256 YOUR_VERIFIED_STOCK_SHA256
```

将摘要占位符替换为独立核实的 64 位十六进制值。工具自检原厂重打包，保留 ramdisk、DTB、DTBO 和 AVB 尾部；原描述符不能认证已替换内核。只有记录中的已解锁设备基线通过启动验证，不能推广到任意固件。

之后由所有者执行：

```sh
fastboot flash recovery build/recovery.img
fastboot reboot
```

使用普通 `fastboot reboot`，不要 `fastboot reboot recovery`。关机、插入已准备好的卡，手动进入 Recovery。U-Boot 菜单为 Ubuntu／重启回鸿蒙／关机，音量键移动、电源键确认，超时默认 Ubuntu。准备完毕后的日常启动无需电脑。回退时仅刷回自己已校验的原厂 Recovery，然后普通重启；恢复出厂设置前先恢复原厂 Recovery，不刷其他内部存储分区。

## 9. 首次检查与常见问题

USB 网络可 SSH 到 `root@172.16.42.1`，Wi-Fi 使用自己私下确认的平板地址。重装会重新生成 SSH 主机密钥，删除旧 known-host 条目前先确认是预期设备。只读检查包括 `uname -r`、`systemctl --failed`、`ip -brief address`、`getent hosts ports.ubuntu.com`、`timedatectl`，以及 NetworkManager、dby-seed 和显示日志；完整桌面另查 GDM／GNOME 与硬件渲染诊断。原始输出私下保存，对外只报告脱敏结论与 H／R／O／U 级别。

- **GNOME 首次向导卡住**：记录中的首会话 dconf worker 忙转，根因未知。以新建桌面用户身份创建 `~/.config/gnome-initial-setup-done`，再用管理权限重启 GDM；这会结束图形会话。该方法恢复了既有会话（H + R），不作为通用修复。
- **显示**：2026-11-18 内核补丁 `0017` + `0018`（一台平板，包 `20261118-5`，H + R）解决了唤醒黑屏与快速连按条纹（46 次唤醒／切换含 38 次 `13–68 ms` 极限连按 `0/46` ESD 复位）；报警脚内核复位（`0013`）仍作为兜底保留，多日长期可靠性仍待验证。可用文档中的 safe_mode／SSH 路线诊断，不猜改硬件默认值或换触摸固件。
- **Wi-Fi 已连但 DNS／时间不通**：当前配方提供 resolved stub 链接和时间服务，应分别检查链路、DNS 和时间，不能仅凭 Wi-Fi 关联推断。
- **安装命令失败**：保留稳定错误码；`DBY_TRACE=1` 增加命令名／状态，不输出原始参数。`udevadm settle` 有限时且其超时可以非致命，但分区表／节点检查仍必须通过，详见[安装诊断](../tools/dby-install/README.md)。
- **构建失败或中断**：检查过滤后的日志与清理结果；若报告保留了挂载工作区，应先检查，不能盲删。重试使用新输出目录。
- **没有声音或蓝牙配对**：后续音频和一只蓝牙鼠标已有状态页所列单机记录，其他组合仍为 U。安装器菜单与用户数据备份恢复仍未实现；设备包 update／rollback 在 §6.1 的范围内已有 H + R，新收尾选项仍为 O。

### 触摸缺失

先查模块加载、固件输入锁和设备镜像内的 `firmware/novatek/nt36523-thp.bin`，再读参数与内核日志。以下命令由所有者在已启动的平板上只读执行，输出私下保存：

```sh
lsmod | grep nt36523
for parameter in /sys/module/nt36523_thp/parameters/*; do
  printf '%s: ' "${parameter##*/}"
  cat "$parameter"
done
dmesg | grep -i nt36523
```

对照上方选项行，尤其是 `guard=0`。记录中的开机状态为 `state=done steps=46 error=0`，随后 `state=running`、`fw=loaded`（H）；这只是对照点，不保证其他输入组合相同。不通过写 sysfs 改 guard 或恢复参数来排错。三指手势、手写笔、长时间／启动可靠性及 guard 本身仍为 U；触摸或显示异常时不替换其他平板的固件。

## 当前源码与旧配方的差异

内核配方现在应用十二个补丁。当前默认模块列表还需要 `dby_chg.ko` 与 `dby-tfa9874.ko`；按内核构建指南在独立副本编译，并把目的路径 `kernel/drivers/power/supply/dby_chg.ko`、`kernel/sound/soc/codecs/dby-tfa9874.ko` 加入 `external_modules`。示例已补齐这两项，仍须逐项替换占位路径与源码提交。当前完整模块／板级组合还要提供所有者锁定的 Venus 目录。ADSP 一项也必须替换为所有者实际准备的锁定目录；若不选择板载音频，移除该私有输入并使用自己明确裁剪的模块列表，不能把完整列表当作精简配方。

当前音频使用所有者提供的配套 ADSP 固件，在锁中逐文件记录包内 `firmware/qcom/dby-w09/adsp.*` 的摘要与权限。原始取得过程和当前完整功能组合尚无第三方从零复现记录；本文历史 radio／GPU／touch 提取流程不足以声称已完整部署音频／视频。Venus 的既有范围和缺陷见状态页，本次不增加固件部署承诺。

安装流程未经第三方从空白卡独立验证，欢迎反馈。块设备 dry-run 同样需要读取设备节点；若普通用户没有读权限，应由所有者在核对目标后用 sudo 执行同一预览，不能把预览误当作不需要设备访问。
