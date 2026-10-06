# linux-okhsunrog

**Experimental** mainline Arch Linux kernel — fork of [`linux-cachyos`](https://aur.archlinux.org/packages/linux-cachyos) tracking 7.2.x, with two extra patches and a newer ZFS shipped as a module subpackage.

Sister package: [`linux-okhsunrog-lts`](https://github.com/okhsunrog/linux-okhsunrog-lts) — same idea but on the 6.18 LTS line and the same ZFS 2.4.4.

## Why

Same MTL GPU hang workaround as the LTS package (see [drm/i915 #14469](https://gitlab.freedesktop.org/drm/i915/kernel/-/issues/14469)) — needs the i915/xe `enable_rc6` modparam to pass `xe.enable_rc6=0` on the kernel command line.

The mainline branch additionally lets us:

- Try newer GuC firmware behavior on a more recent kernel
- Pick up unrelated upstream fixes faster
- Run ZFS 2.4.4 on root

## What's on top of upstream

- `0001-drm-i915-Add-modparam-for-rc6.patch` — the i915 patch posted by Vinay Belgaumkar @ Intel ([patchwork](https://patchwork.freedesktop.org/patch/666117/))
- `0001-drm-xe-Add-modparam-for-rc6.patch` — same idea ported to `xe`. Rebased for the **7.1.x** xe layout: the GuC RC6 control (`pc_action_setup_gucrc()` and friends) moved out of `xe_guc_pc.c` into the new `xe_guc_rc.c` file (`xe_guc_rc_enable()`/`xe_guc_rc_disable()`), and the `DEFAULT_*` macros moved from `xe_module.c` into `xe_defaults.h`. The modparam check now lives in `xe_guc_rc_enable()`. Re-checked on **7.2.0**: same layout, both patches only needed a context refresh (7.2 dropped the neighbouring `i915.inject_probe_failure` param, which made the old i915 hunk apply with fuzz).
- `_build_zfs=yes` defaulted on
- ZFS source is **`openzfs/zfs`** at commit `71a9f957` = release tag `zfs-2.4.4`. 2.4.4 declares `Linux-Maximum: 7.2` and already carries the `sget_fc()` conversion, so the `cachyos/zfs` compat fork is no longer needed. The commit is pinned for reproducible builds and shared with the LTS kernel recipe; the `-zfs` subpackage depends on `zfs-utils=2.4.4` (available from `archzfs`).
- `pkgbase` renamed to `linux-okhsunrog` so it doesn't collide with the upstream AUR package
- `b2sums` block moved up under `source=()` so conditional `b2sums+=('SKIP')` for optional sources isn't clobbered by a later overwrite

## Build / install

```sh
makepkg -si
```

Default config picks up `_use_llvm_lto=thin` from upstream — you'll need `clang`, `llvm`, `lld` in `makedepends`, makepkg pulls them automatically. To build with GCC instead:

```sh
_use_llvm_lto=no makepkg -si
```

After installing, add `xe.enable_rc6=0` (or `i915.enable_rc6=0` if you switch back to i915) to the kernel command line.

## Status / health warning

This is **experimental** — mainline 7.2.x, not LTS. Use it as a test mule, not your only kernel. If you boot it as your daily driver, keep `linux-okhsunrog-lts` (or stock `linux-lts`) installed as a fallback bootable kernel.

## Updating from upstream

```sh
git clone https://aur.archlinux.org/linux-cachyos.git upstream
diff upstream/PKGBUILD PKGBUILD
# manually merge bumps (pkgver, _minor, b2sums of source tarball + config) into PKGBUILD
```

Do **not** run `updpkgsums` on this PKGBUILD — it mangles the hand-written `prepare()` function and the scheduler-patch `case`/`;;&` fallthrough logic (silently drops them). Update the source tarball's b2sum by hand instead: `b2sum <tarball>` and paste the result into the first `b2sums` entry.

7.1 is EOL upstream; this package tracks 7.2.x now. When bumping to **7.3** or later, re-check the xe RC6 patch against the current `xe_guc_rc.c`/`xe_gt_idle.c` layout, and check the pinned OpenZFS release's `META` (`Linux-Maximum`) before bumping — if it lags the kernel, either wait for the next OpenZFS point release or temporarily pin a `cachyos/zfs` compat commit again.

## License

Same as Linux kernel: GPL-2.0-only.

## Secure Boot preparation

The kernel keeps EFI boot, PE signature verification for `kexec_file_load`,
platform/secondary trust keyrings, module signatures and lockdown support enabled.
The recipe checks these settings during preparation. This does **not** enable
mandatory signatures or lockdown, sign the kernel EFI image, or verify an external
initramfs and command line.

The default build embeds `secureboot-trusted.pem`, a bundle of three public
certificates: the local EFI signing certificate, the DKMS module signing
certificate and a dedicated IMA initramfs signing certificate. This permits the
kernel to verify local signatures without giving GitHub Actions any private key.
The additional module signing key generated
by the kernel build continues to sign the packaged modules and ZFS.

To use a different public certificate bundle:

```sh
_secureboot_cert=secureboot-trusted.pem makepkg -s
```

Put the certificates in the recipe directory. Never commit or upload their
private keys. CI verifies that all three certificates are present in the
compiled kernel certificate list, rather than checking the configuration alone.

Ordinary push builds produce `7.2.8-4` and retain permissive defaults. The manual
workflow input `enforce_signatures` enables `_secureboot_enforce=yes` and produces
`7.2.8-5`, requiring signed modules and kexec images with integrity lockdown.
This input is for the later validated deployment: it can prevent old unsigned
snapshot kernels and modules from loading. Do not install it until the ZBM
verification and recovery paths have been prepared. Rust stays enabled in both
build profiles.

## IMA initramfs appraisal

IMA, appraisal, certificate loading and policy readback are enabled in the
kernel, with `ima` in the default LSM list and SHA-256 as its default hash.
The ordinary profile does not load a host-wide appraisal policy. The ZBM
candidate uses the narrow rule in `boot/ima-policy`: only initramfs files read
by `kexec_file_load` require a valid `security.ima` signature. This policy does
not validate the kexec command line or forbid loading without an initramfs;
the manifest, loader restrictions and recovery paths remain separate work.

The local IMA private key is `/var/lib/zbm-secureboot/ima/ima.key`; its certificate
is directly trusted by the compiled kernel. It has the digitalSignature usage
and subject key identifier required by the restricted `.ima` keyring. Unlike
appending a signature, the extended attribute does not change initramfs bytes.
Native ZFS snapshots preserve it; old snapshots without it must be migrated
before enforcement.

`boot/sign-initramfs` is installed as `zbm-sign-initramfs`. The prepared
`boot/module-setup.sh` includes the certificate, policy and early hook only when
`zbm_ima=yes` is explicitly set in the ZBM dracut configuration. It is not enabled
by default. The early hook refuses to continue if
the key or policy cannot be initialized.

CI builds a pinned upstream evmctl and runs `tests/ima-xattr.sh`, then boots the
new kernel in QEMU. The guest uses `boot/load-ima-policy` to verify certificate
acceptance and the kernel's policy readback before testing kexec.
`tests/ima-kexec.sh` checks that unsigned, modified and
wrong-key initramfs files are rejected, that legacy kexec is blocked under
lockdown, and that a signed initramfs boots a second kernel. Test keys are
ephemeral; no private key is placed in the guest initramfs or package artifacts.
Artifacts are uploaded only after these tests pass.

`zbm_enforce=yes` also embeds a mandatory-verification marker. It requires
`zbm_ima=yes` and a kernel with forced module/kexec signatures and integrity
lockdown. `boot/kexec-verify` rejects invalid manifests, changed command lines,
missing initramfs and legacy loading. It stages private copies, preserving IMA
attributes, verifies those copies and uses only `kexec_file_load`. The PASS
message follows a successful kernel load. The default marker-free mode remains
permissive for audit deployments. Boot reports distinguish both modes.

The QEMU gate exercises this wrapper and the actual policy loader, including
modified command lines and missing initramfs. This does not remove recovery-shell
or direct-syscall bypasses; firmware Secure Boot must remain disabled until
those paths and the signed EFI recovery image have been hardened and tested.

Mandatory images also replace automatic ZBM and dracut shells with a diagnostic
screen offering reboot or poweroff. Direct startup-library failures use the
same screen. Explicit menu recovery and chroot require the expected pool GUID,
encryption enabled and its encryption key loaded. This guard uses ZFS state;
it is not a separate recovery password. The patcher changes only generated
initramfs files and rejects unsupported upstream layouts before writing them.
Building this image requires `uv` and `/usr/bin/python` on the build host.
The manual `tests/recovery-init` and `tests/recovery-qemu.py` harness checks
ZBM, dracut and source-failure paths in an isolated guest without host disks.

After downloading and installing the packages, sign the installed `/boot/vmlinuz-*`
images locally with `sbctl sign -s <path>`. Re-sign after every kernel update.
ZFSBootMenu's complete EFI image also needs a signature after every `generate-zbm`.
Keep signing keys on the local machine; the CI artifacts are unsigned EFI images.

For ZFSBootMenu, explicitly select the intended installed kernel using
`generate-zbm -K <kernel-release>` (or `Kernel.Version` in its configuration).
Updating the main OS kernel does not update an existing ZBM image.

The ZFS module subpackage is signed with the kernel build's module key, which is
separate from the EFI signing key. Additional DKMS modules need their own trusted
signatures before enabling lockdown. Keep enforcement disabled until the entire
ZBM boot path, including initramfs/command-line verification, has been prepared
and tested. Snapshot selection can remain available, but old boot environments
must satisfy the eventual verification policy too.
