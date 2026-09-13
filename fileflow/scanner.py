from __future__ import annotations

from pathlib import Path

from .models import (
    ScannedItem,
    ScannedItemKind,
    SafetyDecision,
    SafetyReason,
    SafetyStatus,
)
from .safety import CloudClassifier, FileIdentityProvider, PathChainSafety, WindowsPathPolicy


class ImmediateChildScanner:
    def __init__(
        self,
        path_policy: WindowsPathPolicy,
        chain_safety: PathChainSafety,
        identity_provider: FileIdentityProvider,
        cloud_classifier: CloudClassifier,
    ):
        self.path_policy = path_policy
        self.chain_safety = chain_safety
        self.identity_provider = identity_provider
        self.cloud_classifier = cloud_classifier

    def scan(self, source_root: str) -> tuple[ScannedItem, ...]:
        root_decision = self.chain_safety.classify_chain(source_root)
        if not root_decision.allowed or root_decision.normalized_path is None:
            return (
                ScannedItem(
                    path=str(source_root),
                    relative_path=".",
                    kind=ScannedItemKind.DIRECTORY,
                    safety=root_decision,
                ),
            )

        items: list[ScannedItem] = []
        for child in Path(root_decision.normalized_path).iterdir():
            child_path = str(child)
            child_decision = self.chain_safety.classify_chain(child_path, root_decision.normalized_path)
            relative_path = self.path_policy.relative_to_root(child_path, root_decision.normalized_path)
            if not child_decision.allowed:
                items.append(ScannedItem(child_path, relative_path, ScannedItemKind.FILE, child_decision))
                continue
            if child.is_dir():
                items.append(
                    ScannedItem(
                        child_path,
                        relative_path,
                        ScannedItemKind.DIRECTORY,
                        SafetyDecision.unsupported(
                            SafetyReason.DIRECTORY_SKIPPED,
                            "Directories are classified but not processed in Milestone 1.",
                            normalized_path=child_decision.normalized_path,
                        ),
                    )
                )
                continue
            if not child.is_file():
                items.append(
                    ScannedItem(
                        child_path,
                        relative_path,
                        ScannedItemKind.FILE,
                        SafetyDecision.unsupported(
                            SafetyReason.IDENTITY_UNAVAILABLE,
                            "Only regular immediate child files are supported.",
                            normalized_path=child_decision.normalized_path,
                        ),
                    )
                )
                continue
            cloud = self.cloud_classifier.classify(child_path)
            if not cloud.safe:
                items.append(
                    ScannedItem(
                        child_path,
                        relative_path,
                        ScannedItemKind.FILE,
                        SafetyDecision(SafetyStatus.UNSUPPORTED, cloud.reason, child_decision.normalized_path, cloud.error),
                    )
                )
                continue
            identity_result = self.identity_provider.snapshot(child_decision.normalized_path)
            if not identity_result.supported:
                items.append(
                    ScannedItem(
                        child_path,
                        relative_path,
                        ScannedItemKind.FILE,
                        SafetyDecision.unsupported(
                            SafetyReason.IDENTITY_UNAVAILABLE,
                            "Reliable file identity could not be established.",
                            normalized_path=child_decision.normalized_path,
                            details=(identity_result.error.details if identity_result.error else {}),
                        ),
                    )
                )
                continue
            items.append(
                ScannedItem(
                    path=child_decision.normalized_path,
                    relative_path=relative_path,
                    kind=ScannedItemKind.FILE,
                    safety=child_decision,
                    identity=identity_result.snapshot,
                )
            )
        return tuple(items)
