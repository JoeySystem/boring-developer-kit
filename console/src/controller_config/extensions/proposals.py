from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Protocol

from PySide6.QtCore import QObject, Signal

from controller_config.drafts import DraftChange, LocalDraft
from controller_config.extensions.contracts import (
    SetMappingProposal,
    SetMappingProposalResult,
)
from controller_config.models import AppState, ScreenModel
from controller_config.protocol.contract import Contract
from controller_config.transactions import ConfigTransaction, ConfigTransactionState


class ProposalState(str, Enum):
    REVIEWABLE = "reviewable"
    BLOCKED = "blocked"
    STALE = "stale"
    APPROVED_TO_DRAFT = "approved_to_draft"
    REJECTED = "rejected"
    VERIFIED = "verified"
    FAILED = "failed"


_REMOVABLE_STATES = {
    ProposalState.REJECTED,
    ProposalState.STALE,
    ProposalState.VERIFIED,
    ProposalState.FAILED,
}


@dataclass(frozen=True)
class ProposalRecord:
    proposal: SetMappingProposal
    extension_name: str
    state: ProposalState
    message: str
    before_mapping: dict[str, object] | None
    after_mapping: dict[str, object]
    changes: tuple[DraftChange, ...]
    updated_at: str


class ProposalHost(Protocol):
    @property
    def model(self) -> ScreenModel: ...

    @property
    def draft(self) -> LocalDraft | None: ...

    @property
    def write_transaction(self) -> ConfigTransaction: ...

    def set_mapping(
        self,
        profile_id: int | None,
        control_id: str,
        short_name: str,
        action: dict,
    ) -> None: ...


