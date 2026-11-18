# SPDX-License-Identifier: GPL-3.0-or-later
"""Back up, patch, restore and verify Crystal701 manufacturer blocks 3910/3911."""
import argparse
import json
import os
from pathlib import Path
import secrets
import struct
import subprocess
import sys
from zlib import crc32

from nand_protocol import ECC_WIRE, RAW_WIRE, ecc_page, exact_bulk_read, raw_page
from patch_video import DEFAULT_PATCH, make_decoded_patch
from video_nand_codec import (BLOCKS, PAGES, PAGE_DATA, PAGE_RAW, STOCK_SHA256,
                             PATCH_REVISIONS, check_raw_block, digest, patch_raw_block, protected_spare,
                             raw_bit_differences, require, xor)

HERE = Path(__file__).resolve().parent
LOADERS = HERE/'loaders'
SRAM, DRAM = 0xb4061000, 0xa0800000
BRIDGE_SHA256 = '802962366f31760c971a1db20ac8f2806b85d20327ac8b1f1e7ce06ad00edb3f'
BLOCK_RAW, BLOCK_DECODED = PAGES*PAGE_RAW, PAGES*PAGE_DATA
MAGIC = b'C107'
READY = struct.pack('<4s4sII', b'RD01', MAGIC, DRAM, 65536)
LINK = '''OUTPUT_ARCH(mips)
ENTRY(_entry)
SECTIONS {
 .entry 0xA0800000 : { KEEP(*(.entry)) }
 .text 0xA0800400 : { *(.text*) *(.rodata*) }
 .data : { *(.data*) *(.sdata*) }
 .bss : { *(.bss*) *(.sbss*) *(COMMON) }
 ASSERT(ADDR(.text) + SIZEOF(.text) < 0xA0804000, "stage 2 exceeds reserved code space")
 ASSERT(SIZEOF(.data) == 0 && SIZEOF(.bss) == 0, "unexpected static runtime data")
 /DISCARD/ : { *(.reginfo) *(.MIPS.abiflags) *(.pdr) *(.comment) *(.gnu.attributes) }
}
'''


def command(folder, *args, capture_output=False):
    prefix = []
    if os.name == 'nt':
        path = folder.resolve()
        linux_path = '/mnt/'+path.drive[0].lower()+path.as_posix().split(':', 1)[1]
        prefix = ['wsl', '--cd', linux_path, '-e']
    return subprocess.run(prefix+['mipsel-linux-gnu-'+args[0], *args[1:]], cwd=folder, check=True,
                          capture_output=capture_output)


def replace_one(source, before, after):
    require(source.count(before) == 1, f'unexpected loader template near {before!r}')
    return source.replace(before, after)


def reader_source(block, kind):
    row, end = block*PAGES, (block+1)*PAGES
    source = (LOADERS/('raw_fast.S' if kind == 'raw' else 'ecc_firmware.S')).read_text()
    source = replace_one(source, '    xori $t0, $t0, 0x1e',
                         '    addiu $t1, $zero, -32\n    and $t0, $t0, $t1\n    ori $t0, $t0, 0x1d')
    source = replace_one(source, 'terminal:\n    beq $zero, $zero, terminal\n    nop',
                         'terminal:\n    lui $t0, 0xb406\n    ori $t0, $t0, 0x1000\n    jr $t0\n    nop')
    if kind == 'raw':
        source = replace_one(source, 'lui $s0, 0x0000 ', f'lui $s0, 0x{row>>16:04x} ')
        source = replace_one(source, 'ori $s0, $s0, 0x0000 ', f'ori $s0, $s0, 0x{row&65535:04x} ')
        start = source.index('    lui $s2, 0x0010')
        source = source[:start]+(f'    lui $s2, 0x{end>>16:04x}\n'
                                 f'    ori $s2, $s2, 0x{end&65535:04x}')+source[source.index('\n', start):]
    else:
        source = source.split('pair_rows:\n', 1)[0]+f'pair_rows:\n    .word {row}, {end}, 0, 0\ncode_end:\n'
        source = replace_one(source, '    sw $t0, 32($t5)\n',
                             '    sw $t0, 32($t5)\n    lw $t0, 0x4c($t3)\n    sw $t0, 36($t5)\n'
                             '    lw $t0, 0x50($t3)\n    sw $t0, 40($t5)\n')
    return source


