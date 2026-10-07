// SPDX-License-Identifier: GPL-2.0-only
//! Bounded firmware enumeration; independent of Linux for mock-firmware tests.

pub(crate) const NAME_UNITS: usize = 256;
pub(crate) const LIMIT: usize = 4096;
pub(crate) const RUNTIME: u32 = 4;
pub(crate) const MAX_DATA: usize = 4 * 1024 * 1024;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Fault {
    Firmware,
    Name,
    Metadata,
    Duplicate,
    Limit,
    Memory,
}

#[derive(Clone, PartialEq, Eq)]
pub(crate) struct Key {
    pub(crate) name: [u16; NAME_UNITS],
    pub(crate) guid: [u8; 16],
}

impl Key {
    pub(crate) fn empty() -> Self {
        Self {
            name: [0; NAME_UNITS],
            guid: [0; 16],
        }
    }

    pub(crate) fn ascii(name: &[u8], guid: [u8; 16]) -> Self {
        assert!(!name.is_empty() && name.len() < NAME_UNITS);
        let mut key = Self::empty();
        for (dst, src) in key.name.iter_mut().zip(name) {
            *dst = u16::from(*src);
        }
        key.guid = guid;
        key
    }

    pub(crate) fn validate(&mut self, bytes: usize) -> core::result::Result<(), Fault> {
        if bytes < 4 || bytes > NAME_UNITS * 2 || bytes % 2 != 0 {
            return Err(Fault::Name);
        }
        let len = self.name[..bytes / 2]
            .iter()
            .position(|&c| c == 0)
            .ok_or(Fault::Name)?;
        if len == 0 {
            return Err(Fault::Name);
        }
        // Canonicalise unused buffer words before comparison or storing a key.
        self.name[len..].fill(0);
        Ok(())
    }

    pub(crate) fn path_safe(&self) -> bool {
        self.name
            .iter()
            .take_while(|&&c| c != 0)
            .all(|&c| (0x20..=0x7e).contains(&c) && c != u16::from(b'/'))
    }
}

#[derive(Clone)]
pub(crate) struct Variable {
    pub(crate) key: Key,
    // None means this firmware did not fill Attributes on BUFFER_TOO_SMALL.
    pub(crate) attributes: Option<u32>,
    pub(crate) size: usize,
}

pub(crate) trait Firmware {
    fn next(&mut self, key: &mut Key) -> core::result::Result<Option<usize>, Fault>;
    // A positive result must come from runtime GetVariable SUCCESS/BUFFER_TOO_SMALL:
    // it proves accessibility. No variable contents may be returned by this trait.
    fn metadata(&mut self, key: &Key) -> core::result::Result<(Option<u32>, usize), Fault>;
}

pub(crate) trait Entries {
    fn contains(&self, key: &Key) -> bool;
    fn push(&mut self, variable: Variable) -> core::result::Result<(), Fault>;
}

pub(crate) fn exact(fw: &mut impl Firmware, key: Key) -> core::result::Result<Variable, Fault> {
    let (attributes, size) = fw.metadata(&key)?;
    if attributes.is_some_and(|a| a & RUNTIME == 0) || size == 0 || size > MAX_DATA {
        return Err(Fault::Metadata);
    }
    Ok(Variable {
        key,
        attributes,
        size,
    })
}

pub(crate) fn walk(
    fw: &mut impl Firmware,
    entries: &mut impl Entries,
    seed: Option<Key>,
) -> core::result::Result<usize, Fault> {
    let mut key = seed.clone().unwrap_or_else(Key::empty);
    let mut count = 0;
    if let Some(seed) = seed {
        let variable = exact(fw, seed)?;
        // Unlike other runtime-accessible records, the entry-point seed must
        // have an independently returned RUNTIME bit, not an inferred one.
        if !variable.attributes.is_some_and(|a| a & RUNTIME != 0) {
            return Err(Fault::Metadata);
        }
        entries.push(variable)?;
        count = 1;
    }
    for _ in 0..LIMIT {
        let Some(bytes) = fw.next(&mut key)? else {
            return Ok(count);
        };
        key.validate(bytes)?;
        if entries.contains(&key) {
            return Err(Fault::Duplicate);
        }
        entries.push(exact(fw, key.clone())?)?;
        count += 1;
    }
    Err(Fault::Limit)
}

