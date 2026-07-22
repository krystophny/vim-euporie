#!/usr/bin/env python3
"""Measure flicker while dragging the slider, which is how the user drives it.

Updating a widget by running a later cell does not re-render it -- the output
area goes blank and stays blank -- so cell-driven redraws cannot measure
flicker at all. The user moves the slider with the mouse, which keeps the
widget live, so that is what this reproduces: real button-press, motion and
release events into the VTE window, with the screen recorded throughout.

The slider is located rather than guessed. tmux knows which row of which pane
holds it, and dividing the terminal's pixel size by the client's cell size
converts that row to screen coordinates exactly.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

import stack as harness

FPS = 15
RECORD_SECONDS = 26


def coloured(path: Path) -> np.ndarray:
    """Saturated pixels: the plot, but not the grey-on-black console text."""
    frame = np.asarray(Image.open(path).convert("RGB"), dtype=np.int16)
    return (frame.max(axis=2) - frame.min(axis=2)) > 40


def cell_size(st: harness.Stack) -> tuple[float, float]:
    geometry = subprocess.run(
        ["xdotool", "getwindowgeometry", "--shell", st.window],
        env=st.env, capture_output=True, text=True).stdout
    values = dict(part.split("=", 1) for part in geometry.split() if "=" in part)
    win_w, win_h = int(values["WIDTH"]), int(values["HEIGHT"])
    client_w = int(st.tmux("display", "-p", "#{client_width}"))
    client_h = int(st.tmux("display", "-p", "#{client_height}"))
    return win_w / client_w, win_h / client_h


def locate_slider(st: harness.Stack, pane: str) -> tuple[int, int, int] | None:
    """Return (y, x_start, x_end) in screen pixels for the slider handle.

    Columns come from capture-pane, which maps to screen columns exactly. The
    row does NOT: capture-pane row indices are offset from screen rows by the
    layout's leading spacer rows, and a press one row off lands on a
    DummyControl spacer and is silently swallowed. Take the row from the
    pixels instead: the slider is the first bright text band in the pane,
    above the figure. The press must also land on the handle itself, because
    Euporie sliders do not jump on a bare track click.
    """
    rows = st.tmux("capture-pane", "-p", "-t", pane).splitlines()
    line = next((row for row in rows if "Azimuth" in row), "")
    track = [i for i, char in enumerate(line) if char in "─━╌╍◈●○┿"]
    handle = line.find("●")
    if len(track) < 4 or handle < 0:
        return None

    cell_w, _cell_h = cell_size(st)
    pane_left = int(st.tmux("display", "-p", "-t", pane, "#{pane_left}"))
    pane_px = int(pane_left * cell_w)

    shot = st.screenshot("slider-row-find")
    grey = np.asarray(Image.open(shot).convert("L"), dtype=np.int16)
    bright = np.where(grey[:150, pane_px + 40:].max(axis=1) > 60)[0]
    if bright.size == 0:
        return None
    band_end = bright[0]
    for row in bright:
        if row > band_end + 2:
            break
        band_end = row
    band = bright[bright <= band_end]
    y = int((band.min() + band.max()) / 2)

    x_start = int((pane_left + handle + 0.5) * cell_w)
    x_end = int((pane_left + track[-1] + 0.5) * cell_w)
    print(f"  handle col {handle}, track ends {track[-1]}, "
          f"pixel band {band.min()}..{band.max()}")
    print(f"  press at ({x_start},{y}), drag to x={x_end}")
    return y, x_start, x_end


def main() -> int:
    cells = Path(sys.argv[1])
    st = harness.Stack()
    try:
        st.start(cells)
        st.tmux("send-keys", "-t", "0.0", ":EuporieStart", "C-m")
        pane = None
        for _ in range(70):
            time.sleep(5.0)
            found = [p for p in st.panes() if "euporie" in p.lower()]
            if found:
                pane = found[0].split()[0]
                break
        if not pane:
            print("Euporie pane never appeared")
            return 1

        st.tmux("select-pane", "-t", "%0")
        st.tmux("send-keys", "-t", "%0", "G")
        time.sleep(1.0)
        st.key("shift+Return")
        time.sleep(35.0)

        # Focus the Euporie pane before the drag: the first press otherwise
        # goes into tmux's pane-switching binding rather than the slider.
        st.tmux("select-pane", "-t", pane)
        time.sleep(1.0)

        cell_w, _ = cell_size(st)
        pane_px = int(int(st.tmux("display", "-p", "-t", pane, "#{pane_left}")) * cell_w)

        shot = st.screenshot("slider-reference")
        ink = coloured(shot)
        right = ink[:, pane_px:]
        rows = np.where(right.sum(axis=1) > 5)[0]
        if rows.size == 0:
            print("no figure in the Euporie pane; nothing to measure")
            return 1
        top, bottom = int(rows.min()), int(rows.max())
        reference = int(right[top : bottom + 1].sum())
        print(f"figure rows {top}..{bottom}, reference ink {reference}")

        found = locate_slider(st, pane)
        if not found:
            print("could not locate the slider; refusing to guess")
            return 1
        slider_y, x_start, x_end = found

        frames = harness.WORK / "drag-frames"
        frames.mkdir(exist_ok=True)
        recorder = subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "x11grab",
             "-framerate", str(FPS), "-video_size", "1600x1000",
             "-i", harness.DISPLAY, "-t", str(RECORD_SECONDS),
             str(frames / "f%04d.png")],
            env=st.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)

        subprocess.run(["xdotool", "mousemove", str(x_start), str(slider_y),
                        "mousedown", "1"], env=st.env, capture_output=True)
        for step in range(1, 21):
            x = x_start + (x_end - x_start) * step // 20
            subprocess.run(["xdotool", "mousemove", str(x), str(slider_y)],
                           env=st.env, capture_output=True)
            time.sleep(0.9)
        subprocess.run(["xdotool", "mouseup", "1"], env=st.env, capture_output=True)
        recorder.wait(timeout=60)

        shots = sorted(frames.glob("f*.png"))
        inks = np.array([int(coloured(p)[top : bottom + 1, pane_px:].sum())
                         for p in shots])
        drawn = int(np.percentile(inks, 90))
        blank = inks < drawn * 0.15

        runs, run = [], 0
        for index, is_blank in enumerate(blank):
            if is_blank:
                run += 1
            else:
                if run and index > run:
                    runs.append(run)
                run = 0

        levels = len(set(inks.tolist()))
        print(f"  frames                : {len(inks)}")
        print(f"  ink when drawn (p90)  : {drawn}  (reference {reference})")
        print(f"  distinct ink levels   : {levels}")
        if levels <= 3 or drawn < reference * 0.5:
            print("  MEASUREMENT FAILED: the figure did not change during the")
            print("  drag, so this run says nothing about flicker.")
            return 1
        print(f"  blank frames mid-drag : {sum(runs)}")
        print(f"  blank runs            : {runs}")
        if runs:
            print(f"  longest blank         : {max(runs)} frames "
                  f"= {max(runs) / FPS * 1000:.0f} ms")
        print(f"  VERDICT: {'FLICKERS' if runs else 'no blank frames during the drag'}")
        return 0
    finally:
        st.stop()


if __name__ == "__main__":
    raise SystemExit(main())
