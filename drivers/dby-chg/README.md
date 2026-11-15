# PM8150B charging management / 充电管理

`dby_chg` uses `1042` to control battery charging and `1643` to signal driver
health to the inherited watchdog. The battery gauge `dby-fg` delegates STATUS
to this USB power supply. The device package provides standard OF modalias
loading; no initramfs charger or new module-list entry is needed.

## Evidence and limits

On one tablet, 2026-11-05, the preceding manual driver sustained charging,
reported STATUS, stopped petting when unplugged and resumed after reconnecting
(H); the desktop indicator was observed (R). Unload caused watchdog cutoff in
about 128 seconds (H). Petting after a watchdog bite did not restart charging;
USB replug was required (H + R).

A separate controlled experiment kept petting while writing `1042=00`: battery
current dropped from about +1.30 A to approximately -4 mA within five seconds,
USB remained present and the system stayed powered. After 60 seconds, writing
`1042=01` restored about +1.30 A within two seconds without replugging (H).
The integrated driver (bundle `20261102-10`) was then run on the same tablet
(2026-11-05): it autoloaded at boot, the desktop charging indicator appeared (R),
software stop/resume through `1042` worked without replug, and repeated
plug/unplug left the toggle budget untouched (H + R). On a PC USB port the
input is roughly SDP-class: about +0.2 A into the battery with the screen off,
but net discharge (about -0.65 A) with the screen on, in which case STATUS
correctly reads Not charging (H). **The driver has not run for days and its
long-term risk is unknown.** Natural FULL/recharge, suspend/resume, multiple
battery temperatures (the thermistor model is provisional) and the
unload-restore exception write remain U.

`1006=47` occurs both when charging is disabled and after a watchdog bite;
it cannot distinguish them. `1207` differed between experimental windows,
but its meaning remains U; this driver neither samples nor gates on it.
Those earlier device observations do not validate the new input-current control or fixed 9 V PD candidate described below; both remain pending device validation.

## Hardware ownership / 硬件归属

| Block | Owner | Boundary |
|---|---|---|
| PM8150B `0x10` CHGR, `0x12` BATIF, `0x13` USBIN, `0x16` MISC | `dby-chg` | Only this driver requests their IRQs; `0x1370` input-current limit follows Type-C/PD through `dby-chg` |
| `0x11` DCDC/OTG | Upstream `qcom,pm8150b-vbus-reg` | Charger does not access it |
| `0x15` Type-C, `0x17` PD PHY | Upstream `qcom_pmic_tcpm` | Future charger integration reads standard power_supply/role information |
| `0x40`–`0x43` FG | `dby-fg` | Read-only fuel gauge; `msoc-delta` IRQ requested only by `dby-fg` |
| PM8150L LPG | Upstream `leds-qcom-lpg` | Charger supplies LED trigger brightness only |
| MT5727 (I2C) | Future independent driver | Reads battery capacity/temperature through power_supply; no direct charger calls |

OTG observation on one tablet: `usbin-plugin=0`, USB input absent, Type-C
source/host, VBUS regulator enabled and charge indicator off. Thus the charger
makes no control write or pet while OTG is confirmed absent at USBIN; the
charger does not manage the OTG regulator. This IRQ revision still needs device
validation. Fixed-PDO input-current control is an offline candidate described below. USB3,
PPS, Huawei SCP/FCP and wireless supply (independent MT5727 driver) remain future
work and are not implemented here.

The SPMI irqchip owns type/polarity/clear/enable/disable writes at offsets
`0x11`–`0x16` in the requested interrupt blocks. The charger itself still has
the four original fixed write sites plus one bounded ICL write site below; it
does not write watchdog configuration.

## Fixed write surface

The four original fixed charger write call sites remain unchanged:

- Probe only: `1070=55` permits one `1070=50` write with readback. Already 50
  is accepted without writing; any other value or read error rejects probe.
