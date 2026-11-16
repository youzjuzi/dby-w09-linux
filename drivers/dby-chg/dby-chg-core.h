/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef DBY_CHG_CORE_H
#define DBY_CHG_CORE_H
#ifdef __KERNEL__
#include <linux/errno.h>
#include <linux/math64.h>
#include <linux/types.h>
typedef s64 dby_therm_s64;
static inline dby_therm_s64 dby_therm_div(dby_therm_s64 a, dby_therm_s64 b)
{ return div64_s64(a, b); }
#else
#include <errno.h>
#include <stdint.h>
typedef int64_t dby_therm_s64;
static inline dby_therm_s64 dby_therm_div(dby_therm_s64 a, dby_therm_s64 b)
{ return a / b; }
#endif
/* Candidate Sunwoda profile interpretation; NOT a validated safety model. */
#define DBY_THERM_RPU_OHM 30000
#define DBY_THERM_R0_OHM 10000
#define DBY_THERM_BETA_K 3435
#define DBY_THERM_T0_CENTIK 29815
#define DBY_THERM_Q24 16777216LL
#define DBY_THERM_Q30 1073741824LL

/* ln(x) Q24: range-reduce to [1,2), then 2*(z+z^3/3+...+z^17/17).
 * |z| <= 1/3; no floating point, tables, allocation, state or I/O. */
static inline dby_therm_s64 dby_therm_ln(dby_therm_s64 x)
{
	dby_therm_s64 z, z2, term, sum;
	int exponent = 0, n;

	while (x < DBY_THERM_Q24) { x *= 2; exponent--; }
	while (x >= 2 * DBY_THERM_Q24) { x /= 2; exponent++; }
	z = dby_therm_div((x - DBY_THERM_Q24) * DBY_THERM_Q24,
			 x + DBY_THERM_Q24);
	z2 = dby_therm_div(z * z, DBY_THERM_Q24);
	term = sum = z;
	for (n = 3; n <= 17; n += 2) {
		term = dby_therm_div(term * z2, DBY_THERM_Q24);
		sum += dby_therm_div(term, n);
	}
	return 2 * sum + (dby_therm_s64)exponent * 11629080; /* ln(2) Q24 */
}

/* Input/output: microvolts, microvolts, tenths Celsius.
 * Numeric model domain -100..150 C; this is NOT a safe charging interval.
 * Exact rails, invalid reference, implausible model output => error;
 * output is untouched on error. Near-rail faults still need device validation. */
static inline int dby_batt_therm_decic(int uv, int ref_uv, int *decic)
{
	dby_therm_s64 ratio, ln_ratio, inverse, centic;

	if (!decic || ref_uv <= 0 || ref_uv > 10000000)
		return -EINVAL;
	if (uv <= 0 || uv >= ref_uv)
		return -ERANGE;
	ratio = dby_therm_div((dby_therm_s64)DBY_THERM_RPU_OHM * uv * DBY_THERM_Q24,
			     (dby_therm_s64)DBY_THERM_R0_OHM * (ref_uv - uv));
	if (ratio <= 0)
		return -ERANGE;
	ln_ratio = dby_therm_ln(ratio);
	inverse = dby_therm_div(DBY_THERM_Q30 * 100, DBY_THERM_T0_CENTIK)
		+ dby_therm_div(ln_ratio * 64, DBY_THERM_BETA_K);
	if (inverse <= 0)
		return -ERANGE;
	centic = dby_therm_div(DBY_THERM_Q30 * 100 + inverse / 2, inverse) - 27315;
	if (centic < -10000 || centic > 15000)
		return -ERANGE;
	*decic = (int)dby_therm_div(centic + (centic >= 0 ? 5 : -5), 10);
	return 0;
}
/* All policy thresholds are provisional (U), not battery specifications. */
#define DBY_CHG_FLOAT_REG 0x1070u
#define DBY_CHG_FLOAT_VALUE 0x50u
#define DBY_CHG_PET_REG 0x1643u
#define DBY_CHG_PET_VALUE 0x01u
#define DBY_CHG_ENABLE_REG 0x1042u
#define DBY_CHG_ENABLE_OFF 0x00u
#define DBY_CHG_ENABLE_ON 0x01u
#define DBY_CHG_TOGGLE_MS 30000u
#define DBY_CHG_HOUR_MS 3600000u
#define DBY_CHG_TOGGLE_LIMIT 12u
#define DBY_CHG_TEMP_HYST 10
#define DBY_CHG_VOLT_REENABLE 4400000
#define DBY_CHG_FIRST_MS 2000u
#define DBY_CHG_LOG_MS 600000u
#define DBY_CHG_INTERVAL_MS 30000u
#define DBY_CHG_SETTLE_MS 2000u
#define DBY_CHG_FULL_MS 180000u
#define DBY_CHG_TEMP_MIN 120
#define DBY_CHG_TEMP_MAX 420
#define DBY_CHG_VOLT_MAX 4430000
#define DBY_CHG_FULL_UV 4380000
#define DBY_CHG_RECHARGE_UV 4250000
#define DBY_CHG_TERM_UA 200000
#define DBY_CHG_REF_UV 1875000

