// SPDX-License-Identifier: GPL-2.0-only
/* Fixed-table THP downloader; optional generation-scoped panel recovery. */
#ifdef THP_HOST_TEST
#include "host-backend.h"
#include "auto-host-api.h"
#include "tap-host-api.h"
#else
#include <linux/workqueue.h>
#include <linux/spinlock.h>
#include <linux/input.h>
#include <linux/input/mt.h>
#include <linux/input/touchscreen.h>
#include <linux/delay.h>
#include <linux/kthread.h>
#include <linux/miscdevice.h>
#include <linux/fs.h>
#include <linux/poll.h>
#include <linux/uaccess.h>
#include <linux/vmalloc.h>
#include <linux/completion.h>
#include <linux/interrupt.h>
#include <linux/ktime.h>
#include <linux/firmware.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/slab.h>
#include <linux/spi/spi.h>
#include <crypto/sha2.h>
#include <drm/drm_panel.h>
#endif
#include "thp-plan-table.h"
#include "thp-algo-core.h"
#include "param-core.h"
#include "thp-filter-core.h"
#include "thp-confirm-core.h"
#include "thp-multi-core.h"
#include "thp-guard-core.h"

enum nt_state { NT_IDLE, NT_DONE, NT_ABORTED };
#define NT_FRAME_SIZE 3257U
#define NT_CAPTURE_FRAMES 8U
#define NT_CAPTURE_IRQ_LIMIT 64U
#define NT_CAPTURE_MS 2000U
/* Whole RX is preserved; checkRaw's parser starts at RX+6. */
#define NT_RAW_BASE 6U
#define NT_COUNT_OFFSET (NT_RAW_BASE + 3212U)
#define NT_CRC_OFFSET (NT_RAW_BASE + 3214U)
#define NT_PEN_OFFSET (NT_RAW_BASE + 3216U)
#define NT_PEN_CRC_OFFSET (NT_RAW_BASE + 3246U)
enum nt_cap_state { NT_CAP_IDLE, NT_CAP_RUNNING, NT_CAP_DONE, NT_CAP_ABORTED };
enum nt_cap_reason { NT_CAP_NONE, NT_CAP_LIMIT, NT_CAP_TIMEOUT, NT_CAP_PANEL,
 NT_CAP_IRQ_STORM, NT_CAP_SPI, NT_CAP_LENGTH, NT_CAP_CHECKSUM,
 NT_CAP_REPEAT, NT_CAP_BACKWARD, NT_CAP_ZERO, NT_CAP_FF, NT_CAP_REQUEST };
struct nt_frame_info { u16 counter; bool checksum, pen_checksum; };
#define NT_RING_FRAMES 64U
#define NT_STREAM_QUOTA 64U
#define NT_STREAM_MAX_SECONDS 1800U
#define NT_STREAM_TICK_MS 100U
#define NT_RESILIENCE_V2 1
#define NT_NATIVE_ORIENTATION 1
#define NT_STREAM_STALL_MS 500U
#define NT_STREAM_READER_MS 5000U
#define NT_STREAM_IRQ_MS 1000U
#define NT_STREAM_IRQ_LIMIT 400U
enum nt_stream_state { NT_S_IDLE, NT_S_RUNNING, NT_S_ENDED, NT_S_ABORTED };
enum nt_stream_reason { NT_S_NONE, NT_S_DEADLINE, NT_S_CLOSED, NT_S_NO_READER,
 NT_S_STALL, NT_S_STORM, NT_S_PANEL, NT_S_SPI, NT_S_LENGTH, NT_S_MAIN_CRC,
 NT_S_PEN_CRC, NT_S_ZERO, NT_S_FF, NT_S_REPEAT, NT_S_BACKWARD, NT_S_REQUEST, NT_S_INPUT_OFF };
