import ast
import unittest
from pathlib import Path


class NonDestructiveScopeTests(unittest.TestCase):
    def test_runtime_source_contains_no_file_mutation_primitives(self):
        root = Path(__file__).resolve().parents[1] / "fileflow"
        prohibited_calls = {
            ("shutil", "move"),
            ("shutil", "copy"),
            ("shutil", "copy2"),
            ("shutil", "copyfile"),
            ("os", "rename"),
            ("os", "renames"),
            ("os", "replace"),
            ("os", "remove"),
            ("os", "rmdir"),
            ("os", "unlink"),
            ("Path", "rename"),
            ("Path", "replace"),
            ("Path", "unlink"),
            ("Path", "rmdir"),
            ("shutil", "rmtree"),
        }
        for source in root.rglob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    owner = None
                    if isinstance(node.func.value, ast.Name):
                        owner = node.func.value.id
                    if (owner, node.func.attr) in prohibited_calls:
                        self.fail(f"Prohibited file mutation call in {source}: {owner}.{node.func.attr}")

    def test_no_build_script_added_for_non_gui_core_milestone(self):
        self.assertFalse((Path(__file__).resolve().parents[1] / "build.py").exists())


if __name__ == "__main__":
    unittest.main()
