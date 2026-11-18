/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Crystal701 VIDEO.APP: left taps keep the original movie navigation.
 * The right long press toggles left holds between volume and seeking. */
.set noreorder
.set nomacro
.text
.globl video_key_handler
video_key_handler:
    lui $t0,0x4943
    lui $t1,0x4943
    ori $t1,$t1,0x4b3c        /* appended zero-initialized VIDEO.APP state */
    lw $t2,-5312($t0)          /* player handle */
    lw $t3,4($t1)
    beq $t2,$t3,check_player
    nop
    sw $t2,4($t1)
    sw $zero,0($t1)
    sw $zero,8($t1)
check_player:
    beqz $t2,stock_handler
    ori $t0,$t0,0x3588
    lw $t2,0($a0)
    addiu $t3,$zero,13         /* GUI key message */
    bne $t2,$t3,stock_handler
    nop
    lw $t2,8($a0)
    beqz $t2,stock_handler
    nop
    lw $t3,0($t2)
    addiu $t4,$zero,0x40
    beq $t3,$t4,check_dialogs
    addiu $t4,$zero,0x80
    beq $t3,$t4,check_dialogs
    addiu $t4,$zero,0x1000
    bne $t3,$t4,stock_handler
    nop
    lw $t5,4($t2)
    lui $t6,0x1000
    bne $t5,$t6,stock_handler /* other actions on the right key stay stock */
    nop

check_dialogs:
    addiu $sp,$sp,-48
    sw $ra,44($sp)
    sw $s0,40($sp)
    sw $s1,36($sp)
    sw $s2,32($sp)
    sw $s3,28($sp)
    move $s0,$a0
    move $s1,$t2
    move $s2,$t0
    move $s3,$t1
    /* Match the stock dispatcher: an allocated widget only owns a key when
     * its window ID matches the destination returned for this message. */
    jal 0x4940c8e0
    sw $t3,20($sp)
    lw $t3,20($sp)
    move $t2,$s1
    lw $t4,0x0ccc($s2)
    beqz $t4,next_widget_ccc
    nop
    lw $t5,0($t4)
    beq $t5,$v0,delegate_stock
    nop
next_widget_ccc:
    lw $t4,0x0cd0($s2)
    beqz $t4,next_widget_cd0
    nop
    lw $t5,20($t4)
    beq $t5,$v0,delegate_stock
    nop
next_widget_cd0:
    lw $t4,0x0cbc($s2)
    beqz $t4,next_widget_cbc
    nop
    lw $t5,20($t4)
    beq $t5,$v0,delegate_stock
    nop
next_widget_cbc:
    lw $t4,0x0cdc($s2)
    beqz $t4,next_widget_cdc
    nop
    lw $t5,0($t4)
    beq $t5,$v0,delegate_stock
    nop
next_widget_cdc:
    lw $t4,0x0cc0($s2)
    beqz $t4,next_widget_cc0
    nop
    lw $t5,4($t4)
    beq $t5,$v0,delegate_stock
    nop
next_widget_cc0:
    lw $t6,0($s3)
    addiu $t4,$zero,0x1000
    beq $t3,$t4,handled
    nop
    bnez $t6,delegate_stock
    lw $t5,4($t2)
    lui $t4,0x1000
    beq $t5,$t4,handled
    lui $t4,0x0800
    beq $t5,$t4,handled
    lw $t6,8($s3)
    bne $t6,$t3,delegate_stock
    lui $t4,0x0200
    beq $t5,$t4,handled
    lui $t4,0x0100
    bne $t5,$t4,delegate_stock
    nop

handled:
    lw $t4,4($t2)
    addiu $t5,$zero,0x1000
    beq $t3,$t5,toggle_mode
    lw $t6,0($s3)
    lui $t5,0x1000
    beq $t4,$t5,volume_hold
    lui $t5,0x0800
    bne $t4,$t5,volume_release
    nop

volume_hold:
    /* The widget already acts on stock press and repeat events, updates the
     * volume through MSG_SERV, and removes itself on its stock timer. */
    sw $t3,8($s3)
    lw $a0,0x0cd8($s2)
    bnez $a0,send_volume_key
    nop
    jal 0x494008c4
    nop
    lw $a0,0x0cd8($s2)
    beqz $a0,consume
    move $a1,$s0
send_volume_key:
    lw $t4,4($s1)
    lui $t5,0x0800           /* first long event also makes one volume step */
    sw $t4,24($sp)
    sw $t5,4($s1)
    jal 0x4940c7a8            /* stock COMMONUI volume widget syscall */
    move $a1,$s0
    lw $t4,24($sp)
    sw $t4,4($s1)
    b consume
    nop

volume_release:
    sw $zero,8($s3)
    lw $a0,0x0cd8($s2)
    beqz $a0,consume
    nop
    jal 0x49400938
    nop
    b consume
    nop

toggle_mode:
    xori $t6,$t6,1
    sw $t6,0($s3)
    sw $zero,8($s3)
    lw $a0,0x0cd8($s2)
    beqz $a0,stop_seek
    nop
    jal 0x49400938
    nop
    b stop_seek
    nop

stop_seek:
    lw $t0,0x00b8($s2)        /* stock seek direction, zero when idle */
    beqz $t0,consume
    nop
    jal 0x4940a664
    addiu $a0,$s2,0x0090      /* stock stop, restores previous playback state */

consume:
    addiu $v0,$zero,1
    lw $s3,28($sp)
    lw $s2,32($sp)
    lw $s1,36($sp)
    lw $s0,40($sp)
    lw $ra,44($sp)
    jr $ra
    addiu $sp,$sp,48

delegate_stock:
    move $a0,$s0
    lw $s3,28($sp)
    lw $s2,32($sp)
    lw $s1,36($sp)
    lw $s0,40($sp)
    lw $ra,44($sp)
    b stock_handler
    addiu $sp,$sp,48

stock_handler:
    .word 0                 /* filled from the locally extracted prologue */
    j 0x4940a8bc
    .word 0                 /* filled from the locally extracted delay slot */

.balign 8