#include "tap-types.h"
struct nt_stream {
 struct nt_tap tap;
 struct tf_core filter_state;
 struct tc_core confirm_state;
 /* Heap-owned with nt_stream, never a per-frame stack object. */
 struct tm_core multi_state;
 struct tg_core guard_state;
 unsigned int multi_generation;
 bool multi_mode,slot_active[TM_MAX];
 s32 slot_x[TM_MAX],slot_y[TM_MAX];
 unsigned int touches;
 unsigned int confirm_generation;
 s32 tap_input_x,tap_input_y;
 struct ta_core *algo;
 struct input_dev *input_dev;
 struct touchscreen_properties prop;
 unsigned int input_phase;
 enum nt_stream_reason input_reason;
 u64 input_frames;
 bool native, input_seen, input_down;
 bool managed, probing;
 unsigned int probe_good;
 u64 probe_hit_ms;
 u64 dups, resyncs, recoveries, probe_irqs, probe_frames;
 enum nt_stream_reason last_bad;
 unsigned long generation;
 s32 input_x, input_y;
 s32 input_mapped_x, input_mapped_y;
 s32 edge_lo[2], edge_hi[2], edge_max[2];
 u64 input_accepted, input_rejected, input_downs, input_moves, input_ups;
 u64 proc_count, proc_max_ns, proc_over2ms, proc_over6ms;
 struct mutex life, lock, read_lock;
 struct completion exited; /* IRQ drained; task joined under life. */
 struct task_struct *owner;
 wait_queue_head_t wake, readers;
 struct miscdevice misc;
 struct nt_thp *nt;
 unsigned int refs, sessions, head, count;
 bool registered, removed, reader, seen_reader, poisoned, stop, pending, freed;
 enum nt_stream_state state;
 enum nt_stream_reason reason;
 u64 start_ms, deadline_ms, elapsed_ms, last_frame_ms, frames, delivered, dropped, gaps, irqs;
 u64 rate_ms, rate_count, max_irqs;
 u64 irq_slots[NT_STREAM_IRQ_MS];
 u16 counter;
 bool have_counter;
 u8 *ring;
 u8 reader_frame[NT_FRAME_SIZE];
};
static void nt_stream_quiet(struct nt_stream *s);
static bool nt_stream_busy(struct nt_stream *s);
struct nt_auto {
 struct nt_thp *nt;
 struct mutex control;
 struct workqueue_struct *wq;
 struct delayed_work work;
 bool enabled, removed, down_seen, attempted, first_pending, internal_download;
 unsigned int settle_ms, boot_wait_ms, phase, streak;
 unsigned int recovery_attempts;
 bool recovering;
 u64 download_end_ms, boot_ms, boot_max_ms;
 bool fw_lost, prepared_valid;
 u64 prepared_ms, probe_skips;
 s64 prep_gap, w_prep_gap, w_settle, w_probe, w_dl, w_boot, w_first, w_off;
 bool d18_valid;
 u8 d18;
 unsigned long queued_gen, attempted_gen;
 u64 sessions, resumes, reloads, alive_hits, failures, last_resume_ms, enabled_ms;
 int fw, last_error;
 const char *last_reason;
};
struct nt_thp {
 struct nt_auto auto_state;
 bool auto_initialized;
 struct nt_stream *stream;
#ifndef THP_HOST_TEST
	struct spi_device *spi;
#else
	void *backend;
#endif
	struct mutex lock;
	/* lock -> panel_lock; callbacks release panel_lock before waiting on lock. */
	struct mutex panel_lock;
	struct drm_panel_follower follower;
	bool follower_registered;
	bool panel_on;
	bool dead;
	bool armed;
	unsigned long gen;
	unsigned long download_gen;
	bool used;
	bool started;
	bool verified;
	bool aborted;
	bool quiesce;
	enum nt_state state;
	unsigned int steps;
	unsigned int reads;
	int error;
	int last_error;
	int current_step;
	u8 values[NT_READ_SLOTS][6];
	bool value_valid[NT_READ_SLOTS];
 struct mutex cap_lock;
 struct completion cap_completion;
 int irq;
 bool cap_used, cap_stop, cap_freed, cap_timeout;
 enum nt_cap_state cap_state;
 enum nt_cap_reason cap_reason;
 unsigned int cap_frames, cap_irqs;
 u64 cap_start_ms, cap_elapsed_ms;
 struct nt_frame_info cap_info[NT_CAPTURE_FRAMES];
 u8 frames[NT_CAPTURE_FRAMES][NT_FRAME_SIZE];
	u8 *tx;
	u8 *rx;
};

static int nt_auto_event(struct nt_thp *nt, unsigned int event);
static void nt_auto_frame(struct nt_thp *nt);
static void nt_auto_ended(struct nt_thp *nt);
static int nt_auto_init(struct nt_thp *nt, bool initial, unsigned int settle, unsigned int boot_wait);
static void nt_auto_destroy(struct nt_thp *nt);
static bool nt_auto_active(struct nt_thp *nt)
{ return nt && nt->auto_initialized && READ_ONCE(nt->auto_state.enabled); }
#ifdef THP_HOST_TEST
static void nt_host_auto_boundary(struct nt_thp *nt, unsigned int phase);
#endif
#ifdef THP_HOST_TEST
static int nt_bus_sync(struct nt_thp *nt, const u8 *tx, u8 *rx, size_t len);
static int nt_get_firmware(struct nt_thp *nt, const struct firmware **fw);
static void nt_put_firmware(const struct firmware *fw);
static void nt_delay(struct nt_thp *nt, unsigned int ms);
static void nt_host_boundary(struct nt_thp *nt, unsigned int index, int attempt);
#else
static int nt_bus_sync(struct nt_thp *nt, const u8 *tx, u8 *rx, size_t len)
{
	struct spi_transfer transfer = {
		.tx_buf = tx, .rx_buf = rx, .len = len,
		.bits_per_word = 8, .speed_hz = nt->spi->max_speed_hz,
	};
	struct spi_message message;
	int ret;

	spi_message_init(&message);
	spi_message_add_tail(&transfer, &message);
	ret = spi_sync(nt->spi, &message);
	if (!ret && message.actual_length != len)
		return -EIO;
	return ret;
}

static int nt_get_firmware(struct nt_thp *nt, const struct firmware **fw)
{
	return request_firmware(fw, "novatek/nt36523-thp.bin", &nt->spi->dev);
}

static void nt_put_firmware(const struct firmware *fw)
{
	release_firmware(fw);
}

static void nt_delay(struct nt_thp *nt, unsigned int ms)
{
	if (ms)
		msleep(ms);
}
#endif

static const char *nt_state_name(const struct nt_thp *nt)
{
	if (nt->state == NT_DONE)
		return "done";
	if (nt->state == NT_ABORTED)
		return "aborted";
	return "idle";
}

static int nt_abort(struct nt_thp *nt, int error)
{
	if (!nt->aborted) {
		nt->aborted = true;
		nt->state = NT_ABORTED;
		nt->error = error < 0 ? error : -EIO;
	}
	return nt->error;
}

/* No DRM lock is taken here; only our short-lived bookkeeping mutex. */
static int nt_generation_check(struct nt_thp *nt)
{
 bool invalid;

 mutex_lock(&nt->panel_lock);
 invalid = nt->dead || nt->gen != nt->download_gen || READ_ONCE(nt->quiesce);
 mutex_unlock(&nt->panel_lock);
 return invalid ? nt_abort(nt, -ECANCELED) : 0;
}

