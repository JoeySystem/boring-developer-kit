import json
import pytest
from PySide6.QtWidgets import QLineEdit

from controller_config.drafts import LocalDraft
from controller_config.protocol.framing import canonical_json_bytes
from controller_config.transport.demo import _power_v2_snapshot
from test_session_recovery import session


@pytest.mark.parametrize('unit', ['A', '中', '😀', '中😀A'])
def test_schema_and_draft_use_codepoint_lengths(contract, unit):
    snapshot = _power_v2_snapshot(contract, read_only=False)
    draft = LocalDraft.from_snapshot(snapshot, contract)
    name = (unit * 24)[:24]
    short = (unit * 12)[:12]
    draft.rename_profile(0, name)
    draft.set_mapping(0, 'key.8', short, {'type':'key','usage':4})
    macro = draft.create_macro()
    draft.update_macro(macro, name, [{'op':'tap','usage':4}])
    assert not draft.validate(contract)
    assert json.loads(canonical_json_bytes(draft.config)) == draft.config
    draft.set_mapping(0, 'key.8', short + 'x', {'type':'key','usage':4})
    assert draft.validate(contract)


def test_short_name_input_accepts_twelve_emoji_without_utf16_truncation(session):
    window, vm, gateway, snapshot, _ = session
    window._select_physical_control('key.8')
    editor = window._content.findChild(QLineEdit, 'mappingShortNameEditor')
    try:
        editor.clear()
        editor.insert('🚀' * 12)
        assert editor.text() == '🚀' * 12
        editor.insert('x')
        assert editor.text() == '🚀' * 12
    finally:
        editor.clear()
        window._selected_control_id = None
