# SPDX-License-Identifier: GPL-3.0-or-later
"""Crystal701 BCH24/UDAT8 raw page reconstruction for stock VIDEO.APP."""
from hashlib import sha256

BLOCKS = (3910, 3911)
PAGES = 256
SECTOR = 1024
PAGE_DATA = 4096
PAGE_RAW = 4320
MAX_RAW_BIT_DIFFERENCES = 4
CHANGED_PAGES = {3910: (76, 87, 92, 110), 3911: (109,)}
STOCK_SHA256 = {
    3910: 'a019c04184e667cd011cd0922851013ac9f3da7437ed298ece3136139386702c',
    3911: 'e239490fc168a382463a00a7be2d52944cc5ae08a31317c9770bc8e2f41145ed',
}
PATCH_REVISIONS = {
    'keymode-v1': {
        3910: '67566dccd80f367e24b30f650c04d6ec6490fb67d0ac9f22607a2871e2116a31',
        3911: 'a3cfc666fd83b9b8f8b664c1125855434bcb4de06f7d9ca1f20ee5622a838d78',
    },
    'keymode-v2': {
        3910: 'f0a56790a19627a6208e9b22d48dd6189af0d690832c0bd143f8528d8412d587',
        3911: 'f30f062a56f52cc712ef379d21649b09c2e2d602a8ff5d62bb97fe778bbcc1be',
    },
    'keymode-v3': {
        3910: 'f0a56790a19627a6208e9b22d48dd6189af0d690832c0bd143f8528d8412d587',
        3911: '8dcf19528902c8abf80ca1593162ed92559618d81872b5a4508b2309229fde00',
    },
}

# Generator derived from raw/decoded sector pairs on Crystal701. The 336
# parity bits cover 1024 descrambled bytes followed by 8 OOB user bytes.
GENERATOR = 0x16503CF432240D9D16DC7226FB4D67F1F51F35292F095D4F6A2BE42503AB2BB0A6AED13A3CC57EDFDD70B
MASK = (1 << 336)-1
REVERSE_BITS = bytes(int(f'{byte:08b}'[::-1], 2) for byte in range(256))


def require(test, message):
    if not test:
        raise ValueError(message)


def digest(data):
    return sha256(data).hexdigest()


def remainder(value):
    while value.bit_length() > 336:
        value ^= GENERATOR << (value.bit_length()-337)
    return value


REMAINDERS = tuple(remainder(byte << 336) for byte in range(256))


def parity(data, user):
    require(len(data) == SECTOR and len(user) == 8, 'invalid BCH24/UDAT8 input length')
    checksum = 0
    for byte in (data+user).translate(REVERSE_BITS):
        checksum = ((checksum << 8) & MASK) ^ REMAINDERS[(checksum >> 328) ^ byte]
    return checksum.to_bytes(42, 'big').translate(REVERSE_BITS)


def xor(left, right):
    return bytes(a ^ b for a, b in zip(left, right, strict=True))


def check_raw_block(block, raw):
    require(block in BLOCKS and len(raw) == PAGES*PAGE_RAW, 'unexpected raw block or byte count')
    require(raw[PAGE_DATA:PAGE_DATA+4] == b'\xff\x18\x00\x00',
            f'block {block} is missing the checked manufacturer superblock-24 tag')


def protected_spare(raw):
    return b''.join(raw[at+sector*50:at+sector*50+8]
                    for at in range(PAGE_DATA, len(raw), PAGE_RAW) for sector in range(4))


def raw_bit_differences(actual, expected, *, corrected_spare=None):
    """Bound differences in BCH-protected bytes; require exact unprotected spare."""
    require(len(actual) == len(expected) == PAGES*PAGE_RAW, 'unexpected raw block size')
    if corrected_spare is not None and corrected_spare != protected_spare(expected):
        return None
    if actual == expected:
        return 0
    total = 0
    for base in range(0, len(actual), PAGE_RAW):
        end = base+PAGE_RAW
        if actual[base:end] == expected[base:end]:
            continue
        spare = base+PAGE_DATA
        if (actual[spare:spare+4] != expected[spare:spare+4] or
                actual[spare+200:end] != expected[spare+200:end]):
            return None
        for sector in range(4):
            at = base+sector*SECTOR
            oob = spare+sector*50
            if corrected_spare is None and actual[oob:oob+8] != expected[oob:oob+8]:
                return None
            # Freshly programmed sectors have shown up to three hardware
            # corrections. The independently decoded block must match exactly.
            data = xor(actual[at:at+SECTOR], expected[at:at+SECTOR])
            ecc = xor(actual[oob:oob+50], expected[oob:oob+50])
            bits = int.from_bytes(data+ecc, 'little').bit_count()
            if bits > MAX_RAW_BIT_DIFFERENCES:
                return None
            total += bits
    return total


def patch_raw_block(block, raw, original, patched):
    check_raw_block(block, raw)
    require(len(original) == len(patched) == PAGES*PAGE_DATA, 'unexpected decoded block size')
    updated = bytearray(raw)
    pages = []
    for page in range(PAGES):
        raw_page, decoded_page = page*PAGE_RAW, page*PAGE_DATA
        if original[decoded_page:decoded_page+PAGE_DATA] == patched[decoded_page:decoded_page+PAGE_DATA]:
            continue
        pages.append(page)
        for sector in range(4):
            data_at = decoded_page+sector*SECTOR
            before = original[data_at:data_at+SECTOR]
            after = patched[data_at:data_at+SECTOR]
            if before == after:
                continue
            raw_at = raw_page+sector*SECTOR
            oob = raw_page+PAGE_DATA+sector*50
            user = raw[oob:oob+8]
            require(parity(before, user) == raw[oob+8:oob+50],
                    f'block {block} page {page} sector {sector}: stock BCH24 parity differs')
            # Absolute parity is generated on descrambled bytes. Applying the
            # decoded XOR delta preserves the row-dependent raw scrambling.
            delta = xor(before, after)
            updated[raw_at:raw_at+SECTOR] = xor(raw[raw_at:raw_at+SECTOR], delta)
            updated[oob+8:oob+50] = parity(after, user)
    require(tuple(pages) == CHANGED_PAGES[block], f'block {block}: unexpected changed pages {pages}')
    return bytes(updated)
