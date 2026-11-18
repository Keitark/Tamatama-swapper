# SPDX-License-Identifier: GPL-3.0-or-later
import json
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
from unittest import TestCase
from zlib import crc32

from nand_protocol import ECC_MAGIC, ECC_WIRE, ecc_page
from patch_video import DEFAULT_PATCH, HOOK_OFFSET, TRAMPOLINE, materialize_handler
from tamatama_swapper import run, verify_block
from video_nand_codec import BLOCKS, PAGE_DATA, PAGE_RAW, PAGES, protected_spare


class MemoryBridge:
    def __init__(self, before, after):
        self.info = {'port_numbers': [1], 'bootrom_identity': 'fixture'}
        self.images = before.copy()
        self.replacements = {pair[0]: pair for images in (before, after) for pair in images.values()}
        self.fail_on = None
        self.written = []

    def read_block(self, block, kind):
        image = self.images[block]
        return image[kind == 'ecc'], b'', []

    def program(self, block, raw, commit, before_commit):
        checksum = crc32(raw)
        if commit:
            before_commit(block, checksum, PAGES)
            self.written.append(block)
            if self.fail_on == block:
                self.images[block] = (b'\xff'*len(raw), b'\xff'*(PAGES*PAGE_DATA))
                raise ValueError('simulated interruption')
            self.images[block] = self.replacements[raw]
        return checksum, PAGES


def images():
    stock = {block: (bytes([index])*PAGE_RAW*PAGES, bytes([index])*PAGE_DATA*PAGES)
             for index, block in enumerate(BLOCKS, 1)}
    patch = {block: (bytes([raw[0] ^ 0xff])+raw[1:], bytes([data[0] ^ 0xff])+data[1:])
             for block, (raw, data) in stock.items()}
    return stock, patch


class SwapperTests(TestCase):
    def test_handler_materializes_only_displaced_instructions(self):
        handler = DEFAULT_PATCH.read_bytes()
        trailer = handler.rfind(TRAMPOLINE)
        self.assertGreater(trailer, 0)
        test_prologue = bytes(range(8))
        app = bytes(HOOK_OFFSET)+test_prologue
        code = materialize_handler(handler, app)
        self.assertEqual(code[trailer:trailer+12], test_prologue[:4]+TRAMPOLINE[4:8]+test_prologue[4:])
        self.assertEqual(code[:trailer], handler[:trailer])

    def test_ecc_wire_identity(self):
        row = BLOCKS[0]*PAGES
        wire = b''.join(bytes(1024)+struct.pack('<9I', ECC_MAGIC, row, i, 3, 8, 0, 1024, i+1, 0).ljust(64, b'\0')
                        for i in range(4))
        self.assertEqual(len(wire), ECC_WIRE)
        self.assertEqual(ecc_page(wire, row, 0)[0], bytes(PAGE_DATA))
        with self.assertRaisesRegex(ValueError, 'stream identity mismatch'):
            ecc_page(wire, row+1, 0)

    def test_dry_run_and_bounded_recovery(self):
        stock, patch = images()
        bridge = MemoryBridge(stock, patch)
        with TemporaryDirectory() as temp:
            root = Path(temp)

            def folder(name):
                out = root/name
                out.mkdir()
                return out

            receipt = run(bridge, folder('dry'), 'dry-run', stock, patch)
            self.assertTrue(receipt['complete'])
            self.assertEqual(bridge.written, [])
            bridge.fail_on = 3910
            interrupted = folder('interrupted')
            with self.assertRaisesRegex(ValueError, 'simulated interruption'):
                run(bridge, interrupted, 'apply', stock, patch)
            self.assertEqual(bridge.written, [3911, 3910])
            recovery = json.loads((interrupted/'operation.json').read_bytes())
            bridge.fail_on = None
            result = run(bridge, folder('finish'), 'apply', stock, patch, recovery=recovery)
            self.assertTrue(result['complete'])
            self.assertEqual(bridge.written, [3911, 3910, 3910])
            self.assertEqual(bridge.images, patch)

    def test_corrected_spare_and_one_raw_reread(self):
        stock, patch = images()
        raw, ecc = stock[3911]
        scratch = bytearray(raw)
        scratch[PAGE_DATA+57] ^= 1
        first_read = bytearray(scratch)
        first_read[PAGE_DATA+224-1] ^= 1  # Outside ECC. Reject this capture.
        spare = protected_spare(raw)
        status = b''.join(bytes(36)+spare[at:at+8] for at in range(0, len(spare), 8))

        class NoisyBridge(MemoryBridge):
            def __init__(self):
                super().__init__(stock, patch)
                self.raw_reads = iter((bytes(first_read), bytes(scratch)))

            def read_block(self, block, kind):
                if kind == 'raw':
                    return next(self.raw_reads), b'', []
                return ecc, status, []

        with TemporaryDirectory() as temp:
            result = verify_block(NoisyBridge(), Path(temp), 3911, stock[3911], 'readback-')
            self.assertTrue(result['corrected_spare_verified'])
            self.assertEqual(result['raw_spare_bit_differences'], 1)
            self.assertTrue((Path(temp)/'readback-retry-1-block-3911-raw.bin').is_file())