static int nt_boundary(struct nt_thp *nt, unsigned int index, int attempt)
{
#ifdef THP_HOST_TEST
 nt_host_boundary(nt, index, attempt);
 if(nt->auto_initialized && READ_ONCE(nt->auto_state.internal_download))nt_host_auto_boundary(nt,100+index);
#endif
 return nt_generation_check(nt);
}

static int nt_bus_guard(struct nt_thp *nt)
{
 if(nt->stream && READ_ONCE(nt->stream->probing) && READ_ONCE(nt->stream->state)==NT_S_RUNNING)return -EBUSY;

	if (nt->aborted)
		return nt->error;
	if (nt->state == NT_DONE)
		return -EBUSY;
	if (!nt->verified || !nt->started || (!nt->used && !READ_ONCE(nt->auto_state.internal_download)))
		return nt_abort(nt, -EPERM);
	return 0;
}

/* A page select and its payload are separate messages, deasserting CS. */
static int nt_page(struct nt_thp *nt, const struct nt_step *step)
{
	int ret = nt_bus_guard(nt);

	if (ret)
		return ret;
	nt->tx[0] = 0xff;
	nt->tx[1] = (step->address >> 15) & 0xff;
	nt->tx[2] = (step->address >> 7) & 0xff;
	ret = nt_bus_sync(nt, nt->tx, NULL, 3);
	return ret ? nt_abort(nt, ret) : 0;
}

/* No caller supplies an address: only a fixed, validated table index. */
static int nt_write_step(struct nt_thp *nt, unsigned int index, const u8 *data)
{
	const struct nt_step *step;
	int ret = nt_bus_guard(nt);

	if (ret)
		return ret;
	if (index >= NT_STEP_COUNT || (int)index != nt->current_step)
		return nt_abort(nt, -EPERM);
	step = &nt_plan[index];
	if (step->kind == NT_READ || !step->length ||
	    step->length > NT_MAX_PAYLOAD)
		return nt_abort(nt, -EINVAL);
	ret = nt_page(nt, step);
	if (ret)
		return ret;
	nt->tx[0] = (step->address & 0x7f) | 0x80;
	memcpy(nt->tx + 1, data, step->length);
	ret = nt_bus_sync(nt, nt->tx, NULL, step->length + 1);
	return ret ? nt_abort(nt, ret) : 0;
}

static int nt_read_step(struct nt_thp *nt, unsigned int index, u8 *data)
{
	const struct nt_step *step = &nt_plan[index];
	int ret = nt_bus_guard(nt);

	if (ret)
		return ret;
	if (step->kind != NT_READ || !step->length || step->length > 6)
		return nt_abort(nt, -EINVAL);
	ret = nt_page(nt, step);
	if (ret)
		return ret;
	memset(nt->tx, 0, step->length + 2);
	memset(nt->rx, 0, step->length + 2);
	nt->tx[0] = step->address & 0x7f;
	ret = nt_bus_sync(nt, nt->tx, nt->rx, step->length + 2);
	if (ret)
		return nt_abort(nt, ret);
	memcpy(data, nt->rx + 2, step->length);
	return 0;
}

static int nt_poll_step(struct nt_thp *nt, unsigned int index, u8 *data)
{
	const struct nt_step *step = &nt_plan[index];
	unsigned int attempt;
	int ret;

	for (attempt = 0; attempt < step->max_reads; attempt++) {
		ret = nt_boundary(nt, index, attempt);
		if (ret)
			return ret;
		if (attempt && step->condition == NT_RANGE)
			nt_delay(nt, step->interval_ms);
		ret = nt_generation_check(nt);
		if (ret)
			return ret;
		if (nt->reads >= NT_READ_LIMIT)
			return nt_abort(nt, -E2BIG);
		nt->reads++;
		nt->value_valid[step->read_slot] = false;
		ret = nt_read_step(nt, index, data);
		if (ret)
			return ret;
		memcpy(nt->values[step->read_slot], data, step->length);
		nt->value_valid[step->read_slot] = true;
		if (step->condition == NT_ANY ||
		    (step->condition == NT_EQUAL && data[0] == step->minimum) ||
		    (step->condition == NT_RANGE && data[0] >= step->minimum &&
		     data[0] <= step->maximum))
			return 0;
		if (step->condition == NT_EQUAL)
			nt_delay(nt, step->interval_ms);
	}
	return nt_abort(nt, -ETIMEDOUT);
}

static int nt_execute_blob(struct nt_thp *nt, const u8 *fw, size_t size)
{
	u8 digest[SHA256_DIGEST_SIZE];
	u8 read_data[6];
	u8 value = 0;
	u8 previous_byte = 0;
	bool fresh = false;
	int previous_index = -1;
	u32 previous_address = 0;
	unsigned int i;
	int ret;

 if(nt->stream && READ_ONCE(nt->stream->probing) && READ_ONCE(nt->stream->state)==NT_S_RUNNING)return -EBUSY;
	if (nt->started || nt->aborted)
		return -EBUSY;
	nt->started = true;
	if (size != NT_FW_SIZE)
		return nt_abort(nt, -EMSGSIZE);
	sha256(fw, size, digest);
	if (memcmp(digest, nt_firmware_sha256, sizeof(digest)))
		return nt_abort(nt, -EBADMSG);
	nt->verified = true;
	for (i = 0; i < NT_STEP_COUNT; i++) {
		const struct nt_step *step = &nt_plan[i];
		const u8 *data;

		nt->current_step = i;
		ret = nt_boundary(nt, i, -1);
		if (ret)
			return ret;
		if (step->kind == NT_READ) {
			fresh = false;
			ret = nt_poll_step(nt, i, read_data);
			if (ret)
				return ret;
			if (step->consumer_step >= 0 && step->length == 1) {
				fresh = true;
				previous_index = i;
				previous_address = step->address;
				previous_byte = read_data[0];
			}
		} else {
			switch (step->kind) {
			case NT_FIXED:
				data = step->fixed;
				break;
			case NT_SRAM:
				if (step->source_offset > size ||
				    step->length > size - step->source_offset)
					return nt_abort(nt, -EINVAL);
				data = fw + step->source_offset;
				break;
			case NT_RMW:
				if (!fresh || step->length != 1 ||
				    previous_index != (int)i - 1 ||
				    step->source_step != previous_index ||
				    previous_address != step->address ||
				    nt_plan[previous_index].consumer_step != (int)i)
					return nt_abort(nt, -EPROTO);
				value = (previous_byte & step->preserve_mask) |
					step->set_mask;
				data = &value;
				break;
			default:
				return nt_abort(nt, -EINVAL);
			}
			fresh = false;
			ret = nt_write_step(nt, i, data);
			if (ret)
				return ret;
			nt_delay(nt, step->wait_ms);
		}
		nt->steps++;
	}
	ret = nt_generation_check(nt);
	if (ret)
		return ret;
	nt->state = NT_DONE;
	nt->error = 0;
	return 0;
}

