;; テスト専用の最小 crt0 (GBDK 非依存)。GBDK の代わりに PyBoy でエンジンを検証するためだけに使う。
	.module crt0
	.globl _main
	.globl _shim_vbl_count
	.globl _shadow_OAM

	.area _HEADER (ABS)
	.org 0x40              ; VBlank
	jp vbl_isr
	.org 0x48
	reti
	.org 0x50
	reti
	.org 0x58
	reti
	.org 0x60
	reti

	.org 0x100
	nop
	jp init

	.org 0x150
init:
	di
	ld sp, #0xDFFF
	;; WRAM 0xC000-0xDFEF をゼロクリア
	ld hl, #0xC000
	ld bc, #0x1FF0
1$:
	xor a
	ld (hl+), a
	dec bc
	ld a, b
	or c
	jr nz, 1$
	;; HRAM クリア
	ld hl, #0xFF80
	ld b, #0x7E
2$:
	xor a
	ld (hl+), a
	dec b
	jr nz, 2$
	;; OAM DMA ルーチンを HRAM へ
	ld hl, #dma_src
	ld de, #0xFF80
	ld b, #dma_end - dma_src
3$:
	ld a, (hl+)
	ld (de), a
	inc de
	dec b
	jr nz, 3$
	;; 初期化済みデータのコピー
	ld de, #s__INITIALIZER
	ld hl, #s__INITIALIZED
	ld bc, #l__INITIALIZER
4$:
	ld a, b
	or c
	jr z, 5$
	ld a, (de)
	ld (hl+), a
	inc de
	dec bc
	jr 4$
5$:
	call gsinit
	ld a, #0x01
	ld (0x2000), a
	xor a
	ldh (0x0F), a      ; IF
	ld a, #0x01
	ldh (0xFF), a      ; IE = VBlank
	ld a, #0xD1        ; LCD on, BG on, tile data 0x8000, win map 0x9C00
	ldh (0x40), a
	ei
	call _main
6$:
	halt
	jr 6$

vbl_isr:
	push af
	push bc
	push de
	push hl
	call 0xFF80
	ld hl, #_shim_vbl_count
	inc (hl)
	pop hl
	pop de
	pop bc
	pop af
	reti

dma_src:
	ld a, #0xC0
	ldh (0x46), a
	ld a, #40
7$:
	dec a
	jr nz, 7$
	ret
dma_end:

	.area _HOME
	.area _CODE
	.area _INITIALIZER
	.area _GSINIT
gsinit::
	.area _GSFINAL
	ret
	.area _DATA
	.area _INITIALIZED
	.area _BSEG
	.area _BSS
	.area _HEAP

	.area _OAM (ABS)
	.org 0xC000
_shadow_OAM::
	.ds 0xA0
