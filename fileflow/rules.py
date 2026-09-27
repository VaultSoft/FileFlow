from __future__ import annotations

from dataclasses import dataclass
import ntpath

from .models import Category, Rule, RuleMatch, ScannedItem


@dataclass(frozen=True)
class FilenameRuleTest:
    valid: bool
    matched: bool
    filename: str
    category_name: str = ""
    destination_folder: str = ""
    rule_name: str = ""
    message: str = ""


def default_categories() -> tuple[Category, ...]:
    return (
        Category("images", "Images", "Images", (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"), True, 10, True, 1),
        Category("documents", "Documents", "Documents", (".pdf", ".doc", ".docx", ".txt", ".rtf", ".xlsx", ".csv"), True, 20, True, 1),
        Category("videos", "Videos", "Videos", (".mp4", ".mov", ".avi", ".mkv", ".webm"), True, 30, True, 1),
        Category("audio", "Audio", "Audio", (".mp3", ".wav", ".flac", ".aac", ".m4a"), True, 40, True, 1),
        Category("installers", "Installers", "Installers", (".exe", ".msi", ".msix"), True, 50, True, 1),
        Category("archives", "Archives", "Archives", (".zip", ".7z", ".rar", ".tar", ".gz"), True, 60, True, 1),
        Category("code", "Code", "Code", (".py", ".js", ".ts", ".html", ".css", ".json"), True, 70, True, 1),
        Category("other", "Other", "Other", (), False, 999, True, 1),
    )


def default_rules(categories: tuple[Category, ...] | None = None) -> tuple[Rule, ...]:
    records = categories or default_categories()
    rules: list[Rule] = []
    for category in records:
        if not category.enabled or not category.extensions:
            continue
        rules.append(
            Rule(
                id=f"builtin.extension.{category.id}",
                name=f"{category.name} extensions",
                category_id=category.id,
                destination_folder=category.destination_folder,
                enabled=True,
                priority=100,
                sort_order=category.sort_order,
                version=category.version,
                extensions=category.extensions,
            )
        )
    return tuple(rules)


class RuleEngine:
    def __init__(self, rules: tuple[Rule, ...]):
        self.rules = tuple(sorted((rule for rule in rules if rule.enabled), key=lambda rule: (rule.priority, rule.sort_order, rule.id)))

    def match(self, item: ScannedItem) -> RuleMatch | None:
        filename = ntpath.basename(item.path)
        extension = ntpath.splitext(filename)[1].casefold()
        filename_key = filename.casefold()
        relative_parent = ntpath.dirname(item.relative_path).replace("/", "\\").casefold()

        for rule in self.rules:
            if rule.extensions and extension not in {ext.casefold() for ext in rule.extensions}:
                continue
            if rule.filename_contains and rule.filename_contains.casefold() not in filename_key:
                continue
            if rule.filename_startswith and not filename_key.startswith(rule.filename_startswith.casefold()):
                continue
            if rule.filename_endswith and not filename_key.endswith(rule.filename_endswith.casefold()):
                continue
            if item.identity is not None:
                size = item.identity.metadata.size
                if rule.min_size is not None and size < rule.min_size:
                    continue
                if rule.max_size is not None and size > rule.max_size:
                    continue
            if rule.source_subfolder:
                expected = rule.source_subfolder.replace("/", "\\").strip("\\").casefold()
                if relative_parent != expected:
                    continue
            return RuleMatch(rule, self._reason(rule, filename, extension))
        return None

    def _reason(self, rule: Rule, filename: str, extension: str) -> str:
        if rule.extensions:
            return f"Extension {extension} matched {rule.name}"
        return f"{filename} matched {rule.name}"


def test_filename_against_rules(
    filename: str,
    rules: tuple[Rule, ...],
    categories: tuple[Category, ...],
) -> FilenameRuleTest:
    """Explain filename-only matching without reading or changing the filesystem."""

    candidate = filename.strip()
    if not candidate:
        return FilenameRuleTest(False, False, candidate, message="Enter a filename to test.")
    if candidate in (".", "..") or ntpath.basename(candidate) != candidate:
        return FilenameRuleTest(
            False,
            False,
            candidate,
            message="Enter a filename only, without a folder path.",
        )

    extension = ntpath.splitext(candidate)[1].casefold()
    filename_key = candidate.casefold()
    ordered = sorted((rule for rule in rules if rule.enabled), key=lambda rule: (rule.priority, rule.sort_order, rule.id))
    for rule in ordered:
        # A filename-only test cannot safely evaluate metadata or folder conditions.
        if rule.min_size is not None or rule.max_size is not None or rule.source_subfolder:
            continue
        if rule.extensions and extension not in {item.casefold() for item in rule.extensions}:
            continue
        if rule.filename_contains and rule.filename_contains.casefold() not in filename_key:
            continue
        if rule.filename_startswith and not filename_key.startswith(rule.filename_startswith.casefold()):
            continue
        if rule.filename_endswith and not filename_key.endswith(rule.filename_endswith.casefold()):
            continue
        category = next((item for item in categories if item.id == rule.category_id), None)
        category_name = category.name if category is not None else rule.category_id
        return FilenameRuleTest(
            True,
            True,
            candidate,
            category_name,
            rule.destination_folder,
            rule.name,
            f"{candidate} would match {category_name} and preview a move into {rule.destination_folder}.",
        )

    return FilenameRuleTest(
        True,
        False,
        candidate,
        message=f"{candidate} does not match an active built-in rule and would stay in place.",
    )