/* Both sysfs entry points take lock before panel_lock. Failed arm is read-only. */
static ssize_t nt_arm(struct nt_thp *nt, const char *buf, size_t count)
{
 int ret = 0;

 if (nt_auto_active(nt))return -EBUSY;
 if ((count != 7 && count != 8) || memcmp(buf, "THP-ARM", 7) ||
     (count == 8 && buf[7] != '\n'))
  return -EINVAL;
 mutex_lock(&nt->lock);
 mutex_lock(&nt->panel_lock);
 /* Permanent invalidation takes precedence even after a later enabled event. */
 if(nt_auto_active(nt))ret=-EBUSY;
 else if (nt->dead)
  ret = -ESHUTDOWN;
 else if (nt->used)
  ret = -EBUSY;
 else if (!nt->panel_on)
  ret = -EAGAIN;
 else
  nt->armed = true;
 mutex_unlock(&nt->panel_lock);
 mutex_unlock(&nt->lock);
 return ret ? ret : (ssize_t)count;
}

/* Caller holds lock; a valid, armed request consumes once even on failure. */
static int nt_claim(struct nt_thp *nt, const char *buf, size_t count)
{
	if (nt_auto_active(nt))return -EBUSY;
	if (nt->used)
		return -EBUSY;
	if ((count != 8 && count != 9) || memcmp(buf, "THP-ONCE", 8) ||
	    (count == 9 && buf[8] != '\n'))
		return -EINVAL;
 mutex_lock(&nt->panel_lock);
 if (!nt->armed) {
  mutex_unlock(&nt->panel_lock);
  return -EPERM;
 }
 nt->armed = false;
 nt->download_gen = nt->gen;
 nt->used = true;
 mutex_unlock(&nt->panel_lock);
	if (READ_ONCE(nt->quiesce))
		return nt_abort(nt, -ESHUTDOWN);
	return 0;
}

static ssize_t nt_download(struct nt_thp *nt, const char *buf, size_t count)
{
	const struct firmware *fw;
	int ret;

	mutex_lock(&nt->lock);
	ret = nt_claim(nt, buf, count);
	if (ret)
		goto out;
	ret = nt_get_firmware(nt, &fw);
	if (ret) {
		ret = nt_abort(nt, ret);
		goto out;
	}
	ret = nt_execute_blob(nt, fw->data, fw->size);
	nt_put_firmware(fw);
out:
	nt->last_error = ret;
	mutex_unlock(&nt->lock);
	return ret ? ret : (ssize_t)count;
}

/* Set before locking so an in-flight download sees the request at a boundary.
 * Returning with quiesce=true guarantees the download has released its lock.
 */
static void nt36523_thp_set_quiesce(struct nt_thp *nt, bool quiesce)
{
	if (quiesce)
		WRITE_ONCE(nt->quiesce, true);
	mutex_lock(&nt->lock);
	if (!quiesce)
		WRITE_ONCE(nt->quiesce, false);
	mutex_unlock(&nt->lock);
 if (quiesce && nt->stream)
  nt_stream_quiet(nt->stream);
}

static int nt_panel_prepared_legacy(struct drm_panel_follower *follower)
{
 struct nt_thp *nt = container_of(follower, struct nt_thp, follower);

 mutex_lock(&nt->panel_lock);
 nt->gen++;
 mutex_unlock(&nt->panel_lock);
 return 0;
}

static int nt_panel_enabled_legacy(struct drm_panel_follower *follower)
{
 struct nt_thp *nt = container_of(follower, struct nt_thp, follower);

 mutex_lock(&nt->panel_lock);
 nt->panel_on = true;
 nt->gen++;
 mutex_unlock(&nt->panel_lock);
 return 0;
}

static int nt_panel_stopping_legacy(struct drm_panel_follower *follower)
{
 struct nt_thp *nt = container_of(follower, struct nt_thp, follower);

 mutex_lock(&nt->panel_lock);
 nt->panel_on = false;
 nt->armed = false;
 nt->dead = true;
 nt->gen++;
 mutex_unlock(&nt->panel_lock);
 nt36523_thp_set_quiesce(nt, true);
 return 0;
}

static int nt_panel_prepared(struct drm_panel_follower *f)
{ struct nt_thp *nt=container_of(f,struct nt_thp,follower);return nt->auto_initialized?nt_auto_event(nt,0):nt_panel_prepared_legacy(f); }
static int nt_panel_enabled(struct drm_panel_follower *f)
{ struct nt_thp *nt=container_of(f,struct nt_thp,follower);return nt->auto_initialized?nt_auto_event(nt,1):nt_panel_enabled_legacy(f); }
static int nt_panel_stopping(struct drm_panel_follower *f)
{ struct nt_thp *nt=container_of(f,struct nt_thp,follower);return nt->auto_initialized?nt_auto_event(nt,2):nt_panel_stopping_legacy(f); }

