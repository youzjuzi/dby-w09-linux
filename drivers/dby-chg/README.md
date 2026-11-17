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
Those earlier observations predate input-current control; its device results (bundle `20261115-11`, one tablet, 2026-11-16, H) are recorded at the end of the ICL section below.

## Hardware ownership / 硬件归属

| Block | Owner | Boundary |
|---|---|---|
| PM8150B `0x10` CHGR, `0x12` BATIF, `0x13` USBIN, `0x16` MISC | `dby-chg` | Only this driver requests their IRQs; `0x1370` input-current limit, `0x1344` voltage allowance and `0x1342/0x1366` ICL override/options are managed by `dby-chg` |
| `0x11` DCDC/OTG | Upstream `qcom,pm8150b-vbus-reg` | Charger does not access it |
| `0x15` Type-C, `0x17` PD PHY | Upstream `qcom_pmic_tcpm` | Future charger integration reads standard power_supply/role information |
| `0x40`–`0x43` FG | `dby-fg` | Read-only fuel gauge; `msoc-delta` IRQ requested only by `dby-fg` |
| PM8150L LPG | Upstream `leds-qcom-lpg` | Charger supplies LED trigger brightness only |
| MT5727 (I2C) | Future independent driver | Reads battery capacity/temperature through power_supply; no direct charger calls |

OTG observation on one tablet: `usbin-plugin=0`, USB input absent, Type-C
source/host, VBUS regulator enabled and charge indicator off. Thus the charger
makes no control write or pet while OTG is confirmed absent at USBIN; the
charger does not manage the OTG regulator. This IRQ revision still needs device
validation. Fixed-PDO input-current control is described below. USB3,
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
restoration, with a hard range check `0x0A..0x3C` (codes above `28` only for fixed PD
>=8.5 V within the selected and thermal ceilings). The `1344` allowance site
accepts only `00` (firmware default), `02` (FORCE_9V) or `08`
(CONTINUOUS, fixed-PD 5 V/<=500 mA transition only). The new `1342` site
accepts only `00/01`, and `1366` only `02/01` (automatic/override). Constants have compile-time
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
State/plug IRQs and supplier events share a follow-up two seconds after the
latest event, then return to the
thirty-second period: FG current refreshes roughly once a second and can still
be negative at the IRQ. Another state/plug IRQ restarts its follow-up; bark does
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

## Battery thermistor current-drop compensation

The 2026-11-16 task book supplies the following plug/unplug observations (H)
from one idle tablet, sampled every 1.2 seconds. These are input evidence for
the compensation (device results for it are recorded below):

| Edge | Battery current | Thermistor voltage | Uncompensated reading |
|---|---|---|---|
| Plug Lenovo PD source | -0.86 to +3.75 A | 397253 to 453633 uV | 30.7 to 26.1 C |
| Unplug | +3.44 to -1.08 A | 395242 to 333801 uV | 30.8 to 36.6 C |

Each step completed within one sample. The voltage/current changes imply
12.2 and 13.6 mOhm; their rounded mean, **13 mOhm**, is a two-step estimate
for this tablet. Positive charging current raises the sensed voltage and
makes the uncorrected NTC reading colder; discharging reverses the bias.
The task also reports that the stock fuel-gauge node selects the same `0x2a`
BAT_THERM_30K_PU channel, ratiometric mode and 200 us settling, with
`qcom,fg-temp-ir-for-current` and `qcom,five-pin-battery`. Interpreting the
stock current/temperature pairs as mA and 0.1 C would imply +5.0 C at 3.5 A,
consistent in scale with these steps; those property semantics remain
unverified. No stock correction table is used by this implementation.

Before the unchanged beta conversion, the pure helper calculates
`uv_comp = therm_uv - clamp(current_ua, -6000000, 6000000) * 13 / 1000`.
The signed microvolt correction uses 64-bit arithmetic and integer division
truncated toward zero. A compensated voltage outside the open interval
`(0, ref_uv)` or an invalid model result is a temperature error. The existing
raw ADC/model validation remains; missing battery current follows the same
existing temperature-error path. Both the charging stop/resume gates and the
38/36 C PD thermal policy use the compensated `temp_decic`; their numeric
thresholds and hysteresis are unchanged. Charger `chg_temp` is not corrected.

