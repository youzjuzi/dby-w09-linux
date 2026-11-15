# Versions and recorded hashes / 版本与记录哈希

[English home](../README.md) · [中文首页](../README.zh-CN.md) · [Install](install.md) · [安装](install.zh-CN.md)

## R4 source baseline / R4 源码基线 (2026-11-15)

Local main, tracking origin/main and the live public remote main matched before clean local cloning. Rebuild outputs remain O evidence. The maintainer subsequently passed a fresh installation from a blank 16 GB card following the public documentation on 2026-11-15, bundle `20261115-1` (H + R); see [reported device results](status.md#r4-fresh-rebuild--r4-从零重建). Independent third-party reproduction remains U. Private extraction was not repeated and retains its prior evidence level. The source commits below identify the inputs, not the later documentation branches. / 三库 main 在克隆前核对一致；维护者新卡全新安装已通过（H + R），第三方独立复现仍 U。已有私有输入复用，提取未重做、证据级别不变；文档提交不改变构建来源。

| Repository | R4 main commit |
|---|---|
| kernel | `dbdc1c115a2f2f7b891fd2629744b79377948a1e` |
| linux | `164d4486f5bb148844ee760fbb916650129d0f6e` |
| uboot | `a662eea70fd8c2b2645895617438ac40c89ed4a4` |

The U-Boot source was cloned and its archive checked; no U-Boot/Recovery image was rebuilt or flashed. This commit is not asserted to be the installed Recovery's build source. The checked upstream versions and hashes remain those in the pinned-upstreams table below. See [per-file checksum commands](fresh-install.md#host-inputs--主机输入); a matching digest does not authenticate a signing key. / 保留现有 Recovery；此处 U-Boot 提交不声明为已刷镜像的源码版本。

R4's final kernel configuration has SHA-256 `e6fc51a2331ff01bee7ebe40a574b293f6ae2db3008da57c5bb5771b77ed00ae`; the board DTB is `f09d4e0251614f1c780a16eee8a5fe618ce547099995e8f424b68362dd8a2962`. Both are byte-identical to the corresponding configuration/DTB from the previous `20261102-37` bundle (O). Toolchain: GCC 15.2.0, binutils 2.46, dtc 1.7.2 (36 existing warnings), Python 3.14.4. These owner-specific artifacts are not distributed.

### R4 outputs (O) / 本轮产物

Bundle **`20261115-1`**, full rootfs attached and bundle verification passed. The owner completed the sudo full-rootfs build; its log reports cleanup complete with no build mounts retained. These build checks remain host evidence (O). Subsequent new-card installation/first boot passed within the H + R scope linked above; this feedback did not separately report the full-card verify/read-back result.

| Artifact | SHA-256 |
|---|---|
| Raw Image | `19bd1745898d2b84d2a1618e3462590f0f81cec8fad82cdc916cd217046dcb49` |
| `p1/UBOOT/Image` | `d858362d80c7ccffd59e3510a0c2088e8a5e1ba8f08b58f02bb4a9b6ccaad9ac` |
| `p1/UBOOT/MANIFEST` | `c4e5ead44d5ac0e8dc8587fe6bb67a4df5881f11c8dc43912ae904f876b6e1ca` |
| `p1/UBOOT/dby-device.img` | `b0c65f2dee49a37b103c2eb947e9281b0a28bc8d99f8e6a429dd9b63d8cc042e` |
| `p1/UBOOT/dby-w09.dtb` | `f09d4e0251614f1c780a16eee8a5fe618ce547099995e8f424b68362dd8a2962` |
| `p1/UBOOT/initrd.img` | `44a5aa8c3e67ebce3bfb7ac6bce30fbda2c1e605b87ab9a1fce31fa5afc3d0ad` |
| `rootfs.tar.zst` | `9e1c10c093f4a67da325bcad787eacef3d9211de74e7d005530b468b6ee2179f` |

Against `20261102-37`: DTB and embedded kernel configuration are byte-identical. Raw Image has only 70 changed bytes, all in build version/time, kernel/vDSO build IDs and empty built-in initramfs mtimes. Of 117 modules, 113 become byte-identical after stripping debug sections and build-id. The other four (`qcom-spmi-adc5`, `q6afe`, `q6afe-dai`, `snd-soc-sm8250`) change from old external overrides to the public patched in-tree build; their only extra allocated metadata is `intree=Y`. Removing that metadata as well produces byte-identical files. This changes out-of-tree provenance/taint classification; it is not a driver-code change. The device contents inventories report identical paths, modes and digests for all 102 non-module entries.

Full rootfs records **1493 packages**, inventory SHA-256 `f86f7ef114a48c2e864a77082f6203758afd86f3b0e21ee739ed1de8a08760ae`. Against the owner's 1492-package inventory: adds openssh-server/openssh-sftp-server, removes firmware-sof-signed, updates openssh-client/wireless-regdb. APT is not frozen. No hardware acceptance is inferred from these package changes or host tests (234 bundle, 164 installer, 23 kernel checks).

Linux 7.2.10 does not use SOURCE_DATE_EPOCH alone for its UTS/initramfs times; R4 followed the original README and records actual times. See the corrected [kernel build notes](https://github.com/youzjuzi/dby-w09-kernel/blob/main/docs/build.md). For card selection and first checks, use the [fresh-install checklist](fresh-install.md).

A non-device installer dry-run using 15,258 MiB (15,999,172,608 bytes, slightly below a nominal 16 GB card) passed the rootfs capacity/margin check; p2 is 14,924,382,208 bytes with full p1/p2 read-back planned. This dry-run wrote neither an image file nor a real card; the later owner installation is recorded separately above. For each installation, actual card identity/capacity requires the owner's lsblk comparison and device dry-run. / 仅普通文件目标预检，不替代真卡检查。

## Historical 2026-11-02 inventory / 历史清单

Bilingual baseline inventory, dated **2026-11-02**. The hashes identify recorded inputs/outputs, not downloadable firmware or signed releases. **O** = offline build/hash records; **H** = that input combination later booted on the maintainer's device; **R** = user observation. Other private inputs can produce different outputs. APT is not frozen, so rootfs reproduction across dates is not guaranteed.

本页中英双语，记录 **2026-11-02** 基线。摘要用于识别记录中的输入／产物，不提供固件下载，也不是签名发布。**O**＝离线构建／摘要记录；**H**＝该输入组合后来在维护者设备启动；**R**＝用户观察。其他私有输入可能产生不同结果，APT 未固定快照，不能保证跨日期 rootfs 相同。

## Public source mapping / 公开源码对应

| Repository / 仓库 | Baseline commit / 基线提交 | Role / 职责 |
|---|---|---|
| dby-w09-linux | `0314a17c74ef579ee99a34983181960d90ff11e7` (touch bundle tools, H / 触摸包工具) | Bundle/rootfs tools, SD installer, drivers; ten-parameter touch contract with guard=0 / 打包、安装和树外驱动；十参数触摸合同、guard=0 |
| dby-w09-kernel | `60ab09935d0bfe526231943dce1d41dc926831c8` (booted, H); `601d8af489b8d487ef710d5c8ed72fd614ec419f` (comment/provenance-only changes; DTB hash reproduced identically, O) | Linux patches/configuration/board source / 内核补丁、配置与板级源码 |
| dby-w09-uboot | `b34c276293386f1deaaacd8ce2c831b1ad886f67` (booted, H); `0fdca34fcd51049863c2fcd805fa3c0fd19d6bd7` (comment/provenance-only changes; `u-boot.bin` hash reproduced identically, O) | U-Boot patches/build/recovery packing / 引导器构建与 Recovery 打包 |

The linux entry identifies the tools used for the recorded touch bundle (H, 2026-11-02). Earlier test/full cards used `3733107ff3b6e96151a6998971b21ce4d8bc8ba9`; touch was added using the ten-parameter contract and explicit guard=0 policy. Later documentation commits are not listed. These are not values to copy into your bundle configuration: record the actual full commits used in your own build. No release tag is asserted here.

linux 行标识 2026-11-02 触摸包使用的工具提交（H）。较早测试／完整卡的工具提交为 `3733107ff3b6e96151a6998971b21ce4d8bc8ba9`；触摸接入使用十参数合同并显式固定 guard=0。后续文档提交不列出。这些值不应直接照填到 bundle 配置；自己的构建须记录实际使用的完整提交。这里不声明发布 tag。

## Pinned upstreams / 锁定上游

| Input / 输入 | Version / 版本 | SHA-256 |
|---|---|---|
| Linux archive | 7.2.10 | `2feaa70453e91f541be6a5c9078d8168e081af45a3d9718054850538cff2ebf3` |
| U-Boot archive | v2026.10 | `f943a88389c8fd70a6b0d061a88267465e143c7ca6ea2b75837ab20f5b60c739` |
| Ubuntu desktop ARM64 ISO | 26.04.1 | `c54d196489d3c867975fb3bbb72ca52ec2e137456e305481f81096304e4d2517` |
| ISO `casper/minimal.squashfs` | 26.04.1 | `3b98356ba4c8be279429c11a76b75a254db74b446674982bbe8650b29d8d4457` |
| Ubuntu Base ARM64 tarball | 26.04 | `b2b46a37324ea1954e93f293fe6d7c2241daf2fc298c4022e6e4caceeed74cab` |
| Optional 1492-package comparison inventory / 可选包比较基线 | 1492 sorted package/version/architecture lines / 排序清单 | `8a24455a81c8c715fa71fe37e12c97b7c3dd4c17088270be7e093c8cf3c7e3dd` |

Linux source: [kernel.org archive](https://cdn.kernel.org/pub/linux/kernel/v7.x/linux-7.2.10.tar.xz). U-Boot source: [upstream v2026.10 archive](https://github.com/u-boot/u-boot/archive/refs/tags/v2026.10.tar.gz). Ubuntu input names and checks are in [sources.json](../tools/dby-bundle/rootfs/sources.json). Verify provenance as well as the digest. No baseline inventory is distributed; `--no-baseline` records your actual packages instead.

Linux 和 U-Boot 来源见上方上游链接；Ubuntu 输入名称／校验见 [sources.json](../tools/dby-bundle/rootfs/sources.json)。摘要核对不替代来源核实；包比较清单不分发，没有时用 `--no-baseline` 记录自己的实际结果。

## Recorded artifacts / 记录产物

| Artifact / 产物 | SHA-256 | Evidence / 证据 |
|---|---|---|
| `u-boot.bin` | `12213ff67f89ca67e528509cb0466006cfae55cae0af11009036f3c1c8516474` | O identical rebuild; H boot path / 离线重建一致、实机启动链 |
| Repacked `recovery.img` / 重打包镜像 | `43618116c35fa4e01101241499bd1e42bc360602805c5d7b2ffd2652c354fa11` | O assembly; H boot / 离线组装、实机启动 |
| Stock Recovery input / 原厂回退输入 | `d29b99d03fd777ef907914955281e5fc5157dc3df6f0145fa686e13d61f92326` | Recorded owner input, not a universal image / 所有者输入记录，非通用镜像 |
| Raw kernel `Image` / 未压缩内核 | `0f1d0a0802c651cd2cbe049c78e3c8d21b14d95b95fca588bb8bae5f89f509a7` | O build / 离线构建 |
| p1 `Image` (gzip) | `254ba57f03557a84718d5f04a265faf0dc5781fc46b5c845c3b18fb930bec4ad` | O bundle; H boot / 打包与实机启动 |
| `dby-w09.dtb` | `f2cf2c8fb6c8a170ad753062823760dd100e3fc915d6b60b4b428795f1a50cfd` | O identical rebuild; H boot / 重建一致、实机启动 |
| `nt36523-thp.ko` | `aad5faf247d3fdfbd011a175bc5344d94dee112768f2e7fb1487461d8efcdce9` | O build; H loading/events and R interaction with guard=0 / 离线构建、guard=0 加载与事件 H、交互 R |
| `initrd.img` | `2d7e712c214070d0259ef597efcf3d851330cdf1ab9d66f5b6fc6453ad46df3d` | O bundle; H boot / 打包与实机启动 |
| `dby-device.img` | `c16892a9366c8b8761d2a4543b51858505f53faa6b8d54fc5ff09ac1dc23be56` | O bundle; H mount/modules / 打包、实机挂载与模块 |
| `rootfs-test.tar.zst` | `019dd700e4459e484da5608bf467166a1a12bda6f4a77d98df290edf25811f21` | H minimal-card boot / 最小测试卡启动 |
| `rootfs.tar.zst` | `2aa69c32ea55f4ad0a725f4b909102817445770bb515738719a466dac44f7d42` | H full desktop + R interaction / 完整桌面与交互观察 |

The touch module depends on the [seven pinned plan inputs](touch-plan-preparation.md) and has vermagic `7.2.10 SMP preempt mod_unload aarch64`. Two builds from the same inputs/source in different directories had different debug sections, their debug relocations and build-id; all allocatable sections except the build-id were byte-identical (O). Cross-directory whole-module hashes are therefore not required to match. The initrd/device-image rows above describe the earlier no-touch bundle; they do not identify the later touch image.

触摸模块依赖[七项固定计划输入](touch-plan-preparation.md)，vermagic 为 `7.2.10 SMP preempt mod_unload aarch64`。同一输入／源码在两个目录编译时，调试节、相关调试重定位与 build-id 不同；除 build-id 外，所有可分配节逐字节一致（O），因此不要求跨目录整个模块的哈希相同。上方 initrd／设备镜像行记录较早无触摸 bundle，不代表后来的触摸镜像。

The full-card installation used `--no-readback`; boot evidence is not proof of a later full p2 file read-back. Toolchain recorded for U-Boot/kernel: aarch64-linux-gnu-gcc 15.2.0; DTB: dtc 1.7.2 with 36 warnings (O). Keep your own manifests, package inventory, source digests, final kernel configuration and tool versions. Changes to private firmware, memory/panel inputs or the recipe can legitimately change outputs and need separate acceptance.

完整卡安装用了 `--no-readback`，启动证据不能替代之后的完整 p2 文件回读。记录的工具链为 aarch64-linux-gnu-gcc 15.2.0；DTB 使用 dtc 1.7.2，有 36 条警告（O）。保留自己的 manifest、包清单、来源摘要、最终内核配置与工具版本。固件、内存／面板输入和配方变化可能改变结果，需单独验收。
