from __future__ import annotations

import json

import pytest

from controller_config.prompt_library import (
    PROMPT_BODY_MAX_BYTES,
    PROMPT_NAME_MAX_BYTES,
    PromptEntry,
    PromptLibrary,
    PromptLibraryError,
    PromptLibraryStore,
)


def test_chinese_multiline_prompt_uses_utf8_byte_limits() -> None:
    entry = PromptEntry(1, "代码审查", "检查这段代码。\n先给结论，再说明原因。")

    entry.validate()

    assert entry.name_bytes == len("代码审查".encode("utf-8"))
    assert entry.body_bytes == len(entry.body.encode("utf-8"))


def test_prompt_limits_count_utf8_bytes_not_characters() -> None:
    with pytest.raises(PromptLibraryError, match="名称最多"):
        PromptEntry(1, "中" * (PROMPT_NAME_MAX_BYTES // 3 + 1), "ok").validate()
    with pytest.raises(PromptLibraryError, match="正文最多"):
        PromptEntry(1, "name", "中" * (PROMPT_BODY_MAX_BYTES // 3 + 1)).validate()


def test_prompt_text_rejects_controls_but_allows_body_layout() -> None:
    PromptEntry(1, "名称", "第一行\n\t第二行\r\n").validate()

    with pytest.raises(PromptLibraryError, match="控制字符"):
        PromptEntry(1, "坏\n名称", "正文").validate()
    with pytest.raises(PromptLibraryError, match="控制字符"):
        PromptEntry(1, "名称", "坏\0正文").validate()


def test_unsent_draft_never_replaces_helper_confirmed_text() -> None:
    confirmed = PromptEntry(1, "旧名称", "设备中的正文")
    library = PromptLibrary("CP01-AABBCCDDEEFF", confirmed=(confirmed,))

    library.set_draft(1, "新名称", "尚未写入的正文")

    assert library.confirmed_entry(1) == confirmed
    assert library.draft_entry(1).body == "尚未写入的正文"
    assert library.dirty_prompt_ids == (1,)


def test_device_readback_confirms_one_prompt() -> None:
    library = PromptLibrary("CP01-AABBCCDDEEFF")
    draft = library.set_draft(2, "解释", "解释选中的代码")

    library.confirm_entry(draft)

    assert library.confirmed_entry(2) == draft
    assert library.dirty_prompt_ids == ()


def test_refresh_preserves_unsent_slot_and_updates_other_device_slots() -> None:
    old_one = PromptEntry(1, "一", "设备旧值")
    old_two = PromptEntry(2, "二", "设备旧值二")
    library = PromptLibrary(
        "CP01-AABBCCDDEEFF", confirmed=(old_one, old_two)
    )
    library.set_draft(1, "一", "本地未写入")

    new_one = PromptEntry(1, "一", "设备被其他客户端更新")
    new_two = PromptEntry(2, "二", "设备新值二")
    library.replace_confirmed((new_one, new_two))

    assert library.draft_entry(1).body == "本地未写入"
    assert library.confirmed_entry(1) == new_one
    assert library.draft_entry(2) == new_two
    assert library.dirty_prompt_ids == (1,)


def test_prompt_library_store_round_trips_utf8_and_sync_state(tmp_path) -> None:
    store = PromptLibraryStore(tmp_path)
    library = PromptLibrary(
        "CP01-AABBCCDDEEFF",
        confirmed=(PromptEntry(1, "翻译", "翻译成英文"),),
    )
    library.set_draft(2, "检查", "检查事实。\n不要自动提交。")

    store.save(library)
    loaded = store.load(library.serial)

    assert loaded.confirmed == library.confirmed
    assert loaded.draft == library.draft
    assert loaded.dirty_prompt_ids == (2,)
    raw = json.loads(store.path_for(library.serial).read_text(encoding="utf-8"))
    assert raw["draft"][1]["body"] == "检查事实。\n不要自动提交。"


def test_prompt_library_store_rejects_wrong_serial(tmp_path) -> None:
    store = PromptLibraryStore(tmp_path)
    path = store.path_for("CP01-AABBCCDDEEFF")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"version": 1, "serial": "CP01-000000000000", "confirmed": [], "draft": []}
        ),
        encoding="utf-8",
    )

    with pytest.raises(PromptLibraryError, match="序列号不一致"):
        store.load("CP01-AABBCCDDEEFF")
