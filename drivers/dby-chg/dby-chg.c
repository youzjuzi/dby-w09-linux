// SPDX-License-Identifier: GPL-2.0-only
/* PM8150B guarded watchdog management; hardware watchdog remains enabled. */
#include <linux/debugfs.h>
#include <linux/iio/consumer.h>
#include <linux/interrupt.h>
#include <linux/leds.h>
#include <linux/list.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/power_supply.h>
#include <linux/regmap.h>
#include <linux/seq_file.h>
#include <linux/timekeeping.h>
#include <linux/workqueue.h>
#include <soc/qcom/qcom-spmi-pmic.h>
#include "dby-chg-core.h"

static int temp_min_decic = DBY_CHG_TEMP_MIN;
static int temp_max_decic = DBY_CHG_TEMP_MAX;
static DEFINE_MUTEX(chg_limits_lock);
static int chg_set_limit(const char *value, const struct kernel_param *kp)
{
	int proposed, low, high, ret = kstrtoint(value, 0, &proposed);

	if (ret)
		return ret;
	mutex_lock(&chg_limits_lock);
	low = kp->arg == &temp_min_decic ? proposed : temp_min_decic;
	high = kp->arg == &temp_max_decic ? proposed : temp_max_decic;
	if (!dby_chg_limits_valid(low, high))
		ret = -EINVAL;
	else
		WRITE_ONCE(*(int *)kp->arg, proposed);
	mutex_unlock(&chg_limits_lock);
	return ret;
}
static const struct kernel_param_ops chg_limit_ops = {
	.set = chg_set_limit, .get = param_get_int,
};
module_param_cb(temp_min_decic, &chg_limit_ops, &temp_min_decic, 0600);
module_param_cb(temp_max_decic, &chg_limit_ops, &temp_max_decic, 0600);
MODULE_PARM_DESC(temp_min_decic, "Test lower temperature bound, 120..350 deciC");
MODULE_PARM_DESC(temp_max_decic, "Test upper temperature bound, 150..420 deciC");

static int pd9v_max_ma = DBY_CHG_PD9V_DEFAULT_MA;
static DEFINE_MUTEX(chg_devices_lock);
static LIST_HEAD(chg_devices);

struct dby_chg_icl {
	unsigned int reg, writes, errors;
	unsigned int allow_reg, allow_writes;
	unsigned int ovr_reg, ovr_writes, opt_reg, opt_writes;
	int ovr_read_error, ovr_error, opt_read_error, opt_error;
	int allow_read_error, allow_error;
	int read_error, error, supplier_error, source_online, voltage_uv, current_ua;
	int last_online;
	enum dby_chg_source source;
	bool stopped, recovery_attempted;
};
struct dby_chg_snapshot {
	struct dby_chg_sample sample;
	struct dby_chg_state state;
	struct dby_chg_control control;
	struct dby_chg_pause pause;
	struct dby_chg_icl icl;
	struct dby_chg_thermal thermal;
	int chg_temp_mc, chg_temp_error, pd9v_max_ma;
	bool pd_low_batt;
	u64 sampled_ms;
	unsigned int reason, attempts, pets, write_errors;
	int write_error;
	bool evaluated, feeding;
};
struct dby_chg {
	struct device *dev;
	struct regmap *map;
	struct iio_channel *therm, *chg_temp;
	struct list_head parameter_node;
	struct power_supply *psy;
	struct delayed_work work;
	struct mutex operation_lock;
	spinlock_t status_lock;
	bool stopping;
	bool settle_pending;
	u64 settle_at_ms;
	u64 start_ms, last_attempt_ms, last_log_ms;
	struct dby_chg_snapshot latest;
	struct dentry *debug_dir;
	struct led_trigger *red, *green;
	unsigned int red_brightness, green_brightness;
};
/* Lock order: devices lock -> operation_lock. Evaluation never takes the
 * devices lock. Stop detaches before cancellation, so parameter writes cannot
 * queue work after teardown; the devm action also covers probe failures. */
