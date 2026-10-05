#!/bin/sh
# This hook is embedded only in an explicitly IMA-enabled candidate ZBM.
/usr/local/libexec/zbm-load-ima-policy || {
    echo 'Cannot initialize mandatory IMA initramfs policy; refusing to start ZBM' > /dev/console
    reboot -f
    exit 1
}