#define DBY_CHG_ICL_REG 0x1370u
#define DBY_CHG_ICL_DEFAULT 0x1Eu
#define DBY_CHG_ICL_MIN 0x0Au
#define DBY_CHG_ICL_MAX 0x3Cu
#define DBY_CHG_ICL_5V_MAX 0x28u
#define DBY_CHG_ICL_MAX_UA 2000000
#define DBY_CHG_PD9V_DEFAULT_MA 1500
#define DBY_CHG_PD9V_MAX_MA 3000
#define DBY_CHG_THERMAL_STEP_MA 250
#define DBY_CHG_THERMAL_RISE_MS 60000u
#define DBY_CHG_ICL_STEP_UA 50000
#define DBY_CHG_ALLOW_REG 0x1344u
#define DBY_CHG_ALLOW_DEFAULT 0x00u
#define DBY_CHG_ALLOW_9V 0x02u
#define DBY_CHG_ALLOW_TRANSITION 0x08u
#define DBY_CHG_LOW_BATT_UV 3500000
#define DBY_CHG_LOW_BATT_RELEASE_UV 3600000
#define DBY_CHG_LOW_BATT_MAX_UA 1000000
#define DBY_CHG_OVR_REG 0x1342u
#define DBY_CHG_OVR_DEFAULT 0x00u
#define DBY_CHG_OVR_ON 0x01u
#define DBY_CHG_OPT_REG 0x1366u
#define DBY_CHG_OPT_DEFAULT 0x02u
#define DBY_CHG_OPT_ON 0x01u
enum dby_chg_source { CHG_SRC_UNKNOWN, CHG_SRC_C, CHG_SRC_PD };
static inline int dby_chg_pd9v_valid(int ma)
{
 return ma == 1500 || ma == 2000 || ma == 2250 || ma == 2500 ||
  ma == 2750 || ma == 3000;
}
struct dby_chg_thermal {
 int cap_ma;
 unsigned int drops, rises;
 unsigned long long changed_ms;
};
/* Offline resets the ceiling. Parameter raises never erase a thermal cap.
 * Invalid temperature samples fail safe; only cool samples permit a rise. */
