#!/usr/bin/env python3
"""Launch Euporie without terminal probes which tmux can route to Vim."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from typing import Callable


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
        euporie_main.main("console")
    else:
        # Euporie 3.x moved the output layer to apptk and no longer uses the
        # entry-point discovery launcher.
        from euporie.console.app import ConsoleApp

        suppress_passthrough_queries()
        install_direct_kitty_uploads()
        ConsoleApp.launch()


if __name__ == "__main__":
    main()
