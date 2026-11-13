// SPDX-License-Identifier: GPL-2.0-only
/* Board-specific, explicitly armed TFA9874 bring-up driver. */
#include <linux/delay.h>
#include <linux/gpio/consumer.h>
#include <linux/i2c.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/regmap.h>
#include <sound/pcm_params.h>
#include <sound/soc.h>
#include "dby-tfa9874-core.h"

struct dby_tfa9874_priv {
 struct device *dev;
 struct regmap *map;
 unsigned int pa_index, channels;
 bool unmuted;
};
static DEFINE_MUTEX(chips_lock);
static struct dby_tfa9874_priv *g_chips[4];
static bool reset_done;
static bool safe_enable;
static unsigned int attenuation_step = 15;
static bool input_atten_6db = true;
static unsigned int active_mask = 15;

static int execute(struct dby_tfa9874_priv *p,
 const struct dby_tfa9874_op *ops, unsigned int count, bool best_effort)
{
 unsigned int i, value;
 int ret, first = 0;
 for (i = 0; i < count; i++) {
  const struct dby_tfa9874_op *op = &ops[i];
  ret = 0;
  switch (op->type) {
  case DBY_TFA_OP_REG_WRITE:
   if (op->mask == 0xffff)
    ret = regmap_write(p->map, op->reg, op->val);
   else
    ret = regmap_update_bits(p->map, op->reg, op->mask, op->val);
   break;
  case DBY_TFA_OP_REG_RXORW:
   ret = regmap_read(p->map, op->val, &value);
   if (!ret) ret = regmap_write(p->map, op->reg, value ^ op->mask);
   break;
  case DBY_TFA_OP_REG_READ:
   ret = regmap_read(p->map, op->reg, &value);
   break;
  case DBY_TFA_OP_DELAY_MS:
   break;
  default:
   ret = -EINVAL;
  }
  if (ret && !first) first = ret;
  if (ret && !best_effort) break;
  if (op->delay_ms) msleep(op->delay_ms);
 }
 return first;
}
static int power_off(struct dby_tfa9874_priv *p)
{
 return execute(p, dby_tfa_off, ARRAY_SIZE(dby_tfa_off), true);
}
/* Called with chips_lock: an I2C error revokes the global arm and attempts
 * every shutdown write on every present chip, including after earlier errors.
 */
static void fail_closed(void)
{
 unsigned int i;
 safe_enable = false;
 for (i = 0; i < ARRAY_SIZE(g_chips); i++)
  if (g_chips[i]) power_off(g_chips[i]);
}
static int apply_state(struct dby_tfa9874_priv *p)
{
 struct dby_tfa9874_op ops[DBY_TFA_ON_COUNT];
 unsigned int ctrl, status;
 int n, ret;
 if (!p->unmuted) return power_off(p);
 n = dby_tfa9874_build_power_on_seq(p->pa_index, p->channels,
  attenuation_step, input_atten_6db, safe_enable, active_mask,
  ops, ARRAY_SIZE(ops));
 if (n < 0) return n;
 ret = execute(p, ops, n, false);
 if (ret) return ret;
 if (!safe_enable || !(active_mask & BIT(p->pa_index))) {
  dev_info_ratelimited(p->dev, "speaker locked off\n");
  return 0;
 }
 ret = regmap_read(p->map, 0, &ctrl);
 if (ret) return ret;
 ret = regmap_read(p->map, 0x10, &status);
 if (!ret) dev_dbg(p->dev, "control=%04x status=%04x\n", ctrl, status);
 return ret;
}
static int control_info(struct snd_kcontrol *k, struct snd_ctl_elem_info *u)
{
 u->type = k->private_value == 0 || k->private_value == 2 ?
  SNDRV_CTL_ELEM_TYPE_BOOLEAN : SNDRV_CTL_ELEM_TYPE_INTEGER;
 u->count = 1;
 u->value.integer.min = k->private_value == 1 ? 9 : 0;
 u->value.integer.max = k->private_value == 1 || k->private_value == 3 ? 15 : 1;
 return 0;
}
static int control_get(struct snd_kcontrol *k, struct snd_ctl_elem_value *u)
{
 mutex_lock(&chips_lock);
 switch (k->private_value) {
 case 0: u->value.integer.value[0] = safe_enable; break;
 case 1: u->value.integer.value[0] = attenuation_step; break;
 case 2: u->value.integer.value[0] = input_atten_6db; break;
 case 3: u->value.integer.value[0] = active_mask; break;
 }
 mutex_unlock(&chips_lock);
 return 0;
}
static int control_put(struct snd_kcontrol *k, struct snd_ctl_elem_value *u)
{
 long val = u->value.integer.value[0];
 unsigned int i, old;
 int ret = 0;
 if (val < (k->private_value == 1 ? 9 : 0) ||
     val > (k->private_value == 1 || k->private_value == 3 ? 15 : 1))
  return -EINVAL;
 mutex_lock(&chips_lock);
 switch (k->private_value) {
 case 0: old = safe_enable; safe_enable = val; break;
 case 1: old = attenuation_step; attenuation_step = val; break;
 case 2: old = input_atten_6db; input_atten_6db = val; break;
 default: old = active_mask; active_mask = val; break;
 }
 /* Even repeated safe=0 performs hardware shutdown. */
 for (i = 0; i < ARRAY_SIZE(g_chips); i++) {
  if (!g_chips[i]) continue;
  ret = apply_state(g_chips[i]);
  if (ret) { fail_closed(); break; }
 }
 mutex_unlock(&chips_lock);
 return ret ? ret : old != val;
}
#define DBY_CONTROL(n, id) { .iface = SNDRV_CTL_ELEM_IFACE_MIXER, .name = n, \
 .info = control_info, .get = control_get, .put = control_put, .private_value = id }
