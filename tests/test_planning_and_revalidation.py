import unittest
from dataclasses import replace
from pathlib import Path
import tempfile

from fileflow.models import (
    FileIdentity,
    IdentitySnapshot,
    MetadataSnapshot,
    PlannedOperationStatus,
    RevalidationReason,
    RevalidationStatus,
    SafetyDecision,
    SafetyReason,
    ScannedItem,
    ScannedItemKind,
)
from fileflow.planner import PlanRevalidator, PreviewPlanner
from fileflow.rules import default_categories, default_rules
from fileflow.safety import FakeIdentityProvider, FakeReparseInspector, PathChainSafety, WindowsPathPolicy


def snapshot(path, file_id=None, file_type="file"):
    return IdentitySnapshot(
        FileIdentity("VOL", file_id or path.casefold(), file_type, 1),
        MetadataSnapshot(path, 100, 10, 5),
    )


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.policy = WindowsPathPolicy()
        self.chain = PathChainSafety(self.policy, FakeReparseInspector())

    def planner_with_identities(self, identities):
        return PreviewPlanner(self.policy, self.chain, FakeIdentityProvider(identities))

    def test_plan_freezes_exact_destination_and_snapshots(self):
        root = r"C:\FileFlowTest\Root"
        dest = r"C:\FileFlowTest\Root"
        source = r"C:\FileFlowTest\Root\Photo.JPG"
        identities = {root: snapshot(root, "root", "directory"), source: snapshot(source, "photo")}
        item = ScannedItem(source, "Photo.JPG", ScannedItemKind.FILE, SafetyDecision.safe(source), identities[source])
        categories = default_categories()
        plan = self.planner_with_identities(identities).create_plan(
            profile_id="default",
            source_root=root,
            destination_root=dest,
            items=(item,),
            rules=default_rules(categories),
            categories=categories,
        )
        self.assertEqual(1, len(plan.operations))
        self.assertEqual(PlannedOperationStatus.PLANNED, plan.operations[0].safety_status)
        self.assertEqual(r"C:\FileFlowTest\Root\Images\Photo.JPG", plan.operations[0].destination_path)
        self.assertEqual("images", plan.operations[0].category_snapshot.id)
        self.assertEqual("builtin.extension.images", plan.operations[0].rule_snapshot.id)

    def test_safety_failures_are_not_omitted_from_plan(self):
        root = r"C:\FileFlowTest\Root"
        blocked_path = r"C:\FileFlowTest\Root\bad.txt"
        identities = {root: snapshot(root, "root", "directory")}
        item = ScannedItem(
            blocked_path,
            "bad.txt",
            ScannedItemKind.FILE,
            SafetyDecision.block(SafetyReason.REPARSE_POINT, "blocked", normalized_path=blocked_path),
        )
        plan = self.planner_with_identities(identities).create_plan(
            profile_id="default",
            source_root=root,
            destination_root=root,
            items=(item,),
            rules=default_rules(default_categories()),
            categories=default_categories(),
        )
        self.assertEqual(1, len(plan.operations))
        self.assertEqual(PlannedOperationStatus.BLOCKED, plan.operations[0].safety_status)

    def test_unknown_extension_is_recorded_as_unsupported(self):
        root = r"C:\FileFlowTest\Root"
        source = r"C:\FileFlowTest\Root\file.zzz"
        identities = {root: snapshot(root, "root", "directory"), source: snapshot(source, "zzz")}
        item = ScannedItem(source, "file.zzz", ScannedItemKind.FILE, SafetyDecision.safe(source), identities[source])
        plan = self.planner_with_identities(identities).create_plan(
            profile_id="default",
            source_root=root,
            destination_root=root,
            items=(item,),
            rules=default_rules(default_categories()),
            categories=default_categories(),
        )
        self.assertEqual(PlannedOperationStatus.UNSUPPORTED, plan.operations[0].safety_status)

    def test_destination_case_collision_blocks_operation(self):
        root = r"C:\FileFlowTest\Root"
        source = r"C:\FileFlowTest\Root\Photo.JPG"
        identities = {root: snapshot(root, "root", "directory"), source: snapshot(source, "photo")}
        item = ScannedItem(source, "Photo.JPG", ScannedItemKind.FILE, SafetyDecision.safe(source), identities[source])
        planner = self.planner_with_identities(identities)
        plan = planner.create_plan(
            profile_id="default",
            source_root=root,
            destination_root=root,
            items=(item,),
            rules=default_rules(default_categories()),
            categories=default_categories(),
        )
        collided = planner.with_destination_collisions(plan, (r"C:\FileFlowTest\Root\Images\photo.jpg",))
        self.assertEqual(PlannedOperationStatus.BLOCKED, collided.operations[0].safety_status)

    def test_revalidation_detects_reparse_change(self):
        root = r"C:\FileFlowTest\Root"
        source = r"C:\FileFlowTest\Root\Photo.JPG"
        identities = {root: snapshot(root, "root", "directory"), source: snapshot(source, "photo")}
        item = ScannedItem(source, "Photo.JPG", ScannedItemKind.FILE, SafetyDecision.safe(source), identities[source])
        plan = self.planner_with_identities(identities).create_plan(
            profile_id="default",
            source_root=root,
            destination_root=root,
            items=(item,),
            rules=default_rules(default_categories()),
            categories=default_categories(),
        )
        unsafe_chain = PathChainSafety(self.policy, FakeReparseInspector({source}))
        result = PlanRevalidator(self.policy, unsafe_chain, FakeIdentityProvider(identities)).revalidate(
            plan,
            default_rules(default_categories()),
            default_categories(),
        )
        self.assertFalse(result.valid)
        self.assertEqual(RevalidationStatus.BLOCKED, result.status)
        self.assertIn(RevalidationReason.REPARSE_STATE_CHANGED, result.reasons)

    def test_hardlink_identity_preserves_link_count(self):
        root = r"C:\FileFlowTest\Root"
        source = r"C:\FileFlowTest\Root\linked.txt"
        hardlink_snapshot = IdentitySnapshot(FileIdentity("VOL", "same-file", "file", 2), MetadataSnapshot(source, 10, 1, 1))
        identities = {root: snapshot(root, "root", "directory"), source: hardlink_snapshot}
        item = ScannedItem(source, "linked.txt", ScannedItemKind.FILE, SafetyDecision.safe(source), hardlink_snapshot)
        plan = self.planner_with_identities(identities).create_plan(
            profile_id="default",
            source_root=root,
            destination_root=root,
            items=(item,),
            rules=default_rules(default_categories()),
            categories=default_categories(),
        )
        self.assertEqual(2, plan.operations[0].source_identity.identity.link_count)

    def test_destination_absent_at_preview_then_appears_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.policy.normalize(tmp)
            source = self.policy.join_under_root(root, "Photo.JPG").normalized_path
            identities = {root: snapshot(root, "root", "directory"), source: snapshot(source, "photo")}
            item = ScannedItem(source, "Photo.JPG", ScannedItemKind.FILE, SafetyDecision.safe(source), identities[source])
            plan = self.planner_with_identities(identities).create_plan(
                profile_id="default",
                source_root=root,
                destination_root=root,
                items=(item,),
                rules=default_rules(default_categories()),
                categories=default_categories(),
            )
            destination = Path(plan.operations[0].destination_path)
            destination.parent.mkdir()
            destination.write_text("collision", encoding="utf-8")

            result = PlanRevalidator(self.policy, self.chain, FakeIdentityProvider(identities)).revalidate(
                plan,
                default_rules(default_categories()),
                default_categories(),
            )
            self.assertEqual(RevalidationStatus.STALE, result.status)
            self.assertIn(RevalidationReason.DESTINATION_APPEARED, result.reasons)
            self.assertEqual(str(destination), plan.operations[0].destination_path)

    def test_destination_case_equivalent_collision_after_preview_is_stale(self):
        root = r"C:\FileFlowTest\Root"
        source = r"C:\FileFlowTest\Root\Photo.JPG"
        identities = {root: snapshot(root, "root", "directory"), source: snapshot(source, "photo")}
        item = ScannedItem(source, "Photo.JPG", ScannedItemKind.FILE, SafetyDecision.safe(source), identities[source])
        plan = self.planner_with_identities(identities).create_plan(
            profile_id="default",
            source_root=root,
            destination_root=root,
            items=(item,),
            rules=default_rules(default_categories()),
            categories=default_categories(),
        )
        result = PlanRevalidator(self.policy, self.chain, FakeIdentityProvider(identities)).revalidate(
            plan,
            default_rules(default_categories()),
            default_categories(),
            existing_destination_paths=(r"C:\FileFlowTest\Root\Images\photo.jpg",),
        )
        self.assertEqual(RevalidationStatus.STALE, result.status)
        self.assertIn(RevalidationReason.DESTINATION_COLLISION_CHANGED, result.reasons)

    def test_destination_parent_reparse_after_preview_is_blocked(self):
        root = r"C:\FileFlowTest\Root"
        source = r"C:\FileFlowTest\Root\Photo.JPG"
        parent = r"C:\FileFlowTest\Root\Images"
        identities = {root: snapshot(root, "root", "directory"), source: snapshot(source, "photo")}
        item = ScannedItem(source, "Photo.JPG", ScannedItemKind.FILE, SafetyDecision.safe(source), identities[source])
        plan = self.planner_with_identities(identities).create_plan(
            profile_id="default",
            source_root=root,
            destination_root=root,
            items=(item,),
            rules=default_rules(default_categories()),
            categories=default_categories(),
        )
        unsafe_chain = PathChainSafety(self.policy, FakeReparseInspector({parent}))
        result = PlanRevalidator(self.policy, unsafe_chain, FakeIdentityProvider(identities)).revalidate(
            plan,
            default_rules(default_categories()),
            default_categories(),
        )
        self.assertEqual(RevalidationStatus.BLOCKED, result.status)
        self.assertIn(RevalidationReason.DESTINATION_PARENT_CHANGED, result.reasons)

    def test_destination_escape_after_preview_is_blocked(self):
        root = r"C:\FileFlowTest\Root"
        source = r"C:\FileFlowTest\Root\Photo.JPG"
        identities = {root: snapshot(root, "root", "directory"), source: snapshot(source, "photo")}
        item = ScannedItem(source, "Photo.JPG", ScannedItemKind.FILE, SafetyDecision.safe(source), identities[source])
        plan = self.planner_with_identities(identities).create_plan(
            profile_id="default",
            source_root=root,
            destination_root=root,
            items=(item,),
            rules=default_rules(default_categories()),
            categories=default_categories(),
        )
        escaped = replace(plan.operations[0], destination_path=r"C:\OtherRoot\Photo.JPG")
        plan = replace(plan, operations=(escaped,))
        result = PlanRevalidator(self.policy, self.chain, FakeIdentityProvider(identities)).revalidate(
            plan,
            default_rules(default_categories()),
            default_categories(),
        )
        self.assertEqual(RevalidationStatus.BLOCKED, result.status)
        self.assertIn(RevalidationReason.DESTINATION_OUTSIDE_ROOT, result.reasons)

    def test_destination_unchanged_revalidates_valid(self):
        root = r"C:\FileFlowTest\Root"
        source = r"C:\FileFlowTest\Root\Photo.JPG"
        identities = {root: snapshot(root, "root", "directory"), source: snapshot(source, "photo")}
        item = ScannedItem(source, "Photo.JPG", ScannedItemKind.FILE, SafetyDecision.safe(source), identities[source])
        plan = self.planner_with_identities(identities).create_plan(
            profile_id="default",
            source_root=root,
            destination_root=root,
            items=(item,),
            rules=default_rules(default_categories()),
            categories=default_categories(),
        )
        result = PlanRevalidator(self.policy, self.chain, FakeIdentityProvider(identities)).revalidate(
            plan,
            default_rules(default_categories()),
            default_categories(),
        )
        self.assertEqual(RevalidationStatus.VALID, result.status)


if __name__ == "__main__":
    unittest.main()