def build(folder, codec):
    folder.mkdir(parents=True, exist_ok=False)
    for name in ('stage1.c', 'stage1_start.S', 'stage1.ld', 'v100_usb.h',
                 'stock_video_writer.c', 'stock_video_writer_start.S'):
        (folder/name).write_bytes((LOADERS/name).read_bytes())
    (folder/'dram.ld').write_text(LINK, encoding='ascii')
    compiler = ('-march=mips32r2','-mno-dsp','-mno-abicalls','-fno-pic','-G0',
                '-Os','-ffreestanding','-fno-builtin','-fno-delayed-branch',
                '-ffunction-sections','-fdata-sections','-Wall','-Wextra','-Werror',
                '-nostdlib','-Wl,--build-id=none','-Wl,--gc-sections')
    command(folder, 'gcc', *compiler, '-T', 'stage1.ld', 'stage1_start.S', 'stage1.c', '-o', 'bridge.elf')
    command(folder, 'objcopy', '-O', 'binary', 'bridge.elf', 'bridge.bin')
    bridge = (folder/'bridge.bin').read_bytes()
    require(0 < len(bridge) <= 2048, 'invalid bridge size')
    bridge += bytes(2048-len(bridge))
    encoded = codec.encode(bridge)
    require(digest(encoded) == BRIDGE_SHA256 and codec.decode(encoded) == bridge, 'historical bridge hash differs')
    (folder/'bridge.encoded.bin').write_bytes(encoded)

    command(folder, 'gcc', *compiler, '-T', 'dram.ld', 'stock_video_writer_start.S',
            'stock_video_writer.c', '-o', 'writer.elf')
    stems = ['writer']
    for block in BLOCKS:
        for kind in ('raw', 'ecc'):
            stem = f'{kind}-{block}'
            stems.append(stem)
            (folder/f'{stem}.S').write_text(reader_source(block, kind), encoding='ascii')
            command(folder, 'as', '-EL', '-mips32', '-o', f'{stem}.o', f'{stem}.S')
            command(folder, 'ld', '-EL', '-T', 'dram.ld', '-o', f'{stem}.elf', f'{stem}.o')
    artifacts = {}
    for stem in stems:
        command(folder, 'objcopy', '-O', 'binary', f'{stem}.elf', f'{stem}.bin')
        image = (folder/f'{stem}.bin').read_bytes()
        require(0 < len(image) <= 16384, f'invalid {stem} size')
        artifacts[stem] = digest(image)
    save_json(folder/'build.json', dict(blocks=list(BLOCKS), bridge_sha256=BRIDGE_SHA256,
                                       images=artifacts, ecc_status_udat8=True))


def save_json(path, data):
    pending = path.with_suffix('.tmp')
    with pending.open('wb') as file:
        file.write((json.dumps(data, indent=2)+'\n').encode())
        file.flush()
        os.fsync(file.fileno())
    pending.replace(path)


def save_bytes(path, data):
    with path.open('xb') as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())


def roll(data):
    checksum = 0
    for word, in struct.iter_unpack('<I', data):
        checksum = (((checksum << 5) | (checksum >> 27)) & 0xffffffff) ^ word
    return checksum


class Bulk:
    def __init__(self, usb, packet):
        self.device = usb
        self.packet_size = packet

    def write(self, data, timeout_ms):
        return self.device.write(0x01, data, timeout=timeout_ms)

    def read(self, timeout_ms):
        return bytes(self.device.read(0x82, self.packet_size, timeout=timeout_ms))