static inline int dby_chg_thermal_update(struct dby_chg_thermal *t,
 int max_ma, int online, int batt_error, int batt_decic,
 int chip_error, int chip_mc, unsigned long long now)
{
 int old, direction = 0;

 if (online == 0) {
  t->cap_ma = max_ma;
  t->changed_ms = now;
  return 0;
 }
 if (t->cap_ma > max_ma) {
  t->cap_ma = max_ma;
  t->changed_ms = now;
 }
 old = t->cap_ma;
 if (batt_error || chip_error) {
  t->cap_ma = DBY_CHG_PD9V_DEFAULT_MA;
 } else if (batt_decic >= 380 || chip_mc >= 80000) {
  t->cap_ma -= DBY_CHG_THERMAL_STEP_MA;
  if (t->cap_ma < DBY_CHG_PD9V_DEFAULT_MA)
   t->cap_ma = DBY_CHG_PD9V_DEFAULT_MA;
 } else if (online == 1 && batt_decic <= 360 && chip_mc <= 70000 &&
            now - t->changed_ms >= DBY_CHG_THERMAL_RISE_MS) {
  t->cap_ma += DBY_CHG_THERMAL_STEP_MA;
  if (t->cap_ma > max_ma) t->cap_ma = max_ma;
 }
 if (t->cap_ma != old) {
  t->changed_ms = now;
  if (t->cap_ma < old) { t->drops++; direction = -1; }
  else { t->rises++; direction = 1; }
 }
 return direction;
}
static inline int dby_chg_low_batt(int previous, int error, int voltage_uv)
{
 if (error || voltage_uv < DBY_CHG_LOW_BATT_UV) return 1;
 if (voltage_uv >= DBY_CHG_LOW_BATT_RELEASE_UV) return 0;
 return previous;
}
static inline unsigned int dby_chg_allow_target(int online, int source_online,
 enum dby_chg_source source, int voltage_uv, int current_ua)
{
 if (online != 1 || source_online != 1 || source != CHG_SRC_PD)
  return DBY_CHG_ALLOW_DEFAULT;
 if (voltage_uv == 5000000 && current_ua > 0 && current_ua <= 500000)
  return DBY_CHG_ALLOW_TRANSITION;
 return voltage_uv >= 8500000 ? DBY_CHG_ALLOW_9V : DBY_CHG_ALLOW_DEFAULT;
}
/* Zero means no target/write; only confirmed unplug transitions restore.
 * Contract/Rp values are micro-units from the standard supplier interface. */
static inline unsigned int dby_chg_icl_target(int online, int was_online,
 int source_online, enum dby_chg_source source, int voltage_uv, int current_ua,
 int pd9v_max_ma, int thermal_cap_ma, int low_batt)
{
 unsigned int target;
 int limit;

 if (online == 0) return was_online == 1 ? DBY_CHG_ICL_DEFAULT : 0;
 if (online != 1 || source_online != 1 || voltage_uv <= 0 || current_ua <= 0)
  return 0;
 if (source == CHG_SRC_C) {
  /* Default Rp / PC ports keep firmware automatic current selection. */
  if (current_ua < 1500000) return 0;
  return current_ua >= 3000000 ? DBY_CHG_ICL_5V_MAX : DBY_CHG_ICL_DEFAULT;
 }
 if (source != CHG_SRC_PD) return 0;
 if (voltage_uv < 8500000 && voltage_uv != 5000000) return 0;
 limit = DBY_CHG_ICL_MAX_UA;
 if (voltage_uv >= 8500000) {
  if (!dby_chg_pd9v_valid(pd9v_max_ma) ||
      thermal_cap_ma < DBY_CHG_PD9V_DEFAULT_MA)
   return 0;
  limit = pd9v_max_ma;
  if (thermal_cap_ma < limit) limit = thermal_cap_ma;
  limit *= 1000;
  if (low_batt && limit > DBY_CHG_LOW_BATT_MAX_UA)
   limit = DBY_CHG_LOW_BATT_MAX_UA;
 }
 if (current_ua < limit) limit = current_ua;
 target = limit / DBY_CHG_ICL_STEP_UA;
 if (target < DBY_CHG_ICL_MIN) target = DBY_CHG_ICL_MIN;
 if (target > DBY_CHG_ICL_MAX) target = DBY_CHG_ICL_MAX;
 return target;
}

