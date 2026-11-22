# Design note: the TeensyROM as a USB keyboard

Status: design revised, not yet built. Suggested by juararob: plug the TeensyROM straight into a PC or a
MiSTer and use the C64 as a real keyboard there, with no host process and no network.

```
 C64/C128 keyboard ──► PETSKEY (USB build) ──► "USB Keyboard" Special IO at $DE00 ──► TeensyROM ──► USB HID keyboard ──► PC / MiSTer
```

A new Special IO, "USB Keyboard", gives the C64 a few registers in IO1. A program writes "press HID usage
*n*" and "release usage *n*" to them, and the TeensyROM sends the result out of its own USB device port as a
standard boot-protocol keyboard. Any USB host accepts that with no driver: Windows, macOS, Linux, a PC's BIOS,
and MiSTer (its Linux side passes USB keyboards to the cores).

The registers know nothing about PETSKEY, C64 key matrices or keymaps. Any 6502 program can use them. PETSKEY
is one user: it does its own keymap translation on the C64 and writes HID usages.

## Why a Special IO, not the SwiftLink

The first version, on the `claude/brave-hypatia-ljyd94` branch of
[wesbiggs/TeensyROM](https://github.com/wesbiggs/TeensyROM), hooked the SwiftLink: PETSKEY dialed `ATDT USB`,
and the firmware ran a copy of `netkbd_host.py`'s session and translator on the bytes. That works, but:

* **Only PETSKEY could use it.** After `ATDT USB` the firmware expected PETSKEY's protocol: a hello, a
  360-byte keymap indexed by C64 matrix position and holding Linux key codes, then matrix positions. Another
  program would have had to invent a fake C64 matrix to press one PC key.
* **It changed the SwiftLink.** `AT_DT()`, the connection checks in `PollingHndlr_SwiftLink()` and the Tx path
  all gained a second case. That's the code upstream would be most careful about.
* **It carried app logic in the firmware:** the keymap parser, the Linux key code → HID table, and the
  translator's SHIFT faking.

The USB mouse (`IO_Handlers/USBMouse.c`) is the pattern here, in the other direction: a small register block,
an ID string for software to find it, the bus interrupt doing only what it has to, and the USB work in the
main loop.

The cost: a Special IO is exclusive, so the keyboard can't run beside the SwiftLink, an REU or GeoRAM. That
doesn't matter for a keyboard program. The code is written as a self-contained block like the mouse's, so it
can sit beside another handler later if someone wants that.

## The registers

IO1, `$DE00`–`$DE06`. The other IO1 addresses and all of IO2 are open bus.

| Address | Write | Read |
|---|---|---|
| `$DE00` | press HID usage *n* | status: bit 0 = a USB host has set up the keyboard, bit 1 = event queue full |
| `$DE01` | release usage *n* | the host's LEDs, as HID sends them: bit 0 Num Lock, 1 Caps Lock, 2 Scroll Lock, 3 Compose, 4 Kana |
| `$DE02` | any value: release every key | interface version, 1 |
| `$DE03` | any value: heartbeat | — |
| `$DE04`–`$DE06` | — | `"TRK"`, so a program can tell the registers are there |

* **Usages** are from HID's keyboard page (0x07): `$04` is A, `$28` Enter, `$E0`–`$E7` the modifiers (left
  Ctrl, Shift, Alt, GUI, then the right ones). Usage 0, and usages above `$7F` other than the modifiers, are
  ignored: Teensyduino's boot report declares 0–`$7F` only, so a host would drop them anyway.
* **Presses are counted per usage.** Two presses of the same usage need two releases before the key goes
  up. A program whose keys can overlap on one PC key (two C64 keys both bound to Shift, say) doesn't have to
  count them itself. "Release every key" clears all the counts, so a program that loses track can always
  recover.
* **More than six keys:** the report holds six non-modifier keys. A seventh held key gets no slot until one
  frees up, then appears. (A real 6KRO keyboard sends a rollover error instead. This is friendlier, and the C64
  keyboard ghosts at three keys anyway.)
* **Heartbeat:** if keys are down and nothing has been written to the registers for 3 seconds, the firmware
  releases every key. That covers a C64 reset or crash with a key down, which would otherwise leave the key
  repeating on the PC. A program that holds keys must write something at least every second or so: `$DE03` if
  nothing else. With no keys down there's no timeout.
* **Queue full:** writes go into a 64-entry queue that the main loop empties within a millisecond or two, so
  typing never comes near it. A program that writes in a tight loop can check bit 1 of `$DE00`. A write to a
  full queue is dropped, except "release every key", which always takes effect.
* **No USB host** (bit 0 clear): the writes still count, so the keys are right when a host appears; nothing
  goes out until then.

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
three interfaces. None of Teensyduino's stock USB types is Serial + MIDI + Keyboard.

