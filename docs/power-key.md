# Power key / 电源键

Evidence: **H** = device log read by the maintainer; **R** = user observation; **O** = offline implementation/tests; **U** = not verified.

The session helper in bundle `20261102-24` reads only the input device named
`pm8941_pwrkey`. A tap requests Mutter `PowerSaveMode=1`; a hold of **800 ms**
requests `org.gnome.SessionManager.Shutdown` asynchronously while the key is
still held, without waiting for the dialog to close. GNOME's standard
shutdown/restart/cancel dialog has a **60-second countdown and shuts down
automatically if left unanswered**. Its device behavior was checked on one tablet (H + R, see the validation section below).
If the screen is off at key-down, the helper requests `PowerSaveMode=0` and
ignores the rest of that gesture. It does not request suspend.

设备包 `20261102-24` 提供会话守护进程：短按关屏，按住 800 ms 非阻塞请求 GNOME
电源菜单，无需等待对话框结束；**标准对话框带 60 秒倒计时，无人处理会自动关机**。
按下时检测到屏幕已关则只唤醒。本版已在一台平板上实机验证（H + R，2026-11-08，见下节）：短按关屏/唤醒、长按弹出 GNOME 电源对话框、50 轮开关屏均正常。长期稳定性、休眠和登录界面行为仍未验证（U）。

## Device validation / 实机验证

All results below come from **one tablet, one SD card and one owner on 2026-11-08**, using bundles
`20261102-20` through `20261102-24`. They do not generalize to other hardware, firmware or long-term use.
**H** = journal or kernel log read by the maintainer; **R** = user observation.

| Behavior | Result | Evidence |
|---|---|---|
| Tap blanks, key wakes | Ten consecutive taps normal on `-20`; six more tap/wake pairs on `-23` (tap 128–156 ms, wake 29–47 ms in the journal). No black-screen type ② seen | H + R |
| Hold opens GNOME's shutdown dialog | Seen on `-20` and three times on `-23`; the journal shows `long 801 ms requested`, then `dialog closed rc=1` when cancelled. A dialog left open for 14 s did not block the following tap. Automatic shutdown at the end of the countdown was **not** observed | H + R |
| 50 consecutive off/on cycles (`PowerSaveMode`, 3 s off / 3 s on) | 50/50 completed; every cycle returned to the normal display state with a touch-module reload; the user saw no abnormal picture or touch problem | H + R |
| Login startup | The unit starts, the input ACL is present and the three GNOME settings are taken over. On `-23` and `-24` the first start failed because the ACL arrived late and the unit restarted successfully a few seconds later. The unit no longer runs under a root session | H |
| Cover (lid) switch | 11 close/open pairs without failure; closing blanks, opening wakes, touch works; attaching the pen does not blank; a short power press wakes the screen while the cover is closed; no suspend entry in the kernel log; `hall-lid` has the ACL and logind shows all three lid handlers set to `ignore` | H + R |
| Screen-off draw | About 0.84 W (60 s window, Wi-Fi connected, battery discharging) after the idle-wait change, versus 0.90 W before it; single windows, not repeated | H |

### Kernel-state decision (bundle `20261116-9`, 2026-11-16)

Same single tablet and card; **H** = journal read by the maintainer, **R** = user observation.

| Behavior | Result | Evidence |
|---|---|---|
| Single press after GNOME idle blank (mode 3) | 3/3 woke with one press (`wake 23–43 ms ok lag=1 ms`), no following `short`; previously reproduced as a wake-then-blank race on `20261116-8` | H |
| Tap blanks, key wakes | 10 pairs at ≥3 s intervals: every press decided correctly, processing lag 0–1 ms | H |
| Hold | Screen on: `long 801 ms requested`, dialog cancelled by the user. Screen off, held about 1.7 s: wake only, no dialog, no blank | H + R |
| Lock screen | Single press after idle blank and lock: lock screen shown, unlock normal | H |
| logind fallback configuration | `HandlePowerKey=ignore`, `HandlePowerKeyLongPress=poweroff` active; `gsd-media-keys` still holds `handle-power-key`. The no-desktop long press was **not** exercised (U) | H |