enum dby_chg_reg { CHG_STATE, CHG_FLAGS, CHG_FLOAT, CHG_HW_TEMP, CHG_USB, CHG_ENABLE, CHG_NREG };
struct dby_chg_sample {
 unsigned int reg[CHG_NREG];
 int error[CHG_NREG];
 int identity_error, temp_error, voltage_error, current_error, capacity_error;
 int temp_decic, therm_uv, voltage_uv, current_ua, capacity;
};
struct dby_chg_state {
 unsigned int full, offline_seen, qualifying;
 unsigned long long first_ms, previous_ms;
};
enum dby_chg_reason {
 CHG_IDENTITY = 1u << 0, CHG_READ = 1u << 1, CHG_BAD_FLOAT = 1u << 2,
 CHG_ABSENT = 1u << 3, CHG_BAD_HW_TEMP = 1u << 4, CHG_BAD_TEMP = 1u << 5,
 CHG_BAD_VOLTAGE = 1u << 6, CHG_FULL = 1u << 7,
 CHG_TOO_SOON = 1u << 9,
 CHG_WRITE_ERROR = 1u << 11, CHG_STOPPING = 1u << 12, CHG_BAD_ENABLE = 1u << 13,
 CHG_BUDGET = 1u << 14, CHG_UNOWNED = 1u << 15, CHG_TOGGLE_WAIT = 1u << 16
};
/* Explicit values match enum power_supply_status; checked by kernel adapter. */
enum dby_chg_status { CHG_UNKNOWN, CHG_CHARGING, CHG_DISCHARGING,
 CHG_NOT_CHARGING, CHG_STATUS_FULL };
static inline int dby_chg_identity(unsigned int type, unsigned int subtype,
 unsigned int major, unsigned int minor, unsigned int rev2)
{ return type == 0x51 && subtype == 0x20 && (major == 1 || major == 2) &&
 minor == 0 && rev2 == 0; }
/* Return 1 for exactly one permitted probe write; 0 for no write. */
static inline int dby_chg_float_action(int read_error, unsigned int value)
{
 if (read_error) return -EIO;
 if (value == 0x55) return 1;
 return value == DBY_CHG_FLOAT_VALUE ? 0 : -EINVAL;
}
static inline unsigned int dby_chg_conditions(const struct dby_chg_sample *s)
{
 unsigned int reason = 0;
 int i;
 if (s->identity_error) reason |= CHG_IDENTITY;
 for (i = 0; i < CHG_NREG; i++) if (s->error[i]) reason |= CHG_READ;
 if (s->temp_error || s->voltage_error || s->current_error || s->capacity_error)
  reason |= CHG_READ;
 if (s->error[CHG_FLOAT] || s->reg[CHG_FLOAT] != DBY_CHG_FLOAT_VALUE) reason |= CHG_BAD_FLOAT;
 if (s->error[CHG_USB] || !(s->reg[CHG_USB] & 1)) reason |= CHG_ABSENT;
 if (s->error[CHG_HW_TEMP] || s->reg[CHG_HW_TEMP]) reason |= CHG_BAD_HW_TEMP;
 if (s->temp_error || s->temp_decic < DBY_CHG_TEMP_MIN || s->temp_decic > DBY_CHG_TEMP_MAX)
  reason |= CHG_BAD_TEMP;
 if (s->voltage_error || s->voltage_uv <= 0 || s->voltage_uv > DBY_CHG_VOLT_MAX)
  reason |= CHG_BAD_VOLTAGE;
 return reason;
}
/* One observation starts the dwell; elapsed time must reach >=180 s.
 * Gaps over 2 intervals (e.g. suspend) break continuity, never count as dwell.
 * IRQ observations cannot shorten the dwell. qualifying is diagnostic only. */
