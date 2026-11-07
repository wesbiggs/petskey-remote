# SPDX-License-Identifier: Apache-2.0
# python3 -m unittest discover -s pi
import os
import tempfile
import unittest

import netkbd_host as h

K = h.LINUX_KEYS
C = h.C64_CODES


def run(keys, machine='c64', layout='symbolic', extra=''):
    """keys: list of '+NAME' / '-NAME' C64 key presses; returns the Linux events."""
    events = []
    keymap = h.build_keymap(machine, layout)
    if extra:
        h.parse_keymap(extra, keymap)
    t = h.Translator(keymap, lambda key, value: events.append(('+' if value else '-') + h.LINUX_KEY_NAMES[key]))
    for k in keys:
        (t.key_down if k[0] == '+' else t.key_up)(C[k[1:]])
    return events, t


class TranslatorTest(unittest.TestCase):
    def test_plain_letter_and_shift_pass_through(self):
        ev, _ = run(['+A', '-A', '+LSHIFT', '+A', '-A', '-LSHIFT'])
        self.assertEqual(ev, ['+A', '-A', '+LEFTSHIFT', '+A', '-A', '-LEFTSHIFT'])

    def test_symbolic_shifted_digit_changes_key(self):
        # C64 SHIFT+2 is a double quote: SHIFT+APOSTROPHE on a US layout
        ev, _ = run(['+RSHIFT', '+2', '-2', '-RSHIFT'])
        self.assertEqual(ev, ['+LEFTSHIFT', '+APOSTROPHE', '-APOSTROPHE', '-LEFTSHIFT'])

    def test_shift_removed_for_unshifted_target(self):
        # C64 SHIFT+7 is an apostrophe: the Linux shift must be up while it is typed
        ev, _ = run(['+LSHIFT', '+7', '-7', '+A', '-A', '-LSHIFT'])
        self.assertEqual(ev, ['+LEFTSHIFT', '-LEFTSHIFT', '+APOSTROPHE', '-APOSTROPHE',
                              '+LEFTSHIFT', '+A', '-A', '-LEFTSHIFT'])

    def test_shift_added_for_shifted_target(self):
        ev, _ = run(['+PLUS', '-PLUS', '+AT', '-AT'])
        self.assertEqual(ev, ['+LEFTSHIFT', '+EQUAL', '-EQUAL', '-LEFTSHIFT',
                              '+LEFTSHIFT', '+2', '-2', '-LEFTSHIFT'])

    def test_cursor_keys(self):
        ev, _ = run(['+CRSR_DOWN', '-CRSR_DOWN', '+LSHIFT', '+CRSR_DOWN', '-CRSR_DOWN', '-LSHIFT'])
        self.assertEqual(ev, ['+DOWN', '-DOWN', '+LEFTSHIFT', '-LEFTSHIFT', '+UP', '-UP',
                              '+LEFTSHIFT', '-LEFTSHIFT'])

    def test_binding_fixed_at_press_time(self):
        # SHIFT released before CRSR: the key that went down (UP) is the one released
        ev, _ = run(['+LSHIFT', '+CRSR_DOWN', '-LSHIFT', '-CRSR_DOWN'])
        self.assertEqual(ev, ['+LEFTSHIFT', '-LEFTSHIFT', '+UP', '-UP'])

    def test_modifiers_and_machine(self):
        ev, _ = run(['+CTRL', '+C', '-C', '-CTRL', '+COMMODORE', '-COMMODORE'], machine='c64')
        self.assertEqual(ev, ['+LEFTCTRL', '+C', '-C', '-LEFTCTRL', '+LEFTALT', '-LEFTALT'])
        ev, _ = run(['+COMMODORE', '-COMMODORE', '+ALT', '-ALT'], machine='c128')
        self.assertEqual(ev, ['+LEFTMETA', '-LEFTMETA', '+LEFTALT', '-LEFTALT'])

    def test_caps_lock_toggles(self):
        ev, _ = run(['+CAPS_LOCK', '-CAPS_LOCK'], machine='c128')
        self.assertEqual(ev, ['+CAPSLOCK', '-CAPSLOCK', '+CAPSLOCK', '-CAPSLOCK'])

    def test_two_keys_one_target(self):
        ev, _ = run(['+RUN_STOP', '+ESC', '-RUN_STOP', '-ESC'], machine='c128')
        self.assertEqual(ev, ['+ESC', '-ESC'])

    def test_release_all_and_stray_up(self):
        ev, t = run(['-RETURN', '+LSHIFT', '+Q', '+CTRL'])
        t.release_all()
        self.assertEqual(sorted(ev[3:]), sorted(['-Q', '-LEFTCTRL', '-LEFTSHIFT']))
        self.assertEqual(ev[:3], ['+LEFTSHIFT', '+Q', '+LEFTCTRL'])

    def test_positional(self):
        ev, _ = run(['+PLUS', '-PLUS', '+LSHIFT', '+2', '-2', '-LSHIFT'], layout='positional')
        self.assertEqual(ev, ['+MINUS', '-MINUS', '+LEFTSHIFT', '+2', '-2', '-LEFTSHIFT'])

    def test_keymap_file_overrides(self):
        ev, _ = run(['+RESTORE', '-RESTORE', '+F1', '-F1'],
                    extra='RESTORE = NONE\nF1 = KEY_PAGEUP  # comment\n')
        self.assertEqual(ev, ['+PAGEUP', '-PAGEUP'])

    def test_keymap_errors(self):
        with self.assertRaises(h.KeymapError):
            h.parse_keymap('NOPE = A')
        with self.assertRaises(h.KeymapError):
            h.parse_keymap('A = KEY_NOPE')

    def test_print_keymap_round_trips(self):
        keymap = h.build_keymap('c128', 'symbolic')
        again = h.parse_keymap(h.format_keymap(keymap))
        self.assertEqual(again, keymap)


