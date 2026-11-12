#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""PETSKEY Remote host: a Commodore 64/128 keyboard, over the network, as a Linux or macOS keyboard.

PETSKEY REMOTE on the C64 dials this program (ATDT <host>:<port> through a SwiftLink /
TeensyROM Ethernet modem) and sends one byte per key press or release. Each one is
turned into a key event on a virtual keyboard created through /dev/uinput, so it
drives whatever the Pi's console or desktop session is doing, exactly like a USB
keyboard would. On macOS the events are posted through Quartz instead, into the
logged-in session. Pure Python 3 standard library: nothing to pip install.

Keymaps always name Linux key codes; the macOS backend translates them (Alt is Option,
Meta/Super is Command).

  sudo ./netkbd_host.py --allow 192.168.7.0/24          run it
  ./netkbd_host.py --dry-run                            print the key events instead
  ./netkbd_host.py --print-keymap > my.map              dump the built-in keymap
  ./netkbd_host.py --compile-keymap PETSKEY.K128 --machine c128 --keymap my.map

See README.md at the top of the repository for setup and the protocol.
"""
import argparse
import fcntl
import ipaddress
import os
import selectors
import socket
import struct
import sys
import time

PROTO_VERSION = 2
DEFAULT_PORT = 6526             # like the CIA chip, MOS 6526

# ---------------------------------------------------------------------------
# Linux key codes (linux/input-event-codes.h), the ones a keymap is likely to use.
LINUX_KEYS = {
    'ESC': 1, '1': 2, '2': 3, '3': 4, '4': 5, '5': 6, '6': 7, '7': 8, '8': 9, '9': 10, '0': 11,
    'MINUS': 12, 'EQUAL': 13, 'BACKSPACE': 14, 'TAB': 15,
    'Q': 16, 'W': 17, 'E': 18, 'R': 19, 'T': 20, 'Y': 21, 'U': 22, 'I': 23, 'O': 24, 'P': 25,
    'LEFTBRACE': 26, 'RIGHTBRACE': 27, 'ENTER': 28, 'LEFTCTRL': 29,
    'A': 30, 'S': 31, 'D': 32, 'F': 33, 'G': 34, 'H': 35, 'J': 36, 'K': 37, 'L': 38,
    'SEMICOLON': 39, 'APOSTROPHE': 40, 'GRAVE': 41, 'LEFTSHIFT': 42, 'BACKSLASH': 43,
    'Z': 44, 'X': 45, 'C': 46, 'V': 47, 'B': 48, 'N': 49, 'M': 50,
    'COMMA': 51, 'DOT': 52, 'SLASH': 53, 'RIGHTSHIFT': 54, 'KPASTERISK': 55, 'LEFTALT': 56,
    'SPACE': 57, 'CAPSLOCK': 58,
    'F1': 59, 'F2': 60, 'F3': 61, 'F4': 62, 'F5': 63, 'F6': 64, 'F7': 65, 'F8': 66, 'F9': 67, 'F10': 68,
    'NUMLOCK': 69, 'SCROLLLOCK': 70,
    'KP7': 71, 'KP8': 72, 'KP9': 73, 'KPMINUS': 74, 'KP4': 75, 'KP5': 76, 'KP6': 77, 'KPPLUS': 78,
    'KP1': 79, 'KP2': 80, 'KP3': 81, 'KP0': 82, 'KPDOT': 83,
    '102ND': 86, 'F11': 87, 'F12': 88, 'RO': 89,
    'KPENTER': 96, 'RIGHTCTRL': 97, 'KPSLASH': 98, 'SYSRQ': 99, 'RIGHTALT': 100, 'LINEFEED': 101,
    'HOME': 102, 'UP': 103, 'PAGEUP': 104, 'LEFT': 105, 'RIGHT': 106, 'END': 107, 'DOWN': 108,
    'PAGEDOWN': 109, 'INSERT': 110, 'DELETE': 111,
    'MUTE': 113, 'VOLUMEDOWN': 114, 'VOLUMEUP': 115, 'POWER': 116, 'KPEQUAL': 117, 'PAUSE': 119,
    'KPCOMMA': 121, 'LEFTMETA': 125, 'RIGHTMETA': 126, 'COMPOSE': 127,
    'STOP': 128, 'AGAIN': 129, 'UNDO': 131, 'COPY': 133, 'PASTE': 135, 'FIND': 136, 'CUT': 137,
    'HELP': 138, 'MENU': 139, 'SLEEP': 142, 'WAKEUP': 143,
    'NEXTSONG': 163, 'PLAYPAUSE': 164, 'PREVIOUSSONG': 165,
    'F13': 183, 'F14': 184, 'F15': 185, 'F16': 186, 'F17': 187, 'F18': 188,
    'F19': 189, 'F20': 190, 'F21': 191, 'F22': 192, 'F23': 193, 'F24': 194,
    'PRINT': 210,
}
LINUX_KEY_NAMES = {v: k for k, v in LINUX_KEYS.items()}
KEY_LEFTSHIFT = LINUX_KEYS['LEFTSHIFT']
KEY_RIGHTSHIFT = LINUX_KEYS['RIGHTSHIFT']

# ---------------------------------------------------------------------------
# C64/C128 key codes as the C64 program sends them: column * 8 + row for the C64 matrix,
# 64 + K line * 8 + row for the C128's extra keys, then RESTORE and CAPS LOCK.
C64_KEYS = [
    'INST_DEL', 'RETURN', 'CRSR_RIGHT', 'F7', 'F1', 'F3', 'F5', 'CRSR_DOWN',
    '3', 'W', 'A', '4', 'Z', 'S', 'E', 'LSHIFT',
    '5', 'R', 'D', '6', 'C', 'F', 'T', 'X',
    '7', 'Y', 'G', '8', 'B', 'H', 'U', 'V',
    '9', 'I', 'J', '0', 'M', 'K', 'O', 'N',
    'PLUS', 'P', 'L', 'MINUS', 'PERIOD', 'COLON', 'AT', 'COMMA',
    'POUND', 'ASTERISK', 'SEMICOLON', 'CLR_HOME', 'RSHIFT', 'EQUALS', 'UP_ARROW', 'SLASH',
    '1', 'LEFT_ARROW', 'CTRL', '2', 'SPACE', 'COMMODORE', 'Q', 'RUN_STOP',
    # C128 only
    'HELP', 'KP_8', 'KP_5', 'TAB', 'KP_2', 'KP_4', 'KP_7', 'KP_1',
    'ESC', 'KP_PLUS', 'KP_MINUS', 'LINE_FEED', 'KP_ENTER', 'KP_6', 'KP_9', 'KP_3',
    'ALT', 'KP_0', 'KP_PERIOD', 'UP', 'DOWN', 'LEFT', 'RIGHT', 'NO_SCROLL',
    'RESTORE', 'CAPS_LOCK',
]
C64_CODES = {name: code for code, name in enumerate(C64_KEYS)}
CODE_CAPS = C64_CODES['CAPS_LOCK']
TOGGLE_CODES = {CODE_CAPS}  # a locking key: every change of state is one tap

# ---------------------------------------------------------------------------
# Keymaps, written in the same text format a --keymap file uses:
#   <c64 key> = <target>          the key on its own, or with SHIFT if there is no SHIFT+ line
#   SHIFT+<c64 key> = <target>    the key while a C64 SHIFT (or SHIFT LOCK) is down
#   <target> is NONE, KEY_<name>, or SHIFT+KEY_<name> (the KEY_ is optional), optionally
#   followed by a "label": what the C64's on-screen keyboard shows on the key.
# A SHIFT+ line, or a target with SHIFT+, decides the Linux shift state on its own:
# SHIFT+7 = APOSTROPHE gives an unshifted apostrophe even though SHIFT is held.

KEYMAP_COMMON = """
# Keys that mean the same thing in every layout.
RETURN = ENTER
SPACE = SPACE
INST_DEL = BACKSPACE
SHIFT+INST_DEL = DELETE
CLR_HOME = HOME
SHIFT+CLR_HOME = END
CRSR_DOWN = DOWN
SHIFT+CRSR_DOWN = UP
CRSR_RIGHT = RIGHT
SHIFT+CRSR_RIGHT = LEFT
F1 = F1
SHIFT+F1 = F2
F3 = F3
SHIFT+F3 = F4
F5 = F5
SHIFT+F5 = F6
F7 = F7
SHIFT+F7 = F8
RUN_STOP = ESC
RESTORE = TAB
CTRL = LEFTCTRL
LSHIFT = LEFTSHIFT
RSHIFT = RIGHTSHIFT
A = A
B = B
C = C
D = D
E = E
F = F
G = G
H = H
I = I
J = J
K = K
L = L
M = M
N = N
O = O
P = P
Q = Q
R = R
S = S
T = T
U = U
V = V
W = W
X = X
Y = Y
Z = Z
1 = 1
2 = 2
3 = 3
4 = 4
5 = 5
6 = 6
7 = 7
8 = 8
9 = 9
0 = 0
COMMA = COMMA
PERIOD = DOT
SLASH = SLASH

