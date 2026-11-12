; SPDX-License-Identifier: Apache-2.0
;
; PETSKEY Remote: the keyboard of a C64, or a C128 in 64 mode, as a network
; keyboard for a Linux machine. The program scans the keyboard matrix itself and
; sends every press and release of a physical key over a SwiftLink-style ACIA at
; $DE00 (the TeensyROM Ethernet modem, or any SwiftLink modem that dials with
; ATDT host:port) to host/netkbd_host.py, which types them on a uinput keyboard.
;
; While connected it shows a hi-res picture of the keyboard (a C64 or a C128 one)
; with what each key does on the Linux side; pressed keys light up, and holding
; SHIFT shows the shifted legends.
;
; Files, on the device the program was loaded from:
;   PETSKEY.CFG    settings: modem init command, host, port (written by the program)
;   PETSKEY.K64    keymap for a C64 keyboard \ made by tools/petskey_build.py or
;   PETSKEY.K128   keymap for a C128 keyboard / netkbd_host.py --compile-keymap
; The keymaps are also built in, for when the files are missing.
;
; Protocol (C64 -> host):
;   $F0 v f        hello: protocol version v, flags f (bit 0 = C128, bit 1 = PAL)
;   $F1 lo hi map  the keymap: lo/hi = its length, then MAP_BYTES bytes
;   $00-$5F        key down, code below;  $80-$DF key up (code | $80)
;   $FE            heartbeat, about twice a second
;   $FC            bye: release everything, ignore input until the next hello
; Codes: 0-63 C64 matrix, column ($DC00 bit driven low) * 8 + row ($DC01 bit);
;   64-87 C128 extended keys, 64 + K line ($D02F bit) * 8 + row; 88 RESTORE
;   (press only: sent as down + up); 89 C128 CAPS LOCK lock state.
; Host -> C64: text, shown on the status line.
;
; RUN/STOP + RESTORE hangs up and returns to BASIC.

   !cpu 6510
   !src "gen/consts.inc"

; ---------------------------------------------------------------------------
; system
CHROUT     = $ffd2
GETIN      = $ffe4
STOP       = $ffe1
SETLFS     = $ffba
SETNAM     = $ffbd
OPEN       = $ffc0
CLOSE      = $ffc3
LOAD       = $ffd5
SAVE       = $ffd8
JIFFYLO    = $a2
CURDEV     = $ba
PALFLAG    = $02a6
NMIVEC     = $0318
KERNALNMI  = $fe47

CIA1PRA    = $dc00
CIA1PRB    = $dc01
CIA1DDRA   = $dc02
CIA1DDRB   = $dc03
CIA1TALO   = $dc04
CIA1TAHI   = $dc05
CIA1ICR    = $dc0d
CIA1CRA    = $dc0e
CIA2PRA    = $dd00
CIA2DDRA   = $dd02
CIA2ICR    = $dd0d

VICCTRL1   = $d011
VICCTRL2   = $d016
VICMEM     = $d018
BORDER     = $d020
BGCOLOR    = $d021
VICKBD     = $d02f        ; C128 extended keyboard lines K0-K2
VICCLK     = $d030        ; C128 2 MHz register: $ff on a C64

SW_DATA    = $de00
SW_STAT    = $de01
SW_CMD     = $de02
SW_CTRL    = $de03
ST_RXFULL  = %00001000
ST_TXEMPTY = %00010000
ST_DCD     = %01000000    ; set = no carrier

P_HELLO    = $f0
P_MAP      = $f1
P_BYE      = $fc
P_HEART    = $fe
CODE_RESTORE = 88
CODE_CAPS    = 89

