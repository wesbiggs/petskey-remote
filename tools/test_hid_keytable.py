# SPDX-License-Identifier: Apache-2.0
# python3 -m unittest discover -s tools
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hid_keytable as t  # noqa: E402

h = t.host
K = h.LINUX_KEYS


class TableTest(unittest.TestCase):
    def test_every_host_key_is_decided(self):
        table = t.build_table()
        self.assertEqual(len(table), 256)
        self.assertEqual(table[K['A']], 0x04)
        self.assertEqual(table[K['ENTER']], 0x28)
        self.assertEqual(table[K['LEFTSHIFT']], 0xE1)
        self.assertEqual(table[K['RIGHTMETA']], 0xE7)
        self.assertEqual(table[K['RO']], 0)
        self.assertEqual(table[0], 0)

    def test_new_host_key_needs_a_decision(self):
        with self.assertRaisesRegex(t.TableError, 'NEWKEY'):
            t.build_table({**K, 'NEWKEY': 240})
        with self.assertRaisesRegex(t.TableError, 'not a netkbd_host'):
            t.build_table(K, {**t.HID_USAGES, 'NOPE': 0x10})

    def test_usages_stay_in_the_boot_report(self):
        with self.assertRaisesRegex(t.TableError, 'outside'):
            t.build_table(K, {**t.HID_USAGES, 'RO': 0x87})
        with self.assertRaisesRegex(t.TableError, 'both'):
            t.build_table(K, {**t.HID_USAGES, 'B': 0x04})

    def test_default_keymaps_reach_usb(self):
        # every key the stock keymaps use types something over USB, except the
        # positional layout's EQUALS, which sits on RO (JIS), outside the boot report
        table = t.build_table()
        for machine in ('c64', 'c128'):
            for layout in ('symbolic', 'positional'):
                keymap = h.build_keymap(machine, layout)
                for kind in ('plain', 'shift'):
                    for code, (_, key) in keymap[kind].items():
                        if key is None or (layout == 'positional' and key == K['RO']):
                            continue
                        self.assertTrue(table[key], (machine, layout, kind, h.C64_KEYS[code],
                                                     h.LINUX_KEY_NAMES[key]))

    def test_header(self):
        text = t.header(t.build_table())
        self.assertIn('UsbKbd_LinuxToHID[256] PROGMEM', text)
        self.assertEqual(text.count('0x'), 256)


if __name__ == '__main__':
    unittest.main()
