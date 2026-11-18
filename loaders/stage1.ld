/* SPDX-License-Identifier: Apache-2.0 */
OUTPUT_ARCH(mips)
ENTRY(start)
MEMORY { IRAM (xrw) : ORIGIN = 0xb4061000, LENGTH = 2K }
SECTIONS {
  .text : ALIGN(4) { KEEP(*(.init.text*)) *(.text*) *(.rodata*) } > IRAM
  .data : ALIGN(4) { *(.data*) } > IRAM
  .bss (NOLOAD) : ALIGN(4) { *(.sbss*) *(.bss*) *(COMMON) } > IRAM
  /DISCARD/ : { *(.MIPS.abiflags) *(.reginfo) *(.comment) }
  ASSERT(. <= 0xb4061800, "stage 1 exceeds verified 2 KiB window")
}