- Healthy USB-present cycles: `1643=01`, at least thirty seconds between attempts.
- Disable: `1042=00`, only after a fresh read of exactly 01.
- Enable: `1042=01`, only after a fresh read of exactly 00. Normal enable and
  the bounded remove recovery share this call site.

The additional `1370` ICL write site is shared by runtime changes and bounded
restoration, with a hard range check `0x0A..0x28`. Constants have compile-time
assertions. No watchdog configuration or other charger register is written. Standard ADC5 measurements perform
the provider's own ADC and EOC operations. Do not run concurrent charger
experiments or other register writers.

Identity, regmap, IIO and power-supply acquisition precede the one-shot float
write. Probe accepts enable 01, or 00 with a one-time initial takeover permission;
other values reject probe. An observed 01 ends that permission. Later unowned
00 is not silently acquired.

## Runtime policy

First work is scheduled about two seconds after probe, then every thirty seconds.
`chg-state-change` and both `usbin-plugin` edges queue an immediate sample.
That evaluation schedules one follow-up after two seconds, then returns to the
thirty-second period: FG current refreshes roughly once a second and can still
be negative at the IRQ. Another state/plug IRQ restarts this sequence; bark does
not start, consume or reschedule the follow-up. All existing policy/write gates
and the rule that negative current is Not charging remain unchanged.
The threaded `wdog-bark` handler warns once and runs the same fresh-sample,
policy and pet path immediately, including the thirty-second minimum pet spacing.
An early healthy IRQ can retain the feeding status of a recent successful pet,
including on a brief unplug/replug, without issuing another write. It does not
bypass read, identity, enable, toggle-error or terminal-fault checks.
Old DTBs without all three IRQ resources fall back to thirty-second polling;
IRQ-controller deferral is propagated, and request failures reject probe.
IRQ threads and work serialize through the same mutex; probe and teardown block
new evaluation. All resource acquisitions precede the one-shot float write.
Every sample checks identity/regmap, 1006/1007/1070/1206/1306/1042, FG voltage,
current/capacity and ADC temperature. Read or identity failures cause no enable
write and no pet; later good reads may recover. Invalid enable values, lost
old-value preconditions, exhausted flip budget, and write/readback failures stop
management and petting until reload. Failed writes are never retried, including
on remove. A pet-write failure also stops management.

Healthy means matching identity/regmap, all reads successful, float still 50,
valid enable byte and USB present. Temperature/voltage/FULL policy failures
**do not stop petting**. Charging itself requires 1206=0, USB present, float 50,
all reads successful, positive voltage within limits, permitted temperature,
and no FULL latch.

Default disable boundaries are inclusive 12.0..42.0°C and <=4.43 V. Outside
those limits the desired state becomes disabled. Re-enabling requires
13.0..41.0°C inclusive and <=4.40 V. Hysteresis persists even if a pending
transition has not yet written. Runtime enable writes are at least 30 seconds
apart, with at most 12 attempts in the sliding interval (now-1 hour, now].
Attempts are reserved before I/O; any ambiguous write failure is terminal.
Only confirmed-USB-present runtime flips count toward this budget. Offline or
unreadable USB returns KEEP without changing desired state, ownership or toggle
history, and stops petting. Replug evaluates normal policy immediately, including
unsafe-temperature/voltage/FULL disable (subject to spacing and budget).
A requested thirteenth flip stops management/petting. This fault stays latched
even when old timestamps expire; the timer does not automatically restart management. During the 30-second
cooldown a pending change waits for a later evaluation; healthy petting continues
and the actual enable byte is reported. Thus policy violations do not promise
instant cutoff. Polling and scheduling add latency.

The two test-only module parameters are owner-readable/writable (`0600`):
`temp_min_decic` defaults to 120, accepts 120..350; `temp_max_decic` defaults to
420, accepts 150..420. The pair must leave a nonempty inner window
(min+10 <= max-10). Invalid updates return EINVAL without changing either value.
The worker snapshots the pair under a mutex; changes apply next evaluation.
Defaults may be restored; no setting may be less conservative than defaults.
For a 31°C sample, setting the upper bound to 32°C does **not** cause a violation;
a controlled stop test needs an upper bound below the current temperature.

