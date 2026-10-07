# Insyde efivarfs recovery for the Framework 13

Rust out-of-tree kernel module with a small C bridge to EFI and VFS. Targets
Framework Laptop 13 Intel Core Ultra Series 1 with Insyde firmware. Other DMI
platforms are rejected. License: GPL-2.0-only.

The recovery method comes from
[melynx/insyde-uefi-variable-recovery](https://github.com/melynx/insyde-uefi-variable-recovery/tree/bd0d2ce8ad31c098799844bd0993afa3f1602d50).
All source, build files, helper scripts and documentation in that revision were
reviewed: the kernel module, UEFI probe, NVRAM tools, systemd integration,
measurements, firmware disassembly and stated limits. Its hardware results were
on Lenovo; its Framework findings were static BIOS analysis. Our results below
are independent physical Framework measurements.

The published UEFI probe runs before ExitBootServices and writes a log to its
filesystem. The separate before/after ExitBootServices experiment is described
in the author's measurements; that runtime-transition program is not the
published `efi/varprobe.c`. The SPI parser proposes a seed heuristically and is
not proof of a valid runtime walk. No SPI dump or BIOS flash is needed for our
confirmed seed.

## Mechanism and boundaries

`expose=0` (default) performs only diagnosis. `expose=1` restores missing efivarfs
inodes after those same checks. Neither mode writes firmware variables, deletes
them, patches firmware or changes key trust. Recovered entries remain after
module unload and disappear when the efivarfs instance is destroyed or on reboot.
Ordinary later writes by other programs still use the normal firmware write path.

`PlatformLang` under the global UEFI GUID is the fixed, hardware-tested seed.
Both walks must end with EFI_NOT_FOUND; the normal list must be small, lack the
seed, and be shorter than the seeded list. Names, sizes, runtime accessibility,
duplicates and iteration limits are validated before exposure. Non-ASCII,
control-containing or slash-containing names are skipped. Known Secure Boot and
boot variables are also probed exactly if missing from the seeded suffix.
This does not prove that every unknown variable before the seed is recovered.

On this Framework BIOS, some size-only GetVariable calls do not fill Attributes,
contrary to the UEFI requirement for BUFFER_TOO_SMALL. An untouched reserved-bit
sentinel represents unknown attributes explicitly; it is not converted to a
fabricated bitmask. Runtime GetVariable success/BUFFER_TOO_SMALL proves that
those records are accessible. Returned attributes, when available, must contain
RUNTIME. The seed additionally requires an independently returned RUNTIME bit:
the C bridge reads only the fixed public language setting into a bounded scratch
buffer and clears it, without returning its contents to Rust. Arbitrary variable
payloads, password configuration and private keys are not read by the module.

The module uses Linux's EFI runtime locking. The lock is released before VFS
operations. The C bridge opens efivarfs entries read-only with O_CREAT and sets
their in-memory inode sizes. It calls neither SetVariable nor file write/unlink.
A partial VFS failure is reported; removing those files with unlink would delete
firmware variables and must not be used as rollback.

## Build and test

```sh
make test
make
```

Requires CONFIG_RUST=y, matching prepared kernel headers and the exact Rust
compiler used to build their crate metadata. The kernel package records its
rustup toolchain in `build/rust-toolchain-name`; the Makefile resolves it.
`RUST_TOOLCHAIN_ROOT=/path/to/rustup/toolchains` allows DKMS to find that toolchain
without changing root's HOME. Missing metadata/toolchains cause a build failure.
Kernel Kbuild currently uses Rust edition 2021; independent model tests use 2024.
The model tests cover malformed replies, missing attributes, bad seeds, duplicate
records, firmware/allocation failures, healthy firmware and unbounded enumeration.

Sign each newly built module with the host's trusted module key. On this host,
DKMS already manages `/var/lib/dkms/mok.key` and `/var/lib/dkms/mok.pub`, whose
public certificate is built into the kernel. No MOK enrollment or firmware-key
change is necessary. Never put the private key into this source tree.

```sh
sudo /usr/lib/modules/"$(uname -r)"/build/scripts/sign-file sha512 \
  /var/lib/dkms/mok.key /var/lib/dkms/mok.pub insyde_efivarfs.ko
sudo insmod insyde_efivarfs.ko
sudo journalctl -k -b -g insyde_efivarfs
sudo rmmod insyde_efivarfs
# After the successful dry run:
sudo insmod insyde_efivarfs.ko expose=1
sudo rmmod insyde_efivarfs
```

## Persistence

`dkms.conf` rebuilds and signs the module for installed kernels. The host's
package-specific `/etc/dkms/insyde-efivarfs.conf` supplies its toolchain root.
Normal `systemd-modules-load.service` now loads it with `expose=1`; it remains
loaded. The earlier dedicated recovery service and mount/unmount helper were
removed. On fixed firmware the module performs a healthy no-op.

| Source | Installed location |
| --- | --- |
| `insyde-efivarfs.modules-load.conf` | `/etc/modules-load.d/insyde-efivarfs.conf` |
| `insyde-efivarfs.modprobe.conf` | `/etc/modprobe.d/insyde-efivarfs.conf` |
| `10-insyde-efivarfs-ordering.conf` | `/etc/systemd/system/systemd-modules-load.service.d/10-insyde-efivarfs-ordering.conf` |
| `insyde-efivarfs.dracut.conf` | `/etc/dracut.conf.d/insyde-efivarfs.conf` |
| `pcr-policy.dracut.conf` (optional NvPCR integration) | `/etc/dracut.conf.d/pcr-policy.conf` |
| `nvpcr/*.nvpcr` (Framework allocation priorities) | `/etc/nvpcr/*.nvpcr`, also copied into the OS initrd |
| `dracut-module-setup.sh` and the three runtime configs | `/usr/lib/dracut/modules.d/12insyde-efivarfs/` (`module-setup.sh` for the script) |

PID 1 mounts efivarfs before starting units; this host's stock mount is read-write.
The ordering drop-in places the module loader before udev, tpm2.target, early/normal
TPM setup, the initrd PCR phase and OS separator. Direct service ordering matters:
systemd-tpm2-generator can omit tpm2.target when a built-in TPM driver is present.
The drop-in explicitly wants that target on this hardware to wait for TPM udev
initialization, and orders the early PCR/report sockets after module loading.
No forced remount is performed. A manually configured read-only efivarfs would
prevent creating the missing inodes and must be handled explicitly by its owner.

The dracut plugin places the signed module and the same configuration in the OS
initramfs. It includes the stock early TPM setup service, binaries/TSS libraries,
TPM udev rules, tss user and hwdb module. In a hostonly image hwdb carries the local
TPM configuration. With `pcr-policy.dracut.conf` (`zbm_pcr_policy=yes`) it also
embeds the ZBM PCR public key/signature and the stock PCR separator services.
Package hook order is DKMS build/sign, dracut rebuild, then IMA
initramfs signing and boot-manifest signing. Rebuilding an initramfs manually also
requires the last signing step before booting it.

This runs before the initrd's measured-OS condition checks. Recovery only after
local-fs is too late: PID 1 can cache a negative result and skip TPM services even
if a new systemd-analyze process subsequently reports success. Automatic early
recovery and TPM SRK setup were accepted on the physical boot at 03:59:28.

## Physical validation, 2026-10-07

Kernel `7.2.8-5-okhsunrog`, BIOS `03.07`, firmware Secure Boot enabled:

- Unsigned module rejected with `Key was rejected by service`.
- Module signed with the existing DKMS key accepted by the enforced kernel.
- Normal walk: 15 names; PlatformLang-seeded walk: 131; normal end in both cases.
- Dry run created no entries.
- Recovery created 116 entries, found 15 present, skipped 0, failed 0.
- Module unloaded; 131 entries remained.
- PK, KEK, db and DBX raw-byte hashes unchanged before/after recovery.
- sbctl: Secure Boot enabled, Setup Mode disabled.
- efibootmgr: original BootOrder and ZFSBootMenu entry visible.
- bootctl: firmware information visible, Secure Boot enabled, measured UKI/OS yes.
- New systemd-analyze process: ConditionSecurity=measured-os succeeds.

Those measurement markers describe the EFI ZBM image, not an attestation of all
later kexec inputs. On the subsequent physical boot the module automatically
restored 116 entries in the initrd, early TPM setup ran, and normal TPM setup
exited successfully. Details: [host setup](../../docs/framework-secure-boot.md).

The real TPM setup executable was also run with the stock service's accepted exit
statuses. The stored SRK and its saved public key matched. With the existing udev
property `TPM2_BROKEN_NVPCR=1`, exit 69 (UNAVAILABLE) is accepted; NvPCR is deliberately
not allocated. This demonstrates SRK operation, not native NvPCR support or TPM
unlock of ZFS. No TPM clear/reset was performed.
After that acceptance a signed NvPCR policy was installed and the hwdb workaround
removed. Native NvPCR setup was accepted in isolated VMs. On the physical boot
at 04:52:02, the default first allocation (`verity`, orderly/RAM-backed) returned
TPM_RC_NV_SPACE and early setup skipped all four indices. Late hardware setup
migrated its old index but could no longer satisfy the enter-initrd policy.
The existing SRK, EFI recovery and enforced boot verification still passed.

Local `/etc/nvpcr` priorities now put hardware, cryptsetup and login before
unused verity. Index handles, write policies and orderly modes remain stock;
there is no conversion of runtime measurements to NVRAM writes. Only one
additional 32-byte orderly index fit a direct physical capacity probe; a second
returned NV_SPACE. Probe indices were removed and original indices retained.
The adjusted physical boot passed at 05:10:25 (boot ID
`738a7d34-60ff-4c5b-b9ff-d6d6a28b9dd7`): all three selected NvPCRs initialized,
hardware product-ID and user-login extensions succeeded, and verity was skipped
because of limited RAM. The cryptsetup value is the initialization-only digest;
there is no LUKS unlock measurement on this ZFS host. No systemd units failed.
EFI recovery again created 116 entries without errors; enforced ZBM audit passed.
See the host notes for the diagnosis, VM regression cases and rollback files.

## Isolated initrd acceptance

```sh
sudo uv run --no-project tests/efi-autoload-qemu.py "$(uname -r)" /root/new-efi-test
```

The fixture builds a generic systemd initramfs without the host's ZFS key, boots a
disposable signed UKI under OVMF Secure Boot and uses a fresh swtpm. A signed module
loads through the stock loader before udev and early TPM setup; the measured-OS
condition passes, SRK exists and the NvPCR workaround produces accepted status 69.
A second fixture strips the module signature: the kernel rejects it, the loader
fails and the test verifies the kernel's rejection message. OVMF has healthy EFI
enumeration; this test covers autoload/trust/ordering, not the physical Insyde bug.

Accepted runs on 2026-10-07: `efi-autoload-20261007/vm-007/signed.log` and
`efi-autoload-20261007/vm-008/unsigned.log` under `/var/lib/zbm-secureboot/`.

NvPCR cases in the same harness: `nvpcr`, `nvpcr-wrongref`, `nvpcr-wrongphase`,
`nvpcr-tampered`, `nvpcr-kexec`. They use TCG by default for reproducible virtual
firmware measurement. The last case transfers the PCR policy into an independent
IMA-signed OS initramfs and performs real kexec_file_load. All four NvPCRs initialize
with the correct policy; invalid references/phases/signatures are rejected.

`nvpcr-limited-default` and `nvpcr-limited-kexec` additionally seed **only a
disposable software TPM** with legacy hardware/cryptsetup indices and RAM fillers
until TPM_RC_NV_SPACE. The default-priority control reproduces the early skip of
all indices. The corrected-priority kexec case requires hardware, cryptsetup and
login initialization, verity skipped with accepted status 73, and successful
product-ID extension. Every seeding command explicitly selects the fixture TCTI;
the host TPM and production initramfs are not used by these VM fixtures.