class ExtensionProposalCoordinator(QObject):
    """Keep extension proposals outside device config until the user approves."""

    changed = Signal()

    def __init__(
        self,
        host: ProposalHost,
        *,
        contract: Contract,
        extension_is_active: Callable[[str], bool],
        extension_name: Callable[[str], str] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._host = host
        self._contract = contract
        self._extension_is_active = extension_is_active
        self._extension_name = extension_name or (lambda extension_id: extension_id)
        self._records: dict[str, ProposalRecord] = {}
        self._last_reconcile_signature: tuple[object, ...] | None = None

    @property
    def records(self) -> tuple[ProposalRecord, ...]:
        return tuple(self._records.values())

    def record(self, proposal_id: str) -> ProposalRecord:
        try:
            return self._records[proposal_id]
        except KeyError as exc:
            raise ValueError("未找到扩展配置提案") from exc

    def submit(self, proposal: SetMappingProposal) -> SetMappingProposalResult:
        if proposal.proposal_id in self._records:
            raise ValueError(f"提案 ID 已存在：{proposal.proposal_id}")
        state, message, before, after, changes = self._evaluate(proposal)
        self._records[proposal.proposal_id] = ProposalRecord(
            proposal=proposal,
            extension_name=self._extension_name(proposal.extension_id),
            state=state,
            message=message,
            before_mapping=before,
            after_mapping=after,
            changes=changes,
            updated_at=_now(),
        )
        self._last_reconcile_signature = None
        self.changed.emit()
        return SetMappingProposalResult(
            proposal_id=proposal.proposal_id,
            status=_public_status(state),
            message=message,
        )

    def approve(self, proposal_id: str) -> ProposalRecord:
        current = self.record(proposal_id)
        if current.state is not ProposalState.REVIEWABLE:
            raise ValueError("当前提案不可批准到本地草稿")
        state, message, before, after, changes = self._evaluate(current.proposal)
        if state is not ProposalState.REVIEWABLE:
            updated = replace(
                current,
                state=state,
                message=message,
                before_mapping=before,
                after_mapping=after,
                changes=changes,
                updated_at=_now(),
            )
            self._records[proposal_id] = updated
            self.changed.emit()
            return updated
        proposal = current.proposal
        short_name = ""
        if before is not None and isinstance(before.get("short_name"), str):
            short_name = str(before["short_name"])
        self._host.set_mapping(
            proposal.profile_id,
            proposal.control_id,
            short_name,
            _plain_json(proposal.action),
        )
        updated = replace(
            current,
            state=ProposalState.APPROVED_TO_DRAFT,
            message="已批准到本地草稿；设备尚未写入",
            updated_at=_now(),
        )
        self._records[proposal_id] = updated
        self.changed.emit()
        return updated

    def reject(self, proposal_id: str) -> ProposalRecord:
        current = self.record(proposal_id)
        if current.state not in {
            ProposalState.REVIEWABLE,
            ProposalState.BLOCKED,
            ProposalState.STALE,
        }:
            raise ValueError("当前提案不可拒绝")
        updated = replace(
            current,
            state=ProposalState.REJECTED,
            message="用户已拒绝这条扩展提案",
            updated_at=_now(),
        )
        self._records[proposal_id] = updated
        self.changed.emit()
        return updated

    def remove_record(self, proposal_id: str) -> None:
        current = self.record(proposal_id)
        if current.state not in _REMOVABLE_STATES:
            raise ValueError("只有已结束的扩展提案可以删除")
        self._records.pop(proposal_id)
        self._last_reconcile_signature = None
        self.changed.emit()

    def reconcile(self) -> None:
        signature = self._reconcile_signature()
        if signature == self._last_reconcile_signature:
            return
        self._last_reconcile_signature = signature
        changed = False
        for proposal_id, current in tuple(self._records.items()):
            if current.state is ProposalState.APPROVED_TO_DRAFT:
                draft = self._host.draft
                if (
                    self._host.write_transaction.state is ConfigTransactionState.ACTIVE
                    and draft is not None
                    and _mapping_action(draft, current.proposal)
                    == _plain_json(current.proposal.action)
                    and not draft.is_dirty
                ):
                    self._records[proposal_id] = replace(
                        current,
                        state=ProposalState.VERIFIED,
                        message="已经用户确认写入，并通过 GET_CONFIG 读回验证",
                        updated_at=_now(),
                    )
                    changed = True
                elif draft is None:
                    self._records[proposal_id] = replace(
                        current,
                        state=ProposalState.STALE,
                        message="本地草稿已不可用，已批准的变更未写入设备",
                        updated_at=_now(),
                    )
                    changed = True
                elif (
                    draft.serial != current.proposal.device_serial
                    or draft.base_generation != current.proposal.base_generation
                    or draft.base_digest != current.proposal.base_digest
                ):
                    self._records[proposal_id] = replace(
                        current,
                        state=ProposalState.STALE,
                        message="本地草稿的设备或基础版本已变化，请重新提交提案",
                        updated_at=_now(),
                    )
                    changed = True
                elif (
                    _mapping_action(draft, current.proposal)
                    != _plain_json(current.proposal.action)
                ):
                    self._records[proposal_id] = replace(
                        current,
                        state=ProposalState.STALE,
                        message="本地草稿已不再包含这条已批准的变更",
                        updated_at=_now(),
                    )
                    changed = True
            elif current.state in {
                ProposalState.REVIEWABLE,
                ProposalState.BLOCKED,
            }:
                state, message, before, after, changes = self._evaluate(
                    current.proposal
                )
                if (
                    state != current.state
                    or message != current.message
                    or before != current.before_mapping
                    or after != current.after_mapping
                    or changes != current.changes
                ):
                    self._records[proposal_id] = replace(
                        current,
                        state=state,
                        message=message,
                        before_mapping=before,
                        after_mapping=after,
                        changes=changes,
                        updated_at=_now(),
                    )
                    changed = True
        if changed:
            self.changed.emit()

    def _reconcile_signature(self) -> tuple[object, ...]:
        draft = self._host.draft
        snapshot = self._host.model.snapshot
        serial = None
        if snapshot is not None:
            value = snapshot.identity.get("serial")
            serial = value if isinstance(value, str) else None
        return (
            self._host.model.state,
            serial,
            getattr(draft, "base_generation", None),
            getattr(draft, "base_digest", None),
            bool(getattr(draft, "is_dirty", False)),
            self._host.write_transaction.state,
            tuple(
                (
                    proposal_id,
                    record.state,
                    self._extension_is_active(record.proposal.extension_id),
                    (
                        copy.deepcopy(_mapping_action(draft, record.proposal))
                        if draft is not None
                        and record.state is ProposalState.APPROVED_TO_DRAFT
                        else None
                    ),
                )
                for proposal_id, record in self._records.items()
            ),
        )

    def _evaluate(
        self, proposal: SetMappingProposal
    ) -> tuple[
        ProposalState,
        str,
        dict[str, object] | None,
        dict[str, object],
        tuple[DraftChange, ...],
    ]:
        after_mapping = {
            "control_id": proposal.control_id,
            "action": _plain_json(proposal.action),
        }
        if not self._extension_is_active(proposal.extension_id):
            return (
                ProposalState.BLOCKED,
                "扩展当前未启用或运行中断",
                None,
                after_mapping,
                (),
            )
        model = self._host.model
        draft = self._host.draft
        snapshot = model.snapshot
        if (
            snapshot is None
            or draft is None
            or model.state not in {AppState.READY, AppState.READ_ONLY}
        ):
            return ProposalState.BLOCKED, "目标设备当前不可用", None, after_mapping, ()
        if draft.serial != proposal.device_serial:
            return ProposalState.STALE, "提案所属设备已变化", None, after_mapping, ()
        if (
            draft.base_generation != proposal.base_generation
            or draft.base_digest != proposal.base_digest
        ):
            return ProposalState.STALE, "设备配置代际或摘要已变化", None, after_mapping, ()
        if self._host.write_transaction.state is not ConfigTransactionState.IDLE:
            return ProposalState.BLOCKED, "配置事务尚未回到空闲状态", None, after_mapping, ()
        if draft.is_dirty:
            return ProposalState.BLOCKED, "当前已有未保存本地草稿", None, after_mapping, ()
        try:
            before = copy.deepcopy(draft.mapping(proposal.profile_id, proposal.control_id))
            candidate = copy.deepcopy(draft.config)
            short_name = (
                str(before.get("short_name", "")) if isinstance(before, dict) else ""
            )
            after = {
                "control_id": proposal.control_id,
                "short_name": short_name,
                "action": _plain_json(proposal.action),
            }
            _replace_mapping(
                candidate,
                profile_id=proposal.profile_id,
                control_id=proposal.control_id,
                replacement=after,
            )
            errors = draft.validate_candidate(candidate, self._contract)
            if errors:
                raise ValueError(errors[0])
            changes = draft.preview_changes(candidate)
        except (ValueError, KeyError) as exc:
            return ProposalState.STALE, str(exc), None, after_mapping, ()
        return (
            ProposalState.REVIEWABLE,
            "提案已通过 Schema 与当前设备能力校验",
            before,
            after or after_mapping,
            changes,
        )


def _mapping_action(draft: LocalDraft, proposal: SetMappingProposal) -> dict | None:
    try:
        mapping = draft.mapping(proposal.profile_id, proposal.control_id)
    except ValueError:
        return None
    action = mapping.get("action") if isinstance(mapping, dict) else None
    return action if isinstance(action, dict) else None


def _plain_json(value):
    if isinstance(value, dict) or hasattr(value, "items"):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    return value


def _replace_mapping(
    config: dict,
    *,
    profile_id: int,
    control_id: str,
    replacement: dict[str, object],
) -> None:
    profiles = config.get("profiles")
    if not isinstance(profiles, list):
        raise ValueError("Profiles 不可用")
    profile = next(
        (
            item
            for item in profiles
            if isinstance(item, dict) and item.get("id") == profile_id
        ),
        None,
    )
    if profile is None:
        raise ValueError(f"Profile {profile_id} 不存在")
    mappings = profile.get("mappings")
    if not isinstance(mappings, list):
        raise ValueError("Profile mappings 不可用")
    for index, mapping in enumerate(mappings):
        if isinstance(mapping, dict) and mapping.get("control_id") == control_id:
            mappings[index] = copy.deepcopy(replacement)
            return
    mappings.append(copy.deepcopy(replacement))


def _public_status(state: ProposalState) -> str:
    return {
        ProposalState.REVIEWABLE: "pending",
        ProposalState.BLOCKED: "blocked",
        ProposalState.STALE: "stale",
        ProposalState.APPROVED_TO_DRAFT: "approved",
        ProposalState.REJECTED: "rejected",
        ProposalState.VERIFIED: "approved",
        ProposalState.FAILED: "blocked",
    }[state]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
