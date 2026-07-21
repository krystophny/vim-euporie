#!/usr/bin/env python3
"""Own a project kernel, an Euporie console, and Vim's control channel."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import secrets
import signal
import socketserver
import stat
import subprocess
import sys
import threading
import time
from typing import Any

LOG = logging.getLogger("vim-euporie")
MAX_REQUEST_BYTES = 16 * 1024 * 1024
# Euporie indents cell output past its "In [n]:" prompt.
PROMPT_GUTTER_COLUMNS = 8


def prepare_code(code: str, kind: str) -> str:
    """Convert a script cell into code suitable for a Jupyter execute request."""
    if kind == "markdown":
        return (
            "from IPython.display import Markdown as _VimEuporieMarkdown, display as _VimEuporieDisplay\n"
            f"_VimEuporieDisplay(_VimEuporieMarkdown({code!r}))"
        )
    return code


def pane_pixel_width() -> int:
    """Return the width available for output in this pane, in pixels."""
    pane = os.environ.get("TMUX_PANE", "")
    if not pane or not os.environ.get("TMUX"):
        return 0
    try:
        result = subprocess.run(
            [
                "tmux",
                "display-message",
                "-p",
                "-t",
                pane,
                "#{pane_width} #{client_cell_width}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        LOG.exception("could not measure the Euporie pane")
        return 0
    parts = result.stdout.split()
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return 0
    columns, cell_width = (int(part) for part in parts)
    columns -= PROMPT_GUTTER_COLUMNS
    if columns <= 0 or cell_width <= 0:
        return 0
    return columns * cell_width


def matplotlib_setup_code(target_width_px: int) -> str:
    """Return kernel code enabling inline figures at the pane's width.

    Euporie draws an image at its natural size: it occupies
    ``image_width_px // cell_width_px`` columns and is never scaled up. A
    Matplotlib figure is 6.4in wide at 100dpi, so on a HiDPI terminal, where a
    cell can be 20px wide, the default figure covers barely a third of the
    pane. Raise the figure DPI instead of its size in inches, which scales the
    text along with the axes rather than shrinking it.
    """
    lines = [
        "try:",
        "    get_ipython().run_line_magic('matplotlib', 'inline')",
    ]
    if target_width_px > 0:
        lines += [
            "    import matplotlib as _ve_mpl",
            "    _ve_inches = _ve_mpl.rcParams['figure.figsize'][0]",
            f"    _ve_dpi = {target_width_px} / _ve_inches",
            "    _ve_mpl.rcParams['figure.dpi'] = min(400.0, max(50.0, _ve_dpi))",
            "    del _ve_mpl, _ve_inches, _ve_dpi",
        ]
    lines += [
        "except (ImportError, ModuleNotFoundError):",
        "    pass",
    ]
    return "\n".join(lines)


class Runtime:
    """Mutable process and client state shared with the TCP handler."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.token = secrets.token_urlsafe(32)
        self.stop_event = threading.Event()
        self.clients: dict[str, tuple[float, int]] = {}
        self.clients_lock = threading.Lock()
        self.kernel_lock = threading.Lock()
        self.kernel_manager: Any = None
        self.kernel_client: Any = None
        self.kernel_log: Any = None
        self.transport_encryption = "disabled"
        self.console: subprocess.Popen[bytes] | None = None
        self.server: socketserver.TCPServer | None = None
        self.no_clients_since = time.monotonic()
        self.touch_client(args.owner_client, args.owner_pid)

    def touch_client(self, client: str, pid: int) -> None:
        if not client:
            return
        with self.clients_lock:
            self.clients[client] = (time.monotonic(), pid)
            self.no_clients_since = 0.0

    def detach_client(self, client: str) -> None:
        with self.clients_lock:
            self.clients.pop(client, None)
            if not self.clients:
                self.no_clients_since = time.monotonic()

    def prune_clients(self) -> int:
        cutoff = time.monotonic() - self.args.client_timeout
        with self.clients_lock:
            self.clients = {
                client: (seen, pid)
                for client, (seen, pid) in self.clients.items()
                if (process_is_alive(pid) if pid > 0 else seen >= cutoff)
            }
            if not self.clients and not self.no_clients_since:
                self.no_clients_since = time.monotonic()
            return len(self.clients)

    def execute(self, code: str, kind: str) -> str:
        code = prepare_code(code, kind)
        with self.kernel_lock:
            return self.kernel_client.execute(
                code, silent=False, store_history=True, allow_stdin=False
            )

    def interrupt(self) -> None:
        with self.kernel_lock:
            self.kernel_manager.interrupt_kernel()


