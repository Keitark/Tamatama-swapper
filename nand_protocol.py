# SPDX-License-Identifier: GPL-3.0-or-later
"""Parse the raw NAND and ECC reader frames."""
import struct

from video_nand_codec import PAGE_DATA, PAGE_RAW as PAGE_BYTES

RAW_MAGIC = 0x31574152
ECC_MAGIC = 0x31434345
PAGE_OOB = PAGE_BYTES - PAGE_DATA
RAW_WIRE = 4 * (16 * 64 + 64) + (4 * 64 + 64)
ECC_WIRE = 4 * (1024 + 64)


def exact_bulk_read(usb, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        packet = bytes(usb.device.read(0x82, size - len(data), timeout=30_000))
        if not packet or len(packet) % 64:
            raise ValueError(f"bulk read returned {len(packet)} non-packet bytes")
        data.extend(packet)
    return bytes(data)


def raw_page(wire: bytes, page: int, start_page: int):
    if len(wire) != RAW_WIRE:
        raise ValueError(f"page {page}: expected {RAW_WIRE} wire bytes")
    cursor = 0
    sectors = []
    status_records = []
    bad = []
    for sector in range(5):
        frames = 16 if sector < 4 else 4
        count = PAGE_DATA // 4 if sector < 4 else PAGE_OOB
        data = wire[cursor:cursor + frames * 64]
        cursor += frames * 64
        status = wire[cursor:cursor + 64]
        cursor += 64
        magic, row, actual_sector, flags, nand_status, dma_command, length, sequence = (
            struct.unpack_from("<8I", status))
        expected_sequence = (page - start_page) * 5 + sector + 1
        if (magic, row, actual_sector, length, sequence) != (
                RAW_MAGIC, page, sector, count, expected_sequence):
            raise ValueError(f"page {page}, sector {sector}: status identity differs")
        if flags != 3 or dma_command & 1:
            bad.append({"page": page, "sector": sector, "flags": flags,
                        "nand_status": f"0x{nand_status:08x}",
                        "dma_command": f"0x{dma_command:08x}"})
        sectors.append(data[:count])
        status_records.append(status[:32])
    if cursor != RAW_WIRE:
        raise AssertionError("page frame layout differs")
    page_bytes = b"".join(sectors)
    if len(page_bytes) != PAGE_BYTES:
        raise AssertionError("page payload length differs")
    return page_bytes, b"".join(status_records), bad


def ecc_page(wire, row, index):
    if len(wire) != ECC_WIRE:
        raise ValueError(f"page {row}: short USB data")
    data = bytearray()
    statuses = bytearray()
    bad = []
    for sector in range(4):
        offset = sector * (1024 + 64)
        data.extend(wire[offset:offset + 1024])
        status = wire[offset + 1024:offset + 1088]
        magic, actual_row, actual_sector, flags, nand, dma, length, sequence, ecc = (
            struct.unpack_from("<9I", status))
        if (magic, actual_row, actual_sector, length, sequence) != (
                ECC_MAGIC, row, sector, 1024, index * 4 + sector + 1):
            raise ValueError(f"page {row} sector {sector}: stream identity mismatch")
        if flags != 3 or dma & 1 or (ecc & 0xff) >= 20:
            bad.append({"row": row, "sector": sector, "flags": flags,
                        "nand": f"0x{nand:08x}", "dma": f"0x{dma:08x}",
                        "ecc": f"0x{ecc:08x}"})
        statuses.extend(status[:36])
    return bytes(data), bytes(statuses), bad
