/* SPDX-License-Identifier: GPL-2.0-only */
/* Bounded RFC 2/3 parser with elish NT36523 cascade-header handling. */
#ifndef NT36523_FORMAT_H
#define NT36523_FORMAT_H
#ifdef NT36523_HOST_TEST
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <errno.h>
typedef uint8_t u8;
typedef uint32_t u32;
#else
#include <linux/types.h>
#include <linux/errno.h>
#endif

#define NT_PART_MAX 64
#define NT_SRAM_LIMIT 0x2fe00U
struct nt_part {
	u32 bin;
	u32 ram;
	u32 size;
	u32 crc;
};
struct nt_image {
	struct nt_part part[NT_PART_MAX];
	u32 count;
	bool cascade;
};
static inline u32 nt_le32(const u8 *p)
{
	return (u32)p[0] | (u32)p[1] << 8 | (u32)p[2] << 16 | (u32)p[3] << 24;
}
struct nt_trim {
 u8 id[6];
 u8 mask[6];
};
static const struct nt_trim nt36523_trim_table[] = {
 { { 0x20, 0xff, 0xff, 0x23, 0x65, 0x03 }, { 1, 0, 0, 1, 1, 1 } },
 { { 0x0c, 0xff, 0xff, 0x23, 0x65, 0x03 }, { 1, 0, 0, 1, 1, 1 } },
 { { 0x0b, 0xff, 0xff, 0x23, 0x65, 0x03 }, { 1, 0, 0, 1, 1, 1 } },
 { { 0x0a, 0xff, 0xff, 0x23, 0x65, 0x03 }, { 1, 0, 0, 1, 1, 1 } },
 { { 0xff, 0xff, 0xff, 0x23, 0x65, 0x03 }, { 0, 0, 0, 1, 1, 1 } },
};
static inline bool nt_trim_matches(const u8 *id)
{
 unsigned int i, j;

 for (i = 0; i < sizeof(nt36523_trim_table) / sizeof(nt36523_trim_table[0]); i++) {
  for (j = 0; j < 6; j++)
   if (nt36523_trim_table[i].mask[j] && id[j] != nt36523_trim_table[i].id[j])
    break;
  if (j == 6)
   return true;
 }
 return false;
}
static inline bool nt_ram_range(u32 addr, u32 len)
{
	return len && addr < NT_SRAM_LIMIT && len <= NT_SRAM_LIMIT - addr;
}
static inline bool nt_control_allowed(u32 addr, const u8 *v, size_t len)
{
	switch (addr) {
	case 0x3f0fe:
		return len == 1 && (v[0] == 0xaa || v[0] == 0x69);
	case 0x2fe60:
		return len == 1 && v[0] == 0;
	case 0x3f10d:
		return len == 1 && v[0] == 1;
	case 0x2fe50:
		return len == 1 && v[0] == 0xae;
	case 0x3f7e8:
		return len == 1 && v[0] == 0x69;
	case 0x3f30e:
		return len == 1 && (v[0] & 0x80);
	case 0x3f128:
	case 0x3f12c:
	case 0x3f118:
	case 0x3f130:
		return len == 3;
	case 0x3f100:
	case 0x3f104:
		return len == 4;
	default:
		return false;
	}
}
static inline int nt_parse(const u8 *b, size_t len, struct nt_image *im)
{
	u32 end, stop, info, overlay, i, pos;
	size_t tail;

	if (len < 4096 || len > 512 * 1024 || len % 4096)
		return -EINVAL;
	for (tail = len; tail; tail -= 4096)
		if ((b[tail - 3] == 'N' && b[tail - 2] == 'V' && b[tail - 1] == 'T') ||
		    (b[tail - 3] == 'M' && b[tail - 2] == 'O' && b[tail - 1] == 'D'))
			break;
	if (!tail || (unsigned int)b[tail - 4096] + b[tail - 4095] != 255)
		return -EINVAL;
	end = nt_le32(b);
	im->cascade = !!(b[0x20] & 2);
	stop = im->cascade ? end / 2 : end;
	if (end > tail || stop < 0x30 || stop > 0x400 ||
	    (stop - 0x30) % 16 || (im->cascade && end % 2))
		return -EINVAL;
	info = (stop - 0x30) / 16 + im->cascade;
	overlay = b[0x28] & 0x10 ? b[0x28] & 15 : 0;
	im->count = 2 + info + overlay;
	if (im->count > NT_PART_MAX)
		return -EINVAL;
	for (i = 0; i < im->count; i++) {
		struct nt_part *p = &im->part[i];

		if (i < 2) {
			pos = i * 12;
			p->bin = nt_le32(b + pos);
			p->ram = nt_le32(b + pos + 4);
			p->size = nt_le32(b + pos + 8);
			p->crc = nt_le32(b + 0x18 + i * 4);
		} else {
			if (i < 2 + info)
				pos = im->cascade && i == 1 + info ? end - 16 :
				      0x30 + (i - 2) * 16;
			else {
				if (im->part[1].bin > tail ||
				    (i - 2 - info) * 16 > tail - im->part[1].bin)
					return -EINVAL;
				pos = im->part[1].bin + (i - 2 - info) * 16;
			}
			if (pos > tail || tail - pos < 16)
				return -EINVAL;
			p->ram = nt_le32(b + pos);
			p->size = nt_le32(b + pos + 4);
			p->bin = nt_le32(b + pos + 8);
			p->crc = nt_le32(b + pos + 12);
		}
		if (!p->size) {
			if (i < 2)
				return -EINVAL;
			continue;
		}
		/* Vendor size is last-byte index; validate the extra byte too. */
		if (p->size >= tail || p->bin >= tail ||
		    p->size + 1 > tail - p->bin ||
		    !nt_ram_range(p->ram, p->size + 1))
			return -EINVAL;
	}
	return 0;
}
#endif