pub(crate) fn recovery_allowed(normal: usize, seeded: usize, seed_seen: bool) -> bool {
    normal > 0 && normal <= 32 && seeded > normal && !seed_seen
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Store(std::vec::Vec<Variable>);
    impl Entries for Store {
        fn contains(&self, key: &Key) -> bool {
            self.0.iter().any(|v| &v.key == key)
        }
        fn push(&mut self, value: Variable) -> Result<(), Fault> {
            self.0.push(value);
            Ok(())
        }
    }
    struct Mock {
        step: usize,
        repeated: bool,
        invalid_name: bool,
        runtime: bool,
        failure: Option<Fault>,
    }
    impl Firmware for Mock {
        fn next(&mut self, key: &mut Key) -> Result<Option<usize>, Fault> {
            self.step += 1;
            if self.step > 3 {
                return Ok(None);
            }
            let c = if self.repeated {
                b'a'
            } else {
                b'a' + self.step as u8
            };
            *key = Key::ascii(&[c], [1; 16]);
            Ok(Some(if self.invalid_name { 3 } else { 4 }))
        }
        fn metadata(&mut self, _: &Key) -> Result<(Option<u32>, usize), Fault> {
            if let Some(fault) = self.failure {
                return Err(fault);
            }
            Ok((Some(if self.runtime { RUNTIME } else { 2 }), 16))
        }
    }
    fn mock() -> Mock {
        Mock {
            step: 0,
            repeated: false,
            invalid_name: false,
            runtime: true,
            failure: None,
        }
    }

    #[test]
    fn good_seeded_scan_retains_seed_and_all_returned_names() {
        let mut store = Store(std::vec::Vec::new());
        let count = walk(&mut mock(), &mut store, Some(Key::ascii(b"seed", [1; 16]))).unwrap();
        assert_eq!(count, 4);
        assert_eq!(store.0[0].size, 16);
        assert_eq!(store.0[0].attributes, Some(RUNTIME));
        assert!(recovery_allowed(1, count, false));
    }
    #[test]
    fn duplicate_firmware_response_aborts_instead_of_looping() {
        let mut fw = mock();
        fw.repeated = true;
        assert_eq!(
            walk(&mut fw, &mut Store(std::vec::Vec::new()), None),
            Err(Fault::Duplicate)
        );
    }
    #[test]
    fn bad_returned_length_and_non_runtime_record_abort_scan() {
        let mut fw = mock();
        fw.invalid_name = true;
        assert_eq!(
            walk(&mut fw, &mut Store(std::vec::Vec::new()), None),
            Err(Fault::Name)
        );
        let mut fw = mock();
        fw.runtime = false;
        assert_eq!(
            walk(&mut fw, &mut Store(std::vec::Vec::new()), None),
            Err(Fault::Metadata)
        );
    }
    #[test]
    fn healthy_or_shorter_enumeration_is_never_recovered() {
        assert!(!recovery_allowed(100, 101, false));
        assert!(!recovery_allowed(15, 15, false));
        assert!(!recovery_allowed(15, 150, true));
        assert!(!recovery_allowed(0, 150, false));
    }
    #[test]
    fn stale_firmware_buffer_tail_does_not_hide_duplicates() {
        let mut key = Key::ascii(b"a", [1; 16]);
        key.name[10] = 42;
        key.validate(4).unwrap();
        assert!(key == Key::ascii(b"a", [1; 16]));
        assert!(!Key::ascii(b"../x", [1; 16]).path_safe());
    }

    #[test]
    fn firmware_or_allocation_failure_stops_before_recovery() {
        let mut fw = mock();
        fw.failure = Some(Fault::Firmware);
        let mut store = Store(std::vec::Vec::new());
        assert_eq!(walk(&mut fw, &mut store, None), Err(Fault::Firmware));
        assert!(store.0.is_empty());

        struct NoMemory;
        impl Entries for NoMemory {
            fn contains(&self, _: &Key) -> bool {
                false
            }
            fn push(&mut self, _: Variable) -> Result<(), Fault> {
                Err(Fault::Memory)
            }
        }
        let mut fw = mock();
        assert_eq!(
            walk(&mut fw, &mut NoMemory, Some(Key::ascii(b"seed", [1; 16]))),
            Err(Fault::Memory)
        );
        assert_eq!(fw.step, 0);
    }

    #[test]
    fn firmware_that_never_ends_is_bounded() {
        struct Endless(usize);
        impl Firmware for Endless {
            fn next(&mut self, key: &mut Key) -> Result<Option<usize>, Fault> {
                self.0 += 1;
                *key = Key::ascii(b"x", [1; 16]);
                key.guid[..8].copy_from_slice(&self.0.to_le_bytes());
                Ok(Some(4))
            }
            fn metadata(&mut self, _: &Key) -> Result<(Option<u32>, usize), Fault> {
                Ok((Some(RUNTIME), 1))
            }
        }
        struct CountOnly;
        impl Entries for CountOnly {
            fn contains(&self, _: &Key) -> bool {
                false
            }
            fn push(&mut self, _: Variable) -> Result<(), Fault> {
                Ok(())
            }
        }
        let mut fw = Endless(0);
        assert_eq!(walk(&mut fw, &mut CountOnly, None), Err(Fault::Limit));
        assert_eq!(fw.0, LIMIT);
    }

    #[test]
    fn missing_attributes_never_qualify_a_seed_but_allow_accessible_other_records() {
        struct Quirk {
            step: usize,
            seed_verified: bool,
        }
        impl Firmware for Quirk {
            fn next(&mut self, key: &mut Key) -> Result<Option<usize>, Fault> {
                self.step += 1;
                if self.step > 1 {
                    return Ok(None);
                }
                *key = Key::ascii(b"x", [1; 16]);
                Ok(Some(4))
            }
            fn metadata(&mut self, key: &Key) -> Result<(Option<u32>, usize), Fault> {
                let attrs = if self.seed_verified && key.name[0] == u16::from(b's') {
                    Some(RUNTIME)
                } else {
                    None
                };
                Ok((attrs, 16))
            }
        }
        let mut bad = Quirk {
            step: 0,
            seed_verified: false,
        };
        let mut store = Store(std::vec::Vec::new());
        assert_eq!(
            walk(&mut bad, &mut store, Some(Key::ascii(b"s", [1; 16]))),
            Err(Fault::Metadata)
        );
        assert_eq!(bad.step, 0);
        assert!(store.0.is_empty());

        let mut good = Quirk {
            step: 0,
            seed_verified: true,
        };
        assert_eq!(
            walk(&mut good, &mut store, Some(Key::ascii(b"s", [1; 16]))),
            Ok(2)
        );
        assert_eq!(store.0[0].attributes, Some(RUNTIME));
        assert_eq!(store.0[1].attributes, None);
    }
}