The integer beta model is unchanged: Rpu=30k, R0=10k at 25°C, beta=3435K,
reference 1875000uV. The patched ADC5 provider returns microvolts, not millivolts.
Model and policy limits are provisional (U), with only a room-temperature
comparison; numeric bounds -100..150°C are not charging limits. No comprehensive
open/short validation or multi-temperature calibration is claimed.

## FULL and exit

FULL requires enabled 1042=01, voltage >=4.38 V and 0 <= current <200 mA for
at least 180 seconds from the first qualifying observation (normally seven
observations at thirty-second intervals, about 182 seconds after probe).
Only elapsed boottime determines FULL; IRQ/sample counts never shorten it.
Failed gates, read errors, backwards time or gaps >60 seconds (two periodic
intervals) break continuity. Duplicate timestamps add no time; early valid IRQ
observations refresh continuity but cannot move its start time. FULL
requests 1042=00 while healthy petting continues. Voltage <4.25 V or an observed
unplug/replug clears the software latch; allowed policy can then request 01.
Natural FULL and automatic recharge still need device validation.

Remove synchronously stops work. It may restore 01 once only if this driver
owns a successful OFF and the latest evaluation allowed charging with confirmed
USB presence. Preserved offline intent is not permission for remove recovery. It rechecks
identity/regmap and reads exactly 00 immediately before the write, then verifies
01. This one exit attempt is explicitly exempt from runtime spacing/budget;
there is no retry. If the latest evaluation disallowed charging, or management
has stopped on a fault, it leaves enable unchanged and logs why. Unowned initial
00 is never restored by remove. Shutdown stops work, extinguishes both LED triggers and restores only ICL
`1370=1E`; it does not write enable, float or watchdog registers.

The freezable queue does not pet during suspend. Unload, shutdown and faults
stop pets; the inherited watchdog remains active. After an actual watchdog
bite, reconnect USB even if policy has recovered. Coalesced IRQ work (or old-DTB polling) may miss very brief USB
edges. Disabling battery charge does not prove that all external power is off.

## Status and diagnostics

Confirmed offline means Discharging. Online FULL means Full at SOC >=98%,
otherwise Not charging. Otherwise Charging requires actual 1042=01, positive
measured current and successful feeding; other online states are Not charging.
Initial or >60-second stale samples do not claim Charging. ONLINE returns no
data until a fresh USB observation. Both USB and battery consumers receive
change events. FG falls back to Unknown if this supply is absent.

Read-only `/sys/kernel/debug/dby-chg/status` includes samples/errors, reason mask,
feeding/FULL/dwell, pet counts, `enable_reg`, `want_enable`, `toggles_last_hour`,
`toggle_budget_remaining` (12 minus the current hour count),
`last_toggle_ms`, `manage_stopped_reason` and OFF ownership. Timestamps are
milliseconds since probe; zero before any toggle is disambiguated by the count.
The hour count is pruned at read time. `stopped_seconds_since_present` sums
observed non-feeding time in the current observed USB session, including an
ongoing pause. Unknown USB reads do not reset it. It is not a hardware timer or
bite detector and cannot account for suspend while the last sample was feeding.
`replug_required_after_watchdog_bite=1` states a limitation, not a detected bite.

Reason bits: 0 identity, 1 read, 2 float, 3 USB absent, 4 hardware temperature,
5 model temperature, 6 voltage, 7 FULL, 9 pet spacing, 11 write failure,
12 stopping, 13 enable value/precondition, 14 flip budget, 15 unowned OFF,
16 pending enable-write cooldown. Bits 8 and 10 are unused. Policy reason bits
can coexist with successful feeding. Initial fields are placeholders until
`evaluated=1`. Events cover online, feeding, FULL, reason or enable changes;
summaries appear every ten minutes and failures are logged.

## Loading and host validation

