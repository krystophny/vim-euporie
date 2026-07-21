#!/usr/bin/env python3
"""Verify the patched VTE reports Shift+Enter as CSI 27 ; 2 ; 13 ~.

This is the one layer that previously needed a human at a keyboard. A real
VteTerminal on a headless X server runs a child that asks for modifyOtherKeys
and records its stdin; xdotool delivers a genuine Shift+Return X event. What the
child receives is exactly what tmux and Vim would receive.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

WORK = Path("/tmp/vte-key-probe")
DISPLAY = ":99"

CHILD = r'''
import os, sys, termios, tty
out = open(sys.argv[1], "wb", buffering=0)
fd = sys.stdin.fileno()
old = termios.tcgetattr(fd)
tty.setraw(fd)
# Ask for modifyOtherKeys level 2, exactly as Vim and tmux do.
os.write(1, b"\x1b[>4;2m")
try:
    while True:
        data = os.read(fd, 1024)
        if not data:
            break
        out.write(data)
        if b"\x03" in data:
            break
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
'''

APP = r'''
import sys, gi
gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gtk, Vte, GLib

dump, child = sys.argv[1], sys.argv[2]
window = Gtk.Window(title="vte-key-probe")
term = Vte.Terminal()
window.add(term)
term.spawn_async(
    Vte.PtyFlags.DEFAULT, None,
    ["/usr/bin/python3", child, dump],
    [], GLib.SpawnFlags.DEFAULT, None, None, -1, None, None, None,
)
window.show_all()
window.connect("destroy", Gtk.main_quit)
GLib.timeout_add_seconds(25, Gtk.main_quit)
Gtk.main()
'''


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    dump = WORK / "received.bin"
    if dump.exists():
        dump.unlink()
    child_path = WORK / "child.py"
    child_path.write_text(CHILD)
    app_path = WORK / "app.py"
    app_path.write_text(APP)

    xvfb = subprocess.Popen(
        ["Xvfb", DISPLAY, "-screen", "0", "1024x768x24"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(2.0)
    env = dict(os.environ)
    env["DISPLAY"] = DISPLAY
    env.pop("WAYLAND_DISPLAY", None)

    app = subprocess.Popen(
        ["python3", str(app_path), str(dump), str(child_path)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    time.sleep(5.0)

    found = subprocess.run(
        ["xdotool", "search", "--name", "vte-key-probe"],
        env=env, capture_output=True, text=True,
    ).stdout.split()
    if not found:
        app.kill()
        xvfb.kill()
        err = app.stderr.read().decode()[:800] if app.stderr else ""
        print("could not find the VTE window; app stderr:")
        print(err)
        return 1

    window_id = found[-1]
    subprocess.run(["xdotool", "windowactivate", "--sync", window_id],
                   env=env, capture_output=True)
    subprocess.run(["xdotool", "windowfocus", "--sync", window_id],
                   env=env, capture_output=True)
    time.sleep(1.0)

    results = {}
    for label, keys in (
        ("Enter", "Return"),
        ("Shift+Enter", "shift+Return"),
        ("Ctrl+Enter", "ctrl+Return"),
        ("Alt+Enter", "alt+Return"),
        ("plain 'a'", "a"),
        ("Tab", "Tab"),
        ("Shift+Tab", "shift+Tab"),
    ):
        before = dump.stat().st_size if dump.exists() else 0
        subprocess.run(["xdotool", "key", "--window", window_id, "--clearmodifiers", keys],
                       env=env, capture_output=True)
        time.sleep(0.8)
        after = dump.read_bytes()[before:] if dump.exists() else b""
        results[label] = after

    subprocess.run(["xdotool", "key", "--window", window_id, "ctrl+c"],
                   env=env, capture_output=True)
    time.sleep(0.5)
    app.kill()
    xvfb.kill()

    print(f"libvte in use: {subprocess.run(['pacman','-Q','vte3-sixel-git'], capture_output=True, text=True).stdout.strip()}")
    print("\nWhat the patched VTE sends to the application:\n")
    ok = True
    expectations = {
        "Enter": b"\r",
        "Shift+Enter": b"\x1b[27;2;13~",
        "Ctrl+Enter": b"\x1b[27;5;13~",
        "plain 'a'": b"a",
        "Tab": b"\t",
    }
    for label, data in results.items():
        want = expectations.get(label)
        mark = ""
        if want is not None:
            good = data == want
            ok = ok and good
            mark = "  OK" if good else f"  MISMATCH (wanted {want!r})"
        print(f"  {label:<12} -> {data!r}{mark}")

    print(f"\nverdict: {'all expected sequences correct' if ok else 'SOMETHING IS WRONG'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
