# Status and evidence / 状态与证据

[English home](../README.md) · [中文首页](../README.zh-CN.md) · [Install](install.md) · [安装](install.zh-CN.md)

Date / 日期：**2026-11-18**。This page is bilingual; current and historical results are separated below. / 本页中英双语，当前基线与历史结果分开列出。

| Class / 级别 | Meaning / 含义 |
|---|---|
| H | Recorded checks on the real device, outputs reviewed by the maintainer / 实机检查记录，维护者已审阅输出 |
| R | User observation of interaction or appearance / 用户对交互或外观的观察 |
| O | Offline build, tests, static review or image-file checks / 离线构建、单测、静态审查或普通镜像文件检查 |
| U | Not verified, not built or not implemented as specified / 未验证、未构建或尚未实现 |

Evidence is from one DBY-W09 with HarmonyOS 4.2.0.213 and an unlocked bootloader. H is not proof for other devices, input combinations or every public commit. Recorded source commits are listed in [versions](versions.md). An O result is never promoted to H.

证据来自一台 HarmonyOS 4.2.0.213、Bootloader 已解锁的 DBY-W09。H 不能证明其他设备、输入组合或每个公开提交均成立；记录中的源码提交见[版本页](versions.md)。O 不等于 H。

## R4 fresh rebuild / R4 从零重建

**2026-11-15: the maintainer passed a fresh installation from a blank 16 GB card following the public documentation (H + R), bundle `20261115-1`. / 维护者于 2026-11-15 从空白 16 GB 新卡照公开文档全新安装通过（H + R），包 `20261115-1`。**

Source: the user supplied Claude’s read-only SSH checks (H) and their own interaction observations (R). Codex registers that supplied evidence; it did not access the device or independently review raw SSH output for this update. / 来源为用户提供的 Claude SSH 只读核对（H）及用户交互观察（R）；Codex 本次仅补登，没有访问设备或独立审阅 SSH 原始输出。

| R4 check / 检查 | Reported result / 本次结果 | Class / 级别 |
|---|---|---|
| System / 系统 | Running; 0 failed system services / running，失败系统服务 0 个 | H |
| Wi-Fi seed / 网络种子 | Automatically connected / 自动联网 | H |
| Display / 显示 | Connected; all four panel supplies present; no `PLL lock failed` or DPU errors / 已连接，四路面板供电均在，无 PLL 锁定失败或 DPU 报错 | H |
| Desktop, screen and brightness / 桌面、开关屏、亮度 | Normal desktop and brightness; 10 screen off/on cycles at 3-second intervals passed / 桌面、亮度正常，隔 3 s 开关屏 10 次正常 | R |
| Touch / 触摸 | Loaded at boot and reloaded after screen cycling (H); interaction normal (R) / 开机加载及开关屏后重载成功，用户触摸正常 | H + R |
| Audio / 音频 | Autostart normal and soundcard registered (H); speakers and microphone normal (R) / 自启正常、声卡已注册，喇叭和麦克风正常 | H + R |
| Power key and cover / 电源键、合盖 | Power-key daemon normal (H); long press and cover interaction normal (R) / 电源键守护进程正常，长按及合盖正常 | H + R |
| Charging and battery / 充电、电量 | Charger and fuel-gauge drivers loaded (H); charging display normal (R) / 充电和电量驱动已加载，充电显示正常 | H + R |
| Vibration / 震动 | Device registered (H); vibration normal (R) / 设备已注册，震动正常 | H + R |
| Bluetooth / 蓝牙 | hci0 started; no pairing result supplied for this card / hci0 已启动，本卡未提供配对结果 | H for controller / 控制器 H |
| Watchdog / 看门狗 | Normal / 正常 | H |
| Venus / 视频 | Modules and firmware present; no decode result supplied for this card / 模块和固件均在，本卡未提供解码结果 | H for presence / 存在性 H |
| Time / 时间 | Expected for a fresh card: no saved offset at first boot, then chrony corrected time after network connection and the offset was saved / 符合新卡预期：首次开机无已存偏移，联网后 chrony 校准、偏移已保存 | H |

Clean main source commits, the fresh full rootfs (1493 packages), and bundle build/verification (O) remain recorded in [versions](versions.md). The existing Recovery/U-Boot was retained, without rebuilding or reflashing. Private inputs were reused: **their extraction was not repeated and retains its original evidence level**. **Independent third-party blank-card reproduction remains U.** / 构建与校验仍为 O；保留现有 Recovery/U-Boot，未重编、未重刷。私有输入提取未重做、保持原证据级别；第三方独立复现仍 U。

The feedback does not separately report full-card verify/read-back, a 120 Hz reading, USB-C headset use, Bluetooth pairing, Venus decoding, or a later offline boot using the saved offset; these checks remain unverified for this R4 record. Installation success does not extend to rapid-toggle behavior, suspend or long-term reliability. No Bluetooth address or Wi-Fi name is recorded here. See the reusable [owner checklist](fresh-install.md). / 本次未单独提供完整卡 verify／回读、120 Hz 读数、USB-C 耳机、蓝牙配对、Venus 解码及后续离线开机使用已存偏移的结果，这些分项仍未验证；不扩展为快速连按、挂起或长期可靠性结论。不记录蓝牙地址或 Wi-Fi 名称。