static int chg_set_pd9v(const char *value, const struct kernel_param *kp)
{
	struct dby_chg *chg;
	int proposed, ret = kstrtoint(value, 0, &proposed);

	if (ret)
		return ret;
	if (!dby_chg_pd9v_valid(proposed))
		return -EINVAL;
	mutex_lock(&chg_devices_lock);
	WRITE_ONCE(*(int *)kp->arg, proposed);
	list_for_each_entry(chg, &chg_devices, parameter_node) {
		mutex_lock(&chg->operation_lock);
		if (!chg->stopping)
			mod_delayed_work(system_freezable_wq, &chg->work, 0);
		mutex_unlock(&chg->operation_lock);
	}
	mutex_unlock(&chg_devices_lock);
	return 0;
}
static const struct kernel_param_ops chg_pd9v_ops = {
	.set = chg_set_pd9v, .get = param_get_int,
};
module_param_cb(pd9v_max_ma, &chg_pd9v_ops, &pd9v_max_ma, 0644);
MODULE_PARM_DESC(pd9v_max_ma, "Fixed PD 9V current ceiling: 1500,2000,2250,2500,2750,3000 mA; default 2500");
static void chg_parameter_detach(void *data)
{
	struct dby_chg *chg = data;

	mutex_lock(&chg_devices_lock);
	list_del_init(&chg->parameter_node);
	mutex_unlock(&chg_devices_lock);
}
static const unsigned int sample_regs[CHG_NREG] = {
	0x1006, 0x1007, 0x1070, 0x1206, 0x1306, 0x1042
};
static u64 chg_now(struct dby_chg *chg)
{
	return div_u64(ktime_get_boottime_ns(), NSEC_PER_MSEC) - chg->start_ms;
}
static int chg_identity(struct dby_chg *chg)
{
	const struct qcom_spmi_pmic *p = qcom_pmic_get(chg->dev);

	if (IS_ERR(p))
		return PTR_ERR(p);
	return dby_chg_identity(p->type, p->subtype, p->major, p->minor, p->rev2)
		? 0 : -ENODEV;
}
static void chg_fg_sample(struct dby_chg_sample *s)
{
	struct power_supply *fg = power_supply_get_by_name("dby-fg");
	union power_supply_propval value;

	s->voltage_error = s->current_error = s->capacity_error = -ENODEV;
	if (!fg)
		return;
	s->voltage_error = power_supply_get_property(fg, POWER_SUPPLY_PROP_VOLTAGE_NOW, &value);
	if (!s->voltage_error)
		s->voltage_uv = value.intval;
	s->current_error = power_supply_get_property(fg, POWER_SUPPLY_PROP_CURRENT_NOW, &value);
	if (!s->current_error)
		s->current_ua = value.intval;
	s->capacity_error = power_supply_get_property(fg, POWER_SUPPLY_PROP_CAPACITY, &value);
	if (!s->capacity_error) {
		s->capacity = value.intval;
		if (s->capacity < 0 || s->capacity > 100)
			s->capacity_error = -ERANGE;
	}
	power_supply_put(fg);
}
static void chg_sample(struct dby_chg *chg, struct dby_chg_sample *s)
{
	int i;

	memset(s, 0, sizeof(*s));
	s->identity_error = chg_identity(chg);
	if (!s->identity_error && dev_get_regmap(chg->dev->parent, NULL) != chg->map)
		s->identity_error = -ENODEV;
	for (i = 0; i < CHG_NREG; i++)
		s->error[i] = s->identity_error ?: regmap_read(chg->map, sample_regs[i], &s->reg[i]);
	chg_fg_sample(s);
	/* ADC5 SCALE_HW_CALIB_DEFAULT processed output is already microvolts.
	 * Do not multiply by 1000 as for generic IIO millivolt providers. */
	s->temp_error = iio_read_channel_processed(chg->therm, &s->therm_uv);
	if (!s->temp_error)
		s->temp_error = dby_batt_therm_decic(s->therm_uv, DBY_CHG_REF_UV, &s->temp_decic);
}
/* Update before enable policy; apply the resulting ICL in this evaluation. */
static void chg_thermal_sample(struct dby_chg *chg, struct dby_chg_snapshot *s)
{
	int old = s->thermal.cap_ma;
	int direction;

	s->pd9v_max_ma = READ_ONCE(pd9v_max_ma);
	s->chg_temp_mc = 0;
	s->chg_temp_error = iio_read_channel_processed(chg->chg_temp, &s->chg_temp_mc);
	direction = dby_chg_thermal_update(&s->thermal, s->pd9v_max_ma,
		dby_chg_online(&s->sample), s->sample.temp_error, s->sample.temp_decic,
		s->chg_temp_error, s->chg_temp_mc, chg_now(chg));
	if (direction < 0)
		dev_info(chg->dev, "PD thermal drop: thermal_cap_ma=%d previous_ma=%d pd9v_max_ma=%d temp_decic=%d chg_temp_mc=%d batt_temp_error=%d chg_temp_error=%d drops=%u\n",
			 s->thermal.cap_ma, old, s->pd9v_max_ma, s->sample.temp_decic,
			 s->chg_temp_mc, s->sample.temp_error, s->chg_temp_error, s->thermal.drops);
}
static struct dby_chg_snapshot chg_snapshot(struct dby_chg *chg)
{
	struct dby_chg_snapshot s;

