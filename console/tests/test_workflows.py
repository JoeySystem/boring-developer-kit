from __future__ import annotations

import json

import pytest

from controller_config.workflows import (
    LocalWorkflow,
    WorkflowError,
    WorkflowStep,
    WorkflowStore,
    reference_markdown_workflow,
)


def _workflow(**changes) -> LocalWorkflow:
    values = {
        "workflow_id": "save-selection",
        "name": "保存中英文",
        "trigger_prompt_id": 2,
        "steps": (
            WorkflowStep("capture_selection", {}),
            WorkflowStep(
                "append_text_file",
                {"path": "/tmp/收集.md", "separator": "\n\n"},
            ),
        ),
        "enabled": False,
        "tested": False,
        "review_required": False,
    }
    values.update(changes)
    return LocalWorkflow(**values)


def test_workflow_store_round_trips_utf8_per_device(tmp_path) -> None:
    store = WorkflowStore(tmp_path)
    workflow = _workflow(tested=True, enabled=True)

    store.save("CP01-AABBCCDDEEFF", (workflow,))

    assert store.load("CP01-AABBCCDDEEFF") == (workflow,)
    raw = json.loads(
        store.path_for("CP01-AABBCCDDEEFF").read_text(encoding="utf-8")
    )
    assert raw["workflows"][0]["name"] == "保存中英文"
    assert raw["workflows"][0]["steps"][1]["parameters"]["path"].endswith(
        "收集.md"
    )


@pytest.mark.parametrize("steps", [(), tuple(WorkflowStep("read_clipboard", {}) for _ in range(6))])
def test_workflow_requires_one_to_five_steps(steps) -> None:
    with pytest.raises(WorkflowError, match="1–5"):
        _workflow(steps=steps).validate()


def test_workflow_rejects_unknown_action_and_parameters() -> None:
    with pytest.raises(WorkflowError, match="不支持"):
        WorkflowStep("run_anything", {}).validate()
    with pytest.raises(WorkflowError, match="未知字段"):
        WorkflowStep("read_clipboard", {"extra": True}).validate()


def test_enabled_workflow_requires_successful_test_and_review(tmp_path) -> None:
    with pytest.raises(WorkflowError, match="成功测试"):
        _workflow(enabled=True).validate()
    with pytest.raises(WorkflowError, match="检查本机路径"):
        _workflow(enabled=True, tested=True, review_required=True).validate()


def test_only_one_enabled_workflow_may_own_a_prompt(tmp_path) -> None:
    store = WorkflowStore(tmp_path)
    first = _workflow(workflow_id="one", tested=True, enabled=True)
    second = _workflow(workflow_id="two", tested=True, enabled=True)

    with pytest.raises(WorkflowError, match="其他内置自动化"):
        store.save("CP01-AABBCCDDEEFF", (first, second))


def test_strict_loader_rejects_unknown_fields(tmp_path) -> None:
    store = WorkflowStore(tmp_path)
    path = store.path_for("SERIAL")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "serial": "SERIAL",
        "workflows": [{**_workflow().as_mapping(), "unexpected": True}],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(WorkflowError, match="未知字段"):
        store.load("SERIAL")


def test_reference_workflow_is_an_editable_disabled_definition() -> None:
    workflow = reference_markdown_workflow(target="/tmp/notes.md")

    workflow.validate()
    assert [step.action_id for step in workflow.steps] == [
        "capture_selection",
        "append_text_file",
        "show_notification",
    ]
    assert workflow.enabled is False
    assert workflow.tested is False


def test_duplicate_gets_new_identity_and_must_be_retested() -> None:
    duplicated = _workflow(enabled=True, tested=True).duplicate()

    assert duplicated.workflow_id != "save-selection"
    assert duplicated.name.endswith("副本")
    assert duplicated.enabled is False
    assert duplicated.tested is False