Earlier one-device evidence, not R4 acceptance: DP3 on 2026-11-15 restored boot text, removed the reported boot PLL-lock error and passed six screen cycles at intervals of at least 3 s (maintainer H + R record); rapid-toggle stripes remained in that snapshot and were subsequently resolved by kernel patches `0017` + `0018` (2026-11-18, see the Display row and Known issues below). VR1 vibration passed three 300 ms pulses (H + R). The shared-SDAM RTC hypothesis failed with a 2053 wall clock while its gate stayed read-only; it was reverted. Bundle `20261102-37` uses the original p2 offset services and the maintainer reports normal time (H). / 以上属于旧卡；R4 新卡结果与边界单列于上表（关于快速连按条纹，已于 2026-11-18 经内核补丁 `0017`–`0018` 解决，见下方“显示”与“已知问题”）。

## Power profiles / 电源三档

| Feature / 功能 | Implementation and scope / 实现与范围 | Class / 级别 |
|---|---|---|
| Three power profiles / 电源三档 | tuned-ppd with three DBY-W09 tuned configurations: power-saver caps big/prime CPU and GPU maxima, balanced restores hardware ranges, performance raises big/prime CPU and GPU minima while retaining schedutil. CPU0–3 keep their full range; GPU uses simple_ondemand. No battery-triggered switching, suspend or refresh-rate changes. Existing full cards need a p1 update and owner installation of tuned-ppd; the full rootfs recipe replaces the old daemon. / tuned-ppd + 三个本机配置：省电限制大核／超大核及 GPU 上限，平衡恢复全范围，性能提高大核／超大核及 GPU 下限；CPU 均保留 schedutil，小核全范围，GPU 使用 simple_ondemand。不按电池自动切档，不改挂起或刷新率。旧 full 卡更新 p1 后由用户安装 tuned-ppd，新 full 配方替换原守护进程。 | H + R, PP1 (2026-11-15, one tablet, bundle `20261115-3`): all three profiles switched from GNOME and over D-Bus, CPU/GPU limits read back equal to the configuration, no TuneD errors; the selection survives reboot (power-saver and balanced each checked once). Long-term temperature under performance not observed. / 一台平板、包 `20261115-3`:GNOME 与 D-Bus 切三档,CPU/GPU 限频读回与配置一致,TuneD 无报错;重启后保持(省电、平衡各验证一次)。性能档长时间温度未观察。 |

## Rebuilt baseline / 新工程基线

Source: maintainer-reviewed minimal-card, seed, network, desktop and touch device checks on **2026-11-02**, plus the user's interactive observations. Earlier provisional notes saying full rootfs/network power-up were pending have been superseded by these results.

来源：维护者审阅的 **2026-11-02** 最小卡、种子、网络、桌面及触摸设备检查，以及用户交互观察；早期“full 未实跑／无线供电待测”的暂定记录已被这些结果更新。