	spin_lock(&chg->status_lock);
	s = chg->latest;
	spin_unlock(&chg->status_lock);
	return s;
}
static void chg_publish(struct dby_chg *chg, const struct dby_chg_snapshot *s)
{
	spin_lock(&chg->status_lock);
	chg->latest = *s;
	spin_unlock(&chg->status_lock);
}
static void chg_notify(struct dby_chg *chg)
{
	struct power_supply *fg;

	power_supply_changed(chg->psy);
	/* Battery consumers also need an event when delegated STATUS changes. */
	fg = power_supply_get_by_name("dby-fg");
	if (fg) {
		power_supply_changed(fg);
		power_supply_put(fg);
	}
}
/* operation_lock held; LPG max=511, stock ratios 70/255 and 120/255. */
static void chg_led_set(struct dby_chg *chg, unsigned int red, unsigned int green)
{
	if (red != chg->red_brightness) {
		led_trigger_event(chg->red, red);
		chg->red_brightness = red;
	}
	if (green != chg->green_brightness) {
		led_trigger_event(chg->green, green);
		chg->green_brightness = green;
	}
}
static void chg_led_update(struct dby_chg *chg, const struct dby_chg_sample *s)
{
	struct power_supply *fg;
	union power_supply_propval status;
	int ret, online = dby_chg_online(s);

	if (online < 0 || s->capacity_error)
		return;
	fg = power_supply_get_by_name("dby-fg");
	if (!fg)
		return;
	/* Read after publishing: FG delegates STATUS to our current snapshot. */
	ret = power_supply_get_property(fg, POWER_SUPPLY_PROP_STATUS, &status);
	power_supply_put(fg);
	if (ret || status.intval == POWER_SUPPLY_STATUS_UNKNOWN)
		return;
	if (!online)
		chg_led_set(chg, 0, 0);
	else if (status.intval == POWER_SUPPLY_STATUS_FULL || s->capacity >= 90)
		chg_led_set(chg, 0, 240);
	else if (s->capacity < 10)
		chg_led_set(chg, 140, 0);
	else
		chg_led_set(chg, 140, 240);
}
static void chg_led_unregister(void *data)
{
	struct dby_chg *chg = data;

	led_trigger_unregister_simple(chg->green);
	led_trigger_unregister_simple(chg->red);
}
static const char *chg_source_name(enum dby_chg_source source)
{
	return source == CHG_SRC_C ? "C" : source == CHG_SRC_PD ? "PD" : "unknown";
}
static void chg_supplier_sample(struct dby_chg *chg, struct dby_chg_icl *icl)
{
	union power_supply_propval online, type, voltage, amperage;
	int ret;

	icl->source = CHG_SRC_UNKNOWN;
	icl->source_online = icl->voltage_uv = icl->current_ua = 0;
	ret = power_supply_get_property_from_supplier(chg->psy, POWER_SUPPLY_PROP_ONLINE, &online);
	if (!ret)
		ret = power_supply_get_property_from_supplier(chg->psy, POWER_SUPPLY_PROP_USB_TYPE, &type);
	if (!ret)
		ret = power_supply_get_property_from_supplier(chg->psy, POWER_SUPPLY_PROP_VOLTAGE_NOW, &voltage);
	if (!ret)
		ret = power_supply_get_property_from_supplier(chg->psy, POWER_SUPPLY_PROP_CURRENT_MAX, &amperage);
	icl->supplier_error = ret;
	if (ret)
		return;
	icl->source_online = online.intval;
	icl->voltage_uv = voltage.intval;
	icl->current_ua = amperage.intval;
	if (type.intval == POWER_SUPPLY_USB_TYPE_C)
		icl->source = CHG_SRC_C;
	else if (online.intval == 1 &&
		 (type.intval == POWER_SUPPLY_USB_TYPE_PD ||
		  type.intval == POWER_SUPPLY_USB_TYPE_PD_PPS ||
		  type.intval == POWER_SUPPLY_USB_TYPE_PD_SPR_AVS ||
		  type.intval == POWER_SUPPLY_USB_TYPE_PD_PPS_SPR_AVS))
		icl->source = CHG_SRC_PD;
	/* USB_TYPE describes source capabilities; ONLINE 1 is a fixed contract.
	 * ONLINE 2/3 (active PPS/AVS) must not enter the fixed-PDO policy. */
}
/* operation_lock held. All normal and recovery ICL writes share this site. */
static int chg_icl_set(struct dby_chg *chg, struct dby_chg_snapshot *s,
		       unsigned int target)
{
	struct dby_chg_icl *icl = &s->icl;
	int ret;

	BUILD_BUG_ON(DBY_CHG_ICL_REG != 0x1370 || DBY_CHG_ICL_MIN != 0x0a ||
		     DBY_CHG_ICL_MAX != 0x3c || DBY_CHG_ICL_5V_MAX != 0x28 ||
		     DBY_CHG_ICL_DEFAULT != 0x1e || DBY_CHG_PD9V_MAX_MA != 3000);
	if (target < DBY_CHG_ICL_MIN || target > DBY_CHG_ICL_MAX)
		return -EINVAL;
	/* Extended codes require a live fixed-PD contract and both ceilings. */
	if (target > DBY_CHG_ICL_5V_MAX &&
	    (dby_chg_online(&s->sample) != 1 || icl->source_online != 1 ||
	     s->pd_low_batt ||
	     icl->source != CHG_SRC_PD || icl->voltage_uv < 8500000 ||
	     (int)target * 50 > s->pd9v_max_ma || (int)target * 50 > s->thermal.cap_ma ||
	     (int)target * DBY_CHG_ICL_STEP_UA > icl->current_ua))
		return -EINVAL;
	ret = chg_identity(chg);
	if (!ret && dev_get_regmap(chg->dev->parent, NULL) != chg->map)
		ret = -ENODEV;
	if (!ret)
		ret = regmap_read(chg->map, DBY_CHG_ICL_REG, &icl->reg);
	icl->read_error = ret;
	if (ret) {
		icl->errors++;
		icl->stopped = true;
		return ret;
	}
	if (icl->reg == target)
		return 0;
	icl->writes++;
	ret = regmap_write(chg->map, DBY_CHG_ICL_REG, target);
	if (!ret) {
		ret = regmap_read(chg->map, DBY_CHG_ICL_REG, &icl->reg);
		icl->read_error = ret;
		if (!ret && icl->reg != target)
			ret = -EIO;
	} else {
		/* The cached byte cannot describe an ambiguous write result. */
		icl->read_error = ret;
	}
	if (ret) {
		s->write_errors++;
		icl->errors++;
		icl->stopped = true;
	}
	return ret;
}
/* operation_lock held; the sole allowance write site accepts only 00/02/08. */
static int chg_allow_set(struct dby_chg *chg, struct dby_chg_snapshot *s,
			 unsigned int target)
{
	struct dby_chg_icl *icl = &s->icl;
	int ret;

	BUILD_BUG_ON(DBY_CHG_ALLOW_REG != 0x1344 ||
		     DBY_CHG_ALLOW_DEFAULT != 0x00 || DBY_CHG_ALLOW_9V != 0x02 ||
		     DBY_CHG_ALLOW_TRANSITION != 0x08);
	if (target != DBY_CHG_ALLOW_DEFAULT && target != DBY_CHG_ALLOW_9V &&
	    target != DBY_CHG_ALLOW_TRANSITION)
		return -EINVAL;
	ret = chg_identity(chg);
	if (!ret && dev_get_regmap(chg->dev->parent, NULL) != chg->map)
		ret = -ENODEV;
	if (!ret)
		ret = regmap_read(chg->map, DBY_CHG_ALLOW_REG, &icl->allow_reg);
	icl->allow_read_error = ret;
	if (ret) {
		icl->errors++;
		icl->stopped = true;
		return ret;
	}
	if (icl->allow_reg == target)
		return 0;
	icl->allow_writes++;
	ret = regmap_write(chg->map, DBY_CHG_ALLOW_REG, target);
	if (!ret) {
		ret = regmap_read(chg->map, DBY_CHG_ALLOW_REG, &icl->allow_reg);
		icl->allow_read_error = ret;
		if (!ret && icl->allow_reg != target)
			ret = -EIO;
	} else {
		icl->allow_read_error = ret;
	}
	if (ret) {
		s->write_errors++;
		icl->errors++;
		icl->stopped = true;
	}
	return ret;
}
/* operation_lock held; 0x1342 accepts only its default and ON values. */
static int chg_ovr_set(struct dby_chg *chg, struct dby_chg_snapshot *s,
			 unsigned int target)
{
	struct dby_chg_icl *icl = &s->icl;
	int ret;

	BUILD_BUG_ON(DBY_CHG_OVR_REG != 0x1342 ||
		     DBY_CHG_OVR_DEFAULT != 0x00 || DBY_CHG_OVR_ON != 0x01);
	if (target != DBY_CHG_OVR_DEFAULT && target != DBY_CHG_OVR_ON)
		return -EINVAL;
	ret = chg_identity(chg);
	if (!ret && dev_get_regmap(chg->dev->parent, NULL) != chg->map)
		ret = -ENODEV;
	if (!ret)
		ret = regmap_read(chg->map, DBY_CHG_OVR_REG, &icl->ovr_reg);
	icl->ovr_read_error = ret;
	if (ret) {
		icl->errors++;
		icl->stopped = true;
		return ret;
	}
	if (icl->ovr_reg == target)
		return 0;
	icl->ovr_writes++;
	ret = regmap_write(chg->map, DBY_CHG_OVR_REG, target);
	if (!ret) {
		ret = regmap_read(chg->map, DBY_CHG_OVR_REG, &icl->ovr_reg);
		icl->ovr_read_error = ret;
		if (!ret && icl->ovr_reg != target)
			ret = -EIO;
	} else {
		icl->ovr_read_error = ret;
	}
	if (ret) {
		s->write_errors++;
		icl->errors++;
		icl->stopped = true;
	}
	return ret;
}
/* operation_lock held; 0x1366 accepts only its default and ON values. */
static int chg_opt_set(struct dby_chg *chg, struct dby_chg_snapshot *s,
			 unsigned int target)
{
	struct dby_chg_icl *icl = &s->icl;
	int ret;

	BUILD_BUG_ON(DBY_CHG_OPT_REG != 0x1366 ||
		     DBY_CHG_OPT_DEFAULT != 0x02 || DBY_CHG_OPT_ON != 0x01);
	if (target != DBY_CHG_OPT_DEFAULT && target != DBY_CHG_OPT_ON)
		return -EINVAL;
	ret = chg_identity(chg);
	if (!ret && dev_get_regmap(chg->dev->parent, NULL) != chg->map)
		ret = -ENODEV;
	if (!ret)
		ret = regmap_read(chg->map, DBY_CHG_OPT_REG, &icl->opt_reg);
	icl->opt_read_error = ret;
	if (ret) {
		icl->errors++;
		icl->stopped = true;
		return ret;
	}
	if (icl->opt_reg == target)
		return 0;
	icl->opt_writes++;
	ret = regmap_write(chg->map, DBY_CHG_OPT_REG, target);
	if (!ret) {
		ret = regmap_read(chg->map, DBY_CHG_OPT_REG, &icl->opt_reg);
		icl->opt_read_error = ret;
		if (!ret && icl->opt_reg != target)
			ret = -EIO;
	} else {
		icl->opt_read_error = ret;
	}
	if (ret) {
		s->write_errors++;
		icl->errors++;
		icl->stopped = true;
	}
	return ret;
}
static void chg_icl_restore(struct dby_chg *chg, struct dby_chg_snapshot *s)
{
	/* One ordered recovery sequence, including failed reads. Each setter
	 * is attempted once even if earlier steps fail; no later retries. */
	if (s->icl.recovery_attempted)
		return;
	s->icl.recovery_attempted = true;
	s->icl.allow_error = chg_allow_set(chg, s, DBY_CHG_ALLOW_DEFAULT);
	s->icl.ovr_error = chg_ovr_set(chg, s, DBY_CHG_OVR_DEFAULT);
	s->icl.opt_error = chg_opt_set(chg, s, DBY_CHG_OPT_DEFAULT);
	s->icl.error = chg_icl_set(chg, s, DBY_CHG_ICL_DEFAULT);
}
static void chg_icl_update(struct dby_chg *chg, struct dby_chg_snapshot *s)
{
	struct dby_chg_icl *icl = &s->icl;
	int online = dby_chg_online(&s->sample);
	unsigned int target, allow;
	int ret;

	chg_supplier_sample(chg, icl);
	icl->read_error = s->sample.identity_error ?: regmap_read(chg->map, DBY_CHG_ICL_REG, &icl->reg);
	icl->allow_read_error = s->sample.identity_error ?:
		regmap_read(chg->map, DBY_CHG_ALLOW_REG, &icl->allow_reg);
	icl->ovr_read_error = s->sample.identity_error ?:
		regmap_read(chg->map, DBY_CHG_OVR_REG, &icl->ovr_reg);
	icl->opt_read_error = s->sample.identity_error ?:
		regmap_read(chg->map, DBY_CHG_OPT_REG, &icl->opt_reg);
	if (icl->stopped)
		return;
	target = dby_chg_icl_target(online, icl->last_online,
		icl->source_online, icl->source, icl->voltage_uv, icl->current_ua,
		s->pd9v_max_ma, s->thermal.cap_ma, s->pd_low_batt);
	allow = dby_chg_allow_target(online, icl->source_online,
		icl->source, icl->voltage_uv, icl->current_ua);
	if (online == 1 && target) {
		/* Clear a previous 9 V allowance before raising a 5 V limit. */
		ret = 0;
		if (allow == DBY_CHG_ALLOW_DEFAULT)
			ret = icl->allow_error = chg_allow_set(chg, s, allow);
		if (!ret)
			ret = icl->error = chg_icl_set(chg, s, target);
		if (!ret)
			ret = icl->opt_error = chg_opt_set(chg, s, DBY_CHG_OPT_ON);
		if (!ret)
			ret = icl->ovr_error = chg_ovr_set(chg, s, DBY_CHG_OVR_ON);
		/* Standby 5 V/<=500 mA first verifies ICL 0A, then permits the
		 * ramp with 08. The settled 9 V contract selects 02 instead. */
		if (!ret && allow != DBY_CHG_ALLOW_DEFAULT)
			ret = icl->allow_error = chg_allow_set(chg, s, allow);
	} else {
		ret = icl->allow_error = chg_allow_set(chg, s, DBY_CHG_ALLOW_DEFAULT);
		if (!ret)
			ret = icl->ovr_error = chg_ovr_set(chg, s, DBY_CHG_OVR_DEFAULT);
		if (!ret)
			ret = icl->opt_error = chg_opt_set(chg, s, DBY_CHG_OPT_DEFAULT);
		if (!ret && online == 0 && target)
			ret = icl->error = chg_icl_set(chg, s, target);
	}
	/* Record a handled transition or a terminal management failure. */
	if (online >= 0 && (!ret || icl->stopped))
		icl->last_online = online;
	if (icl->stopped) {
		chg_icl_restore(chg, s);
		dev_warn(chg->dev, "ICL/allowance management stopped: error=%d recovery_allow_error=%d recovery_ovr_error=%d recovery_opt_error=%d recovery_icl_error=%d; charger control unchanged\n",
			 ret, icl->allow_error, icl->ovr_error, icl->opt_error, icl->error);
	}
}
/* Keep the fixed write operands visible as separate audited call sites. */
static noinline int chg_enable_off(struct dby_chg *chg)
{
	BUILD_BUG_ON(DBY_CHG_ENABLE_REG != 0x1042 || DBY_CHG_ENABLE_OFF != 0x00);
	return regmap_write(chg->map, DBY_CHG_ENABLE_REG, DBY_CHG_ENABLE_OFF);
}
static noinline int chg_enable_on(struct dby_chg *chg)
{
	BUILD_BUG_ON(DBY_CHG_ENABLE_REG != 0x1042 || DBY_CHG_ENABLE_ON != 0x01);
	return regmap_write(chg->map, DBY_CHG_ENABLE_REG, DBY_CHG_ENABLE_ON);
}
/* operation_lock held; action was authorized by plan or bounded remove gate.
 * Read failures before a write are transient; write/readback faults are terminal. */