DT: PM8150B child `dby,pm8150b-charger`, `reg=<0x1000>`,
`io-channels=<&pm8150b_adc ADC5_BAT_THERM_30K_PU>`, name `batt_therm`.
ADC unavailability defers probe before writes. FG is queried dynamically;
missing FG inhibits operations until good reads arrive. Module-list order is
not probe order, and a slow boot can still require USB replug.

```sh
make -C "$KERNEL_BUILD" M="$PWD/drivers/dby-chg" MO="$CHG_OUTPUT" \
    ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- W=1 modules
for test in core therm enable; do
    cc -std=c11 -O2 -Wall -Wextra -Werror -fsanitize=undefined \
        -fno-sanitize-recover=all "drivers/dby-chg/test-$test.c" -lm \
        -o "$TEST_OUTPUT/test-$test"
    "$TEST_OUTPUT/test-$test"
done
```

## 中文摘要

1042 控制电池停充/恢复，1643 喂狗表示驱动健康；温度、电压或 FULL 停充时继续
喂狗，读失败停喂，写失败永久停止管理。运行期严格遵守30秒间隔与滑动小时12次
预算；等待间隔期间实际充电状态可能尚未改变。卸载只在最近评估允许充电且拥有
停充状态时尝试恢复一次，允许这一次间隔/预算例外，失败不重试；不允许则保持
禁用并记录日志；shutdown 只恢复新增 ICL 的 1370=1E，不写原有充电控制寄存器。故障咬合后仍需重新插线。单独使能位实验有
实机证据；整合版已在一台设备自动加载并通过桌面标示、停充/恢复与插拔测试（H + R，
2026-11-05），**但未连续运行数日，长期风险未知**；自然FULL/再充电、挂起、
多温度点与卸载恢复例外仍未验证。电脑口约 500 mA，屏亮时可能净放电。

## Source and risk disclosure / 来源与风险披露

This project implementation uses standard Linux regmap, IIO and power-supply APIs under its GPL-2.0-only file notices. The register map and board policy were developed from stock/downstream hardware descriptions and project read-only register observations, followed by bounded enable/watchdog experiments; they are not a newly imported vendor driver. A complete historical derivation audit has not been established. The charger register addresses written are `0x1070/0x1643/0x1042` at the four original call sites, plus bounded `0x1370` at one shared call site; these writes can change charging behavior. The standard ADC provider performs its own conversion operations, so the charger write list is not a claim that no other PMIC block is written.

项目实现使用标准 regmap／IIO／power_supply API，文件声明 GPL-2.0-only；寄存器与策略来自原厂／下游硬件描述、项目只读观测及后续限范围实验，不是新导入的厂商驱动，完整历史推导归属仍未独立核清。会写的充电地址为 `0x1070/0x1643/0x1042`（原四处调用）及新增 `0x1370`（一个受限调用点），可能改变充电行为；标准 ADC 提供者另有转换操作。自然充满／再充电、挂起、长期和多温度点仍未验证，本轮固定 9 V 候选仍待验证，电脑 USB 口屏亮时可能净放电。

## Kernel charge indicator / 内核充电指示灯

`dby-chg-red` and `dby-chg-green` simple LED triggers replace the userspace
helper, udev rule, timer and shutdown services. Board LED default triggers bind
them to the upstream LPG driver. After each published sample: offline is off;
FG Full or capacity >=90% is green; below 10% is red; otherwise yellow (both).
Missing/Unknown FG data leaves the LEDs unchanged. Red/green levels 140/240
retain the stock 70/255 and 120/255 ratios for LPG max_brightness=511.
Unchanged levels are not sent again; late LED registration inherits the trigger
brightness. Remove and shutdown turn both triggers off; remove also unregisters
them. On one tablet (2026-11-15, bundle `20261115-7`, H + R) the three IRQs, the
immediate LED response, the 2-second settle resample and 30-second petting were
confirmed; shutdown turn-off of the kernel-trigger LEDs has not been observed yet.


