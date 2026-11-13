/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef NT_THP_AUTO_HOST_API_H
#define NT_THP_AUTO_HOST_API_H
/* Host-only ordered queue: one delayed_work, a real pthread, virtual ms time.
 * The controller owns queue lifetime and prevents new callers before destroy.
 * No controller lock is acquired by this adapter or across callback dispatch.
 */
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <pthread.h>

#define WQ_MEM_RECLAIM 0x0008U
#define WQ_FREEZABLE 0x0004U
#define WQ_UNBOUND 0x0002U
struct work_struct { void (*fn)(struct work_struct *); };
struct workqueue_struct;
struct delayed_work {
 struct work_struct work;
 struct workqueue_struct *queue; /* atomic publication; lifetime by controller */
};
struct workqueue_struct {
 pthread_mutex_t lock;
 pthread_cond_t changed;
 pthread_t thread;
 struct delayed_work *delayed;
 uint64_t now, deadline;
 unsigned int cancellers;
 bool pending, running, stopping;
};
static unsigned int host_wq_threads __attribute__((unused));

static inline struct delayed_work *to_delayed_work(struct work_struct *work)
{
 return (struct delayed_work *)((char *)work - offsetof(struct delayed_work, work));
}

static inline void host_init_delayed_work(struct delayed_work *delayed,
                                          void (*fn)(struct work_struct *))
{
 delayed->work.fn = fn;
 __atomic_store_n(&delayed->queue, NULL, __ATOMIC_RELEASE);
}
#define INIT_DELAYED_WORK(delayed, fn) host_init_delayed_work((delayed), (fn))

static inline void *host_wq_worker(void *data)
{
 struct workqueue_struct *queue = data;
 pthread_mutex_lock(&queue->lock);
 for (;;) {
  struct work_struct *work;
  while (!queue->stopping && (!queue->pending || queue->deadline > queue->now))
   pthread_cond_wait(&queue->changed, &queue->lock);
  if (queue->stopping)
   break;
  work = &queue->delayed->work;
  queue->pending = false;
  queue->running = true;
  pthread_cond_broadcast(&queue->changed);
  pthread_mutex_unlock(&queue->lock);
  work->fn(work);
  pthread_mutex_lock(&queue->lock);
  queue->running = false;
  pthread_cond_broadcast(&queue->changed);
 }
 pthread_mutex_unlock(&queue->lock);
 __atomic_sub_fetch(&host_wq_threads, 1U, __ATOMIC_ACQ_REL);
 return NULL;
}

static inline struct workqueue_struct *host_alloc_ordered_workqueue(const char *name,
                                                                   unsigned int flags)
{
 struct workqueue_struct *queue = calloc(1, sizeof(*queue));
 (void)name;
 (void)flags;
 if (!queue)
  return NULL;
 if (pthread_mutex_init(&queue->lock, NULL)) {
  free(queue);
  return NULL;
 }
 if (pthread_cond_init(&queue->changed, NULL)) {
  pthread_mutex_destroy(&queue->lock);
  free(queue);
  return NULL;
 }
 __atomic_add_fetch(&host_wq_threads, 1U, __ATOMIC_ACQ_REL);
 if (pthread_create(&queue->thread, NULL, host_wq_worker, queue)) {
  __atomic_sub_fetch(&host_wq_threads, 1U, __ATOMIC_ACQ_REL);
  pthread_cond_destroy(&queue->changed);
  pthread_mutex_destroy(&queue->lock);
  free(queue);
  return NULL;
 }
 return queue;
}
#define alloc_ordered_workqueue(name, flags, ...) host_alloc_ordered_workqueue((name), (flags))

