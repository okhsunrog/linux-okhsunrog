#!/usr/bin/env python3
"""Signed module autoload/TPM ordering in a secret-free systemd initrd.

Run as root with uv run: SCRIPT KERNEL_VERSION NEW_RUN_DIRECTORY [CASE...].
Cases: signed, unsigned, nvpcr, nvpcr-wrongref, nvpcr-wrongphase,
nvpcr-tampered, nvpcr-kexec, nvpcr-limited-default, nvpcr-limited-kexec.
Uses a fresh software TPM, disposable OVMF keys and guest disks only.
Does not emulate the Insyde enumeration bug or validate physical firmware.
"""
from pathlib import Path
import os
import base64
import json
import re
import select
import shutil
import subprocess
import sys
import time

version, directory, *cases = sys.argv[1:]
cases = cases or ['signed', 'unsigned']
if any(case not in ('signed', 'unsigned', 'nvpcr', 'nvpcr-wrongref', 'nvpcr-wrongphase', 'nvpcr-tampered', 'nvpcr-kexec', 'nvpcr-limited-default', 'nvpcr-limited-kexec') for case in cases):
    raise ValueError('Unknown acceptance case')
run = Path(directory).resolve()
run.mkdir(mode=0o700)
repo = Path(__file__).resolve().parent.parent
accel = os.environ.get('EFI_TEST_ACCEL', 'tcg' if any(case.startswith('nvpcr') for case in cases) else 'kvm')
if accel not in ('kvm', 'tcg'):
    raise ValueError('EFI_TEST_ACCEL must be kvm or tcg')


def command(args, *, cwd=None):
    with (run / 'build.log').open('ab') as log:
        subprocess.run(list(map(str, args)), cwd=cwd, stdout=log, stderr=log, check=True)


def archive(root, output):
    with output.open('wb') as dest, (run / 'build.log').open('ab') as log:
        find = subprocess.Popen(['find', '.', '-print0'], cwd=root, stdout=subprocess.PIPE)
        cpio = subprocess.Popen(['cpio', '--null', '-o', '-H', 'newc', '--quiet'], cwd=root,
                                stdin=find.stdout, stdout=subprocess.PIPE, stderr=log)
        find.stdout.close()
        zstd = subprocess.Popen(['zstd', '-q', '-T2'], stdin=cpio.stdout, stdout=dest, stderr=log)
        cpio.stdout.close()
        assert zstd.wait() == cpio.wait() == find.wait() == 0


empty = run / 'empty-conf'
empty.mkdir()
command(['dracut', '--force', '--conf', '/dev/null', '--confdir', empty,
         '--no-hostonly', '--no-hostonly-cmdline', '--no-early-microcode', '--no-hostonly-i18n',
         '--modules', 'base systemd systemd-initrd systemd-udevd systemd-modules-load '
                      'systemd-journald systemd-pcrextend insyde-efivarfs kernel-modules',
         '--drivers', 'insyde_efivarfs virtio_pci virtio_blk', '--compress', 'zstd',
         '--install', '/etc/keys/x509_ima.der /usr/bin/od /usr/bin/sed /usr/bin/grep '
                      '/usr/bin/cat /usr/bin/dmesg /usr/bin/udevadm /usr/bin/journalctl /usr/bin/systemctl '
                      '/usr/bin/systemd-analyze',
         run / 'base.img', '--kver', version])
root = run / 'guest'
root.mkdir()
command(['lsinitrd', '--unpack', run / 'base.img'], cwd=root)
# The generic dracut invocation must never import the production ZFS key.
if (root / 'etc/zfs/zroot.key').exists():
    raise RuntimeError('Secret imported into isolated fixture')
for config in (root / 'usr/lib/modules-load.d').glob('*.conf'):
    config.unlink()
