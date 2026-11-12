# SPDX-License-Identifier: Apache-2.0
# python3 -m unittest discover -s host
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


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def mac(repeat_rate=25.0):
    """A MacKeyboard that records (vk, kind, flags, autorepeat) instead of posting."""
    posted, clock = [], FakeClock()
    kb = h.MacKeyboard(post=lambda vk, kind, flags, autorepeat=False:
                       posted.append((vk, kind, flags, autorepeat)),
                       repeat_delay=0.5, repeat_rate=repeat_rate, clock=clock, log=lambda m: None)
    return kb, posted, clock


DOWN, UP, FLAGS = h.EVENT_KEY_DOWN, h.EVENT_KEY_UP, h.EVENT_FLAGS_CHANGED


class MacKeyboardTest(unittest.TestCase):
    def test_every_default_key_has_a_mac_key(self):
        for machine in ('c64', 'c128'):
            for layout in h.KEYMAP_LAYOUTS:
                keymap = h.build_keymap(machine, layout, [os.path.join(os.path.dirname(__file__), 'macos.map')])
                for table in ('plain', 'shift'):
                    for code, (_, key) in keymap[table].items():
                        if key is not None:
                            self.assertIn(key, h.MAC_KEYCODES, (machine, layout, h.C64_KEYS[code]))

    def test_letter_and_shift(self):
        kb, posted, _ = mac()
        for code, value in [(K['LEFTSHIFT'], 1), (K['A'], 1), (K['A'], 0), (K['LEFTSHIFT'], 0)]:
            kb.key(code, value)
        shift = h.FLAG_SHIFT | 0x02
        self.assertEqual(posted, [(0x38, FLAGS, shift, False), (0x00, DOWN, shift, False),
                                  (0x00, UP, shift, False), (0x38, FLAGS, 0, False)])

    def test_alt_is_option_and_meta_is_command(self):
        kb, posted, _ = mac()
        kb.key(K['LEFTMETA'], 1)
        kb.key(K['LEFTALT'], 1)
        kb.key(K['C'], 1)
        self.assertEqual(posted[-1], (0x08, DOWN, h.FLAG_CMD | 0x08 | h.FLAG_OPTION | 0x20, False))
        kb.key(K['LEFTMETA'], 0)
        self.assertEqual(posted[-1], (0x37, FLAGS, h.FLAG_OPTION | 0x20, False))

    def test_both_shifts_keep_flag_until_last_release(self):
        kb, posted, _ = mac()
        kb.key(K['LEFTSHIFT'], 1)
        kb.key(K['RIGHTSHIFT'], 1)
        kb.key(K['LEFTSHIFT'], 0)
        self.assertEqual(posted[-1][2], h.FLAG_SHIFT | 0x04)
        kb.key(K['RIGHTSHIFT'], 0)
        self.assertEqual(posted[-1][2], 0)

    def test_arrow_and_keypad_flags(self):
        kb, posted, _ = mac()
        kb.key(K['UP'], 1)
        kb.key(K['KP5'], 1)
        kb.key(K['F1'], 1)
        self.assertEqual([p[2] for p in posted], [h.FLAG_NUMPAD | h.FLAG_FN, h.FLAG_NUMPAD, h.FLAG_FN])

    def test_caps_lock_toggles_on_press(self):
        kb, posted, _ = mac()
        kb.key(K['CAPSLOCK'], 1)
        kb.key(K['CAPSLOCK'], 0)
        kb.key(K['A'], 1)
        self.assertEqual(posted, [(0x39, FLAGS, h.FLAG_CAPS, False), (0x00, DOWN, h.FLAG_CAPS, False)])
        kb.key(K['CAPSLOCK'], 1)
        self.assertEqual(posted[-1], (0x39, FLAGS, 0, False))

    def test_unmapped_key_is_ignored(self):
        kb, posted, _ = mac()
        kb.key(K['MUTE'], 1)
        kb.key(K['MUTE'], 0)
        self.assertEqual(posted, [])

    def test_key_repeat(self):
        kb, posted, clock = mac()
        self.assertIsNone(kb.next_timeout())
        kb.key(K['A'], 1)
        self.assertAlmostEqual(kb.next_timeout(), 0.5)
        clock.now += 0.4
        kb.tick()
        self.assertEqual(len(posted), 1)
        clock.now += 0.1
        kb.tick()
        self.assertEqual(posted[-1], (0x00, DOWN, 0, True))
        self.assertAlmostEqual(kb.next_timeout(), 0.04)
        clock.now += 5                       # the loop stalled: one repeat, not a burst
        kb.tick()
        kb.tick()
        self.assertEqual(len(posted), 3)
        self.assertAlmostEqual(kb.next_timeout(), 0.04)
        kb.key(K['A'], 0)
        self.assertIsNone(kb.next_timeout())

    def test_modifier_does_not_repeat_or_stop_repeat(self):
        kb, posted, clock = mac()
        kb.key(K['LEFTSHIFT'], 1)
        self.assertIsNone(kb.next_timeout())
        kb.key(K['B'], 1)
        kb.key(K['LEFTCTRL'], 1)
        clock.now += 0.5
        kb.tick()
        self.assertEqual(posted[-1], (0x0B, DOWN, h.FLAG_SHIFT | 0x02 | h.FLAG_CTRL | 0x01, True))

    def test_newest_key_repeats_and_no_repeat_option(self):
        kb, posted, clock = mac()
        kb.key(K['A'], 1)
        kb.key(K['S'], 1)
        kb.key(K['A'], 0)                    # releasing the older key keeps S repeating
        clock.now += 0.5
        kb.tick()
        self.assertEqual(posted[-1][:2], (0x01, DOWN))
        kb, posted, clock = mac(repeat_rate=0)
        kb.key(K['A'], 1)
        self.assertIsNone(kb.next_timeout())

    def test_translator_drives_mac_keyboard(self):
        kb, posted, _ = mac()
        t = h.Translator(h.build_keymap('c64', 'symbolic'), kb.key)
        t.key_down(C['RSHIFT'])
        t.key_down(C['7'])                   # an apostrophe: shift lifted for it
        self.assertEqual(posted[-1], (0x27, DOWN, 0, False))
        t.release_all()
        self.assertIsNone(kb.next_timeout())
        self.assertEqual(kb.held, set())


class GreetingTest(unittest.TestCase):
    def test_greeting(self):
        import socket
        name = socket.gethostname()
        self.assertEqual(h.greeting_text('Linked to {hostname}.'), f'Linked to {name}.')
        self.assertEqual(h.greeting_text('{machine} on  the\tdesk', 'c128'), 'C128 on the desk')
        self.assertEqual(h.greeting_text(''), '')
        self.assertEqual(len(h.greeting_text('x' * 200)), h.GREETING_MAX)
        with self.assertRaises(SystemExit):
            h.greeting_text('{nope}')


if __name__ == '__main__':
    unittest.main()
