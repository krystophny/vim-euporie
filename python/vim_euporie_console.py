#!/usr/bin/env python3
"""Launch Euporie without terminal probes which tmux can route to Vim."""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
import threading
from typing import Callable


# Smallest possible PNG, used to check that an image converter really emits an
# image instead of exiting successfully with no output.
PROBE_PNG = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    b"IQAAAABJRU5ErkJggg=="
)

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


def direct_kitty_passthrough(
    original: Callable[..., str],
    write: Callable[[bytes], int],
    schedule_redraw: Callable[[], None] | None = None,
) -> Callable[..., str]:
    """Route raw Kitty graphics commands around tmux.

    Unicode image uploads and virtual placements do not carry a screen
    position. Writing them to the outer terminal is therefore safe, while the
    placeholder characters continue through tmux and determine where the
    image is drawn and how it moves through scrollback.
    """

    def routed(command: str, config: object | None = None) -> str:
        if command.startswith("\x1b_G"):
            payload = command.encode("utf-8")
            while payload:
                written = write(payload)
                if written <= 0:
                    raise OSError("short write to Kitty client TTY")
                payload = payload[written:]
            # tmux can leave the first incremental paint of placeholder cells
            # stale. A client redraw resolves them, just as the first mouse
            # wheel event does, without changing the scroll position.
            header = command.partition(";")[0]
            if schedule_redraw is not None and "U=1" in header.split(","):
                schedule_redraw()
            return ""
        return original(command, config)

    return routed


