from __future__ import annotations

import ntpath
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .app_metadata import SAFETY_POLICY_VERSION
from .models import (
    Category,
    CategorySnapshot,
    ConflictStatus,
    ErrorCode,
    IdentitySnapshot,
    OperationIntent,
    PlanStatus,
    PlannedOperation,
    PlannedOperationStatus,
    PreviewPlan,
    RevalidationReason,
    RevalidationStatus,
    Rule,
    RuleSnapshot,
    SafetyDecision,
    SafetyReason,
    Severity,
    ScannedItem,
    ScannedItemKind,
    StructuredError,
)
from .rules import RuleEngine
from .safety import CloudClassifier, FileIdentityProvider, PathChainSafety, WindowsPathPolicy


class PreviewPlanner:
    def __init__(
        self,
        path_policy: WindowsPathPolicy,
        chain_safety: PathChainSafety,
        identity_provider: FileIdentityProvider,
    ):
        self.path_policy = path_policy
        self.chain_safety = chain_safety
        self.identity_provider = identity_provider

    def create_plan(
        self,
        *,
        profile_id: str,
        source_root: str,
        destination_root: str,
        items: tuple[ScannedItem, ...],
        rules: tuple[Rule, ...],
        categories: tuple[Category, ...],
        rule_set_version: int = 1,
        category_version: int = 1,
    ) -> PreviewPlan:
        root_decision = self.chain_safety.classify_chain(source_root)
        if not root_decision.allowed or root_decision.normalized_path is None:
            raise ValueError("Unsafe source root cannot be planned.")
        destination_decision = self.chain_safety.classify_chain(destination_root)
        if not destination_decision.allowed or destination_decision.normalized_path is None:
            raise ValueError("Unsafe destination root cannot be planned.")
        root_identity = self.identity_provider.snapshot(root_decision.normalized_path)
        if not root_identity.supported or root_identity.snapshot is None:
            raise ValueError("Source root identity is required.")
        destination_root_identity = self.identity_provider.snapshot(destination_decision.normalized_path)
        if not destination_root_identity.supported or destination_root_identity.snapshot is None:
            raise ValueError("Destination root identity is required.")

        category_by_id = {category.id: category for category in categories if category.enabled}
        engine = RuleEngine(rules)
        operations: list[PlannedOperation] = []
        preview_index = 0
        for item in items:
            if item.kind != ScannedItemKind.FILE:
                continue
            if not item.safety.allowed:
                operations.append(self._blocked_operation(item, root_decision.normalized_path, preview_index))
                preview_index += 1
                continue
            match = engine.match(item)
            if match is None:
                operations.append(
                    self._blocked_operation(
                        item,
                        root_decision.normalized_path,
                        preview_index,
                        reason="No enabled rule matched this file.",
                        safety=SafetyDecision.unsupported(
                            SafetyReason.NO_MATCHING_RULE,
                            "No enabled rule matched this file.",
                            normalized_path=item.path,
                        ),
                    )
                )
                preview_index += 1
                continue
            category = category_by_id.get(match.rule.category_id)
            if category is None:
                operations.append(
                    self._blocked_operation(
                        item,
                        root_decision.normalized_path,
                        preview_index,
                        reason="Matched category is unavailable.",
                    )
                )
                preview_index += 1
                continue
            destination = self.path_policy.join_under_root(
                destination_decision.normalized_path,
                match.rule.destination_folder,
                ntpath.basename(item.path),
            )
            if not destination.allowed or destination.normalized_path is None:
                operations.append(
                    self._blocked_operation(
                        item,
                        root_decision.normalized_path,
                        preview_index,
                        reason="Planned destination is unsafe.",
                        destination_path=destination.normalized_path,
                        safety=destination,
                    )
                )
                preview_index += 1
                continue
            destination_chain = self.chain_safety.classify_chain(destination.normalized_path, destination_decision.normalized_path)
            if not destination_chain.allowed:
                operations.append(
                    self._blocked_operation(
                        item,
                        root_decision.normalized_path,
                        preview_index,
                        reason="Planned destination path chain is unsafe.",
                        destination_path=destination.normalized_path,
                        safety=destination_chain,
                    )
                )
                preview_index += 1
                continue
            if Path(destination.normalized_path).exists():
                operations.append(
                    self._blocked_operation(
                        item,
                        root_decision.normalized_path,
                        preview_index,
                        reason="Planned destination already exists.",
                        destination_path=destination.normalized_path,
                        safety=SafetyDecision.block(
                            SafetyReason.DESTINATION_EXISTS,
                            "Planned destination already exists.",
                            normalized_path=destination.normalized_path,
                        ),
                    )
                )
                preview_index += 1
                continue
            operations.append(
                PlannedOperation(
                    id=str(uuid.uuid4()),
                    operation_type=OperationIntent.MOVE,
                    source_path=item.path,
                    destination_path=destination.normalized_path,
                    source_root=root_decision.normalized_path,
                    rule_snapshot=RuleSnapshot.from_rule(match.rule),
                    category_snapshot=CategorySnapshot.from_category(category),
                    reason=match.reason,
                    safety_status=PlannedOperationStatus.PLANNED,
                    conflict_status=ConflictStatus.NONE,
                    reversible=False,
                    source_identity=item.identity,
                    preview_index=preview_index,
                    structured_error=None,
                )
            )
            preview_index += 1

        status = PlanStatus.BLOCKED if any(op.safety_status != PlannedOperationStatus.PLANNED for op in operations) else PlanStatus.PLANNED
        return PreviewPlan(
            id=str(uuid.uuid4()),
            profile_id=profile_id,
            source_root=source_root,
            source_root_normalized=root_decision.normalized_path,
            source_root_identity=root_identity.snapshot,
            destination_root=destination_decision.normalized_path,
            destination_root_identity=destination_root_identity.snapshot,
            status=status,
            rule_set_version=rule_set_version,
            category_version=category_version,
            safety_policy_version=SAFETY_POLICY_VERSION,
            operations=tuple(operations),
            rule_snapshots=tuple(RuleSnapshot.from_rule(rule) for rule in rules),
            category_snapshots=tuple(CategorySnapshot.from_category(category) for category in categories),
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def with_destination_collisions(self, plan: PreviewPlan, existing_destinations: tuple[str, ...]) -> PreviewPlan:
        updated: list[PlannedOperation] = []
        for operation in plan.operations:
            if operation.destination_path and self.path_policy.has_case_collision(operation.destination_path, list(existing_destinations)):
                updated.append(
                    replace(
                        operation,
                        safety_status=PlannedOperationStatus.BLOCKED,
                        conflict_status=ConflictStatus.CASE_EQUIVALENT_COLLISION,
                        structured_error=SafetyDecision.block(
                            SafetyReason.CASE_EQUIVALENT_COLLISION,
                            "Destination collides under Windows case-insensitive semantics.",
                        ).error,
                    )
                )
            else:
                updated.append(operation)
        status = PlanStatus.BLOCKED if any(op.safety_status != PlannedOperationStatus.PLANNED for op in updated) else plan.status
        return replace(plan, operations=tuple(updated), status=status)

    def _blocked_operation(
        self,
        item: ScannedItem,
        source_root: str,
        preview_index: int,
        *,
        reason: str | None = None,
        destination_path: str | None = None,
        safety: SafetyDecision | None = None,
    ) -> PlannedOperation:
        decision = safety or item.safety
        status = PlannedOperationStatus.UNSUPPORTED if decision.status.value == "UNSUPPORTED" else PlannedOperationStatus.BLOCKED
        return PlannedOperation(
            id=str(uuid.uuid4()),
            operation_type=OperationIntent.MOVE,
            source_path=item.path,
            destination_path=destination_path,
            source_root=source_root,
            rule_snapshot=None,
            category_snapshot=None,
            reason=reason or (decision.error.message if decision.error else decision.reason.value),
            safety_status=status,
            conflict_status=ConflictStatus.NONE,
            reversible=False,
            source_identity=item.identity,
            preview_index=preview_index,
            structured_error=decision.error,
        )


class PlanRevalidator:
    def __init__(
        self,
        path_policy: WindowsPathPolicy,
        chain_safety: PathChainSafety,
        identity_provider: FileIdentityProvider,
        cloud_classifier: CloudClassifier | None = None,
    ):
        self.path_policy = path_policy
        self.chain_safety = chain_safety
        self.identity_provider = identity_provider
        self.cloud_classifier = cloud_classifier

    def revalidate(
        self,
        plan: PreviewPlan,
        current_rules: tuple[Rule, ...],
        current_categories: tuple[Category, ...],
        existing_destination_paths: tuple[str, ...] = (),
    ):
        from .models import RevalidationResult

        stale_reasons: list[RevalidationReason] = []
        blocked_reasons: list[RevalidationReason] = []
        errors: list[StructuredError] = []
        if tuple(RuleSnapshot.from_rule(rule) for rule in current_rules) != plan.rule_snapshots:
            stale_reasons.append(RevalidationReason.RULE_SNAPSHOT_CHANGED)
            errors.append(
                StructuredError(
                    ErrorCode.RULE_CHANGED,
                    Severity.BLOCKING,
                    "Rule snapshot changed after preview.",
                    {"plan_id": plan.id},
                )
            )
        if tuple(CategorySnapshot.from_category(category) for category in current_categories) != plan.category_snapshots:
            stale_reasons.append(RevalidationReason.CATEGORY_SNAPSHOT_CHANGED)
            errors.append(
                StructuredError(
                    ErrorCode.CATEGORY_CHANGED,
                    Severity.BLOCKING,
                    "Category snapshot changed after preview.",
                    {"plan_id": plan.id},
                )
            )
        if plan.safety_policy_version != SAFETY_POLICY_VERSION:
            stale_reasons.append(RevalidationReason.SAFETY_POLICY_CHANGED)
            errors.append(
                StructuredError(
                    ErrorCode.STALE_PLAN,
                    Severity.BLOCKING,
                    "Safety policy version changed after preview.",
                    {"preview": plan.safety_policy_version, "current": SAFETY_POLICY_VERSION},
                )
            )
        root_chain = self.chain_safety.classify_chain(plan.source_root_normalized)
        if not root_chain.allowed:
            blocked_reasons.append(RevalidationReason.REPARSE_STATE_CHANGED)
            if root_chain.error:
                errors.append(root_chain.error)
        root_identity = self.identity_provider.snapshot(plan.source_root_normalized)
        if not root_identity.supported or root_identity.snapshot != plan.source_root_identity:
            stale_reasons.append(RevalidationReason.ROOT_IDENTITY_CHANGED)
            if root_identity.error:
                errors.append(root_identity.error)
            else:
                errors.append(
                    StructuredError(
                        ErrorCode.ROOT_IDENTITY_CHANGED,
                        Severity.BLOCKING,
                        "Source root identity changed after preview.",
                        {"path": plan.source_root_normalized},
                    )
                )
        destination_root_chain = self.chain_safety.classify_chain(plan.destination_root)
        if not destination_root_chain.allowed:
            blocked_reasons.append(RevalidationReason.DESTINATION_PARENT_CHANGED)
            if destination_root_chain.error:
                errors.append(destination_root_chain.error)
        destination_root_identity = self.identity_provider.snapshot(plan.destination_root)
        if not destination_root_identity.supported or destination_root_identity.snapshot != plan.destination_root_identity:
            stale_reasons.append(RevalidationReason.DESTINATION_ROOT_IDENTITY_CHANGED)
            if destination_root_identity.error:
                errors.append(destination_root_identity.error)
            else:
                errors.append(
                    StructuredError(
                        ErrorCode.ROOT_IDENTITY_CHANGED,
                        Severity.BLOCKING,
                        "Destination root identity changed after preview.",
                        {"path": plan.destination_root},
                    )
                )
        for operation in plan.operations:
            source_chain = self.chain_safety.classify_chain(operation.source_path, plan.source_root_normalized)
            if not source_chain.allowed:
                blocked_reasons.append(RevalidationReason.REPARSE_STATE_CHANGED)
                if source_chain.error:
                    errors.append(source_chain.error)
            if operation.source_identity is not None:
                identity = self.identity_provider.snapshot(operation.source_path)
                if not identity.supported or identity.snapshot != operation.source_identity:
                    stale_reasons.append(RevalidationReason.SOURCE_IDENTITY_CHANGED)
                    if identity.error:
                        errors.append(identity.error)
                    else:
                        errors.append(
                            StructuredError(
                                ErrorCode.SOURCE_IDENTITY_CHANGED,
                                Severity.OPERATION_BLOCKING,
                                "Source identity changed after preview.",
                                {"path": operation.source_path, "operation_id": operation.id},
                            )
                        )
            if self.cloud_classifier is not None:
                cloud = self.cloud_classifier.classify(operation.source_path)
                if not cloud.safe:
                    blocked_reasons.append(RevalidationReason.CLOUD_CLASSIFICATION_CHANGED)
                    if cloud.error:
                        errors.append(cloud.error)
            self._revalidate_destination(
                plan,
                operation,
                existing_destination_paths,
                stale_reasons,
                blocked_reasons,
                errors,
            )
        if blocked_reasons:
            return RevalidationResult(RevalidationStatus.BLOCKED, tuple(dict.fromkeys(blocked_reasons + stale_reasons)), tuple(errors))
        if stale_reasons:
            return RevalidationResult(RevalidationStatus.STALE, tuple(dict.fromkeys(stale_reasons)), tuple(errors))
        return RevalidationResult(RevalidationStatus.VALID)

    def _revalidate_destination(
        self,
        plan: PreviewPlan,
        operation: PlannedOperation,
        existing_destination_paths: tuple[str, ...],
        stale_reasons: list[RevalidationReason],
        blocked_reasons: list[RevalidationReason],
        errors: list[StructuredError],
    ) -> None:
        if operation.destination_path is None:
            return
        destination_policy = self.path_policy.classify(operation.destination_path, plan.destination_root)
        if not destination_policy.allowed or destination_policy.normalized_path is None:
            reason = (
                RevalidationReason.DESTINATION_OUTSIDE_ROOT
                if destination_policy.reason == SafetyReason.PATH_ESCAPE
                else RevalidationReason.DESTINATION_PATH_POLICY_CHANGED
            )
            blocked_reasons.append(reason)
            errors.append(
                destination_policy.error
                or StructuredError(
                    ErrorCode.DESTINATION_PATH_POLICY_CHANGED,
                    Severity.OPERATION_BLOCKING,
                    "Planned destination is no longer supported by path policy.",
                    {"destination": operation.destination_path, "operation_id": operation.id},
                )
            )
            return

        destination_parent = ntpath.dirname(destination_policy.normalized_path)
        parent_chain = self.chain_safety.classify_chain(destination_parent, plan.destination_root)
        if not parent_chain.allowed:
            blocked_reasons.append(RevalidationReason.DESTINATION_PARENT_CHANGED)
            errors.append(
                parent_chain.error
                or StructuredError(
                    ErrorCode.DESTINATION_PARENT_CHANGED,
                    Severity.OPERATION_BLOCKING,
                    "Planned destination parent became unsafe.",
                    {"destination": destination_policy.normalized_path, "operation_id": operation.id},
                )
            )
            return

        if Path(destination_policy.normalized_path).exists():
            stale_reasons.append(RevalidationReason.DESTINATION_APPEARED)
            errors.append(
                StructuredError(
                    ErrorCode.DESTINATION_APPEARED,
                    Severity.OPERATION_BLOCKING,
                    "Planned destination appeared after preview.",
                    {"destination": destination_policy.normalized_path, "operation_id": operation.id},
                )
            )
            destination_chain = self.chain_safety.classify_chain(destination_policy.normalized_path, plan.destination_root)
            if not destination_chain.allowed:
                blocked_reasons.append(RevalidationReason.DESTINATION_REPARSE_CHANGED)
                if destination_chain.error:
                    errors.append(destination_chain.error)

        if self.path_policy.has_case_collision(destination_policy.normalized_path, list(existing_destination_paths)):
            stale_reasons.append(RevalidationReason.DESTINATION_COLLISION_CHANGED)
            errors.append(
                StructuredError(
                    ErrorCode.DESTINATION_COLLISION_CHANGED,
                    Severity.OPERATION_BLOCKING,
                    "A case-equivalent destination collision appeared after preview.",
                    {"destination": destination_policy.normalized_path, "operation_id": operation.id},
                )
            )
