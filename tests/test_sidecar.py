import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "python" / "vim_euporie_sidecar.py"
SPEC = importlib.util.spec_from_file_location("vim_euporie_sidecar", MODULE_PATH)
assert SPEC and SPEC.loader
SIDECAR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SIDECAR)

CONSOLE_PATH = Path(__file__).parents[1] / "python" / "vim_euporie_console.py"
CONSOLE_SPEC = importlib.util.spec_from_file_location(
    "vim_euporie_console", CONSOLE_PATH
)
assert CONSOLE_SPEC and CONSOLE_SPEC.loader
CONSOLE = importlib.util.module_from_spec(CONSOLE_SPEC)
CONSOLE_SPEC.loader.exec_module(CONSOLE)


class PrepareCodeTests(unittest.TestCase):
    def test_code_is_unchanged(self):
        code = "x = 2\nx ** 3"
        self.assertEqual(SIDECAR.prepare_code(code, "code"), code)

    def test_markdown_becomes_rich_display(self):
        rendered = SIDECAR.prepare_code("# Title\n$x^2$", "markdown")
        self.assertIn("IPython.display", rendered)
        self.assertIn("'# Title\\n$x^2$'", rendered)


class SixelConverterTests(unittest.TestCase):
    """A converter which exits zero without output must not be selected."""

    def _run(self, stdout):
        return SimpleNamespace(stdout=stdout, stderr=b"", returncode=0)

    def test_converter_writing_nothing_is_rejected(self):
        with patch.object(CONSOLE.shutil, "which", return_value="/usr/bin/img2sixel"):
            with patch.object(CONSOLE.subprocess, "run", return_value=self._run(b"")):
                self.assertFalse(CONSOLE.converter_emits_sixel("img2sixel"))

    def test_converter_writing_a_sixel_is_accepted(self):
        with patch.object(CONSOLE.shutil, "which", return_value="/usr/bin/img2sixel"):
            with patch.object(
                CONSOLE.subprocess, "run", return_value=self._run(b"\x1bPq#0;2;0;0;0")
            ):
                self.assertTrue(CONSOLE.converter_emits_sixel("img2sixel"))

    def test_missing_converter_is_rejected(self):
        with patch.object(CONSOLE.shutil, "which", return_value=None):
            self.assertFalse(CONSOLE.converter_emits_sixel("img2sixel"))

    def test_only_the_broken_converter_is_dropped(self):
        def img2sixel_converter():
            return None

        img2sixel_converter.__name__ = "png_to_sixel_img2sixel"

        def imagemagick_converter():
            return None

        imagemagick_converter.__name__ = "imagemagick_convert"

        registry = {
            "sixel": {
                "png": [
                    SimpleNamespace(func=img2sixel_converter),
                    SimpleNamespace(func=imagemagick_converter),
                ]
            }
        }
        module = SimpleNamespace(converters=registry)
        with patch.object(CONSOLE, "converter_emits_sixel", return_value=False):
            with patch.dict(
                "sys.modules",
                {
                    "euporie.core.convert": SimpleNamespace(formats=None),
                    "euporie.core.convert.formats": SimpleNamespace(),
                    "euporie.core.convert.registry": module,
                },
            ):
                CONSOLE.drop_broken_sixel_converters()
        self.assertEqual(
            ["imagemagick_convert"],
            [entry.func.__name__ for entry in registry["sixel"]["png"]],
        )

    def test_a_working_converter_is_left_alone(self):
        registry = {"sixel": {"png": []}}
        module = SimpleNamespace(converters=registry)
        with patch.object(CONSOLE, "converter_emits_sixel", return_value=True):
            with patch.dict(
                "sys.modules", {"euporie.core.convert.registry": module}
            ):
                CONSOLE.drop_broken_sixel_converters()
        self.assertEqual({"sixel": {"png": []}}, registry)


class CellSizeTests(unittest.TestCase):
    """Figures are sized from the cell size tmux reports for its client."""

    def _tmux(self, stdout):
        return SimpleNamespace(stdout=stdout, stderr="", returncode=0)

    def test_client_cell_size_is_parsed(self):
        with patch.dict(CONSOLE.os.environ, {"TMUX": "/tmp/tmux-1000/default"}):
            with patch.object(
                CONSOLE.subprocess, "run", return_value=self._tmux("9 16\n")
            ):
                self.assertEqual((9, 16), CONSOLE.tmux_cell_size())

    def test_outside_tmux_there_is_nothing_to_correct(self):
        with patch.dict(CONSOLE.os.environ, {}, clear=True):
            self.assertIsNone(CONSOLE.tmux_cell_size())

    def test_unusable_reply_is_ignored(self):
        with patch.dict(CONSOLE.os.environ, {"TMUX": "/tmp/tmux-1000/default"}):
            for reply in ("", "0 0\n", "9\n", "wide tall\n"):
                with patch.object(
                    CONSOLE.subprocess, "run", return_value=self._tmux(reply)
                ):
                    self.assertIsNone(CONSOLE.tmux_cell_size(), reply)

    def test_pixel_size_is_recomputed_from_the_client_cell(self):
        io_module = SimpleNamespace(
            _tiocgwinsz=lambda: (27, 90, 720, 432), Vt100_Output=type("O", (), {})
        )
        with patch.object(CONSOLE, "tmux_cell_size", return_value=(9, 16)):
            with patch.dict(
                "sys.modules",
                {"euporie.core": SimpleNamespace(io=io_module),
                 "euporie.core.io": io_module},
            ):
                CONSOLE.correct_cell_size()
        # tmux reported an 8px wide cell; the client's real cell is 9px.
        self.assertEqual((27, 90, 810, 432), io_module._tiocgwinsz())


