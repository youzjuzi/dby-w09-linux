# NT36523 THP source snapshot / 触摸源码快照

Current rebuilt baseline (2026-11-02): this source has been built and deployed with **guard=0**; a short device check recorded loading/events (**H**), with responsive single touch, edges/four corners and pinch observed (**R**). Three-finger gestures, stylus and long-term stability remain **U**. / 新基线已构建并在 guard=0 下通过短时设备检查（H），单指跟手、边缘／四角和缩放有用户观察（R）；三指手势、手写笔和长期稳定性仍为 U。See [status](../../docs/status.md).

This directory contains the driver, local headers, and offline plan generator/validator. It extends the earlier tested multi-touch module with a palm-transient guard. **Source defaults to `guard=1`; that feature has not passed acceptance, so the [bundle builder](../../tools/dby-bundle/README.md) fixes `guard=0` and requires exactly ten parameters, including guard.** Code review suggests the earlier reporting behavior when disabled (O); that equivalence has not been separately verified on hardware. The current and earlier modules are not the same build.

本目录提供驱动、算法／控制头和固定下载计划生成／校验脚本。源码在较早实测多指模块上新增手掌短触点 guard，**默认 guard=1，但该功能未通过验收；设备包固定 guard=0，构建期精确要求含 guard 的十参数**。按读码判断关闭后应保留较早上报行为（O），没有单独实机等价验证。触摸与显示共用 TDDI；单个组合的 H + R 不证明任意 NT36523 设备可用，借用的小米 elish/nabu 固件禁止加载。

## 多指功能状态 / Multi-touch status

多指已经实现。**旧工程截至 2026-10-24** 的多指模块用户观察包括：libinput 识别五槽、Chrome 画板多指、两指缩放和 GNOME 三指上滑；后续设备包启用 `confirm=1 multi=1`。**新基线 2026-11-02** 则记录最多五个同时触点（H），用户观察两指缩放正常（R），本次未测三指手势（U）。五槽是当前软件上限，不是对硬件最大触点数的认定。

手掌落下瞬间短触点（原厂也存在）、触点极近或合并的限制仍在；开机偶发起不来、扫描率命令、笔、长时间稳定性与 guard 功能本身仍为 U。不能用旧工程的手势观察填补本次未测项。

Earlier-project observations through 2026-10-24 included multi-touch drawing, pinch and GNOME three-finger swipe. The separate 2026-11-02 check recorded up to five contacts (H) and pinch interaction (R), without a three-finger test. Palm-onset transients, also seen in stock behavior, and close/merged-contact limitations remain. Intermittent startup failure, scan-rate commands, stylus, long-term behavior and the guard feature remain U; older results do not validate them on this baseline.

## Private inputs still required

`thp-plan-table.h` 未收录。该生成表涉及厂商数据，分发依据尚未核清；固件本身也不分发。生成器要求以下固定输入，名称、摘要和格式由 `gen-plan-header.py` 的 PINS 固定：

- stock-nt36523-thp.bin
- plan.json、verification.json
- read-allowlist.json、spi-transactions.json
- write-whitelist.json、forbidden.json

这些 JSON 不是随意填写的配置，而是原下载协议分析和核验产物。本仓库已提供[从自己的匹配 AFE 库重建全套输入的教程](../../docs/touch-plan-preparation.md)，包括离线生成工具和固定摘要核验。仅有固件 BIN 不足以重建完整审计。不能用空表、其他设备固件或跳过校验替代它们。

先按教程生成匹配输入，再在仓库根按以下配方生成私有表；七项输入及头文件两次生成一致（O），匹配模块构建与 guard=0 短时实机结果见上方：

```sh
python3 -B -I drivers/nt36523-thp/gen-plan-header.py   --inputs /path/to/private-plan-inputs   --output-dir "$PWD/local/touch-plan/generated"
```

把本目录的 `.c`、`.h` 和 Makefile 复制到独立模块构建目录，再复制生成的 thp-plan-table.h 到同一目录，以根 README 的树外模块方式构建。生成器只做离线表验证与输出，不操作硬件；固定摘要、长度、地址白名单和输出范围约束保留。

运行时固件名为 `novatek/nt36523-thp.bin`。公开板级源码已有 compatible `novatek,nt36523-thp`、16 MHz、无 reset／regulator 的节点，包含对应 SPI、IRQ 和 `panel` 引用；新触摸检查没有改 DTB 或内核（H）。内核仓库 `dts/` 提供私有内存提取／编译说明；数字 phandle 不能直接复制到另一棵树。使用面板 follower 的生命周期。模块构建／打包见[安装指南](../../docs/install.zh-CN.md#31-启用触摸可选)，上机检查[只读](../../docs/install.zh-CN.md#触摸缺失)。

Firmware is requested as `novatek/nt36523-thp.bin`. The existing public 16 MHz board node needs no DTB/kernel change for the recorded combination; do not copy numeric phandles into another tree. Follow [optional build/bundle setup](../../docs/install.md#31-enable-touch-optional) and [read-only checks](../../docs/install.md#touch-missing). Retain the panel follower lifecycle and the fixed guard=0 bundle policy.
