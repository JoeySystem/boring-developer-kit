from __future__ import annotations

import pytest

from controller_config.extensions.bindings import (
    ExtensionActionBinding,
    ExtensionBindingError,
    ExtensionBindingStore,
)


def test_binding_store_round_trip(tmp_path) -> None:
    store = ExtensionBindingStore(tmp_path / "bindings.json")
    binding = ExtensionActionBinding(
        "CP01-AABBCCDDEEFF", 1, "com.example.prompt-tools", "use_prompt"
    )

    store.save((binding,))

    assert store.load() == (binding,)


def test_binding_store_rejects_two_claimants_for_one_device_slot(tmp_path) -> None:
    store = ExtensionBindingStore(tmp_path / "bindings.json")
    first = ExtensionActionBinding("serial", 2, "com.example.one", "run")
    second = ExtensionActionBinding("serial", 2, "com.example.two", "run")

    with pytest.raises(ExtensionBindingError, match="只能绑定一个"):
        store.save((first, second))


def test_binding_is_scoped_to_device_serial(tmp_path) -> None:
    store = ExtensionBindingStore(tmp_path / "bindings.json")
    first = ExtensionActionBinding("serial-a", 2, "com.example.one", "run")
    second = ExtensionActionBinding("serial-b", 2, "com.example.two", "run")

    store.save((first, second))

    assert len(store.load()) == 2
