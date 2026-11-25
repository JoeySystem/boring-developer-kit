"""Console pages, one class per page (规划第 11 节：主窗口按页面拆成类).

See :mod:`controller_config.views.pages.base` for the page interface.
"""
from controller_config.views.pages.base import Page
from controller_config.views.pages.calibration import CalibrationPage
from controller_config.views.pages.firmware import FirmwarePage
from controller_config.views.pages.lighting import LightingPage
from controller_config.views.pages.prompts import PromptsPage
from controller_config.views.pages.sequences import SequencesPage
from controller_config.views.pages.state import StatePage

__all__ = ["CalibrationPage", "FirmwarePage", "LightingPage", "Page", "PromptsPage", "SequencesPage", "StatePage"]