units = root / 'etc/systemd/system'
units.mkdir(parents=True, exist_ok=True)
(units / 'efi-autoload-test.target').write_text('''[Unit]
Description=Isolated EFI autoload acceptance
DefaultDependencies=no
Wants=systemd-journald.service systemd-modules-load.service systemd-udev-trigger.service systemd-pcrphase-initrd.service systemd-tpm2-setup-early.service efi-autoload-test.service
''')
(units / 'efi-autoload-test.service').write_text('''[Unit]
DefaultDependencies=no
After=systemd-journald.service systemd-modules-load.service systemd-udev-trigger.service systemd-pcrphase-initrd.service systemd-tpm2-setup-early.service
[Service]
Type=oneshot
ExecStart=/usr/libexec/efi-autoload-test
StandardOutput=tty
StandardError=tty
TTYPath=/dev/ttyS0
''')
(root / 'usr/libexec').mkdir(exist_ok=True)
shutil.copyfile(repo / 'tests/efi-autoload-init', root / 'usr/libexec/efi-autoload-test')
(root / 'usr/libexec/efi-autoload-test').chmod(0o755)
(root / 'etc/udev/rules.d').mkdir(parents=True, exist_ok=True)
# Software TPM is a fixture, not the host's Intel MTL TPM. Exercise the same
# workaround through udev, without changing the host's manufacturer match.
(root / 'etc/udev/rules.d/99-tpm-fixture.rules').write_text(
    'SUBSYSTEM=="tpmrm", ENV{TPM2_BROKEN_NVPCR}="1"\n')
command(['systemd-analyze', '--root', root, 'verify', 'efi-autoload-test.target',
         'efi-autoload-test.service', 'systemd-modules-load.service', 'systemd-tpm2-setup-early.service'])
command(['openssl', 'req', '-new', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2',
         '-subj', '/CN=Disposable EFI autoload fixture', '-addext', 'keyUsage=digitalSignature',
         '-addext', 'basicConstraints=critical,CA:FALSE', '-addext', 'subjectKeyIdentifier=hash',
         '-keyout', run / 'fixture.key', '-out', run / 'fixture.pem'])
command(['openssl', 'pkey', '-in', run / 'fixture.key', '-pubout', '-out', run / 'pcr-public.pem'])
command(['uv', 'tool', 'run', '--from', 'virt-firmware', 'virt-fw-vars',
         '--input', '/usr/share/edk2/x64/OVMF_VARS.4m.fd', '--output', run / 'vars-template.fd',
         '--enroll-cert', run / 'fixture.pem', '--no-microsoft', '--secure-boot'])
command(['uv', 'tool', 'run', '--from', 'virt-firmware', 'virt-fw-vars',
         '--inplace', run / 'vars-template.fd', '--add-db',
         '772c0f6d-feb6-4e5e-aa90-790cfc82d1b2', run / 'fixture.pem',
         '--append-boot-filepath', r'\EFI\BOOT\BOOTX64.EFI'])
