/* SPDX-License-Identifier: GPL-3.0-or-later */
.set noreorder
.set nomacro
.section .entry,"ax",@progbits
.globl _entry
_entry:
    mfc0 $t0,$12,0
    lui $t1,0xffff
    ori $t1,$t1,0xfffe
    and $t0,$t0,$t1
    mtc0 $t0,$12,0
    ehb
    lui $sp,0xa080
    ori $sp,$sp,0x7fe0
    jal writer_main
    nop
    lui $t0,0xb406
    ori $t0,$t0,0x1000
    jr $t0
    nop
