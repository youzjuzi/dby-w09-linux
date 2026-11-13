# RT8555 backlight / 背光驱动

Brightness-only I2C driver. It retains the bootloader's configuration and only accesses registers 0x04 and 0x05. Brightness is 0–1023; 0 blanks the backlight. It is not a complete RT8555 power or regulator driver.

仅控制亮度，沿用 Bootloader 初始化状态；不负责面板供电。依赖内核 I2C、REGMAP_I2C、BACKLIGHT_CLASS_DEVICE、OF。构建示例见仓库根 README；需要已准备的 arm64 内核源码及构建目录。当前候选目录未重新构建或验证运行。

## Device tree

在实际连接 RT8555 的 I2C 控制器节点下添加：

```dts
backlight_node: backlight@31 {
    compatible = "richtek,rt8555";
    reg = <0x31>;
};
```

面板节点通过 `backlight = <&backlight_node>;` 引用它。`backlight_node` 是示例标签，不是固定硬件地址或总线编号；控制器、pinmux 和面板节点必须与板级设备树配套。驱动会拒绝不是 0x31 的 I2C 地址。

用户空间接口为 `/sys/class/backlight/rt8555-backlight/`。关屏时由面板与 backlight 核心调用熄灭，开屏应恢复请求亮度；本次整理未在设备上核验该行为。
