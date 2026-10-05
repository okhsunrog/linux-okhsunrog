#!/bin/bash
set -euo pipefail
[[ $# == 1 ]] || { echo 'usage: build-evmctl.sh DESTINATION' >&2; exit 2; }
dest=$1
revision=df48a723c8a8ca5d3e41343644d199ba690d5ffe
git clone --quiet https://github.com/linux-integrity/ima-evm-utils.git "$dest"
git -C "$dest" checkout --quiet "$revision"
cd "$dest"
./autogen.sh
./configure --disable-manpages
make -j2
