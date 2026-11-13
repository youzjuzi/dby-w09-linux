/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef DBY_TFA9874_CORE_H
#define DBY_TFA9874_CORE_H
#ifdef __KERNEL__
#include <linux/types.h>
#include <linux/errno.h>
typedef u16 uint16_t;
typedef u8 uint8_t;
#else
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <errno.h>
#endif
#define DBY_TFA9874_REG_REV 0x03
#define DBY_TFA9874_REV_ID 0x0c74
#define DBY_TFA_INIT_COUNT 44
#define DBY_TFA_ON_COUNT 11
#define DBY_TFA_OFF_COUNT 6
enum dby_tfa9874_op_type {
 DBY_TFA_OP_REG_WRITE = 0, DBY_TFA_OP_DELAY_MS = 2,
 DBY_TFA_OP_REG_RXORW = 4, DBY_TFA_OP_REG_READ = 5
};
struct dby_tfa9874_op {
 uint16_t reg, mask, val, delay_ms;
 uint8_t type;
};
static const uint16_t dby_tfa_slot4[] = {0x0030, 0x1031, 0x2032, 0x3023};
static const uint16_t dby_tfa_slot2[] = {0x0030, 0x1031, 0x2030, 0x3021};
static const uint16_t dby_tfa_reg27[] = {1, 0, 3, 2};
/* Generated owner data is required; see README.md for preparation. */
#if !__has_include("dby-tfa9874-tables-private.h")
#error "Missing private TFA tables: generate them as described in drivers/dby-tfa9874/README.md"
#else
#include "dby-tfa9874-tables-private.h"
#endif
static inline int dby_tfa9874_build_init(unsigned int chip,
 struct dby_tfa9874_op *out, size_t capacity)
{
 unsigned int i;
 if (chip > 3 || capacity < DBY_TFA_INIT_COUNT || !out)
  return -EINVAL;
 for (i = 0; i < DBY_TFA_INIT_COUNT; i++) {
  out[i] = dby_tfa_init[i];
  if (out[i].reg == 0x26) out[i].val = dby_tfa_slot4[chip];
  if (out[i].reg == 0x27) out[i].val = dby_tfa_reg27[chip];
 }
 return DBY_TFA_INIT_COUNT;
}
static inline int dby_tfa9874_build_power_on_seq(unsigned int chip,
 unsigned int channels, unsigned int step, bool input_atten,
 bool safe_enable, unsigned int active_mask,
 struct dby_tfa9874_op *out, size_t capacity)
{
 unsigned int i;
 if (!out || chip > 3 || channels < 1 || channels > 4 ||
     step < 9 || step > 15 || active_mask > 15)
  return -EINVAL;
 if (!safe_enable || !(active_mask & (1U << chip))) {
  if (capacity < DBY_TFA_OFF_COUNT) return -EINVAL;
  for (i = 0; i < DBY_TFA_OFF_COUNT; i++) out[i] = dby_tfa_off[i];
  return DBY_TFA_OFF_COUNT;
 }
 if (capacity < DBY_TFA_ON_COUNT) return -EINVAL;
 {
  struct dby_tfa9874_op seq[DBY_TFA_ON_COUNT] =
   DBY_TFA_ON_INITIALIZER(
    input_atten ? 0x22d8 : 0x22c8,
    channels <= 2 ? dby_tfa_slot2[chip] : dby_tfa_slot4[chip],
    (step << 6) | DBY_TFA_GAIN_LOW_BITS);
  for (i = 0; i < DBY_TFA_ON_COUNT; i++) out[i] = seq[i];
 }
 return DBY_TFA_ON_COUNT;
}
#endif
