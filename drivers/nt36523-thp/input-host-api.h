/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef INPUT_HOST_API_H
#define INPUT_HOST_API_H
#include <stdlib.h>
#include <time.h>
#include <stdio.h>
typedef int64_t s64;
typedef int32_t s32;
#define BUS_SPI 28
#define INPUT_MT_DIRECT 2
#define MT_TOOL_FINGER 0
#define ABS_MT_POSITION_X 53
#define ABS_MT_POSITION_Y 54
#define ABS_X 0
#define ABS_Y 1
#define ABS_MT_PRESSURE 58
#define ABS_PRESSURE 24
struct input_absinfo {int value,minimum,maximum,fuzz,flat,resolution;};
struct host_touch_node {unsigned int mask,values[8];bool invert_x,invert_y,swap_x_y;};
static struct host_touch_node host_touch_node;
struct input_mt_slot {int abs[64];};
struct input_mt {struct input_mt_slot slots[5];};
static inline int input_mt_get_value(const struct input_mt_slot *s,unsigned int a){return s->abs[a];}
struct input_dev {
 struct input_mt mt_storage,*mt;
 const char *name; struct { unsigned short bustype; } id;
 struct { void *parent; } dev;
 struct input_absinfo storage[64],*absinfo;
 unsigned long absbit[1];
 unsigned int phase;
 bool registered,active,slot_on[5]; int selected,nslots,x,y,tracking,next_tracking;
 unsigned int calls[6],registered_count,unregistered_count;
};
static FILE *host_input_calls;
static void host_input_call(const char *name,int value)
{ if(host_input_calls)fprintf(host_input_calls,"%s %d\n",name,value); }
static unsigned int host_input_devices;
static int host_last_tracking;
static void (*host_event_hook)(struct input_dev *);
static struct input_dev *devm_input_allocate_device(void *parent)
{
 struct input_dev *d=calloc(1,sizeof(*d));(void)parent;
 if(d){host_input_call("allocate",0);host_input_devices++;d->tracking=-1;d->absinfo=d->storage;d->mt=&d->mt_storage;}return d;
}
static int input_mt_init_slots(struct input_dev *d,unsigned int n,unsigned int flags)
{ if(n<1||n>5||flags!=INPUT_MT_DIRECT)return -EINVAL;
 d->nslots=n;for(unsigned int i=0;i<n;i++)d->mt->slots[i].abs[57]=-1;
 d->absinfo[ABS_X]=d->absinfo[ABS_MT_POSITION_X];d->absinfo[ABS_X].fuzz=0;
 d->absinfo[ABS_Y]=d->absinfo[ABS_MT_POSITION_Y];d->absinfo[ABS_Y].fuzz=0;return 0; }
static void input_set_abs_params(struct input_dev *d,int a,int lo,int hi,int fuzz,int flat)
{d->absbit[0]|=1UL<<a;d->absinfo[a]=(struct input_absinfo){.minimum=lo,.maximum=hi,.fuzz=fuzz,.flat=flat};}
static void input_abs_set_res(struct input_dev *d,int a,int v){d->absinfo[a].resolution=v;}
static int input_register_device(struct input_dev *d){host_input_call("register",0);d->registered=true;d->registered_count++;return 0;}
static void input_unregister_device(struct input_dev *d){if(d->active)abort();host_input_call("unregister",0);d->registered=false;d->unregistered_count++;}
static void input_mt_slot(struct input_dev *d,int slot){if((d->phase!=0&&d->phase!=2&&d->phase!=4)||slot<0||slot>=d->nslots)abort();d->selected=slot;d->phase=1;host_input_call("slot",slot);d->calls[0]++;}
static bool input_mt_report_slot_state(struct input_dev *d,unsigned int tool,bool active)
{if(d->phase!=1||tool)abort();d->phase=2;host_input_call("slot_state",active);d->calls[1]++;
 if(active&&!d->slot_on[d->selected])d->mt->slots[d->selected].abs[57]=++d->next_tracking;
 if(!active)d->mt->slots[d->selected].abs[57]=-1;
 d->slot_on[d->selected]=active;d->tracking=d->mt->slots[d->selected].abs[57];
 d->active=false;for(int i=0;i<d->nslots;i++)if(d->slot_on[i])d->active=true;return active;}
static void input_report_abs(struct input_dev *d,unsigned int axis,int value)
{if((d->phase==2&&axis!=ABS_MT_POSITION_X)||(d->phase==3&&axis!=ABS_MT_POSITION_Y)||d->phase<2||d->phase>3)abort();d->mt->slots[d->selected].abs[axis]=value;d->phase++;host_input_call(axis==ABS_MT_POSITION_X?"abs_x":"abs_y",value);d->calls[2]++;if(axis==ABS_MT_POSITION_X)d->x=value;if(axis==ABS_MT_POSITION_Y)d->y=value;}
static void input_mt_sync_frame(struct input_dev *d){if(d->phase!=4U&&d->phase!=2U)abort();d->phase=5;host_input_call("mt_sync_frame",0);d->calls[3]++;}
static void input_sync(struct input_dev *d){if(d->phase!=5)abort();d->phase=0;host_input_call("sync",0);d->calls[4]++;if(host_event_hook)host_event_hook(d);}
static u64 host_time_reads;
static u64 ktime_get_ns(void){__atomic_add_fetch(&host_time_reads,1,__ATOMIC_RELAXED);struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (u64)t.tv_sec*1000000000+t.tv_nsec;}
/* Only the standard helper's dependencies are emulated, not its code. */
struct device;
struct input_mt_pos {int x,y;};
struct touchscreen_properties {unsigned int max_x,max_y;bool invert_x,invert_y,swap_x_y;};
static inline int device_property_read_u32(struct device *dev,const char *name,u32 *out)
{
 static const char *const keys[]={"touchscreen-min-x","touchscreen-size-x","touchscreen-fuzz-x","touchscreen-min-y","touchscreen-size-y","touchscreen-fuzz-y","touchscreen-max-pressure","touchscreen-fuzz-pressure"};
 (void)dev;
 for(unsigned int i=0;i<8;i++)if(!strcmp(name,keys[i]) && (host_touch_node.mask&(1U<<i))){*out=host_touch_node.values[i];return 0;}
 return -EINVAL;
}
static inline bool device_property_read_bool(struct device *dev,const char *name)
{
 (void)dev;
 if(!strcmp(name,"touchscreen-inverted-x"))return host_touch_node.invert_x;
 if(!strcmp(name,"touchscreen-inverted-y"))return host_touch_node.invert_y;
 if(!strcmp(name,"touchscreen-swapped-x-y"))return host_touch_node.swap_x_y;
 return false;
}
static inline int input_abs_get_val(struct input_dev *d,unsigned int a){return d->absinfo[a].value;}
static inline int input_abs_get_min(struct input_dev *d,unsigned int a){return d->absinfo[a].minimum;}
static inline int input_abs_get_max(struct input_dev *d,unsigned int a){return d->absinfo[a].maximum;}
static inline int input_abs_get_fuzz(struct input_dev *d,unsigned int a){return d->absinfo[a].fuzz;}
static inline void input_alloc_absinfo(struct input_dev *d){(void)d;}
static inline bool test_bit(unsigned long a,const unsigned long *bits){return !!(bits[0]&(1UL<<a));}
#define dev_warn(...) ((void)0)
#define swap(a,b) do { __typeof__(a) temp_swap=(a);(a)=(b);(b)=temp_swap;} while(0)
#include "touchscreen-host-api.h"
#endif
