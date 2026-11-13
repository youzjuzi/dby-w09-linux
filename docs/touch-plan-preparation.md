# 触摸下载计划：从自己的 AFE 库到编译输入

语言：简体中文详细教程，含 English summary；[安装入口](install.zh-CN.md)／[English install](install.md)。新基线已用本流程的产物构建模块，并于 2026-11-02 在 guard=0 下通过短时实机检查（H + R）；下文输入重建、生成与构建核验仍属 O，三指手势、笔及长期稳定性为 U。

本教程补齐 NT36523 THP 驱动的私有计划输入。整个过程在 Linux 主机上离线运行：读取原厂库作为数据，生成 JSON 和 `thp-plan-table.h`，不加载原厂库，不连接设备。

**适用范围固定为已经分析过的 DBY-W09 原厂 AFE 版本。** 这不是通用协议自动分析器，也不适用于其他 NT36523 设备。协议规则来自项目对该版本的离线分析；固件切片、分区字段和反汇编证据由使用者自己的输入生成。数据不匹配时停止，不修改预期摘要绕过检查。

## 1. 准备自己的原厂文件和主机工具

先按[固件教程第 4 节](firmware-preparation.md#4-从匹配-afe-库离线提取触摸固件)，把匹配的 AFE 库保存在本机，例如 `local/firmware-raw/matching-afe.so`。不要上传原厂库或生成的数据。

本配方需要完整 AFE 库，只有提取后的固件 `.bin` 不够：事务审计还要从库中生成静态反汇编证据。

| 输入 | 要求 |
| --- | --- |
| AFE 库大小 | 624352 字节 |
| AFE SHA-256 | `4d3e05edc9a69769e1af20e1560ff88458229898191d90f33bd90d6eaff17bb0` |
| 内嵌固件 SHA-256 | `b7633e1ef2a271348d2142950e6235238bb4997b98b010bae0df6bc4a4af5b6b` |
| 主机工具 | Python 3.9 或更新版本；带 AArch64 支持的 GNU objdump **2.46** |

这里固定 objdump 版本，是因为历史事务 JSON 包含反汇编文本和行号，格式变化也会改变摘要。工具最终仍核验全部输出摘要，版本号相同不代表一定兼容。不会自动下载或安装依赖；本地核验使用 Python 3.14.4 和 GNU Binutils for Ubuntu 2.46。

以下命令均在 **dby-w09-linux 仓库根目录**执行，输入路径按自己的实际位置调整。输出目录必须是 `local/touch-plan/` 下尚不存在的新目录。

`local/` 被 Git 忽略，所有数据须保持私有。若将数据保存在仓库外，可预先让被忽略的 `local/touch-plan` 符号链接指向自己的外部私有根目录；两生成器均解析此输出根，不能覆盖已有目录或结果。`--afe` 也可改为仓库外自有库的绝对路径，下方仓库内路径仅是示例。独立模块编译与打包输入使用实际私有路径，见[可选触摸接入](install.zh-CN.md#31-启用触摸可选)。

```sh
umask 077
python3 --version
aarch64-linux-gnu-objdump --version
sha256sum local/firmware-raw/matching-afe.so
python3 -B -I drivers/nt36523-thp/prepare-plan-inputs.py \
  --afe "$PWD/local/firmware-raw/matching-afe.so" \
  --output-dir "$PWD/local/touch-plan/owner-a"
```

如果交叉工具使用其他名称，可增加 `--objdump /path/to/aarch64-objdump`。不要传入来自原厂镜像的未知可执行程序。

## 2. 确认计划输入生成成功

最后必须显示 `VERIFIED_OFFLINE`，退出码为 0，且存在 `local/touch-plan/owner-a/preparation.json`。仅出现中间审计输出不代表成功。

```text
local/touch-plan/owner-a/
├── audit/                    # 私有原库副本、固件、反汇编及中间审计结果
├── inputs/
│   ├── stock-nt36523-thp.bin
│   ├── spi-transactions.json
│   ├── write-whitelist.json
│   ├── forbidden.json
│   ├── read-allowlist.json
│   ├── plan.json
│   ├── verification.json
│   └── nt36523-format.h      # 历史解析器校验输入，不是当前驱动头文件
└── preparation.json         # 七项固定摘要核对通过的记录
```

这七项输入的固定摘要保留在[生成器](../drivers/nt36523-thp/gen-plan-header.py)的 `PINS` 中。成功结果包含 46 步：41 次写、5 类读；其中 29 个 SRAM 数据块，共 131984 字节；总读次数上限为 214。读失败中止、读改写依赖和地址限制保持原规则。

`plan-tools/nt36523-format.h` 仅用于保持历史解析器输入的可追溯性。**不要把它的旧控制表用于设备操作或复制到模块构建目录。** 当前驱动使用生成后的计划表。

## 3. 生成私有头文件

```sh
umask 077
python3 -B -I drivers/nt36523-thp/gen-plan-header.py \
  --inputs "$PWD/local/touch-plan/owner-a/inputs" \
  --output-dir "$PWD/local/touch-plan/owner-a/generated"
```

成功后得到 `generated/thp-plan-table.h` 和 `generation.json`。生成器会再次检查七项摘要及计划结构；不接受手写空表或其他版本的 JSON。

## 4. 准备独立模块构建目录

```sh
mkdir "$PWD/local/touch-plan/owner-a/module"
cp drivers/nt36523-thp/*.c drivers/nt36523-thp/*.h \
   drivers/nt36523-thp/Makefile "$PWD/local/touch-plan/owner-a/module/"
cp local/touch-plan/owner-a/generated/thp-plan-table.h \
   "$PWD/local/touch-plan/owner-a/module/"
```

然后使用已准备好的匹配内核构建树编译模块，例如：

```sh
KERNEL_BUILD=/absolute/path/to/prepared-kernel-build
make -C "$KERNEL_BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  M="$PWD/local/touch-plan/owner-a/module" modules
```

`KERNEL_BUILD` 是与你的内核配置、版本和符号版本一致的构建目录；仅解压源码通常不够。相关说明见[主 README](../README.md)及内核仓库的构建教程。模块的运行时固件名是 `novatek/nt36523-thp.bin`，固件目录与设备包布局继续按[固件准备教程](firmware-preparation.md)处理。

已用同一份生成头文件在匹配的 Linux 7.2.10 构建树编译模块（O），并在设备包中固定 guard=0，通过新基线的一次短时设备检查与单指／边缘四角／两指缩放观察（H + R，2026-11-02）。三指手势、笔、长时间表现和 guard 功能本身仍未验证；这些结果不推广到其他输入。上机后的[触摸排错检查只读](install.zh-CN.md#触摸缺失)（[English](install.md#touch-missing)），不通过写 sysfs 开启未验收功能。

## 失败时怎么处理

- `AFE size/SHA-256 mismatch`：文件或版本不匹配；回查自己的原厂输入，不换其他机型固件，不改校验值。
- objdump 版本或 `audit SHA-256 mismatch`：检查工具版本、AArch64 支持和输入。程序固定使用 `LC_ALL=C`；仍不一致时保留私有日志分析，不能将部分输出当成有效计划。
- `output already exists` / `output_exists`：使用另一个新目录，保留原目录。失败可能留下私有中间文件；不会覆盖或自动删除它们。
- 找不到 `thp-plan-table.h`：先完成第 2、3 节，再准备模块目录；不要给驱动加空白替代文件。

`audit/` 含原厂库和反汇编，`inputs/`、`generated/` 含原厂派生数据，全部留在被忽略的 `local/` 下。工具源码的许可不自动授予这些输出的再分发权；使用者应确认自己的获取和使用权限，并自行承担实际安装、运行风险。

## 来源与本地核验范围

公开脚本来自项目协议审计生成逻辑、读限制与计划校验、现有头文件生成器；公开整理只适配路径、补齐输入重建和使用说明。`build-audit.py` 保存项目编写的分析规则及证据地址，不包含原厂固件或反汇编文本；这些内容在本机生成。生成数据中的历史标识和说明字符串是固定摘要契约的一部分，保留原值；读者不需要取得内部开发目录。

离线核验（O）：从匹配 AFE 两次重建全部七项输入，摘要均与 `PINS` 完全相同，头文件也逐字节一致。模块已编译并合入设备包；实机边界是上述 guard=0 的短时 H + R 记录，不能把离线生成或编译成功当作实机等价、长期稳定或其他设备支持。

English summary: run `prepare-plan-inputs.py` with your own exact matching AFE library, Python 3.9+ and GNU AArch64 objdump 2.46. Keep outputs under private ignored `local/touch-plan/`, which may link to your external private output root. All seven pinned hashes must match before header generation; two reconstructions matched (O). The generated module was built and a guard=0 bundle passed a short 2026-11-02 device/interaction check (H + R). Three-finger gestures, stylus, long-term stability and the guard feature remain U. See [optional setup](install.md#31-enable-touch-optional) and [read-only troubleshooting](install.md#touch-missing). This remains a fixed offline recipe, not a general protocol extractor or proof for other inputs.

历史内部标识没有外部含义，读者无需查找对应资料。本轮不改代码中的字段值或固定哈希；仅在发布后另立任务，证明生成计划表与 nt36523-thp.ko 均逐字节不变后才考虑替换。 / Historical internal identifiers have no external meaning and require no external reference lookup. Their field values and pinned hashes are unchanged here; replacement is deferred to a separate post-release task requiring byte-identical generated plans and nt36523-thp.ko.