static int nt_panel_disabling(struct drm_panel_follower *follower)
{
 return nt_panel_stopping(follower);
}

static int nt_panel_unpreparing(struct drm_panel_follower *follower)
{
 struct nt_thp *nt=container_of(follower,struct nt_thp,follower);
 return nt->auto_initialized?nt_auto_event(nt,3):nt_panel_stopping_legacy(follower);
}

static const struct drm_panel_follower_funcs nt_panel_funcs = {
 .panel_prepared = nt_panel_prepared,
 .panel_enabled = nt_panel_enabled,
 .panel_disabling = nt_panel_disabling,
 .panel_unpreparing = nt_panel_unpreparing,
};

static int nt_follower_add(struct nt_thp *nt, struct device *dev)
{
 int ret;

 if (!drm_is_panel_follower(dev))
  return -ENODEV;
 nt->follower.funcs = &nt_panel_funcs;
 ret = drm_panel_add_follower(dev, &nt->follower);
 if (ret)
  return ret;
 nt->follower_registered = true;
 return 0;
}

static void nt_follower_remove(struct nt_thp *nt)
{
 nt36523_thp_set_quiesce(nt, true);
 if (nt->follower_registered) {
  drm_panel_remove_follower(&nt->follower);
  nt->follower_registered = false;
 }
}

/* Capture owner holds lock. IRQ thread never takes lock or a DRM lock.
 * cap_lock serializes the IRQ reader and binary/status readers. It is never
 * held across owner-side disable_irq/free_irq or completion waiting.
 */
#ifdef THP_HOST_TEST
static u64 nt_now_ms(struct nt_thp *nt);
static int nt_capture_bus_sync(struct nt_thp *nt, const u8 *tx, u8 *rx, size_t len, size_t *actual);
static int nt_request_capture_irq(struct nt_thp *nt);
static void nt_disable_capture_irq(struct nt_thp *nt);
static void nt_free_capture_irq(struct nt_thp *nt);
static bool nt_wait_capture(struct nt_thp *nt);
#else
static u64 nt_now_ms(struct nt_thp *nt)
{
 return ktime_to_ms(ktime_get());
}

static int nt_capture_bus_sync(struct nt_thp *nt, const u8 *tx, u8 *rx, size_t len, size_t *actual)
{
 struct spi_transfer transfer = {
  .tx_buf = tx, .rx_buf = rx, .len = len,
  .bits_per_word = 8, .speed_hz = nt->spi->max_speed_hz,
 };
 struct spi_message message;
 int ret;

 spi_message_init(&message);
 spi_message_add_tail(&transfer, &message);
 ret = spi_sync(nt->spi, &message);
 *actual = message.actual_length;
 return ret;
}
#endif

/* Common to kernel and host: actual_length must be independently verified. */
static int nt_capture_transfer(struct nt_thp *nt)
{
 size_t actual = 0;
 int ret = nt_capture_bus_sync(nt, nt->tx, nt->rx, NT_FRAME_SIZE, &actual);

 if (!ret && actual != NT_FRAME_SIZE)
  return -EMSGSIZE;
 return ret;
}

static bool nt_capture_invalid(struct nt_thp *nt)
{
 bool invalid;

 mutex_lock(&nt->panel_lock);
 invalid = nt->dead || !nt->panel_on || nt->gen != nt->download_gen ||
           READ_ONCE(nt->quiesce);
 mutex_unlock(&nt->panel_lock);
 return invalid;
}

static u16 nt_le16(const u8 *data)
{
 return (u16)data[0] | ((u16)data[1] << 8);
}

/* checkRaw.c:47-49,303-306; arithmetic: offline protocol audit frame table,
 * AFE @3cc4-3ccc/@27cdc-27d04. No AAAA sentinel or FD/FE guess.
 */
static u16 nt_frame_checksum(const u8 *data, unsigned int length)
{
 u16 sum = 0;
 unsigned int i;

 for (i = 0; i < length; i += 2)
  sum += nt_le16(data + i);
 return (u16)(0U - sum);
}

/* Caller holds cap_lock; first cause sticks, except IRQ>64 overrides N done. */
static void nt_capture_stop(struct nt_thp *nt, enum nt_cap_reason reason)
{
 if (!nt->cap_stop || (nt->cap_reason == NT_CAP_LIMIT && reason != NT_CAP_LIMIT))
  nt->cap_reason = reason;
 nt->cap_stop = true;
}

static void nt_capture_check_frame(struct nt_thp *nt)
{
 unsigned int n = nt->cap_frames, i;
 const u8 *data = nt->frames[n];
 struct nt_frame_info *info = &nt->cap_info[n];
 bool zero = true, ff = true;

 info->counter = nt_le16(data + NT_COUNT_OFFSET);
 info->checksum = nt_frame_checksum(data + NT_RAW_BASE, 3214) == nt_le16(data + NT_CRC_OFFSET);
 info->pen_checksum = nt_frame_checksum(data + NT_PEN_OFFSET, 30) == nt_le16(data + NT_PEN_CRC_OFFSET);
 for (i = 0; i < NT_FRAME_SIZE; i++) {
  zero = zero && data[i] == 0;
  ff = ff && data[i] == 0xff;
 }
 nt->cap_frames++;
 if (zero)
  nt_capture_stop(nt, NT_CAP_ZERO);
 else if (ff)
  nt_capture_stop(nt, NT_CAP_FF);
 else if (!info->checksum || !info->pen_checksum)
  nt_capture_stop(nt, NT_CAP_CHECKSUM);
 else if (n && info->counter == nt->cap_info[n - 1].counter)
  nt_capture_stop(nt, NT_CAP_REPEAT);
 else if (n && info->counter < nt->cap_info[n - 1].counter)
  nt_capture_stop(nt, NT_CAP_BACKWARD);
 else if (nt->cap_frames == NT_CAPTURE_FRAMES)
  nt_capture_stop(nt, NT_CAP_LIMIT);
}

