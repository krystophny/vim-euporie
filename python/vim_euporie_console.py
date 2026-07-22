#!/usr/bin/env python3
"""Launch Euporie without terminal probes which tmux can route to Vim."""

from __future__ import annotations

import base64
import errno
import logging
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


def synchronize_frames() -> None:
    """Bracket every flushed frame in DEC 2026 synchronized-update markers.

    Euporie erases a widget's old image and emits the new sixel in one frame,
    but the sixel is tens of kilobytes and tmux reads the pane in chunks, so
    the erase and the image can reach the outer terminal in separate updates:
    one visibly blank frame per slider tick. With the frame bracketed, tmux
    holds the pane's output until the closing marker and forwards erase and
    image atomically. Measured on a real drag: 13 blank frames without this,
    none with it. VIM_EUPORIE_NO_SYNC_FRAMES disables it for bisecting.
    """
    if os.environ.get("VIM_EUPORIE_NO_SYNC_FRAMES"):
        return
    try:
        from apptk.output.vt100 import Vt100_Output
    except ModuleNotFoundError:
        from euporie.core.io import Vt100_Output

    original_flush = Vt100_Output.flush

    def flush(self) -> None:  # noqa: ANN001
        buffer = getattr(self, "_buffer", None)
        if buffer:
            buffer.insert(0, "\x1b[?2026h")
            buffer.append("\x1b[?2026l")
        original_flush(self)

    Vt100_Output.flush = flush


def blend_diff_frames() -> None:
    """Paste ipympl diff frames onto the previous canvas, not instead of it.

    Euporie blends an incoming frame only when the widget state's
    ``_image_mode`` is ``diff`` at that instant. The mode update and the
    binary frame arrive as separate messages, so a diff processed against a
    stale mode replaces the whole canvas: the display collapses to the
    diff's own size, and every later mouse event misses it, which is why
    dragging on the plot stopped doing anything after the first press.
    Blend whenever the incoming image is smaller than the current canvas,
    whatever the mode field says.
    """
    try:
        from euporie.core.comm.ipympl import MPLCanvasModel
        from euporie.core.convert.datum import Datum
    except ImportError:
        return

    def set_data(self, display, value):  # noqa: ANN001, ANN202
        # ipympl sometimes sends raw RGB data to clear the canvas; only PNG
        # frames carry a drawable image.
        if not value.startswith(b"\x89PNG"):
            return
        datum = Datum(data=value, format="png")
        try:
            fg = datum.convert("pil")
            state = self.data.get("state", {})
            mode_is_diff = state.get("_image_mode") == "diff"
            previous = getattr(display, "datum", None)
            if previous is not None:
                bg = previous.convert("pil")
                if mode_is_diff or fg.size != bg.size:
                    bg = bg.convert("RGBA")
                    bg.paste(fg.convert("RGBA"), (0, 0), fg.convert("RGBA"))
                    # Keep the datum in PNG form: a PIL datum's only route to
                    # sixel is timg, a pure-Python encoder that takes tens of
                    # seconds per frame and freezes the whole application,
                    # while PNG converts through chafa in milliseconds.
                    import io

                    buffer = io.BytesIO()
                    bg.save(buffer, format="PNG")
                    datum = Datum(buffer.getvalue(), format="png")
        except Exception:  # noqa: BLE001
            # A frame we cannot blend is still better shown than dropped.
            pass
        display.datum = datum
        self.data["state"]["_data_url"] = (
            f"data:image/png;base64,{datum.convert('base64-png')}"
        )

    MPLCanvasModel.set_data = set_data


def quiet_shutdown_errors() -> None:
    """Stop a torn-down pane from ending in a CRITICAL.

    When tmux removes the pane the pty master goes with it, and prompt_toolkit
    raises OSError EIO from its final flush. Euporie only catches EOFError and
    KeyboardInterrupt around app.run(), so that reaches its excepthook, which
    logs CRITICAL; the stdout log handler then fails writing to the same dead
    terminal and prints "--- Logging error ---" on top. Nothing is actually
    wrong, so drop the one exception that only means "the terminal already
    went away".

    setup_logs() assigns sys.excepthook from the module global, so replacing
    the attribute here still takes effect when Euporie installs it later.
    """
    # A handler failing on a dead terminal should stay quiet too.
    logging.raiseExceptions = False

    try:
        from euporie.core import log as euporie_log
    except ImportError:
        return

    original = euporie_log.handle_exception

    def handle_exception(exc_type, exc_value, exc_traceback):  # noqa: ANN001, ANN202
        if issubclass(exc_type, OSError) and getattr(exc_value, "errno", None) == errno.EIO:
            return None
        return original(exc_type, exc_value, exc_traceback)

    euporie_log.handle_exception = handle_exception


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


