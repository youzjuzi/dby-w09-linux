# Architecture / 体系结构

[English home](../README.md) · [中文首页](../README.zh-CN.md) · [Install](install.md) · [安装](install.zh-CN.md)

This page is bilingual. Mechanisms below are **O** source/contract descriptions; the 2026-11-02 baseline's boot, mount and safe-mode checks have **H** recorded device evidence. Interactive desktop effects include **R** user observations. See [status](status.md); the design is not validation of other firmware/input combinations.

本页为中英双语。下列机制属于 **O 离线源码／契约说明**；2026-11-02 基线的启动、挂载和 safe_mode 有 **H 实机记录**，桌面交互另含 **R 用户观察**。证据与限制见[状态页](status.md)，不能将设计推广为其他固件／输入组合已经验证。

## Boot chain / 启动链

```text
manual Recovery entry / 手动进入 Recovery
  → vendor ABL loads the recovery image / 原厂引导器加载 Recovery
  → kernel segment = U-Boot / 内核段替换为 U-Boot
  → checked misc command clearing, menu / 受限清理 misc、菜单
  → read SD p1 and verify MANIFEST / 只读 p1、校验三行清单
  → Linux Image + board DTB + initramfs
  → mount SD p2, attach device image / 挂载 p2 和设备镜像
  → move mounts, chroot, apply early safe_mode / 换根与早期安全模式
  → /sbin/init → systemd → test console or GDM/GNOME
```

Normal power-on still enters HarmonyOS. Once prepared, manual Recovery entry and card boot need no computer. The vendor's acceptance of a modified Recovery image is H for one unlocked DBY-W09 with HarmonyOS 4.2.0.213, not a general verified-boot bypass.

正常开机仍进鸿蒙。准备完成后，手动进 Recovery 和卡启动无需电脑。修改后 Recovery 的接受结果仅对一台已解锁、HarmonyOS 4.2.0.213 的 DBY-W09 有 H 证据，不代表通用绕过验证启动。

## Card layout / 卡布局

DOS partition table, 512-byte sectors. / DOS 分区表、512 字节扇区。

| Partition / 分区 | Contract / 契约 | Runtime access / 运行时访问 |
|---|---|---|
| p1 | Starts at sector 2048, 2097152 sectors (1 GiB), type 0x0c, FAT32 label `UBOOT` / 起始与大小固定 | U-Boot and tablet Linux read-only / U-Boot 与平板 Linux 只读 |
| p2 | Immediately after p1, type 0x83, ext4 label `ubuntu-root`, block 4096 / inode 256 / 紧接 p1 | Linux root, read-write / Linux 根系统可读写 |
| Full / 完整 | p2 reaches the card end / p2 用尽余下空间 | Ubuntu desktop |
| Test / 测试 | p2 defaults to 4 GiB, bounded `--p2-size`; rest unallocated / 默认 4 GiB，余下未分配 | Minimal console/SSH, no GNOME / 最小控制台与 SSH |

Both use the exact fstab contract / 两种 rootfs 共用：

```text
LABEL=ubuntu-root / ext4 defaults 0 1
```

```text
p1 (UBOOT label)
├── UBOOT/
│   ├── Image             # gzip Linux kernel / gzip 内核
│   ├── dby-w09.dtb
│   ├── initrd.img
│   ├── dby-device.img    # read-only squashfs / 只读设备镜像
│   └── MANIFEST
├── manifest.json         # bundle metadata copied by installer / 安装器复制
└── dby-seed/
    ├── authorized_keys   # optional public keys / 可选公钥
    └── seed.conf         # optional owner configuration / 可选所有者配置
```

`UBOOT/MANIFEST` contains exactly three SHA-256 lines, in this order, with two spaces before each basename. Replace the bracketed descriptions with actual lowercase 64-hex digests; they are not literal file contents.

`UBOOT/MANIFEST` 恰好三行，顺序固定，文件名前两个空格。下面的方括号说明须替换为真实 64 位小写十六进制摘要，不是可直接使用的内容：

```text
[SHA-256 of Image]  Image
[SHA-256 of DTB]  dby-w09.dtb
[SHA-256 of initrd]  initrd.img
```

U-Boot verifies those three files, not MANIFEST itself or `dby-device.img`. The bundle manifest separately inventories all payloads, including the device image and selected rootfs archives; the installer checks them. Neither manifest is a publisher signature: replacing a manifest together with its data defeats authenticity assumptions.

U-Boot 校验这三个文件，不校验 MANIFEST 自身或 `dby-device.img`。bundle manifest 另行记录设备镜像及所选 rootfs 等全部载荷，由安装器校验。两种清单都不是发布者签名，不能防范同时替换清单和数据的攻击者。

## Bundle and device image / bundle 与设备镜像

[Bundle tools](../tools/dby-bundle/README.md) package matching prebuilt kernel/modules, an explicit DTB, separately built drivers and locked owner inputs. `manifest.json` records declared repository/upstream provenance and actual input/payload digests. Private inputs contribute digests only; do not put their paths or identity values into public provenance fields. A p1-only bundle must have a selected rootfs attached before installation.

