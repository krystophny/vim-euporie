#!/usr/bin/env python3
"""Launch Euporie without terminal probes which tmux can route to Vim."""

from __future__ import annotations

from functools import wraps
import sys


PASSTHROUGH_QUERY_METHODS = (
    # Euporie 2.x
    "get_colors",
    "get_kitty_graphics_status",
    "get_device_attributes",
    "get_iterm_graphics_status",
    # Euporie 3.x / apptk
    "ask_for_colors",
    "ask_for_kitty_graphics_status",
    "ask_for_device_attributes",
    "ask_for_iterm_graphics_status",
)

# A Kitty virtual placement can arrive through tmux immediately before its
# Unicode placeholder cells.  Ghostty may paint those cells before the new
# placement is ready, leaving a grid of placeholder glyphs until the next
# screen update.  Repaint once promptly and once after a conservative fallback
# delay; the wrapper below schedules these only for new placement dimensions.
GRAPHICS_REDRAW_DELAYS = (0.05, 0.20)


def suppress_passthrough_queries() -> None:
    """Disable only startup queries which escape an inactive tmux pane.

    Euporie's actual Kitty graphics transmissions still use tmux passthrough.
    The other capability probes remain enabled because tmux answers those
    itself instead of forwarding them to Ghostty's currently active pane.
    """
    try:
        from apptk.output.vt100 import Vt100_Output
    except ModuleNotFoundError:
        from euporie.core.io import Vt100_Output

    def ignore_query(_output: Vt100_Output) -> None:
        return None

    for method_name in PASSTHROUGH_QUERY_METHODS:
        if hasattr(Vt100_Output, method_name):
            setattr(Vt100_Output, method_name, ignore_query)


def force_graphics_redraw(app) -> None:
    """Force Prompt Toolkit to emit every visible cell on its next render.

    ``Application.invalidate`` alone can retain the previous screen raster and
    emit no terminal bytes for cells which compare equal.  Clearing only that
    raster preserves the real cursor position and terminal modes, unlike
    ``Renderer.reset``, while ensuring Kitty placeholder escapes are replayed.
    """
    renderer = getattr(app, "renderer", None)
    if renderer is not None:
        renderer._last_screen = None
    app.invalidate()


def patch_kitty_unicode_control(control_type: type) -> None:
    """Redraw after Euporie creates a Kitty Unicode virtual placement."""
    if getattr(control_type, "_vim_euporie_redraw_patched", False):
        return

    original = control_type.get_rendered_lines

    @wraps(original)
    def get_rendered_lines(control, *args, **kwargs):
        loaded_before = getattr(control, "loaded", False)
        placements_before = frozenset(getattr(control, "placements", ()))
        result = original(control, *args, **kwargs)
        placements_after = frozenset(getattr(control, "placements", ()))

        if not loaded_before or placements_after != placements_before:
            app = control.app
            loop = getattr(app, "loop", None)
            is_closed = getattr(loop, "is_closed", None)
            if loop is not None and not (is_closed and is_closed()):

                def redraw() -> None:
                    force_graphics_redraw(app)

                for delay in GRAPHICS_REDRAW_DELAYS:
                    loop.call_later(delay, redraw)

        return result

    control_type.get_rendered_lines = get_rendered_lines
    control_type._vim_euporie_redraw_patched = True


def patch_kitty_unicode_redraw() -> None:
    """Patch the Kitty Unicode control in Euporie 2.x or apptk."""
    try:
        from apptk.layout.graphics import KittyUnicodeGraphicControl
    except ModuleNotFoundError:
        from euporie.core.graphics import KittyUnicodeGraphicControl

    patch_kitty_unicode_control(KittyUnicodeGraphicControl)


def main() -> None:
    sys.argv[0] = "euporie-console"
    from euporie.core import __main__ as euporie_main

    if hasattr(euporie_main, "available_apps"):
        # Euporie 2.x must install its prompt_toolkit layout patches before the
        # output module is imported.
        from euporie.core.layout import containers as _containers  # noqa: F401

        suppress_passthrough_queries()
        patch_kitty_unicode_redraw()
        euporie_main.main("console")
    else:
        # Euporie 3.x moved the output layer to apptk and no longer uses the
        # entry-point discovery launcher.
        from euporie.console.app import ConsoleApp

        suppress_passthrough_queries()
        patch_kitty_unicode_redraw()
        ConsoleApp.launch()


if __name__ == "__main__":
    main()