class LifecycleTests(unittest.TestCase):
    def test_new_owner_is_registered_before_control_socket_starts(self):
        runtime = SIDECAR.Runtime(
            SimpleNamespace(
                owner_client="vim-123-project",
                owner_pid=123,
                client_timeout=45.0,
            )
        )
        self.assertIn("vim-123-project", runtime.clients)
        self.assertEqual(0.0, runtime.no_clients_since)

    def test_idle_timeout_defaults_to_immediate_cleanup(self):
        args = SIDECAR.parse_args(["--state-file", "state", "--root", "."])
        self.assertEqual(0.0, args.idle_timeout)

    def test_dead_owner_is_pruned_without_waiting_for_heartbeat_timeout(self):
        runtime = SIDECAR.Runtime(
            SimpleNamespace(
                owner_client="vim-dead-project",
                owner_pid=2_147_483_647,
                client_timeout=45.0,
            )
        )
        self.assertEqual(0, runtime.prune_clients())
        self.assertGreater(runtime.no_clients_since, 0.0)


class ConsoleLaunchTests(unittest.TestCase):
    def test_only_passthrough_startup_queries_are_suppressed(self):
        self.assertEqual(
            {
                "get_colors",
                "get_kitty_graphics_status",
                "get_device_attributes",
                "get_iterm_graphics_status",
                "ask_for_colors",
                "ask_for_kitty_graphics_status",
                "ask_for_device_attributes",
                "ask_for_iterm_graphics_status",
            },
            set(CONSOLE.PASSTHROUGH_QUERY_METHODS),
        )

    def test_sidecar_launches_the_guarded_console_with_fixed_color_depth(self):
        runtime = SimpleNamespace(
            args=SimpleNamespace(
                graphics="kitty-unicode",
                euporie_args_json="[]",
            )
        )
        command = SIDECAR.euporie_command(runtime, Path("kernel.json"))
        self.assertEqual(SIDECAR.sys.executable, command[0])
        self.assertTrue(command[1].endswith("vim_euporie_console.py"))
        self.assertIn("--multiplexer-passthrough", command)
        self.assertEqual("24", command[command.index("--color-depth") + 1])

    def test_sixel_is_managed_by_tmux_instead_of_passed_through(self):
        runtime = SimpleNamespace(
            args=SimpleNamespace(
                graphics="sixel",
                euporie_args_json="[]",
            )
        )
        command = SIDECAR.euporie_command(runtime, Path("kernel.json"))
        self.assertIn("--no-multiplexer-passthrough", command)
        self.assertNotIn("--multiplexer-passthrough", command)

    def test_kitty_commands_are_written_directly(self):
        written = []
        redraws = []

        def original(command, config=None):
            return f"wrapped:{command}"

        routed = CONSOLE.direct_kitty_passthrough(
            original,
            lambda payload: written.append(payload) or len(payload),
            lambda: redraws.append(True),
        )
        command = "\x1b_Ga=t,q=2;YWJj\x1b\\"
        self.assertEqual("", routed(command))
        self.assertEqual([command.encode()], written)
        self.assertEqual([], redraws)

        placement = "\x1b_Ga=p,U=1,i=1,p=1,c=20,r=10,q=2\x1b\\"
        self.assertEqual("", routed(placement))
        self.assertEqual([True], redraws)
        self.assertEqual("wrapped:\x1b[31m", routed("\x1b[31m"))

    def test_only_kitty_unicode_mode_receives_direct_tty(self):
        runtime = SimpleNamespace(args=SimpleNamespace(graphics="kitty-unicode"))
        with patch.object(SIDECAR, "tmux_client_tty", return_value="/dev/pts/7"):
            environment = SIDECAR.euporie_environment(runtime)
        self.assertEqual("/dev/pts/7", environment["VIM_EUPORIE_KITTY_TTY"])

        runtime.args.graphics = "kitty"
        with patch.object(SIDECAR, "tmux_client_tty") as resolve:
            environment = SIDECAR.euporie_environment(runtime)
        resolve.assert_not_called()
        self.assertNotIn("VIM_EUPORIE_KITTY_TTY", environment)


if __name__ == "__main__":
    unittest.main()
