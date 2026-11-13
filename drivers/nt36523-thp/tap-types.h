/* SPDX-License-Identifier: GPL-2.0-only */
#define NT_TAP_CAPACITY 2048U
#define NT_TAP_RECORD 3296U
#define NT_TAP_READERS 8U
struct nt_tap_cursor {
 const void *key;
 u64 epoch, first, limit, next;
};
struct nt_tap {
 spinlock_t lock;
 struct mutex control;
 bool active;
 unsigned int state, count;
 u8 *data;
 u64 total, dropped, rejected, epoch;
 unsigned int reader_next;
#ifdef THP_HOST_TEST
 u64 host_copies,host_bytes;
#endif
 struct nt_tap_cursor readers[NT_TAP_READERS];
};
