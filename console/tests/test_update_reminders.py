from PySide6.QtCore import QSettings
from controller_config.update_reminders import UpdateReminders


def test_snooze_persists_expires_and_does_not_hide_new_version(tmp_path):
    path = str(tmp_path / 'reminders.ini')
    now = [1000.]
    def store():
        return UpdateReminders(QSettings(path, QSettings.IniFormat), clock=lambda: now[0])
    s = store()
    s.snooze('app', '0.1.25')
    assert store().is_snoozed('app', '0.1.25')
    assert not store().is_snoozed('app', '0.1.26')
    now[0] += 86400
    assert not store().is_snoozed('app', '0.1.25')


def test_device_targets_are_independent_and_clear_after_confirmation(tmp_path):
    s = UpdateReminders(QSettings(str(tmp_path/'s.ini'), QSettings.IniFormat), clock=lambda: 1000.)
    s.snooze('firmware:A', '1.0|build-2')
    assert not s.is_snoozed('firmware:B', '1.0|build-2')
    assert not s.is_snoozed('firmware:A', '1.0|build-3')
    s.clear('firmware:A')
    assert not s.is_snoozed('firmware:A', '1.0|build-2')
