# PM8150B passive battery gauge / 电量只读驱动

Reads existing PM8150B fuel-gauge samples without initializing or programming the gauge. Exposes capacity, voltage, current and status through the standard power_supply interface. Status delegates to the optional `dby-chg` power supply, falling back to Unknown when it is absent or unavailable. All gauge reads remain passive; this module does not control charging.

依赖 QCOM_SPMI_PMIC、REGMAP、POWER_SUPPLY 和 OF。仅接受源码中限定的 PMIC 类型、版本与寄存器范围；不初始化电量计、不管理充电。构建示例见仓库根 README；当前候选目录未重新构建或验证运行。

## Device tree

节点必须放在实际的 PM8150B SPMI PMIC 节点下，继承该父节点的 `#address-cells = <1>`、`#size-cells = <0>`：

```dts
battery@4000 {
    compatible = "dby,pm8150b-fg-readonly";
    reg = <0x4000>, <0x4100>;
};
```

两项 reg 是寄存器窗口地址，不是地址加长度。驱动从父设备取得 regmap 与已缓存的 PMIC 身份，拒绝不匹配的节点和硬件版本。无需 IRQ、固件或用户空间采样守护进程。

标准接口位于 `/sys/class/power_supply/dby-fg/`；voltage_now 单位微伏，current_now 单位微安，充电为正、放电为负。不支持的或暂不可用的样本会返回错误，不能据此推断充电控制已实现。
