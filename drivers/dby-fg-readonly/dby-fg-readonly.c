// SPDX-License-Identifier: GPL-2.0-only
/* DBY-W09: passive PM8150B FG gen4 samples; never initialize the gauge. */
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/power_supply.h>
#include <linux/regmap.h>
#include <soc/qcom/qcom-spmi-pmic.h>

#include "dby-fg-core.h"

static int dby_fg_read(void *context, unsigned int reg, u8 *buf,
		       unsigned int count)
{
	struct regmap *map = context;
	unsigned int value;
	int ret;

	if (!dby_fg_range(reg, count))
		return -EINVAL;
	if (count == 1) {
		ret = regmap_read(map, reg, &value);
		if (!ret)
			*buf = value;
		return ret;
	}
	return regmap_bulk_read(map, reg, buf, count);
}

static enum power_supply_property dby_fg_properties[] = {
	POWER_SUPPLY_PROP_CAPACITY,
	POWER_SUPPLY_PROP_VOLTAGE_NOW,
	POWER_SUPPLY_PROP_CURRENT_NOW,
	POWER_SUPPLY_PROP_STATUS,
};

static int dby_fg_get(struct power_supply *psy, enum power_supply_property prop,
		      union power_supply_propval *val)
{
	int item;
	struct power_supply *charger;

	switch (prop) {
	case POWER_SUPPLY_PROP_STATUS:
		val->intval = POWER_SUPPLY_STATUS_UNKNOWN;
		charger = power_supply_get_by_name("dby-chg");
		if (charger) {
			if (power_supply_get_property(charger, prop, val))
				val->intval = POWER_SUPPLY_STATUS_UNKNOWN;
			power_supply_put(charger);
		}
		return 0;
	case POWER_SUPPLY_PROP_CAPACITY:
		item = 0;
		break;
	case POWER_SUPPLY_PROP_VOLTAGE_NOW:
		item = 1;
		break;
	case POWER_SUPPLY_PROP_CURRENT_NOW:
		item = 2;
		break;
	default:
		return -EINVAL;
	}
	return dby_fg_sample(dby_fg_read, power_supply_get_drvdata(psy),
			     item, &val->intval);
}

static const struct power_supply_desc dby_fg_desc = {
	.name = "dby-fg",
	.type = POWER_SUPPLY_TYPE_BATTERY,
	.properties = dby_fg_properties,
	.num_properties = ARRAY_SIZE(dby_fg_properties),
	.get_property = dby_fg_get,
};

static int dby_fg_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	const struct qcom_spmi_pmic *pmic;
	struct power_supply_config config = {};
	struct power_supply *psy;
	struct regmap *map;
	u32 windows[2];
	int ret;

	if (of_property_count_u32_elems(dev->of_node, "reg") != 2)
		return -EINVAL;
	ret = of_property_read_u32_array(dev->of_node, "reg", windows, 2);
	if (ret)
		return ret;
	if (windows[0] != 0x4000 || windows[1] != 0x4100)
		return -EINVAL;
	/* Parent has already read identity; this accessor performs no bus I/O. */
	pmic = qcom_pmic_get(dev);
	if (IS_ERR(pmic))
		return dev_err_probe(dev, PTR_ERR(pmic), "dby-fg: no identity\n");
	if (!dby_fg_revision(pmic->type, pmic->subtype, pmic->major,
			     pmic->minor, pmic->rev2))
		return dev_err_probe(dev, -ENODEV, "dby-fg: unsupported revision\n");
	map = dev_get_regmap(dev->parent, NULL);
	if (!map)
		return -EPROBE_DEFER;
	config.drv_data = map;
	config.fwnode = dev_fwnode(dev);
	psy = devm_power_supply_register(dev, &dby_fg_desc, &config);
	if (IS_ERR(psy))
		return dev_err_probe(dev, PTR_ERR(psy), "dby-fg: register failed\n");
	dev_info(dev, "dby-fg: passive read-only gauge registered; status Unknown\n");
	return 0;
}

static const struct of_device_id dby_fg_match[] = {
	{ .compatible = "dby,pm8150b-fg-readonly" },
	{ }
};
MODULE_DEVICE_TABLE(of, dby_fg_match);

static struct platform_driver dby_fg_driver = {
	.probe = dby_fg_probe,
	.driver = {
		.name = "dby-fg-readonly",
		.of_match_table = dby_fg_match,
	},
};
module_platform_driver(dby_fg_driver);

MODULE_DESCRIPTION("DBY-W09 PM8150B passive read-only battery gauge");
MODULE_AUTHOR("DBY-W09-linux contributors");
MODULE_LICENSE("GPL");
