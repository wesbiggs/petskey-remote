#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""The C64 program's USB path, run in a 6502 emulator (py65) against the real build.

  make test-6502          (after `make`; needs `pip install py65`)

The 6502 translator must hold down the same keys as netkbd_host.py's Translator for
the same byte stream: both are fed random key sequences through every stock keymap,
and after every byte the HID usages a model of the TeensyROM's "USB Keyboard" Special
IO holds are compared with the Linux keys the Translator holds, mapped to usages. The
start-up detection is checked too: the USB registers, a SwiftLink, and nothing there.
"""
import os
import random
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'host'))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import netkbd_host as host  # noqa: E402
import hid_keytable  # noqa: E402

try:
    from py65.devices.mpu6502 import MPU
    from py65.memory import ObservableMemory
except ImportError:  # pragma: no cover
    MPU = None

PRG = os.path.join(ROOT, 'build', 'petskey-remote.prg')
LABELS = os.path.join(ROOT, 'build', 'labels')
IO1 = 0xDE00
TRAMPOLINE = 0x02A7          # free in the C64's page 2
STEP_LIMIT = 200000
TICK_CYCLES = 8400           # one keyboard scan: a byte must be handled well inside it
HID = hid_keytable.build_table()


def labels():
    out = {}
    with open(LABELS) as f:
        for line in f:
            parts = line.split()
            if len(parts) == 3 and parts[0] == 'al':
                out[parts[2].lstrip('.')] = int(parts[1].split(':')[1], 16)
    return out


class UsbKeyboard:
    """The registers as docs/USB-KEYBOARD.md describes them."""

    def __init__(self, host_present=True):
        self.counts = {}
        self.status = 1 if host_present else 0
        self.writes = []

    def held(self):
        return {u for u, n in self.counts.items() if n}

    def write(self, addr, value):
        reg = addr - IO1
        self.writes.append((reg, value))
        valid = value and (value <= 0x7F or 0xE0 <= value <= 0xE7)
        if reg == 0 and valid:
            self.counts[value] = self.counts.get(value, 0) + 1
        elif reg == 1 and valid and self.counts.get(value):
            self.counts[value] -= 1
        elif reg == 2:
            self.counts.clear()

    def read(self, addr):
        reg = addr - IO1
        return [self.status, 0, 1, 0, ord('T'), ord('R'), ord('K')][reg] if reg < 7 else 0


class Acia:
    def __init__(self):
        self.regs = [0, 0x10, 0, 0]
        self.writes = []

    def write(self, addr, value):
        self.writes.append((addr - IO1, value))
        if addr - IO1 in (2, 3):
            self.regs[addr - IO1] = value

    def read(self, addr):
        reg = addr - IO1
        return self.regs[reg] if reg < 4 else 0xFF


class OpenBus:
    def __init__(self):
        self.writes = []

    def write(self, addr, value):
        self.writes.append((addr - IO1, value))

    def read(self, addr):
        return 0xFF


@unittest.skipUnless(MPU, 'needs py65 (pip install py65)')
@unittest.skipUnless(os.path.exists(PRG), 'needs the build (make)')
class Usb6502Test(unittest.TestCase):
    def machine(self, device):
        mem = ObservableMemory()
        with open(PRG, 'rb') as f:
            data = f.read()
        load = data[0] | data[1] << 8
        for i, b in enumerate(data[2:]):
            mem[load + i] = b
        mem[0xFFD2] = 0x60                     # CHROUT: RTS
        mem.subscribe_to_write(range(IO1, IO1 + 0x100), device.write)
        mem.subscribe_to_read(range(IO1, IO1 + 0x100), device.read)
        mpu = MPU(memory=mem)
        return mem, mpu

    def call(self, mem, mpu, addr, a=0):
        mem[TRAMPOLINE:TRAMPOLINE + 3] = [0x20, addr & 0xFF, addr >> 8]
        mpu.pc, mpu.a, mpu.sp = TRAMPOLINE, a, 0xFF
        start = mpu.processorCycles
        for _ in range(STEP_LIMIT):
            if mpu.pc == TRAMPOLINE + 3:
                return mpu.processorCycles - start
            mpu.step()
        self.fail(f'no return from ${addr:04x}')

    def setUp(self):
        self.lab = labels()

    def start(self, keymap):
        dev = UsbKeyboard()
        mem, mpu = self.machine(dev)
        mem[self.lab['usb']] = 1
        data = b'PKM\x01' + host.encode_map(keymap)
        for i, b in enumerate(data):
            mem[self.lab['keys_buf'] + i] = b
        self.call(mem, mpu, self.lab['usb_begin'])
        return dev, mem, mpu

    def reference(self, keymap):
        held = set()

        def emit(key, value):
            (held.add if value else held.discard)(key)
        return host.Translator(keymap, emit), held

    def keymaps(self):
        for machine in ('c64', 'c128'):
            for layout in ('symbolic', 'positional'):
                yield f'{machine} {layout}', machine, host.build_keymap(machine, layout)
            yield f'{machine} macos', machine, host.build_keymap(
                machine, 'symbolic', [os.path.join(ROOT, 'host', 'macos.map')])

    def test_matches_the_host_translator(self):
        rng = random.Random(6526)
        for name, machine, keymap in self.keymaps():
            codes = list(range(64)) + (list(range(64, 88)) if machine == 'c128' else [])
            for run in range(12):
                dev, mem, mpu = self.start(keymap)
                ref, ref_held = self.reference(keymap)
                down, caps = set(), False
                for step in range(150):
                    r = rng.random()
                    if r < 0.03:
                        stream = [0xFE]                          # heartbeat
                    elif r < 0.05:
                        stream = [0x58, 0xD8]                  # RESTORE: down + up
                    elif r < 0.07 and machine == 'c128':
                        caps = not caps
                        stream = [0x59 if caps else 0xD9]
                    elif r < 0.08:
                        stream, down = [0xFC], set()          # bye: release everything
                    elif r < 0.10:
                        stream = [rng.choice(codes) | 0x80]   # up of a key held before the scan
                    else:
                        code = rng.choice(sorted(down) if len(down) >= 4 else codes)
                        stream = [code | 0x80] if code in down else [code]
                        (down.discard if code in down else down.add)(code)
                    for b in stream:
                        cycles = self.call(mem, mpu, self.lab['usb_feed'], b)
                        self.assertLess(cycles, TICK_CYCLES // 2, (name, b))
                        if b == 0xFC:
                            ref.release_all()
                        elif b < 0x80:
                            ref.key_down(b)
                        elif b < 0xE0:
                            ref.key_up(b & 0x7F)
                        want = {HID[k] for k in ref_held if HID[k]}
                        self.assertEqual(dev.held(), want,
                                         f'{name}, run {run}, step {step}, byte ${b:02x}')

    def test_shift_overrides(self):
        keymap = host.build_keymap('c64', 'symbolic')
        dev, mem, mpu = self.start(keymap)
        C = host.C64_CODES
        feed = lambda b: self.call(mem, mpu, self.lab['usb_feed'], b)
        feed(C['LSHIFT'])
        self.assertEqual(dev.held(), {0xE1})
        feed(C['2'])                                   # SHIFT+2 = SHIFT+APOSTROPHE
        self.assertEqual(dev.held(), {0xE1, 0x34})
        feed(C['2'] | 0x80)
        feed(C['7'])                                   # SHIFT+7 = APOSTROPHE, SHIFT let go
        self.assertEqual(dev.held(), {0x34})
        feed(C['7'] | 0x80)
        self.assertEqual(dev.held(), {0xE1})
        feed(C['LSHIFT'] | 0x80)
        self.assertEqual(dev.held(), set())
        feed(0xFE)
        self.assertIn((3, 0xFE), dev.writes)

    def test_detects_the_usb_registers_and_leaves_them_alone(self):
        dev = UsbKeyboard()
        mem, mpu = self.machine(dev)
        self.call(mem, mpu, self.lab['detect_io'])
        self.assertFalse(mpu.p & 1)
        self.assertEqual(mem[self.lab['usb']], 1)
        self.assertEqual(dev.writes, [])               # no ACIA set-up written over them
        self.call(mem, mpu, self.lab['rx_off'])
        self.call(mem, mpu, self.lab['rx_on'])
        self.assertEqual(dev.writes, [])

    def test_detects_a_swiftlink(self):
        dev = Acia()
        mem, mpu = self.machine(dev)
        self.call(mem, mpu, self.lab['detect_io'])
        self.assertFalse(mpu.p & 1)
        self.assertEqual(mem[self.lab['usb']], 0)
        self.assertIn((3, 0x1F), dev.writes)

    def test_nothing_there(self):
        dev = OpenBus()
        mem, mpu = self.machine(dev)
        self.call(mem, mpu, self.lab['detect_io'])
        self.assertTrue(mpu.p & 1)


if __name__ == '__main__':
    unittest.main()