class Bridge:
    def __init__(self, folder, resume=None):
        from adfu_tool import device
        from adfu_tool.protocol import GETINFO_IDENTITY, Session, read_execute_response

        self.folder = folder
        self.manifest = json.loads((folder/'build.json').read_bytes())
        require(self.manifest['blocks'] == list(BLOCKS), 'loader set is for a different target')
        for stem in ('writer',*(f'{kind}-{block}' for block in BLOCKS for kind in ('raw','ecc'))):
            require(digest((folder/f'{stem}.bin').read_bytes()) == self.manifest['images'][stem],
                    f'{stem} binary hash differs')
        boot = (folder/'bridge.encoded.bin').read_bytes()
        require(digest(boot) == BRIDGE_SHA256, 'bridge binary hash differs')

        candidates = device.find_adfu()
        require(len(candidates) == 1, 'connect exactly one Crystal701 in fresh ADFU mode')
        usb = candidates[0]
        require(usb.bDeviceClass in (0, 0xff), 'expected ADFU, not the running USB composite')
        if resume is not None:
            packet = resume['bulk_packet_size']
            port = list(usb.port_numbers or [])
            require(port and port == resume['port_numbers'] and usb.bus == resume['bus'] and
                    usb.address == resume['address'] and packet in (64, 512) and
                    resume['bootrom_identity'] == GETINFO_IDENTITY.hex(),
                    'resident bridge USB identity differs from the previous operation')
            self.info = resume.copy()
            self.usb = Bulk(usb, packet)
            self.session = Session(self.usb, overall_timeout=1200, operation_timeout=30)
            self.ping()
            return
        usb.set_configuration()
        interfaces = [item for item in usb.get_active_configuration() if item.bInterfaceClass == 0xff]
        require(len(interfaces) == 1, 'expected one vendor-class ADFU interface')
        eps = {ep.bEndpointAddress: ep for ep in interfaces[0]}
        require(0x01 in eps and 0x82 in eps, 'unexpected bulk endpoint addresses')
        packet = eps[0x82].wMaxPacketSize
        require(packet in (64, 512) and eps[0x01].wMaxPacketSize == packet and
                all((eps[address].bmAttributes & 3) == 2 for address in (0x01,0x82)),
                'unexpected bulk endpoint configuration')
        self.info = device.describe(usb)
        self.info['bulk_packet_size'] = packet
        self.usb = Bulk(usb, packet)
        self.session = Session(self.usb, overall_timeout=1200, operation_timeout=30)
        self.info['bootrom_identity'] = self.session.getinfo().hex()
        self.session.upload(boot, address=SRAM)
        self.session.execute(entry=SRAM)
        ready, order = read_execute_response(self.session, b'RD01', len(READY))
        require(ready == READY, 'bootstrap bridge did not identify itself')
        self.info['execute_order'] = order

    def ping(self):
        self.session.write(struct.pack('<4s4sII', b'PG01', MAGIC, 0, 0))
        require(self.session.read_exact(16, 2) == struct.pack('<4s4sII', b'PNG1', MAGIC, DRAM, 65536),
                'resident bridge identity differs')

    def load(self, stem):
        image = (self.folder/f'{stem}.bin').read_bytes()
        require(digest(image) == self.manifest['images'][stem], 'stage-2 image hash changed')
        image += bytes((-len(image)) % 64)
        encoded_size = len(image)
        require(0 < encoded_size <= 16384, 'stage-2 image exceeds its reserved memory')
        self.ping()
        checksum = roll(image)
        self.session.write(struct.pack('<4s4sII', b'LD01', MAGIC, DRAM, encoded_size))
        for at in range(0, encoded_size, 64):
            self.session.write(image[at:at+64])
        require(self.session.read_exact(16, 2) == struct.pack('<4s4sII', b'DON1', MAGIC, encoded_size, checksum),
                'stage-2 transfer checksum differs')
        self.session.write(struct.pack('<4s4sII', b'EX01', MAGIC, DRAM, checksum))

    def standby(self):
        require(self.session.read_exact(16, 2) == READY, 'stage 2 did not return to the SRAM bridge')

    def read_block(self, block, kind):
        require(block in BLOCKS and kind in ('raw','ecc'), 'reader target is outside the reviewed pair')
        self.load(f'{kind}-{block}')
        row = block*PAGES
        width, wire = (PAGE_RAW,RAW_WIRE) if kind == 'raw' else (PAGE_DATA,ECC_WIRE)
        magic = b'RAW1' if kind == 'raw' else b'ECC1'
        hello = self.session.read_exact(64, 2)
        expected = struct.pack('<4s7I', magic, row, 0xffffffff, 0, row+PAGES, 0, width, 0)
        require(hello[:32] == expected, 'reader hello does not match the requested block')
        data, status, faults = bytearray(), bytearray(), []
        for page in range(PAGES):
            frame = exact_bulk_read(self.usb, wire)
            if kind == 'raw':
                body, state, bad = raw_page(frame, row+page, row)
            else:
                body, state, bad = ecc_page(frame, row+page, page)
                if self.manifest.get('ecc_status_udat8') is True:
                    state = b''.join(frame[1024+(1024+64)*sector:1068+(1024+64)*sector]
                                     for sector in range(4))
            data.extend(body)
            status.extend(state)
            faults.extend(bad)
        self.standby()
        return bytes(data), bytes(status), faults

    def program(self, block, data, commit, before_commit):
        require(block in BLOCKS and len(data) == BLOCK_RAW, 'writer payload has incorrect geometry')
        self.load('writer')
        self.expect(b'WV01', 0, BLOCK_RAW, 0, 0)
        checksum = crc32(data)
        nonce = secrets.randbelow(0xffffffff)+1
        self.session.write(frame(b'PRE1', block, BLOCK_RAW, checksum, nonce))
        self.expect(b'LOAD', block, BLOCK_RAW, checksum, nonce)
        for at in range(0, len(data), 64):
            self.session.write(data[at:at+64])
        count = next((index+1 for index in reversed(range(PAGES))
                      if data[index*PAGE_RAW:(index+1)*PAGE_RAW] != b'\xff'*PAGE_RAW), 0)
        self.expect(b'SUM1', block, count, checksum, nonce)
        if commit:
            before_commit(block, checksum, count)
        self.session.write(frame(b'CMIT' if commit else b'ABRT', block, count, checksum, nonce))
        self.expect(b'DONE' if commit else b'SAFE', block, count, checksum, nonce)
        self.standby()
        return checksum, count

    def expect(self, *expected):
        response = self.session.read_exact(64, 2)
        actual = struct.unpack_from('<4s4I', response)
        require(not any(response[20:]), 'unexpected extended writer response')
        if (expected[0] == b'DONE' and actual[0] == b'FAIL' and
                actual[1] == expected[1] and actual[3:] == expected[3:]):
            self.standby()
            phase = {0xfffffffc: 'pre-erase page comparison', 0xfffffffd: 'writable NAND status',
                     0xfffffffe: 'erase read-back', 0xffffffff: 'erase command and status'}
            page = actual[2]
            fallback = f'program page {page}' if page < PAGES else f'inline read-back of page {page-0x10000}'
            raise ValueError(f'block {actual[1]}: {phase.get(page, fallback)} failed')
        require(actual == expected, f'writer state {actual} differs from {expected}')


