# SPDX-License-Identifier: Apache-2.0
# python3 -m unittest discover -s tools
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import petskey_build as b  # noqa: E402

h = b.host
C = h.C64_CODES


class LayoutTest(unittest.TestCase):
    def test_layouts_fit_and_cover_the_keys(self):
        for machine, layout in b.LAYOUTS.items():
            b.check_layout(layout)
            codes = {e[0] for e in b.key_entries(layout)}
            self.assertTrue(set(range(64)) | {C['RESTORE']} <= codes, machine)
        c128 = {e[0] for e in b.key_entries(b.LAYOUT_C128)}
        self.assertTrue(set(range(64, 90)) <= c128)


class LegendTest(unittest.TestCase):
    def test_symbolic_legends(self):
        legends = b.legends_for(h.build_keymap('c64', 'symbolic'), 'c64')
        self.assertEqual(legends[C['2']], ('2', '"'))
        self.assertEqual(legends[C['A']], ('A', 'A'))
        self.assertEqual(legends[C['CRSR_DOWN']], (b.DOWN_A, b.UP_A))
        self.assertEqual(legends[C['RETURN']], ('Enter', 'Enter'))
        self.assertEqual(legends[C['COMMODORE']], ('Alt', 'Alt'))
        self.assertEqual(legends[C['SPACE']], ('', ''))

    def test_labels_and_width(self):
        keymap = h.build_keymap('c128', 'symbolic')
        h.parse_keymap('F1 = KEY_PAGEUP\nSHIFT+F1 = KEY_PAGEDOWN "Dn"\nHELP = KEY_A "Manual"', keymap)
        legends = b.legends_for(keymap, 'c128')
        self.assertEqual(legends[C['F1']], ('PgU', 'Dn'))     # 2-cell key: 3 characters
        self.assertEqual(legends[C['HELP']], ('Man', 'F11'))   # SHIFT+HELP has its own line

    def test_keys_file(self):
        data = b.compile_keys(h.build_keymap('c128', 'symbolic'), 'c128')
        self.assertEqual(len(data), b.KEYS_FILE_SIZE)
        self.assertEqual(data[2:6], b.KEYS_MAGIC)
        self.assertEqual(h.decode_map(data[6:6 + h.MAP_BYTES])['plain'],
                         h.build_keymap('c128', 'symbolic')['plain'])


class D64Test(unittest.TestCase):
    def test_files_read_back(self):
        files = [('PETSKEY REMOTE', 'PRG', bytes(range(256)) * 40), ('PETSKEY.K64', 'PRG', b'\x00\x00abc')]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'x.d64')
            b.make_d64(path, files)
            with open(path, 'rb') as f:
                img = f.read()
        self.assertEqual(len(img), 174848)
        o = b.d64_offset(18, 1)
        for i, (name, kind, content) in enumerate(files):
            e = img[o + i * 32:o + i * 32 + 32]
            self.assertEqual(e[5:21].rstrip(b'\xa0'), name.encode())
            t, s, data = e[3], e[4], bytearray()
            while True:
                off = b.d64_offset(t, s)
                if img[off] == 0:
                    data += img[off + 2:off + 1 + img[off + 1]]
                    break
                data += img[off + 2:off + 256]
                t, s = img[off], img[off + 1]
            self.assertEqual(bytes(data), content)
        bam = b.d64_offset(18, 0)
        free = sum(img[bam + 4 * t] for t in range(1, 36) if t != 18)
        self.assertEqual(free, 664 - 41 - 1)


if __name__ == '__main__':
    unittest.main()