static int chg_toggle(struct dby_chg *chg, struct dby_chg_snapshot *s,
                      enum dby_chg_action action, bool removing)
{
	unsigned int old = 0, readback = 0;
	int ret;

	ret = chg_identity(chg);
	if (ret || dev_get_regmap(chg->dev->parent, NULL) != chg->map) {
		s->control.want_enable = 0;
		s->reason |= CHG_IDENTITY;
		return -ENODEV;
	}
	ret = regmap_read(chg->map, DBY_CHG_ENABLE_REG, &old);
	ret = dby_chg_prewrite(&s->control, action, ret, old);
	if (ret) {
		s->reason |= ret == -EINVAL ? CHG_BAD_ENABLE : CHG_READ;
		return ret;
	}
	if (!removing)
		dby_chg_toggle_attempt(&s->control, chg_now(chg));
	ret = action == CHG_ON ? chg_enable_on(chg) : chg_enable_off(chg);
	if (!ret)
		ret = regmap_read(chg->map, DBY_CHG_ENABLE_REG, &readback);
	s->write_error = dby_chg_toggle_result(&s->control, action, ret, readback);
	if (s->write_error) {
		s->write_errors++;
		s->reason |= CHG_WRITE_ERROR;
		return s->write_error;
	}
	s->sample.reg[CHG_ENABLE] = readback;
	return 0;
}
static void chg_evaluate(struct dby_chg *chg, bool from_work)
{
	struct dby_chg_snapshot s, old;
	u64 now;
	bool event, summary;
	enum dby_chg_action action;
	unsigned int pet_reason;
	int low, high, toggle_error = 0;

	mutex_lock(&chg->operation_lock);
	if (chg->stopping) {
		mutex_unlock(&chg->operation_lock);
		return;
	}
	s = old = chg_snapshot(chg);
	chg_sample(chg, &s.sample);
	s.pd_low_batt = dby_chg_low_batt(s.pd_low_batt,
		s.sample.voltage_error, s.sample.voltage_uv);
	chg_thermal_sample(chg, &s);
	now = chg_now(chg);
	mutex_lock(&chg_limits_lock);
	low = temp_min_decic;
	high = temp_max_decic;
	mutex_unlock(&chg_limits_lock);
	if (dby_chg_policy(&s.control, &s.sample, &s.state, low, high))
		s.state.qualifying = 0;
	dby_chg_update(&s.state, &s.sample, now);
	s.feeding = false;
	s.write_error = 0;
	/* Plan at the latest clock; fresh identity/old-value reads precede toggles. */
	now = chg_now(chg);
	action = dby_chg_plan(&s.control, &s.sample, &s.state, low, high, now, &s.reason);
	if (action != CHG_KEEP)
		toggle_error = chg_toggle(chg, &s, action, false);
	now = chg_now(chg);
	pet_reason = dby_chg_decide(&s.sample, &s.state, now,
                             s.attempts, chg->last_attempt_ms);
	/* An early IRQ samples policy without inventing a feeding failure.
	 * A recent successful pet remains valid across a brief unplug/replug;
	 * every current health gate and the terminal fault latch still apply. */
	if (pet_reason == CHG_TOO_SOON && !toggle_error && !s.control.stopped &&
	    s.pets)
		s.feeding = true;
	s.reason |= pet_reason | s.control.stopped;
	if (!pet_reason && !toggle_error && !s.control.stopped) {
		BUILD_BUG_ON(DBY_CHG_PET_REG != 0x1643 || DBY_CHG_PET_VALUE != 0x01);
		chg->last_attempt_ms = now;
		s.attempts++;
		s.write_error = regmap_write(chg->map, DBY_CHG_PET_REG, DBY_CHG_PET_VALUE);
		if (s.write_error) {
			s.write_errors++;
			s.reason |= CHG_WRITE_ERROR;
			s.control.stopped = CHG_WRITE_ERROR;
			s.control.want_enable = 0;
		} else {
			s.pets++;
			s.feeding = true;
		}
	}
	/* Independent ICL errors must not change enable policy or pet gates. */
	chg_icl_update(chg, &s);
	s.sampled_ms = chg_now(chg);
	s.evaluated = true;
	dby_chg_pause_update(&s.pause, dby_chg_online(&s.sample), s.feeding, s.sampled_ms);
	chg_publish(chg, &s);
	chg_led_update(chg, &s.sample);
	event = dby_chg_event(old.evaluated, dby_chg_online(&old.sample),
		 dby_chg_online(&s.sample), old.feeding, s.feeding,
		 old.state.full, s.state.full, old.reason, s.reason) ||
		old.sample.reg[CHG_ENABLE] != s.sample.reg[CHG_ENABLE] ||
		old.icl.reg != s.icl.reg || old.icl.allow_reg != s.icl.allow_reg ||
		old.icl.ovr_reg != s.icl.ovr_reg || old.icl.opt_reg != s.icl.opt_reg ||
		old.icl.source != s.icl.source ||
		old.icl.voltage_uv != s.icl.voltage_uv || old.icl.current_ua != s.icl.current_ua ||
		old.icl.errors != s.icl.errors || old.pd9v_max_ma != s.pd9v_max_ma ||
		old.thermal.cap_ma != s.thermal.cap_ma || old.chg_temp_error != s.chg_temp_error ||
		old.pd_low_batt != s.pd_low_batt;
	summary = s.sampled_ms - chg->last_log_ms >= DBY_CHG_LOG_MS;
	if (event || summary || s.write_error)
		dev_info(chg->dev, "event=%u summary=%u elapsed_ms=%llu online=%d feeding=%u reason=%#x pets=%u write_errors=%u write_error=%d temp_decic=%d voltage_uv=%d current_ua=%d full=%u enable_reg=%02x want_enable=%u manage_stopped_reason=%#x icl_reg=%02x src=%s contract_mv=%d contract_ma=%d icl_writes=%u icl_stopped=%u icl_read_error=%d icl_error=%d allow_reg=%02x allow_writes=%u allow_read_error=%d allow_error=%d ovr_reg=%02x opt_reg=%02x ovr_writes=%u opt_writes=%u ovr_read_error=%d opt_read_error=%d ovr_error=%d opt_error=%d chg_temp_mc=%d chg_temp_error=%d pd9v_max_ma=%d thermal_cap_ma=%d thermal_drops=%u thermal_rises=%u pd_low_batt=%u\n",
			 event, summary, s.sampled_ms, dby_chg_online(&s.sample), s.feeding,
			 s.reason, s.pets, s.write_errors, s.write_error, s.sample.temp_decic,
			 s.sample.voltage_uv, s.sample.current_ua, s.state.full,
			s.sample.reg[CHG_ENABLE], s.control.want_enable, s.control.stopped,
			s.icl.reg, chg_source_name(s.icl.source), s.icl.voltage_uv / 1000,
			s.icl.current_ua / 1000, s.icl.writes, s.icl.stopped,
			s.icl.read_error, s.icl.error, s.icl.allow_reg, s.icl.allow_writes,
			s.icl.allow_read_error, s.icl.allow_error,
			s.icl.ovr_reg, s.icl.opt_reg, s.icl.ovr_writes, s.icl.opt_writes,
			s.icl.ovr_read_error, s.icl.opt_read_error, s.icl.ovr_error, s.icl.opt_error,
			s.chg_temp_mc, s.chg_temp_error, s.pd9v_max_ma, s.thermal.cap_ma,
			s.thermal.drops, s.thermal.rises, s.pd_low_batt);
	if (summary)
		chg->last_log_ms = s.sampled_ms;
	/* Suspend stops petting. After a watchdog bite, USB must be replugged
	 * (device observation 2026-11-05); petting alone cannot restart charging. */
	if (from_work && !chg->stopping) {
		unsigned int delay = DBY_CHG_INTERVAL_MS;
		u64 scheduled_at = chg_now(chg);

		if (chg->settle_pending)
			chg->settle_at_ms = scheduled_at + DBY_CHG_SETTLE_MS;
		chg->settle_pending = false;
		if (chg->settle_at_ms > scheduled_at)
			delay = chg->settle_at_ms - scheduled_at;
		else
			chg->settle_at_ms = 0;
		/* This sample covers IRQs received before taking operation_lock.
		 * Replace any queued immediate run with the single settle sample.
		 * Later IRQs set the flag again and restart the sequence. Bark
		 * neither consumes the flag nor changes the work deadline.
		 * Supplier events do not create a settle sample, but an existing
		 * IRQ-set deadline must survive their intervening evaluation. */
		mod_delayed_work(system_freezable_wq, &chg->work, msecs_to_jiffies(delay));
	}
	mutex_unlock(&chg->operation_lock);
	chg_notify(chg);
}
static void chg_work(struct work_struct *work)
{
	struct dby_chg *chg = container_of(to_delayed_work(work), struct dby_chg, work);

	chg_evaluate(chg, true);
}
static void chg_external_power_changed(struct power_supply *psy)
{
	struct dby_chg *chg = power_supply_get_drvdata(psy);

	mutex_lock(&chg->operation_lock);
	if (!chg->stopping)
		mod_delayed_work(system_freezable_wq, &chg->work, 0);
	mutex_unlock(&chg->operation_lock);
}
static irqreturn_t chg_changed_irq(int irq, void *data)
{
	struct dby_chg *chg = data;

	mutex_lock(&chg->operation_lock);
	if (!chg->stopping) {
		chg->settle_pending = true;
		mod_delayed_work(system_freezable_wq, &chg->work, 0);
	}
	mutex_unlock(&chg->operation_lock);
	return IRQ_HANDLED;
}
static irqreturn_t chg_bark_irq(int irq, void *data)
{
	struct dby_chg *chg = data;

	dev_warn_once(chg->dev, "wdog-bark: checking health before watchdog pet\n");
	/* Fresh reads, policy, pet spacing and fault latch share the worker path. */
	chg_evaluate(chg, false);
	return IRQ_HANDLED;
}
static int chg_request_irqs(struct platform_device *pdev, struct dby_chg *chg)
{
	static const char * const names[] = {
		"chg-state-change", "usbin-plugin", "wdog-bark",
	};
	int irqs[ARRAY_SIZE(names)], i, ret;

	/* Old DTBs have no resources. Do not leave a partial IRQ set enabled. */
	for (i = 0; i < ARRAY_SIZE(names); i++) {
		irqs[i] = platform_get_irq_byname(pdev, names[i]);
		if (irqs[i] == -EPROBE_DEFER)
			return -EPROBE_DEFER;
		if (irqs[i] < 0)
			goto polling;
	}
	for (i = 0; i < ARRAY_SIZE(names); i++) {
		ret = devm_request_threaded_irq(chg->dev, irqs[i], NULL,
			i == 2 ? chg_bark_irq : chg_changed_irq,
			IRQF_ONESHOT, names[i], chg);
		if (ret) {
			while (i-- > 0)
				devm_free_irq(chg->dev, irqs[i], chg);
			return dev_err_probe(chg->dev, ret, "cannot request charger IRQs\n");
		}
	}
	dev_info(chg->dev, "three charger IRQs active; 30-second periodic sampling\n");
	return 0;
polling:
	dev_info(chg->dev, "charger IRQs unavailable; using 30-second polling\n");
	return 0;
}
static enum power_supply_property chg_properties[] = {
	POWER_SUPPLY_PROP_ONLINE, POWER_SUPPLY_PROP_STATUS,
};
static int chg_get(struct power_supply *psy, enum power_supply_property prop,
		   union power_supply_propval *val)
{
	struct dby_chg *chg = power_supply_get_drvdata(psy);
	struct dby_chg_snapshot s = chg_snapshot(chg);
	u64 now = chg_now(chg);
	bool fresh = s.evaluated && now >= s.sampled_ms &&
		now - s.sampled_ms <= 2 * DBY_CHG_INTERVAL_MS;

