/* SPDX-License-Identifier: Apache-2.0 */
/* Minimal numerical V100 USB register map used by this project.
 * Address meanings were learned from actions-hacking and our device tests.
 * This replaces the build dependency on the external common headers;
 * it does not erase the USB driver's documented research provenance.
 */
#pragma once
#include <stdint.h>
#define REG8(a) (*(volatile uint8_t *)(uintptr_t)(a))
#define REG16(a) (*(volatile uint16_t *)(uintptr_t)(a))
#define REG32(a) (*(volatile uint32_t *)(uintptr_t)(a))
#define USB_BKDOOR 0xb029040bu
#define USB_ENDPRST 0xb02901a2u
#define USB_EP0CS 0xb0290002u
#define USB_EP0INDATA_W0 0xb0290100u
#define USB_EP0OUTDATA_W0 0xb0290140u
#define USB_FIFO1DATA 0xb0290084u
#define USB_FIFO2DATA 0xb0290088u
#define USB_FIFO3DATA 0xb029008cu
#define USB_FRMNRH 0xb02901a5u
#define USB_FRMNRL 0xb02901a4u
#define USB_HCIN2MAXPCKH 0xb02901e5u
#define USB_HCIN2MAXPCKL 0xb02901e4u
#define USB_HCIN4MAXPCKH 0xb02901e9u
#define USB_HCIN4MAXPCKL 0xb02901e8u
#define USB_HCOUT2MAXPCKH 0xb02903e5u
#define USB_HCOUT2MAXPCKL 0xb02903e4u
#define USB_HCOUT3MAXPCKH 0xb02903e7u
#define USB_HCOUT3MAXPCKL 0xb02903e6u
#define USB_IN0BC 0xb0290001u
#define USB_IN1CON 0xb029000eu
#define USB_IN2BCH 0xb0290015u
#define USB_IN2BCL 0xb0290014u
#define USB_IN2CON 0xb0290016u
#define USB_IN2CS 0xb0290017u
#define USB_IN2STADDR 0xb0290348u
#define USB_IN4CON 0xb0290026u
#define USB_IN4STADDR 0xb0290350u
#define USB_INXIEN 0xb0290194u
#define USB_INXIRQ 0xb0290188u
#define USB_OUT0BC 0xb0290000u
#define USB_OUT1BCH 0xb0290009u
#define USB_OUT1BCL 0xb0290008u
#define USB_OUT1CON 0xb029000au
#define USB_OUT1CS 0xb029000bu
#define USB_OUT2BCH 0xb0290011u
#define USB_OUT2BCL 0xb0290010u
#define USB_OUT2CON 0xb0290012u
#define USB_OUT2CS 0xb0290013u
#define USB_OUT2STADDR 0xb0290308u
#define USB_OUT3BCH 0xb0290019u
#define USB_OUT3BCL 0xb0290018u
#define USB_OUT3CON 0xb029001au
#define USB_OUT3CS 0xb029001bu
#define USB_OUT3STADDR 0xb029030cu
#define USB_OUTXIEN 0xb0290196u
#define USB_OUTXIRQ 0xb029018au
#define USB_SETUPDATA_W0 0xb0290180u
#define USB_SETUPDATA_W1 0xb0290184u
#define USB_USBCS 0xb02901a3u
#define USB_USBEIRQ 0xb0290400u
#define USB_USBIEN 0xb0290198u
#define USB_USBIRQ 0xb029018cu
