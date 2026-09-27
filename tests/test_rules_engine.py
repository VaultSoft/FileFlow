import unittest

from fileflow.models import FileIdentity, IdentitySnapshot, MetadataSnapshot, SafetyDecision, ScannedItem, ScannedItemKind
from fileflow.rules import RuleEngine, default_categories, default_rules, test_filename_against_rules


def item(path, relative_path="Photo.JPG", size=12):
    return ScannedItem(
        path=path,
        relative_path=relative_path,
        kind=ScannedItemKind.FILE,
        safety=SafetyDecision.safe(path),
        identity=IdentitySnapshot(
            FileIdentity("VOL", path, "file", 1),
            MetadataSnapshot(path, size, 1, 1),
        ),
    )


class RulesEngineTests(unittest.TestCase):
    def test_default_rules_match_extensions_case_insensitively(self):
        engine = RuleEngine(default_rules(default_categories()))
        match = engine.match(item(r"C:\Users\Demo\Downloads\PHOTO.JPG"))
        self.assertIsNotNone(match)
        self.assertEqual("images", match.rule.category_id)

    def test_unknown_extensions_do_not_match_by_default(self):
        engine = RuleEngine(default_rules(default_categories()))
        self.assertIsNone(engine.match(item(r"C:\Users\Demo\Downloads\unknown.zzz", "unknown.zzz")))

    def test_order_uses_priority_sort_order_and_id(self):
        from fileflow.models import Rule

        rules = (
            Rule("b", "Second", "documents", "Documents", True, 10, 2, 1, extensions=(".txt",)),
            Rule("a", "First", "documents", "Documents", True, 10, 1, 1, extensions=(".txt",)),
            Rule("c", "Highest", "documents", "Documents", True, 20, 0, 1, extensions=(".txt",)),
        )
        match = RuleEngine(rules).match(item(r"C:\Users\Demo\Downloads\note.txt", "note.txt"))
        self.assertEqual("a", match.rule.id)

    def test_source_subfolder_is_root_relative_case_insensitive(self):
        from fileflow.models import Rule

        rule = Rule(
            "nested",
            "Nested Docs",
            "documents",
            "Documents",
            True,
            1,
            1,
            1,
            extensions=(".txt",),
            source_subfolder="Incoming",
        )
        engine = RuleEngine((rule,))
        self.assertIsNotNone(engine.match(item(r"C:\Root\Incoming\note.txt", r"Incoming\note.txt")))
        self.assertIsNone(engine.match(item(r"C:\Root\Other\note.txt", r"Other\note.txt")))

    def test_filename_tester_explains_builtin_match_without_filesystem_access(self):
        categories = default_categories()
        result = test_filename_against_rules("Holiday.PHOTO.JPG", default_rules(categories), categories)
        self.assertTrue(result.valid)
        self.assertTrue(result.matched)
        self.assertEqual("Images", result.category_name)
        self.assertEqual("Images", result.destination_folder)
        self.assertIn("would match Images", result.message)

    def test_filename_tester_leaves_unknown_extension_in_place(self):
        categories = default_categories()
        result = test_filename_against_rules("archive.unknown", default_rules(categories), categories)
        self.assertTrue(result.valid)
        self.assertFalse(result.matched)
        self.assertIn("stay in place", result.message)

    def test_filename_tester_rejects_paths_and_empty_values(self):
        categories = default_categories()
        rules = default_rules(categories)
        for value in ("", r"C:\Users\Demo\photo.jpg", r"folder\photo.jpg", ".."):
            with self.subTest(value=value):
                result = test_filename_against_rules(value, rules, categories)
                self.assertFalse(result.valid)
                self.assertFalse(result.matched)

    def test_filename_tester_skips_conditions_it_cannot_prove(self):
        from fileflow.models import Rule

        categories = default_categories()
        size_rule = Rule("large", "Large images", "images", "Images", True, 1, 1, 1, extensions=(".jpg",), min_size=1)
        result = test_filename_against_rules("photo.jpg", (size_rule,), categories)
        self.assertTrue(result.valid)
        self.assertFalse(result.matched)


if __name__ == "__main__":
    unittest.main()
