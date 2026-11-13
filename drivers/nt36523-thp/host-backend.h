/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef NT_THP_HOST_BACKEND_H
#define NT_THP_HOST_BACKEND_H
#include <stdbool.h>
#include <limits.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
#include <pthread.h>
#include <sys/types.h>
#include <openssl/sha.h>
#include "stream-host-api.h"
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
#include "input-host-api.h"
typedef int irqreturn_t;
#define IRQ_HANDLED 1
#define IRQF_ONESHOT 0x2000
struct completion { unsigned int done; };

typedef int16_t s16;
#define SHA256_DIGEST_SIZE 32
#define READ_ONCE(v) __atomic_load_n(&(v), __ATOMIC_ACQUIRE)
#define WRITE_ONCE(v, x) __atomic_store_n(&(v), (x), __ATOMIC_RELEASE)
#include "param-host-api.h"
/* completion.c: counted tokens, UINT_MAX saturation; a wait consumes one. */
static void init_completion(struct completion *c) { WRITE_ONCE(c->done, 0U); }
static void reinit_completion(struct completion *c) { WRITE_ONCE(c->done, 0U); }
static void complete(struct completion *c) {
 unsigned int n = READ_ONCE(c->done);
 while (n != UINT_MAX && !__atomic_compare_exchange_n(&c->done, &n, n + 1,
        false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {}
}
static inline bool completion_done(struct completion *c) { return READ_ONCE(c->done) > 0; }
static inline void wait_for_completion(struct completion *c) {
 unsigned int n;
 for (;;) {
  n = READ_ONCE(c->done);
  if (n == UINT_MAX) return;
  if (n && __atomic_compare_exchange_n(&c->done, &n, n - 1, false,
                                     __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) return;
  sched_yield();
 }
}
struct mutex { pthread_mutex_t raw; };
static void nt_host_lock_hook(struct mutex *m, bool acquired);
static inline void mutex_init(struct mutex *m) { pthread_mutex_init(&m->raw, NULL); }
static inline void mutex_lock(struct mutex *m) { nt_host_lock_hook(m, false); pthread_mutex_lock(&m->raw); nt_host_lock_hook(m, true); }
static inline bool mutex_trylock(struct mutex *m) { return pthread_mutex_trylock(&m->raw) == 0; }
static inline void mutex_unlock(struct mutex *m) { pthread_mutex_unlock(&m->raw); }
static inline void sha256(const u8 *data, size_t len, u8 *digest) { SHA256(data, len, digest); }
struct firmware { size_t size; const u8 *data; };
#define container_of(ptr, type, member) ((type *)((char *)(ptr) - offsetof(type, member)))
struct device { bool follows, prepared, enabled; int add_error; unsigned int registrations; };
struct drm_panel_follower;
struct drm_panel_follower_funcs {
 int (*panel_prepared)(struct drm_panel_follower *);
 int (*panel_enabled)(struct drm_panel_follower *);
 int (*panel_disabling)(struct drm_panel_follower *);
 int (*panel_unpreparing)(struct drm_panel_follower *);
};
struct drm_panel_follower { const struct drm_panel_follower_funcs *funcs; struct device *panel; };
static bool drm_is_panel_follower(struct device *dev) { return dev->follows; }
static int drm_panel_add_follower(struct device *dev, struct drm_panel_follower *f);
static void drm_panel_remove_follower(struct drm_panel_follower *f);
#endif
