// SPDX-License-Identifier: GPL-2.0-only
/* Minimal EFI/VFS glue. No SetVariable, writes, unlink, or firmware patching. */
#include <linux/dmi.h>
#include <linux/efi.h>
#include <linux/err.h>
#include <linux/file.h>
#include <linux/fs.h>
#include <linux/magic.h>
#include <linux/namei.h>
#include <linux/slab.h>
#include <linux/sizes.h>
#include "bridge.h"

int ief_supported(void)
{
	return dmi_match(DMI_SYS_VENDOR, "Framework") &&
		dmi_match(DMI_BIOS_VENDOR, "INSYDE Corp.") &&
		dmi_match(DMI_PRODUCT_NAME, "Laptop 13 (Intel Core Ultra Series 1)") &&
		efivar_is_available();
}

int ief_lock(void)
{
	return efivar_lock();
}

void ief_unlock(void)
{
	efivar_unlock();
}

unsigned long ief_next(unsigned long *bytes, u16 *name, u8 *guid_bytes)
{
	efi_guid_t guid;
	efi_status_t status;

	memcpy(&guid, guid_bytes, sizeof(guid));
	status = efivar_get_next_variable(bytes, name, &guid);
	memcpy(guid_bytes, &guid, sizeof(guid));
	return status;
}

unsigned long ief_metadata(const u16 *name, const u8 *guid_bytes,
			   u32 *attributes, unsigned long *size)
{
	efi_guid_t guid;

	memcpy(&guid, guid_bytes, sizeof(guid));
	return efivar_get_variable((u16 *)name, &guid, attributes, size, NULL);
}

unsigned long ief_seed_metadata(u32 *attributes, unsigned long *size)
{
	/* Only this public language setting is read. Never read arbitrary payloads
	 * to work around firmware which leaves Attributes unset on a size query. */
	u16 name[] = { 'P', 'l', 'a', 't', 'f', 'o', 'r', 'm', 'L', 'a', 'n', 'g', 0 };
	efi_guid_t guid = EFI_GLOBAL_VARIABLE_GUID;
	u8 language[64];
	efi_status_t status;

	*size = sizeof(language);
	status = efivar_get_variable(name, &guid, attributes, size, language);
	memzero_explicit(language, sizeof(language));
	return status;
}

int ief_check_mount(void)
{
	struct path path;
	int error = kern_path("/sys/firmware/efi/efivars", LOOKUP_FOLLOW, &path);

	if (error)
		return error;
	if (path.dentry->d_sb->s_magic != EFIVARFS_MAGIC)
		error = -EINVAL;
	else if (sb_rdonly(path.dentry->d_sb))
		error = -EROFS;
	path_put(&path);
	return error;
}

int ief_expose(const u16 *name, const u8 *guid_bytes, unsigned long size)
{
	char path[sizeof("/sys/firmware/efi/efivars/") + 255 + 1 + 36];
	char ascii[256];
	efi_guid_t guid;
	struct file *file;
	struct inode *inode;
	bool created = false;
	int index, error;

	/* Rust validates names; repeat the path boundary check here. */
	for (index = 0; index < 256 && name[index]; index++) {
		if (name[index] < 0x20 || name[index] > 0x7e || name[index] == '/')
			return -EILSEQ;
		ascii[index] = name[index];
	}
	if (!index || index == 256 || size > SZ_4M)
		return -EINVAL;
	ascii[index] = '\0';
	memcpy(&guid, guid_bytes, sizeof(guid));
	snprintf(path, sizeof(path), "/sys/firmware/efi/efivars/%s-%pUl", ascii, &guid);
	file = filp_open(path, O_RDONLY | O_CREAT | O_EXCL, 0644);
	if (IS_ERR(file)) {
		error = PTR_ERR(file);
		if (error != -EEXIST)
			return error;
		file = filp_open(path, O_RDONLY, 0);
		if (IS_ERR(file))
			return PTR_ERR(file);
	} else {
		created = true;
	}
	inode = file_inode(file);
	if (inode->i_sb->s_magic != EFIVARFS_MAGIC) {
		filp_close(file, NULL);
		return -EINVAL;
	}
	/* Keep newly created in-memory entries after close. Never call file write. */
	inode_lock(inode);
	if (created || i_size_read(inode) == 0)
		i_size_write(inode, size + sizeof(u32));
	inode_unlock(inode);
	filp_close(file, NULL);
	return created ? 1 : 0;
}