static irqreturn_t nt_capture_irq(int irq, void *dev_id)
{
 struct nt_thp *nt = dev_id;
 bool stopped;
 int ret;

 (void)irq;
 mutex_lock(&nt->cap_lock);
 nt->cap_irqs++;
 if (nt->cap_irqs > NT_CAPTURE_IRQ_LIMIT)
  nt_capture_stop(nt, NT_CAP_IRQ_STORM);
 if (nt_capture_invalid(nt))
  nt_capture_stop(nt, NT_CAP_PANEL);
 if (READ_ONCE(nt->cap_timeout) || nt_now_ms(nt) - nt->cap_start_ms >= NT_CAPTURE_MS)
  nt_capture_stop(nt, NT_CAP_TIMEOUT);
 if (!nt->cap_stop) {
  /* The only capture message, without page selection or write helper. */
  memset(nt->tx, 0, NT_FRAME_SIZE);
  memset(nt->rx, 0, NT_FRAME_SIZE);
  nt->tx[0] = 0x20;
  ret = nt_capture_transfer(nt);
  if (ret)
   nt_capture_stop(nt, ret == -EMSGSIZE ? NT_CAP_LENGTH : NT_CAP_SPI);
  else {
   memcpy(nt->frames[nt->cap_frames], nt->rx, NT_FRAME_SIZE);
   nt_capture_check_frame(nt);
  }
  /* Preserve the completed raw frame even when cancellation arrived in SPI. */
  if (nt_capture_invalid(nt))
   nt_capture_stop(nt, NT_CAP_PANEL);
  if (READ_ONCE(nt->cap_timeout) || nt_now_ms(nt) - nt->cap_start_ms >= NT_CAPTURE_MS)
   nt_capture_stop(nt, NT_CAP_TIMEOUT);
 }
 stopped = nt->cap_stop;
 mutex_unlock(&nt->cap_lock);
 if (stopped)
  complete(&nt->cap_completion);
 return IRQ_HANDLED;
}

#ifndef THP_HOST_TEST
static int nt_request_capture_irq(struct nt_thp *nt)
{
 return request_threaded_irq(nt->irq, NULL, nt_capture_irq, IRQF_ONESHOT,
                             "nt36523-thp-capture", nt);
}
static void nt_disable_capture_irq(struct nt_thp *nt) { disable_irq(nt->irq); }
static void nt_free_capture_irq(struct nt_thp *nt) { free_irq(nt->irq, nt); }
static bool nt_wait_capture(struct nt_thp *nt)
{
 u64 elapsed = nt_now_ms(nt) - nt->cap_start_ms;

 if (elapsed >= NT_CAPTURE_MS)
  return false;
 return wait_for_completion_timeout(&nt->cap_completion,
                                    msecs_to_jiffies(NT_CAPTURE_MS - elapsed)) != 0;
}
#endif

static ssize_t nt_capture(struct nt_thp *nt, const char *buf, size_t count)
{
 bool completed;
 int ret = 0;

 if ((count != 7 && count != 8) || memcmp(buf, "THP-CAP", 7) ||
     (count == 8 && buf[7] != '\n'))
  return -EINVAL;
 if(nt_auto_active(nt))return -EBUSY;
 mutex_lock(&nt->lock);
 mutex_lock(&nt->panel_lock);
 if(nt_auto_active(nt))ret=-EBUSY;
 else if (nt->state != NT_DONE)
  ret = -EPERM;
 else if (nt->dead || READ_ONCE(nt->quiesce))
  ret = -ESHUTDOWN;
 else if (nt->cap_used)
  ret = -EBUSY;
 else if (!nt->panel_on)
  ret = -EAGAIN;
 else if (nt->gen != nt->download_gen)
  ret = -ECANCELED;
 else if (nt->irq <= 0)
  ret = -ENODEV;
 mutex_unlock(&nt->panel_lock);
 if (!ret && nt_stream_busy(nt->stream))
  ret = -EBUSY;
 if (ret)
  goto unlock;
 nt->cap_used = true;
 mutex_lock(&nt->cap_lock);
 nt->cap_state = NT_CAP_RUNNING;
 nt->cap_start_ms = nt_now_ms(nt);
 mutex_unlock(&nt->cap_lock);
 /* completion is initialized before an IRQ can run (also initialized at probe). */
 reinit_completion(&nt->cap_completion);
 ret = nt_request_capture_irq(nt);
 if (ret) {
  mutex_lock(&nt->cap_lock);
  nt_capture_stop(nt, NT_CAP_REQUEST);
  nt->cap_state = NT_CAP_ABORTED;
  nt->cap_elapsed_ms = nt_now_ms(nt) - nt->cap_start_ms;
  mutex_unlock(&nt->cap_lock);
  goto unlock;
 }
 completed = nt_wait_capture(nt);
 if (!completed)
  WRITE_ONCE(nt->cap_timeout, true);
 mutex_lock(&nt->cap_lock);
 /* Recheck even after the eighth frame completed: a panel event may arrive
  * between IRQ completion and owner cleanup without any subsequent IRQ.
  */
 if (nt_capture_invalid(nt))
  nt_capture_stop(nt, NT_CAP_PANEL);
 if (!completed)
  nt_capture_stop(nt, NT_CAP_TIMEOUT);
 if (!nt->cap_stop)
  nt_capture_stop(nt, NT_CAP_PANEL);
 mutex_unlock(&nt->cap_lock);
 /* Never hold cap_lock/panel_lock while draining the IRQ thread. */
 nt_disable_capture_irq(nt);
 nt_free_capture_irq(nt);
 mutex_lock(&nt->cap_lock);
 nt->cap_freed = true;
 nt->cap_elapsed_ms = nt_now_ms(nt) - nt->cap_start_ms;
 nt->cap_state = nt->cap_reason == NT_CAP_LIMIT ? NT_CAP_DONE : NT_CAP_ABORTED;
 ret = nt->cap_state == NT_CAP_DONE ? 0 : -ECANCELED;
 mutex_unlock(&nt->cap_lock);
unlock:
 mutex_unlock(&nt->lock);
 return ret ? ret : (ssize_t)count;
}

