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
    """Return (y, x_start, x_end) in screen pixels for the slider track."""
    rows = st.tmux("capture-pane", "-p", "-t", pane).splitlines()
    row_index = next((i for i, line in enumerate(rows) if "Azimuth" in line), None)
    if row_index is None:
        return None
    line = rows[row_index]

    # The track is drawn to the right of the description with box characters.
    track = [i for i, char in enumerate(line) if char in "─━╌╍◈●○┿"]
    if len(track) < 4:
        return None

    cell_w, cell_h = cell_size(st)
    pane_top = int(st.tmux("display", "-p", "-t", pane, "#{pane_top}"))
    pane_left = int(st.tmux("display", "-p", "-t", pane, "#{pane_left}"))
    y = int((pane_top + row_index + 0.5) * cell_h)
    x_start = int((pane_left + track[0] + 0.5) * cell_w)
    x_end = int((pane_left + track[-1] + 0.5) * cell_w)
    print(f"  slider on pane row {row_index}, columns {track[0]}..{track[-1]}")
    print(f"  cell {cell_w:.2f}x{cell_h:.2f}px -> screen y={y}, x {x_start}..{x_end}")
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