[bundle 工具](../tools/dby-bundle/README.md)使用匹配的已构建内核／模块、显式 DTB、独立驱动和锁定的所有者输入。`manifest.json` 记录声明的仓库／上游来源与实际输入／载荷摘要；私有输入只贡献摘要，不能将路径或身份值放进公开来源字段。仅 p1 的 bundle 需附上所选 rootfs 才能安装。

`dby-device.img` contains selected modules and depmod metadata, firmware, a bounded runtime configuration overlay and `DEVICE-CONTENTS.txt`. It does not contain the Ubuntu rootfs, a user's desktop data or seed credentials by default. Explicit owner `rootfs/etc/` inputs may contain private configuration, so the entire built bundle remains private.

`dby-device.img` 包含选定模块、depmod 元数据、固件、受限运行时配置覆盖层和 `DEVICE-CONTENTS.txt`，默认不含 Ubuntu 根系统、用户桌面数据或种子凭据。所有者显式提供的 `rootfs/etc/` 输入可能带私有配置，因此完整构建 bundle 仍须私下保管。

Initramfs validates/mounts p2, exposes p1 at `/run/dby-p1` read-only, mounts the squashfs at `/run/dby-device`, binds its `modules/7.2.10` onto the rootfs module directory read-only and selects its firmware path. Rootfs therefore has no independent kernel/module or device-firmware payload. Moving to the new root preserves the runtime mounts. Replacing only one kernel-related file can break the module contract; use `dby-install update`, which replaces the boot files and manifest together, instead of copying files by hand.

initramfs 校验并挂载 p2，将 p1 只读挂到 `/run/dby-p1`，squashfs 挂到 `/run/dby-device`，把其中 `modules/7.2.10` 只读绑定到根系统模块目录，并设置固件路径。rootfs 因而不另带内核／模块或设备固件。换根保留运行时挂载；单独替换某个内核文件可能破坏模块契约，目前没有自动 update 流程。

## Seed and safe mode / 种子与安全模式

The systemd seed applier reads p1 without writing it, applies only version-1 whitelist keys and logs value-free results. NetworkManager owns Wi-Fi; networkd owns `usb0`. Key files configure root public-key SSH. Optional settings cover regulatory requests, APT mirror, timezone, next-boot panel refresh and extra packages. It executes no seed-provided script. The service is asynchronous and does not wait for Wi-Fi, which causes the known extra-package timing defect. See the [installation guide](install.md#7-public-key-and-seed).

systemd 种子应用器只读 p1，仅应用版本 1 白名单键，日志不记值。NetworkManager 管 Wi-Fi，networkd 管 `usb0`；公钥文件配置 root 公钥 SSH。可选项有法规请求、APT 镜像、时区、下次启动的刷新率和额外包；不执行种子中的脚本。服务异步且不等待 Wi-Fi，因此有额外包联网时序缺陷，见[安装指南](install.zh-CN.md#7-公钥与种子)。

Before `/sbin/init`, initramfs separately reads `[system] safe_mode=true` and creates only `/run/modprobe.d/dby-safe-mode.conf` in tmpfs. It blacklists/denies loading msm, panel and touch modules. Unknown lines are skipped, duplicate safe_mode keys use the last value, and invalid/unreadable input falls back to false. Removing the request and rebooting leaves no persistent safe-mode policy. The H check covered display-module suppression and restoration while SSH/network stayed available; it did not include the later touch-enabled bundle, so touch suppression still has only O implementation/test evidence.

在 `/sbin/init` 前，initramfs 单独读取 `[system] safe_mode=true`，仅在 tmpfs 写 `/run/modprobe.d/dby-safe-mode.conf`，以 blacklist／拒绝加载规则抑制 msm、面板和触摸模块。未知行跳过，重复 safe_mode 取最后值，无效／不可读输入回落为 false。删除请求并重启不遗留持久策略。H 验证覆盖显示模块抑制与恢复、SSH／网络仍通；该检查未覆盖后来的触摸包，因此触摸抑制仍只有 O 实现与测试证据。

## Storage boundary / 存储边界

Owner flashing changes only Recovery. U-Boot's exceptional internal write clears only the first 32 bytes of `misc`, after confirming `boot-recovery` plus its NUL terminator and then verifying the result. Other internal partitions remain outside the intended write scope. Linux writes only the SD rootfs; p1 is edited on a host while the tablet is off. Factory reset requires restoring stock Recovery. These are source-level boundaries with single-device evidence, not a guarantee for unreviewed hardware/firmware changes.

所有者刷写仅改 Recovery。U-Boot 的内部写入例外仅在确认 `boot-recovery` 及 NUL 终止字节后清除 `misc` 前 32 字节，并读回核对；其他内部存储分区不在预期写入范围。Linux 只写 SD 根系统，p1 在平板关机时由主机编辑。恢复出厂设置前恢复原厂 Recovery。这是源码边界与单机证据，不是对未审查硬件／固件变化的保证。