Each `chg-state-change` IRQ, `usbin-plugin` IRQ or `external_power_changed`
notification records an edge timestamp, even when the USB online bit has not
changed yet. For two
seconds after the most recent event, every battery-temperature policy check
uses the last valid compensated temperature sampled **outside** an edge
window. Samples inside a window never refresh that cache. If no such sample
exists yet (including first boot), the current value is used. The selection
does not hide current ADC/FG errors: offline ceiling reset, parameter clamping
and read-error fallback remain unchanged. Charger `chg_temp` is always fresh.
This replaces the earlier online-state-change thermal-skip rule; temperature
policy continues to run with the selected value, including FULL bookkeeping
and charging stop/resume.

The existing delayed work guarantees a sample at/after the latest edge's
2-second deadline, with the window extended by any later state/plug IRQ or
supplier event. All three use the same edge and settle path. A sample begun
inside the window cannot consume the post-window evaluation merely because
its I/O finishes after the deadline. Of the three IRQs, only `wdog-bark` does
not record an edge; periodic sampling and parameter writes do not either.
Charge-state transitions between fast charge, trickle and full therefore also
hold the last valid outside-window temperature for two seconds; this bounded
hold is accepted by the task. No new timer, register access or desktop
temperature property is added.

Logs and read-only debugfs retain `temp_decic` as the current compensated
measurement, alongside `temp_raw_decic` and `therm_ir_uv` (signed voltage
subtracted). `temp_policy_decic` records the temperature selected for policy;
`temp_held=1` means it came from the outside-window cache. Thermal drop lines
include these fields. Consult `temp_error`: failed current compensation leaves
measurement/correction zero placeholders; a held value does not clear that
error. The raw value exists only when raw ADC/model conversion succeeded.

The 2026-11-16 fix task supplies these observations for bundle `20261116-4`
(H, owner-provided logs, about 1.1-second sampling): a supplier notification at
255.212 s preceded the USB offline indication by 4 ms. USB still read online,
FG current was the old +3.46 A, and the raw temperature had already risen to
36.5 C. Compensation therefore produced 41.4 C and a false drop to 2250 mA.
This explains why skipping only online-state transitions missed that sample.
FG current refreshed about one second later (-1.04 A after 1.1 seconds).
With refreshed current, unplug changed compensated temperature from 35.0 to
35.4 C and plug from 32.4 to 32.2–32.4 C; one initial plug sample read too low
at 28.3 C. The two-second hold covers that reported refresh delay and matches
the existing settle interval. In a 12.5-minute run at 2500 mA (SOC 63–76%),
compensated temperature levelled at 36.0–36.3 C, battery current fell from
3.4 to 2.6 A near constant-voltage charging, and chip die temperature stayed
at/below 58.8 C; only the unplug anomaly caused a drop. These preceding-bundle
observations do not validate this hold candidate; repeated edges and reboot
remain pending device checks.

The next task supplies three plug/unplug trials for bundle `20261116-5`
(2026-11-16, H, owner-provided logs). All three plugs held the preceding
33.0/34.3/33.1 C policy value with `temp_held=1`, returned to fresh 32.8–33.0 C
readings after about two seconds and caused no drop. All three unplugs instead
first evaluated with USB still online and `temp_held=0`: old +2.57–2.68 A
current produced 38.9–39.1 C and a false drop to 2250 mA. Plug/supplier events
arrived only milliseconds later; the two-second samples read 34.2–34.3 C.
The first evaluation was triggered by `chg-state-change`, which the previous
task had excluded from edge handling. On the third unplug the unheld 39.1 C
sample also entered the cache, contaminating the subsequent held samples.
Including the state IRQ in the common edge path addresses that missing event
coverage before cache selection.