static inline void dby_chg_update(struct dby_chg_state *st,
 const struct dby_chg_sample *s, unsigned long long now)
{
 unsigned int online = !s->error[CHG_USB] && (s->reg[CHG_USB] & 1);
 if (!s->error[CHG_USB] && !online) st->offline_seen = 1;
 /* Clearing the software latch cannot clear a watchdog bite. Device test
  * 2026-11-05: charging after a bite requires USB unplug/replug. */
 if (st->full && ((!s->voltage_error && s->voltage_uv > 0 &&
                  s->voltage_uv < DBY_CHG_RECHARGE_UV) ||
                 (online && st->offline_seen))) {
  st->full = 0;
  st->qualifying = 0;
 }
 if (online) st->offline_seen = 0;
 if (st->full) return;
 if (dby_chg_conditions(s) || s->reg[CHG_ENABLE] != DBY_CHG_ENABLE_ON || s->voltage_uv < DBY_CHG_FULL_UV ||
     s->current_ua < 0 || s->current_ua >= DBY_CHG_TERM_UA) {
  st->qualifying = 0;
  return;
 }
 if (!st->qualifying || now < st->previous_ms ||
     now - st->previous_ms > 2 * DBY_CHG_INTERVAL_MS) {
  st->first_ms = st->previous_ms = now;
  st->qualifying = 1;
  return;
 }
 if (now == st->previous_ms) return;
 st->previous_ms = now;
 if (st->qualifying != ~0u) st->qualifying++;
 if (now - st->first_ms >= DBY_CHG_FULL_MS)
  st->full = 1;
}
static inline unsigned int dby_chg_decide(const struct dby_chg_sample *s,
 const struct dby_chg_state *st, unsigned long long now,
 unsigned int attempts, unsigned long long last)
{
 unsigned int reason = dby_chg_conditions(s) &
  (CHG_IDENTITY | CHG_READ | CHG_BAD_FLOAT | CHG_ABSENT);
 (void)st;
 if (s->reg[CHG_ENABLE] > DBY_CHG_ENABLE_ON) reason |= CHG_BAD_ENABLE;
 if (attempts && (now < last || now - last < DBY_CHG_INTERVAL_MS)) reason |= CHG_TOO_SOON;
 return reason;
}
static inline int dby_chg_status(const struct dby_chg_sample *s,
 const struct dby_chg_state *st, int feeding)
{
 if (s->error[CHG_USB]) return CHG_NOT_CHARGING;
 if (!(s->reg[CHG_USB] & 1)) return CHG_DISCHARGING;
 if (st->full) return !s->capacity_error && s->capacity >= 98 ? CHG_STATUS_FULL : CHG_NOT_CHARGING;
 if (feeding && !s->error[CHG_ENABLE] && s->reg[CHG_ENABLE] == DBY_CHG_ENABLE_ON && !s->current_error && s->current_ua > 0) return CHG_CHARGING;
 return CHG_NOT_CHARGING;
}
/* Runtime limits form a nonempty re-enable window, including its endpoints. */
static inline int dby_chg_limits_valid(int low, int high)
{
 return low >= DBY_CHG_TEMP_MIN && low <= 350 && high >= 150 &&
        high <= DBY_CHG_TEMP_MAX && low + 2 * DBY_CHG_TEMP_HYST <= high;
}
struct dby_chg_control {
 unsigned int want_enable, temp_blocked, voltage_blocked;
 unsigned int owned_off, initial_off, stopped, count, have_toggle;
 unsigned int remove_attempted;
 unsigned long long toggles[DBY_CHG_TOGGLE_LIMIT], last_toggle_ms;
};
enum dby_chg_action { CHG_KEEP, CHG_OFF, CHG_ON };
static inline int dby_chg_enable_probe(struct dby_chg_control *c,
 int error, unsigned int value)
{
 if (error) return -EIO;
 if (value > DBY_CHG_ENABLE_ON) return -EINVAL;
 c->initial_off = value == DBY_CHG_ENABLE_OFF;
 return 0;
}
/* Sliding window (now - 1 hour, now]; backwards clocks retain all entries. */
static inline void dby_chg_prune(struct dby_chg_control *c, unsigned long long now)
{
 unsigned int i, n = 0;
 for (i = 0; i < c->count; i++)
  if (now < c->toggles[i] || now - c->toggles[i] < DBY_CHG_HOUR_MS)
   c->toggles[n++] = c->toggles[i];
 c->count = n;
}
static inline unsigned int dby_chg_policy(struct dby_chg_control *c,
 const struct dby_chg_sample *s, const struct dby_chg_state *st, int low, int high)
{
 unsigned int reason = dby_chg_conditions(s) & ~CHG_BAD_TEMP;
 /* Offline/unknown USB must preserve desired state and ownership, including
  * the worker's preliminary policy evaluation before FULL bookkeeping. */
 if (reason & CHG_ABSENT) return reason;
 if (!s->error[CHG_ENABLE] && s->reg[CHG_ENABLE] == DBY_CHG_ENABLE_OFF) {
  if (!s->temp_error && (s->temp_decic < low + DBY_CHG_TEMP_HYST ||
                         s->temp_decic > high - DBY_CHG_TEMP_HYST)) c->temp_blocked = 1;
  if (!s->voltage_error && s->voltage_uv > DBY_CHG_VOLT_REENABLE) c->voltage_blocked = 1;
 }
 if (!s->temp_error) {
  if (!dby_chg_limits_valid(low, high) || s->temp_decic < low || s->temp_decic > high)
   c->temp_blocked = 1;
  else if (s->temp_decic >= low + DBY_CHG_TEMP_HYST &&
           s->temp_decic <= high - DBY_CHG_TEMP_HYST)
   c->temp_blocked = 0;
 }
 if (!s->voltage_error) {
  if (s->voltage_uv <= 0 || s->voltage_uv > DBY_CHG_VOLT_MAX) c->voltage_blocked = 1;
  else if (s->voltage_uv <= DBY_CHG_VOLT_REENABLE) c->voltage_blocked = 0;
 }
 if (s->temp_error || c->temp_blocked) reason |= CHG_BAD_TEMP;
 if (c->voltage_blocked) reason |= CHG_BAD_VOLTAGE;
 if (st->full) reason |= CHG_FULL;
 if (s->reg[CHG_ENABLE] > DBY_CHG_ENABLE_ON) reason |= CHG_BAD_ENABLE;
 c->want_enable = !reason;
 return reason;
}
static inline enum dby_chg_action dby_chg_plan(struct dby_chg_control *c,
 const struct dby_chg_sample *s, const struct dby_chg_state *st,
 int low, int high, unsigned long long now, unsigned int *reason)
{
 *reason = dby_chg_policy(c, s, st, low, high);
 /* Only confirmed-present runtime transitions consume the sliding budget.
  * Absence never clears a latched fault or updates transition history. */
 if (*reason & CHG_ABSENT) { *reason |= c->stopped; return CHG_KEEP; }
 dby_chg_prune(c, now);
 if (c->stopped) { *reason |= c->stopped; c->want_enable = 0; return CHG_KEEP; }
 if (!s->error[CHG_ENABLE] && s->reg[CHG_ENABLE] > DBY_CHG_ENABLE_ON) {
  c->stopped = CHG_BAD_ENABLE;
  return CHG_KEEP;
 }
 if (*reason & (CHG_READ | CHG_IDENTITY)) return CHG_KEEP;
 if (s->reg[CHG_ENABLE] == DBY_CHG_ENABLE_ON) {
  c->initial_off = 0;
  c->owned_off = 0;
 }
 if (c->want_enable == s->reg[CHG_ENABLE]) return CHG_KEEP;
 if (c->want_enable && !c->owned_off && !c->initial_off) {
  c->stopped = CHG_UNOWNED; c->want_enable = 0; *reason |= c->stopped;
  return CHG_KEEP;
 }
 if (c->have_toggle && (now < c->last_toggle_ms ||
                       now - c->last_toggle_ms < DBY_CHG_TOGGLE_MS)) {
  *reason |= CHG_TOGGLE_WAIT;
  return CHG_KEEP;
 }
 if (c->count >= DBY_CHG_TOGGLE_LIMIT) {
  c->stopped = CHG_BUDGET; c->want_enable = 0; *reason |= c->stopped;
  return CHG_KEEP;
 }
 return c->want_enable ? CHG_ON : CHG_OFF;
}
/* Fresh pre-write read: unknown never writes; changed old value is terminal. */
static inline int dby_chg_prewrite(struct dby_chg_control *c,
 enum dby_chg_action action, int error, unsigned int old)
{
 if (error) { c->want_enable = 0; return -EIO; }
 if (old != (action == CHG_ON ? DBY_CHG_ENABLE_OFF : DBY_CHG_ENABLE_ON)) {
  c->stopped = CHG_BAD_ENABLE; c->want_enable = 0;
  return -EINVAL;
 }
 return 0;
}
/* Record at actual write time, before the I/O: ambiguous failures never retry. */
static inline void dby_chg_toggle_attempt(struct dby_chg_control *c,
 unsigned long long now)
{
 c->toggles[c->count++] = now;
 c->last_toggle_ms = now;
 c->have_toggle = 1;
}
static inline int dby_chg_toggle_result(struct dby_chg_control *c,
 enum dby_chg_action action, int error, unsigned int readback)
{
 unsigned int expected = action == CHG_ON ? DBY_CHG_ENABLE_ON : DBY_CHG_ENABLE_OFF;
 if (error || readback != expected) {
  c->stopped = CHG_WRITE_ERROR; c->want_enable = 0;
  return -EIO;
 }
 c->owned_off = action == CHG_OFF;
 c->initial_off = 0;
 return 0;
}
/* User-approved exit exception: one ON attempt may bypass runtime limits.
 * Never retry a failed runtime write; shutdown never calls the write adapter. */
