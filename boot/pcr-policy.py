#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Embed/sign a single-profile ZBM UKI's early-boot PCR 11 policy.

Run via uv run. The private key is opened only by systemd-measure; it is never
copied into a PE section/initrd. The caller signs the resulting EFI image last.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

MEASURE = '/usr/lib/systemd/systemd-measure'
RESOURCES = ('linux', 'osrel', 'cmdline', 'initrd', 'ucode', 'splash', 'dtb',
             'dtbauto', 'uname', 'sbat', 'pcrpkey', 'hwids', 'efifw')


def pe_sections(path, *, require_linux=True):
    data = Path(path).read_bytes()
    if data[:2] != b'MZ':
        raise ValueError('Not a PE image')
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    if data[pe:pe+4] != b'PE\0\0':
        raise ValueError('Invalid PE header')
    count = struct.unpack_from('<H', data, pe+6)[0]
    optional_size = struct.unpack_from('<H', data, pe+20)[0]
    opt = pe+24
    if struct.unpack_from('<H', data, opt)[0] != 0x20b:
        raise ValueError('Expected PE32+ UKI')
    image_base = struct.unpack_from('<Q', data, opt+24)[0]
    alignment = struct.unpack_from('<I', data, opt+32)[0]
    cert_size = struct.unpack_from('<I', data, opt+112+4*8+4)[0]
    sections = {}
    for i in range(count):
        offset = opt+optional_size+i*40
        name = data[offset:offset+8].rstrip(b'\0').decode('ascii')
        size, address, raw_size, raw = struct.unpack_from('<IIII', data, offset+8)
        if name in sections or raw+raw_size > len(data):
            raise ValueError(f'Unsupported/truncated PE section: {name}')
        if size > raw_size and (name[1:] in RESOURCES or name == '.pcrsig'):
            raise ValueError(f'Zero-filled UKI resource is unsupported: {name}')
        # Hash the in-memory VirtualSize, not file-alignment padding.
        sections[name] = (data[raw:raw+size], address, size)
    if '.profile' in sections or (require_linux and '.linux' not in sections) or not alignment:
        raise ValueError('Only a single-profile UKI is supported')
    return image_base, alignment, cert_size, sections


def align(value, alignment):
    return (value+alignment-1)//alignment*alignment


def sign(resources, work, private_key, public_key, phase, policyref, append=None):
    args = [MEASURE, 'sign', '--bank=sha256', f'--phase={phase}', f'--policyref={policyref}',
            f'--private-key={private_key}', f'--public-key={public_key}', '--json=short']
    for name, data in resources.items():
        file = work / name
        file.write_bytes(data)
        args.append(f'--{name}={file}')
    if append:
        args.append(f'--append={append}')
    result = subprocess.check_output(args)
    policy = json.loads(result)
    if not policy.get('sha256'):
        raise ValueError('No SHA-256 PCR policy produced')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--private-key', required=True, type=Path)
    parser.add_argument('--public-key', required=True, type=Path)
    parser.add_argument('--signature', required=True, type=Path)
    parser.add_argument('--append', type=Path)
    parser.add_argument('--sign-existing', type=Path,
                        help='Also authorize the currently trusted backup EFI image')
    parser.add_argument('--phase', default='enter-initrd')
    parser.add_argument('--policyref', default='initrd')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='zbm-pcr-') as directory:
        work = Path(directory)
        image_base, alignment, cert_size, sections = pe_sections(args.image)
        resources = {name: sections['.'+name][0] for name in RESOURCES if '.'+name in sections}
        resources['pcrpkey'] = args.public_key.read_bytes()
        previous = args.append
        if args.sign_existing:
            old = pe_sections(args.sign_existing)[3]
            old_resources = {name: old['.'+name][0] for name in RESOURCES if '.'+name in old}
            previous = work / 'previous.json'
            previous.write_bytes(sign(old_resources, work, args.private_key, args.public_key,
                                      args.phase, args.policyref, args.append))
        policy = sign(resources, work, args.private_key, args.public_key,
                      args.phase, args.policyref, previous)
        source = work / 'source.efi'
        shutil.copyfile(args.image, source)
        if cert_size:
            subprocess.run(['sbattach', '--remove', source], check=True)
        # .sbat belongs to the linked stub; retain its original address/layout.
        remove = [name for name in sections if (name[1:] in RESOURCES and name != '.sbat') or name == '.pcrsig']
        base = work / 'stub.efi'
        subprocess.run(['objcopy', *[f'--remove-section={name}' for name in remove], source, base], check=True)
        base_sections = pe_sections(base, require_linux=False)[3]
        offset = align(max(address+size for _, address, size in base_sections.values()), alignment)
        # Kernel last: its decompressor must not overwrite following resources.
        order = [name for name in resources if name not in ('linux', 'initrd', 'sbat')]
        order += ['pcrsig', 'initrd', 'linux']
        args_objcopy = ['objcopy']
        for name in order:
            if name != 'pcrsig' and name not in resources:
                continue
            data = policy if name == 'pcrsig' else resources[name]
            file = work / ('embed-'+name)
            file.write_bytes(data)
            args_objcopy += ['--add-section', f'.{name}={file}',
                             '--change-section-vma', f'.{name}=0x{image_base+offset:x}',
                             '--set-section-flags', f'.{name}=contents,alloc,load,readonly,data']
            offset = align(offset+len(data), alignment)
        output = work / 'policy.efi'
        subprocess.run([*args_objcopy, base, output], check=True)
        after = pe_sections(output)[3]
        for name, data in resources.items():
            if after['.'+name][0] != data:
                raise ValueError(f'PE payload changed: {name}')
        if after['.pcrsig'][0] != policy:
            raise ValueError('Incorrect PCR signature section')
        # Both destinations should be staging paths, never the live ESP image.
        for destination, payload in ((args.output, output.read_bytes()), (args.signature, policy)):
            fd, temporary = tempfile.mkstemp(prefix='.'+destination.name+'-', dir=destination.parent)
            try:
                with os.fdopen(fd, 'wb') as file:
                    file.write(payload)
                    file.flush()
                    os.fsync(file.fileno())
                os.chmod(temporary, 0o644)
                os.replace(temporary, destination)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
    print(f'PCR 11 policy embedded: phase={args.phase}, policyref={args.policyref}')


if __name__ == '__main__':
    main()
