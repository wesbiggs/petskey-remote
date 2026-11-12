# Design note: the TeensyROM as a USB keyboard

Status: built, host-tested only, not yet tried on hardware. The firmware is on the `claude/brave-hypatia-ljyd94`
branch of [wesbiggs/TeensyROM](https://github.com/wesbiggs/TeensyROM) (`docs/USB_Keyboard.md` there has the
usage); the key code table comes from `tools/hid_keytable.py` here. Suggested by juararob: plug the TeensyROM
straight into a PC or a MiSTer and use the C64 as a real keyboard there, with no host process and no network.

What changed from the proposal below, once built:

* The core patch goes into `build-firmware.mjs`'s private copy of the core (`tools/lib/usb-keyboard.mjs`,
  flag `--usb-keyboard`), not the installed core. MinimalBoot gets the same USB type, so the board stays one
  device. The product ID is 0x048D, unassigned in Teensyduino 1.60-1.62 but not allocated by PJRC.
* Teensyduino's boot report declares key usages up to 0x7F only, so RO, KPCOMMA and the volume keys are
  left out of the table. Of the stock keymaps, only the positional layout's `=` (on RO) is affected.
* Protocol 2 is required. Keys before the keymap arrives are dropped, and a new hello needs the keymap again.
* A seventh key held at once gets no slot and isn't resent when one frees up, as on a 6KRO keyboard.
* `ATDT USB:0` and quoted names work too. The session state is malloc'd on `ATDT USB` and freed on hang-up.

```
 C64/C128 keyboard ──► PETSKEY REMOTE ──► SwiftLink at $DE00 ──► TeensyROM: protocol + keymap ──► USB HID keyboard ──► PC / MiSTer
```

The C64 program stays as it is. The TeensyROM firmware does what `netkbd_host.py` does today, and sends the
result out of its own USB device port as a standard boot-protocol keyboard instead of over TCP. Any USB host
accepts that with no driver: Windows, macOS, Linux, a PC's BIOS, and MiSTer (its Linux side passes USB
keyboards to the cores).

## Which USB port

The TeensyROM has two:

| Port | Role | Usable here? |
|---|---|---|
| USB-A host port (thumb drives, MIDI, mice; `USBHost myusbHost`) | host | **No.** A PC or MiSTer is a host too, and two hosts can't talk. The Teensy 4.1's second controller is wired and driven as host only. |
| The Teensy's micro-USB device port (`trserial`, the TeensyROM UI, flashing, device-side USB MIDI) | device | **Yes.** This is the one a computer plugs into. |

This is the opposite of the decision for USB audio in teensyrom-snoop (`docs/USB-AUDIO.md` there), which moved
audio *off* the device port and onto the host port with a dongle. That option doesn't exist for a keyboard,
so the two reasons that drove audio to the host port come back. Both are smaller here (next section).

## The two device-port problems

### 1. The USB descriptors

TeensyROM builds with Teensyduino's `USB_MIDI_SERIAL` (`usb=serialmidi` in `tools/build-firmware.mjs`, checked
with `#error` in `IOHandlers.h` and `MinimalBoot.ino`): CDC serial on endpoints 2–3 and MIDI on endpoint 4,
three interfaces. None of Teensyduino's stock USB types is Serial + MIDI + Keyboard. "Serial + Keyboard +
Mouse + Joystick" drops MIDI, and "All of the Above" changes everything.

What's needed: a custom type in the core's `usb_desc.h`, for example `USB_MIDI_SERIAL_KEYBOARD`, which is the
`USB_MIDI_SERIAL` block plus

```c
  #define NUM_ENDPOINTS         5
  #define NUM_INTERFACE         4
  #define KEYBOARD_INTERFACE    3
  #define KEYBOARD_ENDPOINT     5
  #define KEYBOARD_SIZE         8      // boot protocol
  #define KEYBOARD_INTERVAL     1
  #define ENDPOINT5_CONFIG      ENDPOINT_RECEIVE_UNUSED + ENDPOINT_TRANSMIT_INTERRUPT
```

`usb_desc.c` already places the keyboard interface after MIDI when `KEYBOARD_INTERFACE` is defined, and
`usb_keyboard.c` compiles in when it is. The build picks the type with `--build-property build.usbtype=...`,
which `build-firmware.mjs` already supports (`usbType`). The core's `usb_desc.h` has to be patched in the
installed core, like `tools/teensy_core_usb_basepri.py` in teensyrom-snoop does for `usb.c`.

