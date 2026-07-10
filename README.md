# linux-okhsunrog

**Experimental** mainline Arch Linux kernel — fork of [`linux-cachyos`](https://aur.archlinux.org/packages/linux-cachyos) tracking 7.1.x, with two extra patches and a newer ZFS shipped as a module subpackage.

Sister package: [`linux-okhsunrog-lts`](https://github.com/okhsunrog/linux-okhsunrog-lts) — same idea but on the 6.18 LTS line and ZFS 2.4.3.

## Why

Same MTL GPU hang workaround as the LTS package (see [drm/i915 #14469](https://gitlab.freedesktop.org/drm/i915/kernel/-/issues/14469)) — needs the i915/xe `enable_rc6` modparam to pass `xe.enable_rc6=0` on the kernel command line.

The mainline branch additionally lets us:

- Try newer GuC firmware behavior on a more recent kernel
- Pick up unrelated upstream fixes faster
- Run ZFS 2.4.3 on root

## What's on top of upstream

- `0001-drm-i915-Add-modparam-for-rc6.patch` — the i915 patch posted by Vinay Belgaumkar @ Intel ([patchwork](https://patchwork.freedesktop.org/patch/666117/))
- `0001-drm-xe-Add-modparam-for-rc6.patch` — same idea ported to `xe`. Rebased for the **7.1.x** xe layout: the GuC RC6 control (`pc_action_setup_gucrc()` and friends) moved out of `xe_guc_pc.c` into the new `xe_guc_rc.c` file (`xe_guc_rc_enable()`/`xe_guc_rc_disable()`), and the `DEFAULT_*` macros moved from `xe_module.c` into `xe_defaults.h`. The modparam check now lives in `xe_guc_rc_enable()`.
- `_build_zfs=yes` defaulted on
- ZFS source is **`cachyos/zfs`** at commit `c681af76`: upstream `zfs-2.4.3` plus CachyOS's `Linux-Maximum: 7.1` metadata update. The commit is pinned for reproducible builds and shared with the LTS kernel recipe.
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

This is **experimental** — mainline 7.1.x, not LTS. Use it as a test mule, not your only kernel. If you boot it as your daily driver, keep `linux-okhsunrog-lts` (or stock `linux-lts`) installed as a fallback bootable kernel.

## Updating from upstream

```sh
git clone https://aur.archlinux.org/linux-cachyos.git upstream
diff upstream/PKGBUILD PKGBUILD
# manually merge bumps (pkgver, _minor, b2sums of source tarball + config) into PKGBUILD
```

Do **not** run `updpkgsums` on this PKGBUILD — it mangles the hand-written `prepare()` function and the scheduler-patch `case`/`;;&` fallthrough logic (silently drops them). Update the source tarball's b2sum by hand instead: `b2sum <tarball>` and paste the result into the first `b2sums` entry.

7.0 is EOL upstream as of 7.0.14; this package tracks 7.1.x now. When bumping to **7.2** or later, re-check the xe RC6 patch against the current `xe_guc_rc.c`/`xe_gt_idle.c` layout — openzfs commit `bf098c76a` ("Linux 7.2: zpl_super: convert to sget_fc()") suggests 7.2 also needs a fresh look at ZFS compat.

## License

Same as Linux kernel: GPL-2.0-only.
