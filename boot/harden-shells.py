#!/usr/bin/env python3
"""Patch only the generated initramfs; reject unsupported upstream layouts."""
import sys
from pathlib import Path
root = Path(sys.argv[1])
# Resolve initramfs absolute symlinks relative to its root, never the build host.
def path(name):
    p = root / name
    for parent in list(p.parents)[:-1]:
        if parent == root: break
        if parent.is_symlink():
            target = parent.readlink()
            if target.is_absolute():
                p = root / str(target).lstrip('/') / p.relative_to(parent)
    return p
changes = {}
def replace(name, old, new, count=1):
    p = path(name)
    s = changes.get(p, p.read_text())
    if s.count(old) != count:
        raise SystemExit(f'Unsupported ZBM layout: {name}: expected {count} occurrences of {old!r}')
    changes[p] = s.replace(old, new)
replace('lib/zfsbootmenu-core.sh', '\nemergency_shell() {', '\n_zbm_menu_recovery_shell() {')
replace('lib/zfsbootmenu-core.sh', '\nzfs_chroot() {', '\n_zbm_menu_zfs_chroot() {')
p = path('lib/zfsbootmenu-core.sh')
changes[p] += '\nsource /lib/zbm-recovery-guard.sh || exit 1\n'
for name in ('usr/bin/zfsbootmenu', 'libexec/zfsbootmenu-init', 'var/lib/dracut/hooks/cmdline/95-zfsbootmenu-parse-commandline.sh'):
    replace(name, 'exec /bin/bash', 'exec /usr/local/libexec/zbm-deny-preunlock "Unable to load startup libraries"')
replace('usr/bin/zfsbootmenu', '    "mod-r")\n      tput cnorm\n      tput clear\n      break\n      ;;', '    "mod-r")\n      zbm_authenticated_recovery\n      ;;')
# The dracut path is independent of ZBM core functions.
replace('lib/dracut-lib.sh', '\n_emergency_shell() {', '\n_zbm_disabled_dracut_shell() {')
p = path('lib/dracut-lib.sh')
changes[p] += '\n_emergency_shell() { /usr/local/libexec/zbm-deny-preunlock "$@"; }\n'
for p, s in changes.items():
    p.write_text(s)
print('Generated initramfs: automatic shells disabled; menu recovery guarded')