# C128 extra keys
ESC = ESC
TAB = TAB
ALT = LEFTALT
HELP = F9
SHIFT+HELP = F11
LINE_FEED = F10
SHIFT+LINE_FEED = F12
NO_SCROLL = SCROLLLOCK
UP = UP
DOWN = DOWN
LEFT = LEFT
RIGHT = RIGHT
CAPS_LOCK = CAPSLOCK
KP_0 = KP0
KP_1 = KP1
KP_2 = KP2
KP_3 = KP3
KP_4 = KP4
KP_5 = KP5
KP_6 = KP6
KP_7 = KP7
KP_8 = KP8
KP_9 = KP9
KP_PLUS = KPPLUS
KP_MINUS = KPMINUS
KP_PERIOD = KPDOT
KP_ENTER = KPENTER
"""

# Machine-dependent: the C64 has no ALT key, so C= is ALT there; a C128 has one,
# so C= becomes the Super/Windows key.
KEYMAP_MACHINE = {
    'c64': "COMMODORE = LEFTALT\n",
    'c128': "COMMODORE = LEFTMETA\n",
}

# Layouts for the punctuation keys. Both assume the Pi uses a US keyboard layout.
KEYMAP_LAYOUTS = {
    # The characters printed on the C64 keycaps, plus the ASCII ones a C64 lacks
    # on otherwise unused shifted keys: ` ~ \ | { } _
    'symbolic': """
