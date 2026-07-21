#!/usr/bin/env python3
"""Measure flicker the way a person sees it: blank frames on the screen.

Byte-level metrics kept misleading me, so this records the actual X display
while the figure is redrawn and counts frames where the image area goes empty.
A blank frame between two drawn frames is precisely what reads as a flicker.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

import stack as harness

FPS = 15
RECORD_SECONDS = 34


def ink_map(path: Path) -> np.ndarray:
    """Coloured pixels, as a boolean image.

    Terminal text is white or grey on black, so it has almost no saturation.
    The plotted surface and its edges are coloured, which isolates the figure
    from the surrounding console text far more reliably than brightness does.
    """
    frame = np.asarray(Image.open(path).convert("RGB"), dtype=np.int16)
    return (frame.max(axis=2) - frame.min(axis=2)) > 40


def main() -> int:
    cells = Path(sys.argv[1])
    work = harness.WORK
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

        # Draw the first figure with a real Shift+Enter.
        st.tmux("select-pane", "-t", "%0")
        st.tmux("send-keys", "-t", "%0", "G")
        time.sleep(1.0)
        st.key("shift+Return")
        time.sleep(30.0)

        shot = st.screenshot("ink-reference")
        ink = ink_map(shot)
        rows = np.where(ink.any(axis=1))[0]
        cols = np.where(ink.any(axis=0))[0]
        if rows.size == 0:
            print("nothing drawn; cannot locate the figure")
            return 1
        # The figure sits in the right-hand pane; restrict to its columns so
        # the Vim pane's text cannot be mistaken for the image.
        mid = ink.shape[1] // 2
        right = ink[:, mid:]
        rrows = np.where(right.any(axis=1))[0]
        if rrows.size == 0:
            print("no figure found in the Euporie pane")
            return 1
        top, bottom = int(rrows.min()), int(rrows.max())
        box = (mid, top, ink.shape[1], bottom + 1)
        baseline = int(right[top : bottom + 1].sum())
        print(f"figure region rows {top}..{bottom}, reference ink {baseline}")
        if baseline < 1000:
            print("the figure looks empty; refusing to measure noise")
            return 1

        frames_dir = work / "frames"
        frames_dir.mkdir(exist_ok=True)
        recorder = subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-y",
             "-f", "x11grab", "-framerate", str(FPS),
             "-video_size", "1600x1000", "-i", harness.DISPLAY,
             "-t", str(RECORD_SECONDS),
             str(frames_dir / "f%04d.png")],
            env=st.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)

        for angle in range(30, 30 + 10 * 15, 15):
            st.tmux("send-keys", "-t", pane, f"redraw({angle})", "C-m")
            time.sleep(2.6)
        recorder.wait(timeout=60)

        shots = sorted(frames_dir.glob("f*.png"))
        print(f"analysed {len(shots)} recorded frames at {FPS} fps")
        inks = []
        for path in shots:
            coloured = ink_map(path)
            inks.append(int(coloured[box[1] : box[3], box[0] : box[2]].sum()))

        inks = np.array(inks)
        drawn = np.median(inks[inks > 0]) if (inks > 0).any() else 0
        blank = inks < drawn * 0.15
        # Only blanks with drawn frames on both sides are flicker; leading and
        # trailing blanks are just the recording starting or ending.
        flicker = 0
        runs = []
        run = 0
        for index, is_blank in enumerate(blank):
            if is_blank:
                run += 1
            else:
                if run and index > run:
                    flicker += run
                    runs.append(run)
                run = 0

        print(f"  median ink when drawn : {drawn:.0f}")
        print(f"  frames recorded       : {len(inks)}")
        print(f"  blank frames mid-run  : {flicker}")
        print(f"  blank runs            : {runs}")
        if runs:
            print(f"  longest blank         : {max(runs)} frames "
                  f"= {max(runs) / FPS * 1000:.0f} ms")
        print(f"  VERDICT: {'FLICKERS' if flicker else 'no blank frames seen'}")
        return 0
    finally:
        st.stop()


if __name__ == "__main__":
    raise SystemExit(main())
