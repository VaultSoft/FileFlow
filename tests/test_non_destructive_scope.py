import ast
import unittest
from pathlib import Path


class NonDestructiveScopeTests(unittest.TestCase):
    def test_runtime_source_contains_only_approved_move_primitive(self):
        root = Path(__file__).resolve().parents[1] / "fileflow"
        approved_real_mutation = root / "operations" / "same_volume_move.py"
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
                        if source == approved_real_mutation and (owner, node.func.attr) == ("os", "rename"):
                            continue
                        self.fail(f"Prohibited file mutation call in {source}: {owner}.{node.func.attr}")
            for api_name in ("MoveFile", "MoveFileEx", "CopyFile", "DeleteFile", "RemoveDirectory", "CreateDirectory"):
                if api_name in text:
                    self.fail(f"Prohibited Win32 mutation API reference in {source}: {api_name}")

    def test_build_script_is_allowed_only_outside_runtime_package(self):
        self.assertTrue((Path(__file__).resolve().parents[1] / "build.py").exists())


if __name__ == "__main__":
    unittest.main()