def frame(tag, block, size, checksum, nonce):
    return struct.pack('<4s4I', tag, block, size, checksum, nonce).ljust(64, b'\0')


def capture(bridge, folder, block, kind, prefix=''):
    data, status, faults = bridge.read_block(block, kind)
    stem = folder/f'{prefix}block-{block}-{kind}'
    save_bytes(stem.with_suffix('.bin'), data)
    save_bytes(stem.with_suffix('.status'), status)
    if faults:
        save_json(stem.with_suffix('.errors.json'), faults)
        raise ValueError(f'{stem.name}: {len(faults)} read failures')
    return data


def backup(bridge, output):
    report = dict(complete=False, device=bridge.info, blocks={})
    try:
        for block in BLOCKS:
            raw = capture(bridge, output, block, 'raw')
            decoded = capture(bridge, output, block, 'ecc')
            require(digest(decoded) == STOCK_SHA256[block], f'block {block} differs from supported stock data')
            spare = captured_spare(output, block, '')
            attempt = 0
            if spare is not None:
                while spare != protected_spare(raw) and attempt < 3:
                    attempt += 1
                    raw = capture(bridge, output, block, 'raw', f'backup-retry-{attempt}-')
            require(spare is None or spare == protected_spare(raw),
                    f'block {block}: raw backup metadata still differs after re-reading')
            check_raw_block(block, raw)
            report['blocks'][str(block)] = dict(raw_sha256=digest(raw), decoded_sha256=digest(decoded),
                                               raw_attempt=attempt)
            print(f'Backed up block {block}: full raw, OOB and ECC data', flush=True)
        report['complete'] = True
    finally:
        save_json(output/'backup.json', report)
    return report