def trace_graphics() -> None:
    """Log the graphics pipeline and mouse dispatch for diagnosis.

    Gated on VIM_EUPORIE_GRAPHICS_LOG, so ordinary runs are untouched. When
    the variable names a file, every stage from comm update to sixel emission
    is logged, along with incoming mouse bytes and which control each event
    is delivered to. This is the tool that located the VTE image-retirement
    bug and the spacer row swallowing slider clicks; keep it working.
    """
    log_path = os.environ.get("VIM_EUPORIE_GRAPHICS_LOG")
    if not log_path:
        return
    import functools
    import time as _time

    handle = open(log_path, "a", buffering=1)

    def note(msg: str) -> None:
        handle.write(f"{_time.monotonic():.3f} {msg}\n")

    def wrap(cls: object, name: str, fmt: Callable) -> None:
        original = getattr(cls, name)

        @functools.wraps(original)
        def wrapped(*args: object, **kwargs: object) -> object:
            try:
                note(fmt(*args, **kwargs))
            except Exception as exc:  # noqa: BLE001
                note(f"{cls.__name__}.{name} fmt error: {exc}")
            try:
                result = original(*args, **kwargs)
            except Exception as exc:
                note(f"{cls.__name__}.{name} RAISED {type(exc).__name__}: {exc}")
                raise
            return result

        setattr(cls, name, wrapped)

    from euporie.core.comm.ipywidgets import OutputModel
    from euporie.core.convert.datum import Datum
    from euporie.core.graphics import (
        GraphicProcessor,
        GraphicWindow,
        SixelGraphicControl,
    )
    from euporie.core.widgets.cell_outputs import CellOutput, CellOutputArea
    from euporie.core.widgets.display import DisplayControl

    wrap(OutputModel, "add_output",
         lambda self, json, own: f"OutputModel.add_output wait={self.clear_output_wait} "
         f"mimes={list(json.get('data', {}))}")
    wrap(OutputModel, "clear_output",
         lambda self, wait=False: f"OutputModel.clear_output wait={wait}")
    wrap(CellOutputArea, "reset",
         lambda self: f"CellOutputArea.reset id={id(self):#x}")
    wrap(CellOutputArea, "add_output",
         lambda self, output_json, refresh=True:
         f"CellOutputArea.add_output id={id(self):#x} "
         f"mimes={list(output_json.get('data', {}))}")
    wrap(CellOutput, "make_element",
         lambda self, mime: f"CellOutput.make_element mime={mime}")
    wrap(DisplayControl, "get_lines",
         lambda self, datum, width, height, fg, bg, wrap_lines=False:
         f"DisplayControl.get_lines ctrl={id(self):#x} datum={id(datum):#x} "
         f"fmt={datum.format} w={width} h={height}")
    wrap(GraphicProcessor, "get_graphic_float",
         lambda self, key: f"GraphicProcessor.get_graphic_float key={key} "
         f"sized={Datum.get_size(key) is not None}")
    wrap(SixelGraphicControl, "convert_data",
         lambda self, wp: f"SixelGraphicControl.convert_data ctrl={id(self):#x} "
         f"wp={wp.width}x{wp.height}")

    original_load = GraphicProcessor._load_positions

    def load_positions(self, content):  # noqa: ANN001, ANN202
        positions = original_load(self, content)
        if positions:
            note(f"GraphicProcessor.positions proc={id(self):#x} "
                 f"keys={list(positions)}")
        return positions

    GraphicProcessor._load_positions = load_positions

    original_get_position = GraphicProcessor._get_position

    def get_position(self, key, rows, cols):  # noqa: ANN001, ANN202
        inner = original_get_position(self, key, rows, cols)

        def logged(screen):  # noqa: ANN001, ANN202
            from prompt_toolkit.application import get_app as ptk_get_app

            if key not in self.positions:
                note(f"pos key={key}: key not in positions "
                     f"(have {list(self.positions)})")
                return inner(screen)
            window = None
            for _w in ptk_get_app().layout.find_all_windows():
                if _w.content == self.control:
                    window = _w
                    break
            if window is None:
                note(f"pos key={key}: control {id(self.control):#x} "
                     "has no window in layout")
            elif window not in screen.visible_windows:
                note(f"pos key={key}: window exists but not in visible_windows")
            return inner(screen)

        return logged

    GraphicProcessor._get_position = get_position

    original_wts = GraphicWindow.write_to_screen

    def write_to_screen(self, screen, mouse_handlers, write_position,  # noqa: ANN001
                        parent_style, erase_bg, z_index):  # noqa: ANN001, ANN202
        filter_value = self.filter()
        position = "?"
        if filter_value:
            try:
                wp = self.get_position(screen)
                position = f"{wp.width}x{wp.height}@{wp.xpos},{wp.ypos}"
            except Exception as exc:  # noqa: BLE001
                position = f"NotVisible({exc.__class__.__name__})"
        note(f"GraphicWindow.write ctrl={id(self.content):#x} "
             f"filter={filter_value} pos={position}")
        return original_wts(self, screen, mouse_handlers, write_position,
                            parent_style, erase_bg, z_index)

    GraphicWindow.write_to_screen = write_to_screen

    from prompt_toolkit.input import vt100_parser

    original_feed = vt100_parser.Vt100Parser.feed

    def feed(self, data):  # noqa: ANN001, ANN202
        if "\x1b[<" in data:
            note(f"input: mouse bytes {data!r:.120}")
        return original_feed(self, data)

    vt100_parser.Vt100Parser.feed = feed

    from prompt_toolkit.mouse_events import MouseEvent

    original_me_init = MouseEvent.__init__

    def me_init(self, position, event_type, button, modifiers):  # noqa: ANN001, ANN202
        where = "?"
        frame = sys._getframe(1)
        for _ in range(4):
            if frame is None:
                break
            owner = frame.f_locals.get("self")
            if owner is not None and hasattr(owner, "content"):
                where = f"window content={type(owner.content).__name__}"
                break
            if "__init__" not in frame.f_code.co_qualname:
                where = frame.f_code.co_qualname
                break
            frame = frame.f_back
        note(f"MouseEvent {event_type} at {position} <- {where}")
        return original_me_init(self, position, event_type, button, modifiers)

    MouseEvent.__init__ = me_init

    try:
        from euporie.core.widgets.forms import SliderControl
    except ImportError:
        SliderControl = None
    if SliderControl is not None:
        for name in ("mouse_handler_", "mouse_handler_handle",
                     "mouse_handler_track", "mouse_handler_arrow"):
            wrap(SliderControl, name,
                 (lambda n: lambda self, *a, **k: f"Slider.{n} args={a}")(name))

    try:
        from euporie.core.comm.ipympl import MPLCanvasModel
    except ImportError:
        MPLCanvasModel = None
    if MPLCanvasModel is not None:
        wrap(MPLCanvasModel, "mouse_handler",
             lambda self, mouse_event: f"MPLCanvas.mouse_handler {mouse_event}")
        wrap(MPLCanvasModel, "set_data",
             lambda self, display, value: f"MPLCanvas.set_data {len(value)} bytes")

    from euporie.core.convert import utils as convert_utils

    original_subproc = convert_utils.call_subproc

    async def call_subproc(data, cmd, *args, **kwargs):  # noqa: ANN001, ANN202
        note(f"call_subproc start {cmd[0]} ({len(data)} bytes in)")
        try:
            result = await original_subproc(data, cmd, *args, **kwargs)
        except Exception as exc:
            note(f"call_subproc {cmd[0]} RAISED {type(exc).__name__}: {exc}")
            raise
        note(f"call_subproc end   {cmd[0]} ({len(result)} bytes out)")
        return result

    convert_utils.call_subproc = call_subproc
    for module_name in list(sys.modules):
        module = sys.modules[module_name]
        if (module_name.startswith("euporie") and
                getattr(module, "call_subproc", None) is original_subproc):
            module.call_subproc = call_subproc

    try:
        from euporie.core.kernel.jupyter import JupyterKernel
    except ImportError:
        JupyterKernel = None
    if JupyterKernel is not None and hasattr(JupyterKernel, "kc_comm"):
        wrap(JupyterKernel, "kc_comm",
             lambda self, comm_id, data: f"kc_comm {data.get('method')} "
             f"{str(data.get('content', {}))[:60]}")
    note("trace_graphics installed")


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
        quiet_shutdown_errors()
        synchronize_frames()
        blend_diff_frames()
        trace_graphics()
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
        quiet_shutdown_errors()
        synchronize_frames()
        blend_diff_frames()
        trace_graphics()
        if os.environ.get('VIM_EUPORIE_FULL_SCREEN'):
            force_full_screen()
        ConsoleApp.launch()


if __name__ == "__main__":
    main()
