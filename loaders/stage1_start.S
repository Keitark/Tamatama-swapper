/* SPDX-License-Identifier: Apache-2.0 */
    .set mips32r2
    .set noreorder
    .section .init.text,"ax"
    .globl start
start:
    di
    la $sp, 0xB4061AF0
    jal main
    nop
1:
    b 1b
    nop
