#!/usr/bin/env python3
"""Full chain: patched VTE -> tmux -> Vim + vim-euporie -> <S-CR>.

Each layer was verified alone; this drives all of them together with a real X
key event, so a pass means Shift+Enter genuinely works in the user's stack
without anyone having to press a key.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

WORK = Path("/tmp/vte-full-probe")
DISPLAY = ":98"
PLUGIN = "/home/ert/code/krystophny/vim-euporie"
SOCKET = "vtefull"

APP = r'''
import sys, gi
gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gtk, Vte, GLib

command = sys.argv[1:]
window = Gtk.Window(title="vte-full-probe")
window.set_default_size(900, 500)
term = Vte.Terminal()
window.add(term)
term.spawn_async(
    Vte.PtyFlags.DEFAULT, None, command, [],
    GLib.SpawnFlags.DEFAULT, None, None, -1, None, None, None,
)
window.show_all()
window.connect("destroy", Gtk.main_quit)
GLib.timeout_add_seconds(70, Gtk.main_quit)
Gtk.main()
'''


def main() -> int:
    extended_keys = sys.argv[1] if len(sys.argv) > 1 else "on"
    with_extkeys = "--no-extkeys" not in sys.argv

    if WORK.exists():
        subprocess.run(["rm", "-rf", str(WORK)])
    WORK.mkdir(parents=True)
    marker = WORK / "fired.txt"
    info = WORK / "info.txt"

    (WORK / "cell.py").write_text("# %%\nprint('hello')\n")

    features = ['set -as terminal-features ",xterm*:sixel"']
    if with_extkeys:
        features.append('set -as terminal-features ",xterm*:extkeys"')
    (WORK / "tmux.conf").write_text(
        'set -g default-terminal "tmux-256color"\n'
        + "\n".join(features) + "\n"
        + f"set -s extended-keys {extended_keys}\n"
        "set -g status off\n"
    )
    (WORK / "vimrc").write_text(
        "set nocompatible\n"
        f"set runtimepath^={PLUGIN}\n"
        "filetype plugin indent on\nsyntax on\nset ttimeoutlen=20\n"
    )
    app_path = WORK / "app.py"
    app_path.write_text(APP)

    subprocess.run(["tmux", "-L", SOCKET, "kill-server"], capture_output=True)

    xvfb = subprocess.Popen(["Xvfb", DISPLAY, "-screen", "0", "1200x800x24"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2.0)
    env = dict(os.environ)
    env["DISPLAY"] = DISPLAY
    env.pop("WAYLAND_DISPLAY", None)
    for key in ("TMUX", "TMUX_PANE"):
        env.pop(key, None)

    app = subprocess.Popen(
        ["python3", str(app_path),
         "tmux", "-L", SOCKET, "-f", str(WORK / "tmux.conf"),
         "new-session", "vim", "--clean", "-u", str(WORK / "vimrc"),
         str(WORK / "cell.py")],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    time.sleep(10.0)

    found = subprocess.run(["xdotool", "search", "--name", "vte-full-probe"],
                           env=env, capture_output=True, text=True).stdout.split()
    if not found:
        app.kill(); xvfb.kill()
        print("window not found; stderr:", app.stderr.read().decode()[:600])
        return 1
    window_id = found[-1]
    subprocess.run(["xdotool", "windowactivate", "--sync", window_id],
                   env=env, capture_output=True)
    subprocess.run(["xdotool", "windowfocus", "--sync", window_id],
                   env=env, capture_output=True)
    time.sleep(1.5)

    def tmux(*args: str) -> str:
        return subprocess.run(["tmux", "-L", SOCKET, *args],
                              capture_output=True, text=True).stdout.strip()

    # Record what the layers negotiated.
    tmux("send-keys", "-t", "0.0",
         f":call writefile([&term, &keyprotocol, maparg('<S-CR>','n')], '{info}')", "C-m")
    time.sleep(1.5)
    # Replace the plugin's action with a marker so nothing tries to boot a kernel.
    tmux("send-keys", "-t", "0.0",
         f":nnoremap <buffer> <S-CR> :call writefile(['S-CR'],'{marker}')<CR>", "C-m")
    time.sleep(1.0)
    tmux("send-keys", "-t", "0.0",
         f":nnoremap <buffer> <CR> :call writefile(['plain-CR'],'{marker}')<CR>", "C-m")
    time.sleep(1.0)

    client_features = tmux("display", "-p", "#{client_termfeatures}")

    # The real thing: a genuine Shift+Return X event into the VTE widget.
    # No --window: this goes through XTEST as a genuine key event rather than
    # an XSendEvent, which GTK treats differently.
    subprocess.run(["xdotool", "key", "--clearmodifiers", "shift+Return"],
                   env=env, capture_output=True)
    time.sleep(2.5)
    if not marker.exists():
        # Fall back to the synthetic path so a failure is attributable.
        subprocess.run(["xdotool", "key", "--window", window_id, "shift+Return"],
                       env=env, capture_output=True)
        time.sleep(2.0)

    fired = marker.read_text().strip() if marker.exists() else "<nothing fired>"
    fields = info.read_text().splitlines() if info.exists() else []

    subprocess.run(["tmux", "-L", SOCKET, "kill-server"], capture_output=True)
    app.kill(); xvfb.kill()

    print(f"config: extended-keys={extended_keys}, "
          f"extkeys feature={'yes' if with_extkeys else 'no'}")
    print(f"  tmux client features : {client_features}")
    print(f"  vim &term            : {fields[0] if len(fields) > 0 else '?'}")
    print(f"  vim &keyprotocol     : {(fields[1] if len(fields) > 1 else '?')[:60]}")
    print(f"  plugin <S-CR> mapping: {fields[2] if len(fields) > 2 else '?'}")
    print(f"  RESULT               : {fired}")
    return 0 if fired == "S-CR" else 1


if __name__ == "__main__":
    raise SystemExit(main())