static inline bool host_queue_delayed_work(struct workqueue_struct *queue,
                                          struct delayed_work *delayed,
                                          uint64_t delay, bool modify)
{
 bool was_pending, accepted;
 struct workqueue_struct *bound;
 pthread_mutex_lock(&queue->lock);
 bound = __atomic_load_n(&delayed->queue, __ATOMIC_ACQUIRE);
 assert(!bound || bound == queue);
 assert(!queue->delayed || queue->delayed == delayed);
 was_pending = queue->pending;
 accepted = !queue->stopping && !queue->cancellers && (modify || !was_pending);
 if (accepted) {
  queue->delayed = delayed;
  __atomic_store_n(&delayed->queue, queue, __ATOMIC_RELEASE);
  queue->deadline = delay > UINT64_MAX - queue->now ? UINT64_MAX : queue->now + delay;
  queue->pending = true;
  pthread_cond_broadcast(&queue->changed);
 }
 pthread_mutex_unlock(&queue->lock);
 return modify ? was_pending : accepted;
}
static inline bool mod_delayed_work(struct workqueue_struct *queue,
                                    struct delayed_work *delayed, uint64_t delay)
{
 return host_queue_delayed_work(queue, delayed, delay, true);
}
static inline bool queue_delayed_work(struct workqueue_struct *queue,
                                      struct delayed_work *delayed, uint64_t delay)
{
 return host_queue_delayed_work(queue, delayed, delay, false);
}
static inline bool cancel_delayed_work_sync(struct delayed_work *delayed)
{
 struct workqueue_struct *queue = __atomic_load_n(&delayed->queue, __ATOMIC_ACQUIRE);
 bool was_pending;
 if (!queue)
  return false;
 pthread_mutex_lock(&queue->lock);
 /* Calling sync cancellation from the callback itself would self-deadlock. */
 assert(!pthread_equal(pthread_self(), queue->thread));
 was_pending = queue->pending;
 queue->cancellers++;
 queue->pending = false;
 pthread_cond_broadcast(&queue->changed);
 while (queue->running)
  pthread_cond_wait(&queue->changed, &queue->lock);
 queue->pending = false;
 queue->cancellers--;
 pthread_cond_broadcast(&queue->changed);
 pthread_mutex_unlock(&queue->lock);
 return was_pending;
}
static inline void host_wq_advance(struct workqueue_struct *queue, uint64_t now)
{
 pthread_mutex_lock(&queue->lock);
 assert(now >= queue->now);
 queue->now = now;
 pthread_cond_broadcast(&queue->changed);
 pthread_mutex_unlock(&queue->lock);
}
static inline void host_wq_drain_due(struct workqueue_struct *queue)
{
 pthread_mutex_lock(&queue->lock);
 assert(!pthread_equal(pthread_self(), queue->thread));
 while (queue->running || (queue->pending && queue->deadline <= queue->now))
  pthread_cond_wait(&queue->changed, &queue->lock);
 pthread_mutex_unlock(&queue->lock);
}
static inline bool host_wq_idle(struct workqueue_struct *queue)
{
 bool idle;
 pthread_mutex_lock(&queue->lock);
 idle = !queue->pending && !queue->running;
 pthread_mutex_unlock(&queue->lock);
 return idle;
}
static inline bool host_wq_running(struct workqueue_struct *queue)
{
 bool running;
 pthread_mutex_lock(&queue->lock);
 running = queue->running;
 pthread_mutex_unlock(&queue->lock);
 return running;
}
static inline void destroy_workqueue(struct workqueue_struct *queue)
{
 if (!queue)
  return;
 assert(!pthread_equal(pthread_self(), queue->thread));
 pthread_mutex_lock(&queue->lock);
 queue->stopping = true;
 queue->pending = false;
 pthread_cond_broadcast(&queue->changed);
 pthread_mutex_unlock(&queue->lock);
 pthread_join(queue->thread, NULL);
 if (queue->delayed)
  __atomic_store_n(&queue->delayed->queue, NULL, __ATOMIC_RELEASE);
 pthread_cond_destroy(&queue->changed);
 pthread_mutex_destroy(&queue->lock);
 free(queue);
}
#endif
