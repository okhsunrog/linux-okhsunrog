#!/usr/bin/env python3
"""Manual isolated guest test. Run with uv run and prepared test initramfs."""
import select
import subprocess
import sys
import time
from pathlib import Path
kernel, initrd, logdir = sys.argv[1:]
Path(logdir).mkdir(parents=True, exist_ok=True)
for case in ("zbm", "dracut", "source"):
    proc = subprocess.Popen([
        "qemu-system-x86_64", "-machine", "q35,accel=kvm", "-cpu", "host",
        "-m", "1024", "-smp", "2", "-nodefaults", "-nographic", "-no-reboot",
        "-monitor", "none", "-serial", "stdio", "-kernel", kernel, "-initrd", initrd,
        "-append", f"console=ttyS0 quiet panic=-1 recovery_test={case}",
    ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = bytearray()
    sent = False
    deadline = time.monotonic() + 30
    try:
        while time.monotonic() < deadline:
            if select.select([proc.stdout], [], [], 0.2)[0]:
                data = proc.stdout.read1(65536)
                if not data:
                    break
                output.extend(data)
                if not sent and b"[R] reboot  [P] power off:" in output:
                    proc.stdin.write(b"R")
                    proc.stdin.flush()
                    sent = True
            if proc.poll() is not None:
                break
        proc.wait(timeout=2)
        if not sent or proc.returncode != 0 or b"RECOVERY_TEST_UNEXPECTED_RETURN" in output:
            raise RuntimeError(f"Recovery failure path did not stop safely: {case}")
        print(f"{case}: denied shell, diagnostic and reboot: PASS")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        Path(logdir, f"{case}.log").write_bytes(output)
