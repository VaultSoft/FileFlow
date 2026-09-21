from __future__ import annotations

from dataclasses import dataclass

from .models import (
    ErrorCode,
    IdentitySnapshot,
    PreviewPlan,
    RevalidationResult,
    SafetyDecision,
    SafetyReason,
    ScannedItem,
    Severity,
    StructuredError,
)
from .planner import PlanRevalidator, PreviewPlanner
from .rules import default_categories, default_rules
from .safety import (
    ConservativeCloudClassifier,
    FileIdentityProvider,
    PathChainSafety,
    WindowsFileIdentityProvider,
    WindowsPathPolicy,
    WindowsReparseInspector,
)
from .scanner import ImmediateChildScanner


@dataclass(frozen=True)
class FolderValidation:
    selected_path: str
    decision: SafetyDecision
    identity: IdentitySnapshot | None = None

    @property
    def allowed(self) -> bool:
        return self.decision.allowed and self.identity is not None


@dataclass(frozen=True)
class PreviewAnalysis:
    validation: FolderValidation
    scanned_items: tuple[ScannedItem, ...]
    plan: PreviewPlan | None


class PreviewWorkflowService:
    """Coordinates safe FileFlow analysis and frozen preview creation.

    This service owns no mutation primitive. It validates a selected root,
    scans immediate children, and asks the planner to build a frozen preview.
    """

    def __init__(
        self,
        *,
        path_policy: WindowsPathPolicy | None = None,
        chain_safety: PathChainSafety | None = None,
        identity_provider: FileIdentityProvider | None = None,
    ):
        self.path_policy = path_policy or WindowsPathPolicy()
        self.chain_safety = chain_safety or PathChainSafety(self.path_policy, WindowsReparseInspector())
        self.identity_provider = identity_provider or WindowsFileIdentityProvider()
        self.cloud_classifier = ConservativeCloudClassifier()
        self.categories = default_categories()
        self.rules = default_rules(self.categories)

    def validate_folder(self, selected_path: str) -> FolderValidation:
        try:
            decision = self.chain_safety.classify_chain(selected_path)
        except Exception as exc:
            decision = SafetyDecision.block(
                SafetyReason.REPARSE_INSPECTION_FAILED,
                "Could not prove the selected folder is safe to analyse.",
                code=ErrorCode.ROOT_UNSAFE,
                severity=Severity.BLOCKING,
                details={"path": str(selected_path), "error": str(exc)},
            )
            return FolderValidation(str(selected_path), decision)

        if not decision.allowed or decision.normalized_path is None:
            return FolderValidation(str(selected_path), decision)

        try:
            identity = self.identity_provider.snapshot(decision.normalized_path)
        except Exception as exc:
            decision = SafetyDecision.unsupported(
                SafetyReason.IDENTITY_UNAVAILABLE,
                "Could not establish selected folder identity.",
                normalized_path=decision.normalized_path,
                code=ErrorCode.ROOT_UNSAFE,
                details={"path": decision.normalized_path, "error": str(exc)},
            )
            return FolderValidation(str(selected_path), decision)

        if not identity.supported or identity.snapshot is None:
            decision = SafetyDecision.unsupported(
                SafetyReason.IDENTITY_UNAVAILABLE,
                "Reliable selected folder identity could not be established.",
                normalized_path=decision.normalized_path,
                code=ErrorCode.ROOT_UNSAFE,
                details=(identity.error.details if identity.error else {"path": decision.normalized_path}),
            )
            return FolderValidation(str(selected_path), decision)

        return FolderValidation(str(selected_path), decision, identity.snapshot)

    def analyse_folder(self, selected_path: str) -> PreviewAnalysis:
        validation = self.validate_folder(selected_path)
        if not validation.allowed or validation.decision.normalized_path is None:
            return PreviewAnalysis(validation, (), None)

        scanner = ImmediateChildScanner(
            self.path_policy,
            self.chain_safety,
            self.identity_provider,
            self.cloud_classifier,
        )
        scanned_items = scanner.scan(validation.decision.normalized_path)
        planner = PreviewPlanner(
            self.path_policy,
            self.chain_safety,
            self.identity_provider,
        )
        try:
            plan = planner.create_plan(
                profile_id="default",
                source_root=validation.decision.normalized_path,
                destination_root=validation.decision.normalized_path,
                items=scanned_items,
                rules=self.rules,
                categories=self.categories,
            )
        except Exception as exc:
            failed = SafetyDecision.block(
                SafetyReason.SCAN_FAILED,
                "Preview could not be generated safely.",
                normalized_path=validation.decision.normalized_path,
                code=ErrorCode.UNKNOWN_IO_ERROR,
                severity=Severity.BLOCKING,
                details={"path": validation.decision.normalized_path, "error": str(exc)},
            )
            return PreviewAnalysis(FolderValidation(selected_path, failed, validation.identity), scanned_items, None)

        return PreviewAnalysis(validation, scanned_items, plan)

    def revalidate_plan(self, plan: PreviewPlan) -> RevalidationResult:
        revalidator = PlanRevalidator(
            self.path_policy,
            self.chain_safety,
            self.identity_provider,
            self.cloud_classifier,
        )
        return revalidator.revalidate(plan, self.rules, self.categories)


def format_structured_error(error: StructuredError | None) -> str:
    if error is None:
        return ""
    return f"{error.code.value}: {error.message}"
