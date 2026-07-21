#!/usr/bin/env python3
"""Measure the byte stream tmux sends to the TERMINAL, which is what flickers.

pipe-pane taps the pane's own output, i.e. what Euporie writes into tmux. The
synchronized-output wrapping and the image re-emissions are added by tmux on
the way out to the client, so the only place the flicker is visible in bytes is
the client pty. This attaches the tmux client to a pty we own and records that.

The real plugin, a real kernel and a real cell run underneath; only the outer
terminal is a pty instead of a VTE window.
"""

from __future__ import annotations

import fcntl
import os
import pty
import re
import select
import shutil
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

WORK = Path("/tmp/euporie-flicker")
PLUGIN = Path("/home/ert/code/krystophny/vim-euporie")
SOCKET = "flicker"
COLS, ROWS, CW, CH = 200, 50, 9, 16
SHIFT_ENTER = b"\x1b[27;2;13~"

TMUX_CONF = """\
set -g default-terminal "tmux-256color"
set -as terminal-features ",xterm*:sixel"
set -as terminal-features ",xterm*:extkeys"
{sync}
set -s extended-keys on
set -g allow-passthrough on
set -g status off
set -g mouse on
"""

VIMRC = """\
set nocompatible
set runtimepath^={plugin}
filetype plugin indent on
syntax on
set ttimeoutlen=20
set mouse=a
set ttymouse=sgr
"""


class Outer:
    def __init__(self, sync: bool) -> None:
        self.sync = sync
        self.master: int | None = None
        self.proc: subprocess.Popen | None = None
        self.stream = bytearray()

    def tmux(self, *args: str) -> str:
        return subprocess.run(["tmux", "-L", SOCKET, *args],
                              capture_output=True, text=True).stdout.strip()

    def start(self, cells: Path) -> None:
        subprocess.run(["tmux", "-L", SOCKET, "kill-server"], capture_output=True)
        if WORK.exists():
            shutil.rmtree(WORK)
        WORK.mkdir(parents=True)
        (WORK / "tmux.conf").write_text(TMUX_CONF.format(
            sync='set -as terminal-features ",xterm*:sync"' if self.sync else ""))
        (WORK / "vimrc").write_text(VIMRC.format(plugin=PLUGIN))
        shutil.copy(cells, WORK / "cells.py")

        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ,
                    struct.pack("HHHH", ROWS, COLS, COLS * CW, ROWS * CH))
        env = dict(os.environ)
        env["TERM"] = "xterm-256color"
        for key in ("TMUX", "TMUX_PANE"):
            env.pop(key, None)
        self.proc = subprocess.Popen(
            ["tmux", "-L", SOCKET, "-f", str(WORK / "tmux.conf"),
             "new-session", "-x", str(COLS), "-y", str(ROWS),
             "vim", "--clean", "-u", str(WORK / "vimrc"), str(WORK / "cells.py")],
            stdin=slave, stdout=slave, stderr=slave, env=env, preexec_fn=os.setsid)
        os.close(slave)
        self.read(5.0)

    def read(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            ready, _, _ = select.select([self.master], [], [], 0.05)
            if ready:
                try:
                    data = os.read(self.master, 262144)
                except OSError:
                    return
                if not data:
                    return
                self.stream.extend(data)

    def mark(self) -> int:
        return len(self.stream)

    def stop(self) -> None:
        subprocess.run(["tmux", "-L", SOCKET, "kill-server"], capture_output=True)
        if self.proc:
            try:
                os.killpg(os.getpgid(self.proc.pid), 9)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        if self.master is not None:
            try:
                os.close(self.master)
            except OSError:
                pass


def analyse(blob: bytes, redraws: int, label: str) -> None:
    sixels = list(re.finditer(rb"\x1bP[0-9;]*q", blob))
    begins = len(re.findall(rb"\x1b\[\?2026h", blob))
    ends = len(re.findall(rb"\x1b\[\?2026l", blob))
    inside = 0
    naked = 0
    for match in sixels:
        head = blob[: match.start()]
        if head.rfind(b"\x1b[?2026h") > head.rfind(b"\x1b[?2026l"):
            inside += 1
        else:
            naked += 1

    # A window that erases the image area but carries no image leaves the
    # picture blank for one frame -- that is exactly what reads as a flicker.
    blank = 0
    for window in re.findall(rb"\x1b\[\?2026h(.*?)\x1b\[\?2026l", blob, re.S):
        if re.search(rb"\x1b\[[0-9]*[JK]", window) and not re.search(rb"\x1bP[0-9;]*q", window):
            blank += 1

    print(f"  --- {label} ---")
    print(f"    bytes to terminal        : {len(blob)}")
    print(f"    sixel images sent        : {len(sixels)} for {redraws} redraws")
    print(f"    sync windows (begin/end) : {begins}/{ends}")
    print(f"    images inside a window   : {inside}")
    print(f"    images with no window    : {naked}" + ("   <-- can tear" if naked else ""))
    print(f"    windows that erase but carry no image : {blank}"
          + ("   <-- blank frames" if blank else ""))


def main() -> int:
    cells = Path(sys.argv[1])
    sync = "--no-sync" not in sys.argv
    outer = Outer(sync)
    try:
        outer.start(cells)
        outer.tmux("send-keys", "-t", "0.0", ":EuporieStart", "C-m")
        pane = None
        for _ in range(70):
            outer.read(5.0)
            found = [p for p in outer.tmux(
                "list-panes", "-a", "-F", "#{pane_id} #{pane_title}").splitlines()
                if "euporie" in p.lower()]
            if found:
                pane = found[0].split()[0]
                break
        if not pane:
            print("Euporie pane never appeared")
            return 1
        print(f"euporie pane {pane}; features: "
              f"{outer.tmux('display', '-p', '#{client_termfeatures}')}")
        outer.read(5.0)

        # Run the cell with a real Shift+Enter, injected as the terminal would.
        outer.tmux("select-pane", "-t", "%0")
        outer.tmux("send-keys", "-t", "%0", "G")
        outer.read(1.0)
        start = outer.mark()
        os.write(outer.master, SHIFT_ENTER)
        outer.read(30.0)
        first = bytes(outer.stream[start:])
        print(f"\nfirst render: {len(re.findall(rb'.P[0-9;]*q', first))} image(s), "
              f"{len(first)} bytes")

        print("\n=== animation: 8 redraws ===")
        start = outer.mark()
        for angle in range(30, 30 + 8 * 15, 15):
            outer.tmux("send-keys", "-t", pane, f"redraw({angle})", "C-m")
            outer.read(3.5)
        outer.read(3.0)
        analyse(bytes(outer.stream[start:]), 8,
                f"sync {'ON' if sync else 'OFF'}")
        Path(WORK / "terminal.bin").write_bytes(bytes(outer.stream))
        print(f"\nfull terminal stream: {WORK / 'terminal.bin'}")
        return 0
    finally:
        outer.stop()


if __name__ == "__main__":
    raise SystemExit(main())
