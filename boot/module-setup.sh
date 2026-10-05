#!/bin/bash
check() { return 255; }
depends() { echo zfsbootmenu; }
install() {
    inst_multiple openssl sha256sum mktemp cat rm jq mount umount mkdir mv sync date chmod
    inst_binary /usr/bin/kexec /usr/local/libexec/zbm-kexec-real
    inst_simple /usr/local/libexec/zbm-verify-boot
    inst_simple /usr/local/libexec/zbm-save-audit
    inst_simple /etc/zfsbootmenu/verification.pem /etc/zbm-verification.pem
    if [[ ${zbm_ima:-no} == yes ]]; then
        # Do not enable the policy in an image whose kernel lacks the capability.
        # shellcheck disable=SC2154
        grep -qx CONFIG_IMA_LOAD_X509=y "/usr/lib/modules/$kernel/build/.config" || return 1
        inst_multiple keyctl grep mountpoint reboot
        inst_simple /etc/zfsbootmenu/ima.der /etc/keys/x509_ima.der
        inst_simple /etc/zfsbootmenu/ima-policy /etc/zbm-ima-policy
        inst_simple /usr/local/libexec/zbm-load-ima-policy
        inst_hook pre-udev 05 /usr/local/libexec/zbm-ima-pre-udev.sh
    fi
    # dracut otherwise keeps the binary installed by zfsbootmenu.
    # shellcheck disable=SC2154
    rm -f "$initdir/usr/bin/kexec"
    inst_simple /usr/local/libexec/zbm-kexec-audit /usr/bin/kexec
}