class ControlHandler(socketserver.StreamRequestHandler):
    """Handle one authenticated, newline-delimited JSON request."""

    def handle(self) -> None:
        runtime: Runtime = self.server.runtime  # type: ignore[attr-defined]
        try:
            raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
            if len(raw) > MAX_REQUEST_BYTES:
                raise ValueError("request is too large")
            request = json.loads(raw)
            if not secrets.compare_digest(str(request.get("token", "")), runtime.token):
                raise PermissionError("invalid control token")
            action = request.get("action")
            client = str(request.get("client", ""))
            pid = int(request.get("pid", 0))

            if action in {"attach", "heartbeat", "execute", "interrupt", "status"}:
                runtime.touch_client(client, pid)

            if action == "execute":
                code = request.get("code")
                if not isinstance(code, str):
                    raise TypeError("code must be a string")
                reply = {
                    "ok": True,
                    "message_id": runtime.execute(
                        code, str(request.get("kind", "code"))
                    ),
                }
            elif action == "interrupt":
                runtime.interrupt()
                reply = {"ok": True}
            elif action == "detach":
                runtime.detach_client(client)
                reply = {"ok": True}
            elif action == "shutdown":
                runtime.stop_event.set()
                reply = {"ok": True}
            elif action in {"attach", "heartbeat", "status"}:
                reply = {
                    "ok": True,
                    "clients": runtime.prune_clients(),
                    "kernel_pid": runtime.kernel_manager.provisioner.pid,
                    "pane_id": os.environ.get("TMUX_PANE", ""),
                }
            else:
                raise ValueError(f"unknown action: {action!r}")
        except Exception as error:  # Control errors must be returned to Vim.
            LOG.exception("control request failed")
            reply = {"ok": False, "error": str(error)}
        self.wfile.write(json.dumps(reply, separators=(",", ":")).encode() + b"\n")


class ControlServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        super().__init__(("127.0.0.1", 0), ControlHandler)


