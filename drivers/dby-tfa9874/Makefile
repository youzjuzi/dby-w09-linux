# SPDX-License-Identifier: GPL-2.0-only
# Required owner input; see README.md, Preparing configuration tables.
ifeq ($(filter /%,$(TFA_TABLES_DIR)),)
$(error Set TFA_TABLES_DIR to an absolute path; see drivers/dby-tfa9874/README.md)
endif
ifeq ($(wildcard $(TFA_TABLES_DIR)/dby-tfa9874-tables-private.h),)
$(error Missing private TFA tables; see drivers/dby-tfa9874/README.md)
endif
ifneq ($(wildcard $(src)/dby-tfa9874-tables-private.h),)
$(error Keep private TFA tables outside the module source directory; see README.md)
endif
ccflags-y += -I$(TFA_TABLES_DIR)
obj-m += dby-tfa9874.o
