import ast
import unittest
from pathlib import Path


MUTATING_ATTRIBUTES = {
    ("shutil", "move"),
    ("shutil", "copy"),
    ("shutil", "copy2"),
    ("shutil", "copyfile"),
    ("shutil", "rmtree"),
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
}
WIN32_MUTATION_NAMES = ("MoveFile", "MoveFileEx", "CopyFile", "DeleteFile", "RemoveDirectory", "CreateDirectory")


def mutation_violations(source_text: str, *, filename: str = "<source>") -> list[str]:
    tree = ast.parse(source_text, filename=filename)
    module_aliases = {"os": "os", "shutil": "shutil"}
    path_aliases = {"Path"}
    direct_mutation_aliases: dict[str, tuple[str, str]] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_name = alias.name.split(".", 1)[0]
                local_name = alias.asname or root_name
                if root_name in ("os", "shutil"):
                    module_aliases[local_name] = root_name
                if alias.name == "pathlib.Path":
                    path_aliases.add(local_name)
        elif isinstance(node, ast.ImportFrom):
            if node.module in ("os", "shutil"):
                for alias in node.names:
                    local_name = alias.asname or alias.name
                    if (node.module, alias.name) in MUTATING_ATTRIBUTES:
                        direct_mutation_aliases[local_name] = (node.module, alias.name)
            elif node.module == "pathlib":
                for alias in node.names:
                    if alias.name == "Path":
                        path_aliases.add(alias.asname or alias.name)

    def mutation_reference(node):
        if not isinstance(node, ast.Attribute):
            return None
        owner = None
        if isinstance(node.value, ast.Name):
            owner_name = node.value.id
            if owner_name in module_aliases:
                owner = module_aliases[owner_name]
            elif owner_name in path_aliases:
                owner = "Path"
        elif isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
            if node.value.func.id in path_aliases:
                owner = "Path"
        mutation = (owner, node.attr)
        return mutation if mutation in MUTATING_ATTRIBUTES else None

    # Capture callable extraction such as ``f = os.replace`` before looking
    # for calls through the alias. The reference itself is also a violation.
    assigned_references: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        mutation = mutation_reference(value)
        if mutation is None:
            continue
        assigned_references.append(mutation)
        targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
        for target in targets:
            if isinstance(target, ast.Name):
                direct_mutation_aliases[target.id] = mutation

    violations: list[str] = [f"{owner}.{attr}" for owner, attr in assigned_references]
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            mutation = mutation_reference(node.func)
            if mutation is not None:
                owner, attr = mutation
                violations.append(f"{owner}.{node.func.attr}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in direct_mutation_aliases:
                owner, attr = direct_mutation_aliases[node.func.id]
                violations.append(f"{owner}.{attr}")

    for api_name in WIN32_MUTATION_NAMES:
        if api_name in source_text:
            violations.append(api_name)
    return violations


class NonDestructiveScopeTests(unittest.TestCase):
    def test_runtime_source_contains_only_approved_move_primitive(self):
        root = Path(__file__).resolve().parents[1] / "fileflow"
        approved_real_mutation = root / "operations" / "same_volume_move.py"
        for source in root.rglob("*.py"):
            text = source.read_text(encoding="utf-8")
            violations = mutation_violations(text, filename=str(source))
            if source == approved_real_mutation:
                violations = [violation for violation in violations if violation != "os.rename"]
            if violations:
                self.fail(f"Prohibited file mutation call in {source}: {violations}")

    def test_mutation_boundary_checker_catches_aliases_and_direct_imports(self):
        text = """
import os as operating_system
import shutil as shell_files
from os import remove as delete_file
from os import rename as move_file
from shutil import move as move_tree
from pathlib import Path as WinPath

operating_system.replace("a", "b")
shell_files.move("a", "b")
delete_file("a")
move_file("a", "b")
move_tree("a", "b")
WinPath("a").unlink()
"""

        violations = mutation_violations(text)

        self.assertIn("os.replace", violations)
        self.assertIn("shutil.move", violations)
        self.assertIn("os.remove", violations)
        self.assertIn("os.rename", violations)
        self.assertIn("Path.unlink", violations)

    def test_mutation_boundary_checker_catches_extracted_callable_aliases(self):
        text = """
import os
import shutil
from pathlib import Path

replace_file = os.replace
copy_file = shutil.copy2
unlink_path = Path.unlink
bound_unlink = Path("a").unlink

replace_file("a", "b")
copy_file("a", "b")
unlink_path(Path("a"))
bound_unlink()
"""

        violations = mutation_violations(text)

        self.assertIn("os.replace", violations)
        self.assertIn("shutil.copy2", violations)
        self.assertGreaterEqual(violations.count("Path.unlink"), 2)

    def test_build_script_is_allowed_only_outside_runtime_package(self):
        self.assertTrue((Path(__file__).resolve().parents[1] / "build.py").exists())


if __name__ == "__main__":
    unittest.main()