def load_backup(folder):
    manifest = json.loads((folder/'backup.json').read_bytes())
    require(manifest['complete'] is True and set(manifest['blocks']) == set(map(str, BLOCKS)),
            'complete backup of both manufacturer blocks required')
    blocks = {}
    for block in BLOCKS:
        record = manifest['blocks'][str(block)]
        attempt = record.get('raw_attempt', 0)
        require(type(attempt) is int and 0 <= attempt <= 3, 'invalid backup raw attempt')
        prefix = f'backup-retry-{attempt}-' if attempt else ''
        raw = (folder/f'{prefix}block-{block}-raw.bin').read_bytes()
        decoded = (folder/f'block-{block}-ecc.bin').read_bytes()
        check_raw_block(block, raw)
        require(digest(raw) == record['raw_sha256'], f'block {block}: raw backup hash differs')
        require(digest(decoded) == record['decoded_sha256'] == STOCK_SHA256[block],
                f'block {block}: ECC stock backup hash differs')
        blocks[block] = (raw, decoded)
    return manifest, blocks


def patch_revision(hashes):
    for name, expected in PATCH_REVISIONS.items():
        if hashes == expected:
            return name
    raise ValueError('payloads must match one reviewed VIDEO.APP revision across both blocks')


def prepare(backup_path, output, patch_binary=DEFAULT_PATCH):
    _, original = load_backup(backup_path)
    modified_blocks = make_decoded_patch(original, patch_binary)
    revision = patch_revision({block: digest(data) for block, data in modified_blocks.items()})
    result = {'revision': revision, 'blocks': {}}
    payloads = {}
    for block, (raw, decoded) in original.items():
        modified = modified_blocks[block]
        replacement = patch_raw_block(block, raw, decoded, modified)
        payloads[block] = (replacement, modified)
        result['blocks'][str(block)] = dict(original_raw_sha256=digest(raw), patched_raw_sha256=digest(replacement),
                                           patched_decoded_sha256=digest(modified))
    output.mkdir(parents=True, exist_ok=False)
    for block, (raw, decoded) in payloads.items():
        save_bytes(output/f'block-{block}-raw.bin', raw)
        save_bytes(output/f'block-{block}-ecc.bin', decoded)
    save_json(output/'payloads.json', result)
    return result


def load_patch(folder, original):
    manifest = json.loads((folder/'payloads.json').read_bytes())
    require(set(manifest['blocks']) == set(map(str, BLOCKS)), 'incomplete prepared patch')
    revision = patch_revision({int(block): record['patched_decoded_sha256']
                               for block, record in manifest['blocks'].items()})
    require(manifest.get('revision', revision) == revision, 'prepared patch revision label differs')
    patched = {}
    for block, (raw, decoded) in original.items():
        record = manifest['blocks'][str(block)]
        new = (folder/f'block-{block}-ecc.bin').read_bytes()
        new_raw = (folder/f'block-{block}-raw.bin').read_bytes()
        require(digest(raw) == record['original_raw_sha256'], 'patch was prepared for another raw backup')
        require(digest(new) == PATCH_REVISIONS[revision][block],
                'prepared decoded patch hash differs')
        require(digest(new_raw) == record['patched_raw_sha256'] and new_raw == patch_raw_block(block, raw, decoded, new),
                'prepared raw patch differs from the exact BCH24/UDAT8 reconstruction')
        patched[block] = (new_raw, new)
    return patched


def captured_spare(output, block, prefix):
    state = (output/f'{prefix}block-{block}-ecc.status').read_bytes()
    if len(state) == PAGES*4*44:
        return b''.join(state[at:at+8] for at in range(36, len(state), 44))
    return None


def compare_raw(bridge, output, block, raw, expected, prefix, corrected_spare=None):
    # Re-read only after the independently corrected data has matched.
    # Each accepted capture still requires exact OOB management bytes.
    for attempt in range(1, 4):
        differences = raw_bit_differences(raw, expected, corrected_spare=corrected_spare)
        if differences is not None:
            return raw, differences
        raw = capture(bridge, output, block, 'raw', f'{prefix}retry-{attempt}-')
    return raw, raw_bit_differences(raw, expected, corrected_spare=corrected_spare)


