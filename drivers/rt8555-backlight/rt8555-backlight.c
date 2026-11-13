// SPDX-License-Identifier: GPL-2.0-only
/* Minimal RT8555 brightness control; retain the bootloader's configuration. */
#include <linux/backlight.h>
#include <linux/err.h>
#include <linux/i2c.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/regmap.h>

#include "rt8555-level.h"

#define RT8555_ADDRESS		0x31
#define RT8555_BRIGHTNESS_LSB	0x04
#define RT8555_BRIGHTNESS_MSB	0x05
#define RT8555_MAX_BRIGHTNESS	1023

struct rt8555 {
	struct regmap *regmap;
	/* Serialize the two-byte brightness transactions. */
	struct mutex lock;
};

static bool rt8555_accessible_reg(struct device *dev, unsigned int reg)
{
	return reg == RT8555_BRIGHTNESS_LSB || reg == RT8555_BRIGHTNESS_MSB;
}

static const struct regmap_config rt8555_regmap_config = {
	.reg_bits = 8,
	.val_bits = 8,
	.max_register = RT8555_BRIGHTNESS_MSB,
	.readable_reg = rt8555_accessible_reg,
	.writeable_reg = rt8555_accessible_reg,
	.cache_type = REGCACHE_NONE,
};

static int rt8555_read_level(struct rt8555 *chip)
{
	unsigned int lsb, msb;
	int ret;

	mutex_lock(&chip->lock);
	ret = regmap_read(chip->regmap, RT8555_BRIGHTNESS_LSB, &lsb);
	if (ret)
		goto out;
	ret = regmap_read(chip->regmap, RT8555_BRIGHTNESS_MSB, &msb);
	if (ret)
		goto out;
	ret = ((msb & 0x03) << 8) | lsb;
out:
	mutex_unlock(&chip->lock);
	return ret;
}

static int rt8555_get_brightness(struct backlight_device *bd)
{
	return rt8555_read_level(bl_get_data(bd));
}

static int rt8555_update_status(struct backlight_device *bd)
{
	struct rt8555 *chip = bl_get_data(bd);
	unsigned int level = backlight_get_brightness(bd);
	int ret;

	/* User authorized brightness 0 for panel blanking on 2026-10-19. */
	if (level > RT8555_MAX_BRIGHTNESS)
		return -EINVAL;

	level = rt8555_adjust_level(level);
	mutex_lock(&chip->lock);
	ret = regmap_write(chip->regmap, RT8555_BRIGHTNESS_LSB, level & 0xff);
	if (!ret)
		ret = regmap_write(chip->regmap, RT8555_BRIGHTNESS_MSB,
				   (level >> 8) & 0x03);
	mutex_unlock(&chip->lock);
	return ret;
}

static const struct backlight_ops rt8555_ops = {
	.update_status = rt8555_update_status,
	.get_brightness = rt8555_get_brightness,
};

static int rt8555_probe(struct i2c_client *client)
{
	struct backlight_properties props = { 0 };
	struct device *dev = &client->dev;
	struct backlight_device *bd;
	struct rt8555 *chip;
	int level;

	if (client->addr != RT8555_ADDRESS)
		return -EINVAL;

	chip = devm_kzalloc(dev, sizeof(*chip), GFP_KERNEL);
	if (!chip)
		return -ENOMEM;

	mutex_init(&chip->lock);
	chip->regmap = devm_regmap_init_i2c(client, &rt8555_regmap_config);
	if (IS_ERR(chip->regmap))
		return PTR_ERR(chip->regmap);

	level = rt8555_read_level(chip);
	if (level < 0)
		return level;

	props.type = BACKLIGHT_RAW;
	props.max_brightness = RT8555_MAX_BRIGHTNESS;
	props.brightness = level;
	bd = devm_backlight_device_register(dev, "rt8555-backlight", dev,
					    chip, &rt8555_ops, &props);
	return PTR_ERR_OR_ZERO(bd);
}

static const struct of_device_id rt8555_of_match[] = {
	{ .compatible = "richtek,rt8555" },
	{ }
};
MODULE_DEVICE_TABLE(of, rt8555_of_match);

static struct i2c_driver rt8555_driver = {
	.driver = {
		.name = "rt8555-backlight",
		.of_match_table = rt8555_of_match,
	},
	.probe = rt8555_probe,
};
module_i2c_driver(rt8555_driver);

MODULE_DESCRIPTION("RT8555 brightness-only backlight driver");
MODULE_LICENSE("GPL");
