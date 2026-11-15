# PM8150B passive battery gauge / 电量只读驱动

Reads existing PM8150B fuel-gauge samples without initializing or programming the gauge. Exposes capacity, voltage, current and status through the standard power_supply interface. Status delegates to the optional `dby-chg` power supply, falling back to Unknown when it is absent or unavailable. All gauge reads remain passive; this module does not control charging.

依赖 QCOM_SPMI_PMIC、REGMAP、POWER_SUPPLY 和 OF。仅接受源码中限定的 PMIC 类型、版本与寄存器范围；不初始化电量计、不管理充电。构建示例见仓库根 README；本次中断与设计参数改动仅离线验证，实机行为待确认。

## Device tree

节点必须放在实际的 PM8150B SPMI PMIC 节点下，继承该父节点的 `#address-cells = <1>`、`#size-cells = <0>`：

```dts
battery@4000 {
    compatible = "dby,pm8150b-fg-readonly";
    reg = <0x4000>, <0x4100>;
    monitored-battery = <&battery>;
    interrupts = <0x2 0x40 0x3 IRQ_TYPE_EDGE_RISING>;
    interrupt-names = "msoc-delta";
};
```

两项 reg 是寄存器窗口地址，不是地址加长度。驱动从父设备取得 regmap 与已缓存的 PMIC 身份，拒绝不匹配的节点和硬件版本。`msoc-delta` 为可选中断；旧 DTB 未提供时保留原有读取/通知方式，无需固件或用户空间采样守护进程。

标准接口位于 `/sys/class/power_supply/dby-fg/`；voltage_now 单位微伏，current_now 单位微安，充电为正、放电为负。不支持的或暂不可用的样本会返回错误，不能据此推断充电控制已实现。


## SOC interrupt and battery information / 电量中断与设计参数

The optional `msoc-delta` threaded IRQ only calls `power_supply_changed()` to
notify consumers immediately; it performs no register access. The SOC delta
threshold is inherited from FG SRAM and remains unknown (U); this driver does
not read or write that threshold. Missing IRQ resources preserve old-DTB
operation; IRQ-controller deferral is propagated. IRQ resources are released
before the power supply on probe failure or removal. The standard SPMI irqchip
owns interrupt configuration writes; this gauge driver still has zero writes
and the same `dby_fg_range()` read allowlist.

The board's root `battery` node uses `simple-battery`; the battery power-supply
core automatically exposes its linked design information without custom driver
properties: Li-poly, 7150000 uAh design charge and 4400000 uV maximum design
voltage. **27670500 uWh design energy is an estimate**, calculated as 7150 mAh
× 3.87 V; that nominal voltage is not present in the stock FDT and is unverified.
It is not measured remaining energy. Capacity and maximum voltage come from
the owner stock battery profiles. Temperature is not exposed. Health/cycle
counts requiring IMA SRAM writes are outside this read-only implementation.

Device result (one tablet, 2026-11-15, H): the IRQ registered and only bit 3 of
`0x4015` was set; the four design properties appeared in sysfs. During 12 min of
discharge (91% → 87%) the IRQ fired 9 times, about every 60–130 s, i.e. about
2–3 times per displayed percent, but **never at the moment `capacity` changed**
(12–40 s apart). It therefore does not track the displayed SOC; displayed changes
still reach userspace through the charger's 30 s notification. Kept for now as a
harmless extra notification; what quantity it follows is unknown (U).

`msoc-delta` 只通知标准 power_supply 消费者，不在处理函数读写寄存器；每变化
多少电量触发一次取决于继承的 FG SRAM 阈值（未知），本驱动不修改。
设计容量 7150 mAh、最高设计电压 4.4 V 来自原厂配置；设计能量 27.6705 Wh
按未证实的标称电压 3.87 V 估算，不是测量值。`simple-battery` 参数由核心自动
公开，不新增驱动属性；不显示温度，不做需要 IMA SRAM 写入的健康度/循环次数。
实机(一台平板,2026-11-15,H):中断注册成功,`0x4015` 只多 bit3;四个设计参数出现在 sysfs。放电 12 分钟(91%→87%)中断 9 次、约 60–130 s 一次,即每个显示百分点 2–3 次,但**没有一次与 `capacity` 变化同时**(相差 12–40 s),说明它跟随的不是显示电量;显示变化仍靠充电驱动 30 s 一次的通知送达。暂时保留(只是多一次无害通知),它跟随的是哪个量未知(U)。
