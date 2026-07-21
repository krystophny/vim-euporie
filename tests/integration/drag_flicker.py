#!/usr/bin/env python3
"""Measure flicker while dragging the slider, which is how the user drives it.

Updating a widget by running a later cell does not re-render it -- the output
area simply goes blank -- so cell-driven redraws cannot measure flicker at all.
The user moves the slider with the mouse, which keeps the widget live, so that
is what this reproduces: real button-press, motion and release events into the
VTE window, with the screen recorded throughout.
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
PANE_LEFT = 840  # the Euporie pane starts here in the 1600px window


def coloured(path: Path) -> np.ndarray:
    frame = np.asarray(Image.open(path).convert("RGB"), dtype=np.int16)
    return (frame.max(axis=2) - frame.min(axis=2)) > 40


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

        shot = st.screenshot("slider-reference")
        ink = coloured(shot)
        right = ink[:, PANE_LEFT:]
        rows = np.where(right.sum(axis=1) > 5)[0]
        if rows.size == 0:
            print("no figure in the Euporie pane; nothing to measure")
            return 1
        top, bottom = int(rows.min()), int(rows.max())
        print(f"figure rows {top}..{bottom}, ink {int(right[top:bottom + 1].sum())}")

        # The slider sits on the first row of the widget box, just above the
        # figure. Aim a little above the coloured region.
        slider_y = max(top - 12, 4)
        print(f"slider row approximately y={slider_y}")

        frames = harness.WORK / "drag-frames"
        frames.mkdir(exist_ok=True)
        recorder = subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "x11grab",
             "-framerate", str(FPS), "-video_size", "1600x1000",
             "-i", harness.DISPLAY, "-t", "26", str(frames / "f%04d.png")],
            env=st.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)

        # Press on the slider track and drag along it, as a person would.
        start_x, end_x = PANE_LEFT + 120, PANE_LEFT + 330
        subprocess.run(["xdotool", "mousemove", str(start_x), str(slider_y),
                        "mousedown", "1"], env=st.env, capture_output=True)
        for step in range(1, 21):
            x = start_x + (end_x - start_x) * step // 20
            subprocess.run(["xdotool", "mousemove", str(x), str(slider_y)],
                           env=st.env, capture_output=True)
            time.sleep(0.9)
        subprocess.run(["xdotool", "mouseup", "1"], env=st.env, capture_output=True)
        recorder.wait(timeout=60)

        shots = sorted(frames.glob("f*.png"))
        inks = np.array([int(coloured(p)[top:bottom + 1, PANE_LEFT:].sum())
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

        print(f"  frames                : {len(inks)}")
        print(f"  ink when drawn (p90)  : {drawn}")
        print(f"  distinct ink levels   : {len(set(inks.tolist()))}"
              "   (>3 means the figure really changed while dragging)")
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