def install_direct_kitty_uploads() -> None:
    """Bypass tmux for Kitty commands when an outer client TTY is supplied."""
    tty = os.environ.get("VIM_EUPORIE_KITTY_TTY", "")
    if not tty:
        return

    from euporie.core import graphics

    descriptor = os.open(tty, os.O_WRONLY | os.O_NOCTTY)

    def schedule_redraw() -> None:
        def redraw() -> None:
            subprocess.run(
                ["tmux", "refresh-client", "-t", tty],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        # The virtual placement is created while prompt_toolkit is still
        # building its output. Redraw after the placeholder grid is flushed.
        for delay in (0.05, 0.20):
            timer = threading.Timer(delay, redraw)
            timer.daemon = True
            timer.start()

    graphics.passthrough = direct_kitty_passthrough(
        graphics.passthrough,
        lambda payload: os.write(descriptor, payload),
        schedule_redraw,
    )


def converter_emits_sixel(command: str) -> bool:
    """Return whether a converter command actually writes a Sixel image."""
    if shutil.which(command) is None:
        return False
    try:
        result = subprocess.run(
            [command, "-I"],
            input=PROBE_PNG,
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    # A Sixel image always starts with the Device Control String introducer.
    return result.stdout.startswith(b"\x1bP")


def drop_broken_sixel_converters() -> None:
    """Stop Euporie choosing an img2sixel which silently produces nothing.

    Euporie registers img2sixel ahead of ImageMagick, and its filter only
    checks that the command exists. Some libsixel builds, such as 1.10.5, exit
    zero while writing no output for every input. Euporie then reserves the
    image's cells and draws nothing, so figures appear as empty boxes while
    text, tracebacks and the kernel all keep working. Remove the converter when
    it fails a round trip so the next candidate, usually ImageMagick, is used.
    """
    if converter_emits_sixel("img2sixel"):
        return
    try:
        # Importing the format modules is what populates the registry.
        from euporie.core.convert import formats  # noqa: F401
        from euporie.core.convert.registry import converters
    except ImportError:
        return

    for source, entries in converters.get("sixel", {}).items():
        converters["sixel"][source] = [
            entry
            for entry in entries
            if "img2sixel" not in getattr(entry.func, "__name__", "")
        ]


def tmux_cell_size() -> tuple[int, int] | None:
    """Return the attached tmux client's true cell size in pixels."""
    if not os.environ.get("TMUX"):
        return None
    try:
        result = subprocess.run(
            [
                "tmux",
                "display-message",
                "-p",
                "#{client_cell_width} #{client_cell_height}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    parts = result.stdout.split()
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None
    width, height = (int(part) for part in parts)
    if width <= 0 or height <= 0:
        return None
    return width, height


def correct_cell_size() -> None:
    """Size figures from the cell size tmux reports for its client.

    Euporie derives the cell size from TIOCGWINSZ and refines it with CSI 14 t.
    Inside tmux both answer with tmux's own rounded geometry rather than the
    attached terminal's: an 9x16 cell is reported as 8x16, so every figure is
    rendered an eighth narrower than the space reserved for it. Where tmux
    reports no pixel size at all, Euporie falls back to a hardcoded 10x20 guess
    and figures come out smaller still, which is what a HiDPI screen usually
    hits. tmux does know the real size and publishes it as
    #{client_cell_width}, so prefer that.
    """
    cell = tmux_cell_size()
    if cell is None:
        return
    cell_width, cell_height = cell

    try:
        from euporie.core import io as euporie_io
    except ImportError:
        return

    original = euporie_io._tiocgwinsz

    def corrected() -> tuple[int, int, int, int]:
        rows, cols, _px, _py = original()
        return rows, cols, cols * cell_width, rows * cell_height

    euporie_io._tiocgwinsz = corrected

    # tmux answers CSI 14 t with the same rounded geometry, and that reply
    # overwrites the corrected size, so stop asking for it.
    try:
        from apptk.output.vt100 import Vt100_Output
    except ModuleNotFoundError:
        from euporie.core.io import Vt100_Output

    if hasattr(Vt100_Output, "get_pixel_size"):
        Vt100_Output.get_pixel_size = lambda _self: None


def force_full_screen() -> None:
    """Keep executed cells inside the live layout so widgets stay usable.

    euporie-console builds its Application with ``full_screen=False``, so
    prompt_toolkit prints each finished cell above the prompt and only the
    prompt itself remains a live layout. An ipywidget scrolled up that way is
    ordinary terminal text: it renders, but no mouse or key event can reach it,
    which is why sliders look right and do nothing. The flag is a plain
    ``setdefault`` in ``ConsoleApp.__init__``, so supplying it wins.
    """
    try:
        from euporie.console.app import ConsoleApp
    except ImportError:
        return

    original = ConsoleApp.__init__

    def full_screen_init(self, **kwargs: object) -> None:
        kwargs["full_screen"] = True
        original(self, **kwargs)

    ConsoleApp.__init__ = full_screen_init


def keep_slider_grab() -> None:
    """Let a slider keep the pointer once dragging, as a real widget would.

    Euporie captures the pointer on mouse-down and clamps events back into the
    slider, but SliderControl.mouse_handler_ only treats MOUSE_MOVE as a drag:
    any other event while the button is held falls through to the branch that
    clears `dragging` and releases the capture. Moving off the slider row then
    stops the drag instead of continuing it, unlike every graphical toolkit.
    Hold the grab until the button is actually released.
    """
    try:
        from prompt_toolkit.mouse_events import MouseEventType
        from euporie.core.widgets.forms import SliderControl
    except ImportError:
        return

    original = SliderControl.mouse_handler_

    def mouse_handler_(self, mouse_event, loc):  # noqa: ANN001, ANN202
        if self.dragging and mouse_event.event_type is not MouseEventType.MOUSE_UP:
            n_options = len(self.slider.options)
            pos = loc - 2 if self.show_arrows() else loc
            pos = max(0, min(self.track_len, pos))
            index = int((n_options - 0.5) * pos / self.track_len)
            if self.slider.vertical():
                index = n_options - index
            self.set_index(self.selected_handle, ab=index)
            return None
        return original(self, mouse_event, loc)

    SliderControl.mouse_handler_ = mouse_handler_


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


def main() -> None:
    sys.argv[0] = "euporie-console"
    from euporie.core import __main__ as euporie_main

    if hasattr(euporie_main, "available_apps"):
        # Euporie 2.x must install its prompt_toolkit layout patches before the
        # output module is imported.
        from euporie.core.layout import containers as _containers  # noqa: F401

        suppress_passthrough_queries()
        install_direct_kitty_uploads()
        drop_broken_sixel_converters()
        correct_cell_size()
        keep_slider_grab()
        if os.environ.get('VIM_EUPORIE_FULL_SCREEN'):
            force_full_screen()
        euporie_main.main("console")
    else:
        # Euporie 3.x moved the output layer to apptk and no longer uses the
        # entry-point discovery launcher.
        from euporie.console.app import ConsoleApp

        suppress_passthrough_queries()
        install_direct_kitty_uploads()
        drop_broken_sixel_converters()
        correct_cell_size()
        keep_slider_grab()
        if os.environ.get('VIM_EUPORIE_FULL_SCREEN'):
            force_full_screen()
        ConsoleApp.launch()


if __name__ == "__main__":
    main()
