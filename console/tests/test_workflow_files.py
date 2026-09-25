from __future__ import annotations

import json

import pytest

from controller_config.workflow_files import export_workflow, import_workflow
from controller_config.workflows import (
    LocalWorkflow,
    WorkflowError,
    WorkflowStep,
)


def _workflow() -> LocalWorkflow:
    return LocalWorkflow(
        "original",
        "保存选中文字",
        1,
        (
            WorkflowStep("capture_selection", {}),
            WorkflowStep(
                "append_text_file",
                {"path": "/Users/example/notes.md", "separator": "\n\n"},
            ),
        ),
        enabled=True,
        tested=True,
    )


def test_export_import_round_trip_assigns_local_id_and_requires_review(tmp_path) -> None:
    path = tmp_path / "保存选中文字.boring-workflow.json"
    export_workflow(path, _workflow())

    imported = import_workflow(path)

    assert imported.name == "保存选中文字"
    assert imported.workflow_id != "original"
    assert imported.enabled is False
    assert imported.tested is False
    assert imported.review_required is True
    assert imported.steps[1].parameters["path"].endswith("notes.md")


def test_import_rejects_unknown_package_or_workflow_fields(tmp_path) -> None:
    path = tmp_path / "bad.json"
    unsupported_package = json.dumps(
        {"kind": "other", "version": 1, "workflow": {}}
    )
    path.write_text(unsupported_package, encoding="utf-8")
    with pytest.raises(WorkflowError, match="不是 BORING"):
        import_workflow(path)
    assert path.read_text(encoding="utf-8") == unsupported_package

    payload = {
        "kind": "boring-workflow",
        "version": 1,
        "workflow": {**_workflow().as_mapping(), "extra": True},
    }
    malformed_workflow = json.dumps(payload)
    path.write_text(malformed_workflow, encoding="utf-8")
    with pytest.raises(WorkflowError, match="未知字段"):
        import_workflow(path)
    assert path.read_text(encoding="utf-8") == malformed_workflow


def test_import_without_machine_local_target_does_not_require_review(tmp_path) -> None:
    source = LocalWorkflow(
        "notify",
        "通知",
        2,
        (WorkflowStep("show_notification", {"message": "完成"}),),
        tested=True,
    )
    path = tmp_path / "notify.json"
    export_workflow(path, source)

    imported = import_workflow(path)

    assert imported.review_required is False