def verify_block(bridge, output, block, expected, prefix):
    raw = capture(bridge, output, block, 'raw', prefix)
    decoded = capture(bridge, output, block, 'ecc', prefix)
    require(decoded == expected[1], f'block {block}: independent ECC read-back differs')
    spare = captured_spare(output, block, prefix)
    raw, differences = compare_raw(bridge, output, block, raw, expected[0], prefix, spare)
    require(differences is not None, f'block {block}: independent raw read-back differs')
    return dict(raw_sha256=digest(raw), decoded_sha256=digest(decoded), raw_bit_differences=differences,
                corrected_spare_verified=spare is not None,
                raw_spare_bit_differences=sum(value.bit_count() for value in
                                              xor(protected_spare(raw), protected_spare(expected[0]))))


def identify_before(bridge, output, block, raw, original, patched, allow_other_revision):
    references = {'original': original}
    if patched is not None:
        references['patched'] = patched
    if not allow_other_revision and all(raw_bit_differences(raw, expected) is None
                                        for expected, _ in references.values()):
        return None, None, None, raw
    decoded = capture(bridge, output, block, 'ecc', 'before-')
    spare = captured_spare(output, block, 'before-')
    for label, (expected, data) in references.items():
        if decoded == data:
            raw, bits = compare_raw(bridge, output, block, raw, expected, 'before-', spare)
            return label if bits is not None else None, (expected, decoded), bits, raw
    if allow_other_revision:
        decoded_hash = digest(decoded)
        for revision, hashes in PATCH_REVISIONS.items():
            if decoded_hash == hashes[block]:
                expected = patch_raw_block(block, *original, decoded)
                raw, bits = compare_raw(bridge, output, block, raw, expected, 'before-', spare)
                if bits is not None:
                    return revision, (expected, decoded), bits, raw
    return None, None, None, raw


def permit_interrupted_write(report, current, expected_before, bridge_info, unknown, action):
    if report is None or len(unknown) != 1:
        return False
    interrupted = unknown[0]
    peer = next(block for block in BLOCKS if block != interrupted)
    port = bridge_info['port_numbers']
    source_actions = ('apply', 'restore') if action == 'restore' else ('apply',)
    return (report.get('operation') in source_actions and report.get('complete') is False and
            port and port == report['device']['port_numbers'] and
            bridge_info['bootrom_identity'] == report['device']['bootrom_identity'] and
            report.get('backup_hashes') == expected_before and
            str(peer) in report.get('before', {}) and current[peer] and
            any(attempt['block'] == interrupted and not attempt.get('verified')
                for attempt in report.get('attempts', [])))


