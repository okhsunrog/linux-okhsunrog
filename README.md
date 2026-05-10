# linux-okhsunrog

**Experimental** mainline Arch Linux kernel — fork of [`linux-cachyos`](https://aur.archlinux.org/packages/linux-cachyos) tracking 7.0.x, with two extra patches and a newer ZFS staged for built-in.

Sister package: [`linux-okhsunrog-lts`](https://github.com/okhsunrog/linux-okhsunrog-lts) — same idea but on the 6.18 LTS line and stable ZFS 2.4.1.

## Why

Same MTL GPU hang workaround as the LTS package (see [drm/i915 #14469](https://gitlab.freedesktop.org/drm/i915/kernel/-/issues/14469)) — needs the i915/xe `enable_rc6` modparam to pass `xe.enable_rc6=0` on the kernel command line.

The mainline branch additionally lets us:

- Try newer GuC firmware behavior on a more recent kernel
- Pick up unrelated upstream fixes faster
- Test ZFS 2.4.2-staging on root before it hits a tagged release

## What's on top of upstream

- `0001-drm-i915-Add-modparam-for-rc6.patch` — the i915 patch posted by Vinay Belgaumkar @ Intel ([patchwork](https://patchwork.freedesktop.org/patch/666117/))
- `0001-drm-xe-Add-modparam-for-rc6.patch` — same idea ported to `xe`. Applies cleanly to the **7.0.x** xe layout (still using `xe_guc_pc.c`; the post-7.0 decoupling into `xe_guc_rc.c` will need a different version of this patch when bumping to 7.1+).
- `_build_zfs=yes` defaulted on
- ZFS source switched from CachyOS's pinned `cachyos/zfs` (2.4.1) to **`tonyhutter/zfs` `zfs-2.4.2-staging`** branch, pinned to commit `ea3171f0` (tag `zfs-2.4.2`). Reports `Linux-Maximum: 7.0` in META, so 7.0.x is in range.
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

This is **experimental** — mainline 7.0.x, not LTS. ZFS is **staging**, not released. Use it as a test mule, not your only kernel. If you boot it as your daily driver, keep `linux-okhsunrog-lts` (or stock `linux-lts`) installed as a fallback bootable kernel.

## Updating from upstream

```sh
git clone https://aur.archlinux.org/linux-cachyos.git upstream
diff upstream/PKGBUILD PKGBUILD
# manually merge bumps (pkgver, _minor, b2sums of source tarball + config) into PKGBUILD
```

When bumping to **7.1** or later, the xe patch will need to be rewritten — the `pc_action_setup_gucrc()` call moved out of `xe_guc_pc.c` into the new `xe_guc_rc.c` file (see kernel commit `40a684f91d`).

## License

Same as Linux kernel: GPL-2.0-only.
