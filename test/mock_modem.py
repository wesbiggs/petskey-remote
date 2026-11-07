#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""A stand-in for TeensyROM's Ethernet modem, for testing PETSKEY Remote in VICE.

VICE emulates the SwiftLink cartridge and connects its RS232 side to this program
using the ip232 protocol (x64sc -acia1 -acia1mode 1 -rsdev1 127.0.0.1:6400 -rsdev1ip232).
In command mode it echoes what it gets and answers like TeensyROM:
  atc           Ethernet settings, including " Gateway IP: ...", then OK
  atdt host:port  opens a TCP connection, raises carrier and passes bytes both ways
"+++" with a second of quiet before and after hangs up. ip232: $FF $00 / $FF $01
from here drops / raises carrier, and a data byte $FF goes as $FF $FF.

  mock_modem.py [--listen 6400] [--gateway 127.0.0.1] [--log FILE]
"""
import argparse
import selectors
import socket
import time

MAGIC = 0xFF


def to_petscii(text):
    out = bytearray()
    for ch in text:
        c = ord(ch)
        if 0x61 <= c <= 0x7A:
            out.append(c - 0x20)
        elif 0x41 <= c <= 0x5A:
            out.append(c + 0x80)
        elif ch == '\n':
            out.append(13)
        else:
            out.append(c)
    return bytes(out)


class Modem:
    def __init__(self, sock, gateway, log):
        self.c64 = sock
        self.gateway = gateway
        self.log = log
        self.remote = None
        self.cmd = bytearray()
        self.esc = False
        self.last_tx = 0.0
        self.plus = 0
        self.plus_time = 0.0

    def send_c64(self, data):
        self.c64.sendall(bytes(data).replace(b'\xff', b'\xff\xff'))

    def carrier(self, on):
        self.c64.sendall(bytes([MAGIC, 1 if on else 0]))

    def reply(self, text):          # TeensyROM's replies: response codes in plain ASCII
        self.send_c64(text.encode() + b'\r')

    def from_c64(self, data):
        for b in data:
            if self.esc:
                self.esc = False
                if b != MAGIC:
                    continue        # DTR change: ignore
            elif b == MAGIC:
                self.esc = True
                continue
            self.byte(b)

    def byte(self, b):
        now = time.monotonic()
        if self.remote:
            if b == ord('+') and (self.plus or now - self.last_tx > 1):
                self.plus += 1
                self.plus_time = now
            else:
                self.plus = 0
            self.last_tx = now
            self.remote.sendall(bytes([b]))
            return
        self.send_c64([b])          # echo
        if b in (0x14, 0x08):
            self.cmd = self.cmd[:-1]
        elif b == 13:
            line = bytes(c & 0x7F | (0x20 if c & 0x40 else 0) for c in self.cmd).decode('ascii', 'replace')
            self.cmd = bytearray()
            self.command(line.strip())
        else:
            self.cmd.append(b)

    def command(self, line):
        self.log(f'command: {line!r}')
        if line == 'atc':
            self.send_c64(to_petscii(' MAC Address: BE:0C:64:C0:FF:EE\n Local IP: 192.168.7.2\n'
                                     ' Subnet Mask: 255.255.255.0\n'
                                     f' Gateway IP: {self.gateway}\n'))
            self.reply('OK')
        elif line.startswith('atdt'):
            host, _, port = line[4:].partition(':')
            self.send_c64(to_petscii(f'Trying "{host}"\n on port {port}...\n'))
            try:
                self.remote = socket.create_connection((host, int(port or 6400)), timeout=5)
            except (OSError, ValueError) as e:
                self.log(f'dial failed: {e}')
                self.reply('NO ANSWER')
                return
            self.remote.setblocking(False)
            self.reply('CONNECT')
            self.carrier(True)
            self.last_tx = time.monotonic()
            self.log(f'connected to {host}:{port}')
        elif line.startswith('at'):
            self.reply('OK')
        elif line:
            self.reply('ERROR')

    def hang_up(self, why):
        self.log(f'hang up: {why}')
        if self.remote:
            self.remote.close()
            self.remote = None
        self.carrier(False)
        self.plus = 0
        self.reply('NO CARRIER')

    def poll(self):
        if self.plus == 3 and time.monotonic() - self.plus_time > 1:
            self.hang_up('+++')


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--listen', type=int, default=6400)
    p.add_argument('--gateway', default='127.0.0.1')
    p.add_argument('--log')
    args = p.parse_args()
    logf = open(args.log, 'a', buffering=1) if args.log else None

    def log(msg):
        line = f'{time.strftime("%H:%M:%S")} {msg}'
        print(line, flush=True)
        if logf:
            logf.write(line + '\n')

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('127.0.0.1', args.listen))
    srv.listen(1)
    log(f'mock modem on 127.0.0.1:{args.listen}')
    sel = selectors.DefaultSelector()
    sel.register(srv, selectors.EVENT_READ, 'listen')
    modem = None
    while True:
        for key, _ in sel.select(timeout=0.2):
            if key.data == 'listen':
                sock, _ = srv.accept()
                log('emulator connected')
                if modem:
                    sel.unregister(modem.c64)
                    modem.c64.close()
                modem = Modem(sock, args.gateway, log)
                modem.carrier(False)
                sel.register(sock, selectors.EVENT_READ, 'c64')
            elif key.data == 'c64':
                data = modem.c64.recv(4096)
                if not data:
                    log('emulator went away')
                    sel.unregister(modem.c64)
                    if modem.remote:
                        sel.unregister(modem.remote)
                        modem.remote.close()
                    modem = None
                    continue
                had_remote = modem.remote
                modem.from_c64(data)
                if modem.remote and not had_remote:
                    sel.register(modem.remote, selectors.EVENT_READ, 'remote')
            elif key.data == 'remote':
                data = modem.remote.recv(4096) if modem and modem.remote else b''
                if not data:
                    sel.unregister(key.fileobj)
                    modem.hang_up('host closed the connection')
                else:
                    modem.send_c64(data)
        if modem:
            had_remote = modem.remote
            modem.poll()
            if had_remote and not modem.remote:
                sel.unregister(had_remote)


if __name__ == '__main__':
    main()
