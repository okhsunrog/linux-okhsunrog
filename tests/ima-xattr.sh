#!/bin/bash
set -euo pipefail
[[ $# == 4 ]] || { echo 'usage: ima-xattr.sh EVMCTL PRIVATE_KEY PEM_CERT DER_CERT' >&2; exit 2; }
evmctl=$(realpath "$1") key=$(realpath "$2") pem=$(realpath "$3") der=$(realpath "$4")
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
printf 'Original IMA fixture\n' > "$work/good"
before=$(sha256sum "$work/good")
"$evmctl" -a sha256 -k "$key" --keyid-from-cert "$pem" ima_sign "$work/good" > "$work/sign.log" 2>&1
[[ $(sha256sum "$work/good") == "$before" ]]
"$evmctl" -k "$der" ima_verify "$work/good" > "$work/verify.log" 2>&1
echo 'PASS unchanged contents and valid security.ima signature'
openssl req -new -x509 -newkey rsa:2048 -nodes -subj /CN=Wrong-Test-Key \
    -addext 'basicConstraints=critical,CA:FALSE' -addext keyUsage=digitalSignature \
    -addext subjectKeyIdentifier=hash -keyout "$work/wrong.key" -out "$work/wrong.pem" >/dev/null 2>&1
openssl x509 -in "$work/wrong.pem" -outform DER -out "$work/wrong.der"
set +e
"$evmctl" -k "$work/wrong.der" ima_verify "$work/good" > "$work/wrong.log" 2>&1
status=$?
set -e
[[ $status == 1 ]] || { cat "$work/wrong.log"; echo "Unexpected verification status $status"; exit 1; }
echo 'PASS wrong key rejected without verifier crash'
printf 'Modified content\n' > "$work/good"
set +e
"$evmctl" -k "$der" ima_verify "$work/good" > "$work/tampered.log" 2>&1
status=$?
set -e
[[ $status == 1 ]] || { cat "$work/tampered.log"; exit 1; }
echo 'PASS tampered contents rejected'
