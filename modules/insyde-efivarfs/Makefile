# SPDX-License-Identifier: GPL-2.0-only
KERNEL_RELEASE ?= $(shell uname -r)
KERNEL_BUILD ?= /usr/lib/modules/$(KERNEL_RELEASE)/build
RUST_TOOLCHAIN ?= $(shell cat $(KERNEL_BUILD)/rust-toolchain-name)
RUSTUP ?= $(shell command -v rustup)
RUST_TOOLCHAIN_ROOT ?=
RUSTC ?= $(if $(RUST_TOOLCHAIN_ROOT),$(RUST_TOOLCHAIN_ROOT)/$(RUST_TOOLCHAIN)-x86_64-unknown-linux-gnu/bin/rustc,$(shell $(RUSTUP) which --toolchain $(RUST_TOOLCHAIN) rustc))

.PHONY: all clean test
all:
	@test -s $(KERNEL_BUILD)/rust/libkernel.rmeta || { echo 'Missing kernel Rust metadata'; exit 1; }
	@test -x $(RUSTC) || { echo 'Missing matching Rust toolchain: $(RUST_TOOLCHAIN)'; exit 1; }
	$(MAKE) -C $(KERNEL_BUILD) M=$(CURDIR) LLVM=1 RUSTC=$(RUSTC) modules
clean:
	$(MAKE) -C $(KERNEL_BUILD) M=$(CURDIR) clean
test:
	@binary=$$(mktemp); trap 'rm -f "$$binary"' EXIT; \
	$(RUSTC) --edition=2024 --test model.rs -o "$$binary" && "$$binary"
