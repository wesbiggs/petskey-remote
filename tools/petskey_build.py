#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build-time data for PETSKEY Remote's C64 program, and its disk image.

  petskey_build.py gen <outdir>          write layout.inc and font.inc (assembler includes)
                                         and PETSKEY.K64 / PETSKEY.K128 (default keymaps)
  petskey_build.py d64 <out.d64> <prg> <file>...   make the disk

The on-screen keyboards (one C64, one C128) are described here in 8x8 character cells,
and every key's legend is worked out from the keymap: the character the key types on a
US-layout Linux machine, or a short name such as Esc or F1.
"""
import os
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'pi'))
import netkbd_host as host  # noqa: E402

C = host.C64_CODES
K = host.LINUX_KEYS
NO_CODE = 0xFF
LEGEND_CHARS = 7          # longest legend stored per key (a 4-cell key holds 7)
KEYS_MAGIC = b'PKM\x01'

# Colour classes (the C64 program turns them into colours)
NORMAL, MODIFIER, FUNCTION, KEYPAD = 0, 1, 2, 3

# ---------------------------------------------------------------------------
# Layouts: (code or label, x, y, w, h, class). x, y, w, h in 8x8 cells; a key's
# legend has room for (w*8 - 3) // 4 characters of the 3x5 font.


def _row(names, x, y, w=2, cls=NORMAL):
    out = []
    for name in names:
        out.append((name, x, y, w, 2, cls))
        x += w
    return out


def main_block(x0, y0, restore=True):
    keys = []
    keys += _row(['LEFT_ARROW', '1', '2', '3', '4', '5', '6', '7', '8', '9', '0',
                  'PLUS', 'MINUS', 'POUND'], x0, y0)
    keys += [('CLR_HOME', x0 + 28, y0, 2, 2, FUNCTION), ('INST_DEL', x0 + 30, y0, 2, 2, FUNCTION)]
    keys += [('CTRL', x0, y0 + 2, 3, 2, MODIFIER)]
    keys += _row(['Q', 'W', 'E', 'R', 'T', 'Y', 'U', 'I', 'O', 'P', 'AT', 'ASTERISK', 'UP_ARROW'],
                 x0 + 3, y0 + 2)
    if restore:
        keys += [('RESTORE', x0 + 29, y0 + 2, 3, 2, FUNCTION)]
    keys += [('RUN_STOP', x0, y0 + 4, 2, 2, FUNCTION), (('LSHIFT', 'Lck'), x0 + 2, y0 + 4, 2, 2, MODIFIER)]
    keys += _row(['A', 'S', 'D', 'F', 'G', 'H', 'J', 'K', 'L', 'COLON', 'SEMICOLON', 'EQUALS'],
                 x0 + 4, y0 + 4)
    keys += [('RETURN', x0 + 28, y0 + 4, 4, 2, FUNCTION)]
    keys += [('COMMODORE', x0, y0 + 6, 2, 2, MODIFIER), ('LSHIFT', x0 + 2, y0 + 6, 3, 2, MODIFIER)]
    keys += _row(['Z', 'X', 'C', 'V', 'B', 'N', 'M', 'COMMA', 'PERIOD', 'SLASH'], x0 + 5, y0 + 6)
    keys += [('RSHIFT', x0 + 25, y0 + 6, 3, 2, MODIFIER),
             ('CRSR_DOWN', x0 + 28, y0 + 6, 2, 2, FUNCTION), ('CRSR_RIGHT', x0 + 30, y0 + 6, 2, 2, FUNCTION)]
    keys += [('SPACE', x0 + 7, y0 + 8, 18, 2, NORMAL)]
    return keys


LAYOUT_C64 = main_block(2, 6) + [
    ('F1', 36, 6, 2, 2, FUNCTION), ('F3', 36, 8, 2, 2, FUNCTION),
    ('F5', 36, 10, 2, 2, FUNCTION), ('F7', 36, 12, 2, 2, FUNCTION),
]

LAYOUT_C128 = (
    _row(['ESC', 'TAB', 'ALT', 'CAPS_LOCK'], 0, 4, cls=MODIFIER)
    + _row(['HELP', 'LINE_FEED', ('', '40'), 'NO_SCROLL'], 9, 4, cls=FUNCTION)
    + _row(['UP', 'DOWN', 'LEFT', 'RIGHT'], 18, 4, cls=FUNCTION)
    + _row(['F1', 'F3', 'F5', 'F7'], 32, 4, cls=FUNCTION)
    + main_block(0, 7)
    + _row(['KP_7', 'KP_8', 'KP_9', 'KP_PLUS'], 32, 7, cls=KEYPAD)
    + _row(['KP_4', 'KP_5', 'KP_6', 'KP_MINUS'], 32, 9, cls=KEYPAD)
    + _row(['KP_1', 'KP_2', 'KP_3'], 32, 11, cls=KEYPAD)
    + [('KP_ENTER', 38, 11, 2, 4, KEYPAD), ('KP_0', 32, 13, 4, 2, KEYPAD), ('KP_PERIOD', 36, 13, 2, 2, KEYPAD)]
)
LAYOUTS = {'c64': LAYOUT_C64, 'c128': LAYOUT_C128}


def key_entries(layout):
    """(code, label or None, x, y, w, h, class) for each key drawn."""
    out = []
    for name, x, y, w, h, cls in layout:
        label = None
        if isinstance(name, tuple):
            name, label = name
        code = C[name] if name else NO_CODE
        out.append((code, label, x, y, w, h, cls))
    return out


def check_layout(layout):
    cells = set()
    for code, label, x, y, w, h, cls in key_entries(layout):
        assert 0 <= x and x + w <= 40 and 2 <= y and y + h <= 23, (code, x, y, w, h)
        for cx in range(x, x + w):
            for cy in range(y, y + h):
                assert (cx, cy) not in cells, ('overlap', code, cx, cy)
                cells.add((cx, cy))


# ---------------------------------------------------------------------------
# Legends

US_CHARS = {}
for i, (plain, shifted) in enumerate(zip('1234567890', '!@#$%^&*()')):
    US_CHARS[K[plain]] = (plain, shifted)
for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
    US_CHARS[K[letter]] = (letter, letter)        # keycaps show capitals
for name, chars in {'MINUS': '-_', 'EQUAL': '=+', 'LEFTBRACE': '[{', 'RIGHTBRACE': ']}',
                    'SEMICOLON': ';:', 'APOSTROPHE': '\'"', 'GRAVE': '`~', 'BACKSLASH': '\\|',
                    'COMMA': ',<', 'DOT': '.>', 'SLASH': '/?', '102ND': '<>'}.items():
    US_CHARS[K[name]] = (chars[0], chars[1])
for name, ch in {'KP0': '0', 'KP1': '1', 'KP2': '2', 'KP3': '3', 'KP4': '4', 'KP5': '5', 'KP6': '6',
                 'KP7': '7', 'KP8': '8', 'KP9': '9', 'KPPLUS': '+', 'KPMINUS': '-', 'KPDOT': '.',
                 'KPASTERISK': '*', 'KPSLASH': '/'}.items():
    US_CHARS[K[name]] = (ch, ch)

# Font codes 1-4 are arrows
UP_A, DOWN_A, LEFT_A, RIGHT_A = '\x01', '\x02', '\x03', '\x04'

# Longest first: the first one that fits the key is used
NAMES = {
    'ESC': ['Esc'], 'TAB': ['Tab'], 'BACKSPACE': ['BkSp', 'BS'], 'DELETE': ['Del'],
    'ENTER': ['Enter', 'Ent'], 'KPENTER': ['Ent'], 'SPACE': [''],
    'LEFTCTRL': ['Ctrl', 'Ctl'], 'RIGHTCTRL': ['Ctrl', 'Ctl'],
    'LEFTSHIFT': ['Shift', 'Sh'], 'RIGHTSHIFT': ['Shift', 'Sh'],
    'LEFTALT': ['Alt'], 'RIGHTALT': ['AltGr', 'AGr'], 'LEFTMETA': ['Super', 'Sup'],
    'RIGHTMETA': ['Super', 'Sup'], 'COMPOSE': ['Menu', 'Mnu'], 'MENU': ['Menu', 'Mnu'],
    'CAPSLOCK': ['Caps', 'Cap'], 'NUMLOCK': ['NumLk', 'Num'], 'SCROLLLOCK': ['ScrLk', 'Scr'],
    'HOME': ['Home', 'Hom'], 'END': ['End'], 'PAGEUP': ['PgUp', 'PgU'], 'PAGEDOWN': ['PgDn', 'PgD'],
    'INSERT': ['Ins'], 'UP': [UP_A], 'DOWN': [DOWN_A], 'LEFT': [LEFT_A], 'RIGHT': [RIGHT_A],
    'PRINT': ['PrtSc', 'Prt'], 'SYSRQ': ['SysRq', 'SRq'], 'PAUSE': ['Pause', 'Pau'],
    'LINEFEED': ['LF'], 'HELP': ['Help', 'Hlp'], 'RO': ['Ro'],
    'VOLUMEUP': ['Vol+', 'V+'], 'VOLUMEDOWN': ['Vol-', 'V-'], 'MUTE': ['Mute', 'Mut'],
}


def legend(binding, shifted, width, label=None):
    """The legend for a binding (shift, key) while SHIFT is (not) held, at most width chars."""
    if label is not None:
        return label[:width]
    if binding is None or binding[1] is None:
        return ''
    shift, key = binding
    effective = shifted if shift is None else shift
    if key in US_CHARS:
        return US_CHARS[key][1 if effective else 0]
    name = host.LINUX_KEY_NAMES.get(key, str(key))
    for option in NAMES.get(name, []):
        if len(option) <= width:
            return option
    if name.startswith('F') and name[1:].isdigit():
        return name
    return name.capitalize()[:width]


def legends_for(keymap, machine):
    """{code: (legend without SHIFT, legend with SHIFT)} for the machine's on-screen keyboard."""
    width = {}
    for code, label, x, y, w, h, cls in key_entries(LAYOUTS[machine]):
        if code != NO_CODE and label is None:
            width[code] = max(width.get(code, 0), (w * 8 - 3) // 4)
    labels = keymap.get('labels', {})
    out = {}
    for code in range(host.NUM_CODES):
        wd = min(width.get(code, 3), LEGEND_CHARS)
        plain = keymap['plain'].get(code)
        shifted_binding = keymap['shift'].get(code) or plain
        if code in keymap['shift']:
            shifted_label = labels.get(('shift', code))
        else:
            shifted_label = labels.get(('plain', code))
        out[code] = (legend(plain, False, wd, labels.get(('plain', code))),
                     legend(shifted_binding, True, wd, shifted_label))
    return out


def compile_keys(keymap, machine):
    """PETSKEY.K64/.K128: 2-byte load address, magic, map (sent to the host), legends."""
    out = bytearray(b'\x00\x00') + KEYS_MAGIC + host.encode_map(keymap)
    legends = legends_for(keymap, machine)
    for code in range(host.NUM_CODES):
        for text in legends[code]:
            raw = text.encode('ascii')[:LEGEND_CHARS]
            out += raw + bytes(LEGEND_CHARS - len(raw))
    return bytes(out)


KEYS_FILE_SIZE = 2 + len(KEYS_MAGIC) + host.MAP_BYTES + host.NUM_CODES * 2 * LEGEND_CHARS

# ---------------------------------------------------------------------------
# 3x5 font, ASCII 0-127 (lower case shares the capitals; 1-4 are arrows)

FONT_ART = {
    ' ': '... ... ... ... ...', '!': '.X. .X. .X. ... .X.', '"': 'X.X X.X ... ... ...',
    '#': 'X.X XXX X.X XXX X.X', '$': '.XX XX. .X. .XX XX.', '%': 'X.. ..X .X. X.. ..X',
    '&': '.X. X.X .X. X.X .XX', "'": '.X. .X. ... ... ...', '(': '..X .X. .X. .X. ..X',
    ')': 'X.. .X. .X. .X. X..', '*': '... X.X .X. X.X ...', '+': '... .X. XXX .X. ...',
    ',': '... ... ... .X. X..', '-': '... ... XXX ... ...', '.': '... ... ... ... .X.',
    '/': '..X ..X .X. X.. X..', '0': 'XXX X.X X.X X.X XXX', '1': '.X. XX. .X. .X. XXX',
    '2': 'XX. ..X .X. X.. XXX', '3': 'XX. ..X .X. ..X XX.', '4': 'X.X X.X XXX ..X ..X',
    '5': 'XXX X.. XX. ..X XX.', '6': '.XX X.. XXX X.X XXX', '7': 'XXX ..X .X. .X. .X.',
    '8': 'XXX X.X XXX X.X XXX', '9': 'XXX X.X XXX ..X XX.', ':': '... .X. ... .X. ...',
    ';': '... .X. ... .X. X..', '<': '..X .X. X.. .X. ..X', '=': '... XXX ... XXX ...',
    '>': 'X.. .X. ..X .X. X..', '?': 'XX. ..X .X. ... .X.', '@': '.X. X.X XXX X.. .XX',
    'A': '.X. X.X XXX X.X X.X', 'B': 'XX. X.X XX. X.X XX.', 'C': '.XX X.. X.. X.. .XX',
    'D': 'XX. X.X X.X X.X XX.', 'E': 'XXX X.. XX. X.. XXX', 'F': 'XXX X.. XX. X.. X..',
    'G': '.XX X.. X.X X.X .XX', 'H': 'X.X X.X XXX X.X X.X', 'I': 'XXX .X. .X. .X. XXX',
    'J': '..X ..X ..X X.X .X.', 'K': 'X.X X.X XX. X.X X.X', 'L': 'X.. X.. X.. X.. XXX',
    'M': 'X.X XXX XXX X.X X.X', 'N': 'XX. X.X X.X X.X X.X', 'O': '.X. X.X X.X X.X .X.',
    'P': 'XX. X.X XX. X.. X..', 'Q': '.X. X.X X.X XX. .XX', 'R': 'XX. X.X XX. X.X X.X',
    'S': '.XX X.. .X. ..X XX.', 'T': 'XXX .X. .X. .X. .X.', 'U': 'X.X X.X X.X X.X XXX',
    'V': 'X.X X.X X.X X.X .X.', 'W': 'X.X X.X XXX XXX X.X', 'X': 'X.X X.X .X. X.X X.X',
    'Y': 'X.X X.X .X. .X. .X.', 'Z': 'XXX ..X .X. X.. XXX', '[': 'XX. X.. X.. X.. XX.',
    '\\': 'X.. X.. .X. ..X ..X', ']': '.XX ..X ..X ..X .XX', '^': '.X. X.X ... ... ...',
    '_': '... ... ... ... XXX', '`': 'X.. .X. ... ... ...', '{': '.XX .X. X.. .X. .XX',
    '|': '.X. .X. .X. .X. .X.', '}': 'XX. .X. ..X .X. XX.', '~': '... .XX XX. ... ...',
    UP_A: '.X. XXX .X. .X. .X.', DOWN_A: '.X. .X. .X. XXX .X.',
    LEFT_A: '... .X. XXX .X. ...', RIGHT_A: '... .X. XXX .X. ...',
}
FONT_ART[LEFT_A] = '.X. X.. XXX X.. .X.'
FONT_ART[RIGHT_A] = '.X. ..X XXX ..X .X.'


def font_bytes():
    out = bytearray()
    for code in range(128):
        ch = chr(code)
        if 'a' <= ch <= 'z':
            ch = ch.upper()
        art = FONT_ART.get(ch, FONT_ART[' '])
        for row in art.split():
            bits = sum(0x80 >> i for i, c in enumerate(row) if c == 'X')
            out.append(bits)
    return bytes(out)


# ---------------------------------------------------------------------------
def version():
    """PETSKEY_VERSION, else `git describe`, else 'dev'."""
    v = os.environ.get('PETSKEY_VERSION')
    if not v:
        try:
            v = subprocess.run(['git', 'describe', '--tags', '--always', '--dirty'], cwd=HERE,
                               capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            v = ''
    v = (v or 'dev')[:24]
    return ''.join(c for c in v if 0x20 < ord(c) < 0x7f and c != '"')


def write_if_changed(path, text):
    try:
        with open(path) as f:
            if f.read() == text:
                return
    except OSError:
        pass
    with open(path, 'w') as f:
        f.write(text)


def write_includes(outdir):
    os.makedirs(outdir, exist_ok=True)
    v = version()
    write_if_changed(os.path.join(outdir, 'version.inc'),
                     '; generated by tools/petskey_build.py\n'
                     f'version_pet:   !pet "{v.lower()}", 0\n'
                     f'version_ascii: !text "{v.upper()}", 0\n')
    consts = ['; generated by tools/petskey_build.py - do not edit', f'LEGEND_CHARS = {LEGEND_CHARS}',
              f'NUM_CODES = {host.NUM_CODES}', f'MAP_BYTES = {host.MAP_BYTES}',
              f'KEYS_DATA_SIZE = {KEYS_FILE_SIZE - 2}', f'PROTO_VER = {host.PROTO_VERSION}',
              f'DEFAULT_PORT = {host.DEFAULT_PORT}',
              f'CLS_NORMAL = {NORMAL}', f'CLS_MODIFIER = {MODIFIER}', f'CLS_FUNCTION = {FUNCTION}',
              f'CLS_KEYPAD = {KEYPAD}', f'NO_CODE = {NO_CODE}']
    lines = ['; generated by tools/petskey_build.py - do not edit', '']
    for machine, layout in LAYOUTS.items():
        check_layout(layout)
        entries = key_entries(layout)
        labels = [e[1] for e in entries if e[1] is not None]
        for field, idx in (('code', 0), ('x', 2), ('y', 3), ('w', 4), ('h', 5), ('cls', 6)):
            vals = [e[idx] for e in entries]
            lines.append(f'lay_{machine}_{field}: !byte ' + ', '.join(str(v) for v in vals))
        lbl = []
        n = 0
        for e in entries:
            if e[1] is None:
                lbl.append(255)
            else:
                lbl.append(n)
                n += 1
        lines.append(f'lay_{machine}_lbl: !byte ' + ', '.join(str(v) for v in lbl))
        lines.append(f'lay_{machine}_labels:')
        for text in labels:
            raw = text.encode('ascii')[:LEGEND_CHARS]
            lines.append('   !byte ' + ', '.join(str(b) for b in raw + bytes(LEGEND_CHARS - len(raw))))
        lines.append('')
    max_entries = max(len(key_entries(l)) for l in LAYOUTS.values())
    consts.append(f'LAYOUT_MAX = {max_entries}')
    for machine, layout in LAYOUTS.items():
        consts.append(f'LAY_{machine.upper()}_COUNT = {len(key_entries(layout))}')
    write_if_changed(os.path.join(outdir, 'consts.inc'), '\n'.join(consts) + '\n')
    write_if_changed(os.path.join(outdir, 'layout.inc'), '\n'.join(lines) + '\n')
    font = font_bytes()
    text = '; generated by tools/petskey_build.py - 3x5 font, 5 bytes per ASCII code 0-127\nfont:\n'
    for i in range(0, len(font), 20):
        text += '   !byte ' + ', '.join(str(b) for b in font[i:i + 20]) + '\n'
    write_if_changed(os.path.join(outdir, 'font.inc'), text)
    for machine, name in (('c64', 'PETSKEY.K64'), ('c128', 'PETSKEY.K128')):
        data = compile_keys(host.build_keymap(machine, 'symbolic'), machine)
        path = os.path.join(outdir, name)
        if not (os.path.exists(path) and open(path, 'rb').read() == data):
            with open(path, 'wb') as f:
                f.write(data)


# ---------------------------------------------------------------------------
# A minimal .d64 writer: 35 tracks, files stored one after another.

def sectors_in(track):
    return 21 if track <= 17 else 19 if track <= 24 else 18 if track <= 30 else 17


def d64_offset(track, sector):
    return (sum(sectors_in(t) for t in range(1, track)) + sector) * 256


def to_petscii_name(name):
    return bytes(ord(c.upper()) if c != '_' else 0x20 for c in name)[:16]


def make_d64(out_path, files, disk_name='PETSKEY REMOTE', disk_id='PK'):
    """files: [(c64 name, 'PRG'|'SEQ', bytes)]"""
    image = bytearray(d64_offset(36, 0))
    used = set()
    # interleave 10 within a track, like the 1541 does
    order = [t for t in range(1, 36) if t != 18]

    def alloc_chain(data):
        blocks = [data[i:i + 254] for i in range(0, max(len(data), 1), 254)]
        chain = []
        for track in order:
            n = sectors_in(track)
            s = 0
            free = [x for x in range(n) if (track, x) not in used]
            while free and len(chain) < len(blocks):
                if s not in free:
                    s = free[0]
                chain.append((track, s))
                used.add((track, s))
                free.remove(s)
                s = (s + 10) % n
            if len(chain) == len(blocks):
                break
        if len(chain) < len(blocks):
            raise SystemExit('disk full')
        for i, ((t, s), block) in enumerate(zip(chain, blocks)):
            off = d64_offset(t, s)
            if i + 1 < len(chain):
                image[off], image[off + 1] = chain[i + 1]
            else:
                image[off], image[off + 1] = 0, len(block) + 1
            image[off + 2:off + 2 + len(block)] = block
        return chain[0], len(chain)

    entries = []
    for name, kind, data in files:
        (t, s), blocks = alloc_chain(data)
        entries.append((name, kind, t, s, blocks))
    if len(entries) > 8:
        raise SystemExit('this writer keeps the directory to one sector (8 files)')
    # directory 18/1
    used.add((18, 0))
    used.add((18, 1))
    dir_off = d64_offset(18, 1)
    image[dir_off] = 0
    image[dir_off + 1] = 0xFF
    for i, (name, kind, t, s, blocks) in enumerate(entries):
        e = dir_off + i * 32
        image[e + 2] = 0x80 | {'SEQ': 1, 'PRG': 2}[kind]
        image[e + 3], image[e + 4] = t, s
        raw = to_petscii_name(name)
        image[e + 5:e + 21] = raw + b'\xa0' * (16 - len(raw))
        image[e + 30:e + 32] = struct.pack('<H', blocks)
    # BAM 18/0
    bam = d64_offset(18, 0)
    image[bam:bam + 4] = bytes([18, 1, 0x41, 0])
    for track in range(1, 36):
        n = sectors_in(track)
        bits = 0
        free = 0
        for s in range(n):
            if (track, s) not in used:
                bits |= 1 << s
                free += 1
        image[bam + 4 * track:bam + 4 * track + 4] = bytes([free]) + struct.pack('<I', bits)[:3]
    raw = to_petscii_name(disk_name)
    image[bam + 0x90:bam + 0xA0] = raw + b'\xa0' * (16 - len(raw))
    image[bam + 0xA0:bam + 0xA2] = b'\xa0\xa0'
    image[bam + 0xA2:bam + 0xA4] = disk_id.encode()
    image[bam + 0xA4] = 0xA0
    image[bam + 0xA5:bam + 0xA7] = b'2A'
    image[bam + 0xA7:bam + 0xAB] = b'\xa0' * 4
    with open(out_path, 'wb') as f:
        f.write(image)


def main(argv):
    if len(argv) >= 2 and argv[0] == 'gen':
        write_includes(argv[1])
    elif len(argv) >= 3 and argv[0] == 'd64':
        files = []
        for path in argv[2:]:
            name, _, kind = path.partition('=')
            if not kind:
                kind = 'PRG'
            src, _, c64name = name.partition(':')
            with open(src, 'rb') as f:
                files.append((c64name or os.path.basename(src), kind.upper(), f.read()))
        make_d64(argv[1], files)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