| Area / 项目 | Result and boundary / 结果与边界 | Class / 级别 |
|---|---|---|
| Boot / 启动 | Test and full cards reach Linux 7.2.10 and Ubuntu through Recovery/U-Boot; root moves to SD p2 / 两种卡均启动并换根到 p2 | H |
| Device image / 设备镜像 | squashfs and selected module mounts present; backlight/gauge/display modules loaded / 设备镜像、模块挂载与相关模块加载 | H |
| PCIe Wi-Fi power / 无线上电 | Power-sequencing module autoloads; ath11k sees the controller and firmware / pwrseq 自动加载，ath11k 识别硬件与固件 | H |
| USB network / SSH | USB network root public-key login / USB 网卡 root 公钥登录 | H |
| Wi-Fi / DNS / time | Rebuilt test image auto-connects using seed; DNS, HTTP and NTP work without the earlier manual resolver repair / 新测试镜像无需手修即可自动联网、解析与同步时间 | H |
| Wi-Fi 6 (HE) / Wi-Fi 6 链路 | One tablet joined a 5 GHz WPA2 phone hotspot with an 80 MHz HE 2×2 link (negotiated rate, not throughput); firmware regulatory rules disable 2.4 GHz channels 12–14; a 2.4 GHz hotspot on the same phone was not scanned, cause unconfirmed / 一台平板连上 5 GHz WPA2 手机热点，协商 80 MHz、HE、2×2（链路速率，非吞吐）；固件法规规则禁用 2.4 GHz 信道 12–14；同一手机 2.4 GHz 热点未被扫描到，原因未证实 | H + R, 2026-11-03 |
| Display / GPU | DSI connected, full desktop uses msm/Adreno GBM/EGL; selectable 30/60/120 Hz modes (`0014`) and brightness control; alarm-IRQ kernel recovery (`0013`); `0017` (slave C-PHY `DSICLK_SEL` restore) + `0018` (12 ms post-VSN bias settle) resolved wake black screens and rapid-toggle stripes on one tablet (`46/46` post-boot wakes/switches including `38` rapid `13–68 ms` presses, `0` ESD resets, bundle `20261118-5`); same-tablet static desktop draw at `855/1023` brightness (`P4`, 30 s discharging each): `4.177 / 4.302 / 4.576 W` median for `30 / 60 / 120 Hz` (`120→60` −0.274 W, `60→30` −0.125 W, `120→30` −0.399 W) / 显示连接、Adreno 硬件渲染、30/60/120 Hz 三档切换（`0014`）与亮度调节；报警脚内核自动复位（`0013`）；`0017` + `0018` 在一台平板解决唤醒黑屏与快速连按条纹根因（包 `20261118-5` 开机后 46 次唤醒／切换含 38 次 `13–68 ms` 极限连按 0 次 ESD 复位）；同机 `855/1023` 亮度静止桌面 30/60/120 Hz 整机功耗中位 `4.177 / 4.302 / 4.576 W`（`120→30 Hz` 省 `0.399 W`） | H + R (`0017`–`0018`), H (`P4`), 2026-11-18; multi-day stability and one 2026-11-16 idle-screen reset requiring touch U / 多日稳定性与 11-16 单次静止复位需触摸仍 U |
| Mouse / 鼠标 | USB receiver mouse used in full desktop / 完整桌面使用 USB 接收器鼠标 | H + R |
| Bluetooth / 蓝牙 | Controller UP and visible to BlueZ/GNOME when the DTB carries an owner-supplied `local-bd-address` (one tablet, bundle 20261102-4, 2026-11-03); without it the controller stays unconfigured (DOWN RAW) and BlueZ shows none. One Bluetooth mouse paired and worked (user report, 2026-11-03); reconnection after reboot/sleep, other device types and A2DP not tested on this baseline / DTB 带所有者提供的 `local-bd-address` 时控制器 UP，BlueZ／GNOME 可见；缺该属性时控制器未配置（DOWN RAW），BlueZ 看不到。一只蓝牙鼠标配对并可用（用户报告）；重启／休眠后回连、其他设备类型和音频未测 | H + R for controller; R for one mouse; U for the rest / 控制器 H + R、鼠标 R、其余 U |
| GDM / GNOME | Login, user creation and desktop reached; first-setup defect below / 登录、创建用户、桌面已到达，首次向导有下述缺陷 | H + R |
| Key / Wi-Fi seed / 公钥与网络种子 | Root key applied; NetworkManager connection and regulatory request checked / 公钥、连接及法规请求已核对 | H |
| Other seed / 其余种子 | Timezone retained when omitted; APT mirror applied/restored; 60 Hz selected on a later boot, removal returns 120 Hz on a later boot / 时区保留、APT 镜像恢复、刷新率延迟生效 | H |
| Extra packages / 额外包 | Boot request misses Wi-Fi readiness; restarting dby-seed after connection installed the test package / 开机错过联网，联网后重启服务安装成功 | H, defect / 有缺陷 |
| Safe mode / 安全模式 | msm/panel denied before init; expected black screen, SSH/network/systemd retained; removing seed restores modules / 显示抑制、黑屏但 SSH 等正常，删除后恢复 | H + R, on/off including touch / on/off 含触摸，2026-11-02/03 |
| Full install read-back / 完整卡回读 | Initial desktop install used `--no-readback`; later full reinstall and full verify passed / 首次安装跳过回读，后续完整重装与完整 verify 通过 | H + R within the reinstall scope below / 限下述重装范围 |
| Touch / stylus / 触摸与笔 | Module/firmware load and up to five contacts recorded; single touch, edges/four corners and pinch observed with guard=0 / guard=0 下模块与固件加载、最多五点有记录，单指、边缘／四角及缩放有观察 | H + R, 2026-11-02; three-finger gestures/stylus/long-term behavior U / 三指手势、笔与长期表现 U |
| Charge indicator / 充电指示灯 | `dby-chg` kernel LED triggers, IRQ sampling plus 30-second periodic work; off on unplug/remove/shutdown, red below 10%, green at Full or ≥90%, otherwise yellow. / 内核触发器接管红绿灯，移除用户态服务。 | H + R (2026-11-15, one tablet, bundle `20261115-7`): three charger IRQs registered with only their enable bits set; LED and power-connected sound follow plug/unplug immediately; tray icon switches to charging within about 2 s; watchdog pets every 30 s with no bark and no write errors. Shutdown turn-off of the kernel-trigger LEDs not yet observed. / 一台平板、包 `20261115-7`:三中断仅开对应使能位;插拔时灯与提示音即时,托盘图标约 2 s 内变充电;每 30 s 喂狗、无 bark、无写错误。内核触发器版本的关机灭灯尚未目测。 |
| USB-C charging / 充电 | Integrated driver `dby_chg` (bundle `20261102-10`) autoloads, pets the charger watchdog so charging no longer stops after ~2 min, shows standard status to the desktop, and stops/resumes battery charging through the enable bit without replug; repeated plug/unplug did not consume the toggle budget (H + R, one tablet, 2026-11-05). Wall charger: sustained charging. PC USB port: about +0.2 A with screen off, net discharge with screen on because input is roughly SDP-class (H). **Not run for days; long-term risk is unknown.** Natural FULL/recharge, suspend, multi-temperature thermistor calibration and the unload-restore exception remain U. Watchdog bite still requires USB replug. No fast charging, PD or input-current control. / 驱动自动加载并持续喂狗，充电不再约2分钟中断；桌面状态正常，可通过使能位停充/恢复且无需重插；反复插拔不消耗翻转预算。壁充持续充电；电脑口屏灭约+0.2 A、屏亮净放电。**未连续运行数日，长期风险未知**；自然FULL/再充电、挂起、多温度点与卸载恢复例外仍为 U。See [driver notes](../drivers/dby-chg/README.md). | H + R, one tablet, 2026-11-05; long-term / suspend / FULL U |
| Audio / speakers / 音频喇叭 | Kernel autoload through device-tree modaliases and deferred probe: on bundle `20261116-13`, six cold boots and two warm reboots on one tablet all brought up desktop speakers and the four-channel microphone without manual steps, with no deferred devices or AFE vote errors (H + R, 2026-11-17). Configuration mounts precede the display manager; a sound-card udev event runs retained HiFi/Mic initialization and WirePlumber refresh. Safe mode no longer prevents audio module loading or ADSP boot. Four microphones and four desktop speakers were validated earlier; no speaker protection algorithm. See [audio notes](audio-capture.md). | Autoload H + R (8 boots, one tablet, 2026-11-17); capture and speakers H + R; long-term reliability U |
| Power key / cover switch / 电源键与合盖 | Tap blanks and the key wakes the screen; hold opens GNOME's shutdown dialog; closing the magnetic cover blanks and opening wakes (suspend never requested; the pen does not blank) / 短按关屏、按键唤醒，长按弹出 GNOME 关机对话框，合上磁性皮套关屏、打开亮屏（不请求休眠，笔不误关）。50/50 off/on cycles and 11 cover cycles without failure; screen-off draw about 0.84–0.91 W / 50/50 轮开关屏、11 次合盖无失败，关屏待机约 0.84–0.91 W | H + R, one tablet, bundles `20261102-20`–`-24`, 2026-11-08; suspend, touch wake, login screen and long-term stability U / 休眠、触摸唤醒、登录界面与长期稳定性 U |
| Hardware video decoding / 硬件视频解码 | Venus driver (`venus_core`, loaded manually with `modprobe`; not autoloaded) on bundle `20261102-28`: firmware accepted by TZ, `/dev/video0` encoder and `/dev/video1` decoder appear. HEVC and VP9 (profile 0) decode correctly; 1080p VP9 decodes in about 2 s hardware vs 8.5 s software. H.264 decodes continuously, but at end of stream or seek the firmware reports a system error, the driver recovers after about 3 s and the last ~17 frames are lost. 1080p30 VP9 to a null sink: about 0.13 W lower than software decoding (3 alternating pairs; no rendering or compositing). Browsers cannot use it (no V4L2 decoder in the official Chrome). Not tested: long playback, desktop player integration, behaviour across suspend. The firmware is an owner-extracted private input, not distributed; source and redistribution gate open. / Venus 驱动（`venus_core`，需手动 `modprobe`，不自动加载）：固件被 TZ 接受，出现编码器与解码器设备。HEVC、VP9 正常；1080p VP9 硬解约 2 s、软解 8.5 s。H.264 连续解码正常，流结束或 seek 时固件报错、约 3 s 后恢复并丢最后约 17 帧。1080p30 VP9 输出到空设备：硬解比软解低约 0.13 W（不含渲染与合成）。浏览器用不上。未测：长时间播放、桌面播放器整合、休眠。固件为所有者提取的私有输入，不分发，来源与再分发门禁未关闭。 | H, one tablet, 2026-11-10; H.264 flush defect; long playback / player / suspend U |
| Device-package update / rollback / 设备包更新回滚 | Same-version no-op, downgrade, upgrade and two backup rollbacks; preserves p2/seed / 同版本不写、降级、升级、两次备份回滚，保留 p2／种子 | H + R, 2026-11-02; one SD / tablet, bundles `20261102-2` / `20261102-3`, kernel `7.2.10` / 一卡一机、两个同内核包 |
| Seed / safe-mode editor / 种子安全模式编辑 | Validated commands, private backup/restore, p1 only / 命令校验、私有备份恢复，只访问 p1 | H: wizard/timezone / 向导时区；H + R: safe-mode, derived-PSK write/read-back and restore / 安全模式、派生写入自检与恢复；其余 O/U |
| Reinstall system / 重装系统 | Existing p2 only, p1/seed preserved, no data backup / 只重做现有 p2，保留 p1／种子，不备份数据 | H + R, full system / 完整系统，2026-11-02/03 |
| Menu / data backup / restore / 菜单及数据备份恢复 | Unimplemented / 未实现 | U |

