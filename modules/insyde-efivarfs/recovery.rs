// SPDX-License-Identifier: GPL-2.0-only
//! Restore the efivarfs view after an Insyde enumeration failure.

use kernel::error::to_result;
use kernel::prelude::*;
mod model;
use model::{Entries, Fault, Firmware, Key, Variable};

module! {
    type: Recovery,
    name: "insyde_efivarfs",
    authors: ["okhsunrog"],
    description: "Restore Insyde EFI enumeration without writing firmware variables",
    license: "GPL",
    imports_ns: ["EFIVAR"],
    params: {
        expose: u32 {
            default: 0,
            description: "0: diagnose only; 1: restore in-memory efivarfs entries",
        },
    },
}

unsafe extern "C" {
    fn ief_supported() -> i32;
    fn ief_lock() -> i32;
    fn ief_unlock();
    fn ief_next(bytes: *mut usize, name: *mut u16, guid: *mut u8) -> usize;
    fn ief_metadata(name: *const u16, guid: *const u8, attrs: *mut u32, size: *mut usize) -> usize;
    fn ief_seed_metadata(attrs: *mut u32, size: *mut usize) -> usize;
    fn ief_check_mount() -> i32;
    fn ief_expose(name: *const u16, guid: *const u8, size: usize) -> i32;
}

const EFI_ERROR: usize = 1 << (usize::BITS - 1);
const EFI_NOT_FOUND: usize = EFI_ERROR | 14;
const EFI_BUFFER_TOO_SMALL: usize = EFI_ERROR | 5;
const GLOBAL_GUID: [u8; 16] = [
    0x61, 0xdf, 0xe4, 0x8b, 0xca, 0x93, 0xd2, 0x11, 0xaa, 0x0d, 0x00, 0xe0, 0x98, 0x03, 0x2b, 0x8c,
];
const IMAGE_GUID: [u8; 16] = [
    0xcb, 0xb2, 0x19, 0xd7, 0x3a, 0x3d, 0x96, 0x45, 0xa3, 0xbc, 0xda, 0xd0, 0x0e, 0x67, 0x65, 0x6f,
];
const LOADER_GUID: [u8; 16] = [
    0x82, 0xb0, 0x67, 0x4a, 0x4c, 0x0a, 0xcf, 0x41, 0xb6, 0xc7, 0x44, 0x0b, 0x29, 0xbb, 0x8c, 0x4f,
];

struct LockedFirmware {
    missing_attributes: usize,
}
impl LockedFirmware {
    fn acquire() -> Result<Self> {
        // SAFETY: no pointers; EFI's global lock is acquired once and released by Drop.
        to_result(unsafe { ief_lock() })?;
        Ok(Self {
            missing_attributes: 0,
        })
    }
}
impl Drop for LockedFirmware {
    fn drop(&mut self) {
        // SAFETY: this value owns the lock acquired in acquire().
        unsafe { ief_unlock() };
    }
}
impl Firmware for LockedFirmware {
    fn next(&mut self, key: &mut Key) -> core::result::Result<Option<usize>, Fault> {
        let mut bytes = model::NAME_UNITS * 2;
        // SAFETY: unique writable name/GUID buffers of the specified size; EFI lock held.
        let status = unsafe { ief_next(&mut bytes, key.name.as_mut_ptr(), key.guid.as_mut_ptr()) };
        match status {
            0 => Ok(Some(bytes)),
            EFI_NOT_FOUND => Ok(None),
            _ => {
                pr_err!("GetNextVariableName failed: {:#x}\n", status);
                Err(Fault::Firmware)
            }
        }
    }
    fn metadata(&mut self, key: &Key) -> core::result::Result<(Option<u32>, usize), Fault> {
        // A reserved bit identifies an untouched output parameter. Do not
        // mistake missing metadata for actual zero attributes or invent bits.
        const UNSET: u32 = 0x80000000;
        let mut attrs = UNSET;
        let mut size = 0;
        let is_seed = key == &Key::ascii(b"PlatformLang", GLOBAL_GUID);
        // SAFETY: seed wrapper reads only the fixed public language variable;
        // all other names use zero-capacity size queries. EFI lock is held.
        let status = unsafe {
            if is_seed {
                ief_seed_metadata(&mut attrs, &mut size)
            } else {
                ief_metadata(key.name.as_ptr(), key.guid.as_ptr(), &mut attrs, &mut size)
            }
        };
        if (is_seed && status != 0) || (!is_seed && status != EFI_BUFFER_TOO_SMALL && status != 0) {
            pr_err!("GetVariable metadata failed: {:#x}\n", status);
            return Err(Fault::Firmware);
        }
        let attrs = if attrs == UNSET {
            self.missing_attributes += 1;
            None
        } else {
            Some(attrs)
        };
        Ok((attrs, size))
    }
}

