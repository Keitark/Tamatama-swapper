/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Crystal701 manufacturer blocks 3910/3911 only. Stock raw NAND command
 * sequence from stock_rewrite.S. Data is staged and checked before erase. */
#include <stdint.h>

#include "v100_usb.h"

#define NAND(offset) REG32(0xb0280000u + (offset))
#define DMA(offset) REG32(0xb0220000u + (offset))
#define PAGE_BYTES 4320u
#define BLOCK_BYTES (256u * PAGE_BYTES)
#define MAX_RAW_BIT_DIFFERENCES 4u
#define BUFFER ((uint32_t *)0xa0808000u)
#define DMA_BUFFER ((volatile uint32_t *)0xb4061b00u)
#define TAG(a,b,c,d) ((uint32_t)(a) | (uint32_t)(b) << 8 | (uint32_t)(c) << 16 | (uint32_t)(d) << 24)

static unsigned host_frame(uint32_t *out, unsigned more)
{
    REG8(USB_OUT1CS) = more;
    unsigned limit = 0x08000000u;
    while ((REG8(USB_OUT1CS) & 2u) && --limit) {}
    if (!limit || (REG8(USB_OUT1BCL) | ((unsigned)REG8(USB_OUT1BCH) << 8)) != 64u)
        return 0;
    for (unsigned i = 0; i < 16; ++i)
        out[i] = REG32(USB_FIFO1DATA);
    return 1;
}

static unsigned report(uint32_t tag, uint32_t block, uint32_t arg, uint32_t crc, uint32_t nonce)
{
    unsigned limit = 0x01000000u;
    while ((REG8(USB_IN2CS) & 2u) && --limit) {}
    if (!limit) return 0;
    REG32(USB_FIFO2DATA) = tag;
    REG32(USB_FIFO2DATA) = block;
    REG32(USB_FIFO2DATA) = arg;
    REG32(USB_FIFO2DATA) = crc;
    REG32(USB_FIFO2DATA) = nonce;
    for (unsigned i = 5; i < 16; ++i) REG32(USB_FIFO2DATA) = 0;
    REG8(USB_IN2BCL) = 64;
    REG8(USB_IN2BCH) = 0;
    REG8(USB_IN2CS) = 0;       /* same explicit 64-byte IN commit as RAW1 */
    limit = 0x01000000u;
    while ((REG8(USB_IN2CS) & 2u) && --limit) {}
    return limit != 0;
}

