# DBY-W09 TFA9874 bring-up

The original O/U stage below is historical; later one-tablet H + R evidence and current limits are recorded at the end. / 下文最初 O／U 阶段为历史记录，后续单机 H + R 与当前限制见末节。

This board-specific driver supports four revision `0x0c74` amplifiers on Primary
TDM0. It is experimental: actual probe, clocks, sound, temperature and microphone
coexistence remain U. There is no proprietary excursion or thermal protection.

The global Safe Enable Switch defaults off. Attenuation Step accepts only 9..15
(default 15); Input Atten 6dB defaults on; Active Mask defaults 15. Initialization
also uses step 15, preserving the owner table's low bits. Playback selects
the project input-attenuation policy. The interrupt enable register remains zero. Mute, remove and shutdown
attempt the complete power-off sequence. I2C errors revoke the global arm and
attempt shutdown on every present chip, including after a failed write. Software
cannot guarantee hardware shutdown if I2C itself is broken.

One mutex serializes controls, stream callbacks and the instance table. Only chip
zero registers card controls, without codec prefixes. Other chips defer until
chip zero performs the single shared active-high reset. Removal revokes the
arm globally; reprobing does not pulse reset again during the module lifetime.
Channels 1..2 select paired stereo slots; 3..4 select independent slots as
specified by the board bring-up plan. Actual mono/three-channel acoustics remain U;
the diagnostic helper uses stereo only.

## Preparing configuration tables / 准备配置表（私有输入）

The checked-out source tree contains no original register sequences. The project
implementation and extraction tool are GPL-2.0-only; owner configuration data is
not distributed. Provenance and redistribution permission for that data remain
unresolved. Obtain the four original audio amplifier configuration files from
your own device and place them together in a private directory. This tool does
not grant any rights to those files, the generated header, or modules containing
that data. Keep the generated header and resulting modules private.

当前源码树不含原厂寄存器序列。驱动与生成工具为 GPL-2.0-only；所有者数据不分发，
其来源与再分发许可并未因此核清。请所有者从自己的设备取得四份原厂音频功放配置文件，
放入同一个私有目录。工具不授予输入、生成头文件或含这些数据的模块任何权利；
生成头文件和产出模块均须私有保存。

From the Linux repository root, set `TFA_INPUT_DIR` to your private directory
containing `tfa9874_0_configs.xml` through `tfa9874_3_configs.xml`, and
`TFA_TABLES_DIR` to a separate, absolute output directory. Both paths are chosen
by the caller. The output file must not exist. All four normalized XML digests
are pinned; changed or unsupported inputs are refused without printing values.
There is no bypass switch, download, or device access.

在 Linux 仓库根目录执行；`TFA_INPUT_DIR` 指向存放上述四个文件的私有目录，
`TFA_TABLES_DIR` 是调用者指定的独立绝对输出目录。输出文件必须尚不存在。
四份 XML 的规范化摘要均固定校验；任何改动或不支持的输入均拒绝，不回显原值，
无绕过开关、下载或设备访问。

```sh
: "${TFA_INPUT_DIR:?Set an absolute private input directory}"
: "${TFA_TABLES_DIR:?Set an absolute private output directory}"
mkdir -p "$TFA_TABLES_DIR"
python3 -I -B tools/tfa9874-extract-tables.py \
  --input-dir "$TFA_INPUT_DIR" \
  --output "$TFA_TABLES_DIR/dby-tfa9874-tables-private.h"
```

Copy the module sources to a **new**, separate build directory; use the same
prepared ARM64 kernel and toolchain as Image. The matching q6afe, q6afe-dai and
sm8250 TDM patches are also required. Do not build in the source checkout.

将模块源码复制到**新建**的独立构建目录，使用与 Image 相同的已准备 ARM64 内核及
工具链，并保留配套 TDM 补丁；不要在源码目录构建。

```sh
: "${TFA_MODULE_DIR:?Set a new absolute module build directory}"
: "${KERNEL_BUILD:?Set the prepared kernel build directory}"
cp -R drivers/dby-tfa9874 "$TFA_MODULE_DIR"
make -C "$KERNEL_BUILD" M="$TFA_MODULE_DIR" ARCH=arm64 \
  CROSS_COMPILE=aarch64-linux-gnu- TFA_TABLES_DIR="$TFA_TABLES_DIR" modules
```

Missing `TFA_TABLES_DIR` or a missing header fails with a Makefile message pointing
to this README; direct inclusion also reports `Missing private TFA tables`.
There is no built-in fallback. Keep the header outside the module source copy
so a stale local header cannot override the explicitly supplied directory.

未设置变量或缺少头文件时 Makefile 明确报错并指向本页；直接编译核心也会提示缺少
私有表，不存在内置回退。头文件须放在模块源码副本外，防止误用旧的本地文件。

Host tests use `tests/synthetic-tables.h`, marked **SYNTHETIC, not vendor data**.
The fixture refuses kernel compilation and is never a module build fallback.
Run both the core invariant test and the regmap fault harness with no owner inputs:

主机测试使用明确标记的合成夹具，无需任何私有输入；夹具禁止内核编译，不能作为
模块构建回退。测试只证明离线逻辑，不证明实机声学安全。

```sh
python3 -I -B -m unittest discover -s tools/dby-bundle/tests -p 'test_tfa*.py'
```

## Desktop speaker policy (offline)

The hardware control range is 9..15; steps 7 and 8 are rejected. The driver
still starts at 15 with Safe Enable off and input attenuation on; the initialization policy is unchanged. UCM uses step 9, input attenuation off and
mask 15, arming only when Speaker is enabled. The bounded tone helper still
defaults to step 15 and accepts 9..15. There is still no protection algorithm;
neither these limits nor host tests establish safe long-duration playback.
Device validation is separate. The desktop speaker format rule is documented
in [audio capture and playback](../../docs/audio-capture.md).

## Later device evidence and limits / 后续设备证据与限制

The O/U statements above describe the original bring-up stage. Later short desktop sessions on one tablet validated four speakers with S16LE (H + R, 2026-11-07); see the hub audio notes. These observations do not validate prolonged loud playback. There is no speaker protection algorithm; gain limits are not protection and high volume over long periods may damage the speakers. The source/provenance statement above remains unresolved and is not cleared by device operation.

上文 O／U 为最初 bring-up 阶段；后续一台平板的 S16LE 桌面四喇叭短测通过（H + R，2026-11-07），详见音频说明。没有喇叭保护算法，限制增益不等于保护，高音量长时间播放可能损伤喇叭；设备出声不关闭上文来源门禁。