static const struct snd_kcontrol_new controls[] = {
 DBY_CONTROL("DBY Speaker Safe Enable Switch", 0),
 DBY_CONTROL("DBY Speaker Attenuation Step", 1),
 DBY_CONTROL("DBY Speaker Input Atten 6dB Switch", 2),
 DBY_CONTROL("DBY Speaker Active Mask", 3),
};
static int component_probe(struct snd_soc_component *component)
{
 struct dby_tfa9874_priv *p = dev_get_drvdata(component->dev);
 return p->pa_index ? 0 : snd_soc_add_card_controls(component->card,
  controls, ARRAY_SIZE(controls));
}
static int hw_params(struct snd_pcm_substream *s, struct snd_pcm_hw_params *params,
 struct snd_soc_dai *dai)
{
 struct dby_tfa9874_priv *p = dev_get_drvdata(dai->dev);
 unsigned int ch = params_channels(params);
 if (ch < 1 || ch > 4) return -EINVAL;
 mutex_lock(&chips_lock);
 p->channels = ch;
 mutex_unlock(&chips_lock);
 return 0;
}
static int mute_stream(struct snd_soc_dai *dai, int mute, int stream)
{
 struct dby_tfa9874_priv *p = dev_get_drvdata(dai->dev);
 int ret;
 mutex_lock(&chips_lock);
 p->unmuted = !mute;
 ret = apply_state(p);
 if (ret) fail_closed();
 mutex_unlock(&chips_lock);
 return ret;
}
static const struct snd_soc_dai_ops dai_ops = {
 .hw_params = hw_params, .mute_stream = mute_stream,
 .no_capture_mute = 1,
};
static struct snd_soc_dai_driver dai_driver = {
 .name = "dby-tfa9874-hifi",
 .playback = { .stream_name = "Playback", .channels_min = 1, .channels_max = 4,
  .rates = SNDRV_PCM_RATE_48000,
  .formats = SNDRV_PCM_FMTBIT_S16_LE | SNDRV_PCM_FMTBIT_S24_LE | SNDRV_PCM_FMTBIT_S32_LE },
 .ops = &dai_ops,
};
static const struct snd_soc_dapm_widget widgets[] = {
 SND_SOC_DAPM_AIF_IN("AIFIN", "Playback", 0, SND_SOC_NOPM, 0, 0),
 SND_SOC_DAPM_OUTPUT("OUT"),
};
static const struct snd_soc_dapm_route routes[] = {{"OUT", NULL, "AIFIN"}};
static const struct snd_soc_component_driver component_driver = {
 .probe = component_probe,
 .dapm_widgets = widgets, .num_dapm_widgets = ARRAY_SIZE(widgets),
 .dapm_routes = routes, .num_dapm_routes = ARRAY_SIZE(routes),
};
static const struct regmap_config regmap_config = {
 .reg_bits = 8, .val_bits = 16, .max_register = 0xff,
 .val_format_endian = REGMAP_ENDIAN_BIG,
};
/* Installed before component registration: devres unregisters all ASoC
 * callbacks before dropping the table entry and releasing regmap/GPIO memory.
 */
