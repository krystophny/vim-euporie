#!/usr/bin/env python3
"""Verify the patched VTE's sixel image lifecycle under tmux.

This is the probe that caught the black-figure bug: the terminal retired an
image whenever text was inserted into any row it covers, ignoring the column,
so a repaint of the neighbouring pane or the border column killed the figure
and the next full redraw showed black. The fix intersects on row and column.

Needs no Euporie and runs in about half a minute, so run it after every VTE
rebuild. Cases:

- text in the other pane on the same rows must NOT retire the image
- select-pane round trips and refresh-client must keep it visible
- an alternate-screen program must cover it, and quitting must not ghost
- text written over the image's own cells MUST retire it (the ghost fix)
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import numpy as np
from PIL import Image

WORK = Path("/tmp/vte-images-probe")
DISPLAY = ":97"
SOCKET = "vteimg"

APP = r'''
import sys, gi
gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gtk, Vte, GLib

window = Gtk.Window(title="vte-images-probe")
window.set_default_size(1400, 900)
term = Vte.Terminal()
term.set_enable_sixel(True)
window.add(term)
term.spawn_async(
    Vte.PtyFlags.DEFAULT, None, sys.argv[1:], [],
    GLib.SpawnFlags.DEFAULT, None, None, -1, None, None, None,
)
window.show_all()
window.connect("destroy", Gtk.main_quit)
GLib.timeout_add_seconds(180, Gtk.main_quit)
Gtk.main()
'''


def ink(env: dict, name: str) -> int:
    path = WORK / f"{name}.png"
    subprocess.run(["import", "-window", "root", str(path)],
                   env=env, capture_output=True)
    frame = np.asarray(Image.open(path).convert("RGB"), dtype=np.int16)
    return int(((frame.max(axis=2) - frame.min(axis=2)) > 40)[:, 700:].sum())


def main() -> int:
    WORK.mkdir(exist_ok=True)
    (WORK / "app.py").write_text(APP)
    subprocess.run(["magick", "-size", "300x200", "gradient:red-blue",
                    str(WORK / "img.png")], capture_output=True)
    with open(WORK / "img.six", "wb") as fh:
        subprocess.run(["magick", str(WORK / "img.png"), "sixel:-"],
                       stdout=fh, stderr=subprocess.DEVNULL)
    (WORK / "tmux.conf").write_text(
        'set -as terminal-features ",xterm*:sixel"\n'
        'set -as terminal-features ",xterm*:sync"\n'
        "set -g status off\nset -g allow-passthrough on\n")

    env = dict(os.environ)
    env["DISPLAY"] = DISPLAY
    env.pop("TMUX", None)
    env.pop("TMUX_PANE", None)

    xvfb = subprocess.Popen(["Xvfb", DISPLAY, "-screen", "0", "1600x1000x24"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["tmux", "-L", SOCKET, "kill-server"], capture_output=True)
    time.sleep(2.0)
    app = subprocess.Popen(
        ["python3", str(WORK / "app.py"),
         "tmux", "-L", SOCKET, "-f", str(WORK / "tmux.conf"),
         "new-session", "-x", "200", "-y", "50", "bash", "--norc"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(5.0)

    def tmux(*args: str) -> str:
        return subprocess.run(["tmux", "-L", SOCKET, *args],
                              capture_output=True, text=True).stdout.strip()

    failures = 0

    def check(label: str, value: int, expect_image: bool) -> None:
        nonlocal failures
        good = value > 20000 if expect_image else value < 5000
        mark = "ok  " if good else "FAIL"
        if not good:
            failures += 1
        print(f"  [{mark}] {label:44s}: {value}")

    try:
        tmux("split-window", "-h", "bash", "--norc")
        time.sleep(1.0)
        tmux("send-keys", "-t", "0.1", f"clear; cat {WORK}/img.six", "C-m")
        time.sleep(2.0)
        check("image drawn in right pane", ink(env, "v0"), True)

        tmux("send-keys", "-t", "0.0",
             "for i in $(seq 1 30); do echo left pane text $i; done", "C-m")
        time.sleep(2.0)
        check("text in left pane on the same rows", ink(env, "v1"), True)

        tmux("select-pane", "-t", "0.0")
        time.sleep(1.0)
        tmux("select-pane", "-t", "0.1")
        time.sleep(1.0)
        check("select-pane round trip", ink(env, "v2"), True)

        tmux("refresh-client")
        time.sleep(1.0)
        check("refresh-client", ink(env, "v3"), True)

        tmux("send-keys", "-t", "0.1", "less /etc/services", "C-m")
        time.sleep(2.0)
        check("alternate-screen app covers image", ink(env, "v4"), False)
        tmux("send-keys", "-t", "0.1", "q")
        time.sleep(2.0)
        print(f"  [info] after quitting the app (tmux restores): {ink(env, 'v5')}")

        tmux("send-keys", "-t", "0.1",
             "clear; for i in $(seq 1 30); do echo overwrite $i; done", "C-m")
        time.sleep(2.0)
        check("text over the image's own cells", ink(env, "v6"), False)

        print("PASS" if failures == 0 else f"{failures} FAILURES")
        return 1 if failures else 0
    finally:
        tmux("kill-server")
        app.kill()
        xvfb.kill()


if __name__ == "__main__":
    raise SystemExit(main())
