import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest


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

    def test_new_kitty_placement_schedules_redraws_once(self):
        scheduled = []
        invalidations = []

        class Loop:
            def is_closed(self):
                return False

            def call_later(self, delay, callback):
                scheduled.append((delay, callback))

        class Control:
            def __init__(self):
                self.loaded = False
                self.placements = set()
                self.app = SimpleNamespace(
                    loop=Loop(),
                    renderer=SimpleNamespace(_last_screen="previous screen"),
                    invalidate=lambda: invalidations.append(True),
                )

            def get_rendered_lines(self, width, height):
                self.loaded = True
                self.placements.add((width, height))
                return ["graphic"]

        CONSOLE.patch_kitty_unicode_control(Control)
        control = Control()

        self.assertEqual(["graphic"], control.get_rendered_lines(40, 20))
        self.assertEqual(
            list(CONSOLE.GRAPHICS_REDRAW_DELAYS),
            [delay for delay, _callback in scheduled],
        )

        for _delay, callback in scheduled:
            control.app.renderer._last_screen = "previous screen"
            callback()
            self.assertIsNone(control.app.renderer._last_screen)
        self.assertEqual([True, True], invalidations)

        control.get_rendered_lines(40, 20)
        self.assertEqual(2, len(scheduled))

    def test_resized_kitty_placement_schedules_redraws(self):
        scheduled = []

        class Loop:
            def call_later(self, delay, callback):
                scheduled.append((delay, callback))

        class Control:
            def __init__(self):
                self.loaded = True
                self.placements = {(40, 20)}
                self.app = SimpleNamespace(loop=Loop(), invalidate=lambda: None)

            def get_rendered_lines(self, width, height):
                self.placements.add((width, height))
                return []

        CONSOLE.patch_kitty_unicode_control(Control)
        Control().get_rendered_lines(50, 25)

        self.assertEqual(2, len(scheduled))

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


if __name__ == "__main__":
    unittest.main()
