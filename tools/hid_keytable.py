#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""The Linux key code -> USB HID usage table for the TeensyROM's USB keyboard firmware.

  hid_keytable.py               print the C header
  hid_keytable.py <out.h>       write it (the TeensyROM fork keeps it as
                                Source/Teensy/MinimalBoot/Common/IO_Handlers/UsbKbdKeys.h)

The keymap the C64 sends holds Linux key codes (docs/USB-KEYBOARD.md). On the TeensyROM
they become HID keyboard-page usages, the inverse of Linux's own HID -> key table
(hid_keyboard[] in drivers/hid/hid-input.c), so a key types the same on a PC as through
netkbd_host.py on a Pi. The table is written out by name for every key netkbd_host.py
knows, so a new key there fails the tests here until it gets a usage or None.

Teensyduino's boot keyboard report declares usages 0-0x7F plus the eight modifiers
(0xE0-0xE7). A usage above 0x7F is outside it and a host would drop it, so the keys that
only have one up there (RO, KPCOMMA, the volume keys) are None, as are the keys Linux
maps from no keyboard-page usage at all (LINEFEED, MENU, PRINT, the media keys).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'host'))
import netkbd_host as host  # noqa: E402

REPORT_MAX = 0x7F            # Usage Maximum of the keys array in Teensyduino's descriptor
MODIFIERS = range(0xE0, 0xE8)

# Linux key name (netkbd_host.LINUX_KEYS) -> HID usage, keyboard page (0x07)
HID_USAGES = {
    'ESC': 0x29, '1': 0x1E, '2': 0x1F, '3': 0x20, '4': 0x21, '5': 0x22, '6': 0x23, '7': 0x24,
    '8': 0x25, '9': 0x26, '0': 0x27,
    'MINUS': 0x2D, 'EQUAL': 0x2E, 'BACKSPACE': 0x2A, 'TAB': 0x2B,
    'Q': 0x14, 'W': 0x1A, 'E': 0x08, 'R': 0x15, 'T': 0x17, 'Y': 0x1C, 'U': 0x18, 'I': 0x0C,
    'O': 0x12, 'P': 0x13,
    'LEFTBRACE': 0x2F, 'RIGHTBRACE': 0x30, 'ENTER': 0x28, 'LEFTCTRL': 0xE0,
    'A': 0x04, 'S': 0x16, 'D': 0x07, 'F': 0x09, 'G': 0x0A, 'H': 0x0B, 'J': 0x0D, 'K': 0x0E,
    'L': 0x0F,
    'SEMICOLON': 0x33, 'APOSTROPHE': 0x34, 'GRAVE': 0x35, 'LEFTSHIFT': 0xE1, 'BACKSLASH': 0x31,
    'Z': 0x1D, 'X': 0x1B, 'C': 0x06, 'V': 0x19, 'B': 0x05, 'N': 0x11, 'M': 0x10,
    'COMMA': 0x36, 'DOT': 0x37, 'SLASH': 0x38, 'RIGHTSHIFT': 0xE5, 'KPASTERISK': 0x55,
    'LEFTALT': 0xE2, 'SPACE': 0x2C, 'CAPSLOCK': 0x39,
    'F1': 0x3A, 'F2': 0x3B, 'F3': 0x3C, 'F4': 0x3D, 'F5': 0x3E, 'F6': 0x3F, 'F7': 0x40,
    'F8': 0x41, 'F9': 0x42, 'F10': 0x43,
    'NUMLOCK': 0x53, 'SCROLLLOCK': 0x47,
    'KP7': 0x5F, 'KP8': 0x60, 'KP9': 0x61, 'KPMINUS': 0x56, 'KP4': 0x5C, 'KP5': 0x5D,
    'KP6': 0x5E, 'KPPLUS': 0x57, 'KP1': 0x59, 'KP2': 0x5A, 'KP3': 0x5B, 'KP0': 0x62,
    'KPDOT': 0x63,
    '102ND': 0x64, 'F11': 0x44, 'F12': 0x45, 'RO': None,             # RO is 0x87
    'KPENTER': 0x58, 'RIGHTCTRL': 0xE4, 'KPSLASH': 0x54, 'SYSRQ': 0x46, 'RIGHTALT': 0xE6,
    'LINEFEED': None,
    'HOME': 0x4A, 'UP': 0x52, 'PAGEUP': 0x4B, 'LEFT': 0x50, 'RIGHT': 0x4F, 'END': 0x4D,
    'DOWN': 0x51, 'PAGEDOWN': 0x4E, 'INSERT': 0x49, 'DELETE': 0x4C,
    'MUTE': 0x7F, 'VOLUMEDOWN': None, 'VOLUMEUP': None,              # 0x81, 0x80
    'POWER': 0x66, 'KPEQUAL': 0x67, 'PAUSE': 0x48,
    'KPCOMMA': None,                                                  # 0x85
    'LEFTMETA': 0xE3, 'RIGHTMETA': 0xE7, 'COMPOSE': 0x65,
    'STOP': 0x78, 'AGAIN': 0x79, 'UNDO': 0x7A, 'COPY': 0x7C, 'PASTE': 0x7D, 'FIND': 0x7E,
    'CUT': 0x7B, 'HELP': 0x75, 'MENU': None, 'SLEEP': None, 'WAKEUP': None,
    'NEXTSONG': None, 'PLAYPAUSE': None, 'PREVIOUSSONG': None,
    'F13': 0x68, 'F14': 0x69, 'F15': 0x6A, 'F16': 0x6B, 'F17': 0x6C, 'F18': 0x6D,
    'F19': 0x6E, 'F20': 0x6F, 'F21': 0x70, 'F22': 0x71, 'F23': 0x72, 'F24': 0x73,
    'PRINT': None,
}