Masking `dby-w09-audio-config.service` and `dby-w09-audio-ucm.service` disables configuration only; it does not stop kernel module loading or ADSP startup. Full onboard-audio disablement requires an owner-created blacklist and a reboot. Safe mode also only skips the two configuration units. See [exact disable/restore instructions](audio-capture.md).

### Device-package maintenance / 设备包维护

**H + R, 2026-11-02:** one SD card and one tablet, two bundles `20261102-2` /
`20261102-3`, both kernel `7.2.10`. The owner exercised no-op, explicit downgrade,
upgrade and two rollbacks from separate host backups; update boots worked and
touch returned after upgrading. MANIFEST/device-image hashes were checked after
rollback, no UBOOT.new/old remained, and seed, Android/other p1 directories and p2
were preserved. / **H + R**：限一卡一机、两个同内核设备包；同版本、显式降级、升级、两次不同备份回滚及更新后启动通过，升级后触摸恢复；回滚哈希正确、无暂存残留，种子、其他 p1 目录与 p2 保留。

An unclean card removal left p2 `needs_recovery`; `ro,noload` exposed an older
rootfs view and the old generic kind error. The owner replayed the journal with
`e2fsck -p` on unmounted p2 and retried successfully. New read-only detection
returns `E_INSTALL_P2_RECOVERY` before mounting p2; it never repairs/replays
itself. Cleanly shut down before taking out the card. / 曾直接拔卡导致 p2 日志未重放，只读视图旧、kind 报错误导；用户在卸载 p2 后手动运行 `e2fsck -p`，重试恢复。新检测只读拒绝并说明原因，工具不自行修复；取卡前先正常关机。

