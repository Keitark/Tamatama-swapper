/* SPDX-License-Identifier: Apache-2.0 */
/* Crystal701 V100 SRAM bridge: receive a checked stage 2 into uncached DRAM.
 * No NAND controller addresses or flash commands occur in this program.
 */
#include <stdint.h>
#include "v100_usb.h"

#define DRAM_ENTRY 0xA0800000u
#define MAX_IMAGE 65536u
#define READY0 0x31304452u /* RD01 */
#define PING0  0x31304750u /* PG01 */
#define PONG0  0x31474e50u /* PNG1 */
#define LOAD0  0x3130444cu /* LD01 */
#define DONE0  0x314e4f44u /* DON1 */
#define EXEC0  0x31305845u /* EX01 */
#define MAGIC1 0x37303143u /* C107 */

static void send4(uint32_t a, uint32_t b, uint32_t c, uint32_t d)
{
    while (REG8(USB_IN2CS) & 2) { }
    REG32(USB_FIFO2DATA) = a;
    REG32(USB_FIFO2DATA) = b;
    REG32(USB_FIFO2DATA) = c;
    REG32(USB_FIFO2DATA) = d;
    REG8(USB_IN2BCL) = 16;
    REG8(USB_IN2BCH) = 0;
    REG8(USB_IN2CS) = 16;
    while (REG8(USB_IN2CS) & 2) { }
}

static uint32_t recv_word_count(void)
{
    REG8(USB_OUT1CS) = 1;
    while (REG8(USB_OUT1CS) & 2) { }
    return (uint32_t)(REG8(USB_OUT1BCL) |
                      ((uint32_t)REG8(USB_OUT1BCH) << 8)) >> 2;
}

static uint32_t roll(uint32_t sum, uint32_t word)
{
    return ((sum << 5) | (sum >> 27)) ^ word;
}

void main(void)
{
    uint32_t length = 0, sum = 0;
    send4(READY0, MAGIC1, DRAM_ENTRY, MAX_IMAGE);
    for (;;) {
        uint32_t n = recv_word_count();
        uint32_t op = 0, magic = 0, arg = 0, size = 0;
        for (uint32_t i = 0; i < n; ++i) {
            uint32_t w = REG32(USB_FIFO1DATA);
            if (i == 0) op = w;
            if (i == 1) magic = w;
            if (i == 2) arg = w;
            if (i == 3) size = w;
        }
        if (n != 4 || magic != MAGIC1) continue;
        if (op == PING0) {
            send4(PONG0, MAGIC1, DRAM_ENTRY, MAX_IMAGE);
            continue;
        }
        if (op == LOAD0 && arg == DRAM_ENTRY && size &&
            size <= MAX_IMAGE && !(size & 3)) {
            uint32_t *dst = (uint32_t *)DRAM_ENTRY;
            uint32_t remaining = size;
            sum = 0;
            while (remaining) {
                n = recv_word_count();
                uint32_t bytes = n << 2;
                if (!n || bytes > remaining || bytes > 512) {
                    send4(0x31525245u, MAGIC1, remaining, sum); /* ERR1 */
                    for (;;) { }
                }
                for (uint32_t i = 0; i < n; ++i) {
                    uint32_t word = REG32(USB_FIFO1DATA);
                    *dst++ = word;
                    sum = roll(sum, word);
                }
                remaining -= bytes;
            }
            length = size;
            send4(DONE0, MAGIC1, length, sum);
        } else if (op == EXEC0 && arg == DRAM_ENTRY &&
                   size == sum && length) {
            ((void (*)(void))DRAM_ENTRY)();
            for (;;) { }
        }
    }
}