def process_is_alive(pid: int) -> bool:
    """Return whether a local Vim process still owns a client registration."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def start_kernel(runtime: Runtime, connection_file: Path) -> None:
    from jupyter_client import KernelManager

    manager = KernelManager(kernel_name="python3", connection_file=str(connection_file))
    runtime.kernel_manager = manager
    # Force the Python kernel to use the interpreter selected by `uv run`. This
    # is what makes project dependencies importable without installing a global
    # kernelspec for every uv environment.
    manager.kernel_spec.argv = [
        sys.executable,
        "-m",
        "ipykernel_launcher",
        "-f",
        "{connection_file}",
    ]

    # jupyter_client 8.9 can provision CurveZMQ keys locally when the kernel
    # advertises support. This avoids the ipykernel plaintext-TCP warning and
    # protects all kernel traffic, even though it remains bound to localhost.
    encrypted = False
    try:
        import zmq

        if hasattr(manager, "transport_encryption") and zmq.has("curve"):
            manager.transport_encryption = "required"
            manager.kernel_spec.metadata = {
                **(manager.kernel_spec.metadata or {}),
                "supported_encryption": ["curve"],
            }
            encrypted = True
    except Exception:
        LOG.exception("CurveZMQ setup failed; falling back to localhost transport")

    kernel_log_path = runtime.args.state_file.with_suffix(".kernel.log")
    runtime.kernel_log = kernel_log_path.open("ab", buffering=0)
    manager.start_kernel(
        cwd=str(runtime.args.root),
        stdout=runtime.kernel_log,
        stderr=subprocess.STDOUT,
    )
    client_options = {}
    if encrypted:
        # jupyter_client 8.9 serializes these keys to text in manager.client(),
        # while the client traits correctly require raw CurveZMQ key bytes.
        client_options = {
            "curve_publickey": manager.curve_publickey,
            "curve_secretkey": manager.curve_secretkey,
        }
    client = manager.client(**client_options)
    runtime.kernel_client = client
    client.start_channels()
    client.wait_for_ready(timeout=runtime.args.kernel_timeout)
    runtime.transport_encryption = "curve" if encrypted else "disabled"
    # Match notebook behavior for `plt.show()` when matplotlib is part of the
    # uv project. A missing matplotlib is deliberately ignored.
    client.execute(
        matplotlib_setup_code(pane_pixel_width()),
        silent=True,
        store_history=False,
    )


def euporie_command(runtime: Runtime, connection_file: Path) -> list[str]:
    command = [
        sys.executable,
        str(Path(__file__).with_name("vim_euporie_console.py")),
        "--connection-file",
        str(connection_file),
        "--show-remote-inputs",
        "--show-remote-outputs",
        "--graphics",
        runtime.args.graphics,
        "--force-graphics",
    ]
    # tmux 3.4+ parses, stores, crops, and redraws Sixel itself. Wrapping Sixel
    # in passthrough would prevent tmux from managing the image as pane output.
    command.append(
        "--no-multiplexer-passthrough"
        if runtime.args.graphics == "sixel"
        else "--multiplexer-passthrough"
    )
    command.extend(
        [
            "--color-scheme",
            "dark",
            "--color-depth",
            "24",
        ]
    )
    command.extend(json.loads(runtime.args.euporie_args_json))
    return command


def tmux_client_tty() -> str:
    """Return the writable outer client TTY for this tmux pane.

    tmux intentionally drops passthrough sequences emitted by inactive panes.
    Kitty Unicode placeholders only need tmux for the placeholder text; their
    image uploads and virtual placements are position-independent and can be
    written directly to the attached terminal.
    """
    pane = os.environ.get("TMUX_PANE", "")
    if not pane or not os.environ.get("TMUX"):
        return ""
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-p", "-t", pane, "#{client_tty}"],
            check=True,
            capture_output=True,
            text=True,
        )
        tty = Path(result.stdout.strip())
        tty_stat = tty.stat()
    except (OSError, subprocess.SubprocessError):
        LOG.exception("could not resolve tmux client TTY")
        return ""
    if not tty.is_absolute() or not stat.S_ISCHR(tty_stat.st_mode):
        LOG.error("tmux returned a non-TTY client path: %s", tty)
        return ""
    if tty_stat.st_uid != os.getuid() or not os.access(tty, os.W_OK):
        LOG.error("tmux client TTY is not writable by this user: %s", tty)
        return ""
    return str(tty)


def euporie_environment(runtime: Runtime) -> dict[str, str]:
    """Build the console environment, including direct Kitty upload routing."""
    environment = os.environ.copy()
    environment.pop("VIM_EUPORIE_KITTY_TTY", None)
    if runtime.args.graphics == "kitty-unicode":
        if tty := tmux_client_tty():
            environment["VIM_EUPORIE_KITTY_TTY"] = tty
    return environment


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-file", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--owner-client", default="")
    parser.add_argument("--owner-pid", type=int, default=0)
    parser.add_argument("--idle-timeout", type=float, default=0.0)
    parser.add_argument("--client-timeout", type=float, default=45.0)
    parser.add_argument("--kernel-timeout", type=float, default=60.0)
    parser.add_argument("--graphics", default="sixel")
    parser.add_argument("--euporie-args-json", default="[]")
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.root = args.root.resolve()
    state_file = args.state_file.resolve()
    log_file = state_file.with_suffix(state_file.suffix + ".log")
    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    runtime = Runtime(args)
    connection_file = state_file.with_suffix(".kernel.json")

    def request_stop(_signum: int, _frame: Any) -> None:
        runtime.stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, request_stop)

    try:
        start_kernel(runtime, connection_file)
        runtime.server = ControlServer(runtime)
        server_thread = threading.Thread(
            target=runtime.server.serve_forever, name="vim-euporie-control", daemon=True
        )
        server_thread.start()

        runtime.console = subprocess.Popen(
            euporie_command(runtime, connection_file),
            cwd=args.root,
            env=euporie_environment(runtime),
            stderr=runtime.kernel_log,
        )
        # Give Euporie time to subscribe to IOPub before Vim can send the first
        # cell, otherwise very fast first executions can be invisible.
        time.sleep(0.75)
        if runtime.console.poll() is not None:
            raise RuntimeError("euporie-console exited during startup")

        atomic_write_json(
            state_file,
            {
                "version": 1,
                "port": runtime.server.server_address[1],
                "token": runtime.token,
                "pane_id": os.environ.get("TMUX_PANE", ""),
                "pid": os.getpid(),
                "kernel_pid": runtime.kernel_manager.provisioner.pid,
                "root": str(args.root),
                "connection_file": str(connection_file),
                "transport_encryption": runtime.transport_encryption,
            },
        )

        while not runtime.stop_event.wait(0.25):
            if runtime.console.poll() is not None:
                break
            client_count = runtime.prune_clients()
            if (
                client_count == 0
                and runtime.no_clients_since
                and time.monotonic() - runtime.no_clients_since >= args.idle_timeout
            ):
                LOG.info("stopping after the final Vim client detached")
                break
        return 0
    except Exception:
        LOG.exception("sidecar failed")
        print(f"vim-euporie failed; see {log_file}", file=sys.stderr)
        return 1
    finally:
        if runtime.server is not None:
            runtime.server.shutdown()
            runtime.server.server_close()
        if runtime.console is not None and runtime.console.poll() is None:
            runtime.console.terminate()
            try:
                runtime.console.wait(timeout=3)
            except subprocess.TimeoutExpired:
                runtime.console.kill()
        if runtime.kernel_client is not None:
            runtime.kernel_client.stop_channels()
        if runtime.kernel_manager is not None and runtime.kernel_manager.has_kernel:
            runtime.kernel_manager.shutdown_kernel(now=True)
        if runtime.kernel_log is not None:
            runtime.kernel_log.close()
        state_file.unlink(missing_ok=True)
        connection_file.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(run())
