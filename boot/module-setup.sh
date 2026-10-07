#!/bin/bash
check() { return 255; }
depends() { echo zfsbootmenu; }
install() {
    inst_multiple openssl sha256sum mktemp cat rm rmdir cp jq mount umount mkdir mv sync date chmod || exit 1
    inst_binary /usr/bin/kexec /usr/local/libexec/zbm-kexec-real || exit 1
    inst_simple /usr/local/libexec/zbm-verify-boot || exit 1
    inst_simple /usr/local/libexec/zbm-save-audit || exit 1
    inst_simple /etc/zfsbootmenu/verification.pem /etc/zbm-verification.pem || exit 1
    if [[ ${zbm_enforce:-no} == yes ]]; then
        [[ ${zbm_ima:-no} == yes ]] || exit 1
        local flag
        for flag in KEXEC_SIG_FORCE MODULE_SIG_FORCE LOCK_DOWN_KERNEL_FORCE_INTEGRITY; do
            # shellcheck disable=SC2154
            grep -qx "CONFIG_$flag=y" "/usr/lib/modules/$kernel/build/.config" || exit 1
        done
        # Marker is covered by the complete EFI image's signature.
        inst_dir /etc || exit 1
        # shellcheck disable=SC2154
        : > "$initdir/etc/zbm-enforce" || exit 1
        inst_simple /usr/local/libexec/zbm-deny-preunlock || exit 1
        inst_simple /usr/local/libexec/zbm-recovery-guard.sh /lib/zbm-recovery-guard.sh || exit 1
        inst_simple /etc/zfsbootmenu/recovery-pool-guid /etc/zbm-recovery-pool-guid || exit 1
        uv run --no-project --offline --python /usr/bin/python \
            /usr/local/libexec/zbm-harden-shells.py "$initdir" || exit 1
    fi
    if [[ ${zbm_ima:-no} == yes ]]; then
        # Do not enable the policy in an image whose kernel lacks the capability.
        # shellcheck disable=SC2154
        grep -qx CONFIG_IMA_LOAD_X509=y "/usr/lib/modules/$kernel/build/.config" || exit 1
        inst_multiple keyctl grep mountpoint od tr || exit 1
        # Use attr's actual xattr implementation, not a BusyBox applet.
        inst_binary /usr/bin/setfattr || exit 1
        inst_simple /etc/zfsbootmenu/ima.der /etc/keys/x509_ima.der || exit 1
        inst_simple /etc/zfsbootmenu/ima-policy /etc/zbm-ima-policy || exit 1
        inst_simple /etc/zfsbootmenu/ima-policy.sig /etc/zbm-ima-policy.sig || exit 1
        inst_simple /usr/local/libexec/zbm-deny-preunlock || exit 1
        inst_simple /usr/local/libexec/zbm-load-ima-policy || exit 1
        inst_hook pre-udev 05 /usr/local/libexec/zbm-ima-pre-udev.sh || exit 1
    fi
    # dracut otherwise keeps the binary installed by zfsbootmenu.
    # shellcheck disable=SC2154
    rm -f "$initdir/usr/bin/kexec"
    inst_simple /usr/local/libexec/zbm-kexec-audit /usr/bin/kexec || exit 1
}
