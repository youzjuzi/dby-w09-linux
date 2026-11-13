/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef RT8555_LEVEL_H
#define RT8555_LEVEL_H

static inline unsigned int rt8555_adjust_level(unsigned int level)
{
	if (level && !(level & 0x0f))
		level++;
	if ((level & 0xff) == 0xff)
		level--;

	return level;
}

#endif
