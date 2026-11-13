/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef NT_PARAM_HOST_API_H
#define NT_PARAM_HOST_API_H
#include <stdlib.h>
#include <stdarg.h>
typedef int64_t s64;
struct kernel_param {void *arg;};
static int kstrtouint(const char *text,unsigned int base,unsigned int *out)
{
 char *end;unsigned long value;
 if(!text || !*text || *text=='-' || *text==' ' || *text=='\t')return -EINVAL;
 errno=0;value=strtoul(text,&end,(int)base);
 if(errno || value>UINT_MAX || end==text)return -EINVAL;
 if(*end=='\n')end++;
 if(*end)return -EINVAL;
 *out=(unsigned int)value;return 0;
}
int param_set_uint_minmax(const char *val, const struct kernel_param *kp,
		unsigned int min, unsigned int max)
{
	unsigned int num;
	int ret;

	if (!val)
		return -EINVAL;
	ret = kstrtouint(val, 0, &num);
	if (ret)
		return ret;
	if (num < min || num > max)
		return -EINVAL;
	*((unsigned int *)kp->arg) = num;
	return 0;
}

/* Only formatter dependency is emulated; production format string is shared. */
static int sysfs_emit_at(char *buf,int at,const char *fmt,...)
{va_list ap;int n;va_start(ap,fmt);n=vsnprintf(buf+at,4096-(size_t)at,fmt,ap);va_end(ap);return n;}
#endif