static uint32_t block_crc(void)
{
    const uint8_t *data = (const uint8_t *)BUFFER;
    uint32_t crc = 0xffffffffu;
    for (unsigned i = 0; i < BLOCK_BYTES; ++i) {
        crc ^= data[i];
        for (unsigned bit = 0; bit < 8; ++bit)
            crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}

static unsigned nand_ready(void)
{
    unsigned limit = 0x02000000u;
    while (!(NAND(0x04) & 8u) && --limit) {}
    if (!limit) return 0;
    limit = 0x02000000u;
    while ((NAND(0x04) & 0x80000000u) && --limit) {}
    return limit != 0;
}

static unsigned nand_busy(void)
{
    unsigned limit = 0x02000000u;
    while (!(NAND(0x04) & 0x80000000u) && --limit) {}
    return limit != 0;
}

static unsigned writable_status(void)
{
    NAND(0x14) = 0;
    NAND(0x18) = 0;
    NAND(0x1c) = 0;
    NAND(0x08) &= 0xffffff8fu;
    NAND(0x0c) = 1;
    NAND(0x20) = 0x70;
    NAND(0x24) = 0x61;
    NAND(0x38) = 1;
    unsigned limit = 0x01000000u;
    while (!(NAND(0x04) & 0x10u) && --limit) {}
    return limit && (NAND(0x10) & 0xc1u) == 0xc0u;
}

static void select_row(unsigned row)
{
    NAND(0x18) = row;
    NAND(0x1c) = 0;
    NAND(0x14) = 0x10000000u;
}

static unsigned erase_block(unsigned block)
{
    select_row(block * 256u);
    NAND(0x08) = (NAND(0x08) & 0xffffff8fu) | 0x20u;
    NAND(0x20) = 0x60;
    NAND(0x24) = 0x68;
    NAND(0x38) = 1;
    if (!nand_ready()) return 0;
    /* V100 stock STORAGE sends the row latch and confirm separately. */
    NAND(0x20) = 0xd0;
    NAND(0x24) = 0x60;
    NAND(0x38) = 1;
    return nand_busy() && nand_ready() && writable_status();
}

static unsigned read_first_sector(unsigned row)
{
    for (unsigned i = 0; i < 256; ++i) DMA_BUFFER[i] = 0;
    __asm__ volatile("sync" ::: "memory");
    select_row(row);
    NAND(0x08) = (NAND(0x08) & 0xfffffc8cu) | 0x20u;
    NAND(0x0c) = 1024;
    DMA(0x130) = 0x00040054;
    DMA(0x134) = 0x10280010;
    DMA(0x138) = 0x14061b00;
    DMA(0x13c) = 1024;
    NAND(0x20) = 0x3000;
    NAND(0x24) = 0x626c;
    NAND(0x38) = 1;
    if (!nand_ready()) return 0;
    for (volatile unsigned delay = 8192; delay; --delay) {}
    NAND(0x20) = 0xe005;
    NAND(0x24) = 0x6164;
    NAND(0x38) = 1;
    DMA(0x144) = 1;
    unsigned limit = 0x00100000u;
    while ((DMA(0x144) & 1u) && --limit) {}
    __asm__ volatile("sync" ::: "memory");
    return limit != 0;
}

static unsigned first_sector_matches(const uint32_t *expected)
{
    unsigned bits = 0;
    for (unsigned i = 0; i < 256; ++i) {
        uint32_t difference = DMA_BUFFER[i] ^ expected[i];
        while (difference) {
            if (++bits > MAX_RAW_BIT_DIFFERENCES) return 0;
            difference &= difference - 1u;
        }
    }
    return 1;
}

static unsigned first_sector_erased(void)
{
    for (unsigned i = 0; i < 256; ++i)
        if (DMA_BUFFER[i] != 0xffffffffu) return 0;
    return 1;
}

static unsigned verify_programmed_sector(unsigned row, const uint32_t *source)
{
    for (unsigned attempt = 0; attempt < 3; ++attempt)
        if (read_first_sector(row) && first_sector_matches(source)) return 1;
    return 0;
}

static unsigned program_page(unsigned row, const uint32_t *source)
{
    select_row(row);
    NAND(0x20) = 0x80;
    NAND(0x24) = 0x6c;
    NAND(0x38) = 1;
    if (!nand_ready()) return 0;
    for (unsigned sector = 0; sector < 5; ++sector) {
        unsigned length = sector < 4 ? 1024u : 224u;
        for (unsigned i = 0; i < length / 4; ++i) DMA_BUFFER[i] = *source++;
        __asm__ volatile("sync" ::: "memory");
        NAND(0x14) = 0x10000000u | (sector << 10);
        NAND(0x08) = (NAND(0x08) & 0xfffffcfcu) | 0x20u;
        NAND(0x0c) = length;
        DMA(0x13c) = length;
        NAND(0x20) = 0x85;
        NAND(0x24) = 0x65;
        NAND(0x38) = 3;
        DMA(0x144) = 1;
        unsigned limit = 0x00100000u;
        while ((DMA(0x144) & 1u) && --limit) {}
        if (!limit || !nand_ready()) return 0;
    }
    NAND(0x20) = 0x10;
    NAND(0x24) = 0x60;
    NAND(0x38) = 1;
    return nand_busy() && nand_ready() && writable_status();
}

static unsigned populated_pages(void)
{
    unsigned count = 256;
    while (count) {
        const uint32_t *page = BUFFER+(count-1)*(PAGE_BYTES/4);
        unsigned word = 0;
        while (word < PAGE_BYTES/4 && page[word] == 0xffffffffu) ++word;
        if (word < PAGE_BYTES/4) break;
        --count;
    }
    return count;
}

void writer_main(void)
{
    uint32_t cmd[16];
    if (!report(TAG('W','V','0','1'), 0, BLOCK_BYTES, 0, 0) || !host_frame(cmd, 0)) return;
    unsigned block = cmd[1];
    uint32_t expected = cmd[3], nonce = cmd[4];
    if (cmd[0] != TAG('P','R','E','1') || (block != 3910u && block != 3911u) ||
        cmd[2] != BLOCK_BYTES || !nonce) {
        report(TAG('R','E','J','!'), block, 0, 0, nonce);
        return;
    }
    if (!report(TAG('L','O','A','D'), block, BLOCK_BYTES, expected, nonce)) return;
    for (unsigned off = 0; off < BLOCK_BYTES; off += 64)
        if (!host_frame(BUFFER+off/4, off+64 < BLOCK_BYTES)) return;
    uint32_t crc = block_crc();
    unsigned count = populated_pages();
    if (crc != expected || !count) {
        report(TAG('R','E','J','!'), block, count, crc, nonce);
        return;
    }
    if (!report(TAG('S','U','M','1'), block, count, crc, nonce) || !host_frame(cmd, 0)) return;
    if (cmd[0] == TAG('A','B','R','T') && cmd[1] == block && cmd[2] == count &&
        cmd[3] == crc && cmd[4] == nonce) {
        report(TAG('S','A','F','E'), block, count, crc, nonce);
        return;
    }
    if (cmd[0] != TAG('C','M','I','T') || cmd[1] != block || cmd[2] != count ||
        cmd[3] != crc || cmd[4] != nonce) {
        report(TAG('R','E','J','!'), block, count, crc, nonce);
        return;
    }

    /* No controller programming or erase commands occur before commit.
     * Use the same ROM setup and raw DMA direction as stock_rewrite.S. */
    ((void (*)(void))0xbfc056c4u)();
    NAND(0) = (NAND(0) & ~0x1fu) | 0x1du;
    DMA(0) |= 2;
    if (!writable_status()) {
        report(TAG('F','A','I','L'), block, 0xfffffffdu, crc, nonce);
        return;
    }
    if (!read_first_sector(block*256u) || (!first_sector_matches(BUFFER) && !first_sector_erased())) {
        report(TAG('F','A','I','L'), block, 0xfffffffcu, crc, nonce);
        return;
    }
    if (!erase_block(block)) {
        report(TAG('F','A','I','L'), block, 0xffffffffu, crc, nonce);
        return;
    }
    if (!read_first_sector(block*256u) || !first_sector_erased()) {
        report(TAG('F','A','I','L'), block, 0xfffffffeu, crc, nonce);
        return;
    }
    /* Program all pages through the last populated one in original order.
     * Finish the block from RAM even if the host misses a progress packet. */
    for (unsigned page = 0; page < count; ++page) {
        const uint32_t *source = BUFFER+page*(PAGE_BYTES/4);
        unsigned row = block*256u+page;
        DMA(0x130) = 0x00540004;
        DMA(0x134) = 0x14061b00;
        DMA(0x138) = 0x10280010;
        if (!program_page(row, source)) {
            report(TAG('F','A','I','L'), block, page, crc, nonce);
            return;
        }
        /* Reading the array both verifies progress and leaves status mode
         * before the next page's serial-data-input command. */
        if (!verify_programmed_sector(row, source)) {
            report(TAG('F','A','I','L'), block, 0x10000u+page, crc, nonce);
            return;
        }
    }
    report(TAG('D','O','N','E'), block, count, crc, nonce);
}
