"""Native pointer paths and rendered rotation must agree on encoder direction."""
import math
import os

import pytest
from PySide6.QtCore import Q_ARG, Q_RETURN_ARG, QMetaObject, QObject, QPoint, Qt
from PySide6.QtGui import QSurfaceFormat, QVector3D
from PySide6.QtQuick3D import QQuick3D
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton, QWidget

from controller_config.transport.demo import _power_v2_snapshot
from controller_config.views.device_silhouette import DeviceModelCanvas

if os.environ.get('BORING_NATIVE_RENDERER_TEST') == '1':
    QSurfaceFormat.setDefaultFormat(QQuick3D.idealSurfaceFormat())
pytestmark = pytest.mark.skipif(os.environ.get('BORING_NATIVE_RENDERER_TEST') != '1',
                              reason='Requires native Qt Quick 3D rendering')


@pytest.fixture
def encoder_scene(qtbot, contract, monkeypatch):
    monkeypatch.setenv('BORING_FORCE_QUICK3D', '1')
    snapshot = _power_v2_snapshot(contract, read_only=False)
    selected = []
    host = QWidget()
    host.resize(480, 480)
    canvas = DeviceModelCanvas(snapshot, snapshot.mappings, None, host, on_control=selected.append)
    canvas.move(30, 30)
    button = QPushButton(canvas)
    canvas.attach_control('encoder', button)
    host.show()
    qtbot.waitUntil(lambda: hasattr(host, '_boring_mist_quick_viewport'), timeout=10000)
    viewport = host._boring_mist_quick_viewport
    qtbot.wait(600)
    assert not viewport.errors()
    root = viewport.rootObject()
    scene = root.findChild(QObject, 'boringMist3DScene')
    knob = root.findChild(QObject, 'encoder')
    def project(point):
        return QMetaObject.invokeMethod(scene, 'mapFrom3DScene', Q_RETURN_ARG('QVector3D'),
                                       Q_ARG('QVector3D', point))
    center = project(knob.property('scenePosition'))
    yield canvas, viewport, knob, center, project, selected
    DeviceModelCanvas.release_shared_viewport(host)
    host.close()
    host.deleteLater()
    qtbot.wait(80)


@pytest.mark.parametrize('start', [-170, -90, 0, 90, 170])
@pytest.mark.parametrize('direction', [1, -1])
def test_circular_drag_all_sides_and_angle_wrap(encoder_scene, start, direction):
    canvas, viewport, knob, center, project, selected = encoder_scene
    def point(angle):
        radians = math.radians(angle)
        return QPoint(round(center.x() + 13 * math.cos(radians)),
                      round(center.y() + 13 * math.sin(radians)))
    first = point(start)
    QTest.mousePress(viewport, Qt.LeftButton, pos=first)
    for offset in (10, 20, 30, 40):
        QTest.mouseMove(viewport, point(start + direction * offset), 30)
    QTest.mouseRelease(viewport, Qt.LeftButton, pos=point(start + direction * 40))
    expected = 'encoder.cw' if direction == 1 else 'encoder.ccw'
    assert selected, (start, direction, 'no drag action')
    assert set(selected) == {expected}, (start, direction, selected)


@pytest.mark.parametrize('control,sign', [('encoder.cw', 1), ('encoder.ccw', -1)])
def test_rendered_rotation_matches_selected_direction(encoder_scene, qtbot, control, sign):
    canvas, viewport, knob, center, project, selected = encoder_scene
    def marker():
        scene_point = QMetaObject.invokeMethod(knob, 'mapPositionToScene', Q_RETURN_ARG('QVector3D'),
                                              Q_ARG('QVector3D', QVector3D(10, 0, 0)))
        return project(scene_point)
    before = marker()
    canvas.activateControl(control)
    qtbot.wait(650)
    after = marker()
    cross = ((before.x()-center.x()) * (after.y()-center.y())
             - (before.y()-center.y()) * (after.x()-center.x()))
    # Screen Y grows downward, so a positive cross product is clockwise.
    assert sign * cross > 1, (control, cross, knob.property('eulerRotation'))
    assert selected == [control]


def test_click_and_complete_turn_do_not_share_release_action(encoder_scene, qtbot):
    canvas, viewport, knob, center, project, selected = encoder_scene
    center_point = QPoint(round(center.x()), round(center.y()))
    QTest.mouseClick(viewport, Qt.LeftButton, pos=center_point)
    qtbot.waitUntil(lambda: selected == ['encoder.press'])
    selected.clear()
    first = center_point + QPoint(13, 0)
    QTest.mousePress(viewport, Qt.LeftButton, pos=first)
    for degrees in range(10, 361, 10):
        angle = math.radians(degrees)
        QTest.mouseMove(viewport, center_point + QPoint(round(13 * math.cos(angle)),
                                                       round(13 * math.sin(angle))), 10)
    QTest.mouseRelease(viewport, Qt.LeftButton, pos=first)
    assert selected and set(selected) == {'encoder.cw'}


def test_radial_drag_through_center_does_not_rotate_or_click(encoder_scene):
    canvas, viewport, knob, center, project, selected = encoder_scene
    center_point = QPoint(round(center.x()), round(center.y()))
    QTest.mousePress(viewport, Qt.LeftButton, pos=center_point + QPoint(13, 0))
    for x in (6, 0, -6, -13):
        QTest.mouseMove(viewport, center_point + QPoint(x, 0), 20)
    QTest.mouseRelease(viewport, Qt.LeftButton, pos=center_point + QPoint(-13, 0))
    assert selected == []
