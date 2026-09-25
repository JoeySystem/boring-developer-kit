from PySide6.QtCore import QSettings

from controller_config.voice_setup import save_voice_trial, voice_trial


ACTION = {"type": "key_gesture", "usage": 104, "double_usage": 40}


def test_trial_is_retained_only_for_the_mapping_and_software_tried(tmp_path):
    settings = QSettings(str(tmp_path / "voice.ini"), QSettings.IniFormat)
    settings.setValue("ui/voice_input_software", "typeless")
    save_voice_trial(settings, "device", 1, "key.8", ACTION, "typeless", "codex")
    reopened = QSettings(str(tmp_path / "voice.ini"), QSettings.IniFormat)
    assert voice_trial(reopened, "device", 1, "key.8", {**ACTION, "modifiers": []})["target"] == "codex"
    for serial, profile, control, action in (
        ("other", 1, "key.8", ACTION), ("device", 2, "key.8", ACTION),
        ("device", 1, "key.9", ACTION),
        ("device", 1, "key.8", {**ACTION, "usage": 231}),
        ("device", 1, "key.8", {**ACTION, "double_modifiers": [227]}),
    ):
        assert voice_trial(reopened, serial, profile, control, action) is None
    reopened.setValue("ui/voice_input_software", "other")
    assert voice_trial(reopened, "device", 1, "key.8", ACTION) is None
