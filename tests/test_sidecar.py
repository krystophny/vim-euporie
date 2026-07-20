import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).parents[1] / "python" / "vim_euporie_sidecar.py"
SPEC = importlib.util.spec_from_file_location("vim_euporie_sidecar", MODULE_PATH)
assert SPEC and SPEC.loader
SIDECAR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SIDECAR)


class PrepareCodeTests(unittest.TestCase):
    def test_code_is_unchanged(self):
        code = "x = 2\nx ** 3"
        self.assertEqual(SIDECAR.prepare_code(code, "code"), code)

    def test_markdown_becomes_rich_display(self):
        rendered = SIDECAR.prepare_code("# Title\n$x^2$", "markdown")
        self.assertIn("IPython.display", rendered)
        self.assertIn("'# Title\\n$x^2$'", rendered)


if __name__ == "__main__":
    unittest.main()