static ssize_t nt_frames_read(struct nt_thp *nt, char *buf, loff_t off, size_t count)
{
 size_t available;

 if (off < 0)
  return -EINVAL;
 mutex_lock(&nt->cap_lock);
 available = (size_t)nt->cap_frames * NT_FRAME_SIZE;
 if ((u64)off >= available)
  count = 0;
 else {
  available -= off;
  if (count > available)
   count = available;
  memcpy(buf, (u8 *)nt->frames + off, count);
 }
 mutex_unlock(&nt->cap_lock);
 return count;
}

static const char *nt_cap_state_name(struct nt_thp *nt)
{
 static const char * const names[] = { "idle", "running", "done", "aborted" };
 return names[nt->cap_state];
}
static const char *nt_cap_reason_name(struct nt_thp *nt)
{
 static const char * const names[] = { "none", "frame_limit", "timeout", "panel",
  "irq_limit", "spi_error", "actual_length", "checksum", "counter_repeat",
  "counter_backward", "all_zero", "all_ff", "irq_request" };
 return names[nt->cap_reason];
}

#include "tap-core.h"
#include "input-core.h"
#include "stream-core.h"
#include "auto-core.h"
#include "input-control.h"

#ifndef THP_HOST_TEST
static ssize_t capture_store(struct device *dev, struct device_attribute *attr,
                             const char *buf, size_t count)
{
 return nt_capture(dev_get_drvdata(dev), buf, count);
}
static ssize_t frames_read(struct file *file, struct kobject *kobj,
                          const struct bin_attribute *attr, char *buf,
                          loff_t off, size_t count)
{
 return nt_frames_read(dev_get_drvdata(kobj_to_dev(kobj)), buf, off, count);
}
static struct bin_attribute nt_frames_attr = {
 .attr = { .name = "frames", .mode = 0400 },
 .size = NT_CAPTURE_FRAMES * NT_FRAME_SIZE, .read = frames_read,
};
static const struct bin_attribute * const nt_bin_attrs[] = { &nt_frames_attr, &nt_tap_attr, NULL };

static ssize_t capture_status_show(struct device *dev, struct device_attribute *attr,
                                  char *buf)
{
 struct nt_thp *nt = dev_get_drvdata(dev);
 ssize_t at;
 unsigned int i;

 mutex_lock(&nt->cap_lock);
 at = sysfs_emit(buf, "state=%s reason=%s frames=%u irqs=%u elapsed_ms=%llu free_irq=%u\n",
                 nt_cap_state_name(nt), nt_cap_reason_name(nt), nt->cap_frames,
                 nt->cap_irqs, nt->cap_state == NT_CAP_RUNNING ?
                 nt_now_ms(nt) - nt->cap_start_ms : nt->cap_elapsed_ms, nt->cap_freed);
 for (i = 0; i < nt->cap_frames; i++)
  at += sysfs_emit_at(buf, at, "frame%u counter=%u checksum=%u pen_checksum=%u\n", i,
                     nt->cap_info[i].counter, nt->cap_info[i].checksum, nt->cap_info[i].pen_checksum);
 mutex_unlock(&nt->cap_lock);
 return at;
}

static ssize_t arm_store(struct device *dev, struct device_attribute *attr,
                         const char *buf, size_t count)
{
 return nt_arm(dev_get_drvdata(dev), buf, count);
}

static ssize_t download_store(struct device *dev, struct device_attribute *attr,
			      const char *buf, size_t count)
{
	return nt_download(dev_get_drvdata(dev), buf, count);
}

static ssize_t status_show(struct device *dev, struct device_attribute *attr,
			   char *buf)
{
	struct nt_thp *nt = dev_get_drvdata(dev);
	ssize_t at;
	unsigned int i;

	mutex_lock(&nt->lock);
	mutex_lock(&nt->panel_lock);
	at = sysfs_emit(buf, "state=%s steps=%u reads=%u error=%d abort_error=%d used=%u quiesce=%u panel_on=%u armed=%u dead=%u gen=%lu\n",
			nt_state_name(nt), nt->steps, nt->reads, nt->last_error, nt->error,
			nt->used, READ_ONCE(nt->quiesce), nt->panel_on, nt->armed, nt->dead, nt->gen);
	mutex_unlock(&nt->panel_lock);
	for (i = 0; i < NT_READ_SLOTS; i++) {
		const struct nt_step *step = &nt_plan[nt_read_indices[i]];

		if (nt->value_valid[i])
			at += sysfs_emit_at(buf, at, "%s=%*phN\n", step->id,
					   step->length, nt->values[i]);
		else
			at += sysfs_emit_at(buf, at, "%s=unavailable\n", step->id);
	}
	mutex_unlock(&nt->lock);
	return at;
}

