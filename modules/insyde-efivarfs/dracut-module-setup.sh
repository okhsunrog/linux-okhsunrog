#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
# Build-time integration; runtime loading uses stock systemd-modules-load.
check() { return 255; }
depends() { echo systemd-modules-load systemd-udevd hwdb; }

installkernel() {
    instmods insyde_efivarfs || return 1
    # Keep the host's TPM driver; generic test images use the available TPM drivers.
    hostonly=$(optional_hostonly) instmods '=drivers/char/tpm'
}

install() {
    # shellcheck disable=SC2154
    inst_simple "$moddir/insyde-efivarfs.modules-load.conf" /etc/modules-load.d/insyde-efivarfs.conf || return 1
    inst_simple "$moddir/insyde-efivarfs.modprobe.conf" /etc/modprobe.d/insyde-efivarfs.conf || return 1
    inst_simple "$moddir/10-insyde-efivarfs-ordering.conf" \
        /etc/systemd/system/systemd-modules-load.service.d/10-insyde-efivarfs-ordering.conf || return 1

    # Stock dracut's systemd-pcrextend module installs the phase service, but
    # not systemd-tpm2-setup-early. Include the real service and its dependencies.
    inst_multiple /usr/lib/systemd/systemd-tpm2-setup /usr/lib/systemd/systemd-pcrextend || return 1
    inst_simple /usr/lib/systemd/system/systemd-tpm2-setup-early.service || return 1
    inst_simple /usr/lib/systemd/system/tpm2.target || return 1
    inst_simple /usr/lib/systemd/system-generators/systemd-tpm2-generator || return 1
    inst_simple /usr/lib/systemd/system/sysinit.target.wants/systemd-tpm2-setup-early.service || return 1
    inst_rules 60-tpm-udev.rules 60-tpm2-id.rules || return 1
    inst_sysusers tpm2-tss.conf
    inst_sysusers system-user-tss.conf
    inst_libdir_file 'libtss2-esys.so.*' 'libtss2-mu.so.*' 'libtss2-rc.so.*' 'libtss2-tcti-device.so.*' || return 1

    # Keep allocation priorities identical in the initrd and the host. Local
    # /etc definitions override the packaged /usr/lib/nvpcr defaults.
    local definition
    for definition in /etc/nvpcr/*.nvpcr; do
        [[ -e $definition || -L $definition ]] || continue
        inst_simple "$definition" || return 1
    done

    # kexec does not carry systemd-stub's synthetic /.extra cpio to the OS.
    # Embed the policy for the trusted ZBM UKI into the independent OS initrd.
    if [[ ${zbm_pcr_policy:-no} == yes ]]; then
        inst_simple /etc/systemd/tpm2-pcr-public-key.pem || return 1
        inst_simple /etc/systemd/tpm2-pcr-signature.json || return 1
        inst_simple /usr/lib/systemd/system/systemd-pcrosseparator.service || return 1
        inst_simple /usr/lib/systemd/system/systemd-pcrnvdone.service || return 1
        inst_simple /usr/lib/systemd/system/sysinit.target.wants/systemd-pcrosseparator.service || return 1
        inst_simple /usr/lib/systemd/system/sysinit.target.wants/systemd-pcrnvdone.service || return 1
    fi
}
