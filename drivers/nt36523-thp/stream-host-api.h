/* SPDX-License-Identifier: GPL-2.0-only */
/* Host adapters for the same file operations, wait queues and kthread owner. */
#ifndef NT_STREAM_HOST_API_H
#define NT_STREAM_HOST_API_H
#include <fcntl.h>
#include <poll.h>
#include <stdlib.h>
#include <time.h>
#include <sched.h>
#include <stdio.h>
#define __user
#define THIS_MODULE NULL
#define GFP_KERNEL 0
#define MISC_DYNAMIC_MINOR 255
#define EPOLLIN POLLIN
#define EPOLLRDNORM POLLRDNORM
#define EPOLLHUP POLLHUP
#define IS_ERR(p) ((uintptr_t)(p) > (uintptr_t)-4096)
#define PTR_ERR(p) ((long)(p))
typedef unsigned int __poll_t;
typedef struct { unsigned int registrations; } poll_table;
typedef struct { pthread_mutex_t lock; pthread_cond_t cond; unsigned int waits; } wait_queue_head_t;
struct file { void *private_data; int f_flags; };
struct inode { int unused; };
struct file_operations {
 void *owner;
 int (*open)(struct inode *, struct file *);
 ssize_t (*read)(struct file *, char *, size_t, loff_t *);
 __poll_t (*poll)(struct file *, poll_table *);
 int (*release)(struct inode *, struct file *);
};
struct miscdevice { int minor; const char *name; unsigned int mode; const struct file_operations *fops; };
struct task_struct { pthread_t thread; int (*fn)(void *); void *data; bool stop; };
static unsigned int host_allocations, host_tasks;
static int host_misc_error, host_task_error;
static inline void *kvmalloc(size_t n, int flags) {
 (void)flags; void *p = malloc(n); if (p) __atomic_add_fetch(&host_allocations, 1, __ATOMIC_RELAXED); return p;
}
static inline void *kvzalloc(size_t n, int flags) { void *p = kvmalloc(n, flags); if (p) memset(p, 0, n); return p; }
static inline void kvfree(void *p) { if (p) { free(p); __atomic_sub_fetch(&host_allocations, 1, __ATOMIC_RELAXED); } }
static inline int misc_register(struct miscdevice *m) { (void)m; return host_misc_error; }
static inline void misc_deregister(struct miscdevice *m) { (void)m; }
static inline int nonseekable_open(struct inode *i, struct file *f) { (void)i; (void)f; return 0; }
static bool host_copy_fault;
static inline unsigned long copy_to_user(void *to, const void *from, size_t n) { if (host_copy_fault) return n; memcpy(to, from, n); return 0; }
static inline void init_waitqueue_head(wait_queue_head_t *q) { pthread_mutex_init(&q->lock, NULL); pthread_cond_init(&q->cond, NULL); q->waits = 0; }
static inline void host_wake(wait_queue_head_t *q) { pthread_mutex_lock(&q->lock); pthread_cond_broadcast(&q->cond); pthread_mutex_unlock(&q->lock); }
#define wake_up_all(q) host_wake(q)
static inline void poll_wait(struct file *f, wait_queue_head_t *q, poll_table *p) { (void)f; (void)q; if (p) p->registrations++; }
static inline unsigned long msecs_to_jiffies(unsigned int ms) { return ms; }
/* A short REAL timeout permits deterministic virtual-clock steps without a
 * timer thread. Only the driver owner's 100 ms policy check advances policy.
 */
#define HOST_WAIT(q, condition, timed) ({ \
 int host_wait_ret = 0; \
 pthread_mutex_lock(&(q).lock); \
 (q).waits++; \
 while (!(condition)) { \
  if (timed) { \
   struct timespec host_until; clock_gettime(CLOCK_REALTIME, &host_until); \
   host_until.tv_nsec += 1000000; \
   if (host_until.tv_nsec >= 1000000000) { host_until.tv_sec++; host_until.tv_nsec -= 1000000000; } \
   if (pthread_cond_timedwait(&(q).cond, &(q).lock, &host_until) == ETIMEDOUT) break; \
  } else pthread_cond_wait(&(q).cond, &(q).lock); \
 } \
 pthread_mutex_unlock(&(q).lock); host_wait_ret; })
#define wait_event_timeout(q, condition, timeout) ((void)(timeout), HOST_WAIT(q, condition, true))
#define wait_event_interruptible(q, condition) HOST_WAIT(q, condition, false)
#define wait_event(q, condition) do { while (!(condition)) HOST_WAIT(q, condition, true); } while (0)
static _Thread_local struct task_struct *host_current;
static bool host_owner_hold;
static inline bool kthread_should_stop(void) { return __atomic_load_n(&host_current->stop, __ATOMIC_ACQUIRE); }
static void *host_task_run(void *arg) {
 struct task_struct *t = arg; host_current = t;
 while (__atomic_load_n(&host_owner_hold, __ATOMIC_ACQUIRE)) sched_yield();
 t->fn(t->data); return NULL;
}
static inline int kthread_stop(struct task_struct *t) {
 __atomic_store_n(&t->stop, true, __ATOMIC_RELEASE);
 pthread_join(t->thread, NULL); free(t);
 __atomic_sub_fetch(&host_tasks, 1, __ATOMIC_RELAXED); return 0;
}
static inline struct task_struct *kthread_create(int (*fn)(void *), void *data, const char *name) {
 (void)name; if (host_task_error) return (void *)(intptr_t)host_task_error;
 struct task_struct *t = malloc(sizeof(*t)); if (!t) return (void *)(intptr_t)-ENOMEM;
 t->fn = fn; t->data = data; t->stop = false; return t;
}
static inline int wake_up_process(struct task_struct *t) {
 __atomic_add_fetch(&host_tasks, 1, __ATOMIC_RELAXED);
 if (pthread_create(&t->thread, NULL, host_task_run, t)) abort();
 return 0;
}
#endif