SHIFT+2 = SHIFT+APOSTROPHE
SHIFT+6 = SHIFT+7
SHIFT+7 = APOSTROPHE
SHIFT+8 = SHIFT+9
SHIFT+9 = SHIFT+0
SHIFT+0 = SHIFT+0
PLUS = SHIFT+EQUAL
MINUS = MINUS
SHIFT+MINUS = SHIFT+MINUS
POUND = BACKSLASH
SHIFT+POUND = SHIFT+BACKSLASH
AT = SHIFT+2
SHIFT+AT = SHIFT+LEFTBRACE
ASTERISK = SHIFT+8
SHIFT+ASTERISK = SHIFT+RIGHTBRACE
UP_ARROW = SHIFT+6
COLON = SHIFT+SEMICOLON
SHIFT+COLON = LEFTBRACE
SEMICOLON = SEMICOLON
SHIFT+SEMICOLON = RIGHTBRACE
EQUALS = EQUAL
SHIFT+EQUALS = EQUAL
LEFT_ARROW = GRAVE
SHIFT+LEFT_ARROW = SHIFT+GRAVE
""",
    # Where the keys sit on a PC keyboard, for use with your own XKB layout. The two
    # keys a PC has no place for go to the ISO 102nd and the JIS RO key codes.
    'positional': """
