/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef DBY_FG_CORE_H
#define DBY_FG_CORE_H

#ifdef DBY_FG_HOST_TEST
#include <errno.h>
#include <stdint.h>
typedef uint8_t u8;
typedef int64_t s64;
#else
#include <linux/errno.h>
#include <linux/types.h>
#endif

/* Only these byte ranges may reach the parent's uncached regmap. */
static inline int dby_fg_range(unsigned int reg, unsigned int count)
{
	switch (reg) {
	case 0x4010:
	case 0x4046:
		return count == 1;
	case 0x4009:
	case 0x41a0:
	case 0x41a2:
	case 0x41a6:
	case 0x41a8:
		return count == 2;
	default:
		return 0;
	}
}

static inline int dby_fg_revision(unsigned int type, unsigned int subtype,
				  unsigned int major, unsigned int minor,
				  unsigned int rev2)
{
	return type == 0x51 && subtype == 0x20 && (major == 1 || major == 2) &&
	       minor == 0 && rev2 == 0;
}

static inline int dby_fg_soc(u8 a, u8 b, int *value)
{
	if (a != b)
		return -EAGAIN;
	if (a == 0 || a == 255)
		*value = a ? 100 : 0;
	else
		*value = ((a - 1) * 98 + 126) / 253 + 1;
	return 0;
}

static inline int dby_fg_vi(const u8 *a, const u8 *b, int is_current, int *value)
{
	int raw = a[0] | (a[1] << 8);

	if (a[0] != b[0] || a[1] != b[1])
		return -EAGAIN;
	if (is_current && (raw & 0x8000))
		raw -= 0x10000;
	/* FG is discharge-positive; invert in s64 before narrowing, never -int. */
	*value = (s64)raw * (is_current ? -488281 : 122070) / 1000;
	return 0;
}

typedef int (*dby_fg_reader)(void *context, unsigned int reg, u8 *buf,
			     unsigned int count);

/* item: 0 capacity, 1 voltage, 2 current. No retries or sample caching. */
static inline int dby_fg_sample(dby_fg_reader read, void *context,
				int item, int *value)
{
	u8 ready, enabled, a[2], b[2];
	unsigned int reg;
	int ret;

	if (item < 0 || item > 2)
		return -EINVAL;
	ret = read(context, 0x4010, &ready, 1);
	if (ret)
		return ret;
	ret = read(context, 0x4046, &enabled, 1);
	if (ret)
		return ret;
	if (!(ready & 2) || !(enabled & 128))
		return -ENODATA;
	reg = item == 0 ? 0x4009 : item == 1 ? 0x41a0 : 0x41a2;
	ret = read(context, reg, a, 2);
	if (ret)
		return ret;
	if (!item)
		return dby_fg_soc(a[0], a[1], value);
	ret = read(context, reg + 6, b, 2);
	if (ret)
		return ret;
	return dby_fg_vi(a, b, item == 2, value);
}
#endif