Display black screens (panel error flag high with the CRTC active) still occurred on 3 of 13 wakes in that 2026-11-16 test (`20261116-9`) and were recovered by the display watchdog in about 5–6 s; they were a panel/DSI-PHY wake issue, later resolved by kernel patches `0017` + `0018` (`2026-11-18`, see [Display recovery](#display-recovery--黑屏自动恢复)). During any active black screen the kernel state reads “on”, so a press is treated as a tap and blanks (H). The cover switch was not re-tested in this round.

内核状态判定(包 `20261116-9`,2026-11-16,同一台平板):GNOME 空闲息屏后单按 3/3 一下即亮、无误关;短按开关 10 对判定全对,处理延迟 0–1 ms;
亮屏长按弹框(用户取消)、灭屏按住约 1.7 s 只亮屏;锁屏单按出现锁屏界面并可解锁;logind 兜底配置生效,无桌面长按关机未实测(U)(H/R)。
该轮（2026-11-16）13 次亮屏中 3 次面板黑屏由显示看门狗约 5–6 s 救回，属面板/从 PHY 唤醒问题，其根因已由 2026-11-18 内核补丁 `0017` + `0018` 修复（见下文[黑屏自动恢复](#display-recovery--黑屏自动恢复)）；黑屏期间内核状态为"亮",此时按键按短按处理(H)。合盖本轮未复测。

**Not validated (U):** `stop`/`mask`/`unmask`, restoration after a crash or SIGKILL, the login screen (GDM),
starting with the cover already closed, touch waking a blanked screen, the suspend path, logout/relogin cycles
and long-term reliability. Earlier intermittent wake black screens and rapid-toggle stripes were resolved on
one tablet on 2026-11-18 (`0017` + `0018`); multi-day long-term stability and the 2026-11-16 idle-screen reset requiring touch remain U.

以上结果来自**一台平板、一张 SD 卡、一位所有者，2026-11-08**，设备包 `20261102-20` 至 `-24`，
不能推广到其他硬件、固件或长期使用。**H** = 维护者读过的日志；**R** = 用户观察。

- 短按关屏、再按唤醒：`-20` 连续 10 次正常，`-23` 另 6 对正常；无黑屏类型②（H + R）。
- 长按弹出 GNOME 关机对话框：日志为 `long 801 ms requested`，取消后 `dialog closed rc=1`；对话框开着 14 秒期间不阻塞后续短按；倒计时结束后的自动关机**没有观察**（H + R）。
- 50 轮开关屏（关 3 秒／开 3 秒）：50/50 完成，画面与触摸无异常（H + R）。
- 登录后启动：单元启动、输入 ACL 到位、三项 GNOME 设置被接管；ACL 稍晚时第一次启动失败、数秒后自动重试成功；root 会话下不再运行（H）。
- 合盖：11 对开合无失败，合上关屏、打开亮屏、触摸可用，贴笔不关屏，合盖状态按电源键可亮屏，内核日志无 suspend，logind 三项合盖处理均为 ignore（H + R）。
- 关屏待机约 0.84 W（60 秒单次，连着 Wi-Fi，放电），空闲等待改动前为 0.90 W；均为单次窗口（H）。
- **未验证（U）**：stop/mask/unmask、崩溃还原、登录界面（GDM）、启动时盖子已合上、触摸唤醒已关屏、休眠路径、登出再登录、多日长期可靠性（唤醒黑屏与快速连按条纹根因已于 2026-11-18 经内核补丁 `0017` + `0018` 在一台平板上解决，2026-11-16 单次亮屏静止复位后需触摸及多日稳定性仍 U）。

## Disable and restore / 关闭与恢复

Run as the logged-in desktop user, in that user's session:

```sh
# Stop now; restore the user's previous GNOME power-button action.
systemctl --user stop dby-w09-powerkey.service
# Prevent future automatic starts (does not stop an already running instance).
systemctl --user mask dby-w09-powerkey.service
# Undo the mask, then start again.
systemctl --user unmask dby-w09-powerkey.service
systemctl --user start dby-w09-powerkey.service
```

以当前桌面用户运行。临时关闭用 `stop`；永久关闭先 `stop` 再 `mask`。
`unmask` 后可 `start` 或下次登录恢复。无需把用户加入 `input` 组。

The helper reads and saves `power-button-action`, `lid-close-battery-action`
and `lid-close-ac-action` together in
`$XDG_RUNTIME_DIR/dby-w09-powerkey.saved` with mode 0600 before setting all three to
`nothing` in one GIO delayed-apply transaction. Any unavailable read prevents
takeover. The existing GJS runtime executes the bounded transaction; every key
is checked for writability and schema range before staging. A staging failure
reverts the pending changes before apply. The JSON save file contains all three
original values; a legacy single-value file restores only the power-button key. Normal exit, SIGTERM and SIGINT restore that value only if takeover
succeeded at least once in that process. `ExecStopPost` restores a valid saved
value after exit, including a crash or SIGKILL: the file is written before
takeover, so its presence means takeover may have occurred. A missing or
invalid saved file (including a symbolic link) is a successful no-op.
The saved file is kept so both restores are idempotent.
A new start saves the then-current setting. Property/settings calls have a five-second timeout;
failures are logged without exposing subprocess output. Failed takeover is
retried without overwriting the saved value. Until takeover succeeds, no
custom key actions run. Restoration still requires a working session bus;
it cannot be guaranteed when GNOME or the bus is unavailable (**U**).

启动时先完整读取并保存三项设置（JSON、0600），再用现有 GJS/GIO 的 delay/apply
一次提交三项 nothing；任一读取失败不接管，写入前检查全部键的可写性和范围，
暂存失败则 revert，不提交部分结果。旧单值保存文件仍只还原电源键；本进程至少成功接管过一次，退出时才还原。
`ExecStopPost` 独立检查保存文件：有效则还原（文件先于接管写入，表示可能接管过），
缺失、无效或为符号链接则不动设置并成功退出。还原失败会记录日志；会话总线失效时
不能保证还原成功（U）。

The user unit sets `StartLimitIntervalSec=0` while keeping `Restart=on-failure`
and `RestartSec=2`. This allows retries if the input ACL arrives late during login, without exhausting systemd's start-rate limit. On the tested tablet the first start failed that way and the retry succeeded a few seconds later (H).

用户单元用 `StartLimitIntervalSec=0` 关闭启动频率限制，保留失败后每 2 秒重试，
以等待登录初期可能稍晚生效的输入 ACL；实机上第一次启动确因此失败、数秒后重试成功（H）。

The user unit uses `ConditionUser=!root`; both the normal entry point and
`--restore` quietly skip UID 0 and the `gdm`/`Debian-gdm` greeter accounts.
Other users still need a ready Mutter session before takeover succeeds.
After activation, idle input waits indefinitely in `select`, with no periodic
polling when no dialog client exists. A pending hold waits for its remaining
800 ms deadline; desktop readiness retries retain their two-second interval.
A pending dialog client is polled at most one second after the previous loop
iteration (earlier input can also trigger a poll). Once reaped, idle waiting
is indefinite again. A nonblocking signal wakeup pipe makes SIGTERM/SIGINT
wake `select`, including a signal arriving just before the wait; cleanup
restores the previous wakeup fd and closes both pipe ends. These paths are
offline-tested (O). On the tested tablet the helper no longer appeared among the top wake sources in a 60 s screen-off window (H, a single window).

用户单元通过 `ConditionUser=!root` 排除 root；正常入口和 `--restore` 都会
安静跳过 UID 0 以及 `gdm`/`Debian-gdm`。其他用户仍须等待 Mutter 会话就绪后
才能接管。激活后无待决长按、无对话框客户端时，`select` 无限阻塞等待输入，
不再周期轮询；待决长按仅等剩余的 800 ms 截止时间，桌面未就绪仍每 2 秒重试。
有对话框客户端时，select 最长等 1 秒便检查一次（更早的输入也会触发检查），回收后恢复无限等待。
非阻塞信号管道使 SIGTERM/SIGINT 能唤醒等待，包括信号恰好先于 select 到达的情况；
退出时恢复原 wakeup fd 并关闭管道。以上有离线测试（O）；实机一次 60 秒关屏窗口中，守护进程已不在唤醒来源前列（H，仅单次）。

Long presses log `long N ms requested` when the gdbus client starts; this is
a request, not proof that the dialog appeared. While that client is pending,
another hold logs `long N ms ignored (dialog pending)`. The event loop polls
the client without waiting and logs `dialog closed rc=<code> after N ms` on
exit, preserving nonzero return codes without labelling them failures.
Only failure to spawn the client logs `long N ms failed`; child output is
discarded. Short-press/wake handling continues while the request is pending.

There is no five-second dialog timeout. A 120-second cleanup guard requests
termination of a stuck client; after another two seconds it requests kill if
necessary, with no waiting in the event loop. On daemon exit, cleanup requests
termination and waits up to two seconds, then kills and waits up to two more
seconds if needed, before restoring settings. Terminating the gdbus client
does not prove the GNOME dialog was cancelled (U).

长按启动客户端后记录 `long N ms requested`，不把它当作对话框已显示的证明；
客户端仍在时再次长按记 `ignored (dialog pending)`，不启动第二个请求。
事件循环非阻塞 poll 回收，退出只记 `dialog closed rc=<code> after N ms`，非零码
不再称为失败；仅启动客户端抛 OSError 时记 `long N ms failed`，子进程输出丢弃。
等待对话框期间仍可处理短按/唤醒。对话框不受 5 秒超时限制；120 秒兜底请求
terminate，再过 2 秒仍未退出则请求 kill，循环中不 wait。守护进程退出时先
terminate 并最多等 2 秒，必要时 kill 再最多等 2 秒，然后按原语义还原设置。
结束 gdbus 客户端是否取消 GNOME 对话框仍未验证（U）。

The local `systemd.unit(5)` “Unit File Load Path” documents that earlier paths
win: the user's `$XDG_CONFIG_HOME/systemd/user` (normally
`~/.config/systemd/user`) precedes `/run/systemd/user`. This is the offline
basis for the persistent user mask. The device's effective path and mask
behavior still need verification. The rule `70-dby-w09-powerkey.rules` adds
`uaccess` only to the exact `pm8941_pwrkey` and `hall-lid` event devices, before
`73-seat-late.rules` applies the active seat ACL. No other input permissions
are changed. The existing builder copies the service, wants link and rule
from the device image's `etc/` into `/run/`; its existing user-link support
requires no change.

## Key-down decision table / 按下时判定

Since bundle `20261116-9` the helper reads the unique `/sys/class/drm/card*-DSI-*`
connector's `enabled` and `dpms`, plus
`/sys/class/backlight/rt8555-backlight/bl_power`, directly at key-down.
There is no key-down D-Bus property query or blank-history cache.

| Kernel screen state at key-down | Decision / 判定 |
|---|---|
| Off: all three values recognized, but not all `enabled` / `On` / `0` | Immediately request mode 0, including GNOME idle mode 3; ignore hold/release / 立即唤醒，忽略其余按键过程 |
| On: `enabled` / `On` / `0` | Release before 800 ms requests mode 1; holding to the deadline requests the GNOME dialog / 短按关屏、长按对话框 |
| Unavailable: missing/ambiguous connector, unreadable or unknown value | Ignore the gesture; log `screen state unavailable` / 忽略并记录日志 |

Recognized values are `enabled`/`disabled`, `On`/`Standby`/`Suspend`/`Off`,
and backlight power 0–4. Mixed transition states count as off. The connector
card number is discovered, not fixed. Takeover only requires Mutter to answer
with an integer, so a session starting in idle mode 3 can activate the helper.

`EVIOCSCLOCKID` selects `CLOCK_MONOTONIC` on the power-key descriptor. The hold
deadline is the kernel key-down timestamp plus 800 ms; short-press duration is
release timestamp minus press timestamp. If the ioctl fails, one diagnostic
is logged and processing-time measurement is used instead. `wake` and `short`
logs append `lag=N ms` measured when processing the down/release event;
`long` carries the key-down processing lag. Without kernel timestamps the
field is `lag=?`. Logs never include subprocess output.

**Known race:** the idle-screen single-press problem was reproduced on
2026-11-16 (H, bundle `20261116-8`): GNOME woke the screen before the blocking
`PowerSaveMode` query completed, then the helper read mode 0 and blanked it
again at release. Direct kernel-state reads and kernel event timestamps
replace that query (O). **A remaining race is unverified (U):** if the helper
only processes key-down after GNOME has fully lit the screen, it can still
read “on.” The lag field helps observe this delay; the change does not claim
hardware elimination of the race. Input remains non-exclusive so GNOME can
reset idle time and present the lock screen on input.

自包 `20261116-9` 起,按下时直接读唯一 DSI 连接器的 enabled/dpms 与 RT8555 的 bl_power，
三项恰为 enabled/On/0 才算亮；三项可读且值有效但不全满足时（含过渡态）算灭。
找不到或有多个 DSI、任一文件读取失败或值不认识则忽略本次按键并记录日志。
不再在按下时调用 D-Bus 查询，也不保留关屏历史缓存；Mutter 能回答整数即可接管，
包括空闲息屏时的 3。灭屏按下立即请求模式 0，长按和释放均忽略；亮屏才处理短按和长按。

通过 EVIOCSCLOCKID 选择单调时钟，用内核按下时间戳加 800 ms 定截止时间，
释放与按下时间戳之差判短按。设置时钟失败只记一次诊断，退回处理时取时钟。
wake/short 日志分别附按下/释放的处理延迟 lag=N ms，long 沿用按下时的延迟；
时间戳不可用时为 lag=?，不输出子进程内容。

已复现的原因（H，2026-11-16，包 20261116-8）：GNOME 先因输入亮屏，
守护进程的阻塞属性查询随后读到 0，释放时又关掉。现用内核状态与事件时间戳（O）；
**仍有未验证限制（U）：若进程处理按下事件时 GNOME 已完成亮屏，仍可能读到亮。**
lag 日志用于观察调度延迟，不能据离线测试宣称竞态已在实机消除。
输入不独占，保留 GNOME 重置空闲计时和显示锁屏界面的输入路径。

## Without a desktop / 没有桌面时

The bundle installs `/run/systemd/logind.conf.d/20-dby-w09-powerkey.conf` with
`HandlePowerKey=ignore` and `HandlePowerKeyLongPress=poweroff`. These apply only
when no program holds the `handle-power-key` inhibitor. In GNOME,
`gsd-media-keys` holds that inhibitor and the session helper decides the key
behavior. Without a holder (for example before the desktop starts), short
presses do nothing and logind powers off on a long press. The hold duration is
built into logind and is not configured here; this fallback and its timing
remain unverified on the device (U).

设备包把新增配置复制到 /run/systemd/logind.conf.d/，短按 ignore、长按 poweroff。
只在没有程序持有 handle-power-key 抑制锁时生效；GNOME 会话由 gsd-media-keys
持锁，由会话守护进程决定行为。没有持锁程序时（如桌面尚未启动）短按无动作、
长按由 logind 关机；时长由 logind 内置决定，不在此配置，兜底效果与时长仍待实机（U）。

## Validation limits / 验证边界

- Only the named event device is opened read-only. Repeats are ignored. The
  timer fires a long action at most once while held; release cannot also blank.
- Input overflow cancels an incomplete gesture. Blocking desktop calls can
  delay processing; a release timestamp at least 800 ms after key-down without a timer action
  is discarded instead of opening a dialog after release.
- `After`/`PartOf=graphical-session.target` express ordering/lifetime, not
  proof of Mutter readiness; the helper probes the bus and defers takeover.
  User-unit startup and udev ACLs worked at login on the tested tablet (H);
  logout/relogin cycles are U.
- Both start and restore skip root and the `gdm`/`Debian-gdm` greeter accounts because
  `/run/systemd/user` is shared by user managers. Login-screen behavior is
  outside this feature and remains U. No system-wide dconf defaults are changed; the runtime logind lid
  and power-key policies are described in their sections.
- Stop/mask and crash restoration, the login screen and multi-day reliability
  are U. Earlier wake black screens and rapid-toggle stripes were resolved on one
  tablet on 2026-11-18 (`0017` + `0018`); long-term absence remains unverified.

未验证项包括 stop/mask、崩溃还原、登录/退出会话循环、GDM 行为和长期可靠性。压力脚本的内核读数不能代替用户观察屏幕与触摸（用户观察见上节）。


## Lid screen control / 皮套合盖关屏

In bundle `20261102-24`, the separate `hall-lid` input reports GPIO113 as
`EV_SW/SW_LID`, active-low with **200 ms debounce**, without a wakeup-source.
The helper opens it read-only by exact device name. After settings takeover it
reads the current switch bitmap: already closed requests `PowerSaveMode=1`;
already open does nothing. Later close/open transitions request modes 1/0.
Duplicate states do not repeat requests, even after a failed display call.
These actions only control the screen and never request suspend or shutdown.

GPIO113 (hall0) activates around **45 degrees**, so the screen can turn off
before the cover is fully closed. GPIO23 (hall2) also detects the pen and is
not used for lid control; hall1/hall2 remain observation switches. The probe's shared pinctrl and LDO8 power settings are unchanged. The mapping comes from probe observations on one tablet with one magnetic tri-fold cover and one pen (H + R); with that cover the screen blanks as soon as the segment nearest the screen (about one third) comes down. The lid behavior was validated on that tablet (see the validation section above).

**A short power press while the lid remains closed can still wake the
screen.** A repeated closed state does not force it off again. Power-key tap,
hold and wake decisions otherwise remain unchanged. If the lid input is missing,
inaccessible, disconnected, or its initial state cannot be read, one diagnostic
is logged and power-key handling continues. A missing/failed input is not retried
periodically; restart the user service after resolving it. Input overflow is
resynchronized from the switch bitmap. Idle waiting still has no periodic timer
unless desktop readiness, a pending hold or a dialog client requires one.

The bundle adds `/run/systemd/logind.conf.d/10-dby-w09-lid.conf` at boot, setting
`HandleLidSwitch`, `HandleLidSwitchExternalPower`, and `HandleLidSwitchDocked` to
`ignore`. The local `logind.conf.d(5)` manual lists `/run/systemd/logind.conf.d/`
as a drop-in search path. A persistent local override belongs in
`/etc/systemd/logind.conf.d/`; a same-named file there overrides the runtime file.
This policy prevents logind's own lid action; the session helper separately
sets both GNOME lid actions to `nothing`. Startup ordering, GNOME policy and
failure behavior still require hardware validation (**U**).

Stop/mask the user service as described above to disable its screen handling.
Stop, normal exit or ExecStopPost restores the saved GNOME actions. **If the
saved lid actions are `suspend` (as in the prior device observation), restoration
re-enables GNOME's suspend policy: closing the lid afterwards may trigger
unvalidated sleep (U), despite logind's own ignore policy.** This is not a
validated suspend configuration. Bus or backend failure can prevent restoration;
a timed-out transaction can have committed all three settings, so the saved
file is retained for ExecStopPost recovery. Killing the helper without a working
restoration path cannot guarantee the original settings return.

设备包 `20261102-24` 将 GPIO113 独立为 `hall-lid`，上报 SW_LID，低有效，
**去抖 200 ms，无 wakeup-source**。用户进程按名字只读打开；三键接管成功后
读取当前开关：启动时已合上则关屏，已打开则不操作。之后合上关屏、打开亮屏，
重复状态不重复请求，调用失败只记录日志；**不请求休眠或关机**。

hall0 约半开 **45°** 就触发，因此关屏可能偏早；hall2 同时被笔触发，不能作为
合盖信号，hall1/hall2 继续保留探测用途。引脚配置和 LDO8 供电策略不变。
**合盖状态下短按电源键仍可亮屏**，
不会因为盖子持续合上而重复强制关屏。其余电源键判定不变。

设备缺失、权限不足、断开或初始状态读取失败时，记录一次诊断并保留电源键功能；
不周期重试该输入，修好后重启用户服务。输入溢出后通过开关位图重新同步。
只给 `hall-lid` 和既有电源键精确授予 uaccess，不授权 hall-probe 或其它输入设备。

运行时 logind drop-in 将三项 HandleLidSwitch 配置设为 ignore；本机手册确认
`/run/systemd/logind.conf.d/` 是标准路径，持久覆盖应放在 `/etc/systemd/logind.conf.d/`，
同名文件优先于运行时版本。守护进程同时把两项 GNOME 合盖动作设为 nothing。
用上文 stop/mask 可关闭自定义处理，但停止/退出/ExecStopPost 会还原保存值：
**若原值为 suspend，还原后合盖可能触发尚未验证的休眠（U），logind 自身的 ignore
不能保证拦住 GNOME 的请求。** 会话总线或后端失效时还原也不能保证成功。

已在一台平板上验证（H + R，2026-11-08）：11 次开合无失败、触摸恢复、贴笔不关屏、合盖时电源键可唤醒、全程无 suspend 日志；本机的三段磁性皮套只要最靠近屏幕的一段（约三分之一）贴下就关屏。启动时盖子已合上的同步、触摸唤醒已关屏仍未验证（**U**）。

## Lid behavior after stopping / 停止后的合盖行为

s2idle has not been validated. A successful stop restores the saved GNOME lid actions, commonly the default `suspend`; it does not force them to stay `nothing`. With those settings restored, closing the cover can request an unverified suspend path even though logind's package drop-in remains installed. Check the saved/restored settings before closing the cover after disabling this helper; restoration failures retain the U boundary described above.

s2idle 未验证。正常停止会还原保存的 GNOME 合盖动作，通常为默认 `suspend`，不会继续强制 `nothing`。即使 logind 包内 drop-in 仍在，还原后合盖也可能请求未验证的休眠；停用后先核对实际保存／还原值，再合盖。还原失败仍按上文 U 边界处理。


## Display recovery / 黑屏自动恢复

The matching kernel panel driver uses TLMM 135/136 alarm IRQs. Both flags must
remain high through a 1000 ms debounce while the panel/CRTC is active, then the
standard DRM helper resets the display pipeline. Device results (one tablet,
bundles `20261116-11`/`-12`, 2026-11-16, H + R): about 450 alarm edges from normal
and rapid screen off/on caused no false reset; four real both-high events each
reset successfully in one attempt. Three occurred shortly after a screen wake and
the picture returned by itself 1.6–1.9 s after the panel was enabled (the old
userspace watchdog took 5–6 s). **Known limitation:** in one event while the
screen was on and idle, the screen stayed black after the reset until the user
touched it (cause not identified, U); a touch or a screen off/on restores it. Its unit and script remain in the image, but its boot wants
link is removed. Kernel recovery is limited to five attempts per 60 seconds.

For manual fallback, first disable kernel recovery, then start the old service:

```sh
echo 0 | sudo tee /sys/module/panel_novatek_nt36523/parameters/esd_recovery
sudo systemctl start dby-w09-display-watchdog
```

**Do not run the old watchdog with `esd_recovery=1`.** Setting 0 keeps kernel
GPIO ownership and IRQ diagnostics; the old script's GPIO request will be busy
and it uses its existing read-only `/dev/mem` fallback. This fallback's coexistence
with IRQ monitoring needs device validation; check its journal for startup or
mapping errors. To return to kernel recovery, stop the service first:

```sh
sudo systemctl stop dby-w09-display-watchdog
echo 1 | sudo tee /sys/module/panel_novatek_nt36523/parameters/esd_recovery
```

Do not enable the fallback service at boot. Parameter changes alone do not
schedule a recovery: subsequent GPIO edges or panel enable trigger checks.
After hitting the limit, recovery can resume on such an event after the 60 s
cooldown; there is no periodic retry timer.

内核面板驱动通过两路报警脚中断检测黑屏并复位显示管线。实机（一台平板，包 `20261116-11`/`-12`，2026-11-16，H + R）：
约 450 次开关屏瞬态零误触发；4 次真实黑屏均一次复位成功，其中 3 次（亮屏后不久）画面在面板点亮后 1.6–1.9 s 自行恢复
（旧用户态看门狗 5–6 s）。**已知限制**：一次在亮屏静止使用中发生，复位后仍黑，用户触摸屏幕后画面才出现（原因未明，U）；
触摸或关开屏一次即可恢复。
旧 Python 脚本与 unit 保留为手动备用、取消开机自启。先把 `esd_recovery` 置 0，
再启动旧服务；恢复内核方案时先停旧服务再置 1，禁止两者同时恢复。置 0 不释放 GPIO，旧服务会使用已有的只读 `/dev/mem` 退路，启动成功与中断共存仍需上机确认。

**2026-11-18 root-cause resolution (`0017` + `0018`, bundle `20261118-5`, H + R on one tablet):**
Two kernel patches resolved the wake black-screen and rapid-toggle stripe root causes so ESD resets were no longer triggered during stress testing: `0017` restores slave 7nm C-PHY `CMN_CLK_CFG1.DSICLK_SEL=3` after PHY reset (preventing slave `PHY1` from falling from `0x37` to `0x34`; `0/52` normal resets, `1/86` at `160 ms` on bundle `20261118-3`), and `0018` extends post-`vsn` panel bias settle delay from `2 ms` to `12 ms` (`0/46` ESD resets across 46 post-boot wakes and 60/120 Hz switches, including 38 rapid `13–68 ms` off-to-wake presses, on bundle `20261118-5`). Kernel alarm-IRQ recovery (`esd_recovery=1`) and the manual Python watchdog remain retained as safety nets; multi-day reliability and the earlier 2026-11-16 idle-screen reset requiring touch remain U.

**2026-11-18 根因修复（内核补丁 `0017` + `0018`，包 `20261118-5`，一台平板 H + R）：**
两个内核补丁解决了唤醒黑屏与快速连按条纹根因：`0017` 在每次 PHY 复位后恢复从 7nm C-PHY 的 `CMN_CLK_CFG1.DSICLK_SEL=3`（消除从 `PHY1` 由 `0x37` 掉回 `0x34`；包 `20261118-3` 常规 `0/52` 复位、仅 `160 ms` 极限连按 `1/86` 复位），`0018` 将 `vsn` 上电后偏压稳定延时从 `2 ms` 延长至 `12 ms`（包 `20261118-5` 开机后 46 次 60/120 Hz 唤醒与切换含 38 次 `13–68 ms` 极速连按 `0/46` ESD 复位）。内核报警脚中断自救（`esd_recovery=1`）与手动 Python 看门狗仍作安全兜底保留；多日长期可靠性及 2026-11-16 单次亮屏静止复位后需触摸仍为 U。
