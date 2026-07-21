#!/usr/bin/env python3
"""Drive the whole real stack headlessly and measure it.

Patched VTE on Xvfb -> tmux -> Vim + vim-euporie -> sidecar -> Euporie -> sixel.
Nothing is simulated: the kernel is the one the plugin starts, the cell is run
with a genuine Shift+Enter X key event, and the numbers come from tmux tapping
the Euporie pane's own output stream. Screenshots of the X display give visual
evidence of whether frames tear.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

WORK = Path("/tmp/euporie-stack")
DISPLAY = ":97"
PLUGIN = Path("/home/ert/code/krystophny/vim-euporie")
SOCKET = "stack"

APP = r'''
import sys, gi
gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gtk, Vte, GLib

window = Gtk.Window(title="euporie-stack")
window.set_default_size(1400, 900)
term = Vte.Terminal()
term.set_font_scale(1.0)
# VTE keeps Sixel support off unless the embedder asks for it; Xfce Terminal
# does, so a bare widget would render no graphics at all and wrongly look
# like a broken stack.
term.set_enable_sixel(True)
window.add(term)
term.spawn_async(
    Vte.PtyFlags.DEFAULT, None, sys.argv[1:], [],
    GLib.SpawnFlags.DEFAULT, None, None, -1, None, None, None,
)
window.show_all()
window.connect("destroy", Gtk.main_quit)
GLib.timeout_add_seconds(600, Gtk.main_quit)
Gtk.main()
'''

TMUX_CONF = """\
set -g default-terminal "tmux-256color"
set -as terminal-features ",xterm*:sixel"
set -as terminal-features ",xterm*:extkeys"
set -as terminal-features ",xterm*:sync"
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


def analyse(blob: bytes, frames: int) -> None:
    """Report the numbers that decide whether a frame can be seen tearing."""
    sixels = list(re.finditer(rb"\x1bP[0-9;]*q", blob))
    print(f"  bytes written        : {len(blob)}")
    print(f"  sixel images emitted : {len(sixels)} for {frames} redraws")
    if frames:
        print(f"  images per redraw    : {len(sixels) / frames:.2f}")
        print(f"  bytes per redraw     : {len(blob) // frames}")

    # An image is safe from tearing only if the erase that precedes it and the
    # image itself land inside one synchronized-output window.
    inside = outside = 0
    unpaired = 0
    for match in sixels:
        head = blob[: match.start()]
        opened = head.rfind(b"\x1b[?2026h")
        closed = head.rfind(b"\x1b[?2026l")
        if opened > closed:
            inside += 1
            # Does the erase of the image area sit in the same window?
            window = blob[opened : match.start()]
            if not re.search(rb"\x1b\[[0-9]*[JK]", window) and b"  " not in window:
                unpaired += 1
        else:
            outside += 1
    print(f"  images inside a sync window : {inside}")
    print(f"  images outside any window   : {outside}"
          + ("   <-- these can tear" if outside else ""))
    print(f"  sync begin / end pairs      : "
          f"{len(re.findall(rb'\x1b\[.2026h', blob))} / "
          f"{len(re.findall(rb'\x1b\[.2026l', blob))}")