struct Store(KVec<Variable>);
impl Store {
    fn new() -> Self {
        Self(KVec::new())
    }
}
impl Entries for Store {
    fn contains(&self, key: &Key) -> bool {
        self.0.iter().any(|v| &v.key == key)
    }
    fn push(&mut self, variable: Variable) -> core::result::Result<(), Fault> {
        self.0.push(variable, GFP_KERNEL).map_err(|_| Fault::Memory)
    }
}

fn checked<T>(result: core::result::Result<T, Fault>) -> Result<T> {
    result.map_err(|fault| {
        pr_err!("Scan rejected: {:?}; no entries exposed\n", fault);
        if fault == Fault::Memory {
            ENOMEM
        } else {
            EIO
        }
    })
}

struct Recovery;
impl kernel::Module for Recovery {
    fn init(_module: &'static ThisModule) -> Result<Self> {
        let expose = *module_parameters::expose.value();
        if expose > 1 {
            return Err(EINVAL);
        }
        // SAFETY: no pointers; checks exact DMI platform and EFI availability.
        if unsafe { ief_supported() } == 0 {
            return Err(ENODEV);
        }
        let seed = Key::ascii(b"PlatformLang", GLOBAL_GUID);
        let mut normal = Store::new();
        let mut recovered = Store::new();
        let mut fw = LockedFirmware::acquire()?;
        let normal_count = checked(model::walk(&mut fw, &mut normal, None))?;
        let seed_seen = normal.contains(&seed);
        pr_info!(
            "empty-start count={} seed_seen={} expose={}\n",
            normal_count,
            seed_seen,
            expose
        );
        if normal_count > 32 || seed_seen {
            pr_info!("Enumeration is healthy; no action\n");
            return Ok(Self);
        }
        let seeded_count = checked(model::walk(&mut fw, &mut recovered, Some(seed)))?;
        pr_info!("seeded count={}\n", seeded_count);
        if !model::recovery_allowed(normal_count, seeded_count, seed_seen) {
            return Err(ENODEV);
        }

        // Known variables may precede the seed or be in another store. Probe names only,
        // never their contents. Required keyring/boot variables must remain readable.
        for (name, guid) in [
            (b"SecureBoot".as_slice(), GLOBAL_GUID),
            (b"SetupMode".as_slice(), GLOBAL_GUID),
            (b"PK".as_slice(), GLOBAL_GUID),
            (b"KEK".as_slice(), GLOBAL_GUID),
            (b"db".as_slice(), IMAGE_GUID),
            (b"dbx".as_slice(), IMAGE_GUID),
            (b"BootOrder".as_slice(), GLOBAL_GUID),
            (b"BootCurrent".as_slice(), GLOBAL_GUID),
        ] {
            let key = Key::ascii(name, guid);
            if !recovered.contains(&key) {
                let variable = checked(model::exact(&mut fw, key))?;
                checked(recovered.push(variable))?;
            }
        }
        for name in [
            b"StubPcrKernelImage".as_slice(),
            b"StubInfo".as_slice(),
            b"LoaderFirmwareInfo".as_slice(),
            b"LoaderFirmwareType".as_slice(),
            b"LoaderTpm2ActivePcrBanks".as_slice(),
        ] {
            let key = Key::ascii(name, LOADER_GUID);
            if !recovered.contains(&key) {
                if let Ok(variable) = model::exact(&mut fw, key) {
                    checked(recovered.push(variable))?;
                }
            }
        }
        // Release EFI lock before VFS operations; no firmware writes at either stage.
        pr_info!(
            "metadata queries without attributes={}\n",
            fw.missing_attributes
        );
        drop(fw);
        if expose == 0 {
            pr_info!(
                "Dry run passed: {} accessible variables, no entries created\n",
                recovered.0.len()
            );
            return Ok(Self);
        }
        // SAFETY: checks fixed efivarfs path and writable mount before creating inodes.
        to_result(unsafe { ief_check_mount() })?;
        let mut created = 0;
        let mut present = 0;
        let mut skipped = 0;
        let mut failed = 0;
        for variable in &recovered.0 {
            if variable.attributes.is_some_and(|a| a & model::RUNTIME == 0) {
                return Err(EIO);
            }
            if !variable.key.path_safe() {
                skipped += 1;
                continue;
            }
            // SAFETY: bounded terminated path-safe name, 16-byte GUID, validated data size.
            let result = unsafe {
                ief_expose(
                    variable.key.name.as_ptr(),
                    variable.key.guid.as_ptr(),
                    variable.size,
                )
            };
            match result {
                1 => created += 1,
                0 => present += 1,
                _ => {
                    failed += 1;
                    pr_err!("efivarfs inode creation failed: {}\n", result);
                }
            }
        }
        pr_info!(
            "Recovery complete: created={} present={} skipped={} failed={}\n",
            created,
            present,
            skipped,
            failed
        );
        if failed != 0 {
            return Err(EIO);
        }
        Ok(Self)
    }
}