module = root / f'usr/lib/modules/{version}/updates/dkms/insyde_efivarfs.ko.zst'
signed_module = module.read_bytes()
stub = Path('/usr/lib/systemd/boot/efi/linuxx64.efi.stub')
headers = subprocess.check_output(['objdump', '-p', stub], text=True)
image_base = int(re.search(r'ImageBase\s+([0-9a-f]+)', headers).group(1), 16)
cmdline_address = image_base + 0x30000
initrd_address = image_base + 0x1000000
for case in cases:
    local_definitions = root / 'etc/nvpcr'
    if local_definitions.exists():
        shutil.rmtree(local_definitions)
    if case == 'nvpcr-limited-kexec':
        shutil.copytree(repo / 'modules/insyde-efivarfs/nvpcr', local_definitions)
    for name in ('tpm2-pcr-public-key.pem', 'tpm2-pcr-signature.json'):
        (root / 'etc/systemd' / name).unlink(missing_ok=True)
    module.write_bytes(signed_module)
    workaround = 0 if case.startswith('nvpcr') else 1
    (root / 'etc/udev/rules.d/99-tpm-fixture.rules').write_text(
        f'SUBSYSTEM=="tpmrm", ENV{{TPM2_BROKEN_NVPCR}}="{workaround}"\n')
    if case == 'unsigned':
        raw = subprocess.check_output(['zstd', '-d', '-c', module])
        marker = b'~Module signature appended~\n'
        assert raw.endswith(marker)
        info = raw[-len(marker)-12:-len(marker)]
        size = int.from_bytes(info[-4:], 'big')
        assert 0 < size < len(raw) - len(marker) - 12
        raw = raw[:-len(marker)-12-size]
        module.write_bytes(subprocess.check_output(['zstd', '-q', '-c'], input=raw))
    initrd = run / f'{case}.img'
    archive(root, initrd)
    target_initrd = initrd
    if case in ('nvpcr-kexec', 'nvpcr-limited-kexec'):
        loader = run / f'{case}-loader'
        loader.mkdir()
        command(['dracut', '--force', '--conf', '/dev/null', '--confdir', empty,
                 '--no-hostonly', '--no-hostonly-cmdline', '--no-early-microcode', '--no-hostonly-i18n',
                 '--modules', 'base bash busybox kernel-modules',
                 '--drivers', 'virtio_pci virtio_blk vfat', '--compress', 'zstd',
                 '--install', '/etc/keys/x509_ima.der /usr/bin/kexec /usr/bin/setfattr /usr/bin/keyctl '
                              '/usr/bin/od /usr/bin/tr /usr/bin/grep /usr/bin/mountpoint '
                              '/usr/bin/mount /usr/bin/mkdir /usr/bin/cp /usr/bin/modprobe '
                              '/usr/local/libexec/zbm-load-ima-policy',
                 '--include', '/etc/zfsbootmenu/ima-policy', '/etc/zbm-ima-policy',
                 '--include', '/etc/zfsbootmenu/ima-policy.sig', '/etc/zbm-ima-policy.sig',
                 run / 'loader-base.img', '--kver', version])
        command(['lsinitrd', '--unpack', run / 'loader-base.img'], cwd=loader)
        if (loader / 'etc/zfs/zroot.key').exists():
            raise RuntimeError('Secret imported into loader fixture')
        command(['install', '-m755', repo / 'tests/nvpcr-kexec-init', loader / 'init'])
        initrd = run / 'loader.img'
        archive(loader, initrd)
    cmdline = run / 'cmdline'
    cmdline.write_bytes(('console=ttyS0,115200 loglevel=6 panic=-1 '
                        f'rd.systemd.unit=efi-autoload-test.target autoload_case={case}\0').encode())
    # Build from the plain stub; re-adding resources gives correct PE VirtualSize.
    linux_address = (initrd_address + initrd.stat().st_size + 4095) & ~4095
    unsigned = run / 'unsigned.efi'
    command(['objcopy', '--add-section', f'.initrd={initrd}', '--add-section', f'.cmdline={cmdline}',
             '--add-section', f'.linux=/usr/lib/modules/{version}/vmlinuz',
             '--change-section-vma', f'.initrd=0x{initrd_address:x}',
             '--change-section-vma', f'.cmdline=0x{cmdline_address:x}',
             '--change-section-vma', f'.linux=0x{linux_address:x}',
             '--set-section-flags', '.initrd=contents,alloc,load,readonly,data',
             '--set-section-flags', '.cmdline=contents,alloc,load,readonly,data',
             '--set-section-flags', '.linux=contents,alloc,load,readonly,data',
             stub, unsigned])
    if case.startswith('nvpcr'):
        policy_image = run / 'policy.efi'
        command(['uv', 'run', '--no-project', repo / 'boot/pcr-policy.py', unsigned, policy_image,
                 '--private-key', run / 'fixture.key', '--public-key', run / 'pcr-public.pem',
                 '--signature', run / 'policy.json',
                 '--policyref', 'wrong-reference' if case == 'nvpcr-wrongref' else 'initrd',
                 '--phase', 'enter-initrd:leave-initrd' if case == 'nvpcr-wrongphase' else 'enter-initrd'])
        unsigned = policy_image
        if case == 'nvpcr-tampered':
            old_policy = (run / 'policy.json').read_bytes()
            signature = json.loads(old_policy)['sha256'][0]['sig']
            decoded = bytearray(base64.b64decode(signature))
            decoded[0] ^= 1
            changed = base64.b64encode(decoded)
            new_policy = old_policy.replace(signature.encode(), changed, 1)
            payload = unsigned.read_bytes()
            assert len(new_policy) == len(old_policy) and payload.count(old_policy) == 1
            unsigned.write_bytes(payload.replace(old_policy, new_policy, 1))
    if case in ('nvpcr-kexec', 'nvpcr-limited-kexec'):
        # The policy authenticates the first-stage UKI; embedding it into the
        # independent second initramfs creates no PCR/hash/signature cycle.
        shutil.copyfile(run / 'policy.json', root / 'etc/systemd/tpm2-pcr-signature.json')
        shutil.copyfile(run / 'pcr-public.pem', root / 'etc/systemd/tpm2-pcr-public-key.pem')
        archive(root, target_initrd)
        command(['/usr/local/sbin/zbm-sign-initramfs', target_initrd])
        signature = subprocess.check_output(['getfattr', '--only-values', '-n', 'security.ima', target_initrd])
        (run / 'target.img.sig').write_bytes(signature)
    efi = run / f'{case}.efi'
    command(['sbsign', '--key', run / 'fixture.key', '--cert', run / 'fixture.pem',
             '--output', efi, unsigned])
    disk = run / f'{case}.fat'
    with disk.open('wb') as file:
        file.truncate(256 * 1024 * 1024)
    command(['sgdisk', '--clear', '--new=1:2048:0', '--typecode=1:ef00', disk])
    command(['mkfs.vfat', '-F', '32', '--offset', '2048', disk, '261103'])
    filesystem = f'{disk}@@1048576'
    command(['mmd', '-i', filesystem, '::/EFI', '::/EFI/BOOT'])
    command(['mcopy', '-i', filesystem, efi, '::/EFI/BOOT/BOOTX64.EFI'])
    if case in ('nvpcr-kexec', 'nvpcr-limited-kexec'):
        command(['mmd', '-i', filesystem, '::/fixtures'])
        command(['mcopy', '-i', filesystem, target_initrd, '::/fixtures/target.img'])
        command(['mcopy', '-i', filesystem, run / 'target.img.sig', '::/fixtures/target.img.sig'])
        command(['mcopy', '-i', filesystem, f'/usr/lib/modules/{version}/vmlinuz', '::/fixtures/kernel'])
    variables = run / f'{case}-vars.fd'
    shutil.copyfile(run / 'vars-template.fd', variables)
    tpm_state = run / f'{case}-tpm'
    tpm_state.mkdir()
    socket = run / f'{case}-tpm.sock'
    data_socket = run / f'{case}-tpm-data.sock'
    with (run / f'{case}-swtpm.log').open('wb') as log:
        tpm = subprocess.Popen(['swtpm', 'socket', '--tpm2', '--tpmstate', f'dir={tpm_state}',
                                '--ctrl', f'type=unixio,path={socket}',
                                '--server', f'type=unixio,path={data_socket}', '--flags', 'not-need-init'],
                               stdout=log, stderr=log)
        proc = None
        output = bytearray()
        try:
            deadline = time.monotonic() + 10
            while not socket.exists():
                if tpm.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('Software TPM did not start')
                time.sleep(0.05)
            if case.startswith('nvpcr-limited'):
                # Explicit fixture TCTI on EVERY command: never access the host TPM.
                # Use a temporary seed socket whose control channel has the name
                # expected by tcti-swtpm, then restart with QEMU's control socket.
                tpm.terminate()
                tpm.wait(timeout=10)
                socket.unlink(missing_ok=True)
                data_socket.unlink(missing_ok=True)
                seed_control = Path(str(data_socket) + '.ctrl')
                tpm = subprocess.Popen(['swtpm', 'socket', '--tpm2', '--tpmstate', f'dir={tpm_state}',
                                        '--ctrl', f'type=unixio,path={seed_control}',
                                        '--server', f'type=unixio,path={data_socket}',
                                        '--flags', 'not-need-init'], stdout=log, stderr=log)
                deadline = time.monotonic() + 10
                while not seed_control.exists() or not data_socket.exists():
                    if tpm.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError('Seed software TPM did not start')
                    time.sleep(0.05)
                tcti = f'swtpm:path={data_socket}'
                command(['tpm2_startup', '-T', tcti, '-c'])
                for index in (0x01d10200, 0x01d10201):
                    command(['tpm2_nvdefine', '-T', tcti, hex(index), '-C', 'o',
                             '-s', '32', '-g', 'sha256', '-a', '0x0c060046'])
                for n in range(256):
                    result = subprocess.run(['tpm2_nvdefine', '-T', tcti, hex(0x01d11000+n),
                                             '-C', 'o', '-s', '32', '-g', 'sha256', '-a', '0x0c060046'],
                                            capture_output=True, text=True)
                    if result.returncode:
                        if '0x0000014b' not in result.stderr:
                            raise RuntimeError(f'Unexpected seed TPM error: {result.stderr}')
                        (run / f'{case}-seed.txt').write_text(
                            f'Legacy hardware/cryptsetup plus {n} RAM filler indices; NV_SPACE confirmed.\n'
                            + result.stderr)
                        break
                else:
                    raise RuntimeError('Fixture did not exhaust orderly NV space')
                command(['tpm2_shutdown', '-T', tcti, '-c'])
                tpm.terminate()
                tpm.wait(timeout=10)
                data_socket.unlink(missing_ok=True)
                seed_control.unlink(missing_ok=True)
                tpm = subprocess.Popen(['swtpm', 'socket', '--tpm2', '--tpmstate', f'dir={tpm_state}',
                                        '--ctrl', f'type=unixio,path={socket}', '--flags', 'not-need-init'],
                                       stdout=log, stderr=log)
                deadline = time.monotonic() + 10
                while not socket.exists():
                    if tpm.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError('Seeded software TPM did not restart')
                    time.sleep(0.05)
            proc = subprocess.Popen([
                'qemu-system-x86_64', '-machine', f'q35,accel={accel},smm=on',
                '-cpu', 'host' if accel == 'kvm' else 'max',
                '-global', 'driver=cfi.pflash01,property=secure,value=on', '-m', '2048', '-smp', '1',
                '-nodefaults', '-nographic', '-no-reboot',
                '-monitor', f'unix:{run / "monitor.sock"},server=on,wait=off', '-serial', 'stdio',
                '-smbios', 'type=0,vendor=INSYDE Corp.,version=03.07',
                '-smbios', 'type=1,manufacturer=Framework,product=Laptop 13 (Intel Core Ultra Series 1)',
                '-drive', 'if=pflash,format=raw,unit=0,readonly=on,file=/usr/share/edk2/x64/OVMF_CODE.secboot.4m.fd',
                '-drive', f'if=pflash,format=raw,unit=1,file={variables}',
                '-drive', f'if=none,format=raw,id=esp,file={disk}',
                '-device', 'virtio-blk-pci,drive=esp,bootindex=1',
                '-chardev', f'socket,id=chrtpm,path={socket}',
                '-tpmdev', 'emulator,id=tpm0,chardev=chrtpm', '-device', 'tpm-tis,tpmdev=tpm0',
            ], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if select.select([proc.stdout], [], [], 0.2)[0]:
                    data = proc.stdout.read1(65536)
                    if not data:
                        break
                    output.extend(data)
                    (run / f'{case}.log').write_bytes(output)
                    if b'EFI_AUTOLOAD_FAIL=' in output or f'EFI_AUTOLOAD_PASS={case}'.encode() in output:
                        break
                if proc.poll() is not None:
                    break
            if f'EFI_AUTOLOAD_PASS={case}'.encode() not in output or b'EFI_AUTOLOAD_FAIL=' in output:
                raise RuntimeError(f'Guest acceptance failed: {case}; see {run / f"{case}.log"}')
            print(f'{case}: Secure Boot, autoload ordering, TPM policy: PASS', flush=True)
        finally:
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.wait()
            if tpm.poll() is None:
                tpm.terminate()
                tpm.wait(timeout=10)
            (run / f'{case}.log').write_bytes(output)
module.write_bytes(signed_module)
