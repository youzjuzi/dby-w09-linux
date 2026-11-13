# Firmware / 固件

[Preparation guide / 提取准备](firmware-preparation.md) · [Install / 安装](install.md) · [中文安装](install.zh-CN.md)

This page is bilingual. Firmware preparation is an **O** documented procedure; hardware claims apply only to the combinations identified in [status](status.md). / 本页中英双语。固件准备属于 **O 离线文档流程**；实机结论只适用于[状态页](status.md)记录的组合。

## Policy / 原则

Vendor firmware, stock Recovery, partition images, identities and extracted panel/touch data are not distributed. Owners obtain their own stock data or appropriately licensed upstream firmware and record its origin/version/licence. File names and self-generated hashes alone do not establish compatibility or redistribution rights.

不分发厂商固件、原厂 Recovery、分区镜像、身份或提取的面板／触摸数据。所有者自行取得原厂数据或具有适当许可的上游固件，并记录来源／版本／许可；文件名和自行生成的摘要不能证明兼容或再分发权。

| Category / 类别 | Preparation source / 准备来源 | Current scope / 当前范围 |
|---|---|---|
| Wi-Fi | Matching ath11k firmware plus owner board/calibration data / 匹配的 ath11k 固件与本机板级数据 | H connection on recorded baseline / 记录基线联网 H |
| Bluetooth / 蓝牙 | Owner stock firmware and matching parameters / 本机原厂固件与配套参数 | H + R controller; R for one paired working Bluetooth mouse (2026-11-03, one tablet); reboot/sleep reconnection, other types and A2DP U / 控制器 H + R，一只蓝牙鼠标配对可用 R（2026-11-03，一台平板）；重启／休眠回连、其他类型与 A2DP U |
| GPU | Matching SQE/GMU from licensed upstream source and owner ZAP / 许可上游 SQE/GMU 与本机 ZAP | H accelerated desktop / 加速桌面 H |
| Touch / 触摸 | Exact supported owner AFE library's embedded firmware / 受支持的精确 AFE 输入内嵌固件 | O extraction/plan generation; H + R short new-baseline check with guard=0, 2026-11-02; long-term behavior U / 提取与计划生成 O，新基线 guard=0 短时检查 H + R，长期表现 U |
| Audio DSP / 音频 DSP | Owner stock firmware / 本机原厂固件 | Owner-supplied; four-mic and speaker records H + R on one tablet, 2026-11-06/07; full owner preparation not covered here / 所有者输入，四麦／喇叭单机记录 H + R，完整准备流程不在本文内 |

The [preparation guide](firmware-preparation.md) supplies owner-run read-only extraction examples, the pinned AFE slice and directory/lock creation. No device extraction was rerun for this documentation. The [bundle configuration](../tools/dby-bundle/README.md) selects each locked input directory explicitly; no historical directory name or input registry is required. Firmware stays in p1's device image, outside the rootfs's device-firmware payload.

[准备教程](firmware-preparation.md)提供所有者手动只读提取示例、锁定 AFE 区间与目录／锁生成；本次文档未重新执行设备提取。[bundle 配置](../tools/dby-bundle/README.md)显式选择每个锁定目录，不要求历史目录名或注册表。固件随 p1 设备镜像提供，不放入 rootfs 的设备固件载荷。