Device acceptance (2026-11-16, H, bundle `20261116-6`, one tablet, 1.1-second
sampling plus driver logs): three unplugs and three plugs of the Lenovo PD
source caused no thermal drop. Every unplug's first evaluation (USB still
online, stale +2.1–2.2 A) already had `temp_held=1` and used the pre-edge
32.8–33.7 C; the stale-current measurement of 37.7–38.0 C was logged only.
Two seconds later fresh current gave 33.4–33.9 C. Plugs held the pre-edge value
through the `08`→`02` transition and returned to 32.8–33.0 C after about three
seconds. Boot with the source attached caused no drop. A 3 A Rp charger, unplug,
a PC port and OTG behaved as before, with `write_errors=0`. On one plug, four
events within 2.5 s extended the hold to about 4.8 s; there is no upper bound on
the hold if edge events kept arriving less than two seconds apart (not observed).

Current-drop compensation does **not** establish true cell temperature.
The task reports a further slow 4.7 C rise over 2.8 minutes while charging;
after unplug, at similar discharge current, the reading fell from 36.6 to
33.4 C after 45 seconds, 32.0 C after two minutes and 31.2 C after nine minutes
(30.7 C before charging). PM8150B die temperature also fell from 58 to 36 C
in 15 seconds and then to 32 C. This suggests fast thermal coupling to board
heat (interpretation U); that effect is not corrected here. The 13 mOhm
estimate, thermistor model and board/cell relationship need device validation.

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
00 is never restored by remove. Shutdown stops work, extinguishes both LED triggers and restores the input controls in order:
`1344=00`, `1342=00`, `1366=02`, `1370=1E`; it does not write enable, float or watchdog registers.

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
IIO channels `batt_therm` (`ADC5_BAT_THERM_30K_PU`, microvolts) and `chg_temp`
(`ADC5_CHG_TEMP`, processed millicelsius). Both providers are required at probe;
the new module must be paired with the updated DTB.
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
python3 -I -B drivers/dby-chg/test-pm.py
```

## System sleep preparation (offline)

Only `usbin-plugin` is attached as a system wake IRQ, using
`device_init_wakeup()` and `devm_pm_set_wake_irq()`. The PM core arms and
disarms it according to the device's `power/wakeup` setting. Managed cleanup
detaches the wake IRQ before disabling wakeup and freeing the IRQs; setup
errors fail probe. Old DTBs without the complete IRQ set retain polling and
do not gain wake capability. Charge-state and watchdog IRQs remain ordinary
IRQs. This is preparation for battery-only suspend; suspend while charging
is not supported by the intended policy because the watchdog needs service.

There is no suspend callback or new register write. Resume (also thaw/restore)
queues the existing freezable worker immediately, with the existing two-second
edge settling sample and stop guard. Hardware access occurs after thaw through
the unchanged sampling/control path. This does not enable automatic suspend
or establish that USB insertion wakes this board; suspend and wake reliability
remain unverified (U). The host PM test executes the actual IRQ setup and resume
functions with injected resource/setup failures and teardown checks.

## 中文摘要

1042 控制电池停充/恢复，1643 喂狗表示驱动健康；温度、电压或 FULL 停充时继续
喂狗，读失败停喂，写失败永久停止管理。运行期严格遵守30秒间隔与滑动小时12次
预算；等待间隔期间实际充电状态可能尚未改变。卸载只在最近评估允许充电且拥有
停充状态时尝试恢复一次，允许这一次间隔/预算例外，失败不重试；不允许则保持
禁用并记录日志；shutdown 依次恢复 1344=00、1342=00、1366=02、1370=1E，不写原有充电控制寄存器。故障咬合后仍需重新插线。单独使能位实验有
实机证据；整合版已在一台设备自动加载并通过桌面标示、停充/恢复与插拔测试（H + R，
2026-11-05），**但未连续运行数日，长期风险未知**；自然FULL/再充电、挂起、
多温度点与卸载恢复例外仍未验证。电脑口约 500 mA，屏亮时可能净放电。

## Source and risk disclosure / 来源与风险披露

This project implementation uses standard Linux regmap, IIO and power-supply APIs under its GPL-2.0-only file notices. The register map and board policy were developed from stock/downstream hardware descriptions and project read-only register observations, followed by bounded enable/watchdog experiments; they are not a newly imported vendor driver. A complete historical derivation audit has not been established. The charger register addresses written are `0x1070/0x1643/0x1042` at the four original call sites, plus bounded `0x1370/0x1344/0x1342/0x1366`, each at one shared call site; these writes can change charging behavior. The standard ADC provider performs its own conversion operations, so the charger write list is not a claim that no other PMIC block is written.

项目实现使用标准 regmap／IIO／power_supply API，文件声明 GPL-2.0-only；寄存器与策略来自原厂／下游硬件描述、项目只读观测及后续限范围实验，不是新导入的厂商驱动，完整历史推导归属仍未独立核清。会写的充电地址为 `0x1070/0x1643/0x1042`（原四处调用）及 `0x1370/0x1344/0x1342/0x1366`（各一个受限调用点），可能改变充电行为；标准 ADC 提供者另有转换操作。自然充满／再充电、挂起、长期和多温度点仍未验证；固定 9 V/1.5 A 与输入限流已在一台设备实机通过（2026-11-16，H），电脑 USB 口屏亮时可能净放电。

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

The board retains the owner stock 5 V / 2 A sink PDO and requests up to
9 V / 3 A with 27 W operating sink power. The latter is a contract ceiling,
while the default input-current ceiling is 2.5 A at 9 V (22.5 W).
Source PDOs and dual-role policy are unchanged.
The charger links to the Type-C supply with standard `power-supplies`;
`.external_power_changed` queues the existing work immediately and restarts
the two-second temperature hold and edge settle deadline described above.
There is no new poller or notifier chain. Each existing evaluation reads ONLINE,
USB_TYPE, VOLTAGE_NOW and CURRENT_MAX with
`power_supply_get_property_from_supplier()`; no private supplier reference is
held. With the new DTB, the core defers charger registration until the supplier
is registered. With an old DTB lacking the link, supplier data is unavailable
and runtime ICL does not increase.

Fixed PD means supplier `ONLINE == 1` and USB_TYPE is `PD`, `PD_PPS`,
`PD_SPR_AVS` or `PD_PPS_SPR_AVS`. USB_TYPE advertises the source's capabilities;
a PPS-capable source can still supply a fixed PDO. Active PPS/AVS (`ONLINE`
2/3) is not managed or requested. Type `C` with ONLINE 1 remains non-PD.

| Observation | ICL target at `0x1370` (50 mA per step) | `0x1344` | Override `1342/1366` |
|---|---|---|---|
| Confirmed online → offline | Restore firmware `1E` | `00` | Off: `00/02` |
| Stable offline / OTG, or USB state unreadable | No ICL write | `00` | Off: `00/02` |
| Supplier offline/unreadable or invalid/nonpositive contract values | No ICL change | `00` | Off: `00/02` |
| Online Type-C, Rp current >=3 A | `28` (2.0 A) | `00` | On: `01/01` |
| Online Type-C, Rp >=1.5 A and <3 A | `1E` (1.5 A) | `00` | On: `01/01` |
| Online Type-C, default Rp / PC port (<1.5 A) | No target; automatic mode | `00` | Off: `00/02` |
| Online fixed PD, voltage >=8.5 V | min(contract, selected ceiling, thermal cap, low-battery cap) / 50 mA; default ceiling 2.5 A, thermal floor 1.5 A | `02` | On: `01/01` |
| Online fixed PD, 5 V and 0 < current <=0.5 A standby | `0A` (0.5 A), verified before allowance | `08` | On: `01/01` |
| Online fixed PD, 5 V and current >0.5 A | min(contract current, 2.0 A) / 50 mA | `00` | On: `01/01` |
| Active PPS/AVS, unknown source, other PD voltage below 8.5 V | No ICL change | `00` | Off: `00/02` |

PD targets round down to 50 mA steps. 5 V targets remain at most `28` (2 A);
only fixed PD >=8.5 V can use `29..3C` (up to 3 A). The overall range is `0A..3C`. Default Rp yields
no target, keeping PC ports in firmware automatic mode instead of forcing
1.5 A. AICL may settle below a requested limit. This is a bounded board policy,
not a general current-limit algorithm for other hardware.

Enabling override applies `1370=target`, then `1366=01`, then `1342=01`; only
after all three succeed may a 9 V contract set `1344=02`. Fixed-PD 5 V/<=500 mA
standby verifies ICL `0A` before setting `1344=08`, allowing the voltage ramp;
the established 9 V contract applies its current target before returning to
`02`. Other managed 5 V contracts clear the previous allowance first. With override already
on, unchanged option/override bytes are read and skipped; a changed target
only writes ICL (and allowance if the voltage changes). Disabling follows
`1344=00` → `1342=00` → `1366=02`; confirmed unplug then restores `1370=1E`.
Stable OTG with default bytes performs no writes.

Every step checks identity/regmap, reads before writing, skips equal bytes,
and reads back writes. Any step failure, including a pre-write read failure,
stops the normal sequence and latches **only input-current management**.
Recovery attempts `1344=00` → `1342=00` → `1366=02` → `1370=1E`, once each,
even when an earlier recovery step fails. Failed recovery reads also consume
the opportunity. Later events/unplug/remove/shutdown cannot retry it. Normal
remove/shutdown use the same ordered recovery sequence. Enable/temperature/
FULL policy, toggle budget and watchdog pet gates are unchanged.

Summary/event logs and debugfs include `icl_reg`, `allow_reg`, `ovr_reg`,
`opt_reg`, their write-attempt counters, `src`, contract values, per-register
read/operation errors and the shared latch. `icl_management_errors` counts
failed input-control operations (including identity/pre-read failures);
aggregate `write_errors` counts failed writes/readback only. Read-error fields
mark stale bytes. Supplier read failure selects automatic mode without itself
latching management. Successful recovery can clear current operation errors
while the stopped flag and cumulative error count remain latched.

Device results supplied for the 2026-11-15 predecessor: a non-PD Rp 3 A charger
reached `1370=28`, AICL status `28`, about +0.94 A battery current with the screen
on (previously +0.22 A), and restored `1E` on unplug. A Lenovo source negotiated
a fixed 9 V/1.5 A contract, but `1310` bit3 (USBIN overvoltage) was asserted and
the battery discharged at about -1.09 A. `1344` was still `00` despite inherited
`1360=07`: the earlier assumption that the latter alone permits 9 V was refuted.
The source advertised PPS and was incorrectly classified as unknown, retaining
ICL `28`. These observations motivate fixed-contract classification and the
bounded FORCE_9V override, following the stock smb5 allowance flow.

The subsequent FORCE_9V candidate charged from 81% to 88% in 15 minutes at
9 V/1.5 A, with reported battery temperature 26.9–29.3 C, no hard resets and
no write errors. However, `1370=1E` did not constrain the input: ICL status
`1107..1109=2C` indicated about 2.2 A with automatic `1342=00 / 1366=02`.
The owner stock comparison used `1342=01 / 1366=01 / 1370=1E` with status
`1A`. These supplied device observations motivate the explicit override and
high-current option, consistent with the stock smb5 ICL override flow. The
old non-PD 5 V result happened to follow `1370` in automatic mode and did not
prove 9 V limiting.

**Device result (bundle `20261115-11`, one tablet, 2026-11-16, H).** Lenovo PD
charger: fixed 9 V/1.5 A contract, `1342=01`, `1366=01`, `1370=1E`, `1344=02`,
ICL status `1106..1109=1E`, charging, no hard reset. 3 A Rp charger: `src=C`,
`1370=28`, ICL status `28`, charging. Unplug restores `1344/1342/1366/1370 =
00/00/02/1E`. PC USB port (default Rp, SDP): no override, hardware picks 500 mA
(`1106=0A`); net discharge with the screen on, as before. OTG receiver: offline,
LEDs off, no writes. `write_errors=0` throughout. Earlier (bundle `-10`, before
the override) a 15-minute 9 V run went 81→88% at 26.9–29.3 °C. No `1340/1341`, `1362`, fast-charge current (`1061`),
watchdog configuration or other allowance values are written. Higher-current PD operation remains subject to the selected limit and thermal
policy below; no Huawei 22.5 W protocol is
implemented.

Acknowledgements: the owner stock `smb5-lib.c` flow and a community Type-C PD
proposal based on the CoWave kernel informed the approach. The community
proposal has no established licence declaration here; no implementation code
was copied. This driver uses independently written standard power-supply API
integration and the bounded board policy above.

## Fixed PD power ceiling and thermal derating

`pd9v_max_ma` is a module parameter with mode `0644` (root writes). It accepts
only `1500`, `2000`, `2250`, `2500`, `2750` or `3000`, default **2500**. The thermal
floor and temperature-read failure fallback remain **1500 mA**, independently
of this default. Each
accepted write schedules an immediate evaluation on the existing work queue,
without starting or extending the temperature-hold window. Device
registration/removal and scheduling
are serialized; parameter writes cannot queue work after detach. No new poller
or register write site is added.

Each evaluation, including the existing 30-second cycle, reads charger
millicelsius and battery temperature before the existing enable policy. The
resulting ICL is applied in that evaluation. Thermal rules:

- Battery >=38.0 C or charger >=80.0 C: lower the cap by 250 mA, floor 1500 mA.
- Battery <=36.0 C and charger <=70.0 C: raise by 250 mA only after at least
  60 seconds since the last cap change, never above the selected ceiling.
- Charger read failure immediately caps at 1500 mA. Battery temperature read
  failure is also treated conservatively; it cannot authorize a power increase.
- Offline resets the cap to the selected ceiling. A lower parameter immediately
  clamps the cap; raising it preserves an existing cap and the cool/60-second
  recovery rule. Reset/parameter clamping update the time reference but do not
  count as thermal drops/rises. Failed reads cannot authorize recovery.

The original <12 C / >42 C battery stop gates and all enable/watchdog control
remain unchanged. Logs/debugfs add `chg_temp_mc`, `chg_temp_error`,
`pd9v_max_ma`, `thermal_cap_ma`, `thermal_drops`, `thermal_rises`; each actual
thermal drop logs a separate line. Values in debugfs describe the last sample,
while the module parameter reflects the requested ceiling immediately.

An earlier note read `1570=00` as the thermal-source register and concluded
that hardware thermal regulation was probably disabled. That address came from
an unverified third-party patch and lies in the Type-C block; the whole
`0x1570–0x1579` range reads `00`. A read-only dump on 2026-11-16 (H, charging)
shows the MISC-block register `0x1670=13` and non-zero values at `0x1680–0x1689`.
By the usual SMB5 layout this would mean die, skin and connector thermal sources
are enabled with thresholds programmed before Linux (interpretation U; no
datasheet). The driver never writes these registers. Software sampling is
separate from any hardware thermal loop. The ramp is recorded below; actual
input power and sustained operation with a case remain unmeasured.
The separate device procedure stops at battery >=38 C, charger >=75 C, any
thermal drop, hard reset or a hot enclosure; its charger stop threshold is
intentionally lower than the 80 C software derating threshold.

## Owner-supplied PD ramp observations (2026-11-16)

The task book supplies hardware evidence (H) from the owner's ramp log and
separate 1500 mA baseline, using bundle `20261116-1`, a Lenovo source with a
9 V / 3 A contract, half screen brightness and the case removed. The 2000 mA
step ran for 9 minutes; the higher steps ran for 5 minutes each at the owner's
request. The separate 1500 mA baseline duration is unspecified. SOC and step
duration differ, so these are not controlled comparisons under identical
conditions. Enclosure feel is the owner's observation (R).

| Ceiling (mA) | ICL | Stable battery current | Battery temperature | Charger temperature | SOC | Enclosure feel (R) |
|---|---|---|---|---|---|---|
| 1500 | `1e` | +1.6–1.7 A | 28–29 C | 41–42 C | 31% | — |
| 2000 | `28` | +2.8–3.0 A | 29–30 C | 52–53 C | 33–39% | — |
| 2250 | `2d` | +3.1–3.4 A | 31 C | 58.5 C | 39–43% | Barely noticeable |
| 2500 | `32` | +3.4–3.6 A | 31.5–32 C | 62 C | 44–47% | Warm |
| 2750 | `37` | +3.6–3.74 A | 32.6 C | 65 C | 47–52% | Warm |
| 3000 | `3c` | +3.7–3.9 A | 33.3 C | 68.2 C | 52–57% | — |

The supplied record reports `thermal_drops=0`, no hard reset,
`write_errors=0`, `1006=03` and `allow=02` throughout. Above 2500 mA, each
step added roughly 0.2 A of battery current and 3 C of charger temperature.
The owner selected 2500 mA as the default: the observed 62 C is 8 C below the
70 C recovery gate and 18 C below the 80 C derating gate. Leaving margin for
a case is the selection rationale; operation with a case was not tested here.
The 22.5 W figure is the input ceiling, not measured input or battery power.
The thermal floor/read-failure fallback remains 1500 mA; 5 V limits, parameter
choices and all temperature thresholds remain unchanged.

Device acceptance (2026-11-16, H, bundle `20261116-3`, one tablet, 66%): the
default 2500 mA gave `1370=32` on a Lenovo fixed 9 V/3 A contract with battery
current +3.2–3.5 A; the transition was logged as `allow 08` (ICL `0a`) and
then `02` about 0.28 s later, with no hard reset. A 3 A Rp charger gave
`28/00/01/01`; unplug restored `1e/00/00/02`; a PC port stayed in firmware
automatic mode; OTG enumerated a USB disk with no charger writes;
`write_errors=0` throughout. About 100 s after boot (no case), the battery
thermistor read 39.4 C and the cap dropped to 2250 mA, recovering to 2500 mA
two minutes later; the fast rise and fall suggest board heat during boot rather
than cell temperature (U). The low-battery screen-on 9 V case is not yet tested.

## PD transition and low battery guard

The 2026-11-16 task supplied these observations for the preceding candidate:
an empty battery at 0%, about 2.89 V, with maximum screen brightness saw the
Lenovo source repeatedly attempt 9 V/3 A, enter 5 V/500 mA standby and hard
reset before PS_RDY, with input absent and about -3.06 A battery current.
At 31% with half brightness, the same request reached SNK_READY in about
0.27 s without hard resets; battery current was +1.56–1.73 A and charger
temperature 40.8–41.4 C. This shows the 3 A request can work, but does not
establish which factor causes the empty-battery failure. The proposed input
interruption/overvoltage explanation remains a hypothesis.

The bounded workaround identifies a transition only from fixed-PD supplier
ONLINE 1, exactly 5 V and positive current <=500 mA, while charger input is
online. It verifies `1370=0A` and the existing override sequence before
`1344=08`. Current errors or a failed preceding operation cannot authorize
08. Non-PD, offline, latched management and exit still select `00`; PPS/AVS
remain unsupported. The existing `allow_reg` event/summary/debugfs field
shows `08` and then `02`; short transitions may complete between evaluations.
No polling loop, delay or new register write site is added.

A separate persistent `pd_low_batt` guard caps only fixed PD >=8.5 V at 1 A
when measured battery voltage is below 3.5 V; it releases at >=3.6 V, retaining
its previous state in between. It starts enabled until a >=3.6 V measurement,
and voltage-read failures enable it conservatively. It is independent of the
1500 mA thermal floor: the final target takes the smaller limit. Parameter
changes/unplug do not erase the voltage hysteresis. All 5 V rules are unchanged.
`pd_low_batt` is included in summary logs and debugfs for validation.

The same task reported unintended overnight screen-on due to lid events,
UPower's old Ignore policy, deep discharge and an unclean filesystem shutdown.
The repository rootfs overlay now sets `CriticalPowerAction=PowerOff` and
`AllowRiskyCriticalPowerAction=false`, since dby-chg provides online/status.
This configuration is for future rootfs builds; the p1-only candidate does not
install or update it on an existing card. No lid-sensor fix is included.

固定 PD 待机先限 0.5 A 再设 allowance=08；9 V 合同后回 02。低于 3.5 V 时
9 V 再限 1 A，达到 3.6 V 解除。过渡与回归已在设备上通过（H，见上节）；空电池
失败的主因与低电量亮屏升压仍待验证。UPower 关机策略只改仓库 rootfs overlay，
不随 p1 包部署。