The recorded 2026-11-02 owner retest on one SD card covered `info`, `verify --p1-only` and dry-run (R). The record does not separately establish `info --full` or injected abnormal paths on a real card: those and journal/contract-error test coverage remain O. Full verify compares p2 with the original archive and suits a freshly installed, not-yet-booted card; p1-only after use does not establish p2 integrity. / 2026-11-02 记录的一张真卡复验覆盖 info、verify --p1-only 与 dry-run（R）。未单独证实 info --full 或注入异常路径已真卡复验，这些及日志／合同错误测试仍为 O。完整 verify 适合刚装好未开机的卡；用过后 p1-only 成功不代表 p2 完整性。

**U / unsupported:** real-card power loss or interruption during switching (only
image fault simulation O), cross-kernel updates (format accepts only `7.2.10`),
and p2 updates (not performed). Menu and
user-data backup/restore commands remain unimplemented. FAT directory renames
are not power-loss safe. / 真卡中断恢复未验证，跨内核格式不支持，update 不更新 p2；菜单与用户数据备份恢复未实现；FAT 重命名不保证掉电安全。

Seed defaults to plaintext with two no-echo entries; `--derived-psk` is explicit and WPA2-only (not WPA3/SAE or mixed mode). On 2026-11-02, `seed set` wrote a derived PSK and passed its own read-back check, and `seed restore` restored the original seed on one SD card / one tablet (H + R). On the one tested router, the correctly derived PSK could not connect; restoring the original plaintext passphrase connected immediately. WPA3/SAE or mixed-mode negotiation is a suspected cause, not verified on the router. On 2026-11-02/03, one SD card and one tablet with bundle `20261102-3` and kernel `7.2.10` provided these additional results: `seed wizard` rejected mismatched passphrases in dry-run without writing, and a real timezone-only edit showed only that change, passed read-back and created a backup (H). `seed set --timezone` was written and confirmed after boot with `timedatectl` for two timezones (H). A mistyped target confirmation was refused without writing (H). `safe-mode on/off` blocked then restored msm, panel and touch modules across boots; while active the backlight was lit without an image and USB networking/SSH worked (H + R). These results do not establish other devices or networks. Other `seed set` settings (mirror/rate/packages/replacement or added public keys) still need real-card retesting; derived-PSK connectivity on confirmed WPA2-only networks remains U (command logic O). The new wizard label-selection rules are tested offline (O), awaiting real-card retest.

种子默认明文，交互两次无回显；显式 `--derived-psk` 仅限纯 WPA2，不适用于 WPA3/SAE 或混合模式。2026-11-02，一张 SD 卡／一台平板上，`seed set` 已写入派生 PSK 并通过工具自校验，`seed restore` 已恢复原种子（H + R）。测试者的一个路由器上，计算正确的派生 PSK 无法连接，恢复原明文口令后立即连上。原因推测为 WPA3/SAE 或混合模式协商，未在路由器侧核实。2026-11-02/03，一张 SD 卡、一台平板、bundle `20261102-3`、内核 `7.2.10` 新增以下证据：`seed wizard` 在 dry-run 下拒绝两次不一致的口令且未写入；真实向导只改时区，预览仅该项变化，写入自检通过并生成备份（H）。`seed set --timezone` 两次写入后均由开机后的 `timedatectl` 确认（H）。误敲目标确认串被拒绝且未写入（H）。`safe-mode on/off` 跨启动阻止再恢复 msm、面板和触摸模块；开启时背光亮但无画面，USB 网卡与 SSH 正常（H + R）。结论不推广到其他设备或网络。`seed set` 其他项（镜像源／刷新率／软件包／替换或追加公钥）仍需真卡复验；纯 WPA2 网络上的派生 PSK 联网仍为 U（命令逻辑 O）。本次向导标签选择修复为 O，待真卡复验。