	switch (prop) {
	case POWER_SUPPLY_PROP_ONLINE:
		if (!fresh || s.sample.error[CHG_USB])
			return -ENODATA;
		val->intval = !!(s.sample.reg[CHG_USB] & 1);
		return 0;
	case POWER_SUPPLY_PROP_STATUS:
		if (!fresh) {
			val->intval = POWER_SUPPLY_STATUS_NOT_CHARGING;
			return 0;
		}
		val->intval = dby_chg_status(&s.sample, &s.state,
			s.feeding && !READ_ONCE(chg->stopping));
		return 0;
	default:
		return -EINVAL;
	}
}
static const struct power_supply_desc chg_desc = {
	.name = "dby-chg", .type = POWER_SUPPLY_TYPE_USB,
	.properties = chg_properties, .num_properties = ARRAY_SIZE(chg_properties),
	.get_property = chg_get,
	.external_power_changed = chg_external_power_changed,
};
static int chg_status_show(struct seq_file *seq, void *unused)
{
	struct dby_chg *chg = seq->private;
	struct dby_chg_snapshot s = chg_snapshot(chg);
	u64 now = chg_now(chg);
	int i;

	seq_printf(seq, "evaluated=%u sampled_ms=%llu now_ms=%llu stopping=%u\n",
		s.evaluated, s.sampled_ms, now, READ_ONCE(chg->stopping));
	seq_printf(seq, "stopped_seconds_since_present=%llu replug_required_after_watchdog_bite=1\n",
		div_u64(dby_chg_pause_ms(&s.pause, now), 1000));
	seq_printf(seq, "reason_mask=%#x feeding=%u full=%u qualifying_samples=%u attempts=%u pets=%u write_errors=%u last_write_error=%d\n",
		s.reason, s.feeding && !READ_ONCE(chg->stopping), s.state.full, s.state.qualifying,
		s.attempts, s.pets, s.write_errors, s.write_error);
	seq_printf(seq, "icl_reg=%02x src=%s contract_mv=%d contract_ma=%d icl_writes=%u icl_management_errors=%u icl_stopped=%u icl_recovery_attempted=%u icl_read_error=%d icl_error=%d supplier_online=%d supplier_error=%d\n",
		s.icl.reg, chg_source_name(s.icl.source), s.icl.voltage_uv / 1000,
		s.icl.current_ua / 1000, s.icl.writes, s.icl.errors, s.icl.stopped,
		s.icl.recovery_attempted, s.icl.read_error, s.icl.error,
		 s.icl.source_online, s.icl.supplier_error);
	seq_printf(seq, "allow_reg=%02x allow_writes=%u allow_read_error=%d allow_error=%d\n",
		s.icl.allow_reg, s.icl.allow_writes, s.icl.allow_read_error, s.icl.allow_error);
	seq_printf(seq, "ovr_reg=%02x opt_reg=%02x ovr_writes=%u opt_writes=%u ovr_read_error=%d opt_read_error=%d ovr_error=%d opt_error=%d\n",
		s.icl.ovr_reg, s.icl.opt_reg, s.icl.ovr_writes, s.icl.opt_writes,
		s.icl.ovr_read_error, s.icl.opt_read_error, s.icl.ovr_error, s.icl.opt_error);
	seq_printf(seq, "chg_temp_mc=%d chg_temp_error=%d pd9v_max_ma=%d thermal_cap_ma=%d thermal_drops=%u thermal_rises=%u thermal_changed_ms=%llu pd_low_batt=%u\n",
		s.chg_temp_mc, s.chg_temp_error, s.pd9v_max_ma, s.thermal.cap_ma,
		s.thermal.drops, s.thermal.rises, s.thermal.changed_ms, s.pd_low_batt);
	dby_chg_prune(&s.control, now);
	seq_printf(seq, "enable_reg=%02x want_enable=%u toggles_last_hour=%u toggle_budget_remaining=%u last_toggle_ms=%llu manage_stopped_reason=%#x owned_off=%u\n",
		s.sample.reg[CHG_ENABLE], s.control.want_enable, s.control.count,
		DBY_CHG_TOGGLE_LIMIT - s.control.count,
		s.control.last_toggle_ms, s.control.stopped, s.control.owned_off);
	seq_printf(seq, "identity_error=%d therm_uv=%d temp_decic=%d temp_error=%d voltage_uv=%d voltage_error=%d current_ua=%d current_error=%d capacity=%d capacity_error=%d\n",
		s.sample.identity_error, s.sample.therm_uv, s.sample.temp_decic, s.sample.temp_error,
		s.sample.voltage_uv, s.sample.voltage_error, s.sample.current_ua,
		s.sample.current_error, s.sample.capacity, s.sample.capacity_error);
	for (i = 0; i < CHG_NREG; i++)
		seq_printf(seq, "%04x=%02x error=%d\n", sample_regs[i], s.sample.reg[i], s.sample.error[i]);
	return 0;
}
DEFINE_SHOW_ATTRIBUTE(chg_status);

