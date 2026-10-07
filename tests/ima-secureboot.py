#!/usr/bin/env python3
"""Local signed-policy acceptance with isolated OVMF variables and fixture EFI key.
Run as root via uv run: SCRIPT UNSIGNED_UKI SIGNED_TARGET_KERNEL NEW_RUN_DIRECTORY.
The local signing helper signs the public second-stage initramfs outside the guest.
No host disks/firmware variables or private keys are passed to QEMU.
"""
import os
from pathlib import Path
import re
import select
import shutil
import subprocess
import sys
import time

image, kernel, run = map(lambda value: Path(value).resolve(), sys.argv[1:])
run.mkdir(mode=0o700)
repo = Path(__file__).resolve().parent.parent

def command(args, *, cwd=None):
    with (run / 'build.log').open('ab') as log:
        subprocess.run(list(map(str, args)), cwd=cwd, stdout=log, stderr=log, check=True)

def archive(root, output):
    # A trusted fixture directory; no interpolated shell paths or key inputs.
    with output.open('wb') as dest, (run / 'build.log').open('ab') as log:
        find = subprocess.Popen(['find', '.', '-print0'], cwd=root, stdout=subprocess.PIPE)
        cpio = subprocess.Popen(['cpio', '--null', '-o', '-H', 'newc', '--quiet'], cwd=root,
                                stdin=find.stdout, stdout=subprocess.PIPE, stderr=log)
        find.stdout.close()
        zstd = subprocess.Popen(['zstd', '-q', '-T2'], stdin=cpio.stdout, stdout=dest, stderr=log)
        cpio.stdout.close()
        assert zstd.wait() == cpio.wait() == find.wait() == 0

root = run / 'guest'
root.mkdir()
command(['objcopy', '--dump-section', f'.initrd={run / "base.img"}',
         '--dump-section', f'.linux={run / "linux.bin"}', image, run / 'inspect.efi'])
command(['objcopy', '--remove-section', '.initrd', '--remove-section', '.linux',
         '--remove-section', '.cmdline', image, run / 'stub.efi'])
command(['lsinitrd', '--unpack', run / 'base.img'], cwd=root)
command(['install', '-m755', repo / 'tests/ima-secureboot-init', root / 'init'])
second = run / 'second'
for directory in ('bin', 'proc', 'sys', 'dev'):
    (second / directory).mkdir(parents=True, exist_ok=True)
busybox = Path(shutil.which('busybox'))
for binary in (busybox, Path(shutil.which('od'))):
    shutil.copyfile(binary, second / 'bin' / binary.name)
    (second / 'bin' / binary.name).chmod(0o755)
    dependencies = subprocess.run(['ldd', binary], capture_output=True, text=True)
    if dependencies.returncode and 'not a dynamic executable' not in dependencies.stderr:
        raise RuntimeError(dependencies.stderr)
    for path in re.findall(r'(/[^\s()]+)', dependencies.stdout):
        target = second / path.lstrip('/')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        target.chmod(0o755)
for applet in ('sh', 'mount', 'mkdir', 'cat', 'tr', 'poweroff'):
    (second / 'bin' / applet).symlink_to('busybox')
(second / 'etc/keys').mkdir(parents=True)
shutil.copyfile(root / 'etc/keys/x509_ima.der', second / 'etc/keys/x509_ima.der')
(second / 'init').write_text('''#!/bin/sh
export PATH=/bin
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mkdir -p /sys/firmware/efi/efivars
mount -t efivarfs efivarfs /sys/firmware/efi/efivars
state=$(cat /sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c | od -An -tu1 -j4 -N1 | tr -d ' \\n')
echo "IMA_SB_SECOND_STATE=$state"
echo IMA_SB_SECOND_KERNEL_BOOTED
poweroff -f
''')
(second / 'init').chmod(0o755)
archive(second, run / 'second.img')
command(['/usr/local/sbin/zbm-sign-initramfs', run / 'second.img'])
# Transport signature separately because cpio cannot preserve security.ima.
signature = subprocess.check_output(['getfattr', '--only-values', '-n', 'security.ima', run / 'second.img'])
(run / 'second.img.sig').write_bytes(signature)
(root / 'fixtures').mkdir(exist_ok=True)
shutil.copyfile(kernel, root / 'fixtures/kernel')
shutil.copyfile(run / 'second.img', root / 'fixtures/second.img')
shutil.copyfile(run / 'second.img.sig', root / 'fixtures/second.img.sig')
command(['openssl', 'req', '-new', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2',
         '-subj', '/CN=Disposable OVMF fixture', '-addext', 'keyUsage=digitalSignature',
         '-addext', 'basicConstraints=critical,CA:FALSE', '-addext', 'subjectKeyIdentifier=hash',
         '-keyout', run / 'fixture.key', '-out', run / 'fixture.pem'])
