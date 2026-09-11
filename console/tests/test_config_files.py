from __future__ import annotations

import copy
import json

import pytest

from controller_config.config_files import (
    ConfigFileError,
    MAX_CONFIG_FILE_BYTES,
    config_payload_digest,
    export_config_package,
    load_config_package,
)
from controller_config.drafts import LocalDraft
from controller_config.transactions import ConfigTransactionState
from controller_config.transport.demo import DemoGateway
from controller_config.viewmodels.main import MainViewModel


def _snapshot(qtbot, contract):
    gateway = DemoGateway(contract, "ready")
    with qtbot.waitSignal(gateway.snapshot_ready, timeout=1000) as blocker:
        gateway.connect_port("demo://power-v2")
    return blocker.args[0]


def test_export_and_import_draft_round_trip_without_device_specific_port_data(
    qtbot, contract, tmp_path
) -> None:
    snapshot = _snapshot(qtbot, contract)
    draft = LocalDraft.from_snapshot(snapshot, contract)
    draft.rename_profile(snapshot.active_profile_id, "Portable")
    path = tmp_path / "portable.boring-config.json"

    export_config_package(
        path,
        config=draft.config,
        hardware_ids=(draft.hardware_id,),
        kind="draft",
        base_generation=draft.base_generation,
        base_digest=draft.base_digest,
    )
    package = load_config_package(path, contract, current_hardware_id=draft.hardware_id)

    assert package.kind == "draft"
    assert package.config == draft.config
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "serial" not in raw
    assert "port_name" not in raw


def test_import_rejects_digest_mismatch_and_other_hardware(qtbot, contract, tmp_path) -> None:
    snapshot = _snapshot(qtbot, contract)
    draft = LocalDraft.from_snapshot(snapshot, contract)
    path = tmp_path / "candidate.boring-config.json"
    export_config_package(
        path,
        config=draft.config,
        hardware_ids=(draft.hardware_id,),
        kind="confirmed",
        base_generation=draft.base_generation,
        base_digest=draft.base_digest,
    )

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["config"]["display"]["brightness"] = 41
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ConfigFileError, match="摘要"):
        load_config_package(path, contract, current_hardware_id=draft.hardware_id)

    raw = copy.deepcopy(raw)
    raw["payload_digest"] = config_payload_digest(raw["config"])
    raw["hardware_ids"] = ["WMP-S3-REV-A"]
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ConfigFileError, match="当前设备"):
        load_config_package(path, contract, current_hardware_id=draft.hardware_id)


def test_import_rejects_oversized_file_before_json_parsing(contract, tmp_path) -> None:
    path = tmp_path / "oversized.boring-config.json"
    path.write_bytes(b" " * (MAX_CONFIG_FILE_BYTES + 1))

    with pytest.raises(ConfigFileError, match="导入上限"):
        load_config_package(
            path,
            contract,
            current_hardware_id="WMP-S3-MATRIX12-POWER-V2",
        )


def test_view_model_import_replaces_only_local_draft(qtbot, contract, tmp_path) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    draft = view_model.draft
    assert draft is not None
    view_model.rename_profile(0, "Imported later")
    path = tmp_path / "draft.boring-config.json"
    view_model.export_configuration(path, kind="draft")
    view_model.discard_draft()

    package = view_model.read_configuration_file(path)
    view_model.apply_configuration_import(package)

    assert view_model.draft.profile(0)["name"] == "Imported later"
    assert view_model.write_transaction.state is ConfigTransactionState.IDLE
    assert view_model.draft.is_dirty