class TableError(ValueError):
    pass


def build_table(names=None, usages=None):
    """256 bytes: HID usage for each Linux key code below 256, 0 for none."""
    names = host.LINUX_KEYS if names is None else names
    usages = HID_USAGES if usages is None else usages
    missing = sorted(set(names) - set(usages))
    if missing:
        raise TableError('no HID usage (or None) for ' + ', '.join(missing))
    extra = sorted(set(usages) - set(names))
    if extra:
        raise TableError('not a netkbd_host.py key: ' + ', '.join(extra))
    table = bytearray(256)
    owner = {}
    for name, usage in usages.items():
        if usage is None:
            continue
        if not (0x04 <= usage <= REPORT_MAX or usage in MODIFIERS):
            raise TableError(f'{name}: usage 0x{usage:02X} is outside the boot keyboard report')
        if usage in owner:
            raise TableError(f'{name} and {owner[usage]} both have usage 0x{usage:02X}')
        owner[usage] = name
        code = names[name]
        if not 0 < code < 256:
            raise TableError(f'{name}: Linux key {code} does not fit in the keymap')
        table[code] = usage
    return bytes(table)


def header(table):
    rows = []
    for i in range(0, 256, 16):
        rows.append('   ' + ' '.join(f'0x{b:02x},' for b in table[i:i + 16]) + f'  // {i}-{i + 15}')
    return (
        '// SPDX-License-Identifier: MIT\n'
        '// Generated by tools/hid_keytable.py in petskey-remote; edit that, not this.\n'
        '// Linux key code (what the PETSKEY keymap holds) -> USB HID keyboard-page usage, 0 for none.\n'
        '\n'
        'static const uint8_t UsbKbd_LinuxToHID[256] PROGMEM =\n'
        '{\n' + '\n'.join(rows) + '\n};\n'
    )


def main(argv):
    text = header(build_table())
    if len(argv) > 2:
        raise SystemExit('usage: hid_keytable.py [out.h]')
    if len(argv) == 2:
        with open(argv[1], 'w', newline='\n') as f:
            f.write(text)
    else:
        sys.stdout.write(text)


if __name__ == '__main__':
    main(sys.argv)