static void clear_chip(void *data)
{
 struct dby_tfa9874_priv *p = data;
 mutex_lock(&chips_lock);
 if (g_chips[p->pa_index] == p) {
  fail_closed();
  g_chips[p->pa_index] = NULL;
 }
 mutex_unlock(&chips_lock);
}
static int probe(struct i2c_client *client)
{
 struct device *dev = &client->dev;
 struct dby_tfa9874_priv *p;
 struct gpio_desc *rst;
 struct dby_tfa9874_op ops[DBY_TFA_INIT_COUNT];
 unsigned int index, rev;
 int ret;
 ret = device_property_read_u32(dev, "huawei,pa-index", &index);
 if (ret || index > 3) return -EINVAL;
 p = devm_kzalloc(dev, sizeof(*p), GFP_KERNEL);
 if (!p) return -ENOMEM;
 p->dev = dev; p->pa_index = index; p->channels = 2;
 p->map = devm_regmap_init_i2c(client, &regmap_config);
 if (IS_ERR(p->map)) return PTR_ERR(p->map);
 i2c_set_clientdata(client, p);
 mutex_lock(&chips_lock);
 if (g_chips[index]) { ret = -EBUSY; goto unlock; }
 /* Non-owner chips must not initialize before the one shared reset. */
 if (index && !reset_done) { ret = -EPROBE_DEFER; goto unlock; }
 if (!index) {
  rst = devm_gpiod_get_optional(dev, "reset", GPIOD_OUT_LOW);
  if (IS_ERR(rst)) { ret = PTR_ERR(rst); goto unlock; }
  if (!rst) { ret = -EINVAL; goto unlock; }
  if (!reset_done) {
   gpiod_set_value_cansleep(rst, 1); usleep_range(1500, 2500);
   gpiod_set_value_cansleep(rst, 0); usleep_range(1500, 2500);
   reset_done = true;
  }
 }
 ret = regmap_read(p->map, DBY_TFA9874_REG_REV, &rev);
 if (ret) goto unidentified;
 if (rev != DBY_TFA9874_REV_ID) {
  dev_err(dev, "unsupported chip revision\n"); ret = -ENODEV; goto unidentified;
 }
 ret = dby_tfa9874_build_init(index, ops, ARRAY_SIZE(ops));
 if (ret >= 0) ret = execute(p, ops, ret, false);
 if (ret) goto failed;
 g_chips[index] = p;
 mutex_unlock(&chips_lock);
 ret = devm_add_action_or_reset(dev, clear_chip, p);
 if (ret) return ret;
 ret = devm_snd_soc_register_component(dev, &component_driver, &dai_driver, 1);
 if (!ret) return 0;
 mutex_lock(&chips_lock);
 g_chips[index] = NULL;
failed:
 power_off(p);
unidentified:
 fail_closed();
unlock:
 mutex_unlock(&chips_lock);
 return ret;
}
static void remove_chip(struct i2c_client *client)
{
 mutex_lock(&chips_lock);
 fail_closed();
 mutex_unlock(&chips_lock);
}
static void shutdown(struct i2c_client *client)
{
 mutex_lock(&chips_lock);
 fail_closed();
 mutex_unlock(&chips_lock);
}
static const struct of_device_id match[] = {
 { .compatible = "huawei,dby-tfa9874" }, {}
};
MODULE_DEVICE_TABLE(of, match);
static struct i2c_driver dby_tfa_driver = {
 .driver = { .name = "dby-tfa9874", .of_match_table = match },
 .probe = probe, .remove = remove_chip, .shutdown = shutdown,
};
module_i2c_driver(dby_tfa_driver);
MODULE_DESCRIPTION("DBY-W09 explicitly armed TFA9874 speaker bring-up");
MODULE_AUTHOR("DBY-W09 contributors");
MODULE_LICENSE("GPL");
