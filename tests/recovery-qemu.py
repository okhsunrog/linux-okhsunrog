#!/usr/bin/env python3
"""Manual isolated guest test. Run with uv run and prepared test initramfs."""
import select
import subprocess
import sys
import time
from pathlib import Path
kernel, initrd, logdir = sys.argv[1:]
Path(logdir).mkdir(parents=True, exist_ok=True)
for case in ("zbm", "dracut", "source", "discovery", "discovery-mount", "discovery-pair"):
    proc = subprocess.Popen([
        "qemu-system-x86_64", "-machine", "q35,accel=kvm", "-cpu", "host",
        "-m", "2048", "-smp", "2", "-nodefaults", "-nographic", "-no-reboot",
        "-monitor", "none", "-serial", "stdio", "-kernel", kernel, "-initrd", initrd,
        "-append", f"console=ttyS0 quiet panic=-1 recovery_test={case}",
    ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = bytearray()
    sent = False
    attempts = 0
    prompt = b"Enter passphrase for 'novafs':"
    deadline = time.monotonic() + 45
    try:
        while time.monotonic() < deadline:
            if select.select([proc.stdout], [], [], 0.2)[0]:
                data = proc.stdout.read1(65536)
                if not data:
                    break
                output.extend(data)
                if case.startswith("discovery"):
                    while attempts < output.count(prompt):
                        password = (b"wrong-fixture-passphrase" if case == "discovery"
                                    and not sent else b"fixture-passphrase-only")
                        # The terminal may flush pending input while changing modes.
                        time.sleep(0.15)
                        proc.stdin.write(password + b"\n")
                        proc.stdin.flush()
                        attempts += 1
                    if not sent and b"[M] retry boot menu" in output:
                        expected = {"discovery": b"Key not loaded:",
                                    "discovery-mount": b"Mount failed:",
                                    "discovery-pair": b"No matching kernel/initramfs pair:"}[case]
                        if expected not in output:
                            raise RuntimeError(f"Missing discovery failure reason: {case}")
                        proc.stdin.write(b"M")
                        proc.stdin.flush()
                        sent = True
                    continue
                if not sent and b"[R] reboot  [P] power off:" in output:
                    proc.stdin.write(b"R")
                    proc.stdin.flush()
                    sent = True
            if proc.poll() is not None:
                break
        proc.wait(timeout=2)
        if not sent or proc.returncode != 0 or b"RECOVERY_TEST_UNEXPECTED_RETURN" in output:
            raise RuntimeError(f"Recovery failure path did not stop safely: {case}")
        if case.startswith("discovery"):
            if b"RECOVERY_RETRY_PASS" not in output:
                raise RuntimeError(f"Discovery retry failed: {case}")
            print(f"{case}: reason displayed, menu retry, Arch rediscovered: PASS")
        else:
            if b"[M] retry boot menu" in output:
                raise RuntimeError(f"Early failure unexpectedly permits menu retry: {case}")
            print(f"{case}: denied shell, diagnostic and reboot: PASS")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        Path(logdir, f"{case}.log").write_bytes(output)