TICK_CYCLES = 8400        ; CIA1 timer A: ~117 Hz PAL, ~122 Hz NTSC
HEART_TICKS = 58          ; heartbeat ~twice a second (well inside TeensyROM's 1 s "+++" guard)
RESTORE_LIT = 15          ; ticks RESTORE stays lit on screen
KERNAL_TA_PAL  = $4025
KERNAL_TA_NTSC = $4295
INIT_TIMEOUT = 10         ; seconds to wait for the init command's OK
DIAL_TIMEOUT = 15         ; seconds to wait for the connection

; The two keyboard pictures: VIC bank 3 has the normal legends, bank 2 the
; shifted ones. The bitmaps sit under the BASIC and KERNAL ROMs: only written.
SCR_N      = $c000
BM_N       = $e000
SCR_S      = $8000
BM_S       = $a000
BANK_N     = 0            ; $DD00 bits for bank 3
BANK_S     = 1            ; bank 2

COL_PRESSED = 13          ; light green
COL_UNUSED  = 11          ; dark grey

STRMAX     = 40           ; init command / host
PORTMAX    = 5

; zero page
ptr        = $fb          ; general pointer, and $fc
ptr2       = $fd          ; and $fe
p1         = $57          ; and $58 (BASIC FP temporaries: free while we run)
p2         = $59          ; and $5a
q          = $5b          ; and $5c
leg        = $5d          ; and $5e: the legend being drawn
txt        = $5f          ; and $60: the text being drawn
fp         = $22          ; and $23: font pointer (a BASIC temporary)

; PETSCII
C_RETURN   = 13
C_LOWER    = 14
C_NOSWITCH = 8
C_CLEAR    = 147
C_DEL      = $14
C_WHITE    = 5
C_LTBLUE   = 154
C_LTGREEN  = 153
C_LTRED    = 150
C_GREY     = 155
C_YELLOW   = 158

!macro copy .src, .dst, .len {
   lda #<.src
   sta ptr
   lda #>.src
   sta ptr+1
   lda #<.dst
   sta ptr2
   lda #>.dst
   sta ptr2+1
   ldx #.len
   jsr memcpy
}

; ---------------------------------------------------------------------------
; BASIC stub: 2026 SYS start
   * = $0801
   !word BasicEnd
   !word 2026
   !byte $9e
   !byte (start/1000)+$30, ((start/100)%10)+$30, ((start/10)%10)+$30, (start%10)+$30
   !byte 0
BasicEnd
   !word 0

start:
   jsr init_once
   jsr load_keys
   jsr prepare_display
   jsr load_cfg
   jsr rx_on
flow:
   lda SW_STAT
   and #ST_DCD
   bne +
   lda #<msg_already         ; still connected from an earlier run
   ldy #>msg_already
   jsr print
   jmp go_live
+  lda cfg_ok
   beq need_setup
   jsr connect
   bcc go_live
need_setup:
   jsr setup
   bcs to_basic              ; RUN/STOP in the settings
   jsr rx_off
   jsr save_cfg
   jsr rx_on
   lda #1
   sta cfg_ok
   jsr connect
   bcs need_setup
go_live:
   jsr run_keyboard          ; carry set = quit, clear = connection lost
   bcc flow
   jsr hang_up
to_basic:
   jsr rx_off
   lda #<KERNALNMI
   sta NMIVEC
   lda #>KERNALNMI
   sta NMIVEC+1
   lda #<msg_bye
   ldy #>msg_bye
   jmp print

; ---------------------------------------------------------------------------
init_once:
   lda #0
   sta is128
   sta flags
   sta gwlen
   lda VICCLK
   cmp #$ff
   beq +
   inc is128
   lda #1
   sta flags
+  lda PALFLAG
   beq +
   lda flags
   ora #2
   sta flags
+  lda CURDEV
   cmp #8
   bcs +
   lda #8
+  sta dev
   lda #0
   sta BORDER
   sta BGCOLOR
   jsr print_title
   ; ACIA: no reset (it would report "no carrier" while TeensyROM is still
   ; connected), 38400, receive interrupts off until the files are loaded
   lda #0
   sta rxhead
   sta rxtail
   lda #<nmi_handler
   sta NMIVEC
   lda #>nmi_handler
   sta NMIVEC+1
   jsr rx_off
   lda #%00011111
   sta SW_CTRL
   ldx #0                    ; drop anything left in the receive register
-  lda SW_STAT
   and #ST_RXFULL
   beq +
   lda SW_DATA
+  dex
   bne -
   ; the layout for this keyboard
   lda is128
   beq +
   jmp layout_c128
+  +copy lay_c64_code, lay_code, LAY_C64_COUNT
   +copy lay_c64_x, lay_x, LAY_C64_COUNT
   +copy lay_c64_y, lay_y, LAY_C64_COUNT
   +copy lay_c64_w, lay_w, LAY_C64_COUNT
   +copy lay_c64_h, lay_h, LAY_C64_COUNT
   +copy lay_c64_cls, lay_cls, LAY_C64_COUNT
   +copy lay_c64_lbl, lay_lbl, LAY_C64_COUNT
   lda #LAY_C64_COUNT
   sta lay_count
   lda #<lay_c64_labels
   sta lay_labels
   lda #>lay_c64_labels
   sta lay_labels+1
   rts
layout_c128:
   +copy lay_c128_code, lay_code, LAY_C128_COUNT
   +copy lay_c128_x, lay_x, LAY_C128_COUNT
   +copy lay_c128_y, lay_y, LAY_C128_COUNT
   +copy lay_c128_w, lay_w, LAY_C128_COUNT
   +copy lay_c128_h, lay_h, LAY_C128_COUNT
   +copy lay_c128_cls, lay_cls, LAY_C128_COUNT
   +copy lay_c128_lbl, lay_lbl, LAY_C128_COUNT
   lda #LAY_C128_COUNT
   sta lay_count
   lda #<lay_c128_labels
   sta lay_labels
   lda #>lay_c128_labels
   sta lay_labels+1
   rts


; copy X bytes (1-255) from (ptr) to (ptr2)
memcpy:
   ldy #0
-  lda (ptr),y
   sta (ptr2),y
   iny
   dex
   bne -
   rts

; Bytes from the modem arrive under NMI (a real SwiftLink, or VICE, does not
; wait for the C64 to read each one, TeensyROM does) into rxbuf.
rx_on:
   lda #%00001001            ; DTR, RTS low/Tx ready, receive interrupt (NMI) on
   sta SW_CMD
   rts
rx_off:                      ; during disk access and back in BASIC
   lda #%00001011
   sta SW_CMD
   rts

; next received byte in A, carry set; carry clear if there is none
get_rx:
   ldx rxtail
   cpx rxhead
   beq +
   lda rxbuf,x
   inc rxtail
   sec
   rts
+  clc
   rts

drain_rx:
   lda rxhead
   sta rxtail
   rts

; NMI: a received byte (RX full) or RESTORE
nmi_handler:
   pha
   txa
   pha
   lda SW_STAT               ; (reading it also acknowledges the ACIA's interrupt)
   and #ST_RXFULL
   beq +
   lda SW_DATA
   ldx rxhead
   sta rxbuf,x
   inx
   cpx rxtail
   beq ++                    ; full: drop the byte
   stx rxhead
   jmp ++
+  lda #1
   sta restore_flag
++ pla
   tax
   pla
   rti

; ---------------------------------------------------------------------------
; Files

; The keymap for this machine: PETSKEY.K64 / .K128, else the built-in one.
load_keys:
   lda is128
   bne +
   lda #fn_k64_end-fn_k64
   ldx #<fn_k64
   ldy #>fn_k64
   jmp ++
+  lda #fn_k128_end-fn_k128
   ldx #<fn_k128
   ldy #>fn_k128
++ jsr SETNAM
   lda #1
   ldx dev
   ldy #0                    ; load to the address given below
   jsr SETLFS
   lda #0
   ldx #<keys_buf
   ldy #>keys_buf
   jsr LOAD
   bcs lk_default
   ldx #3
-  lda keys_buf,x
   cmp keys_magic,x
   bne lk_default
   dex
   bpl -
   lda #<msg_keys_loaded
   ldy #>msg_keys_loaded
   jmp print
lk_default:
   lda #<msg_keys_builtin
   ldy #>msg_keys_builtin
   jsr print
   lda is128
   bne +
   lda #<keys_c64
   ldx #>keys_c64
   jmp ++
+  lda #<keys_c128
   ldx #>keys_c128
++ sta ptr
   stx ptr+1
   lda #<keys_buf
   sta ptr2
   lda #>keys_buf
   sta ptr2+1
   ldx #<KEYS_DATA_SIZE      ; copy KEYS_DATA_SIZE bytes, page by page
   lda #>KEYS_DATA_SIZE
   sta tmp
-  lda tmp
   beq +
   ldy #0
--  lda (ptr),y
   sta (ptr2),y
   iny
   bne --
   inc ptr+1
   inc ptr2+1
   dec tmp
   jmp -
+  cpx #0
   beq +
   jsr memcpy
+  rts

load_cfg:
   lda #0
   sta cfg_ok
   lda #fn_cfg_end-fn_cfg
   ldx #<fn_cfg
   ldy #>fn_cfg
   jsr SETNAM
   lda #1
   ldx dev
   ldy #0
   jsr SETLFS
   lda #0
   ldx #<cfg
   ldy #>cfg
   jsr LOAD
   bcs lc_default
   ldx #3
-  lda cfg,x
   cmp cfg_magic,x
   bne lc_default
   dex
   bpl -
   lda #0                    ; make sure the strings are terminated
   sta cfg_init+STRMAX
   sta cfg_host+STRMAX
   sta cfg_port+PORTMAX
   lda cfg_host
   beq +
   inc cfg_ok
+  rts
lc_default:
   ldx #cfg_end-cfg-1
-  lda cfg_defaults,x
   sta cfg,x
   dex
   bpl -
   rts

save_cfg:
   lda #<msg_saving
   ldy #>msg_saving
   jsr print
   lda #fn_scratch_end-fn_scratch     ; scratch the old file (errors don't matter)
   ldx #<fn_scratch
   ldy #>fn_scratch
   jsr SETNAM
   lda #15
   ldx dev
   ldy #15
   jsr SETLFS
   jsr OPEN
   lda #15
   jsr CLOSE
   lda #fn_cfg_end-fn_cfg
   ldx #<fn_cfg
   ldy #>fn_cfg
   jsr SETNAM
   lda #1
   ldx dev
   ldy #0
   jsr SETLFS
   lda #<cfg
   sta ptr
   lda #>cfg
   sta ptr+1
   lda #ptr
   ldx #<cfg_end
   ldy #>cfg_end
   jsr SAVE
   bcc +
   lda #<msg_save_failed
   ldy #>msg_save_failed
   jsr print
+  rts

; ---------------------------------------------------------------------------
; Settings screen. Carry set = RUN/STOP (quit).
setup:
   lda #<msg_setup
   ldy #>msg_setup
   jsr print
   lda cfg_host
   bne su_fields
   lda gwlen                 ; no host yet: offer the gateway, which the
   bne +                     ; init command (ATC) reports
   lda cfg_init
   beq su_fields
   lda #<msg_asking_gw
   ldy #>msg_asking_gw
   jsr print
   jsr run_init
   lda gwlen
   beq su_fields
+  ldx #0
-  lda gateway,x
   sta cfg_host,x
   inx
   cpx gwlen
   bne -
   lda #0
   sta cfg_host,x
   lda #<msg_gw_offered
   ldy #>msg_gw_offered
   jsr print
su_fields:
   lda #<msg_ask_init
   ldy #>msg_ask_init
   jsr print
   lda #<cfg_init
   ldx #>cfg_init
   ldy #STRMAX
   jsr edit_text
   bcs su_quit
-  lda #<msg_ask_host
   ldy #>msg_ask_host
   jsr print
   lda #<cfg_host
   ldx #>cfg_host
   ldy #STRMAX
   jsr edit_text
   bcs su_quit
   lda cfg_host
   beq -                     ; a host is required
-  lda #<msg_ask_port
   ldy #>msg_ask_port
   jsr print
   lda #<cfg_port
   ldx #>cfg_port
   ldy #PORTMAX
   jsr edit_digits
   bcs su_quit
   lda cfg_port
   beq -
   clc
su_quit:
   rts

; Line editor: the buffer at A/X (zero-terminated), at most Y characters.
; Shows the current value to edit; RETURN accepts (carry clear), RUN/STOP
; cancels (carry set).
edit_digits:
   pha
   lda #1
   bne +
edit_text:
   pha
   lda #0
+  sta ed_digits
   pla
   sta ptr
   stx ptr+1
   sty ed_max
   ldy #0
-  lda (ptr),y
   beq +
   jsr CHROUT
   iny
   bne -
+  sty ed_len
ed_loop:
   lda #0
   sta $cc                   ; cursor on
-  jsr GETIN
   beq -
   pha
   jsr cursor_off
   pla
   cmp #C_RETURN
   beq ed_done
   cmp #3                    ; RUN/STOP
   beq ed_stop
   cmp #C_DEL
   beq ed_del
   ldx ed_digits
   beq +
   cmp #'0'
   bcc ed_loop
   cmp #'9'+1
   bcs ed_loop
   bcc ed_add
+  cmp #$20
   bcc ed_loop
   cmp #$80
   bcc ed_add
   cmp #$c1
   bcc ed_loop
   cmp #$db
   bcs ed_loop
ed_add:
   ldy ed_len
   cpy ed_max
   bcs ed_loop
   sta (ptr),y
   jsr CHROUT
   iny
   lda #0
   sta (ptr),y
   sty ed_len
   jmp ed_loop
ed_del:
   ldy ed_len
   beq ed_loop
   dey
   lda #0
   sta (ptr),y
   sty ed_len
   lda #C_DEL
   jsr CHROUT
   jmp ed_loop
ed_done:
   lda #C_RETURN
   jsr CHROUT
   clc
   rts
ed_stop:
   lda #C_RETURN
   jsr CHROUT
   sec
   rts

cursor_off:
   sei
   lda #1
   sta $cc
   lda $cf                   ; cursor currently shown?
   beq +
   lda #0
   sta $cf
   ldy $d3
   lda $ce
   sta ($d1),y
   lda $0287
   sta ($f3),y
+  cli
   rts

; ---------------------------------------------------------------------------
; Modem

; Init command, then ATDT. Carry clear when connected.
connect:
   lda #<msg_connecting
   ldy #>msg_connecting
   jsr print
   jsr print_hostport
   lda #C_RETURN
   jsr CHROUT
   jsr run_init
   jmp dial

; Send the init command (if any) and wait for OK or ERROR. Notes the gateway
; address if the reply has one (TeensyROM's ATC prints " Gateway IP: a.b.c.d").
run_init:
   lda cfg_init
   beq ri_done
   lda #C_GREY
   jsr CHROUT
   lda #<cfg_init
   ldy #>cfg_init
   jsr send_string
   lda #C_RETURN
   jsr send_byte_wait
   lda #INIT_TIMEOUT
   jsr timer_start
   lda #0
   sta linelen
ri_loop:
   jsr rx_line
   bcc ri_clock
   jsr check_gateway
   lda linebuf
   cmp #$4f                  ; "OK"
   bne +
   lda linebuf+1
   cmp #$4b
   beq ri_done
+  lda linebuf
   cmp #$45                  ; "ERROR"
   bne +
   lda linebuf+1
   cmp #$52
   beq ri_done
+  lda #0
   sta linelen
ri_clock:
   jsr STOP
   beq ri_done
   jsr timer_tick
   bcc ri_loop
ri_done:
   lda #C_WHITE
   jmp CHROUT

dial:
   lda #<at_dial
   ldy #>at_dial
   jsr send_string
   lda #<cfg_host
   ldy #>cfg_host
   jsr send_string
   lda #':'
   jsr send_byte_wait
   lda #<cfg_port
   ldy #>cfg_port
   jsr send_string
   lda #C_RETURN
   jsr send_byte_wait
   lda #C_GREY
   jsr CHROUT
   lda #DIAL_TIMEOUT
   jsr timer_start
   lda #0
   sta linelen
dial_wait:
   lda SW_STAT
   and #ST_DCD
   beq dial_connected
   jsr rx_line
   bcc dial_clock
   ; a reply line: NO ANSWER / NO CARRIER / BUSY / ERROR (ASCII capitals,
   ; PETSCII lower case) mean the dial failed
   lda linebuf
   cmp #$4e
   beq dial_failed
   cmp #$42
   beq dial_failed
   cmp #$45
   beq dial_failed
   lda #0
   sta linelen
dial_clock:
   jsr STOP
   beq dial_cancel
   jsr timer_tick
   bcc dial_wait
   lda #<msg_timeout
   ldy #>msg_timeout
   jsr print
   sec
   rts
dial_cancel:
   lda #<msg_cancel
   ldy #>msg_cancel
   jsr print
dial_failed:
   lda #<msg_failed
   ldy #>msg_failed
   jsr print
   sec
   rts
dial_connected:
   clc
   rts

; Take one received byte, if any, echo it, and collect lines. Carry set when a
; line is complete in linebuf (zero-terminated, linelen long).
rx_line:
   jsr get_rx
   bcs +
   rts
+  sta tmp
   jsr CHROUT
   lda tmp
   cmp #C_RETURN
   beq +
   cmp #10
   beq rl_none
   ldx linelen
   cpx #STRMAX
   bcs rl_none
   sta linebuf,x
   inc linelen
rl_none:
   clc
   rts
+  ldx linelen
   lda #0
   sta linebuf,x
   sec
   rts

; " Gateway IP: 192.168.7.1" -> gateway
check_gateway:
   ldx #0
-  lda linebuf,x
   beq cg_no
   cmp #' '
   bne +
   inx
   bne -
+  and #$7f                  ; G in any case
   ora #$20
   cmp #$67
   bne cg_no
-  lda linebuf,x             ; find the colon
   beq cg_no
   inx
   cmp #':'
   bne -
-  lda linebuf,x
   cmp #' '
   bne +
   inx
   bne -
+  ldy #0
-  lda linebuf,x
   cmp #'.'
   beq +
   cmp #'0'
   bcc ++
   cmp #'9'+1
   bcs ++
+  sta gateway,y
   inx
   iny
   cpy #15
   bne -
++ cpy #7                    ; at least "1.2.3.4"
   bcc cg_no
   sty gwlen
cg_no:
   rts

timer_start:
   sta t_secs
   lda #60
   sta t_jiff
   lda JIFFYLO
   sta t_last
   rts

; carry set once the time is up
timer_tick:
   lda JIFFYLO
   cmp t_last
   beq +
   sta t_last
   dec t_jiff
   bne +
   lda #60
   sta t_jiff
   dec t_secs
   bne +
   sec
   rts
+  clc
   rts

; After a quit: TeensyROM hangs up on "+++" with a second of silence either side.
hang_up:
   lda #<msg_hangup
   ldy #>msg_hangup
   jsr print
   jsr wait_second
   lda #'+'
   jsr send_byte_wait
   lda #'+'
   jsr send_byte_wait
   lda #'+'
   jsr send_byte_wait
   jsr wait_second
   jmp drain_rx

wait_second:                 ; ~1.3 s, needs the KERNAL interrupt running
   ldx #80
-- lda JIFFYLO
-  cmp JIFFYLO
   beq -
   dex
   bne --
   rts

send_byte_wait:
   pha
-  lda SW_STAT
   and #ST_TXEMPTY
   beq -
   pla
   sta SW_DATA
   rts

send_string:                 ; zero-terminated, at A/Y
   sta ptr
   sty ptr+1
   ldy #0
-  lda (ptr),y
   beq +
   jsr send_byte_wait
   iny
   bne -
+  rts

print:
   sta ptr
   sty ptr+1
   ldy #0
-  lda (ptr),y
   beq +
   jsr CHROUT
   iny
   bne -
+  rts

print_hostport:
   lda #<cfg_host
   ldy #>cfg_host
   jsr print
   lda #':'
   jsr CHROUT
   lda #<cfg_port
   ldy #>cfg_port
   jmp print

; ---------------------------------------------------------------------------
; The live part: KERNAL interrupts off, keyboard scanned ~120 times a second,
; the keyboard picture on screen. Carry set = RUN/STOP+RESTORE, clear = carrier lost.
run_keyboard:
   jsr drain_rx              ; whatever the modem said before the host
   lda #P_HELLO
   jsr send_byte_wait
   lda #PROTO_VER
   jsr send_byte_wait
   lda flags
   jsr send_byte_wait
   lda #P_MAP
   jsr send_byte_wait
   lda #<MAP_BYTES
   jsr send_byte_wait
   lda #>MAP_BYTES
   jsr send_byte_wait
   lda #<(keys_buf+4)
   sta ptr2
   lda #>(keys_buf+4)
   sta ptr2+1
   lda #<MAP_BYTES
   sta cnt
   lda #>MAP_BYTES
   sta cnt+1
-  ldy #0
   lda (ptr2),y
   jsr send_byte_wait
   inc ptr2
   bne +
   inc ptr2+1
+  lda cnt
   bne +
   dec cnt+1
+  dec cnt
   lda cnt
   ora cnt+1
   bne -

   ; status lines: who we are connected to, no host message yet
   ldx #0
   ldy #0
-  lda msg_conn_ascii,y
   beq +
   sta status_buf,x
   inx
   iny
   bne -
+  ldy #0
-  lda cfg_host,y
   beq +
   sta status_buf,x
   inx
   iny
   bne -
+  lda #':'
   sta status_buf,x
   inx
   ldy #0
-  lda cfg_port,y
   sta status_buf,x
   beq +
   inx
   iny
   bne -
+  lda #1
   jsr draw_status_buf
   lda #0
   sta status_buf
   sta hostlen
   sta host_ready
   lda #2
   jsr draw_status_buf
   jsr recolor_all

   lda #0
   sta qhead
   sta qtail
   sta restore_flag
   sta restore_timer
   sta heart
   sta shown_shift

   sei
   lda #$7f
   sta CIA1ICR
   sta CIA2ICR
   lda CIA1ICR
   lda CIA2ICR
   lda #<nmi_handler
   sta NMIVEC
   lda #>nmi_handler
   sta NMIVEC+1
   lda #$ff
   sta CIA1DDRA
   lda #0
   sta CIA1DDRB
   lda #<TICK_CYCLES
   sta CIA1TALO
   lda #>TICK_CYCLES
   sta CIA1TAHI
   lda #%00010001
   sta CIA1CRA
   jsr display_on

   lda #8
   ldx is128
   beq +
   lda #11
+  sta numlines
   ; keys already down are the baseline: they send nothing until pressed again
   jsr scan
   ldx #10
-  lda raw,x
   sta last,x
   sta stable,x
   dex
   bpl -
   jsr read_caps
   sta caps_prev

main_loop:
   jsr wait_tick
   jsr scan
   jsr debounce_and_send
   jsr check_caps
   lda restore_flag
   beq +
   lda #0
   sta restore_flag
   lda stable+7              ; RUN/STOP held too: quit
   bmi kb_quit
   lda #CODE_RESTORE
   jsr enqueue
   jsr show_key
   lda #CODE_RESTORE|$80
   jsr enqueue
   lda #RESTORE_LIT
   sta restore_timer
+  lda restore_timer
   beq +
   dec restore_timer
   bne +
   lda #CODE_RESTORE|$80
   jsr show_key
+  inc heart
   lda heart
   cmp #HEART_TICKS
   bcc +
   lda #0
   sta heart
   lda #P_HEART
   jsr enqueue
+  ; SHIFT (or SHIFT LOCK) held: show the shifted legends
   lda stable+1
   and #$80
   sta tmp
   lda stable+6
   and #$10
   ora tmp
   beq +
   lda #1
+  cmp shown_shift
   beq +
   sta shown_shift
   jsr set_bank
+  lda host_ready            ; a line of text from the host
   beq +
   lda #0
   sta host_ready
   lda #2
   jsr draw_status_buf
   lda #0
   sta hostlen
+  lda SW_STAT
   and #ST_DCD
   bne +
   jmp main_loop
+  jsr restore_system
   lda #<msg_lost
   ldy #>msg_lost
   jsr print
   clc
   rts

kb_quit:
   lda #P_BYE
   jsr enqueue
-  jsr service
   lda qhead
   cmp qtail
   bne -
   jsr restore_system
   sec
   rts

restore_system:
   lda #$ff
   sta CIA1PRA
   lda is128
   beq +
   lda #$ff
   sta VICKBD
+  lda #<KERNAL_TA_NTSC
   ldx #>KERNAL_TA_NTSC
   ldy PALFLAG
   beq +
   lda #<KERNAL_TA_PAL
   ldx #>KERNAL_TA_PAL
+  sta CIA1TALO
   stx CIA1TAHI
   lda #%00010001
   sta CIA1CRA
   jsr display_off
   lda #$81
   sta CIA1ICR
   cli
print_title:
   lda #<msg_title
   ldy #>msg_title
   jsr print
   lda #<version_pet
   ldy #>version_pet
   jsr print
   lda #<msg_title2
   ldy #>msg_title2
   jmp print

wait_tick:
-  jsr service
   lda CIA1ICR
   and #1
   beq -
   rts

; One byte each way if the ACIA is ready. Host text goes to status_buf; a
; RETURN marks it ready to show.
service:
   jsr get_rx
   bcc +
   jsr host_char
+  lda SW_STAT
   and #ST_TXEMPTY
   beq +
   ldx qtail
   cpx qhead
   beq +
   lda queue,x
   sta SW_DATA
   inc qtail
+  rts

host_char:
   ldx host_ready            ; still waiting to be drawn: drop
   bne ++
   cmp #C_RETURN
   bne +
   ldx hostlen
   lda #0
   sta status_buf,x
   inc host_ready
   rts
+  jsr pet_to_ascii
   beq ++
   ldx hostlen
   cpx #76
   bcs ++
   sta status_buf,x
   inc hostlen
++ rts

; PETSCII (lower-case set), or ASCII, to the font's ASCII; 0 if it has no glyph
pet_to_ascii:
   cmp #$20
   bcc ++
   cmp #$60
   bcc +                     ; space, digits, punctuation, letters
   cmp #$7b
   bcs +++
   and #$5f                  ; ASCII lower case
   rts
+++
   cmp #$c1
   bcc ++
   cmp #$db
   bcs ++
   and #$7f                  ; shifted letters: also capitals in the font
+  rts
++ lda #0
   rts

; Queue A for sending. Keeps A, X and Y. Drops the byte if the queue is full.
enqueue:
   stx eq_x
   ldx qhead
   sta queue,x
   inx
   cpx qtail
   beq +
   stx qhead
+  ldx eq_x
   rts

; ---------------------------------------------------------------------------
; Keyboard

scan:
   lda #$ff
   sta CIA1PRA
   lda CIA1PRB               ; low with no column selected: joystick 1, mask it
   sta joy_ok
   ldx #0
   lda #%11111110
   sta colsel
-  lda colsel
   sta CIA1PRA
   lda CIA1PRB
   eor #$ff
   and joy_ok
   sta raw,x
   sec
   rol colsel
   inx
   cpx #8
   bne -
   lda #$ff
   sta CIA1PRA
   lda is128
   beq ++
   lda #%11111110
   sta colsel
-  lda colsel
   sta VICKBD
   lda CIA1PRB
   eor #$ff
   and joy_ok
   sta raw,x
   sec
   rol colsel
   inx
   cpx #11
   bne -
   lda #$ff
   sta VICKBD
++ rts

; A change counts once two scans in a row agree. Each one is queued and shown.
debounce_and_send:
   ldx numlines
   dex
db_line:
   lda raw,x
   eor last,x
   eor #$ff
   sta agree
   lda raw,x
   sta last,x
   and agree
   sta tmp
   lda agree
   eor #$ff
   and stable,x
   ora tmp
   tay
   eor stable,x
   beq db_next
   sta changed
   tya
   sta stable,x
   txa
   asl
   asl
   asl
   sta codebase
   ldy #0
   lda #1
   sta bitmask
db_bit:
   lda changed
   and bitmask
   beq db_nextbit
   tya
   ora codebase
   sta tmp
   lda stable,x
   and bitmask
   bne +
   lda tmp
   ora #$80
   jmp ++
+  lda tmp
++ jsr enqueue
   jsr show_key
db_nextbit:
   asl bitmask
   iny
   cpy #8
   bne db_bit
db_next:
   dex
   bpl db_line
   rts

read_caps:                   ; C128 CAPS LOCK: bit 6 of $01, low when locked
   lda is128
   beq +
   lda $01
   and #$40
   eor #$40
   beq +
   lda #1
+  rts

check_caps:
   jsr read_caps
   cmp caps_prev
   beq ++
   sta caps_prev
   tax
   beq +
   lda #CODE_CAPS
   jsr enqueue
   jmp show_key
+  lda #CODE_CAPS|$80
   jsr enqueue
   jmp show_key
++ rts

; ---------------------------------------------------------------------------
; Display

display_on:
   lda CIA2DDRA
   ora #3
   sta CIA2DDRA
   lda #0
   jsr set_bank
   lda #%00001000            ; screen at bank offset 0, bitmap at $2000
   sta VICMEM
   lda #$3b                  ; bitmap mode
   sta VICCTRL1
   lda #$08
   sta VICCTRL2
   rts

display_off:
   lda CIA2PRA
   ora #3                    ; bank 0
   sta CIA2PRA
   lda #$17                  ; text at $0400, lower-case characters
   sta VICMEM
   lda #$1b
   sta VICCTRL1
   lda #$08
   sta VICCTRL2
   rts

; A = 0: normal legends, 1: shifted
set_bank:
   tax
   lda CIA2PRA
   and #$fc
   ora bank_bits,x
   sta CIA2PRA
   rts
bank_bits: !byte BANK_N, BANK_S

; Light (down) or unlight (up) every key drawn for the event in A. Keeps A, X, Y.
show_key:
   sta sk_ev
   stx sk_x
   sty sk_y
   and #$7f
   sta sk_code
   ldx #0
sk_loop:
   lda lay_code,x
   cmp sk_code
   bne sk_next
   stx sk_i
   lda normcol,x
   bit sk_ev
   bmi +
   lda #COL_PRESSED
+  jsr color_key
   ldx sk_i
sk_next:
   inx
   cpx lay_count
   bne sk_loop
   lda sk_ev
   ldx sk_x
   ldy sk_y
   rts

recolor_all:
   ldx #0
-  stx sk_i
   lda normcol,x
   jsr color_key
   ldx sk_i
   inx
   cpx lay_count
   bne -
   rts

; Colour (A, 0-15) the cells of key X in both screens
color_key:
   asl
   asl
   asl
   asl
   sta ck_val
   lda lay_x,x
   sta ck_x
   lda lay_w,x
   sta ck_w
   lda lay_h,x
   sta ck_h
   lda lay_y,x
   sta ck_row
ck_row_loop:
   ldx ck_row
   lda row40_lo,x
   clc
   adc ck_x
   sta p1
   sta p2
   lda row40_hi,x
   adc #0
   tax
   clc
   adc #>SCR_N
   sta p1+1
   txa
   clc
   adc #>SCR_S
   sta p2+1
   ldy ck_w
   dey
   lda ck_val
-  sta (p1),y
   sta (p2),y
   dey
   bpl -
   inc ck_row
   dec ck_h
   bne ck_row_loop
   rts

; Clear both pictures, draw every key twice (normal and shifted legends), and the
; fixed status lines. Takes about a second.
prepare_display:
   lda #<msg_drawing
   ldy #>msg_drawing
   jsr print
   lda #>BM_N
   ldx #32
   jsr clear_pages
   lda #>BM_S
   ldx #32
   jsr clear_pages
   lda #>SCR_N
   ldx #4
   jsr clear_pages
   lda #>SCR_S
   ldx #4
   jsr clear_pages
   ldx #0
-  stx cur_key
   jsr draw_key_both
   ldx cur_key
   inx
   cpx lay_count
   bne -
   ; status line colours
   lda #$70                  ; yellow title
   ldx #0
   jsr color_row
   lda #$e0                  ; light blue connection line
   ldx #1
   jsr color_row
   lda #$f0                  ; light grey host messages
   ldx #2
   jsr color_row
   lda #$c0                  ; grey help line
   ldx #24
   jsr color_row
   ldx #0
   lda is128
   beq +
   ldx #title128-title64
+  ldy #0
-  lda title64,x
   sta status_buf,y
   beq +
   inx
   iny
   bne -
+  lda #' '
   sta status_buf,y
   iny
   ldx #0
-  lda version_ascii,x
   sta status_buf,y
   beq +
   inx
   iny
   bne -
+  lda #0
   jsr draw_status_buf
   ldx #0
-  lda help_ascii,x
   sta status_buf,x
   beq +
   inx
   bne -
+  lda #24
   jmp draw_status_buf

; A = first page, X = number of pages
clear_pages:
   sta ptr+1
   lda #0
   sta ptr
   tay
-  sta (ptr),y
   iny
   bne -
   inc ptr+1
   dex
   bne -
   rts

; Colour a whole status row X with screen byte A
color_row:
   sta tmp
   lda row40_lo,x
   sta p1
   sta p2
   lda row40_hi,x
   pha
   clc
   adc #>SCR_N
   sta p1+1
   pla
   clc
   adc #>SCR_S
   sta p2+1
   ldy #39
   lda tmp
-  sta (p1),y
   sta (p2),y
   dey
   bpl -
   rts

draw_key_both:
   lda lay_x,x
   sta k_x
   lda lay_y,x
   sta k_y
   lda lay_w,x
   sta k_w
   lda lay_h,x
   sta k_h
   ; its normal colour: dark grey if it does nothing
   lda lay_code,x
   cmp #NO_CODE
   beq dk_unused
   ldy #0                    ; code * 4, 16 bits
   sty tmp2
   asl
   rol tmp2
   asl
   rol tmp2
   clc
   adc #<(keys_buf+4)
   sta ptr
   lda tmp2
   adc #>(keys_buf+4)
   sta ptr+1
   ldy #0
   lda (ptr),y               ; flags of the plain binding
   bpl dk_unused             ; not defined
   iny
   lda (ptr),y               ; Linux key
   beq dk_unused             ; NONE
   ldy lay_cls,x
   lda class_colors,y
   jmp +
dk_unused:
   lda #COL_UNUSED
+  sta normcol,x
   lda #0
   jsr get_legend
   lda #>BM_N
   jsr draw_key
   ldx cur_key
   lda #1
   jsr get_legend
   lda #>BM_S
   jsr draw_key
   ldx cur_key
   lda normcol,x
   jmp color_key

class_colors: !byte 15, 12, 14, 15   ; normal, modifier, function, keypad

; Legend of key X, A = 0 normal / 1 shifted, into (leg) (LEGEND_CHARS bytes)
get_legend:
   sta tmp
   lda lay_lbl,x
   cmp #$ff
   beq gl_keymap
   jsr times7                ; fixed label from the layout (both states)
   clc
   adc lay_labels
   sta leg
   lda tmp2
   adc lay_labels+1
   sta leg+1
   rts
gl_keymap:
   lda lay_code,x
   cmp #NO_CODE
   bne +
   lda #<empty_legend
   sta leg
   lda #>empty_legend
   sta leg+1
   rts
+  asl                       ; (code * 2 + state) * 7
   ora tmp
   jsr times7
   clc
   adc #<(keys_buf+4+MAP_BYTES)
   sta leg
   lda tmp2
   adc #>(keys_buf+4+MAP_BYTES)
   sta leg+1
   rts

; A * 7 -> A (low), tmp2 (high)
times7:
   sta t7
   lda #0
   sta tmp2
   lda t7
   asl
   rol tmp2
   asl
   rol tmp2
   asl
   rol tmp2                  ; *8
   sec
   sbc t7
   bcs +
   dec tmp2
+  rts

; Draw key (k_x, k_y, k_w, k_h) with legend (leg) into the bitmap whose high
; byte is A: a light key face with the legend cut out of it.
draw_key:
   sta dk_base
   lda k_w
   asl
   asl
   asl
   sta cb_bw
   lda #0
   sta cb_bw+1
   jsr set_band_offsets
   ; face
   lda #<canvas
   sta q
   lda #>canvas
   sta q+1
   lda #0
   sta fb
fk_band:
   lda #0
   sta fc
fk_cell:
   ldx fc
   inx
   lda #$ff
   cpx k_w
   bne +
   lda #$fe                  ; gap on the right
+  sta fv
   lda #$ff                  ; rounded corners
   ldx fc
   bne +
   lda #$7f
+  inx
   cpx k_w
   bne +
   and #$fd
+  sta cm
   ldy #7
fk_row:
   lda fv
   cpy #0
   bne +
   ldx fb
   bne +
   and cm                    ; top row
+  ldx fb
   inx
   cpx k_h
   bne fk_store
   cpy #7
   bne +
   lda #0                    ; gap at the bottom
   beq fk_store
+  cpy #6
   bne fk_store
   and cm
fk_store:
   sta (q),y
   dey
   bpl fk_row
   lda q
   clc
   adc #8
   sta q
   bcc +
   inc q+1
+  inc fc
   lda fc
   cmp k_w
   bne fk_cell
   inc fb
   lda fb
   cmp k_h
   bne fk_band
   ; legend, centred
   ldy #0
-  lda (leg),y
   beq +
   iny
   cpy #LEGEND_CHARS
   bne -
+  tya
   beq dk_copy
   asl
   asl
   sta tmp                   ; n*4 = text width + 1
   lda cb_bw
   sec
   sbc tmp                   ; bw - 1 - (n*4 - 1)
   lsr
   sta gx
   lda #0
   sta gx+1
   lda k_h
   asl
   asl
   asl
   sec
   sbc #6
   lsr
   sta gy
   lda #0                    ; clear pixels
   sta draw_mode
   sty dk_n
   lda leg
   sta txt
   lda leg+1
   sta txt+1
   jsr draw_text_n
dk_copy:
   ; band by band into the bitmap at (k_x, k_y)
   lda #<canvas
   sta q
   lda #>canvas
   sta q+1
   lda #0
   sta fb
-  lda k_y
   clc
   adc fb
   tax
   lda k_x
   asl
   asl
   asl
   sta p1
   lda #0
   rol
   sta p1+1
   lda p1
   clc
   adc row320_lo,x
   sta p1
   lda p1+1
   adc row320_hi,x
   clc
   adc dk_base
   sta p1+1
   ldy cb_bw
   dey
--  lda (q),y
   sta (p1),y
   dey
   cpy #$ff
   bne --
   lda q
   clc
   adc cb_bw
   sta q
   bcc +
   inc q+1
+  inc fb
   lda fb
   cmp k_h
   bne -
   rts

; band_off[b] = b * cb_bw, for b = 0..3
set_band_offsets:
   lda #0
   sta band_off
   sta band_off+1
   ldx #0
-  lda band_off,x
   clc
   adc cb_bw
   sta band_off+2,x
   lda band_off+1,x
   adc cb_bw+1
   sta band_off+3,x
   inx
   inx
   cpx #6
   bne -
   rts

; Draw dk_n characters from (txt) at gx/gy in the canvas
draw_text_n:
   lda #0
   sta ti
-  ldy ti
   cpy dk_n
   beq +
   lda (txt),y
   jsr glyph
   lda gx
   clc
   adc #4
   sta gx
   bcc ++
   inc gx+1
++ inc ti
   jmp -
+  rts

; One 3x5 character A at gx/gy
glyph:
   and #$7f
   sta tmp
   lda #0
   sta fp+1
   lda tmp
   asl
   rol fp+1
   asl
   rol fp+1                  ; *4
   clc
   adc tmp
   sta fp
   lda fp+1
   adc #0
   sta fp+1                  ; *5
   lda fp
   clc
   adc #<font
   sta fp
   lda fp+1
   adc #>font
   sta fp+1
   lda #0
   sta grow
gl_row:
   ldy grow
   lda (fp),y
   sta gbits
   lda gx
   sta pxl
   lda gx+1
   sta pxh
   lda gy
   clc
   adc grow
   sta py
-  asl gbits
   bcc +
   jsr plot
+  lda gbits
   beq +
   inc pxl
   bne -
   inc pxh
   jmp -
+  inc grow
   lda grow
   cmp #5
   bne gl_row
   rts

; Set or clear (draw_mode $80 / 0) the canvas pixel pxh:pxl, py
plot:
   lda py
   lsr
   lsr
   lsr
   asl
   tax
   lda pxl
   and #$f8
   clc
   adc band_off,x
   sta q
   lda pxh
   adc band_off+1,x
   sta q+1
   lda py
   and #7
   clc
   adc q
   sta q
   lda q+1
   adc #0
   sta q+1
   lda q
   clc
   adc #<canvas
   sta q
   lda q+1
   adc #>canvas
   sta q+1
   lda pxl
   and #7
   tax
   lda bit_tab,x
   ldy #0
   bit draw_mode
   bmi +
   eor #$ff
   and (q),y
   sta (q),y
   rts
+  ora (q),y
   sta (q),y
   rts
bit_tab: !byte $80, $40, $20, $10, $08, $04, $02, $01

; Draw status_buf (zero-terminated ASCII) as status row A, in both pictures
draw_status_buf:
   sta st_row
   lda #<320
   sta cb_bw
   lda #>320
   sta cb_bw+1
   jsr set_band_offsets
   lda #<canvas
   sta ptr
   lda #>canvas
   sta ptr+1
   lda #0
   ldx #2
   tay
-  sta (ptr),y               ; clear 512 bytes (320 needed)
   iny
   bne -
   inc ptr+1
   dex
   bne -
   ldy #0
-  lda status_buf,y
   beq +
   iny
   cpy #80
   bne -
+  sty dk_n
   lda #4
   sta gx
   lda #0
   sta gx+1
   lda #1
   sta gy
   lda #$80
   sta draw_mode
   lda #<status_buf
   sta txt
   lda #>status_buf
   sta txt+1
   jsr draw_text_n
   ; copy 320 bytes to both bitmaps
   ldx st_row
   lda row320_lo,x
   sta p1
   sta p2
   lda row320_hi,x
   pha
   clc
   adc #>BM_N
   sta p1+1
   pla
   clc
   adc #>BM_S
   sta p2+1
   lda #<canvas
   sta q
   lda #>canvas
   sta q+1
   ldy #0
-  lda (q),y
   sta (p1),y
   sta (p2),y
   iny
   bne -
   inc q+1
   inc p1+1
   inc p2+1
-  lda (q),y
   sta (p1),y
   sta (p2),y
   iny
   cpy #320-256
   bne -
   rts

; ---------------------------------------------------------------------------
; Text

msg_title:
   !byte C_CLEAR, C_LOWER, C_NOSWITCH, C_YELLOW
   !pet "PETSKEY Remote ", 0
msg_title2:
   !byte C_GREY
   !pet " - C64 keyboard for Linux", C_RETURN, C_RETURN, C_WHITE, 0
msg_keys_loaded:
   !byte C_GREY
   !pet "Keymap loaded from disk.", C_RETURN, C_WHITE, 0
msg_keys_builtin:
   !byte C_GREY
   !pet "No keymap file: using the built-in one.", C_RETURN, C_WHITE, 0
msg_drawing:
   !byte C_GREY
   !pet "Drawing the keyboard...", C_RETURN, C_WHITE, 0
msg_setup:
   !byte C_RETURN, C_YELLOW
   !pet "Settings", C_RETURN, C_GREY
   !pet "Edit with DEL, RETURN accepts a line,", C_RETURN
   !pet "RUN/STOP quits.", C_RETURN, C_WHITE, 0
msg_asking_gw:
   !byte C_GREY
   !pet "Asking the modem for the gateway...", C_RETURN, 0
msg_gw_offered:
   !byte C_RETURN, C_LTGREEN
   !pet "The gateway is suggested as the host.", C_RETURN, C_WHITE, 0
msg_ask_init:
   !byte C_RETURN, C_LTBLUE
   !pet "Modem init command (empty for none):", C_RETURN, C_WHITE, 0
msg_ask_host:
   !byte C_LTBLUE
   !pet "Host (name or IP address):", C_RETURN, C_WHITE, 0
msg_ask_port:
   !byte C_LTBLUE
   !pet "Port:", C_RETURN, C_WHITE, 0
msg_saving:
   !byte C_GREY
   !pet "Saving settings...", C_RETURN, C_WHITE, 0
msg_save_failed:
   !byte C_LTRED
   !pet "Could not save the settings.", C_RETURN, C_WHITE, 0
msg_connecting:
   !byte C_RETURN, C_WHITE
   !pet "Connecting to ", 0
msg_timeout:
   !byte C_LTRED, C_RETURN
   !pet "No answer within 15 seconds.", C_RETURN, C_WHITE, 0
msg_cancel:
   !byte C_LTRED, C_RETURN
   !pet "Cancelled.", C_RETURN, 0
msg_failed:
   !byte C_LTRED
   !pet "Could not connect.", C_RETURN, C_WHITE, 0
msg_already:
   !byte C_GREY
   !pet "Already connected, reusing the link.", C_RETURN, C_WHITE, 0
msg_lost:
   !byte C_LTRED
   !pet "Connection lost.", C_RETURN, C_WHITE, 0
msg_hangup:
   !byte C_GREY
   !pet "Hanging up...", C_RETURN, 0
msg_bye:
   !byte C_GREY
   !pet "Bye.", C_RETURN, 0

at_dial:     !pet "atdt", 0

; on-screen (the font is ASCII)
title64:     !text "PETSKEY REMOTE - C64 KEYBOARD", 0
title128:    !text "PETSKEY REMOTE - C128 KEYBOARD", 0
help_ascii:  !text "RUN/STOP+RESTORE: QUIT     HOLD SHIFT: SHIFTED KEYS", 0
msg_conn_ascii: !text "CONNECTED TO ", 0
empty_legend: !fill LEGEND_CHARS, 0

; file names (unshifted PETSCII is ASCII capitals)
fn_k64:      !text "PETSKEY.K64"
fn_k64_end
fn_k128:     !text "PETSKEY.K128"
fn_k128_end
fn_cfg:      !text "PETSKEY.CFG"
fn_cfg_end
fn_scratch:  !text "S0:PETSKEY.CFG"
fn_scratch_end
keys_magic:  !text "PKM", 1
cfg_magic:   !text "PKC", 1

cfg_defaults:
   !text "PKC", 1
   !pet "atc"
   !fill STRMAX+1-3, 0
   !fill STRMAX+1, 0
   !pet "6526"
   !fill PORTMAX+1-4, 0

; ---------------------------------------------------------------------------
; Tables and generated data

row40_lo:  !for r, 0, 24 { !byte <(r*40) }
row40_hi:  !for r, 0, 24 { !byte >(r*40) }
row320_lo: !for r, 0, 24 { !byte <(r*320) }
row320_hi: !for r, 0, 24 { !byte >(r*320) }

   !src "gen/layout.inc"
   !src "gen/font.inc"
   !src "gen/version.inc"

keys_c64:  !binary "gen/PETSKEY.K64", , 2
keys_c128: !binary "gen/PETSKEY.K128", , 2

; ---------------------------------------------------------------------------
; Variables
cfg:                         ; PETSKEY.CFG is this block, saved as it is
              !fill 4, 0
cfg_init:     !fill STRMAX+1, 0
cfg_host:     !fill STRMAX+1, 0
cfg_port:     !fill PORTMAX+1, 0
cfg_end

is128:        !byte 0
flags:        !byte 0
dev:          !byte 8
cfg_ok:       !byte 0
gwlen:        !byte 0
gateway:      !fill 16, 0
linelen:      !byte 0
linebuf:      !fill STRMAX+1, 0
t_secs:       !byte 0
t_jiff:       !byte 0
t_last:       !byte 0
ed_digits:    !byte 0
ed_max:       !byte 0
ed_len:       !byte 0
tmp:          !byte 0
tmp2:         !byte 0
t7:           !byte 0
cnt:          !word 0
numlines:     !byte 8
restore_flag: !byte 0
restore_timer: !byte 0
heart:        !byte 0
caps_prev:    !byte 0
joy_ok:       !byte 0
colsel:       !byte 0
agree:        !byte 0
changed:      !byte 0
codebase:     !byte 0
bitmask:      !byte 0
eq_x:         !byte 0
qhead:        !byte 0
qtail:        !byte 0
rxhead:       !byte 0
rxtail:       !byte 0
shown_shift:  !byte 0
hostlen:      !byte 0
host_ready:   !byte 0
sk_ev:        !byte 0
sk_x:         !byte 0
sk_y:         !byte 0
sk_code:      !byte 0
sk_i:         !byte 0
ck_val:       !byte 0
ck_x:         !byte 0
ck_w:         !byte 0
ck_h:         !byte 0
ck_row:       !byte 0
cur_key:      !byte 0
k_x:          !byte 0
k_y:          !byte 0
k_w:          !byte 0
k_h:          !byte 0
dk_base:      !byte 0
dk_n:         !byte 0
fb:           !byte 0
fc:           !byte 0
fv:           !byte 0
cm:           !byte 0
cb_bw:        !word 0
band_off:     !fill 8, 0
gx:           !word 0
gy:           !byte 0
ti:           !byte 0
grow:         !byte 0
gbits:        !byte 0
pxl:          !byte 0
pxh:          !byte 0
py:           !byte 0
draw_mode:    !byte 0
st_row:       !byte 0
raw:          !fill 11, 0
last:         !fill 11, 0
stable:       !fill 11, 0
lay_count:    !byte 0
lay_labels:   !word 0
lay_code:     !fill LAYOUT_MAX, 0
lay_x:        !fill LAYOUT_MAX, 0
lay_y:        !fill LAYOUT_MAX, 0
lay_w:        !fill LAYOUT_MAX, 0
lay_h:        !fill LAYOUT_MAX, 0
lay_cls:      !fill LAYOUT_MAX, 0
lay_lbl:      !fill LAYOUT_MAX, 0
normcol:      !fill LAYOUT_MAX, 0
status_buf:   !fill 81, 0

end_of_file:
; buffers after the end of the file (not saved in the .prg)
queue      = (end_of_file + 255) & $ff00
rxbuf      = queue + 256
canvas     = rxbuf + 256
keys_buf   = canvas + 512
end_of_program = keys_buf + KEYS_DATA_SIZE
   !if end_of_program > SCR_S { !error "program overlaps the screens at $8000" }
