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
            text = source.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(source))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    owner = None
                    if isinstance(node.func.value, ast.Name):
                        owner = node.func.value.id
                    if (owner, node.func.attr) in prohibited_calls:
                        self.fail(f"Prohibited file mutation call in {source}: {owner}.{node.func.attr}")
            for api_name in ("MoveFile", "MoveFileEx", "CopyFile", "DeleteFile", "RemoveDirectory", "CreateDirectory"):
                if api_name in text:
                    self.fail(f"Prohibited Win32 mutation API reference in {source}: {api_name}")

    def test_build_script_is_allowed_only_outside_runtime_package(self):
        self.assertTrue((Path(__file__).resolve().parents[1] / "build.py").exists())


if __name__ == "__main__":
    unittest.main()