The fork already has this part: a `USB_MIDI_SERIAL_KEYBOARD` type patched into `build-firmware.mjs`'s private
copy of the core (`tools/lib/usb-keyboard.mjs`, flag `--usb-keyboard`), which is the `USB_MIDI_SERIAL` block
plus

```c
  #define NUM_ENDPOINTS         5
  #define NUM_INTERFACE         4
  #define KEYBOARD_INTERFACE    3
  #define KEYBOARD_ENDPOINT     5
  #define KEYBOARD_SIZE         8      // boot protocol
  #define KEYBOARD_INTERVAL     1
  #define ENDPOINT5_CONFIG      ENDPOINT_RECEIVE_UNUSED + ENDPOINT_TRANSMIT_INTERRUPT
```

MinimalBoot gets the same USB type, so the board stays one device. It needs its own product ID, so Windows
doesn't apply its cached setup for `0x16C0:0x0489` to a different interface layout; the build uses `0x048D`,
unassigned in Teensyduino 1.60–1.62 but not allocated by PJRC. The serial port can get a new COM number, and
the flash tools and the TeensyROM UI have to find it on the new ID.

So this stays **a separate firmware build** ("TeensyROM + USB keyboard"), with no change for anyone else.
Picking the descriptor set at boot from an EEPROM byte would take more core surgery and is only worth it if
the build catches on.

### 2. Interrupts off during device-port transfers

Every transmit on the device port goes through `schedule_transfer()` in the core's `usb.c` with all interrupts
off. In teensyrom-snoop, any USB write delayed the phi2 bus interrupt and crashed Ms. Pac-Man, stock firmware
included. The BASEPRI patch there (`tools/teensy_core_usb_basepri.py`) got rid of the late interrupts
(>250 ns); Ms. Pac-Man still crashed later with it, for a reason not yet settled.

For a keyboard this is much less of a problem:

* **Traffic is tiny.** One 8-byte report per change of key state: a few a second while typing, a few dozen at
  most in a burst. The snoop crash came from streams of hundreds of transfers a second.
* **Only a keyboard program runs while the Special IO is selected,** not a game. The bus interrupt serves
  the keyboard registers, and nothing on the C64 depends on exact cycle timing.
* A report is only sent while a host has configured the keyboard. With no host, `usb_keyboard_send()`
  returns at once (`!usb_configuration`). With a host that has stopped polling, it waits at most 50 ms once
  (`TX_TIMEOUT_MSEC`), then drops reports until the host comes back.

Still worth building with the BASEPRI patch if it's ready by then, and worth testing (see Testing).

## Firmware design

### The handler

`IO_Handlers/IOH_UsbKbd.c`, the handler, and `IO_Handlers/UsbKbd.c`, the register block, both only in the
`--usb-keyboard` build:

* **`IOHndlr_UsbKbd`**, "USB Keyboard": `InitHndlr`, `IO1Hndlr` and `PollingHndlr`, no IO2 or ROM handlers.
  It goes into `enumIOHandlers` and `IOHandler[]` right after `IOH_GeoRAM`, and `LastSelectableIOH` moves to
  it. Appending after the last selectable handler keeps every stock handler's number, so a Special IO choice
  saved by the stock firmware means the same thing in this build.
* **`IO1Hndlr`** (bus interrupt): a read answers from values the main loop keeps ready (status, LEDs,
  version, ID). It does no arithmetic before driving the bus, as `USBMouseIORead()` does. A write takes
  `DataPortWaitRead()` and stores one (register, byte) pair in a single-producer, single-consumer ring buffer,
  and stamps the time of the last write. A write to `$DE02` is queued like the others; if the queue is full,
  it sets a "release all" flag instead.
* **`PollingHndlr`** (main loop): if the flag is set, it drops everything queued so far and releases every
  key, so nothing written before the "release all" is applied after it. Then it empties the queue. Each event updates a
  256-entry count table. The 8-byte report follows from the counts: the modifier byte from `$E0`–`$E7`, then
  the first six other usages that are held, kept in press order so held keys don't move between slots. After
  each event that changes the report it calls `usb_keyboard_send()`, so a press and release that both arrive
  in one poll still reach the host as two reports. It also applies the 3 s silence rule and copies
  `keyboard_leds` and `usb_configuration` into the read values.
* **`InitHndlr`**: clears the counts, the queue and the report, and sends one empty report, so switching to
  the handler, or a TeensyROM reset that re-runs it, never leaves a key down on the PC.

Nothing in the firmware knows about C64 keys, keymaps, Linux key codes or SHIFT faking. `UsbKbdKeys.h` and
this repo's `tools/hid_keytable.py` are no longer needed there (the table moves to the keymap compiler; see
below). The `ATDT USB` changes to `IOH_Swiftlink.c` and `Swift_ATcommands.c` come out, so the SwiftLink is the
same as stock.