LEFT_ARROW = GRAVE
PLUS = MINUS
MINUS = EQUAL
POUND = BACKSLASH
AT = LEFTBRACE
ASTERISK = RIGHTBRACE
UP_ARROW = 102ND
COLON = SEMICOLON
SEMICOLON = APOSTROPHE
EQUALS = RO
""",
}


class KeymapError(ValueError):
    pass


def parse_target(text, where):
    text = text.strip().upper()
    if text == 'NONE':
        return (None, None)
    shift = None
    if text.startswith('SHIFT+'):
        shift, text = True, text[6:].strip()
    name = text[4:] if text.startswith('KEY_') else text
    if name not in LINUX_KEYS:
        if name.isdigit() and 0 < int(name) < 768:
            return (shift, int(name))
        raise KeymapError(f'{where}: unknown Linux key {text!r}')
    return (shift, LINUX_KEYS[name])


def new_keymap():
    return {'plain': {}, 'shift': {}, 'labels': {}}


def parse_keymap(text, keymap=None, source='keymap'):
    """Adds the lines of text to keymap = {'plain': {code: (shift, key)}, 'shift': {...},
    'labels': {(table, code): label}}."""
    keymap = keymap or new_keymap()
    for lineno, line in enumerate(text.splitlines(), 1):
        where = f'{source}:{lineno}'
        label = None
        if '"' in line:                 # a "label" (which may contain a #)
            head, _, rest = line.partition('"')
            label, quote, tail = rest.partition('"')
            if not quote or tail.split('#', 1)[0].strip():
                raise KeymapError(f'{where}: a label goes at the end of the line, in "double quotes"')
            if not all(0x20 <= ord(c) < 0x7f for c in label):
                raise KeymapError(f'{where}: labels are plain ASCII')
            line = head
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        if '=' not in line:
            raise KeymapError(f'{where}: expected "<c64 key> = <target>"')
        left, right = (part.strip() for part in line.split('=', 1))
        left = left.upper()
        table = 'plain'
        if left.startswith('SHIFT+') and left != 'SHIFT+':
            table, left = 'shift', left[6:].strip()
        if left not in C64_CODES:
            raise KeymapError(f'{where}: unknown C64 key {left!r} (see --list-keys)')
        shift, key = parse_target(right, where)
        if table == 'shift' and key is not None:
            shift = bool(shift)  # a SHIFT+ line always sets the shift state itself
        keymap[table][C64_CODES[left]] = (shift, key)
        if label is not None:
            keymap['labels'][(table, C64_CODES[left])] = label
        else:
            keymap['labels'].pop((table, C64_CODES[left]), None)
    return keymap


def build_keymap(machine, layout, extra_files=()):
    keymap = parse_keymap(KEYMAP_COMMON, source='built-in')
    parse_keymap(KEYMAP_LAYOUTS[layout], keymap, source=f'built-in {layout}')
    parse_keymap(KEYMAP_MACHINE[machine], keymap, source=f'built-in {machine}')
    for path in extra_files:
        with open(path, encoding='utf-8') as f:
            parse_keymap(f.read(), keymap, source=path)
    return keymap


def format_keymap(keymap):
    def target(binding):
        shift, key = binding
        if key is None:
            return 'NONE'
        name = 'KEY_' + LINUX_KEY_NAMES.get(key, str(key))
        return ('SHIFT+' + name) if shift else name
    def label(table, code):
        text = keymap.get('labels', {}).get((table, code))
        return '' if text is None else f' "{text}"'
    lines = []
    for code, name in enumerate(C64_KEYS):
        if code in keymap['plain']:
            lines.append(f'{name} = {target(keymap["plain"][code])}{label("plain", code)}')
        if code in keymap['shift']:
            lines.append(f'SHIFT+{name} = {target(keymap["shift"][code])}{label("shift", code)}')
    return '\n'.join(lines) + '\n'


# The keymap as the C64 sends it (and as the start of a PETSKEY.K64/.K128 file):
# for each of the NUM_CODES key codes, the plain then the SHIFT+ binding, two bytes each:
# flags (bit 7 defined, bit 0 sets the shift state, bit 1 that state) and the Linux key
# (0 = NONE).
NUM_CODES = len(C64_KEYS)
MAP_BYTES = NUM_CODES * 4


def encode_map(keymap):
    out = bytearray()
    for code in range(NUM_CODES):
        for table in ('plain', 'shift'):
            binding = keymap[table].get(code)
            if binding is None:
                out += b'\0\0'
                continue
            shift, key = binding
            if key is not None and not 0 < key < 256:
                raise KeymapError(f'{C64_KEYS[code]}: Linux key {key} does not fit in the C64 keymap')
            flags = 0x80 | (0 if shift is None else 1) | (2 if shift else 0)
            out += bytes([flags, key or 0])
    return bytes(out)


def decode_map(data):
    keymap = new_keymap()
    for code in range(min(NUM_CODES, len(data) // 4)):
        for i, table in enumerate(('plain', 'shift')):
            flags, key = data[code * 4 + i * 2], data[code * 4 + i * 2 + 1]
            if flags & 0x80:
                keymap[table][code] = (bool(flags & 2) if flags & 1 else None, key or None)
    return keymap


# ---------------------------------------------------------------------------
class Translator:
    """Turns C64 key down/up codes into Linux key events, sent through emit(key, value)."""

    def __init__(self, keymap, emit):
        self.keymap = keymap
        self.emit = emit
        # C64 keys that act as SHIFT: they set the "SHIFT held" state the SHIFT+ lines
        # look at, and are sent as the one virtual Linux shift key.
        self.shift_codes = {code for code, (shift, key) in keymap['plain'].items()
                            if key in (KEY_LEFTSHIFT, KEY_RIGHTSHIFT) and not shift}
        self.down = {}            # c64 code -> (shift, key), or None for an unmapped key
        self.counts = {}          # linux key -> how many C64 keys hold it down
        self.overrides = []       # c64 codes, in press order, whose binding sets the shift state
        self.sent_shift = False

    def physical_shift(self):
        return any(code in self.down for code in self.shift_codes)

    def _sync_shift(self):
        want = self.physical_shift()
        if self.overrides:
            want = self.down[self.overrides[-1]][0]
        if want != self.sent_shift:
            self.sent_shift = want
            self.emit(KEY_LEFTSHIFT, 1 if want else 0)

    def _press(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        if self.counts[key] == 1:
            self.emit(key, 1)

    def _release(self, key):
        n = self.counts.get(key, 0) - 1
        if n <= 0:
            self.counts.pop(key, None)
            if n == 0:
                self.emit(key, 0)
        else:
            self.counts[key] = n

    def key_down(self, code):
        if code in TOGGLE_CODES:
            return self._tap(code)
        if code in self.down:
            return
        if code in self.shift_codes:
            self.down[code] = None
            self._sync_shift()
            return
        binding = None
        if self.physical_shift():
            binding = self.keymap['shift'].get(code)
        if binding is None:
            binding = self.keymap['plain'].get(code)
        if binding is None or binding[1] is None:
            self.down[code] = None
            return
        self.down[code] = binding
        if binding[0] is not None:
            self.overrides.append(code)
        self._sync_shift()
        self._press(binding[1])

    def key_up(self, code):
        if code in TOGGLE_CODES:
            return self._tap(code)
        if code not in self.down:
            return  # held before the C64 started scanning, or after a release-all
        binding = self.down.pop(code)
        if binding is not None:
            self._release(binding[1])
            if code in self.overrides:
                self.overrides.remove(code)
        self._sync_shift()

    def _tap(self, code):
        binding = self.keymap['plain'].get(code)
        if binding and binding[1] is not None:
            self._press(binding[1])
            self._release(binding[1])

    def release_all(self):
        for key in list(self.counts):
            self.counts[key] = 1
            self._release(key)
        self.down.clear()
        self.overrides.clear()
        if self.sent_shift:
            self.sent_shift = False
            self.emit(KEY_LEFTSHIFT, 0)


# ---------------------------------------------------------------------------
class Session:
    """Parses the byte stream from one C64 connection."""

    def __init__(self, make_translator, on_hello=None, log=print):
        self.make_translator = make_translator      # (machine, keymap from the C64 or None)
        self.on_hello = on_hello
        self.log = log
        self.translator = None
        self.state = 'wait_hello'
        self.hello = []
        self.machine = None
        self.map = bytearray()
        self.map_len = 0

    def feed(self, data):
        for b in data:
            self._byte(b)

    def _byte(self, b):
        if self.state in ('map_len', 'map'):
            if self.state == 'map_len':
                self.hello.append(b)
                if len(self.hello) == 2:
                    self.map_len = self.hello[0] | self.hello[1] << 8
                    self.state, self.map = 'map', bytearray()
                    if self.map_len == 0:
                        self._map_done()
                return
            self.map.append(b)
            if len(self.map) >= self.map_len:
                self._map_done()
            return
        if self.state == 'hello':
            self.hello.append(b)
            if len(self.hello) == 2:
                version, flags = self.hello
                self.machine = 'c128' if flags & 1 else 'c64'
                self.translator = self.make_translator(self.machine, None)
                self.state = 'live'
                self.log(f'hello from a {self.machine.upper()} '
                         f'({"PAL" if flags & 2 else "NTSC"}), protocol {version}')
                if version != PROTO_VERSION:
                    self.log(f'warning: this host speaks protocol {PROTO_VERSION}')
                if self.on_hello:
                    self.on_hello(self.machine)
            return
        if b == 0xF0:                       # hello (also a restart while live)
            self.release_all()
            self.state, self.hello = 'hello', []
            return
        if self.state != 'live':
            return                          # modem chatter, "+++", anything before hello
        if b == 0xF1:                       # the C64's keymap follows: length, then the map
            self.release_all()
            self.state, self.hello = 'map_len', []
        elif b == 0xFC:                       # bye
            self.log('C64 signed off')
            self.release_all()
            self.state = 'wait_hello'
        elif b == 0xFE or b == 0xFF:        # heartbeat / reserved
            pass
        elif b < 0x60:
            self.translator.key_down(b)
        elif 0x80 <= b < 0xE0:
            self.translator.key_up(b & 0x7F)

    def _map_done(self):
        self.state = 'live'
        self.translator = self.make_translator(self.machine, decode_map(bytes(self.map)))
        self.log(f'keymap received from the C64 ({len(self.map)} bytes)')

    def release_all(self):
        if self.translator:
            self.translator.release_all()


# ---------------------------------------------------------------------------
# uinput
EV_SYN, EV_KEY, EV_REP = 0x00, 0x01, 0x14
SYN_REPORT = 0
BUS_USB = 0x03
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502
UI_DEV_SETUP = 0x405C5503      # _IOW('U', 3, struct uinput_setup), 92 bytes
UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
INPUT_EVENT = struct.Struct('llHHi')   # struct timeval is two native longs


class Keyboard:
    """What serve() needs from a keyboard backend. key() takes a Linux key code."""

    def key(self, code, value):
        raise NotImplementedError

    def next_timeout(self):
        """Seconds until tick() has work to do (key repeat), or None."""
        return None

    def tick(self):
        pass

    def close(self):
        pass


class UinputKeyboard(Keyboard):
    def __init__(self, name):
        self.fd = os.open('/dev/uinput', os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_REP)   # kernel autorepeat (text console)
        for key in range(1, 256):                       # everything a keymap might use
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, key)
        setup = struct.pack('HHHH80sI', BUS_USB, 0x1209, 0xC064, 1, name.encode()[:79], 0)
        fcntl.ioctl(self.fd, UI_DEV_SETUP, setup)
        fcntl.ioctl(self.fd, UI_DEV_CREATE)

    def key(self, code, value):
        os.write(self.fd, INPUT_EVENT.pack(0, 0, EV_KEY, code, value)
                 + INPUT_EVENT.pack(0, 0, EV_SYN, SYN_REPORT, 0))

    def close(self):
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        finally:
            os.close(self.fd)


class PrintKeyboard(Keyboard):
    def key(self, code, value):
        print(f'  KEY_{LINUX_KEY_NAMES.get(code, code)} {"down" if value else "up"}', flush=True)


# ---------------------------------------------------------------------------
# macOS: Quartz keyboard events, posted at the HID level as if from a real keyboard.
# Linux key code -> macOS virtual key code (kVK_* in HIToolbox/Events.h). Keys a Mac has
# no place for are left out; PC keyboards on a Mac put PrintScreen/ScrollLock/Pause on
# F13/F14/F15, Insert on Help and NumLock on the keypad's Clear.
MAC_KEYS = {
    'A': 0x00, 'S': 0x01, 'D': 0x02, 'F': 0x03, 'H': 0x04, 'G': 0x05, 'Z': 0x06, 'X': 0x07,
    'C': 0x08, 'V': 0x09, '102ND': 0x0A, 'B': 0x0B, 'Q': 0x0C, 'W': 0x0D, 'E': 0x0E, 'R': 0x0F,
    'Y': 0x10, 'T': 0x11, '1': 0x12, '2': 0x13, '3': 0x14, '4': 0x15, '6': 0x16, '5': 0x17,
    'EQUAL': 0x18, '9': 0x19, '7': 0x1A, 'MINUS': 0x1B, '8': 0x1C, '0': 0x1D,
    'RIGHTBRACE': 0x1E, 'O': 0x1F, 'U': 0x20, 'LEFTBRACE': 0x21, 'I': 0x22, 'P': 0x23,
    'ENTER': 0x24, 'L': 0x25, 'J': 0x26, 'APOSTROPHE': 0x27, 'K': 0x28, 'SEMICOLON': 0x29,
    'BACKSLASH': 0x2A, 'COMMA': 0x2B, 'SLASH': 0x2C, 'N': 0x2D, 'M': 0x2E, 'DOT': 0x2F,
    'TAB': 0x30, 'SPACE': 0x31, 'GRAVE': 0x32, 'BACKSPACE': 0x33, 'ESC': 0x35,
    'RIGHTMETA': 0x36, 'LEFTMETA': 0x37, 'LEFTSHIFT': 0x38, 'CAPSLOCK': 0x39,
    'LEFTALT': 0x3A, 'LEFTCTRL': 0x3B, 'RIGHTSHIFT': 0x3C, 'RIGHTALT': 0x3D, 'RIGHTCTRL': 0x3E,
    'F17': 0x40, 'KPDOT': 0x41, 'KPASTERISK': 0x43, 'KPPLUS': 0x45, 'NUMLOCK': 0x47,
    'KPSLASH': 0x4B, 'KPENTER': 0x4C, 'KPMINUS': 0x4E, 'F18': 0x4F, 'F19': 0x50,
    'KPEQUAL': 0x51, 'KP0': 0x52, 'KP1': 0x53, 'KP2': 0x54, 'KP3': 0x55, 'KP4': 0x56,
    'KP5': 0x57, 'KP6': 0x58, 'KP7': 0x59, 'F20': 0x5A, 'KP8': 0x5B, 'KP9': 0x5C,
    'RO': 0x5E, 'KPCOMMA': 0x5F,
    'F5': 0x60, 'F6': 0x61, 'F7': 0x62, 'F3': 0x63, 'F8': 0x64, 'F9': 0x65, 'F11': 0x67,
    'F13': 0x69, 'SYSRQ': 0x69, 'PRINT': 0x69, 'F16': 0x6A, 'F14': 0x6B, 'SCROLLLOCK': 0x6B,
    'F10': 0x6D, 'F12': 0x6F, 'F15': 0x71, 'PAUSE': 0x71, 'HELP': 0x72, 'INSERT': 0x72,
    'HOME': 0x73, 'PAGEUP': 0x74, 'DELETE': 0x75, 'F4': 0x76, 'END': 0x77, 'F2': 0x78,
    'PAGEDOWN': 0x79, 'F1': 0x7A, 'LEFT': 0x7B, 'RIGHT': 0x7C, 'DOWN': 0x7D, 'UP': 0x7E,
}
MAC_KEYCODES = {LINUX_KEYS[name]: vk for name, vk in MAC_KEYS.items()}

# CGEventFlags: the generic mask plus the left/right device bit a real keyboard sets
FLAG_CAPS, FLAG_SHIFT, FLAG_CTRL, FLAG_OPTION, FLAG_CMD = 0x10000, 0x20000, 0x40000, 0x80000, 0x100000
FLAG_NUMPAD, FLAG_FN = 0x200000, 0x800000
MAC_MODIFIERS = {               # Linux Alt is Option, Meta (Super) is Command
    LINUX_KEYS['LEFTSHIFT']: FLAG_SHIFT | 0x02, LINUX_KEYS['RIGHTSHIFT']: FLAG_SHIFT | 0x04,
    LINUX_KEYS['LEFTCTRL']: FLAG_CTRL | 0x01, LINUX_KEYS['RIGHTCTRL']: FLAG_CTRL | 0x2000,
    LINUX_KEYS['LEFTALT']: FLAG_OPTION | 0x20, LINUX_KEYS['RIGHTALT']: FLAG_OPTION | 0x40,
    LINUX_KEYS['LEFTMETA']: FLAG_CMD | 0x08, LINUX_KEYS['RIGHTMETA']: FLAG_CMD | 0x10,
}
KEY_CAPSLOCK = LINUX_KEYS['CAPSLOCK']
# Flags a real Apple keyboard puts on these keys' own events
MAC_KEY_FLAGS = {}
for _name in ('UP', 'DOWN', 'LEFT', 'RIGHT'):
    MAC_KEY_FLAGS[LINUX_KEYS[_name]] = FLAG_NUMPAD | FLAG_FN
for _name in ('HOME', 'END', 'PAGEUP', 'PAGEDOWN', 'DELETE', 'HELP', 'INSERT', 'SYSRQ', 'PRINT',
              'SCROLLLOCK', 'PAUSE', *(f'F{n}' for n in range(1, 21))):
    MAC_KEY_FLAGS[LINUX_KEYS[_name]] = FLAG_FN
for _name in ('KP0', 'KP1', 'KP2', 'KP3', 'KP4', 'KP5', 'KP6', 'KP7', 'KP8', 'KP9', 'KPDOT',
              'KPASTERISK', 'KPPLUS', 'KPMINUS', 'KPSLASH', 'KPENTER', 'KPEQUAL', 'NUMLOCK'):
    MAC_KEY_FLAGS[LINUX_KEYS[_name]] = FLAG_NUMPAD

EVENT_KEY_DOWN, EVENT_KEY_UP, EVENT_FLAGS_CHANGED = 10, 11, 12


class QuartzPoster:
    """Posts keyboard events through CoreGraphics (ctypes, no PyObjC needed)."""

    def __init__(self):
        import ctypes
        import ctypes.util
        from ctypes import c_bool, c_int32, c_int64, c_uint16, c_uint32, c_uint64, c_void_p
        path = ctypes.util.find_library('ApplicationServices')
        if not path:
            raise OSError('the ApplicationServices framework was not found')
        q = ctypes.CDLL(path)
        cf = ctypes.CDLL(ctypes.util.find_library('CoreFoundation'))
        q.AXIsProcessTrusted.restype = c_bool
        if not q.AXIsProcessTrusted():
            raise PermissionError('this program is not allowed to control the computer '
                                  '(System Settings > Privacy & Security > Accessibility)')
        q.CGEventSourceCreate.restype = c_void_p
        q.CGEventSourceCreate.argtypes = [c_int32]
        q.CGEventCreateKeyboardEvent.restype = c_void_p
        q.CGEventCreateKeyboardEvent.argtypes = [c_void_p, c_uint16, c_bool]
        q.CGEventSetType.argtypes = [c_void_p, c_uint32]
        q.CGEventSetFlags.argtypes = [c_void_p, c_uint64]
        q.CGEventSetIntegerValueField.argtypes = [c_void_p, c_uint32, c_int64]
        q.CGEventPost.argtypes = [c_uint32, c_void_p]
        cf.CFRelease.argtypes = [c_void_p]
        self.q, self.cf = q, cf
        self.source = q.CGEventSourceCreate(1)      # kCGEventSourceStateHIDSystemState

    def __call__(self, vk, kind, flags, autorepeat=False):
        q = self.q
        event = q.CGEventCreateKeyboardEvent(self.source, vk, kind != EVENT_KEY_UP)
        if not event:
            return
        try:
            if kind == EVENT_FLAGS_CHANGED:
                q.CGEventSetType(event, EVENT_FLAGS_CHANGED)
            q.CGEventSetFlags(event, flags)
            if autorepeat:
                q.CGEventSetIntegerValueField(event, 8, 1)   # kCGKeyboardEventAutorepeat
            q.CGEventPost(0, event)                          # kCGHIDEventTap
        finally:
            self.cf.CFRelease(event)


class MacKeyboard(Keyboard):
    """Linux key events to macOS ones. Keeps the modifier flags itself (a posted event
    carries its own flags) and repeats a held key itself (macOS only repeats keys of a
    real keyboard). CAPS LOCK toggles a caps flag on the events it posts; it does not
    change the Mac's own Caps Lock state or light."""

    def __init__(self, post=None, repeat_delay=0.5, repeat_rate=25.0, clock=time.monotonic,
                 log=print):
        self.post = post or QuartzPoster()
        self.repeat_delay = repeat_delay
        self.repeat_interval = 1.0 / repeat_rate if repeat_rate > 0 else None
        self.clock = clock
        self.log = log
        self.held = set()           # Linux codes of the modifiers that are down
        self.caps = False
        self.repeat = None          # (linux code, time of the next repeat)
        self.warned = set()

    def flags(self, code=None):
        f = FLAG_CAPS if self.caps else 0
        for mod in self.held:
            f |= MAC_MODIFIERS[mod]
        return f | MAC_KEY_FLAGS.get(code, 0)

    def key(self, code, value):
        vk = MAC_KEYCODES.get(code)
        if vk is None:
            if code not in self.warned:
                self.warned.add(code)
                self.log(f'KEY_{LINUX_KEY_NAMES.get(code, code)} has no macOS key; ignored')
            return
        if code in MAC_MODIFIERS:
            (self.held.add if value else self.held.discard)(code)
            self.post(vk, EVENT_FLAGS_CHANGED, self.flags())
        elif code == KEY_CAPSLOCK:
            if value:
                self.caps = not self.caps
                self.post(vk, EVENT_FLAGS_CHANGED, self.flags())
        elif value:
            self.post(vk, EVENT_KEY_DOWN, self.flags(code))
            if self.repeat_interval:
                self.repeat = (code, self.clock() + self.repeat_delay)
        else:
            self.post(vk, EVENT_KEY_UP, self.flags(code))
            if self.repeat and self.repeat[0] == code:
                self.repeat = None

    def next_timeout(self):
        if not self.repeat:
            return None
        return max(0.0, self.repeat[1] - self.clock())

    def tick(self):
        if not self.repeat:
            return
        code, due = self.repeat
        now = self.clock()
        if now < due:
            return
        self.post(MAC_KEYCODES[code], EVENT_KEY_DOWN, self.flags(code), autorepeat=True)
        # one repeat per tick: if the loop fell behind, carry on from now, no burst
        due += self.repeat_interval
        self.repeat = (code, due if due > now else now + self.repeat_interval)