static DEVICE_ATTR(managed, 0600, managed_show, managed_store);
static DEVICE_ATTR(native, 0200, NULL, native_store);
static DEVICE_ATTR(tap, 0200, NULL, tap_store);
static DEVICE_ATTR(tap_status, 0444, tap_status_show, NULL);
static DEVICE_ATTR(native_status, 0444, native_status_show, NULL);
static DEVICE_ATTR(map, 0600, map_show, map_store);
static DEVICE_ATTR(stream, 0200, NULL, stream_store);
static DEVICE_ATTR(stream_status, 0444, stream_status_show, NULL);
static DEVICE_ATTR(capture, 0200, NULL, capture_store);
static DEVICE_ATTR(capture_status, 0444, capture_status_show, NULL);
static DEVICE_ATTR(arm, 0200, NULL, arm_store);
static DEVICE_ATTR(download, 0200, NULL, download_store);
static DEVICE_ATTR(status, 0444, status_show, NULL);
static struct attribute *nt_attrs[] = {
	&dev_attr_tap.attr, &dev_attr_tap_status.attr, &dev_attr_managed.attr, &dev_attr_native.attr, &dev_attr_native_status.attr, &dev_attr_map.attr, &dev_attr_stream.attr, &dev_attr_stream_status.attr, &dev_attr_capture.attr, &dev_attr_capture_status.attr, &dev_attr_arm.attr, &dev_attr_download.attr, &dev_attr_status.attr, NULL,
};
static const struct attribute_group nt_group = { .attrs = nt_attrs, .bin_attrs = nt_bin_attrs };

static bool auto_mode;
static unsigned int boot_wait_ms=60000;
module_param_named(managed,auto_mode,bool,0444);
module_param(boot_wait_ms,uint,0444);
MODULE_PARM_DESC(managed,"Panel-managed THP lifecycle (default off)");
MODULE_PARM_DESC(boot_wait_ms,"Post-download scan wait, 1000..120000 ms (default 60000)");

static int nt_probe(struct spi_device *spi)
{
	struct nt_thp *nt;
	int ret;

 if(settle_ms<100 || settle_ms>5000){
  dev_err(&spi->dev,"invalid settle_ms=%u (100..5000)\n",settle_ms);return -EINVAL;
 }
 if(boot_wait_ms<1000 || boot_wait_ms>120000){
  dev_err(&spi->dev,"invalid boot_wait_ms=%u (1000..120000)\n",boot_wait_ms);return -EINVAL;
 }
	if (!drm_is_panel_follower(&spi->dev))
		return -ENODEV;
	/* No setup, trim read, page selection or other transaction in probe. */
	if (spi->mode != SPI_MODE_0 || spi->bits_per_word != 8 ||
	    spi->max_speed_hz != 16000000)
		return -EINVAL;
	nt = devm_kzalloc(&spi->dev, sizeof(*nt), GFP_KERNEL);
	if (!nt)
		return -ENOMEM;
	nt->tx = devm_kmalloc(&spi->dev, NT_MAX_PAYLOAD + 2, GFP_KERNEL);
	nt->rx = devm_kmalloc(&spi->dev, NT_MAX_PAYLOAD + 2, GFP_KERNEL);
	if (!nt->tx || !nt->rx)
		return -ENOMEM;
	nt->spi = spi;
	nt->current_step = -1;
	mutex_init(&nt->lock);
	mutex_init(&nt->panel_lock);
	mutex_init(&nt->cap_lock);
	init_completion(&nt->cap_completion);
	nt->irq = spi->irq;
	spi_set_drvdata(spi, nt);
 ret = nt_stream_alloc(nt);
 if (ret)return ret;
 ret = nt_auto_init(nt,auto_mode,settle_ms,boot_wait_ms);
 if (ret){nt_stream_detach(nt->stream);nt_stream_put(nt->stream);return ret;}
 ret = nt_follower_add(nt,&spi->dev);
 if (ret){nt_auto_destroy(nt);nt_stream_detach(nt->stream);nt_stream_put(nt->stream);return ret;}
 /* Devres is LIFO: remove sysfs (and drain its callbacks), then drop the
  * binding reference, then free nt/tx/rx. Open files retain stream state.
  */
 ret = devm_add_action(&spi->dev, nt_stream_binding_put, nt->stream);
 if (ret) {
  nt_auto_destroy(nt);
  nt_stream_detach(nt->stream);
  nt_follower_remove(nt);
  nt_stream_put(nt->stream);
  return ret;
 }
 ret = devm_device_add_group(&spi->dev, &nt_group);
 if (ret) {
  nt_auto_destroy(nt);
  nt_stream_detach(nt->stream);
  nt_follower_remove(nt);
 }
 return ret;
}

static void nt_remove(struct spi_device *spi)
{
 struct nt_thp *nt = spi_get_drvdata(spi);

 nt_auto_destroy(nt);
 WRITE_ONCE(nt->quiesce, true);
 nt_stream_detach(nt->stream);
 nt_follower_remove(nt);
 /* Binding reference is released after devres has drained sysfs. */
}

static const struct of_device_id nt_of_match[] = {
	{ .compatible = "novatek,nt36523-thp" }, { }
};
MODULE_DEVICE_TABLE(of, nt_of_match);
static const struct spi_device_id nt_ids[] = { { "nt36523-thp", 0 }, { } };
MODULE_DEVICE_TABLE(spi, nt_ids);
static struct spi_driver nt_driver = {
	.driver = { .name = "nt36523-thp", .of_match_table = nt_of_match },
	.probe = nt_probe, .remove = nt_remove, .id_table = nt_ids,
};
module_spi_driver(nt_driver);
MODULE_FIRMWARE("novatek/nt36523-thp.bin");
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Fixed-table once-only NT36523 THP SRAM downloader");
#endif
