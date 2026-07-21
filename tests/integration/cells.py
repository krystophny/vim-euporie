# /// script
# dependencies = ["matplotlib", "ipywidgets", "ipykernel"]
# ///

# %%
import io
import ipywidgets as widgets
import matplotlib.pyplot as plt
import numpy as np
from IPython.display import Image, display

fig = plt.figure(figsize=(5.0, 4.0), dpi=80)
ax = fig.add_subplot(projection="3d")
u = np.linspace(0, 2 * np.pi, 40)
v = np.linspace(0, np.pi, 30)
x = np.outer(np.cos(u), np.sin(v))
y = np.outer(np.sin(u), np.sin(v))
z = np.outer(np.ones_like(u), np.cos(v))
ax.plot_surface(x, y, z, rstride=2, cstride=2, edgecolor="#1f3b73", linewidth=0.2)
out = widgets.Output()

def redraw(azim):
    ax.view_init(elev=25, azim=azim)
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    with out:
        out.clear_output(wait=True)
        display(Image(data=buf.getvalue()))

display(out)
redraw(30)