class SessionTest(unittest.TestCase):
    def test_stream(self):
        events, machines = [], []
        def make(machine, uploaded):
            machines.append(machine)
            return h.Translator(h.build_keymap(machine, 'symbolic'),
                                lambda k, v: events.append((h.LINUX_KEY_NAMES[k], v)))
        s = h.Session(make, log=lambda m: None)
        a = C['A']
        # modem echo before the hello is ignored, then hello C128/PAL, A down/up,
        # heartbeat, Q down, bye (releases Q), then "+++" which must be ignored
        s.feed(b'ATDT X\rCONNECT\r' + bytes([0xF0, 1, 3, a, 0xFE, a | 0x80, C['Q'], 0xFC]) + b'+++')
        self.assertEqual(machines, ['c128'])
        self.assertEqual(events, [('A', 1), ('A', 0), ('Q', 1), ('Q', 0)])


class KeymapUploadTest(unittest.TestCase):
    def test_labels(self):
        keymap = h.parse_keymap('F1 = KEY_PAGEUP "PgU"  # comment\nSHIFT+F1 = KEY_PAGEDOWN "#!"\n')
        self.assertEqual(keymap['labels'], {('plain', C['F1']): 'PgU', ('shift', C['F1']): '#!'})
        self.assertEqual(h.parse_keymap(h.format_keymap(keymap)), keymap)
        with self.assertRaises(h.KeymapError):
            h.parse_keymap('F1 = KEY_A "x" KEY_B')

    def test_encode_decode_round_trip(self):
        for machine in ('c64', 'c128'):
            keymap = h.build_keymap(machine, 'symbolic')
            data = h.encode_map(keymap)
            self.assertEqual(len(data), h.MAP_BYTES)
            back = h.decode_map(data)
            self.assertEqual(back['plain'], keymap['plain'])
            self.assertEqual(back['shift'], keymap['shift'])

    def test_session_uses_uploaded_keymap(self):
        events, got = [], []
        keymap = h.parse_keymap('A = KEY_B\nLSHIFT = LEFTSHIFT\n')
        data = h.encode_map(keymap)

        def make(machine, uploaded):
            got.append(uploaded is not None)
            km = uploaded if uploaded is not None else h.build_keymap(machine, 'symbolic')
            return h.Translator(km, lambda k, v: events.append((h.LINUX_KEY_NAMES[k], v)))
        s = h.Session(make, log=lambda m: None)
        s.feed(bytes([0xF0, 2, 0, 0xF1, len(data) & 0xFF, len(data) >> 8]) + data
               + bytes([C['A'], C['A'] | 0x80]))
        self.assertEqual(got, [False, True])
        self.assertEqual(events, [('B', 1), ('B', 0)])


if __name__ == '__main__':
    unittest.main()
