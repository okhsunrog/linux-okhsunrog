# Gotchas

Things that bit (or will bite) when maintaining this package. Write them down so future-you doesn't relearn the same lessons.

## 1. xe rc6 patch is fragile to kernel version

The xe driver's RC6 path keeps getting refactored:

| Kernel | Where `pc_action_setup_gucrc(GUCRC_FIRMWARE_CONTROL)` lives | Patch needs |
|---|---|---|
| 6.18.x (LTS) | `xe_guc_pc.c::xe_guc_pc_start()` with `goto out;` style | original 6.18 patch |
| **7.0.x (this package)** | same file, but rewritten to use `CLASS(xe_force_wake, ...)` + direct `return ret;` | `return xe_guc_pc_gucrc_disable(pc);` (no `goto out;`) |
| 7.1.x | new file `xe_guc_rc.c::xe_guc_rc_enable()`, gucrc setup decoupled per kernel commit `40a684f91d` | full re-port: gate inside `xe_guc_rc_enable`, call `xe_guc_rc_disable(guc)` |
| **7.2.x (this package)** | same as 7.1 | context refresh only (regenerated with `git format-patch` from a patched 7.2.0 tree). The **i915** hunk in `i915_params.c` needed it too: 7.2 removed the `inject_probe_failure` param the hunk anchored on, so the old patch applied with fuzz 2 |

**When bumping past 7.2:** dry-run both patches against the new tarball first (`patch -Np1 --dry-run`) and treat any `fuzz` as a rewrite signal, not a pass. If `xe_guc_rc.c` moved again, re-port based on the new file structure — search for `xe_guc_rc_enable` in upstream linux master.

**Fuzzy patch matching warning.** `patch -p1` happily applies hunks "with fuzz" by silently matching context lines that no longer exist verbatim. The 6.18 patch fuzzy-matched `goto out;` against `return 0;` here — patch reported success but the resulting code didn't compile. **Always re-test the build after a rebase, even when patch reports clean apply.**

## 2. b2sums must mirror conditional `source+=()`

Upstream cachyos PKGBUILD declares `b2sums=(...)` at the bottom, AFTER conditional `source+=` blocks for LTO/ZFS/NVIDIA/r8125/sched patches. With non-default flags this fails:

```
==> ERROR: Integrity checks (b2) differ in size from the source array.
```

Our PKGBUILD moves `b2sums=(...)` up under `source=()` and adds `b2sums+=('SKIP')` next to every conditional `source+=`. **When merging upstream changes, watch for new `source+=` blocks** — add a matching `b2sums+=('SKIP')` next to each, or the build breaks for any non-default flag combination.

## 3. ZFS is an extramodule, not builtin

Despite `_build_zfs=yes`, cachyos PKGBUILD does **not** use `--enable-linux-builtin` + `copy-builtin`. It builds zfs.ko + spl.ko as **kernel modules** (`./configure --with-config=kernel`) and packages them as a separate `linux-okhsunrog-zfs` subpackage in `/usr/lib/modules/<ver>/extramodules/`.

Practical implications:
- Modules are **version-locked** to the kernel package: `depend=linux-okhsunrog=$pkgver-$pkgrel`
- Loaded by `modprobe` from initramfs at boot — not vmlinuz-baked
- No DKMS rebuild needed on target system — modules are pre-compiled with the kernel
- You can `pacman -R linux-okhsunrog-zfs` independently if ZFS misbehaves; kernel stays installed

## 4. Default build pulls in LLVM toolchain

Non-LTS cachyos PKGBUILD defaults to `_use_llvm_lto=thin`, which means **clang + llvm + lld are required** to build. They get auto-installed by `makepkg -s`. If you want a classic GCC build:

```sh
_use_llvm_lto=no makepkg -s
```

Note that `_pkgsuffix` won't change to `-gcc` unless you also set `_use_gcc_suffix=yes` (default is `no` in upstream non-LTS — different from LTS variant).

## 5. ZFS is pinned to a tagged release commit

Pinned to `71a9f957` in `openzfs/zfs` = release tag `zfs-2.4.4` (`Linux-Maximum: 7.2`). No CachyOS fork any more — only reach for `cachyos/zfs` again if a new kernel lands before an OpenZFS release that supports it. This keeps the mainline and LTS recipes on the same ZFS source. Minor releases can still surface regressions in the wild. Risks:

- New behavior may surprise (slow imports, unexpected log spam, etc.)
- If something breaks ZFS root, recovery requires alternative kernel/initramfs with working ZFS
- **Always keep `linux-okhsunrog-lts` (or stock `linux-lts` + `zfs-dkms`) installed as a bootable fallback**

We pin to a specific commit (not a tag/branch) for reproducible builds. Bump `_zfsver` and `_zfscommit` deliberately for later OpenZFS releases; `git+...zfs.git#commit=...` ensures fixes are only picked up explicitly. The ZFS module package has a matching versioned dependency on `zfs-utils`, preventing pacman from silently accepting a userland/kmod release mismatch.

## 6. Mainline will EOL fast

7.2 is **not** an LTS release. It enters EOL when 7.3 is stable (roughly 2-3 months after 7.2). After that, no upstream stable backports for 7.2.x. Plan:

- Either jump to 7.3 when it hits stable (and re-check the xe patch — see #1)
- Or jump to whatever next LTS is announced (was 6.18, next LTS is TBD per kernel.org schedule)
- Or fall back to `linux-okhsunrog-lts`

Either way, **do not let this kernel become your only kernel**.

## 7. RC6 must be disabled in cmdline, patch alone is not enough

The patch only **adds** the modparam. By default `xe.enable_rc6=1` (kept enabled). To actually fix the MTL freeze:

```
xe.enable_rc6=0     # in kernel cmdline, e.g. via ZFSBootMenu commandline property
```

Or `i915.enable_rc6=0` if you switch back to i915. Without this on the cmdline, the bug still triggers — the patched kernel behaves like stock until you flip the modparam.

## 8. Updating from upstream: workflow

```sh
# clone upstream snapshot for reference
git clone https://aur.archlinux.org/linux-cachyos.git upstream-fresh
diff upstream-fresh/PKGBUILD PKGBUILD | less
```

Things to re-check on every upstream bump:

- [ ] `_minor` bumped → update tarball b2sum (in our top `b2sums=()` block, position 1)
- [ ] Any new `source+=` block in PKGBUILD → add matching `b2sums+=('SKIP')` next to it
- [ ] Did upstream rebase against a kernel with refactored xe RC6? → see #1
- [ ] Did i915 RC6 code change? Generally stable, but check `drivers/gpu/drm/i915/gt/intel_rc6.c` for context shifts
- [ ] Patch applied "with fuzz" warnings → suspect the patch needs a refresh, not a reapply
- [ ] After rebuild, **do an actual boot test**, not just compile success

Recompute b2sum for any patch you edit:
```sh
b2sum 0001-drm-xe-Add-modparam-for-rc6.patch
```

## 9. Don't trust silent makepkg builds

The build can succeed (`exit 0`) with subtle issues:
- Patch applied with fuzz against drifting context (see #1)
- Modules built but `depmod`/`modprobe` complains at runtime
- Kernel boots but key features (e.g. ZFS) silently fail to load due to ABI mismatch

After install, verify:
```sh
zpool status                                # ZFS modules loaded?
cat /sys/module/xe/parameters/enable_rc6    # rc6 modparam present + active?
dmesg | grep -E 'GuC|rc6|RC6'               # GuC FW loaded, rc6 state sane?
journalctl -k -p err -b                     # any kernel errors since boot?
```
