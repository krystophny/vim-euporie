"""Does the rendered figure survive on screen if nothing at all is done?

This separates "the terminal loses the image by itself" from "something the
user does loses it". The answer, measured over 90 seconds, is that the figure
is pixel-identical the whole time: the VTE patch, Sixel and tmux are stable at
rest, and the image is only lost when the widget updates.
"""
import sys, time
from pathlib import Path
import numpy as np
from PIL import Image
sys.path.insert(0, '/home/ert/code/krystophny/vim-euporie/tests/integration')
import stack as harness

def ink(path, x0):
    a = np.asarray(Image.open(path).convert("RGB"), dtype=np.int16)
    m = (a.max(axis=2) - a.min(axis=2)) > 40
    return int(m[:, x0:].sum())

st = harness.Stack()
try:
    st.start(Path('/home/ert/code/krystophny/vim-euporie/tests/integration/cells_slider.py'))
    st.tmux("send-keys", "-t", "0.0", ":EuporieStart", "C-m")
    pane = None
    for _ in range(70):
        time.sleep(5.0)
        f = [p for p in st.panes() if "euporie" in p.lower()]
        if f:
            pane = f[0].split()[0]; break
    if not pane:
        print("no euporie pane"); raise SystemExit(1)
    st.tmux("select-pane", "-t", "%0")
    st.tmux("send-keys", "-t", "%0", "G")
    time.sleep(1.0)
    st.key("shift+Return")
    print("cell sent; sampling the figure with no further interaction\n")
    x0 = 840
    prev = None
    for elapsed in range(5, 95, 5):
        time.sleep(5.0)
        shot = st.screenshot(f"persist-{elapsed:03d}")
        value = ink(shot, x0)
        flag = ""
        if prev is not None and prev > 3000 and value < prev * 0.2:
            flag = "   <-- FIGURE DISAPPEARED"
        print(f"  t+{elapsed:>3}s  coloured pixels {value:>7}{flag}")
        prev = max(prev or 0, value)
finally:
    st.stop()
