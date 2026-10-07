/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef INSYDE_EFIVARFS_BRIDGE_H
#define INSYDE_EFIVARFS_BRIDGE_H
#include <linux/types.h>

int ief_supported(void);
int ief_lock(void);
void ief_unlock(void);
unsigned long ief_next(unsigned long *bytes, u16 *name, u8 *guid_bytes);
unsigned long ief_metadata(const u16 *name, const u8 *guid_bytes,
			   u32 *attributes, unsigned long *size);
unsigned long ief_seed_metadata(u32 *attributes, unsigned long *size);
int ief_check_mount(void);
int ief_expose(const u16 *name, const u8 *guid_bytes, unsigned long size);
#endif