Full-system reinstall has H + R evidence on 2026-11-02/03, limited to one SD card, one tablet, bundle `20261102-3` and kernel `7.2.10`: preview/execution succeeded, independent whole-p1 SHA256 comparison matched, and subsequent full `verify` passed. The new system booted with old users/data gone and regenerated SSH host keys; the preserved plaintext Wi-Fi seed connected automatically and touch worked. Shared install formatting/extraction code was exercised on the card. First setup did not hang in this run; the intermittent defect remains unresolved. `--minimal`, `--no-readback` and mid-operation failures remain O only; real-card interruption/power loss and cross-kernel updates remain U. Menu and user-data backup/restore remain unimplemented (U).

完整系统重装已有 H + R 证据（2026-11-02/03），限一张 SD 卡、一台平板、bundle `20261102-3`、内核 `7.2.10`：预览与执行成功，独立比较整个 p1 的 SHA256 前后相同，随后完整 `verify` 通过。全新系统启动，旧用户数据消失、SSH 主机密钥重新生成；保留的明文 Wi-Fi 种子自动联网，触摸正常。共享的 install 格式化／解压代码也在该卡运行。本次首次向导未卡住，不能据此认定偶发缺陷已解决。`--minimal`、`--no-readback` 和中途失败仅 O；真卡断电／中断、跨内核更新仍 U。菜单与用户数据备份恢复仍未实现（U）。

### Touch on the rebuilt baseline / 新基线触摸

- **H, 2026-11-02:** `nt36523_thp` loaded and automatically downloaded its firmware at boot (`state=done steps=46 error=0`); `nt36523-thp-touch` appeared. The short check recorded `state=running`, `frames=22050 accepted=22050 rejected=0`, `failures=0 restarts=0 recoveries=0` and `fw=loaded`. Parameters matched the bundle: `managed=Y settle_ms=100 skip_probe=Y edge_px=50 filter=1 lock_px=12 confirm=1 multi=1 guard=0`. A 20-second event capture contained 54 downs/54 ups, at most five simultaneous contacts, x 36–1547 and y 30–2535; no new display errors were recorded. This was one short check about three minutes after boot. / **H，2026-11-02**：开机自动下载固件成功并出现输入设备；上述帧计数全部接受，无失败、重启或恢复，参数与设备包一致。20 秒事件记录为 54 次按下／54 次抬起，最多五点，坐标范围如上，无新增显示错误；仅覆盖开机约三分钟时的一次短时检查。
- **R:** the user observed responsive single touch, usable edges/all four corners and two-finger pinch. / **R 用户观察**：单指跟手、边缘与四角可点、两指缩放正常。
- **U:** three-finger gestures were not tested in this session. Long-term stability, intermittent startup failure, stylus and scan-rate commands remain unverified or unfinished. Palm-onset short contacts remain a limitation also seen with the stock implementation; the new guard feature has not passed acceptance and is fixed off in the bundle. / **U**：本次未测三指手势；长时间稳定性、开机偶发起不来、手写笔与扫描率命令仍未验证或未完成。手掌落下瞬间短触点的限制在原厂也存在；guard 功能验收未过，设备包固定关闭。
- **O:** the public driver extends the earlier tested multi-touch version with a palm-transient guard, defaulting to `guard=1` in source. The bundle overrides it with `guard=0`; read-code reasoning suggests the earlier reporting behavior, but that equivalence has not been separately verified on hardware. The builder requires exactly ten parameter exports, including `guard:uint`, and rejects missing or extra parameters. Seven pinned inputs reconstructed twice from the owner's exact matching AFE library had identical hashes. Two module builds differed in debug information/relocations and build-id; allocatable sections except build-id matched. / **O**：公开源码在较早实测多指版本上新增默认开启的 guard；设备包固定为 0，读码判断应保留原上报行为，但没有单独实机等价验证。构建期精确检查十参数；所有者匹配 AFE 两次重建的七项摘要一致；两次模块构建的调试信息／重定位及 build-id 不同，除 build-id 外可分配节一致。

