// SPDX-License-Identifier: GPL-2.0-only
/* PM8150B guarded watchdog management; hardware watchdog remains enabled. */
#include <linux/debugfs.h>
#include <linux/iio/consumer.h>
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

struct dby_chg_snapshot {
	struct dby_chg_sample sample;
	struct dby_chg_state state;
	struct dby_chg_control control;
	struct dby_chg_pause pause;
	u64 sampled_ms;
	unsigned int reason, attempts, pets, write_errors;
	int write_error;
	bool evaluated, feeding;
};
struct dby_chg {
	struct device *dev;
	struct regmap *map;
	struct iio_channel *therm;
	struct power_supply *psy;
	struct delayed_work work;
	struct mutex operation_lock;
	spinlock_t status_lock;
	bool stopping;
	u64 start_ms, last_attempt_ms, last_log_ms;
	struct dby_chg_snapshot latest;
	struct dentry *debug_dir;
};
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
static void chg_work(struct work_struct *work)
{
	struct dby_chg *chg = container_of(to_delayed_work(work), struct dby_chg, work);
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
	s.sampled_ms = chg_now(chg);
	s.evaluated = true;
	dby_chg_pause_update(&s.pause, dby_chg_online(&s.sample), s.feeding, s.sampled_ms);
	chg_publish(chg, &s);
	event = dby_chg_event(old.evaluated, dby_chg_online(&old.sample),
		 dby_chg_online(&s.sample), old.feeding, s.feeding,
		 old.state.full, s.state.full, old.reason, s.reason) ||
		old.sample.reg[CHG_ENABLE] != s.sample.reg[CHG_ENABLE];
	summary = s.sampled_ms - chg->last_log_ms >= DBY_CHG_LOG_MS;
	if (event || summary || s.write_error)
		dev_info(chg->dev, "event=%u summary=%u elapsed_ms=%llu online=%d feeding=%u reason=%#x pets=%u write_errors=%u write_error=%d temp_decic=%d voltage_uv=%d current_ua=%d full=%u enable_reg=%02x want_enable=%u manage_stopped_reason=%#x\n",
			 event, summary, s.sampled_ms, dby_chg_online(&s.sample), s.feeding,
			 s.reason, s.pets, s.write_errors, s.write_error, s.sample.temp_decic,
			 s.sample.voltage_uv, s.sample.current_ua, s.state.full,
			s.sample.reg[CHG_ENABLE], s.control.want_enable, s.control.stopped);
	if (summary)
		chg->last_log_ms = s.sampled_ms;
	/* Suspend stops petting. After a watchdog bite, USB must be replugged
	 * (device observation 2026-11-05); petting alone cannot restart charging. */
	if (!chg->stopping)
		queue_delayed_work(system_freezable_wq, &chg->work, msecs_to_jiffies(DBY_CHG_INTERVAL_MS));
	mutex_unlock(&chg->operation_lock);
	chg_notify(chg);
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
	mutex_init(&chg->operation_lock);
	spin_lock_init(&chg->status_lock);
	INIT_DELAYED_WORK(&chg->work, chg_work);
	chg->start_ms = div_u64(ktime_get_boottime_ns(), NSEC_PER_MSEC);
	config.drv_data = chg;
	config.fwnode = dev_fwnode(dev);
	chg->psy = devm_power_supply_register(dev, &chg_desc, &config);
	if (IS_ERR(chg->psy))
		return PTR_ERR(chg->psy);
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
	queue_delayed_work(system_freezable_wq, &chg->work, msecs_to_jiffies(DBY_CHG_FIRST_MS));
	dev_info(dev, "charging manager active; temperature model provisional; replug USB after watchdog bite\n");
	return 0;
}
static void chg_stop(struct platform_device *pdev)
{
	struct dby_chg *chg = platform_get_drvdata(pdev);
	struct dby_chg_snapshot s;

	mutex_lock(&chg->operation_lock);
	WRITE_ONCE(chg->stopping, true);
	mutex_unlock(&chg->operation_lock);
	cancel_delayed_work_sync(&chg->work);
	s = chg_snapshot(chg);
	s.feeding = false;
	s.reason |= CHG_STOPPING;
	dby_chg_pause_update(&s.pause, dby_chg_online(&s.sample), false, chg_now(chg));
	chg_publish(chg, &s);
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