def run(bridge, output, action, original, patched, expect='patched', recovery=None):
    hashes = {str(block):digest(raw) for block, (raw, _) in original.items()}
    report = dict(complete=False, operation=action, device=bridge.info, before={}, before_check={},
                  backup_hashes=hashes, attempts=[], verified={})
    desired = original if action == 'restore' or (action == 'verify' and expect == 'original') else patched
    report['target_decoded_hashes'] = {str(block): digest(data) for block, (_, data) in desired.items()}
    try:
        if action == 'verify':
            for block in BLOCKS:
                report['verified'][str(block)] = verify_block(bridge, output, block, desired[block], 'readback-')
                save_json(output/'operation.json', report)
        else:
            current, recognized = {}, {}
            pending = {attempt['block'] for attempt in (recovery or {}).get('attempts', [])
                       if not attempt.get('verified')}
            for block in BLOCKS:
                raw = capture(bridge, output, block, 'raw', 'before-')
                report['before'][str(block)] = digest(raw)
                save_json(output/'operation.json', report)
                label, baseline, bits, raw = identify_before(
                    bridge, output, block, raw, original[block], None if patched is None else patched[block],
                    block not in pending)
                current[block] = baseline
                recognized[block] = label
                report['before'][str(block)] = digest(raw)
                report['before_check'][str(block)] = dict(image=label, raw_bit_differences=bits)
                save_json(output/'operation.json', report)
            unknown = [block for block in BLOCKS if not recognized[block]]
            if unknown:
                require(action in ('apply', 'restore') and permit_interrupted_write(
                    recovery, recognized, hashes, bridge.info, unknown, action),
                    f'current raw data is neither stock nor prepared patch on block(s) {unknown}')
                report['interrupted_blocks'] = unknown

            # Program the block containing the tail first and the block holding
            # the FAT16 file size and the entry-point edit last.
            for block in reversed(BLOCKS):
                payload = desired[block]
                desired_label = 'original' if action == 'restore' else 'patched'
                if action == 'dry-run' or recognized[block] != desired_label:
                    def will_commit(target, checksum, pages):
                        report['attempts'].append(dict(block=target, crc32=checksum, pages=pages, verified=False))
                        save_json(output/'operation.json', report)
                        print(f'Transfer checked; committing physical block {target}', flush=True)

                    print(f'Staging block {block} in RAM', flush=True)
                    checksum, pages = bridge.program(block, payload[0], action != 'dry-run', will_commit)
                    if action == 'dry-run':
                        report['attempts'].append(dict(block=block, crc32=checksum, pages=pages, written=False))
                expected = current[block] if action == 'dry-run' else payload
                readback = verify_block(bridge, output, block, expected, 'readback-')
                report['verified'][str(block)] = readback
                for attempt in report['attempts']:
                    if attempt['block'] == block:
                        attempt['verified'] = True
                save_json(output/'operation.json', report)
                print(f'Block {block}: ECC read-back matched; raw bit differences '
                      f'{readback["raw_bit_differences"]}', flush=True)
        report['complete'] = True
    except BaseException as error:
        report['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        save_json(output/'operation.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adfu-checkout', type=Path, default=HERE.parent/'adfu-bootstrap',
                        help='actions-adfu-tool checkout used for BootROM transport and encoding')
    parser.add_argument('--resume-from', type=Path, help='backup.json or operation.json from the connected RAM bridge')
    commands = parser.add_subparsers(dest='action', required=True)
    build_cmd = commands.add_parser('build')
    backup_cmd = commands.add_parser('backup')
    prepare_cmd = commands.add_parser('prepare')
    prepare_cmd.add_argument('--backup', type=Path, required=True)
    prepare_cmd.add_argument('--patch-binary', type=Path, default=DEFAULT_PATCH,
                             help='assembled key handler (defaults to the supplied v3 binary)')
    for cmd in (build_cmd, backup_cmd, prepare_cmd):
        cmd.add_argument('--output', type=Path, required=True)
    backup_cmd.add_argument('--loaders', type=Path, required=True)
    for action in ('dry-run','apply','restore','verify'):
        cmd = commands.add_parser(action)
        for name in ('--loaders', '--backup', '--output'):
            cmd.add_argument(name, type=Path, required=True)
        cmd.add_argument('--payloads', type=Path, required=action in ('dry-run', 'apply'))
        if action in ('apply','restore'):
            cmd.add_argument('--write', action='store_true', required=True)
            cmd.add_argument('--recover-from', type=Path, help='operation.json for an interrupted commit')
        if action == 'verify':
            cmd.add_argument('--expect', choices=('patched','original'), required=True)
    args = parser.parse_args()
    if args.action == 'verify' and args.expect == 'patched' and args.payloads is None:
        parser.error('--payloads is required to verify the patched image')
    command_errors = (ValueError, KeyError, OSError, RuntimeError, ImportError, subprocess.SubprocessError)
    try:
        if args.action == 'prepare':
            result = prepare(args.backup, args.output, args.patch_binary)
        else:
            require((args.adfu_checkout/'adfu_tool/protocol.py').is_file(), 'provide --adfu-checkout')
            sys.path.insert(0, str(args.adfu_checkout.resolve()))
            if args.action == 'build':
                from adfu_tool import codec
                build(args.output, codec)
                result = json.loads((args.output/'build.json').read_bytes())
            else:
                from adfu_tool.protocol import AdfuError
                command_errors += (AdfuError,)
                if args.action != 'backup':
                    _, original = load_backup(args.backup)
                    patched = load_patch(args.payloads, original) if args.payloads else None
                recovery_path = getattr(args, 'recover_from', None)
                recovery = json.loads(recovery_path.read_bytes()) if recovery_path else None
                args.output.mkdir(parents=True, exist_ok=False)
                resume = json.loads(args.resume_from.read_bytes())['device'] if args.resume_from else None
                bridge = Bridge(args.loaders, resume)
                if args.action == 'backup':
                    result = backup(bridge, args.output)
                else:
                    result = run(bridge, args.output, args.action, original, patched,
                                 getattr(args, 'expect', 'patched'), recovery)
    except command_errors as error:
        parser.exit(1, f'{error}\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