Touch and display share the NT36523 controller. Use only the matching owner firmware; borrowed Xiaomi elish/nabu firmware must not be loaded. The public board source already has the `novatek,nt36523-thp` node at 16 MHz without reset/regulator properties; enabling touch does not require a DTB or kernel change. See [optional setup](install.md#31-enable-touch-optional) / [启用步骤](install.zh-CN.md#31-启用触摸可选). / 触摸与显示共用 NT36523，不加载其他平板或借用的小米 elish/nabu 固件；公开板级源码已有相应 16 MHz 节点，不需改 DTB 或内核。

## Known issues / 已知问题

- **Wake black screen & rapid-toggle stripes root causes resolved (one tablet, 2026-11-18); idle-screen reset-until-touch and long-term rate remain U.** Kernel patches `0017` (restore slave 7nm C-PHY `CMN_CLK_CFG1.DSICLK_SEL=3` across PHY reset) and `0018` (extend post-`vsn` panel bias settle delay `2 → 12 ms`) eliminated wake ESD resets and rapid-toggle stripes on one tablet (`0/46` ESD resets across 46 post-boot wakes/switches including 38 rapid `13–68 ms` presses on bundle `20261118-5`, `H + R`). The kernel alarm-IRQ pipeline reset (`0013`) and manual Python watchdog fallback remain retained; one earlier 2026-11-16 idle-screen reset stayed black until touched (cause not identified, U) and multi-day long-term stability remains U. See [recovery instructions](power-key.md#display-recovery--黑屏自动恢复). / **唤醒黑屏与快速连按条纹根因已解决（一台平板，2026-11-18）；亮屏静止复位后需触摸及多日稳定性仍为 U**。内核补丁 `0017`（恢复从 C-PHY `DSICLK_SEL=3`）与 `0018`（`vsn` 后偏压稳定延时由 `2 ms` 增至 `12 ms`）消除了唤醒 ESD 复位与快速连按条纹（包 `20261118-5` 46 次唤醒／切换含 38 次 `13–68 ms` 极限连按 `0/46` 复位，H + R）；保留 `0013` 报警脚内核复位与旧脚本手动备用，2026-11-16 曾见 1 次亮屏静止复位后需触摸才出图（原因 U）与多日长期表现仍为 U。
- **First GNOME setup can hang.** The recorded initial session had a dconf worker using a full CPU thread. Marking initial setup done and restarting GDM restored the next session (H + R); the cause is unknown. / **首次向导可能卡住**，记录中 dconf worker 忙转；标记向导完成并重启 GDM 后恢复，根因未知。
- **Extra-package seed timing:** the asynchronous service does not wait for Wi-Fi; network-arrival retry is pending. / **额外包联网时序**：异步服务不等待 Wi-Fi，网络到来触发重试未实现。
- **Known device warnings:** DSI PLL lock/unprepared warnings, Bluetooth wake-ack messages and reserved-memory overlap were recorded. No absence-of-warning or hardware-safety certification is claimed. / **日志警告**：记录过 DSI PLL、蓝牙 wake-ack 与保留内存重叠，不宣称零警告或硬件安全认证。
- **Power/stability:** long-duration black-screen rate, suspend, natural FULL, long-term charging behavior, battery life and power consumption are not accepted for this baseline (U). / **耗电与稳定性**：长时间黑屏率、挂起、自然FULL、长期充电、电池续航及功耗尚未验收（U）。
- **Reproducibility:** archives and recipes are pinned, but APT repositories are not snapshotted; cross-date rootfs package versions can change (O boundary). / **复现边界**：归档／配方锁定，APT 无仓库快照，跨日期依赖版本可变（O 边界）。

## Earlier project: historical device results / 旧工程历史实机

These are **earlier-project records through 2026-10-30**, not fresh acceptance of the rebuilt sources. Source: the maintainer's earlier device records and user observations. / 以下为**旧工程截至 2026-10-30 的记录**，来源是维护者旧工程设备记录与用户观察，不是新源码验收。

| Area / 项目 | Historical result / 历史结果 | Class and date / 级别日期 |
|---|---|---|
| Touch / 触摸 | Earlier module supported single/five-slot multi-touch; pinch and GNOME gesture reported / 较早模块有单指、五槽多指，缩放与手势有用户观察 | H + R, through / 截至 2026-10-24 |
| Bluetooth / 蓝牙 | BLE mouse pairing/reconnection recorded; classic audio not established / BLE 鼠标配对回连，经典音频未验证 | H, through / 截至 2026-10-22 |
| Audio / 音频 | ADSP/APR/card and analog microphone path progressed; speakers not implemented / ADSP、声卡与模拟麦克风推进，喇叭未做 | H, 2026-10-23–24 |
| Other peripherals / 其他外设 | Keys, temperature/cpufreq, read-only RTC/gauge, SD and USB 2.0 host/peripheral recorded / 按键、温度、调频、只读 RTC／电量计、SD、USB 2.0 等有记录 | H, through / 截至 2026-10-26 |
| Display / 显示 | Accelerated desktop with intermittent black-screen defects / 加速桌面但有偶发黑屏 | H + R, through / 截至 2026-10-22 |

Current device audio evidence comes from earlier manual-start packages and is limited to USB-C digital headsets, opt-in four-channel onboard capture with ALSA UCM2 / PipeWire / GNOME desktop `Internal Microphone` integration, and short-session desktop playback on the four onboard speakers (`Internal Speakers`, H + R, 2026-11-07) as listed above; physical microphone-to-channel mapping and long-term speaker use remain U. Current Bluetooth controller evidence is H + R and one mouse pairing is R as listed above; reboot/sleep reconnection, other device types and A2DP remain U. Current touch evidence is the separate short 2026-11-02 check above; older gesture observations do not establish current three-finger support. Camera, sensors, hardware video decode, stylus, speaker protection/drive and broader power-management work remain unfinished or unverified. / 当前实机音频证据来自此前手动启动包，仅限上表所列数字 USB-C 耳机、显式四路板载录音及桌面 UCM2 `Internal Microphone` 集成，以及四个板载喇叭的短时桌面播放（`Internal Speakers`，H + R，2026-11-07）；物理麦克风映射和喇叭长期使用仍为 U；蓝牙按上表控制器 H + R、一只鼠标配对 R，重启／休眠回连、其他类型与 A2DP 仍为 U；当前触摸证据是上方独立的 2026-11-02 短时检查，旧手势观察不证明当前三指手势已验证。摄像头、传感器、硬件解码、手写笔、喇叭保护驱动和完整电源管理仍未完成或未验证。

## Offline source checks / 离线源码核验

Offline verification replayed ten U-Boot and six Linux patches with fuzz=0 and rebuilt identical U-Boot/DTB hashes (O); DTB compilation had 36 dtc warnings, with no dt-schema acceptance. Current tools have synthetic manifest/archive, seed parser, image-file filesystem and target-check tests (O). Neither those tests nor a dry-run establishes real-card I/O, arbitrary firmware compatibility, touch or audio operation.

离线核验以 fuzz=0 应用了十个 U-Boot、六个 Linux 补丁，重建 U-Boot／DTB 哈希一致（O）；DTB 有 36 条 dtc 警告，未通过 dt-schema 验收。新工具有清单／归档、种子、普通镜像文件系统与目标检查测试（O）；这些测试和 dry-run 均不能证明实卡 I/O、任意固件兼容、触摸或音频可用。

## Experimental onboard capture

板载音频：包 `20261102-17` 在一台平板四次连续冷启动中三次成功、一次失败；已定位等待受墙钟步进影响 (H, 2026-11-08)，修复包 `20261102-18` 随后连续六次冷启动全部成功 (H + R, 2026-11-08)，自启长期可靠性 R/U，此前手动启动下四路模拟麦克风同时录音与桌面 UCM2 `Internal Microphone` 集成已在一台设备验证 (H + R, 2026-11-06)；四个喇叭桌面播放已在一台设备短时验证 (H + R, 2026-11-07)；物理麦克风映射仍未验证 (U)，没有喇叭保护算法。See [audio startup and limits](audio-capture.md). Superseded on 2026-11-17 by kernel autoload (bundle `20261116-13`: 6 cold + 2 warm boots, H + R); see the audio row above.

## Four-microphone capture and desktop UCM2 integration (H + R, 2026-11-06)

Package `20261102-12` contains the externally rebuilt
`snd-soc-sm8250` module with kernel patch `0008`. It removes the forced
two-channel override only for `TX_CODEC_DMA_TX_3`, allowing standard
ASoC channel negotiation; 48 kHz/S16_LE and other backends are unchanged.
Image, DTB, the other 104 modules, firmware, startup script and modprobe
rules are unchanged from `20261102-11` (O). On one tablet (H + R,
2026-11-06), a 12-second 48 kHz S16_LE 4-channel capture recorded real
simultaneous signal on channels 1–4 (`rms_dbfs` −31.79 / −47.82 / −29.97 /
−32.63) and reboot restored the stopped default.

Package `20261102-13` adds capture-only ALSA UCM2 (`DBY-W09.conf`,
`sm8250.conf`, `HiFi.conf`) and updates `dby-audio-start` to bind-mount
`/usr/share/alsa/ucm2/conf.d/sm8250` read-only, initialize `alsaucm -c hw:DBYW09 set _verb HiFi set _enadev Mic`,
and restart active desktop users' `wireplumber` after routing is ready.
On one tablet (H + R, 2026-11-06), explicit `/run/dby-device/bin/dby-audio-start`
automatically exposed the 4-channel `Internal Microphone` source (`capture_FL/FR/RL/RR`)
in PipeWire and GNOME Settings, recorded clean audio in GNOME Sound Recorder,
coexisted cleanly with USB-C digital headset switching, and left no runtime UCM2
mount or onboard audio modules loaded after reboot. Physical mic-to-channel
mapping and speakers remain U.

## Release and operating boundaries / 发布与使用边界

The installation workflow has not been independently validated by a third party starting from a blank card; feedback is welcome. Speakers have no protection algorithm: gain limits do not prevent all damage during prolonged loud playback. `dby-chg` writes PMIC charger registers `0x1070/0x1643/0x1042`; the limited one-tablet tests do not establish long-term or multi-temperature safety. s2idle remains U. Stopping the power-key daemon restores saved GNOME lid settings, normally `suspend`, so closing the cover can enter an unverified suspend path. Cameras and sensors are not implemented. See the feature rows above for the eight-boot audio autoload limit, private Bluetooth address requirement and Venus H.264/browser defects.

安装流程未经第三方从空白卡独立验证，欢迎反馈。喇叭没有保护算法，限增益不能避免长时间大音量的全部损伤风险。`dby-chg` 写 PMIC 充电寄存器 `0x1070/0x1643/0x1042`，单机短测不证明长期或多温度安全。s2idle 仍为 U；停止电源键守护进程会还原保存的 GNOME 合盖设置（通常为 `suspend`），合盖可能进入未验证的休眠路径。摄像头、传感器未实现。六次冷启动、自备蓝牙地址和 Venus H.264／浏览器限制见上表。

Speaker module builds require [owner-supplied private configuration tables](../drivers/dby-tfa9874/README.md). This source separation is an offline change (O), not new device evidence or redistribution clearance. / 喇叭模块构建需要所有者自备私有配置表；本次源码分离为离线变更（O），不新增实机证据，也不证明再分发许可已核清。