static inline int dby_chg_remove_allowed(const struct dby_chg_control *c,
 int evaluated, int shutdown)
{
 return !shutdown && evaluated && c->want_enable && c->owned_off &&
        !c->stopped && !c->remove_attempted;
}
/* Diagnostic only: sum observed non-feeding intervals in this USB session.
 * online: -1 unreadable, 0 absent, 1 present. Unknown never invents a new
 * session or resets an existing one. This is NOT a watchdog-bite detector,
 * nor a gate: suspend while previously feeding is not observable here. */
struct dby_chg_pause {
 unsigned long long total_ms, since_ms;
 unsigned int session, paused;
};
static inline unsigned long long dby_chg_pause_ms(const struct dby_chg_pause *p,
 unsigned long long now)
{
 return p->total_ms + (p->session && p->paused && now >= p->since_ms ?
                      now - p->since_ms : 0);
}
static inline void dby_chg_pause_update(struct dby_chg_pause *p, int online,
 int feeding, unsigned long long now)
{
 p->total_ms = dby_chg_pause_ms(p, now);
 if (!online) {
  p->session = p->paused = 0;
  p->total_ms = 0;
 } else {
  if (online > 0) p->session = 1;
  p->paused = p->session && !feeding;
 }
 p->since_ms = now;
}
static inline int dby_chg_online(const struct dby_chg_sample *s)
{ return s->error[CHG_USB] ? -1 : !!(s->reg[CHG_USB] & 1); }
/* Event logging deliberately ignores changing numerical samples/counters. */
static inline int dby_chg_event(int evaluated, int old_online, int online,
 int old_feeding, int feeding, int old_full, int full,
 unsigned int old_reason, unsigned int reason)
{
 return !evaluated || old_online != online || old_feeding != feeding ||
        old_full != full || old_reason != reason;
}
#endif