static int chg_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct power_supply_config config = {};
	struct dby_chg *chg;
	unsigned int value = 0;
	u32 reg;
	int ret;

	BUILD_BUG_ON((int)CHG_UNKNOWN != (int)POWER_SUPPLY_STATUS_UNKNOWN);
	BUILD_BUG_ON((int)CHG_CHARGING != (int)POWER_SUPPLY_STATUS_CHARGING);
	BUILD_BUG_ON((int)CHG_DISCHARGING != (int)POWER_SUPPLY_STATUS_DISCHARGING);
	BUILD_BUG_ON((int)CHG_NOT_CHARGING != (int)POWER_SUPPLY_STATUS_NOT_CHARGING);
	BUILD_BUG_ON((int)CHG_STATUS_FULL != (int)POWER_SUPPLY_STATUS_FULL);
	if (of_property_count_u32_elems(dev->of_node, "reg") != 1 ||
	    of_property_read_u32(dev->of_node, "reg", &reg) || reg != 0x1000)
		return -EINVAL;
	chg = devm_kzalloc(dev, sizeof(*chg), GFP_KERNEL);
	if (!chg)
		return -ENOMEM;
	chg->dev = dev;
	ret = chg_identity(chg);
	if (ret)
		return dev_err_probe(dev, ret, "unsupported PMIC identity\n");
	chg->map = dev_get_regmap(dev->parent, NULL);
	if (!chg->map)
		return -EPROBE_DEFER;
	chg->therm = devm_iio_channel_get(dev, "batt_therm");
	if (IS_ERR(chg->therm))
		return dev_err_probe(dev, PTR_ERR(chg->therm), "no battery ADC channel\n");
	chg->chg_temp = devm_iio_channel_get(dev, "chg_temp");
	if (IS_ERR(chg->chg_temp))
		return dev_err_probe(dev, PTR_ERR(chg->chg_temp), "no charger temperature ADC channel\n");
	INIT_LIST_HEAD(&chg->parameter_node);
	ret = devm_add_action_or_reset(dev, chg_parameter_detach, chg);
	if (ret)
		return ret;
	mutex_init(&chg->operation_lock);
	spin_lock_init(&chg->status_lock);
	INIT_DELAYED_WORK(&chg->work, chg_work);
	/* IRQs may fire as soon as requested; no work until probe commits. */
	chg->stopping = true;
	chg->latest.icl.last_online = -1;
	/* Unknown initial voltage cannot authorize unrestricted 9 V current. */
	chg->latest.pd_low_batt = true;
	chg->latest.pd9v_max_ma = READ_ONCE(pd9v_max_ma);
	chg->latest.thermal.cap_ma = chg->latest.pd9v_max_ma;
	chg->latest.chg_temp_error = -ENODATA;
	chg->latest.icl.read_error = -ENODATA;
	chg->latest.icl.allow_read_error = -ENODATA;
	chg->latest.icl.ovr_read_error = -ENODATA;
	chg->latest.icl.opt_read_error = -ENODATA;
	chg->start_ms = div_u64(ktime_get_boottime_ns(), NSEC_PER_MSEC);
	config.drv_data = chg;
	config.fwnode = dev_fwnode(dev);
	chg->psy = devm_power_supply_register(dev, &chg_desc, &config);
	if (IS_ERR(chg->psy))
		return PTR_ERR(chg->psy);
	led_trigger_register_simple("dby-chg-red", &chg->red);
	led_trigger_register_simple("dby-chg-green", &chg->green);
	ret = devm_add_action_or_reset(dev, chg_led_unregister, chg);
	if (ret)
		return ret;
	if (!chg->red || !chg->green)
		return -ENOMEM;
	ret = chg_request_irqs(pdev, chg);
	if (ret)
		return ret;
	/* Reject unsupported enable states before the float write. */
	ret = regmap_read(chg->map, DBY_CHG_ENABLE_REG, &value);
	ret = dby_chg_enable_probe(&chg->latest.control, ret, value);
	if (ret)
		return ret;
	chg->latest.sample.reg[CHG_ENABLE] = value;
	/* All deferrable/fallible acquisitions precede the one-shot write. */
	value = 0;
	ret = regmap_read(chg->map, DBY_CHG_FLOAT_REG, &value);
	ret = dby_chg_float_action(ret, value);
	if (ret < 0)
		return ret;
	if (ret == 1) {
		BUILD_BUG_ON(DBY_CHG_FLOAT_REG != 0x1070 || DBY_CHG_FLOAT_VALUE != 0x50);
		ret = regmap_write(chg->map, DBY_CHG_FLOAT_REG, DBY_CHG_FLOAT_VALUE);
		if (ret)
			return -EIO;
		ret = regmap_read(chg->map, DBY_CHG_FLOAT_REG, &value);
		if (ret || value != DBY_CHG_FLOAT_VALUE)
			return -EIO;
	}
	platform_set_drvdata(pdev, chg);
	chg->debug_dir = debugfs_create_dir("dby-chg", NULL);
	debugfs_create_file("status", 0444, chg->debug_dir, chg, &chg_status_fops);
	mutex_lock(&chg_devices_lock);
	mutex_lock(&chg->operation_lock);
	spin_lock(&chg->status_lock);
	chg->latest.pd9v_max_ma = READ_ONCE(pd9v_max_ma);
	chg->latest.thermal.cap_ma = chg->latest.pd9v_max_ma;
	spin_unlock(&chg->status_lock);
	list_add_tail(&chg->parameter_node, &chg_devices);
	chg->stopping = false;
	queue_delayed_work(system_freezable_wq, &chg->work, msecs_to_jiffies(DBY_CHG_FIRST_MS));
	mutex_unlock(&chg->operation_lock);
	mutex_unlock(&chg_devices_lock);
	dev_info(dev, "charging manager active; temperature model provisional; replug USB after watchdog bite\n");
	return 0;
}
static void chg_stop(struct platform_device *pdev)
{
	struct dby_chg *chg = platform_get_drvdata(pdev);
	struct dby_chg_snapshot s;

	chg_parameter_detach(chg);
	mutex_lock(&chg->operation_lock);
	WRITE_ONCE(chg->stopping, true);
	chg_led_set(chg, 0, 0);
	mutex_unlock(&chg->operation_lock);
	cancel_delayed_work_sync(&chg->work);
	mutex_lock(&chg->operation_lock);
	s = chg_snapshot(chg);
	chg_icl_restore(chg, &s);
	dev_info(chg->dev, "exit ICL/allowance restore: icl_reg=%02x icl_writes=%u icl_stopped=%u icl_error=%d allow_reg=%02x allow_writes=%u allow_error=%d ovr_reg=%02x opt_reg=%02x ovr_writes=%u opt_writes=%u ovr_error=%d opt_error=%d write_errors=%u\n",
		 s.icl.reg, s.icl.writes, s.icl.stopped, s.icl.error,
		 s.icl.allow_reg, s.icl.allow_writes, s.icl.allow_error,
		 s.icl.ovr_reg, s.icl.opt_reg, s.icl.ovr_writes, s.icl.opt_writes,
		 s.icl.ovr_error, s.icl.opt_error, s.write_errors);
	s.feeding = false;
	s.reason |= CHG_STOPPING;
	dby_chg_pause_update(&s.pause, dby_chg_online(&s.sample), false, chg_now(chg));
	chg_publish(chg, &s);
	mutex_unlock(&chg->operation_lock);
	chg_notify(chg);
}
static void chg_remove(struct platform_device *pdev)
{
	struct dby_chg *chg = platform_get_drvdata(pdev);

	chg_stop(pdev);
	mutex_lock(&chg->operation_lock);
	{
		struct dby_chg_snapshot s = chg_snapshot(chg);

		if (dby_chg_remove_allowed(&s.control,
		    s.evaluated && dby_chg_online(&s.sample) == 1, false)) {
			int ret;

			s.control.remove_attempted = 1;
			ret = chg_toggle(chg, &s, CHG_ON, true);
			dev_info(chg->dev, "remove restore attempted once (runtime budget exception), result=%d\n", ret);
			chg_publish(chg, &s);
		} else {
			dev_info(chg->dev, "remove: no restore; want_enable=%u owned_off=%u stopped=%#x; enable unchanged\n",
				s.control.want_enable, s.control.owned_off, s.control.stopped);
		}
	}
	mutex_unlock(&chg->operation_lock);
	debugfs_remove_recursive(chg->debug_dir);
}
static const struct of_device_id chg_match[] = {
	{ .compatible = "dby,pm8150b-charger" }, { }
};
MODULE_DEVICE_TABLE(of, chg_match);
static struct platform_driver chg_driver = {
	.probe = chg_probe, .remove = chg_remove, .shutdown = chg_stop,
	.driver = { .name = "dby-chg", .of_match_table = chg_match },
};
module_platform_driver(chg_driver);
MODULE_DESCRIPTION("DBY-W09 guarded PM8150B charging manager");
MODULE_AUTHOR("DBY-W09 contributors");
MODULE_LICENSE("GPL");
