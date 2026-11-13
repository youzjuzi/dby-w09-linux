/* SPDX-License-Identifier: GPL-2.0-only */
/* Spin locking emulated by pthread for host races; production uses spinlock_t. */
typedef pthread_mutex_t spinlock_t;
#define spin_lock_init(p) pthread_mutex_init(p,NULL)
#define spin_lock_irqsave(p,f) do {(f)=0;pthread_mutex_lock(p);} while(0)
#define spin_unlock_irqrestore(p,f) do {(void)(f);pthread_mutex_unlock(p);} while(0)
static inline void *vzalloc(size_t n){return kvzalloc(n,GFP_KERNEL);}
static inline void vfree(void *p){kvfree(p);}

struct ta_result;
static void (*host_tap_result)(const struct ta_result *);