## Type-C input current and fixed PD / 输入限流与固定 PD

The board requests the owner stock sink capabilities: 5 V / 2 A and 9 V / 1.5 A,
with 13.5 W operating sink power. Source PDOs and dual-role policy are unchanged.
The charger links to the Type-C supply with standard `power-supplies`;
`.external_power_changed` queues the existing work immediately without setting
the two-second settle flag. An already pending IRQ settle deadline is preserved.
There is no new poller or notifier chain. Each existing evaluation reads ONLINE,
USB_TYPE, VOLTAGE_NOW and CURRENT_MAX with
`power_supply_get_property_from_supplier()`; no private supplier reference is
held. With the new DTB, the core defers charger registration until the supplier
is registered. With an old DTB lacking the link, supplier data is unavailable
and runtime ICL does not increase.

| Observation | ICL target at `0x1370` (50 mA per step) |
|---|---|
| Confirmed online → offline | Restore firmware `0x1E` (1.5 A) |
| Stable offline / OTG, or USB state unreadable | No runtime write |
| Online, supplier offline/unreadable or invalid/nonpositive contract values | No change (retain the last byte, normally firmware `0x1E`) |
| Online Type-C, Rp current >=3 A | `0x28` (2.0 A) |
| Online Type-C, other Rp | `0x1E` (1.5 A) |
| Online fixed PD, voltage >=8.5 V | min(contract current, 1.5 A) / 50 mA |
| Online fixed PD, exactly 5 V contract | min(contract current, 2.0 A) / 50 mA |
| PPS/AVS, unknown source type, or other PD voltage below 8.5 V | No change; unsupported |

PD targets are rounded down to 50 mA steps and clamped to `0x0A..0x28` as the
board policy specifies. This preserves the inherited 1.5 A setting on a PC port;
it is not a general USB current-limit algorithm for other boards. AICL may settle
below the requested limit. The pure core function is the only normal target
selector. Identity/regmap are checked again, current ICL is read before each
write, equal values are skipped, and writes are read back. A pre-write read
failure does not authorize a write. Write/readback failure increments aggregate
`write_errors` and separate ICL errors, latches **only ICL management**, and
allows one attempt to restore `0x1E`. Even failed recovery reads consume that
one opportunity; later events/unplug/remove/shutdown do not retry it. A failed
exit restore is itself the single recovery attempt. Existing enable/temperature/
FULL policy, toggle budget and watchdog pet gates are unchanged. Normal remove
and shutdown restore `0x1E` through the same checked write site.

Summary/event logs and debugfs include `icl_reg`, `src`, `contract_mv`,
`contract_ma`, `icl_writes`, ICL error and latch fields. `icl_writes` counts
attempts; aggregate `write_errors` now also includes ICL write/readback failures.
`icl_read_error` marks an invalid/stale register byte; `supplier_error` and
`supplier_online` disambiguate unavailable contract data. A successful recovery
can leave `icl_error=0` while `icl_stopped=1` and the error counter remain latched.

**9 V is unverified (U)**: it relies on inherited `0x1360=07` allowing that input;
this implementation never writes `0x1344` voltage override, APSD/BC1.2/HC control,
`0x1061` fast-charge current, or watchdog configuration. If device testing shows
9 V charging failure or input collapse, unplug and stop for review; another
register change requires a separate decision. No Huawei 22.5 W claim is made.

Acknowledgements: the owner stock `smb5-lib.c` flow and a community Type-C PD
proposal based on the CoWave kernel informed the approach. The community
proposal has no established licence declaration here; no implementation code
was copied. This driver uses independently written standard power-supply API
integration and the bounded board policy above.

本轮仅为离线候选（O）：5 V/2 A、9 V/1.5 A 受电档位来自原厂描述；只增加
1370 限流写入，9 V 依赖继承的 1360=07，未写 1344。ICL 错误独立锁存并最多
恢复一次，不改变原有停充与喂狗门禁；实际合同、输入电流、温度和持续充电待上机。
