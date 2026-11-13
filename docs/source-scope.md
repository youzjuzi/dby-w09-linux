# Source inventory / 源码收录范围

[Home](../README.md) · [中文首页](../README.zh-CN.md) · [Install](install.md) · [安装](install.zh-CN.md)

Bilingual inventory for the 2026-11-02 development baseline. Hardware results (H/R) cover one owner input combination; source/build checks (O) do not validate other combinations. See [status](status.md). / 本页中英双语。2026-11-02 的 H／R 只覆盖一组所有者输入，O 不证明其他组合可用，见[状态](status.md)。

| Component / 组件 | Included / 收录内容 | External inputs and limits / 外部输入与限制 |
|---|---|---|
| U-Boot | Twelve patches, configuration, board descriptions, build and Recovery repacking / 十二补丁、配置、板级描述及构建打包 | Pinned upstream/toolchain and own stock Recovery / 锁定上游、工具链、自有原厂 Recovery |
| Linux | Ten patches, configuration, board DTS, panel/memory extractors / 十补丁、配置、板级源码与提取工具 | Upstream source, owner-generated panel header and memory include / 上游、所有者生成的面板与内存数据 |
| Backlight/gauge / 背光电量计 | Out-of-tree sources and integration notes / 树外源码与接入说明 | Matching prepared kernel/source pair and DT wiring / 匹配内核构建树、DT 接入 |
| Touch / 触摸 | Driver, headers, offline reconstruction/generation/validation / 驱动与离线重建生成校验 | Exact owner AFE, GNU objdump 2.46, seven locked inputs and generated table; H + R short new-baseline check with guard=0, 2026-11-02; stylus/three-finger gestures/long-term behavior U / 精确 AFE、七项锁定输入和生成表；新基线 guard=0 短时检查 H + R，笔／三指手势／长期表现 U |
| [Bundle tools](../tools/dby-bundle/README.md) | p1 builder, initramfs, module list, runtime overlay, manifests / p1 构建、运行时与清单 | Explicit matching built inputs and locked firmware/configuration / 显式匹配产物及锁定固件配置 |
| [Rootfs recipes](../tools/dby-bundle/rootfs/README.md) | Full/test recipes and overlays / 完整与测试配方 | Pinned ISO/base, root-capable host and APT; no repository snapshot / 锁定输入、root 主机与 APT，无仓库快照 |
| [SD installer](../tools/dby-install/README.md) | install, verify, info, update, rollback, seed, safe-mode, reinstall-system, synthetic/image tests / 安装、验证、信息、更新、回滚、种子、安全模式、重装系统与离线测试 | Whole supported SD target; owner performs real writes; one-card checks are recorded in the status page; menu and user-data backup/restore are not implemented (U) / 受支持整卡，所有者写卡；单卡验证见状态页；菜单与用户数据备份恢复未实现（U） |

The [firmware](firmware-preparation.md) and [touch plan](touch-plan-preparation.md) guides explain owner preparation, not vendor-data redistribution. Bundles may contain private firmware/configuration and are not public release attachments. The public sources include no stock images, device firmware, private keys, owner MAC/serial or live identity logs. USB gadget addresses are kernel-generated; no fixed device identity is embedded.

[固件](firmware-preparation.md)和[触摸计划](touch-plan-preparation.md)教程仅说明自行准备，不授予厂商数据再分发权。bundle 可能含私有固件／配置，不作为公开附件。公开源码不含原厂镜像、设备固件、私钥、本机 MAC／序列号或身份日志；USB gadget 地址由内核生成，无固定设备身份。

Publication work still includes source commit mapping, per-component provenance/licence review and a confirmed private security-reporting channel. See [versions](versions.md), [third-party notes](../THIRD_PARTY.md) and [security](../SECURITY.md). / 发布前仍需填写提交对应、逐项来源许可审查和确认私密安全报告通道，见上述链接。
