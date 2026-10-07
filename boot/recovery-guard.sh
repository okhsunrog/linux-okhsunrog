#!/bin/bash
# Sourced after the original core definitions in a mandatory-verification image.
zbm_discovery_failure() {
    printf '%s: %s\n' "$1" "$2" >> "${BASE:-/zfsbootmenu}/discovery-errors"
}
zbm_discovery_recovery() {
    /usr/local/libexec/zbm-deny-preunlock --retry-menu 'No bootable environments found'
}
zbm_recovery_authorized() {
    local expected actual status encryption
    IFS= read -r expected < /etc/zbm-recovery-pool-guid || return 1
    actual=$(zpool get -H -o value guid novafs 2>/dev/null) || return 1
    [[ $actual == "$expected" ]] || return 1
    encryption=$(zfs get -H -o value encryption novafs 2>/dev/null) || return 1
    [[ $encryption != off && $encryption != '-' ]] || return 1
    status=$(zfs get -H -o value keystatus novafs 2>/dev/null) || return 1
    [[ $status == available ]]
}
emergency_shell() {
    /usr/local/libexec/zbm-deny-preunlock "$@"
}
zbm_authenticated_recovery() {
    if zbm_recovery_authorized; then
        _zbm_menu_recovery_shell "$@"
    else
        /usr/local/libexec/zbm-deny-preunlock 'Encrypted root is not unlocked'
    fi
}
zfs_chroot() {
    if zbm_recovery_authorized; then
        _zbm_menu_zfs_chroot "$@"
    else
        /usr/local/libexec/zbm-deny-preunlock 'Encrypted root is not unlocked'
    fi
}