class Stack:
    def __init__(self) -> None:
        self.env = dict(os.environ)
        self.env["DISPLAY"] = DISPLAY
        self.env.pop("WAYLAND_DISPLAY", None)
        for key in ("TMUX", "TMUX_PANE"):
            self.env.pop(key, None)
        self.xvfb: subprocess.Popen | None = None
        self.app: subprocess.Popen | None = None
        self.window: str | None = None

    def tmux(self, *args: str) -> str:
        return subprocess.run(["tmux", "-L", SOCKET, *args],
                              capture_output=True, text=True).stdout.strip()

    def start(self, cell_file: Path) -> None:
        subprocess.run(["tmux", "-L", SOCKET, "kill-server"], capture_output=True)
        if WORK.exists():
            shutil.rmtree(WORK)
        WORK.mkdir(parents=True)
        (WORK / "tmux.conf").write_text(TMUX_CONF)
        (WORK / "vimrc").write_text(VIMRC.format(plugin=PLUGIN))
        (WORK / "app.py").write_text(APP)
        shutil.copy(cell_file, WORK / "cells.py")

        self.xvfb = subprocess.Popen(
            ["Xvfb", DISPLAY, "-screen", "0", "1600x1000x24"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2.0)

        self.app = subprocess.Popen(
            ["python3", str(WORK / "app.py"),
             "tmux", "-L", SOCKET, "-f", str(WORK / "tmux.conf"),
             "new-session", "-x", "200", "-y", "50",
             "vim", "--clean", "-u", str(WORK / "vimrc"), str(WORK / "cells.py")],
            env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(8.0)

        found = subprocess.run(["xdotool", "search", "--name", "euporie-stack"],
                               env=self.env, capture_output=True, text=True).stdout.split()
        if not found:
            raise RuntimeError("VTE window never appeared: "
                               + self.app.stderr.read().decode()[:500])
        self.window = found[-1]
        for action in ("windowactivate", "windowfocus"):
            subprocess.run(["xdotool", action, "--sync", self.window],
                           env=self.env, capture_output=True)
        time.sleep(1.5)

    def key(self, combo: str) -> None:
        """Send a genuine X key event to the focused VTE widget."""
        subprocess.run(["xdotool", "key", "--clearmodifiers", combo],
                       env=self.env, capture_output=True)

    def screenshot(self, name: str) -> Path:
        path = WORK / f"{name}.png"
        subprocess.run(["import", "-window", "root", str(path)],
                       env=self.env, capture_output=True)
        return path

    def panes(self) -> list[str]:
        return self.tmux("list-panes", "-a", "-F", "#{pane_id} #{pane_title}").splitlines()

    def stop(self) -> None:
        subprocess.run(["tmux", "-L", SOCKET, "kill-server"], capture_output=True)
        for proc in (self.app, self.xvfb):
            if proc:
                proc.kill()


def main() -> int:
    cell = Path(sys.argv[1])
    stack = Stack()
    try:
        stack.start(cell)
        print("vim started in VTE; panes:")
        for line in stack.panes():
            print("   ", line)

        print("\nstarting Euporie (this resolves a uv environment, be patient)...")
        stack.tmux("send-keys", "-t", "0.0", ":EuporieStart", "C-m")

        pane_id = None
        for _ in range(60):
            time.sleep(5.0)
            panes = stack.panes()
            euporie = [p for p in panes if "euporie" in p.lower()]
            if euporie:
                pane_id = euporie[0].split()[0]
                break
        if not pane_id:
            print("Euporie pane never appeared. Panes:", stack.panes())
            print("vim screen:")
            print(stack.tmux("capture-pane", "-p", "-t", "0.0")[:1500])
            return 1

        print(f"Euporie pane is {pane_id}")
        capture = WORK / "euporie.bin"
        stack.tmux("pipe-pane", "-o", "-t", pane_id, f"cat >> {capture}")
        time.sleep(2.0)
        print("euporie pane contents:")
        print(stack.tmux("capture-pane", "-p", "-t", pane_id)[-300:])
        stack.screenshot("01-started")

        # --- Shift+Enter through the entire real stack -------------------
        print("\n=== Shift+Enter (real X key event) runs the cell ===")
        stack.tmux("select-pane", "-t", "%0")
        time.sleep(1.0)
        # The cursor must sit inside the code cell, not in the PEP 723 header
        # block above the first "# %%" marker.
        stack.tmux("send-keys", "-t", "%0", "G")
        time.sleep(0.5)
        before = capture.stat().st_size if capture.exists() else 0
        stack.key("shift+Return")
        time.sleep(25.0)
        produced = capture.read_bytes()[before:] if capture.exists() else b""
        sixels = re.findall(rb"\x1bP[0-9;]*q", produced)
        print(f"  bytes produced by the cell : {len(produced)}")
        print(f"  sixel images emitted       : {len(sixels)}")
        print(f"  -> Shift+Enter {'RAN THE CELL' if produced else 'DID NOTHING'}")
        stack.screenshot("02-after-shift-enter")

        # --- Animation: how much work per changed frame? -----------------
        print("\n=== animation: 8 redraws ===")
        mark = capture.stat().st_size
        for angle in range(30, 30 + 8 * 15, 15):
            stack.tmux("send-keys", "-t", pane_id, f"redraw({angle})", "C-m")
            time.sleep(3.0)
        time.sleep(3.0)
        anim = capture.read_bytes()[mark:]
        analyse(anim, 8)
        stack.screenshot("03-after-animation")

        print(f"\nscreenshots and capture in {WORK}")
        return 0
    finally:
        stack.stop()


if __name__ == "__main__":
    raise SystemExit(main())
