from __future__ import annotations

import json
import uuid
from dataclasses import replace
from pathlib import Path

from controller_config.workflows import LocalWorkflow, WorkflowError


WORKFLOW_PACKAGE_KIND = "boring-workflow"
WORKFLOW_PACKAGE_VERSION = 1


def export_workflow(path: Path, workflow: LocalWorkflow) -> None:
    workflow.validate()
    payload = {
        "kind": WORKFLOW_PACKAGE_KIND,
        "version": WORKFLOW_PACKAGE_VERSION,
        "workflow": workflow.as_mapping(),
    }
    try:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise WorkflowError(f"无法导出自动化：{exc}") from exc


def import_workflow(path: Path) -> LocalWorkflow:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"无法导入自动化：{exc}") from exc
    if not isinstance(value, dict) or set(value) != {"kind", "version", "workflow"}:
        raise WorkflowError("自动化包字段不完整或包含未知字段")
    if value.get("kind") != WORKFLOW_PACKAGE_KIND:
        raise WorkflowError("这不是 BORING 自动化包")
    if value.get("version") != WORKFLOW_PACKAGE_VERSION:
        raise WorkflowError("自动化包版本不受支持")
    workflow = LocalWorkflow.from_mapping(value.get("workflow"))
    requires_local_review = any(
        step.action_id in {"append_text_file", "open_target"}
        for step in workflow.steps
    )
    return replace(
        workflow,
        workflow_id=uuid.uuid4().hex,
        enabled=False,
        tested=False,
        review_required=requires_local_review,
    )