### Using it

The user flashes the `_usbkbd` image, sets Settings → Special IO to "USB Keyboard", and launches the program.
A program checks `$DE04`–`$DE06` for `"TRK"` before using the registers, and can show "no USB host" from bit 0
of `$DE00`.

## PETSKEY's side

PETSKEY REMOTE gets a second output path. The keyboard scan, the keyboard drawing, the settings and the keymap
files stay shared. Choosing `USB` as the host picks the register path instead of dialing: it checks for
`"TRK"`, and says to select the "USB Keyboard" Special IO if it isn't there. Whether that ends up as one
program or a separate `petskey-usbhid` build depends on how much room the second path takes; one program
means one disk and the same setup screen for both.

* **The translator moves to the 6502.** It is `netkbd_host.py`'s `Translator`: for each key down, pick the
  plain or SHIFT binding, track which held keys force SHIFT on or off and send the one virtual SHIFT to
  match, and turn the C128's CAPS LOCK into a tap per change. It writes usages to `$DE00`/`$DE01` instead of
  sending Linux key events. The firmware's press counts replace the translator's own per-key counts, so the
  6502 version needs only the list of held keys that override SHIFT, the SHIFT key state, and what each
  held key pressed (90 bytes). Estimate: a few hundred bytes of code.
* **The keymap files hold HID usages for this path.** `--compile-keymap --usb` writes the same layout as now
  (90 keys × plain and SHIFT binding × 2 bytes: flags and a key) with HID usages instead of Linux key codes,
  using the table in `tools/hid_keytable.py`. A key that has no usage the boot report can carry (LINEFEED,
  MENU, the media keys, RO) is an error at compile time instead of silently typing nothing. The network path
  keeps its Linux-code files, so the disk has both, for example `PETSKEY.K64` and `PETSKEY.U64`.
* **Heartbeat:** PETSKEY's existing twice-a-second timer writes `$DE03` instead of sending `$FE`.
* **Quitting** (RUN/STOP + RESTORE) writes `$DE02`.

The network path (`netkbd_host.py`, the SwiftLink, protocol 2) doesn't change.

## Behavior differences from the host process

* **Keyboard layout:** HID sends key positions, so the computer's layout setting matters just as it does with
  uinput. The default keymap assumes the computer is set to a US layout. Set to UK, for example, SHIFT+2 types
  `@` instead of `"`, because UK PCs swap those two. The keymap files are the fix, as on the network side.
* **Caps Lock LED:** readable at `$DE01`. PETSKEY could show it on the status line; that's optional.
* **Power:** the TeensyROM takes power from the C64. Check how the TR+ handles VUSB vs. the C64's 5 V before
  plugging it into a PC or MiSTer that is on while the C64 is off. This is the same situation as `trserial`
  today, so the answer is probably known already.

## Testing

1. **Host-side unit tests in the fork,** like `tests/usb_mouse_harness.cpp`: the register reads, the queue,
   the counts, the report built from them (modifiers, six slots, press order, a seventh key), "release all"
   with a full queue, and the silence rule.
2. **VICE, for PETSKEY's 6502 translator:** a stand-in for the registers in `test/vice_e2e.py` that logs the
   writes, compared with `netkbd_host.py --dry-run` for the same key sequence, run through the same keymap.
3. **Descriptors, no keyboard code:** build the new USB type, plug into Windows, macOS and Linux. Check that
   serial, MIDI and an idle keyboard all enumerate, that `trserial` and the TeensyROM UI still find the
   port, and that the flash tools work.
4. **Bus timing:** run Ms. Pac-Man and other cartridge games on the keyboard build with the device port
   plugged in, no typing. Nothing should change from the `USB_MIDI_SERIAL` build. Then run PETSKEY with fast
   typing, and check with `evtest` for dropped or doubled events. teensyrom-snoop's `--isr` late-interrupt
   counts are the precise measure, if that build is at hand.
5. **Translation:** every key in both keymaps on Linux (`evtest`). Then macOS and Windows for the
   layout-dependent keys, CAPS LOCK taps, held SHIFT plus overridden keys (`SHIFT+2` → `"`), and autorepeat
   (the host does it).
6. **Lifecycle:** quit with RUN/STOP + RESTORE (keys released), reset the C64 with a key held (released
   within 3 s), unplug and replug USB mid-session, and start PETSKEY with no USB host connected and with
   another Special IO selected.
7. **MiSTer:** C64 and Amiga cores and the MiSTer menu (F12 is not on the C64 keyboard: map it, for example
   `SHIFT+RESTORE = KEY_F12`).

## Rough size

Firmware: the handler and register block, perhaps 150 lines, plus the USB type and build flag the fork
already has. PETSKEY: the register output path and the 6502 translator, a few hundred bytes. Host tools: a
`--usb` option for the keymap compiler.