command(['uv', 'tool', 'run', '--from', 'virt-firmware', 'virt-fw-vars',
         '--input', '/usr/share/edk2/x64/OVMF_VARS.4m.fd', '--output', run / 'vars-template.fd',
         '--enroll-cert', run / 'fixture.pem', '--no-microsoft', '--secure-boot'])
command(['uv', 'tool', 'run', '--from', 'virt-firmware', 'virt-fw-vars',
         '--inplace', run / 'vars-template.fd', '--add-db',
         '772c0f6d-feb6-4e5e-aa90-790cfc82d1b2', run / 'fixture.pem',
         '--append-boot-filepath', r'\EFI\BOOT\BOOTX64.EFI'])
headers = subprocess.check_output(['objdump', '-h', image], text=True)
match = re.search(r'\s\.initrd\s+[0-9a-f]+\s+([0-9a-f]+)', headers)
assert match
initrd_address = int(match.group(1), 16)
cmdline_match = re.search(r'\s\.cmdline\s+[0-9a-f]+\s+([0-9a-f]+)', headers)
assert cmdline_match
cmdline_address = int(cmdline_match.group(1), 16)
original_policy = (root / 'etc/zbm-ima-policy').read_bytes()
original_signature = (root / 'etc/zbm-ima-policy.sig').read_bytes()
for case in ('good', 'raw', 'tampered', 'wrong-key', 'missing-signature'):
    if case == 'raw':
        for fixture in (root / 'fixtures').iterdir():
            fixture.unlink()
    (root / 'etc/zbm-ima-policy').write_bytes(original_policy)
    (root / 'etc/zbm-ima-policy.sig').write_bytes(original_signature)
    if case == 'tampered':
        (root / 'etc/zbm-ima-policy').write_bytes(original_policy + b'\n# changed after signing\n')
    elif case == 'wrong-key':
        env = os.environ | {'LD_LIBRARY_PATH': '/usr/local/libexec/zbm-ima-tools'}
        with (run / 'build.log').open('ab') as log:
            subprocess.run(['/usr/local/libexec/zbm-ima-tools/evmctl', '-a', 'sha256',
                            '-k', str(run / 'fixture.key'), '--keyid-from-cert', str(run / 'fixture.pem'),
                            '--sigfile', 'ima_sign', str(root / 'etc/zbm-ima-policy')],
                           env=env, stdout=log, stderr=log, check=True)
    elif case == 'missing-signature':
        (root / 'etc/zbm-ima-policy.sig').unlink()
    initrd = run / f'{case}.img'
    archive(root, initrd)
    cmdline = run / 'cmdline'
    cmdline.write_bytes(f'console=ttyS0,115200 earlycon=uart8250,io,0x3f8,115200 loglevel=7 panic=-1 policy_test={case}\0'.encode())
    linux_address = (initrd_address + initrd.stat().st_size + 4095) & ~4095
    unsigned = run / 'unsigned.efi'
    # PE update-section can leave old VirtualSize values. Re-add all resources.
    command(['objcopy', '--add-section', f'.initrd={initrd}', '--add-section', f'.cmdline={cmdline}',
             '--add-section', f'.linux={run / "linux.bin"}',
             '--change-section-vma', f'.initrd=0x{initrd_address:x}',
             '--change-section-vma', f'.cmdline=0x{cmdline_address:x}',
             '--change-section-vma', f'.linux=0x{linux_address:x}',
             '--set-section-flags', '.initrd=contents,alloc,load,readonly,data',
             '--set-section-flags', '.cmdline=contents,alloc,load,readonly,data',
             '--set-section-flags', '.linux=contents,alloc,load,readonly,data',
             run / 'stub.efi', unsigned])
    esp = run / f'esp-{case}' / 'EFI/BOOT'
    esp.mkdir(parents=True)
    command(['sbsign', '--key', run / 'fixture.key', '--cert', run / 'fixture.pem',
             '--output', esp / 'BOOTX64.EFI', unsigned])
    disk = run / f'esp-{case}.fat'
    with disk.open('wb') as file:
        file.truncate(256 * 1024 * 1024)
    command(['sgdisk', '--clear', '--new=1:2048:0', '--typecode=1:ef00', disk])
    command(['mkfs.vfat', '-F', '32', '--offset', '2048', disk, '261103'])
    filesystem = f'{disk}@@1048576'
    command(['mmd', '-i', filesystem, '::/EFI', '::/EFI/BOOT'])
    command(['mcopy', '-i', filesystem, esp / 'BOOTX64.EFI', '::/EFI/BOOT/BOOTX64.EFI'])
    shutil.copyfile(run / 'vars-template.fd', run / f'vars-{case}.fd')
    proc = subprocess.Popen([
        'qemu-system-x86_64', '-machine', 'q35,accel=kvm,smm=on', '-cpu', 'host',
        '-global', 'driver=cfi.pflash01,property=secure,value=on', '-m', '2048', '-smp', '2',
        '-nodefaults', '-nographic', '-no-reboot', '-monitor', 'none', '-serial', 'stdio',
        '-drive', 'if=pflash,format=raw,unit=0,readonly=on,file=/usr/share/edk2/x64/OVMF_CODE.secboot.4m.fd',
        '-drive', f'if=pflash,format=raw,unit=1,file={run / f"vars-{case}.fd"}',
        '-drive', f'if=none,format=raw,id=esp,file={disk}',
        '-device', 'virtio-blk-pci,drive=esp,bootindex=1',
    ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = bytearray()
    sent = False
    completed = False
    deadline = time.monotonic() + 120
    try:
        while time.monotonic() < deadline:
            if select.select([proc.stdout], [], [], 0.2)[0]:
                data = proc.stdout.read1(65536)
                if not data:
                    break
                output.extend(data)
                (run / f'{case}.log').write_bytes(output)
                if not sent and b'[R] reboot  [P] power off:' in output:
                    proc.stdin.write(b'P')
                    proc.stdin.flush()
                    sent = True
                if case == 'good' and b'IMA_SB_SECOND_KERNEL_BOOTED' in output:
                    completed = True
                    break
                if case == 'raw' and b'IMA_SB_RAW_POLICY_REJECTED' in output:
                    completed = True
                    break
                if case not in ('good', 'raw') and sent and b'reboot: Power down' in output:
                    completed = True
                    break
            if proc.poll() is not None:
                break
        if not completed:
            proc.wait(timeout=5)
        if (not completed and proc.returncode) or b'IMA_SB_STATE=1' not in output or b'IMA_SB_UNEXPECTED' in output:
            raise RuntimeError(f'Invalid Secure Boot result: {case}')
        if case == 'good':
            for marker in (b'IMA_SB_SIGNED_POLICY_LOADED', b'IMA_SB_UNSIGNED_INITRAMFS_REJECTED',
                           b'IMA_SB_LEGACY_KEXEC_REJECTED', b'IMA_SB_SIGNED_INITRAMFS_ACCEPTED',
                           b'IMA_SB_SECOND_STATE=1', b'IMA_SB_SECOND_KERNEL_BOOTED'):
                if marker not in output:
                    raise RuntimeError(f'Missing {marker!r}')
        elif case == 'raw':
            if b'IMA_SB_RAW_POLICY_REJECTED' not in output:
                raise RuntimeError('Raw policy accepted')
        elif not sent or b'IMA_SB_SIGNED_POLICY_LOADED' in output:
            raise RuntimeError(f'Policy failure did not stop safely: {case}')
        print(f'{case}: firmware Secure Boot enabled, expected result: PASS', flush=True)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        (run / f'{case}.log').write_bytes(output)
