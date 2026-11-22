# Design note: the TeensyROM as a USB keyboard

Status: built, host-tested only, not yet tried on hardware. The firmware is on the
`claude/brave-hypatia-ljyd94` branch of [wesbiggs/TeensyROM](https://github.com/wesbiggs/TeensyROM)
(`docs/USB_Keyboard.md` there has the usage). Suggested by juararob: plug the TeensyROM straight into a PC or a
MiSTer and use the C64 as a real keyboard there, with no host process and no network.

```
 C64/C128 keyboard ──► PETSKEY REMOTE ──► "USB Keyboard" Special IO at $DE00 ──► TeensyROM ──► USB HID keyboard ──► PC / MiSTer
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
| `$DE03` | any value: heartbeat | 0 |
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

`IO_Handlers/IOH_UsbKbd.c`, only in the `--usb-keyboard` build. Nothing in it knows about C64 keys, keymaps,
Linux key codes or SHIFT faking, and the SwiftLink is the same as stock: the first version's `ATDT USB`
changes are reverted.

* **`IOHndlr_UsbKbd`**, "USB Keyboard": `InitHndlr`, `IO1Hndlr` and `PollingHndlr`, no IO2 or ROM handlers.
* **Its number.** It goes *last* in `enumIOHandlers` and `IOHandler[]`, after the CRT handlers, so every
  stock handler keeps its number. That also makes a downgrade safe: flash a stock image with USB Keyboard
  still selected, and the stock `IOHandlerInit()` finds the saved number out of range and loads nothing,
  instead of loading whichever handler has that number in the stock build. In the Settings menu it still
  comes right after GeoRAM: in this build only, the `rwRegNextIOHndlr` write in `IOH_TeensyROM.c` steps from
  GeoRAM to it, from it back to None, and the other way round.
* **`IO1Hndlr`** (bus interrupt): a read answers from values the main loop keeps ready (status, LEDs,
  version, ID), with no work before driving the bus. A write takes `DataPortWaitRead()`, stamps the time,
  and stores (register, byte) in a 64-entry single-producer, single-consumer ring. A release-all that finds
  the ring full sets a flag and records where in the ring it landed.
* **`PollingHndlr`** (main loop): before each entry it checks the flag, and if it is set, skips the entries
  written before the release-all and releases everything; entries written after it still apply. Each press
  or release updates a count per usage; the report is the modifier byte from `$E0`–`$E7` plus the first six
  held keys in press order, so held keys don't move between slots. Each event that changes the report sends
  it, so a press and release in one poll reach the host as two reports. With no host, the report is kept
  and sent once one sets up the keyboard. Then the 3 s silence rule, and the status and LED values.
* **`InitHndlr`** clears everything and sends one empty report. **`UsbKbd_End()`**, called from
  `SetUpMainMenuROM()` and before a remote launch, does the same when anything is held, so a C64 reset or
  a switch back to the menu leaves nothing down on the PC.

Tests: `Source/Teensy/tests/usb-kbd.test.js` (a host harness, like the mouse's).

## PETSKEY's side

One program does both. At start it looks at `$DE00`:

* `"TRK"` at `$DE04`: the USB Keyboard registers. No settings, no dialing: it draws the keyboard and types.
  The ACIA is never touched, and RESTORE's NMI handler doesn't read it.
* Otherwise, the ACIA's control and command registers must read back what it writes to them: a SwiftLink,
  and everything is as before.
* Neither: it says so (set Special IO to SwiftLink or USB Keyboard) and returns to BASIC, rather than
  waiting for a modem that isn't there.

In USB mode:

* **The translator runs on the 6502.** The key bytes the scan queues (the same protocol stream it sends a
  host) go through `usb_feed`, a copy of `netkbd_host.py`'s `Translator`: pick the plain or SHIFT binding,
  track which held keys force SHIFT on or off and send the one virtual left Shift to match, and turn the
  C128's CAPS LOCK into a tap per change. It writes usages to `$DE00`/`$DE01`. The firmware's press counts
  replace the translator's own, so the 6502 keeps only what each held key pressed, the keys that override
  SHIFT in press order, and how many SHIFT keys are down: 270 bytes of buffers, under 600 bytes of code.
* **The keymap files are unchanged.** They hold Linux key codes; the program has a built-in 256-byte Linux
  code → HID usage table (`c64/gen/hidtable.inc`, generated by `tools/hid_keytable.py`). A custom keymap
  works on both paths. Keys with no usage the boot report can carry (LINEFEED, MENU, the media keys, RO)
  type nothing, and are drawn grey on the on-screen keyboard.
* **Heartbeat:** the twice-a-second `$FE` becomes a write to `$DE03`. **Quitting** (RUN/STOP + RESTORE)
  writes `$DE02`.
* **Status line:** "USB KEYBOARD", and whether a computer has set up the keyboard (bit 0 of `$DE00`).

`test/test_usb_6502.py` (`make test-6502`, needs py65) runs the built program in a 6502 emulator: random key
sequences through every stock keymap, comparing the keys held after every byte with the host's
`Translator`, and the start-up detection for all three cases. The network path (`netkbd_host.py`, the
SwiftLink, protocol 2) doesn't change; `make e2e` still passes in VICE on a C64 and a C128.

## Behavior differences from the host process

* **Keyboard layout:** HID sends key positions, so the computer's layout setting matters just as it does with
  uinput. The default keymap assumes the computer is set to a US layout. Set to UK, for example, SHIFT+2 types
  `@` instead of `"`, because UK PCs swap those two. The keymap files are the fix, as on the network side.
* **Caps Lock LED:** readable at `$DE01`. PETSKEY could show it on the status line; that's optional.
* **Power:** the TeensyROM takes power from the C64. Check how the TR+ handles VUSB vs. the C64's 5 V before
  plugging it into a PC or MiSTer that is on while the C64 is off. This is the same situation as `trserial`
  today, so the answer is probably known already.

## Testing

Done (host only):

1. **The firmware's registers:** `usb-kbd.test.js` in the fork: reads, counts, the six slots, press order,
   a seventh key, invalid usages, release-all (also with a full queue, and landing mid-poll), the silence
   rule, no USB host, LEDs, teardown.
2. **PETSKEY's 6502 translator and detection:** `make test-6502`, as above.

Still to do, on a board:

3. **Descriptors:** plug into Windows, macOS and Linux. Check that serial, MIDI and an idle keyboard all
   enumerate, that `trserial` and the TeensyROM UI still find the port, and that the flash tools work.
4. **Bus timing:** run Ms. Pac-Man and other cartridge games on the keyboard build with the device port
   plugged in, no typing. Nothing should change from the `USB_MIDI_SERIAL` build. Then run PETSKEY with fast
   typing, and check with `evtest` for dropped or doubled events. teensyrom-snoop's `--isr` late-interrupt
   counts are the precise measure, if that build is at hand.
5. **Translation:** every key in both keymaps on Linux (`evtest`). Then macOS and Windows for the
   layout-dependent keys, CAPS LOCK taps, held SHIFT plus overridden keys (`SHIFT+2` → `"`), and autorepeat
   (the host does it).
6. **Lifecycle:** quit with RUN/STOP + RESTORE (keys released), reset the C64 with a key held (released
   within 3 s), unplug and replug USB mid-session, start PETSKEY with no USB host connected and with no
   Special IO selected, and step the Special IO list into and out of USB Keyboard both ways.
7. **MiSTer:** C64 and Amiga cores and the MiSTer menu (F12 is not on the C64 keyboard: map it, for example
   `SHIFT+RESTORE = KEY_F12`).
