#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""End-to-end test in VICE: the real .d64 and C64 program, a mock TeensyROM modem,
and the real host program in --dry-run mode.

Needs x64sc (VICE 3.x) with the C64 and 1541 ROMs installed, Xvfb, xdotool and
ImageMagick's import. Run from the repository root after `make`:

  python3 test/vice_e2e.py [--machine c64|c128] [--out DIR]

--machine c128 runs x128 in 64 mode (needs the C128 ROMs too) and also checks the
C128's extra keys.

Run 1 (first run): the settings screen offers the mock's gateway as the host; RETURN
three times saves PETSKEY.CFG and connects. Keys are pressed through the emulated
keyboard and must arrive at the host as Linux key events. RUN/STOP+RESTORE quits.
Run 2: the saved settings connect without any typing.
Screenshots go to DIR (default build/e2e).
"""
import argparse
import os
import shutil
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEM_PORT = 6400
HOST_PORT = 6526
DISPLAY = ':93'


class Fail(Exception):
    pass


def wait_for(path, text, timeout, after=0):
    end = time.time() + timeout
    while time.time() < end:
        if os.path.exists(path):
            with open(path) as f:
                content = f.read()
            if text in content[after:]:
                return
        time.sleep(0.2)
    raise Fail(f'timed out waiting for {text!r} in {os.path.basename(path)}')


def size(path):
    return os.path.getsize(path) if os.path.exists(path) else 0


def key(*args):
    subprocess.run(['xdotool', *args], env={**os.environ, 'DISPLAY': DISPLAY}, check=True)


def tap(keysym, hold=0.25):
    key('keydown', keysym)
    time.sleep(hold)
    key('keyup', keysym)
    time.sleep(0.25)


def shot(out, name):
    subprocess.run(['import', '-display', DISPLAY, '-window', 'root', os.path.join(out, name)], check=True)


def start_vice(disk, log, machine):
    # -default first: it resets everything given before it
    if machine == 'c128':
        emulator = ['x128', '-default', '-go64', '-40col', '-keymap', '1']
    else:
        emulator = ['x64sc', '-default', '-keymap', '1']
    args = [*emulator, '+sound', '-sounddev', 'dummy',
            '-acia1', '-acia1mode', '1', '-acia1base', '0xDE00', '-acia1irq', '1', '-myaciadev', '0',
            '-rsdev1', f'127.0.0.1:{MODEM_PORT}', '-rsdev1ip232', '-rsdev1baud', '38400',
            '+drive8truedrive', '-virtualdev8', '-attach8rw', '-autostart', disk]
    return subprocess.Popen(args, env={**os.environ, 'DISPLAY': DISPLAY},
                            stdout=open(log, 'w'), stderr=subprocess.STDOUT)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--machine', choices=('c64', 'c128'), default='c64')
    p.add_argument('--out')
    args = p.parse_args()
    out = args.out or os.path.join(ROOT, 'build', f'e2e-{args.machine}')
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    disk = os.path.join(out, 'petskey-remote.d64')
    shutil.copy(os.path.join(ROOT, 'build', 'petskey-remote.d64'), disk)
    host_log, modem_log = os.path.join(out, 'host.log'), os.path.join(out, 'modem.log')

    procs = []

    def spawn(cmd, log, **kw):
        proc = subprocess.Popen(cmd, stdout=open(log, 'w'), stderr=subprocess.STDOUT, **kw)
        procs.append(proc)
        return proc

    try:
        spawn(['Xvfb', DISPLAY, '-screen', '0', '1024x768x24'], os.path.join(out, 'xvfb.log'))
        spawn([sys.executable, '-u', os.path.join(ROOT, 'pi', 'netkbd_host.py'), '--dry-run',
               '--port', str(HOST_PORT), '--bind', '127.0.0.1',
               '--greeting', 'Typing into {hostname} ({machine})'], host_log)
        spawn([sys.executable, '-u', os.path.join(ROOT, 'test', 'mock_modem.py'),
               '--listen', str(MODEM_PORT), '--gateway', '127.0.0.1'], modem_log)
        time.sleep(1.5)

        # ---- run 1: first run, settings, keys, quit
        vice = start_vice(disk, os.path.join(out, 'vice1.log'), args.machine)
        procs.append(vice)
        wait_for(modem_log, "command: 'atc'", 90)           # asking for the gateway
        time.sleep(3)
        shot(out, '1-settings.png')
        for _ in range(3):                                     # init, host (= gateway), port
            tap('Return', 0.1)
            time.sleep(0.5)
        wait_for(modem_log, f'connected to 127.0.0.1:{HOST_PORT}', 90)
        wait_for(host_log, 'keymap received from the C64', 30)
        wait_for(host_log, f'hello from a {args.machine.upper()}', 5)
        time.sleep(1.5)
        shot(out, '2-connected.png')

        if args.machine == 'c128':
            # VICE's positional C128 keymap: F10 = TAB, F9 = ESC, F5 = HELP, F11 = ALT,
            # Up = cursor up: all three K lines. Keypad keys sit on the same lines, but
            # keypad presses from xdotool never reach x128 under Xvfb, so they aren't
            # tested here. (VICE's keymap also swaps keypad + and - against the matrix.)
            missing = []
            for keysym, expect in (('F10', 'KEY_TAB'), ('F9', 'KEY_ESC'), ('Up', 'KEY_UP'),
                                   ('F11', 'KEY_LEFTALT'), ('F5', 'KEY_F9')):
                mark = size(host_log)
                tap(keysym)
                try:
                    wait_for(host_log, f'{expect} up', 5, mark)
                except Fail:
                    missing.append(f'{keysym}->{expect}')
            if missing:
                raise Fail(f'C128 keys that did not arrive: {missing}')
            mark = size(host_log)
            key('keydown', 'F10')
            time.sleep(0.4)
            shot(out, '2b-tab-held.png')
            key('keyup', 'F10')
            wait_for(host_log, 'KEY_TAB up', 10, mark)

        mark = size(host_log)
        tap('a')
        wait_for(host_log, 'KEY_A up', 10, mark)

        mark = size(host_log)
        key('keydown', 'Shift_L')
        time.sleep(0.3)
        key('keydown', '2')
        time.sleep(0.5)
        shot(out, '3-shift-2.png')
        key('keyup', '2')
        time.sleep(0.2)
        key('keyup', 'Shift_L')
        wait_for(host_log, 'KEY_LEFTSHIFT up', 10, mark)
        with open(host_log) as f:
            events = [l.strip() for l in f.read()[mark:].splitlines() if l.strip().startswith('KEY_')]
        expected = ['KEY_LEFTSHIFT down', 'KEY_APOSTROPHE down', 'KEY_APOSTROPHE up', 'KEY_LEFTSHIFT up']
        if events != expected:
            raise Fail(f'SHIFT+2 gave {events}, expected {expected}')

        mark = size(host_log)
        tap('Page_Up')                                         # RESTORE: Tab
        wait_for(host_log, 'KEY_TAB up', 10, mark)

        mark = size(host_log)
        key('keydown', 'Escape')                               # RUN/STOP + RESTORE: quit
        time.sleep(0.4)
        tap('Page_Up')
        key('keyup', 'Escape')
        wait_for(host_log, 'C64 signed off', 10, mark)
        wait_for(modem_log, 'hang up: +++', 15)
        time.sleep(2)
        shot(out, '4-quit.png')
        vice.send_signal(signal.SIGTERM)
        vice.wait(10)

        # ---- run 2: saved settings connect on their own
        mark_modem = size(modem_log)
        vice = start_vice(disk, os.path.join(out, 'vice2.log'), args.machine)
        procs.append(vice)
        wait_for(modem_log, f'connected to 127.0.0.1:{HOST_PORT}', 90, mark_modem)
        wait_for(host_log, 'keymap received from the C64', 30, mark)
        time.sleep(1.5)
        shot(out, '5-reconnected.png')
        mark = size(host_log)
        tap('q')
        wait_for(host_log, 'KEY_Q up', 10, mark)
        print('PASS')
        return 0
    except Fail as e:
        print('FAIL:', e)
        try:
            shot(out, 'failure.png')
        except Exception:
            pass
        return 1
    finally:
        for proc in reversed(procs):
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(5)
                except subprocess.TimeoutExpired:
                    proc.kill()


if __name__ == '__main__':
    sys.exit(main())
