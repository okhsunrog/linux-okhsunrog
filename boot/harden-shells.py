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
replace('usr/bin/zfsbootmenu', '''      timed_prompt -d 10 \\
        -m "$( colorize red "No boot environments with kernels found" )" \\
        -m "$( colorize red "Dropping to an emergency shell to allow recovery attempts" )"
      tput clear
      tput cnorm
      exit 1''', '''      zbm_discovery_recovery
      if [ "$?" -eq 75 ]; then
        tput clear
        tput cnorm
        continue
      fi
      exit 1''')
replace('lib/zfsbootmenu-ui.sh', '  : > "${be_list}"',
        '  : > "${be_list}"\n  : > "${BASE}/discovery-errors"')
replace('lib/zfsbootmenu-ui.sh', '    load_key "${fs}" || continue', '''    if ! load_key "${fs}"; then
      zbm_discovery_failure "Key not loaded" "${fs}"
      continue
    fi''')
replace('lib/zfsbootmenu-core.sh', '    zerror "unable to mount ${fs}"',
        '    zerror "unable to mount ${fs}"\n    zbm_discovery_failure "Mount failed" "${fs}"')
replace('lib/zfsbootmenu-core.sh', '  zerror "failed to find kernels on ${fs}"',
        '  zerror "failed to find kernels on ${fs}"\n  zbm_discovery_failure "No matching kernel/initramfs pair" "${fs}"')
# The dracut path is independent of ZBM core functions.
replace('lib/dracut-lib.sh', '\n_emergency_shell() {', '\n_zbm_disabled_dracut_shell() {')
p = path('lib/dracut-lib.sh')
changes[p] += '\n_emergency_shell() { /usr/local/libexec/zbm-deny-preunlock "$@"; }\n'
for p, s in changes.items():
    p.write_text(s)
print('Generated initramfs: automatic shells disabled; menu recovery guarded')