The cost is the same as for audio: a different USB device for every user who gets this build. It needs its own
`PRODUCT_ID`, so Windows doesn't apply its cached setup for `0x16C0:0x0489` to a different interface layout,
and that can give a new COM port number. The flash tools and the TeensyROM UI have to find the serial port
on the new ID too. Options, in order of preference:

1. **A separate firmware build** ("TeensyROM + USB keyboard"). No change for anyone else. This is the easiest
   one to get upstream to accept, or to ship from the fork.
2. **A setting that picks the descriptor set at boot.** The core's descriptors are `const` tables chosen at
   compile time, but `usb_desc.c` could hold both sets and choose one from an EEPROM byte before `usb_init()`.
   Switching would mean a reboot. This takes more core surgery and is only worth it if option 1 catches on.

Boot protocol (6 keys plus modifiers) is enough. PETSKEY sends physical keys, and the C64 matrix ghosts on
three or more keys anyway (README, Limitations).

### 2. Interrupts off during device-port transfers

Every transmit on the device port goes through `schedule_transfer()` in the core's `usb.c` with all interrupts
off. In teensyrom-snoop, any USB write delayed the phi2 bus interrupt and crashed Ms. Pac-Man, stock firmware
included. The BASEPRI patch there (`tools/teensy_core_usb_basepri.py`) got rid of the late interrupts
(>250 ns); Ms. Pac-Man still crashed later with it, for a reason not yet settled.

For a keyboard this is much less of a problem:

* **Traffic is tiny.** One 8-byte report per change of key state: a few a second while typing, a few dozen at
  most in a burst. The snoop crash came from streams of hundreds of transfers a second.
* **What runs on the C64 is PETSKEY REMOTE, not a game.** The bus interrupt is serving the SwiftLink
  registers, and nothing on the C64 depends on exact cycle timing.
* A report is only sent while a host has configured the keyboard. With no host, `usb_keyboard_send()`
  returns at once (`!usb_configuration`). With a host that has stopped polling, it waits at most 50 ms once
  (`TX_TIMEOUT_MSEC`), then drops reports until the host comes back.

Still worth building with the BASEPRI patch if it's ready by then, and worth testing (see Testing).

## Firmware design

### Hooking the modem: `ATDT USB`

The C64 already dials with `ATDT host:port` and treats DCD on as connected (`dial_wait` in
`c64/petskey.asm`). The firmware recognizes one host name and connects to a built-in endpoint instead of
TCP:

* **`AT_DT()`** (`Swift_ATcommands.c`): before its call to `AT_C()` (which starts Ethernet), if the host
  name is `usb` (the command arrives in lower case after `SwiftTxBufToLcaseASSCII()`), set
  `UsbKbdConnected = true`, start a session, ignore the port, and return `ATRC_CONNECT`. This needs no
  Ethernet cable and no DHCP wait.
* **`PollingHndlr_SwiftLink()`** (`IOH_Swiftlink.c`) asks `client.connected()` in three places: the
  connection-change check, the choice between online and AT-command mode, and `SendSwiftTxOut()`. A small
  `HostConnected()` (`client.connected() || UsbKbdConnected`) replaces the first two. In online mode with
  `UsbKbdConnected`, each Tx byte goes to `UsbKbd_Feed(byte)` instead of into `SwiftTxOut`, and
  `SwiftRegStatus |= SwiftStatusTxEmpty` at once, since the bytes never wait for a TCP stack. The `+++`
  counting stays where it is.
* **Hang-up:** `+++` (what PETSKEY sends on quit, `hang_up`) and `InitHndlr_SwiftLink()` (a reset or a new
  cartridge choice) clear `UsbKbdConnected` and call `UsbKbd_ReleaseAll()`. The existing "connection lost"
  path then raises DCD and sends `NO CARRIER`.

Names like `ATDT USB` can't collide with a real host in practice. If that matters, `ATDT USB:0` (port 0 is
never a TCP port) is an alternative.

### The session (a C++ port of `Session` and `Translator`)

New file next to `USBMouse.c`, about 300 lines:

* **Parser**, as `Session._byte()` in `host/netkbd_host.py`: `$F0` hello (version, flags), `$F1` keymap (length,
  then 90 × 4 bytes, so a 360-byte buffer), `$00`–`$5F` down, `$80`–`$DF` up, `$FE` heartbeat, `$FC` bye.
  Anything before a hello is ignored, as now (the modem's echo of the dial command).
* **Translator**, as `Translator` there: the SHIFT state, per-key overrides that force SHIFT on or off,
  reference counts per target key, and CAPS LOCK as a tap per change. Its `emit(key, value)` becomes
  "set or clear one key in the report, then `usb_keyboard_send()`": modifier keys set bits in the
  modifier byte, others take one of the six slots.
* **Linux key code → HID usage table.** The keymap on the wire holds Linux key codes (below 256). A
  256-byte table maps them to HID keyboard-page usages: the inverse of Linux's `hid_keyboard[]` in
  `drivers/hid/hid-input.c`. Generate it with a script in `tools/` from the key names `netkbd_host.py`
  already knows, so the two can't drift apart. Codes with no keyboard-page usage map to 0 and are dropped.
* **Greeting:** on hello, queue "Linked to USB keyboard." (or "... (no USB host)" when
  `usb_configuration` is 0) with `AddToPETSCIIStrToRxQueueLN()`, where PETSKEY shows it on its status line.
* **Silence:** the C64 sends a heartbeat twice a second. As in `netkbd_host.py`, release every key after 3 s
  without a byte, so a C64 reset with a key down can't leave it repeating on the PC. There's no need to drop
  the "connection" after 15 s; a new hello restarts the session.
* **Protocol 1** C64s send no keymap. Either require protocol 2 (current PETSKEY always sends one) or build
  in the default C64 map, generated from `netkbd_host.py --print-keymap`. Requiring 2 is simpler.

Everything runs from the main loop (the SwiftLink polling handler), never from the bus interrupt.

### Not needed

* No change to `c64/petskey.asm`. Users type `USB` as the host and can clear the init command (`atc`),
  which would otherwise start Ethernet before each dial. A later PETSKEY could offer `USB` on first run if
  the modem's `atc` reply shows no network.
* No host-side software. The keymap compiler (`--compile-keymap`) still makes the `.K64`/`.K128` files.

## Behavior differences from the host process

* **Keyboard layout:** HID sends key positions, so the host's layout setting matters just as it does with
  uinput. The default keymap assumes a US layout; a MiSTer core or a PC set to another layout will give
  other characters for the punctuation keys.
* **Caps Lock LED:** the host sends LED state (`keyboard_leds`). The firmware could tell the C64, for
  example to show it on the status line. That's optional, and the C64 and host don't share Caps Lock today
  either.
* **Power:** the TeensyROM takes power from the C64. Check how the TR+ handles VUSB vs. the C64's 5 V before
  plugging it into a PC or MiSTer that is on while the C64 is off. This is the same situation as `trserial`
  today, so the answer is probably known already.

## Testing

1. **Descriptors, no keyboard code:** build the new USB type, plug into Windows, macOS and Linux. Check that
   serial, MIDI and an idle keyboard all enumerate, that `trserial` and the TeensyROM UI still find the
   port, and that the flash tools work.
2. **Bus timing:** with the keyboard type built in, run Ms. Pac-Man and other cartridge games with the device
   port plugged in, no typing. Nothing should change from the `USB_MIDI_SERIAL` build. Then run PETSKEY with
   fast typing and watch for SwiftLink glitches: dropped or doubled key events, which `--dry-run` style
   logging on the firmware's serial port would show. teensyrom-snoop's `--isr` late-interrupt counts are the
   precise measure, if that build is at hand.
3. **Translation:** every key in both keymaps on Linux (`evtest`), which compares directly with
   `netkbd_host.py --dry-run`'s output for the same keys. Then macOS and Windows for the layout-dependent
   keys, CAPS LOCK taps, held SHIFT plus overridden keys (`SHIFT+2` → `"`), and autorepeat (the host does it).
4. **Lifecycle:** quit with RUN/STOP + RESTORE (keys released, `NO CARRIER`), reset the C64 with a key held
   (released within 3 s), unplug and replug USB mid-session, and start PETSKEY with no USB host connected.
5. **MiSTer:** C64 and Amiga cores and the MiSTer menu (F12 is not on the C64 keyboard: map it, for example
   `SHIFT+RESTORE = KEY_F12`).

## Rough size

Firmware: a few hundred lines (session, translator, table, AT hook), a patch to the core's `usb_desc.h`, and a
build entry. Host side: a script to generate the key code table. C64 side: nothing.
