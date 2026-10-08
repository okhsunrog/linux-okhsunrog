#!/bin/bash
set -euo pipefail
[[ $# == 2 ]] || { echo 'usage: ima-kexec.sh KERNEL_SOURCE EVMCTL' >&2; exit 2; }
source_dir=$(realpath "$1") evmctl=$(realpath "$2")
repo=$(cd "$(dirname "$0")/.." && pwd)
for flag in IMA IMA_APPRAISE IMA_LOAD_X509 IMA_KEYRINGS_PERMIT_SIGNED_BY_BUILTIN_OR_SECONDARY MODULE_SIG_KEY_TYPE_RSA; do
    grep -qx "CONFIG_$flag=y" "$source_dir/.config"
done
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
root=$work/root
mkdir -p "$root"/{bin,proc,sys,dev,run,tmp,etc/keys,fixtures,usr/local/libexec}
chmod 1777 "$root/tmp"
copy_binary() {
    local binary=$1 lib
    install -Dm755 "$binary" "$root/bin/$(basename "$binary")"
    while read -r lib; do
        install -Dm755 "$lib" "$root$lib"
    done < <(ldd "$binary" 2>/dev/null | awk '/=> \// {print $3} /^[[:space:]]*\// {print $1}')
}
copy_binary "$(command -v busybox)"
copy_binary "$(command -v bash)"
copy_binary "$(command -v cp)"
copy_binary "$(command -v openssl)"
copy_binary "$(command -v sha256sum)"
copy_binary "$(command -v mktemp)"
copy_binary "$(command -v kexec)"
copy_binary "$(command -v keyctl)"
copy_binary "$(command -v setfattr)"
copy_binary "$(command -v getfattr)"
copy_binary "$(command -v od)"
copy_binary "$(command -v tr)"
for app in sh mount mountpoint cat dd poweroff sleep grep dmesg cut mkdir rm rmdir; do ln -s busybox "$root/bin/$app"; done
install -m755 "$repo/tests/ima-guest-init" "$root/init"
install -m644 "$repo/boot/ima-policy" "$root/etc/zbm-ima-policy"
install -m755 "$repo/boot/load-ima-policy" "$root/usr/local/libexec/zbm-load-ima-policy"
install -m755 "$repo/boot/kexec-verify" "$root/bin/kexec"
install -m755 "$(command -v kexec)" "$root/usr/local/libexec/zbm-kexec-real"
install -m755 "$repo/boot/verify-boot" "$root/usr/local/libexec/zbm-verify-boot"
install -m755 "$repo/boot/save-audit" "$root/usr/local/libexec/zbm-save-audit"
: > "$root/etc/zbm-enforce"
install -m644 "$source_dir/certs/signing_key.x509" "$root/etc/keys/x509_ima.der"
awk '/-----BEGIN CERTIFICATE-----/{n++} n==3 {print} /-----END CERTIFICATE-----/ && n==3 {exit}' \
    "$repo/secureboot-trusted.pem" > "$work/owner-ima.pem"
openssl x509 -in "$work/owner-ima.pem" -outform DER -out "$root/fixtures/owner-ima.der"
openssl x509 -inform DER -in "$source_dir/certs/signing_key.x509" -out "$work/build.pem"
# Match the production loader: it restores a detached policy signature from
# cpio, then submits the signed policy's absolute pathname to securityfs.
# Use only this build's ephemeral signing key, never an owner private key.
"$evmctl" -a sha256 -k "$source_dir/certs/signing_key.pem" \
    --keyid-from-cert "$work/build.pem" --sigfile ima_sign "$root/etc/zbm-ima-policy"
test -s "$root/etc/zbm-ima-policy.sig"
sbsign --key "$source_dir/certs/signing_key.pem" --cert "$work/build.pem" \
    --output "$work/kernel" "$source_dir/arch/x86/boot/bzImage"
# The second-stage archive contains no private key and uses the same simple init.
(cd "$root"; find . -print0 | cpio --null -o -H newc --quiet | gzip -n) > "$work/second.img"
cp "$work/second.img" "$work/good"
"$evmctl" -a sha256 -k "$source_dir/certs/signing_key.pem" --keyid-from-cert "$work/build.pem" --sigfile ima_sign "$work/good"
openssl req -new -x509 -newkey rsa:2048 -nodes -subj /CN=Untrusted-IMA-Test \
    -addext keyUsage=digitalSignature -keyout "$work/wrong.key" -out "$work/wrong.pem" >/dev/null 2>&1
cp "$work/second.img" "$work/wrong"
"$evmctl" -a sha256 -k "$work/wrong.key" --keyid-from-cert "$work/wrong.pem" --sigfile ima_sign "$work/wrong"
install -m644 "$work/kernel" "$root/kernel"
install -m644 "$work/good" "$root/fixtures/good"
install -m644 "$work/second.img" "$root/fixtures/unsigned"
kh=$(sha256sum "$work/kernel"); ih=$(sha256sum "$work/good")
printf 'ZBM-POLICY-v1\n%s\n%s\nroot=ZFS=novafs/\nconsole=ttyS0 ima_test=second\n' "${kh%% *}" "${ih%% *}" > "$root/kernel.zbm-policy"
openssl dgst -sha256 -sign "$source_dir/certs/signing_key.pem" -out "$root/kernel.zbm-policy.sig" "$root/kernel.zbm-policy"
openssl x509 -in "$work/build.pem" -pubkey -noout > "$root/etc/zbm-verification.pem"
for kind in good wrong; do
    printf 0x > "$root/fixtures/$kind.hex"
    od -An -v -tx1 "$work/$kind.sig" | tr -d ' \n' >> "$root/fixtures/$kind.hex"
done
# Check the same hexadecimal transport used inside the guest before booting.
setfattr -n user.ima-test -v "$(cat "$root/fixtures/good.hex")" "$work/good"
getfattr --only-values -n user.ima-test "$work/good" > "$work/decoded.sig"
cmp "$work/good.sig" "$work/decoded.sig"
(cd "$root"; find . -print0 | cpio --null -o -H newc --quiet | gzip -n) > "$work/first.img"
diagnostics=$repo/ima-test-artifacts
mkdir -p "$diagnostics"
# Public boot inputs only: retain them so a failed guest can be rerun locally.
install -m644 "$source_dir/arch/x86/boot/bzImage" "$diagnostics/bzImage"
install -m644 "$source_dir/.config" "$diagnostics/kernel.config"
install -m644 "$work/first.img" "$diagnostics/first.img"
set +e
timeout 180 qemu-system-x86_64 -machine q35,accel=tcg -cpu max -m 1024 -smp 2 \
    -nodefaults -nographic -no-reboot -monitor none -serial stdio \
    -kernel "$source_dir/arch/x86/boot/bzImage" -initrd "$work/first.img" \
    -append 'console=ttyS0 quiet panic=-1 lockdown=integrity ima_appraise=enforce' \
    > "$work/console.log" 2>&1
status=$?
set -e
cat "$work/console.log"
install -m644 "$work/console.log" "$diagnostics/console.log"
[[ $status == 0 ]] || { echo "QEMU failed or timed out: $status" >&2; exit 1; }
if grep -q IMA_TEST_FAIL "$work/console.log"; then exit 1; fi
for marker in OWNER_KEYS_TRUSTED POLICY_LOADED NATIVE_REJECTED_unsigned NATIVE_REJECTED_tampered NATIVE_REJECTED_wrong-key REJECTED_unsigned REJECTED_tampered REJECTED_wrong-key LEGACY_KEXEC_REJECTED LEGACY_SYSCALL_REJECTED CMDLINE_REJECTED MISSING_INITRAMFS_REJECTED VALID_LOAD SECOND_KERNEL_BOOTED; do
    grep -q "IMA_TEST_$marker" "$work/console.log" || { echo "Missing marker: $marker" >&2; exit 1; }
done
echo 'IMA guest tests passed'
