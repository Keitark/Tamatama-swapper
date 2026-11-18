# SPDX-License-Identifier: GPL-3.0-or-later
"""Patch the known Crystal701 VIDEO.APP using two validated NAND blocks."""
from pathlib import Path
import struct

from video_nand_codec import BLOCKS, PAGE_DATA, digest, require

STOCK_HASH = '1ac4f53b4b2fa9d6d3697d2439bd7813cfd236e20f58c8bd9598a7378600372f'
STOCK_SIZE = 188564
PATCHED_APP_HASH = '09796f93ef66be7fc26611456743310561b1f945ef0e63758a4fe581bd7a430a'
DEFAULT_PATCH = Path(__file__).resolve().parent/'patches/keymode-v3.bin'
# Relative to the 2 MiB manufacturer superblock assembled from 3910/3911.
DIRECTORY_ENTRY = 0x980c0
APP_START = 0xae000
CHAIN_SIZE = 93*2048
HOOK_OFFSET = 0xa934
TRAMPOLINE = struct.pack('<III', 0, 0x0a502a2f, 0)


def patch_app(original, code):
    require(len(original) == STOCK_SIZE and digest(original) == STOCK_HASH,
            'VIDEO.APP is not the supported stock revision')
    require(0 < len(code) <= 1024 and len(code) % 8 == 0, 'unexpected assembled patch size')
    # Extend the RX segment. Preserve every original virtual address. The
    # following RW bytes and section metadata move only within the file.
    exec_segment, data_segment = 52, 84
    first = struct.unpack_from('<8I', original, exec_segment)
    second = struct.unpack_from('<8I', original, data_segment)
    insertion = second[1]
    address = first[2]+first[4]
    require(first[1]+first[4] == insertion and address+len(code) <= second[2],
            'ELF executable extension would overlap the data segment')
    require(second[2]+second[5] == 0x49434b3c, 'unexpected position for zero-initialized state')
    output = bytearray(original[:insertion]+code+original[insertion:])
    struct.pack_into('<II', output, exec_segment+16, first[4]+len(code), first[5]+len(code))
    struct.pack_into('<I', output, data_segment+4, insertion+len(code))
    struct.pack_into('<I', output, data_segment+20, second[5]+12)
    hook_offset = first[1]+0xa8b4
    require(struct.unpack_from('<II', output, hook_offset) == (0x27bdfbc8, 0xafb20428),
            'stock video event handler prologue differs')
    struct.pack_into('<II', output, hook_offset, 0x08000000 | ((address>>2)&0x03ffffff), 0)

    old_table = struct.unpack_from('<I', original, 32)[0]
    entry_size, count, names_index = struct.unpack_from('<3H', original, 46)
    require(entry_size == 40 and old_table+entry_size*count == len(original), 'ELF section table differs')
    table = old_table+len(code)
    names = table+names_index*entry_size
    name_size = struct.unpack_from('<I', output, names+20)[0]
    name = b'.keymode\0'
    padding = name+bytes((-len(name))%4)
    sections = bytearray(output[table:])
    extended_bss = False
    for offset in range(0, len(sections), entry_size):
        _, kind, flags, base, file_offset, size, *_ = struct.unpack_from('<10I', sections, offset)
        if file_offset >= insertion:
            struct.pack_into('<I', sections, offset+16, file_offset+len(code))
        if kind == 8 and (flags&3) == 3 and size and base+size == 0x49434b3c:
            struct.pack_into('<I', sections, offset+20, size+12)
            extended_bss = True
    require(extended_bss, 'could not extend the existing VIDEO.APP BSS')
    struct.pack_into('<I', sections, names_index*entry_size+20, name_size+len(name))
    sections.extend(struct.pack('<10I', name_size, 1, 6, address, insertion, len(code), 0, 0, 4, 0))
    output[table:] = padding+sections
    struct.pack_into('<I', output, 32, table+len(padding))
    struct.pack_into('<H', output, 48, count+1)
    return bytes(output)


def materialize_handler(binary, app):
    """Copy two displaced stock instructions out of the user's own input."""
    position = binary.rfind(TRAMPOLINE)
    padding = binary[position+len(TRAMPOLINE):]
    require(position >= 0 and len(padding) < 16 and not any(padding),
            'unexpected binary patch trampoline')
    code = bytearray(binary)
    code[position:position+4] = app[HOOK_OFFSET:HOOK_OFFSET+4]
    code[position+8:position+12] = app[HOOK_OFFSET+4:HOOK_OFFSET+8]
    return bytes(code)


def make_decoded_patch(original, patch_binary=DEFAULT_PATCH):
    first, second = (original[block][1] for block in BLOCKS)
    logical = bytearray(b''.join(first[at:at+PAGE_DATA]+second[at:at+PAGE_DATA]
                                 for at in range(0, len(first), PAGE_DATA)))
    require(logical[DIRECTORY_ENTRY:DIRECTORY_ENTRY+11] == b'VIDEO   APP' and
            struct.unpack_from('<I', logical, DIRECTORY_ENTRY+28)[0] == STOCK_SIZE,
            'VIDEO.APP directory entry differs from the supported FAT16 layout')
    app = bytes(logical[APP_START:APP_START+STOCK_SIZE])
    require(digest(app) == STOCK_HASH, 'stock VIDEO.APP image differs')
    code = materialize_handler(patch_binary.read_bytes(), app)
    patched = patch_app(app, code)
    require(len(patched) <= CHAIN_SIZE and digest(patched) == PATCHED_APP_HASH,
            'binary patch did not produce the reviewed VIDEO.APP image')
    struct.pack_into('<I', logical, DIRECTORY_ENTRY+28, len(patched))
    logical[APP_START:APP_START+len(patched)] = patched
    return {block: b''.join(logical[at:at+PAGE_DATA]
                            for at in range(slot*PAGE_DATA, len(logical), 2*PAGE_DATA))
            for slot, block in enumerate(BLOCKS)}
