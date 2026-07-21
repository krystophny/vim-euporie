# /// script
# dependencies = ["matplotlib", "ipywidgets", "ipykernel", "ipympl"]
# ///

# %%
# The conventional Jupyter way to get an interactive figure: ipympl renders a
# live canvas and Euporie forwards mouse events to it, so the 3D axes can be
# dragged to rotate instead of being driven by hand-built sliders.
%matplotlib widget
import matplotlib.pyplot as plt
import numpy as np

fig = plt.figure(figsize=(5.0, 4.0), dpi=80)
ax = fig.add_subplot(projection="3d")
u = np.linspace(0, 2 * np.pi, 40)
v = np.linspace(0, np.pi, 30)
x = np.outer(np.cos(u), np.sin(v))
y = np.outer(np.sin(u), np.sin(v))
z = np.outer(np.ones_like(u), np.cos(v))
ax.plot_surface(x, y, z, rstride=2, cstride=2, cmap="plasma", edgecolor="none")
ax.set_title("drag to rotate")
fig.canvas
