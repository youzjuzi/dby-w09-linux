/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef NT_THP_PARAM_CORE_H
#define NT_THP_PARAM_CORE_H
/* Parameter core serializes setters/getters. Queue takes one atomic snapshot;
 * no device lock is taken by a setter and no queued deadline is modified. */
static unsigned int settle_ms=700;
static bool skip_probe;
static unsigned int edge_px;
static unsigned int filter=1;
static unsigned int confirm=1;
static unsigned int multi;
static unsigned int multi_generation;
static unsigned int guard=1;
static int __attribute__((unused)) nt_set_guard(const char *val,const struct kernel_param *kp)
{
 unsigned int next;struct kernel_param local=*kp;int ret;
 local.arg=&next;ret=param_set_uint_minmax(val,&local,0,1);
 if(ret)return -EINVAL;
 WRITE_ONCE(*(unsigned int *)kp->arg,next);
 WRITE_ONCE(multi_generation,READ_ONCE(multi_generation)+1U);return 0;
}
static int __attribute__((unused)) nt_set_multi(const char *val,const struct kernel_param *kp)
{
 unsigned int next;struct kernel_param local=*kp;int ret;
 local.arg=&next;ret=param_set_uint_minmax(val,&local,0,1);
 if(ret)return -EINVAL;
 WRITE_ONCE(*(unsigned int *)kp->arg,next);
 WRITE_ONCE(multi_generation,READ_ONCE(multi_generation)+1U);return 0;
}
/* Generation changes on every successful parameter write, even 1->0->1
 * between frames. Each stream resets its private states at the next boundary. */
static unsigned int confirm_generation;
static int __attribute__((unused)) nt_set_confirm(const char *val,const struct kernel_param *kp)
{
 unsigned int next;struct kernel_param local=*kp;int ret;
 local.arg=&next;ret=param_set_uint_minmax(val,&local,0,1);
 if(ret)return -EINVAL;
 WRITE_ONCE(*(unsigned int *)kp->arg,next);
 WRITE_ONCE(confirm_generation,READ_ONCE(confirm_generation)+1U);return 0;
}

static unsigned int lock_px=20;
static int __attribute__((unused)) nt_set_filter(const char *val,const struct kernel_param *kp)
{
 unsigned int next;struct kernel_param local=*kp;int ret;
 local.arg=&next;ret=param_set_uint_minmax(val,&local,0,1);
 if(ret)return -EINVAL;
 WRITE_ONCE(*(unsigned int *)kp->arg,next);return 0;
}
static int __attribute__((unused)) nt_set_lock_px(const char *val,const struct kernel_param *kp)
{
 unsigned int next;struct kernel_param local=*kp;int ret;
 local.arg=&next;ret=param_set_uint_minmax(val,&local,0,100);
 if(ret)return -EINVAL;
 WRITE_ONCE(*(unsigned int *)kp->arg,next);return 0;
}

static int __attribute__((unused)) nt_set_edge(const char *val,const struct kernel_param *kp)
{
 unsigned int next;struct kernel_param local=*kp;int ret;
 local.arg=&next;ret=param_set_uint_minmax(val,&local,0,200);
 if(ret)return -EINVAL;
 WRITE_ONCE(*(unsigned int *)kp->arg,next);return 0;
}
static int __attribute__((unused)) nt_set_settle(const char *val,const struct kernel_param *kp)
{
 unsigned int next;
 struct kernel_param local=*kp;
 int ret;
 local.arg=&next;
 ret=param_set_uint_minmax(val,&local,100,5000);
 if(ret)return -EINVAL;
 WRITE_ONCE(*(unsigned int *)kp->arg,next);
 return 0;
}
#ifndef THP_HOST_TEST
static const struct kernel_param_ops nt_guard_ops={.set=nt_set_guard,.get=param_get_uint};
module_param_cb(guard,&nt_guard_ops,&guard,0644);
__MODULE_PARM_TYPE(guard,"uint");
MODULE_PARM_DESC(guard,"Multi-touch palm transient guard, 0..1 (default 1)");
static const struct kernel_param_ops nt_multi_ops={.set=nt_set_multi,.get=param_get_uint};
module_param_cb(multi,&nt_multi_ops,&multi,0644);
__MODULE_PARM_TYPE(multi,"uint");
MODULE_PARM_DESC(multi,"Multi-touch reporting, 0..1 (default 0)");
static const struct kernel_param_ops nt_confirm_ops={.set=nt_set_confirm,.get=param_get_uint};
module_param_cb(confirm,&nt_confirm_ops,&confirm,0644);
__MODULE_PARM_TYPE(confirm,"uint");
MODULE_PARM_DESC(confirm,"Touch confirmation, 0..1 (default 1)");
static const struct kernel_param_ops nt_filter_ops={.set=nt_set_filter,.get=param_get_uint};
module_param_cb(filter,&nt_filter_ops,&filter,0644);
__MODULE_PARM_TYPE(filter,"uint");
MODULE_PARM_DESC(filter,"Single-touch coordinate filter, 0..1 (default 1)");
static const struct kernel_param_ops nt_lock_ops={.set=nt_set_lock_px,.get=param_get_uint};
module_param_cb(lock_px,&nt_lock_ops,&lock_px,0644);
__MODULE_PARM_TYPE(lock_px,"uint");
MODULE_PARM_DESC(lock_px,"Position lock radius, 0..100 pixels (0 disables lock)");
static const struct kernel_param_ops nt_edge_ops={.set=nt_set_edge,.get=param_get_uint};
module_param_cb(edge_px,&nt_edge_ops,&edge_px,0644);
__MODULE_PARM_TYPE(edge_px,"uint");
MODULE_PARM_DESC(edge_px,"Edge extrapolation width, 0..200 pixels (0 disables)");
static const struct kernel_param_ops nt_settle_ops={.set=nt_set_settle,.get=param_get_uint};
module_param_cb(settle_ms,&nt_settle_ops,&settle_ms,0644);
__MODULE_PARM_TYPE(settle_ms,"uint");
module_param(skip_probe,bool,0644);
MODULE_PARM_DESC(settle_ms,"Panel stable delay, 100..5000 ms, next queued wake");
MODULE_PARM_DESC(skip_probe,"Skip preprobe after unpreparing (default off)");
#endif
#endif
