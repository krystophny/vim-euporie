#!/usr/bin/env python3
"""Launch Euporie without terminal probes which tmux can route to Vim."""

from __future__ import annotations

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
        euporie_main.main("console")
    else:
        # Euporie 3.x moved the output layer to apptk and no longer uses the
        # entry-point discovery launcher.
        from euporie.console.app import ConsoleApp

        suppress_passthrough_queries()
        ConsoleApp.launch()


if __name__ == "__main__":
    main()