# ---------------------------------------------------------------------------
def to_petscii(text):
    """ASCII to PETSCII for the C64's lower-case character set."""
    out = bytearray()
    for ch in text:
        c = ord(ch)
        if 0x61 <= c <= 0x7A:
            out.append(c - 0x20)
        elif 0x41 <= c <= 0x5A:
            out.append(c + 0x80)
        elif ch == '\n':
            out.append(13)
        elif 0x20 <= c < 0x60:
            out.append(c)
    return bytes(out)


GREETING_MAX = 76           # what fits on the C64's status line


def greeting_text(template, machine='c64'):
    """The line shown on the C64 after it connects; '' for none."""
    try:
        text = template.format(hostname=socket.gethostname(), machine=machine.upper())
    except (KeyError, IndexError, ValueError) as e:
        raise SystemExit(f'--greeting {template!r}: {e} (placeholders are {{hostname}} and {{machine}})')
    return ' '.join(text.split())[:GREETING_MAX]


def parse_allow(values):
    nets = []
    for value in values:
        try:
            nets.append(ipaddress.ip_network(value, strict=False))
        except ValueError as e:
            raise SystemExit(f'--allow {value}: {e}')
    return nets


def serve(args, keyboard):
    allow = parse_allow(args.allow)
    if not allow and args.bind in ('0.0.0.0', '::'):
        print('warning: listening on every interface with no --allow: anyone who can reach '
              f'port {args.port} can type into this machine', file=sys.stderr)

    def make_translator(machine, c64_keymap):
        if c64_keymap is not None and not args.host_keymap:
            return Translator(c64_keymap, keyboard.key)
        machine = args.machine or machine
        return Translator(build_keymap(machine, args.layout, args.keymap), keyboard.key)

    family = socket.AF_INET6 if ':' in args.bind else socket.AF_INET
    listener = socket.socket(family, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((args.bind, args.port))
    listener.listen(2)
    listener.setblocking(False)
    sel = selectors.DefaultSelector()
    sel.register(listener, selectors.EVENT_READ)
    print(f'listening on {args.bind}:{args.port} ({args.layout} layout)', flush=True)

    client = None
    session = None
    last_rx = 0.0
    keys_released_for_silence = False

    def drop(reason):
        nonlocal client, session
        if client:
            print(f'disconnected: {reason}', flush=True)
            session.release_all()
            sel.unregister(client)
            client.close()
        client, session = None, None

    def greet(sock):
        def on_hello(machine):
            text = greeting_text(args.greeting, machine)
            if not text:
                return
            try:
                sock.sendall(to_petscii(text + '\n'))
            except OSError:
                pass
        return on_hello

    try:
        while True:
            timeout = 0.5
            due = keyboard.next_timeout()
            if due is not None:
                timeout = min(timeout, due)
            for key, _ in sel.select(timeout=timeout):
                if key.fileobj is listener:
                    sock, addr = listener.accept()
                    ip = ipaddress.ip_address(addr[0])
                    if getattr(ip, 'ipv4_mapped', None):
                        ip = ip.ipv4_mapped
                    if allow and not any(ip in net for net in allow):
                        print(f'refused {addr[0]}: not in --allow', flush=True)
                        sock.close()
                        continue
                    drop('replaced by a new connection')
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                    sock.setblocking(False)
                    client = sock
                    session = Session(make_translator, on_hello=greet(sock),
                                      log=lambda m: print(m, flush=True))
                    sel.register(sock, selectors.EVENT_READ)
                    last_rx = time.monotonic()
                    print(f'connection from {addr[0]}', flush=True)
                else:
                    try:
                        data = client.recv(4096)
                    except (BlockingIOError, InterruptedError):
                        continue
                    except OSError as e:
                        drop(str(e))
                        continue
                    if not data:
                        drop('closed by the C64')
                        continue
                    last_rx = time.monotonic()
                    keys_released_for_silence = False
                    if args.verbose:
                        print('rx', data.hex(' '), flush=True)
                    session.feed(data)
            keyboard.tick()
            if client:
                silent = time.monotonic() - last_rx
                # the C64 sends a heartbeat twice a second; silence means it was reset
                # or the link is gone, so let go of any held keys before they repeat forever
                if silent > 3 and not keys_released_for_silence:
                    session.release_all()
                    keys_released_for_silence = True
                if silent > args.timeout:
                    drop(f'nothing received for {args.timeout:.0f} s')
    except KeyboardInterrupt:
        pass
    finally:
        drop('host exiting')
        keyboard.close()


def main(argv=None):
    p = argparse.ArgumentParser(description='Use a C64/C128 running PETSKEY REMOTE as a Linux or macOS keyboard.')
    p.add_argument('--port', type=int, default=DEFAULT_PORT, help=f'TCP port (default {DEFAULT_PORT})')
    p.add_argument('--bind', default='0.0.0.0', help='address to listen on (default all)')
    p.add_argument('--allow', action='append', default=[], metavar='CIDR',
                   help='only accept connections from this network, e.g. 192.168.7.0/24 (repeatable)')
    p.add_argument('--layout', choices=sorted(KEYMAP_LAYOUTS), default='symbolic',
                   help='symbolic: keys type what their keycaps say (default); positional: by position')
    p.add_argument('--machine', choices=sorted(KEYMAP_MACHINE),
                   help='force the C64 or C128 keymap instead of taking it from the C64')
    p.add_argument('--keymap', action='append', default=[], metavar='FILE',
                   help='keymap file applied on top of the built-in one (repeatable)')
    p.add_argument('--name', default='Commodore PETSKEY Remote', help='Linux: input device name')
    p.add_argument('--greeting', default='Linked to {hostname}.', metavar='TEXT',
                   help='line shown on the C64 once connected; {hostname} and {machine} are '
                        'filled in, "" shows nothing (default: "Linked to {hostname}.")')
    p.add_argument('--timeout', type=float, default=15, help='drop a silent connection after this many seconds')
    p.add_argument('--repeat-delay', type=float, default=0.5, metavar='SECONDS',
                   help='macOS: how long a key is held before it repeats (default 0.5)')
    p.add_argument('--repeat-rate', type=float, default=25, metavar='PER_SECOND',
                   help='macOS: repeats per second while a key is held, 0 for none (default 25)')
    p.add_argument('--dry-run', action='store_true',
                   help='print key events instead of typing them')
    p.add_argument('--verbose', action='store_true', help='print the raw bytes received')
    p.add_argument('--print-keymap', action='store_true', help='print the effective keymap and exit')
    p.add_argument('--list-keys', action='store_true', help='list the C64 and Linux key names and exit')
    p.add_argument('--host-keymap', action='store_true',
                   help="use this program's keymap (--layout, --machine, --keymap) even when the "
                        "C64 sends its own")
    p.add_argument('--compile-keymap', metavar='FILE',
                   help='write a PETSKEY.K64/.K128 keymap file for the C64 disk and exit '
                        '(uses --machine, default c64, --layout and --keymap)')
    args = p.parse_args(argv)

    try:
        if args.compile_keymap:
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools'))
            import petskey_build
            machine = args.machine or 'c64'
            data = petskey_build.compile_keys(build_keymap(machine, args.layout, args.keymap), machine)
            with open(args.compile_keymap, 'wb') as f:
                f.write(data)
            print(f'{args.compile_keymap}: {machine.upper()} keymap, {len(data)} bytes')
            return
        if args.print_keymap:
            sys.stdout.write(format_keymap(build_keymap(args.machine or 'c128', args.layout, args.keymap)))
            return
        if args.list_keys:
            print('C64/C128 keys:', ' '.join(C64_KEYS))
            print('Linux keys (KEY_ prefix optional):', ' '.join(LINUX_KEYS))
            return
        build_keymap('c64', args.layout, args.keymap)   # report keymap errors before listening
        greeting_text(args.greeting)                     # and greeting errors
    except KeymapError as e:
        raise SystemExit(f'error: {e}')

    if args.dry_run:
        keyboard = PrintKeyboard()
    elif sys.platform == 'darwin':
        try:
            keyboard = MacKeyboard(repeat_delay=args.repeat_delay, repeat_rate=args.repeat_rate,
                                   log=lambda m: print(m, flush=True))
        except OSError as e:
            raise SystemExit(f'cannot post key events: {e}\n'
                             'Allow the app that runs this program (Terminal, or the python3 '
                             'a LaunchAgent runs) under System Settings > Privacy & Security > '
                             'Accessibility, then start it again. See README.md.')
    else:
        try:
            keyboard = UinputKeyboard(args.name)
        except OSError as e:
            raise SystemExit(f'cannot create the virtual keyboard via /dev/uinput: {e}\n'
                             'Run as root, or see README.md for the udev rule '
                             '(and "sudo modprobe uinput" if the device is missing).')
    serve(args, keyboard)


if __name__ == '__main__':
    main()
