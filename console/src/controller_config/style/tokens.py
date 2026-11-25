"""Console visual tokens: the only place UI code takes colours and type sizes from.

Values are exactly what the interface used on 2026-11-18 (完整整合版 84e70797),
gathered without merging look-alike values: choosing which near-identical
colours to merge is a design decision (the designer), not a refactor. Names say
what a value is for, so a later change happens here once.

Stylesheets use the string values through `QSS` (see `stylesheet`);
painted widgets use the same constants directly, e.g. `QColor(TEXT_PRIMARY)`
or `QColor(*SHELL_CAPSULE_FILL)` for the RGBA tuples.

Not here yet, on purpose: the old `APP_STYLE` layers in views/main_window.py
(step 3 of the style foundation plan), the device model renderings (being
redrawn) and the onboarding theme.
"""
from __future__ import annotations

from string import Template

# -- surfaces ---------------------------------------------------------------
SURFACE_BASE = "#1C1B19"            # window and card background
SURFACE_1 = "#232220"               # disabled controls, progress track, multi-line inputs
SURFACE_2 = "#2B2A27"               # controls: buttons, inputs, chips
SURFACE_3 = "#34322E"               # control hover, slider groove
SURFACE_FOCUS = "#221F1B"           # focus card
SURFACE_WIDGET = "#242320"          # widget card, menus, combo popups
SURFACE_SELECTED = "#3A3833"        # selected menu / list item
SURFACE_PROMPT_DIRECTION = "#514F46"  # idle direction button on the prompt preview
SURFACE_PROMPT_EDITOR = "#252420"   # prompt body editor
SURFACE_EVENT_LOG = "#171814"       # prompt event log
SURFACE_ACCENT_DISABLED = "#3B332E"  # disabled primary button
SCROLLBAR_HANDLE = "#4A4740"

# -- text -------------------------------------------------------------------
TEXT_PRIMARY = "#EFEAE0"            # main text; also the cream "selected" fill
TEXT_SECONDARY = "#9A958C"          # muted text
TEXT_TERTIARY = "#7E796F"           # faint text, disabled text
BRAND_WORD = "#F7F7F2"              # top bar logo: BORING (顶条 v1 定稿)
BRAND_EDITION = TEXT_TERTIARY       # top bar logo: CONSOLE
TEXT_SECONDARY_ALT = "#B6B0A5"      # secondary text in Bluetooth computer rows
WHITE = "#FFFFFF"                   # text on accent, hover of cream buttons

# -- accent and status ------------------------------------------------------
ACCENT = "#FF6A00"                  # primary action, progress, selection
ACCENT_HOVER = "#FF7A1A"
ACCENT_FOCUS = "#FF9A52"            # focus ring on toggles
SIGNAL = "#CC5200"                  # pending / error text
STATUS_OK = "#8FB98F"
STATUS_DONE = "#4FD07F"            # status graphics v1 final: the green check (done / on)
AGENT = "#9FB3EE"                   # Agent status lights

# -- lines ------------------------------------------------------------------
BORDER_SUBTLE = "rgba(239,234,224,16)"       # card outline
BORDER_CONTROL = "rgba(239,234,224,26)"      # control outline, menu separator
BORDER_CONTROL_HOVER = "rgba(239,234,224,52)"
BORDER_STRONG = "rgba(239,234,224,71)"       # secondary button outline
BORDER_ON_ACCENT = "rgba(255,255,255,61)"    # primary button outline
BORDER_POPUP = "#45423B"                     # combo popup outline
DIVIDER = "rgba(239,234,224,41)"             # navigation separator

# -- painted shell (RGBA tuples for QColor(*value)) ---------------------------
SHELL_TINT = (28, 27, 25, 102)          # neutral tint over the native glass, 40%
SHELL_CAPSULE_FILL = (28, 27, 25, 140)  # top navigation capsule
SHELL_CAPSULE_EDGE = (255, 255, 255, 18)
SHELL_DOT = (239, 234, 224, 5)          # dotted window background
FIRMWARE_TRACK = (245, 245, 238, 10)    # firmware progress track
FIRMWARE_TRACK_EDGE = (245, 245, 238, 92)
FIRMWARE_WARNING_DOT = "#FF5F57"

# -- macOS menu bar panel (system menu palette, not the window palette) -------
MENUBAR_TEXT = "#f5f5f7"
MENUBAR_TEXT_HOVER = "#ffffff"
MENUBAR_TEXT_SOURCE = "rgba(235, 235, 245, 150)"
MENUBAR_TEXT_WINDOW = "rgba(245, 245, 247, 210)"
MENUBAR_TEXT_MESSAGE = "rgba(235, 235, 245, 170)"
MENUBAR_TEXT_DISABLED = "rgba(235, 235, 245, 138)"
MENUBAR_PLAN = "#ff8068"
MENUBAR_DIVIDER = "rgba(255, 255, 255, 30)"
MENUBAR_BUTTON_HOVER = "rgba(255, 255, 255, 28)"
MENUBAR_BUTTON_PRESSED = "rgba(255, 255, 255, 38)"
MENUBAR_QUOTA_RING_LIT = MENUBAR_TEXT              # quota ring: what is left (进度条 定稿 v1: 系统菜单的白)
MENUBAR_QUOTA_RING_UNLIT = (255, 255, 255, 56)     # ... and what is used (半透明白; alpha is the Agent's pick)
MENUBAR_TEXT_RESET = MENUBAR_TEXT_WINDOW           # reset time on the right of a quota row

# -- settings wall (views/settings_wall.py; the designer's 11-17 design) ----------
# The wall's cream is the window's cream (#EFEAE0, the firmware owner 11-19, design ticket B).
# The wall's green still differs slightly from STATUS_OK.
WALL_TILE = "#1E1D1A"                   # tile background
WALL_CHIP = "#2B2926"                   # icon well in a tile
WALL_TEXT = TEXT_PRIMARY               # tile text; the cream selected fill
WALL_RAIL = "#2E2E2D"                   # knob colour inside rail icons
WALL_STATUS_OK = "#86B98A"
WALL_TEXT_ON_CREAM = "#1A1916"          # selected rail icon, active language
WALL_ICON = "#A9A59D"                   # rail icon
WALL_ICON_DISABLED = "#5F5C56"
WALL_ICON_TILE = "#CFCABF"              # icons and lamp numbers inside tiles
WALL_LAMP_EDGE = "#6A665F"
WALL_TEXT_DISABLED = "#6F6B64"          # disabled wall button text
WALL_SEGMENT = "#161513"                # language segmented control
# Language keys in Settings · 通用 (the designer 11-19, design decision; 演示页 v0.5/v0.7). Sizes are the Agent's.
LANGUAGE_KEY = WALL_SEGMENT              # an unselected key
LANGUAGE_KEY_SELECTED = WALL_TEXT        # the selected key is cream all over
LANGUAGE_KEY_TEXT = WALL_ICON            # glyph and name on an unselected key
LANGUAGE_KEY_TEXT_SELECTED = WALL_TEXT_ON_CREAM
LANGUAGE_KEY_HEIGHT = 112                # at most; shrinks with the wall
LANGUAGE_KEY_RADIUS = 14
LANGUAGE_KEY_GAP = 8
FONT_LANGUAGE_GLYPH = 28                 # 文 / A / あ
LANGUAGE_FILL_MS = 480                   # “点阵长满”: 1.5 × the dot slider's 0.32 s settle
LANGUAGE_FILL_PITCH = 7                  # dot spacing while the cream grows or shrinks
# BORING orbital logo as a watermark under 设备信息 (design decision item 1; 2026-09-15 frozen geometry)
ORBITAL_MARK = WALL_CHIP                 # same as the icon well: reads as an emboss
ORBITAL_MARK_HEIGHT = 150                # at most; shrinks when the tile is small
WALL_DIALOG = "#262522"                 # settings card dialog
WALL_TAG_BORDER = "rgba(255, 255, 255, 66)"
WALL_RULE = "rgba(255, 255, 255, 18)"
WALL_BUTTON_BORDER = "rgba(255, 255, 255, 61)"
WALL_BUTTON_BORDER_HOVER = "rgba(255, 255, 255, 120)"
WALL_BUTTON_BORDER_DISABLED = "rgba(255, 255, 255, 26)"
WALL_EDGE = (255, 255, 255, 18)         # tile and icon-well outline
WALL_EDGE_RAISED = (255, 255, 255, 46)  # an opened tile lying over its neighbour
WALL_TIP_EDGE = (255, 255, 255, 36)     # icon tip bubble outline
WALL_HOVER = (255, 255, 255, 20)        # rail button hover
WALL_LAMP_FILL = (255, 255, 255, 10)

# -- dot matrix slider (the designer 11-18 final: every slider in the Console) -----
DOT_SLIDER_LIT = WALL_TEXT               # dots up to the value
DOT_SLIDER_UNLIT = "#4A4843"             # dots past the value

# -- progress ring and dot row (进度条点阵样式 定稿 v1, design decision) -------------
# Progress is a ring of dots (views/ui/progress_ring.py); readings and demos are
# one row of orange dots (views/ui/dot_row.py). 6-数值和谁定的.png.
PROGRESS_RING_LIT = DOT_SLIDER_LIT       # dots up to the value (the slider's cream)
PROGRESS_RING_UNLIT = DOT_SLIDER_UNLIT   # dots not reached yet
PROGRESS_RING_PAUSED = TEXT_SECONDARY    # "等着": lit dots and the number turn grey (#9A958C)
PROGRESS_RING_FAILED = ACCENT            # "没做成": every lit dot turns orange (the designer 11-20 真屏验收)
PROGRESS_RING_LOW = ACCENT               # quota ring: lit dots when the quota is nearly used up
PROGRESS_RING_NUMBER_MEDIUM = 16         # number in the 48 ring
PROGRESS_RING_NUMBER_SMALL = 12.5        # number in the 36 ring
QUOTA_LOW_PERCENT = 20                   # quota ring turns orange at or below this much left (no value in the spec; Agent's pick)
DOT_ROW_LIT = ACCENT                     # one row of dots: the reading / demo step
DOT_ROW_UNLIT = DOT_SLIDER_UNLIT

LEGACY_MODE_CONTROLS_SELECTED = "#574130"  # selected item in the control picker dialog

# -- dialogs (the designer B direction v0.4 final) ----------------------------------
DIALOG_SURFACE = SURFACE_1               # dialog card
DIALOG_EDGE = (239, 234, 224, 26)        # 1 px cream outline at 10%
DIALOG_SCRIM = (0, 0, 0, 115)            # 45% black over the main window
RADIUS_LIST_ROW = 18                     # a list row or choice card inside a dialog (B v0.4 数值表: 列表的一行和勾选卡圆角 18)
# 选别的动作… (Mode页 两个弹窗 v0, 2026-11-20)
OTHER_ACTION_TILE_OFF = "#262523"        # a kind greyed out for now
OTHER_ACTION_REASON = "#C98A5A"          # why it is greyed (orange-brown)
NOTICE_WARN_FILL = "#3A2A1F"             # the orange-triangle notice box in a dialog
RADIUS_NOTICE = 14
# 设置语音输入… 的操作演示 (Mode页 两个弹窗 v0, 10): a white stage, as in the dialog draft's guide.
VOICE_STAGE = "#F8F8F8"
VOICE_STAGE_INK = "#1A1916"
VOICE_STAGE_MUTED = "#8A867E"
VOICE_STAGE_EDGE = "#E2DED6"
VOICE_STAGE_FIELD = "#FFFFFF"
VOICE_KEY = "#2A2926"
RADIUS_CHIP = 18

# -- device top view (views/ui/device_top_view.py) ------------------------------
# 设备图俯视图 正式稿 v0.5 数值表 (8-数值表.png), black model, flat.
TOP_VIEW_BODY = "#3A3936"                # body frame
TOP_VIEW_PANEL = "#2F2E2B"               # top panel; also the gap inside the selection ring
TOP_VIEW_STATUS_CAP = "#C9CAC4"          # the 6 translucent status-light keys (1, 2, 4-7)
TOP_VIEW_KEY_CAP = "#A29F96"             # the 6 function keys (3, 8-12) with their light off
TOP_VIEW_KEY_CAP_HOVER = "#B6B3AA"       # function key under the pointer, light off
TOP_VIEW_KEY_OFFLINE = "#8C8A84"         # all 12 keys while no device is connected (design ticket 第 1 件, design decision)
TOP_VIEW_LIGHT_CREAM = TEXT_PRIMARY      # a lit key's colour is mixed with this cream ...
TOP_VIEW_LIGHT_CREAM_SHARE = 0.22        # ... at 22%, like light through a milky cap (design ticket 第 2 件;
                                         # the device draft's #E9EBE6 swapped for the window cream, the designer 11-20)
TOP_VIEW_KNOB = "#474641"
TOP_VIEW_KNOB_TICK = "#77756D"           # the three short lines on the knob
TOP_VIEW_KNOB_GAP = "#1F1E1C"            # 1.6 mm ring around the knob
TOP_VIEW_JOYSTICK_CAP = "#CFD0CA"
TOP_VIEW_JOYSTICK_INNER = "#BFC0B9"      # dimple in the cap
TOP_VIEW_JOYSTICK_BASE = "#1E1D1B"
TOP_VIEW_SCREEN = "#0F0F0E"              # the round screen, drawn plain
TOP_VIEW_SELECTED = ACCENT               # selection ring (#FF6A00)
TOP_VIEW_KEY_NUMBER = WALL_TEXT_ON_CREAM  # key numbers, drawn at 60% over the key colour
TOP_VIEW_KNOB_PRESSED = "#33322F"        # the knob while pressed (正式稿 v0.5 第 6 张 "旋钮 · 按下")
TOP_VIEW_MARK = ACCENT                   # ring round the joystick base / knob and the direction arrow
TOP_VIEW_MARK_WIDTH = 2                  # px, the ring and the arrow line
TOP_VIEW_JOYSTICK_PUSH_MM = 2.4          # the cap leans this far towards a pushed direction (v0.5 第 6 张)

# -- key light colour presets (灯光页 排法二 v0.4, 3-点键改颜色-一排预设色.png) -------
# The 11 colours in order: 白 暖白 黄 橙 红 粉 紫 蓝紫 蓝 青 绿 (sampled from the draft).
LIGHT_PRESET_WHITE = "#FFFFFF"
LIGHT_PRESET_WARM_WHITE = "#FFD9A8"
LIGHT_PRESET_YELLOW = "#FFD23F"
LIGHT_PRESET_ORANGE = "#FF8A3D"
LIGHT_PRESET_RED = "#FF3B5C"
LIGHT_PRESET_PINK = "#FF5FA2"
LIGHT_PRESET_PURPLE = "#C45BFF"
LIGHT_PRESET_INDIGO = "#5B4BFF"
LIGHT_PRESET_BLUE = "#3D8BFF"
LIGHT_PRESET_CYAN = "#22D3C5"
LIGHT_PRESET_GREEN = "#5BE06A"
LIGHT_PRESETS = (
    ("白", LIGHT_PRESET_WHITE), ("暖白", LIGHT_PRESET_WARM_WHITE), ("黄", LIGHT_PRESET_YELLOW),
    ("橙", LIGHT_PRESET_ORANGE), ("红", LIGHT_PRESET_RED), ("粉", LIGHT_PRESET_PINK),
    ("紫", LIGHT_PRESET_PURPLE), ("蓝紫", LIGHT_PRESET_INDIGO), ("蓝", LIGHT_PRESET_BLUE),
    ("青", LIGHT_PRESET_CYAN), ("绿", LIGHT_PRESET_GREEN),
)
LIGHT_SWATCH_RING = WALL_TEXT            # ring around the chosen colour
LIGHT_SWATCH_RIM = (255, 255, 255, 41)   # 1 px light rim inside every colour dot (16% white)
LIGHT_SWATCH_OFF = WALL_SEGMENT          # "不亮" dot fill (#161513)
LIGHT_SWATCH_OFF_EDGE = "#3B3A38"        # "不亮" dot outline
LIGHT_SWATCH_OFF_SLASH = "#8F8B83"       # "不亮" slash
LIGHT_SWATCH_DIVIDER = DOT_SLIDER_UNLIT  # line after an old custom colour (#4A4843)

# -- prompt direction cross (views/ui/direction_cross.py) -------------------------
# Prompt 页 定稿 v1 (5-小图形和数值表.png), cell colours sampled from 1-主画面-960x640.
PROMPT_CELL = "#252320"                  # direction cell (数值表 "输入框底"; 1 off SURFACE_PROMPT_EDITOR)
PROMPT_CELL_EDGE = "#2D2C29"             # 1 px cell outline (数值表 "描边")
PROMPT_CELL_TEXT = WALL_TEXT             # prompt name
PROMPT_CELL_MUTED = TEXT_SECONDARY       # direction word, body preview, "＋ 添加"
PROMPT_CELL_SELECTED = WALL_TEXT         # the selected cell is cream all over
PROMPT_CELL_TEXT_ON_CREAM = WALL_TEXT_ON_CREAM  # name on the selected cell
PROMPT_CELL_MUTED_ON_CREAM = "#5F5A52"   # direction word and body preview on the selected cell
PROMPT_JOYSTICK = WALL_TEXT              # the joystick disc and the lit arrow dots
PROMPT_GLYPH_WELL = WALL_CHIP            # 32 px well behind the small glyph in the editor title
PROMPT_SET_DOT = WALL_TEXT               # the four-dot cross: a direction that is set
PROMPT_UNSET_DOT = TEXT_SECONDARY        # ... and the outline of one that is not

# -- Prompt 页 定稿 v1 (design decision, 20261120-02) ----------------------------------
# "保存到设备" while it cannot be pressed: the draft's `.bt.p.off`.
PROMPT_SAVE_OFF = "#35302B"              # background and outline
PROMPT_SAVE_OFF_TEXT = "#8D857B"
# 看演示: the step numbers, `.steps i` / `.steps .hot i`.
PROMPT_STEP_DIAMETER = 17
PROMPT_STEP_WELL = "#2B2926"
PROMPT_STEP_TEXT = TEXT_SECONDARY        # number on an idle step (the draft's muted ink)
PROMPT_STEP_HOT = ACCENT                 # the current step's circle, white number
FONT_PROMPT_STEP = 10.5                  # bold; painted only

# -- pop-up menu (views/ui/menu.py; Prompt 页 定稿 v1 `.menu`) --------------------
MENU_SURFACE = "#262522"
MENU_EDGE = (239, 234, 224, 31)          # 1 px outline, rgba(239,234,224,.12)
MENU_RADIUS = 14
MENU_SHADOW = (0, 0, 0, 128)             # 0 16px 40px rgba(0,0,0,.5)
MENU_SHADOW_OFFSET = 16
MENU_SHADOW_BLUR = 40
MENU_PADDING = 6
MENU_WIDTH = 206                         # at least; a longer translation widens it
MENU_ITEM_HEIGHT = 34
MENU_ITEM_RADIUS = 9
MENU_ITEM_PADDING = 10                   # left and right
MENU_ITEM_HOVER = SURFACE_3              # #34322E, the faint rounded box under the pointer
MENU_TEXT = TEXT_PRIMARY
MENU_TEXT_DISABLED = "#716D65"
MENU_TEXT_DANGER = "#FF8A5C"             # "从设备删除"
MENU_RULE = (239, 234, 224, 20)          # 1 px line above the danger item, rgba(239,234,224,.08)
MENU_RULE_INSET = 6                      # the line stops this far from each side
MENU_ARROW = 12                          # submenu "›": M9 6l6 6-6 6 in a 24 box, stroke 2
MENU_GAP = 6                             # between the button and the menu below it
# The window is bigger than the card by the shadow's reach: views/ui/menu.py sets
# these as its contents margins and paints the card inside them.
MENU_SHADOW_LEFT = MENU_SHADOW_RIGHT = MENU_SHADOW_BLUR // 2
MENU_SHADOW_TOP = max(0, MENU_SHADOW_BLUR // 2 - MENU_SHADOW_OFFSET)
MENU_SHADOW_BOTTOM = MENU_SHADOW_BLUR // 2 + MENU_SHADOW_OFFSET
MENU_ITEM_PADDING_RIGHT = MENU_ITEM_PADDING + MENU_ARROW + MENU_ITEM_PADDING  # room for "›"

# -- onboarding (使用引导: the dark dialog shell with a white stage, B v0.4 合成版 3) --
ONBOARDING_STAGE = "#F8F8F8"             # the white stage; must equal the GIF background or a seam shows
ONBOARDING_STAGE_RADIUS = 18
ONBOARDING_STAGE_TEXT = PROMPT_CELL_MUTED_ON_CREAM  # fallback text on the stage when the animation is missing (Agent's pick)

# -- Mode page (Mode页 定稿 v1, 2026-11-19; measured from 01-整台设备那一屏.png at 2x) --
# Every number here was set by the Mode page agent; the designer looked at the picture,
# not at each value (单 design ticket).
MODE_BASE_WIDTH = 960               # the page is drawn at this size and scaled with the window
MODE_BASE_HEIGHT = 532              # design 640 tall minus the 108 above the page
MODE_BACKGROUND = SURFACE_BASE
MODE_WORD_DOT = SURFACE_2           # NORMAL / CODEX behind the device
MODE_WORD_SCALE = 7                 # BORING 5R at 7x: dot 14, pitch 21
MODE_FLOOR_DOT = TOP_VIEW_BODY      # the dot floor under the device, at its brightest
MODE_FLOOR_PITCH = 13
MODE_FLOOR_DOT_SIZE = 2.4
MODE_SWEEP_DOT = "#DCD6CB"         # the brightest dots of the sheet that wells up on connecting (06)
MODE_SWEEP_SKIP = (6, 4, 72, 32)    # x, y, w, h in window px: the sheet goes over the whole window but this pill round the three window buttons (design decision)
MODE_ZOOM_CARD = "#2A2926"         # a joystick direction card not chosen (11)
MODE_KEY_GLYPH_FIT = "CW"           # dot letters on the card: three letters (CCW) shrink to this width, so the title is not cut (design decision)
MODE_SHEET_SETTLE_MS = 150          # while the window is dragged, the Mode page restyles its sizes only this long after it stops (design decision: 拖动中不重设样式)
MODE_DRAG_GAP_MS = 100             # a resize this soon after the last one was drawn is one drag of the window edge; a lone resize (maximise) restyles at once (真屏 11-22: by hand the next size comes 40～150 ms after a frame)
MODE_DEVICE_BODY = 330              # device body width at scale 1
MODE_NEIGHBOUR_SHOWN = 0.40         # only this much of each screen either side is inside the window, from its edge (design ticket)
MODE_NEIGHBOUR_BLUR = 8.0          # their Gaussian blur radius, page units (-08: 露出 40% + 高斯模糊)
MODE_NEIGHBOUR_SCALE = 0.68        # and smaller, so the device being set stands out (the firmware owner 11-20)
MODE_NEIGHBOUR_OPACITY = 0.2       # the same device as the one on show, as one faint shadow (定稿 nb(), design ticket)
MODE_JOYSTICK_RING = "#45423D"      # the circle round the joystick's press label
MODE_PAGE_DOT = "#4A4843"
MODE_PAGE_DOT_VIEWED = TEXT_PRIMARY  # the screen on show
MODE_PAGE_RING = STATUS_DONE        # ring round the screen the device is using
MODE_LABEL_TITLE = "#716D65"        # 旋钮, 摇杆, 状态灯 ×6
MODE_LABEL_MUTED = TEXT_SECONDARY   # 逆时针, 顺时针, 按下
MODE_LABEL_VALUE = TEXT_PRIMARY     # 音量 − ...
MODE_AGENT_WORKING = "#5971F9"      # status-light keys as the draft paints them
MODE_AGENT_COMPLETED = "#33FB6E"
MODE_AGENT_ATTENTION = "#FA8933"
MODE_AGENT_ERROR = "#F2475A"        # not in the draft; the firmware red, softened like the others
MODE_BAR_FILL = "#21201D"
MODE_BAR_EDGE = "rgba(239,234,224,18)"
MODE_BAR_RULE = TOP_VIEW_BODY
MODE_BAR_ROUND = SURFACE_2          # the round × and Bluetooth buttons
MODE_CARD = TEXT_PRIMARY            # the cream card on the right
MODE_CARD_TEXT = WALL_TEXT_ON_CREAM
MODE_CARD_MUTED = "#6B665D"
MODE_CARD_WELL = "#E2DDD5"          # recorder box, name field
MODE_CARD_SEGMENT = "#DDD8D1"
MODE_CARD_SEGMENT_TEXT = "#5D584F"     # a purpose segment not chosen (.cc .sg)
MODE_CARD_EDGE = "rgba(26,25,22,40)"
MODE_CARD_DISABLED = "rgba(26,25,22,90)"   # a purpose the firmware cannot do yet
MODE_CARD_ALERT = "rgba(255,106,0,28)"     # 没读到设备里现在的设置 (04): an orange-tinted well
MODE_CARD_INK_HOVER = "#000000"            # the black record button under the pointer
MODE_BATTERY = TEXT_PRIMARY
MODE_BATTERY_LOW = ACCENT           # 20% and below
MODE_BATTERY_LOW_FROM = 20

# -- type -------------------------------------------------------------------
FONT_FAMILY = '"HarmonyOS Sans SC", ".AppleSystemUIFont", "PingFang SC", sans-serif'
FONT_BODY = 13
FONT_CAPTION = 12
FONT_ROW_TITLE = 14
FONT_EMPHASIS = 15
FONT_MODULE_TITLE = 18
FONT_PAGE_TITLE = 22
FONT_ONBOARDING_TITLE = 26  # the guide's title, as on the spec (design ticket 答复第 2 件)
MENUBAR_FONT_SMALL = 11
MENUBAR_FONT_TINY = 10
MENUBAR_FONT_BODY = 13
FONT_TAG = 11           # small capsule tag (settings wall "测试" tag, views/settings_wall.py wallTag)
FONT_PROMPT_PREVIEW = 11.5  # body preview in a prompt direction cell (Prompt 页 定稿 v1); painted only
FONT_FAMILY_MONO = '"SF Mono", Menlo, Consolas, monospace'  # activity log in the prompt diagnostics (spec: 等宽小字)

# -- corner radius ------------------------------------------------------------
RADIUS_CARD = 25        # window, cards, multi-line inputs
RADIUS_CONTROL = 20     # buttons, single-line inputs, combo boxes
MENUBAR_RADIUS_BUTTON = 7
RADIUS_CONTROL_SMALL = 17   # small in-row button, half its height (dialog B v0.4: 高 34、圆角 17)
RADIUS_TAG = 6              # small capsule tag (wallTag)
RADIUS_SEGMENT = 18         # segmented control track (screen rotation, appearance.py displayRotationControl)
RADIUS_SEGMENT_OPTION = 14  # one option inside it

# -- control heights (shared components in views/ui/) ---------------------------
CONTROL_HEIGHT = 40         # buttons (dialog B v0.4: 高 40、圆角 20、最窄 88), inputs
CONTROL_HEIGHT_SMALL = 34   # small in-row button (dialog B v0.4)
CONTROL_HEIGHT_OUTLINED = CONTROL_HEIGHT - 2  # min/max-height of an outlined button: its 1 px border makes it 40 (07 单第 16 件)
BLE_HELP_MODEL_SIZE = 320   # the 3D device in 「如何用设备切换」 (design ticket 答复第 2 件: 420 → about 320)
ROUND_ARROW_SIZE = 32       # the cream round arrow at a settings tile's top right (灯光页 v0.2)
PROMPT_SAVE_INNER = CONTROL_HEIGHT - 2  # Prompt "保存到设备": 40 tall with its 1 px outline, corners RADIUS_CONTROL
SEGMENT_OPTION_HEIGHT = 30  # one option of a segmented control (screen rotation)
SEGMENT_OPTION_HEIGHT_COMPACT = 24  # the same inside a crowded wall tile (震动 opened)
RADIUS_SEGMENT_COMPACT = 15         # its track (half its height, or Qt squares the corners)
RADIUS_SEGMENT_OPTION_COMPACT = 12  # one option inside it

# -- Prompt page top row (the designer, Prompt 页定稿 v1, 5-小图形和数值表.png: 宽 848、高 44、圆角 22) --
STATUS_BAR_HEIGHT = 44
RADIUS_STATUS_BAR = 22
STATUS_BAR_ROUND = 30           # the round "重新读取" / "诊断信息" buttons (measured on the spec)
RADIUS_STATUS_BAR_ROUND = 15
STATUS_BAR_PILL_HEIGHT = 28     # "看演示" / "去允许" / "说明" (measured on the spec)
RADIUS_STATUS_BAR_PILL = 14
# Qt stylesheet sizes are the content box, without the 1 px outline: outer size minus 2.
STATUS_BAR_ROUND_INNER = STATUS_BAR_ROUND - 2
STATUS_BAR_PILL_INNER = STATUS_BAR_PILL_HEIGHT - 2
RADIUS_DIAGNOSTICS_LOG = 14     # the activity log box in the diagnostics dialog (measured on the spec)

# -- legacy (old APP_STYLE layers, moved from views/main_window.py in step 3c) --
# Still in effect, each used where the comment says. Values unchanged; these
# are the colours to retire or merge into the palette above once the old
# pages are redrawn (the designer decides what they become).
LEGACY_ROOT_COLOR = "#eee6df"  # QWidget#root color
LEGACY_WINDOW_CONTROL_CLOSE_BACKGROUND = "#ff5f57"  # QPushButton#windowControl[windowRole="close"] background
LEGACY_WINDOW_CONTROL_CLOSE_BORDER = "#e0443e"  # QPushButton#windowControl[windowRole="close"] border
LEGACY_WINDOW_CONTROL_MINIMIZE_BACKGROUND = "#febc2e"  # QPushButton#windowControl[windowRole="minimize"] background
LEGACY_WINDOW_CONTROL_MINIMIZE_BORDER = "#d99b22"  # QPushButton#windowControl[windowRole="minimize"] border
LEGACY_WINDOW_CONTROL_ZOOM_BACKGROUND = "#28c840"  # QPushButton#windowControl[windowRole="zoom"] background
LEGACY_WINDOW_CONTROL_ZOOM_BORDER = "#20a934"  # QPushButton#windowControl[windowRole="zoom"] border
LEGACY_WINDOW_CONTROL_HOVER_BORDER_COLOR = "rgba(255, 255, 255, 150)"  # QPushButton#windowControl:hover border-color
LEGACY_SHELL_BRAND_COLOR = "#fff4eb"  # QLabel#shellBrand color
LEGACY_PUSH_BUTTON_HOVER_COLOR = "#ffffff"  # QComboBox selection-color; QLabel#shortcutRecorderPreview color; QPushButton#configurationModeTab:checked color; QPushButton#onboardingSkip:hover colo
LEGACY_MODE_NORMAL_BORDER = "#665154"  # QFrame#modeNormal border
LEGACY_MODE_CODEX_BORDER = "#526d8d"  # QFrame#modeCodex border
LEGACY_STATUS_READY_COLOR = "#9ed6b6"  # QLabel#statusReady color
LEGACY_STATUS_WARN_COLOR = "#ffb29f"  # QLabel#statusWarn color; QLabel#statusWarn[compactStatus="true"] color
LEGACY_PROFILE_PILL_BORDER = "#67403a"  # QPushButton#profilePill border
LEGACY_APP_RELEASE_SUMMARY_TITLE_COLOR = "#fff0e6"  # QLabel#appReleaseSummaryTitle color
LEGACY_APP_RELEASE_HIGHLIGHT_COLOR = "#d5cbc4"  # QLabel#appReleaseHighlight color
LEGACY_ROLE_HUMAN_COLOR = "#efe4dd"  # QLabel#roleHuman color
LEGACY_ROLE_HUMAN_BACKGROUND = "#2b1e20"  # QLabel#roleHuman background
LEGACY_ROLE_HUMAN_BORDER_LEFT = "#e85b42"  # QFrame#roleHuman border-left; QLabel#roleHuman border-left
LEGACY_ROLE_AGENT_COLOR = "#c8dbf3"  # QLabel#roleAgent color
LEGACY_ROLE_AGENT_BACKGROUND = "#1d2a3a"  # QLabel#roleAgent background
LEGACY_ROLE_AGENT_BORDER_LEFT = "#7196c4"  # QFrame#roleAgent border-left; QLabel#roleAgent border-left
LEGACY_ROLE_CONTEXT_COLOR = "#f7eee8"  # QLabel#roleContext color
LEGACY_ROLE_CONTEXT_BACKGROUND = "#171113"  # QLabel#roleContext background
LEGACY_ROLE_CONTEXT_BORDER_LEFT = "#d6c9c1"  # QFrame#roleContext border-left; QLabel#roleContext border-left
LEGACY_PROMPT_DIRECTION_CARD_COLOR = "#eee3dc"  # QPushButton#promptDirectionCard color
LEGACY_MENU_BACKGROUND = "#211719"  # QMenu background
LEGACY_INPUT_DIALOG_COLOR = "#f5f5f7"  # QInputDialog QLabel color; QInputDialog QLineEdit color; QInputDialog QPushButton color; QLabel#lightingLevelValue color; QLabel#pageTitle color; QMes
LEGACY_INPUT_DIALOG_BACKGROUND = "rgba(12, 12, 14, 220)"  # QInputDialog QLineEdit background
LEGACY_INPUT_DIALOG_BACKGROUND_2 = "rgba(255, 255, 255, 18)"  # QInputDialog QPushButton background
LEGACY_INPUT_DIALOG_BORDER_COLOR = "rgba(255, 255, 255, 42)"  # QInputDialog QPushButton border-color
LEGACY_WINDOW_CHROME_TITLE_COLOR = "rgba(235, 235, 245, 150)"  # QLabel#extensionPlatformStatus color; QLabel#shortcutRecorderHeading color; QLabel#shortcutRecorderMessage color; QLabel#windowChromeTitle color
LEGACY_NAVIGATION_CAPSULE_BORDER_COLOR = "rgba(255, 255, 255, 34)"  # QFrame#navigationCapsule border-color; QPushButton#promptDirectionCard border-color
LEGACY_SHELL_EDITION_COLOR = "rgba(235, 235, 245, 138)"  # QLabel#shellEdition color
LEGACY_SETTINGS_ROW_BORDER_BOTTOM = "rgba(255, 255, 255, 22)"  # QFrame#settingsRow border-bottom
LEGACY_TOP_METRIC_BORDER_LEFT = "rgba(255, 255, 255, 26)"  # QFrame#topMetric border-left
LEGACY_MODE_NORMAL_BACKGROUND = "rgba(255, 255, 255, 24)"  # QFrame#modeNormal background
LEGACY_MODE_NORMAL_BORDER_COLOR = "rgba(255, 255, 255, 38)"  # QFrame#modeNormal border-color
LEGACY_MODE_CODEX_BACKGROUND = "rgba(74, 110, 155, 88)"  # QFrame#modeCodex background
LEGACY_MODE_CODEX_BORDER_COLOR = "rgba(133, 174, 222, 92)"  # QFrame#modeCodex border-color
LEGACY_STATUS_READY_TRUE_COLOR = "#8fd3a8"  # QLabel#statusReady[compactStatus="true"] color
LEGACY_STATUS_ERROR_TRUE_COLOR = "#ffaaa2"  # QLabel#statusError[compactStatus="true"] color
LEGACY_MESSAGE_BOX_BACKGROUND_COLOR = "#252527"  # QMessageBox background-color
LEGACY_MESSAGE_BOX_BACKGROUND = "#3a3a3c"  # QMessageBox QPushButton background
LEGACY_MESSAGE_BOX_BORDER = "rgba(255, 255, 255, 44)"  # QMessageBox QPushButton border
LEGACY_MESSAGE_BOX_HOVER_BACKGROUND = "#48484a"  # QMessageBox QPushButton:hover background
LEGACY_MESSAGE_BOX_HOVER_BORDER_COLOR = "rgba(255, 255, 255, 74)"  # QMessageBox QPushButton:hover border-color
LEGACY_NAV_ICON_BUTTON_COLOR = "rgba(224, 222, 211, 148)"  # QPushButton#navIconButton color
LEGACY_NAV_ICON_BUTTON_TRUE_COLOR = "rgba(255, 254, 248, 245)"  # QPushButton#navIconButton[active="true"] color
LEGACY_TOP_BAR_BORDER_BOTTOM = "rgba(225, 238, 244, 22)"  # QFrame#topBar border-bottom
LEGACY_DEVICE_PANEL_NAME_COLOR = "#f2f1e8"  # QLabel#devicePanelName color
LEGACY_DEVICE_PANEL_VALUE_COLOR = "rgba(244, 243, 234, 220)"  # QLabel#devicePanelValue color
LEGACY_DEVICE_STATUS_ROW_BORDER_TOP = "rgba(228, 226, 211, 24)"  # QFrame#deviceStatusRow border-top
LEGACY_DEVICE_STAGE_NAME_COLOR = "rgba(217, 218, 208, 105)"  # QLabel#deviceStageName color
LEGACY_ROLE_HUMAN_BORDER_TOP = "rgba(228, 226, 211, 26)"  # QFrame#roleHuman border-top
LEGACY_ROLE_HUMAN_COLOR_2 = "rgba(236, 235, 226, 190)"  # QFrame#roleHuman QLabel color
LEGACY_OPEN_ONBOARDING_GUIDE_BACKGROUND = "rgba(245, 244, 236, 10)"  # QPushButton#openOnboardingGuide background; QPushButton#secondary background
LEGACY_OPEN_ONBOARDING_GUIDE_BORDER = "rgba(245, 244, 236, 46)"  # QPushButton#openOnboardingGuide border
LEGACY_OPEN_ONBOARDING_GUIDE_HOVER_BACKGROUND = "rgba(245, 244, 236, 24)"  # QPushButton#openOnboardingGuide:hover background
LEGACY_OPEN_ONBOARDING_GUIDE_HOVER_BORDER_COLOR = "rgba(245, 244, 236, 86)"  # QPushButton#openOnboardingGuide:hover border-color
LEGACY_OPEN_ONBOARDING_GUIDE_PRESSED_BACKGROUND = "rgba(255, 106, 0, 54)"  # QPushButton#openOnboardingGuide:pressed background
LEGACY_OPEN_ONBOARDING_GUIDE_PRESSED_BORDER_COLOR = "rgba(255, 106, 0, 112)"  # QPushButton#openOnboardingGuide:pressed border-color
LEGACY_BLE_SLOTS_DISCLOSURE_COLOR = "#b8b6ad"  # QPushButton#bleSlotsDisclosure color
LEGACY_BLE_SLOTS_DISCLOSURE_HOVER_BACKGROUND = "rgba(245, 244, 236, 12)"  # QPushButton#bleSlotsDisclosure:hover background
LEGACY_BLE_SLOTS_DISCLOSURE_HOVER_COLOR = "#ece8de"  # QPushButton#bleSlotsDisclosure:hover color
LEGACY_PRIMARY_BORDER_COLOR = "#FF8C3A"  # QPushButton#primary border-color
LEGACY_SECONDARY_BORDER_COLOR = "rgba(245, 244, 236, 36)"  # QPushButton#secondary border-color
LEGACY_CARD_BACKGROUND = "rgba(19, 20, 18, 210)"  # QFrame#card background
LEGACY_CONFIGURATION_MODE_TAB_CHECKED_BACKGROUND = "rgba(255, 106, 0, 46)"  # QPushButton#configurationModeTab:checked background
LEGACY_CONFIGURATION_MODE_TAB_CHECKED_BORDER_COLOR = "rgba(255, 106, 0, 92)"  # QPushButton#configurationModeTab:checked border-color
LEGACY_GROUP_BOX_BACKGROUND = "rgba(19, 20, 18, 116)"  # QGroupBox background
LEGACY_GROUP_BOX_TITLE_COLOR = "rgba(239, 238, 229, 198)"  # QGroupBox::title color

# -- motion (design decision; the designer 11-19/20, design ticket 答复第 1 件) ------
# Four durations. Two curves: coming in and pressing use OutCubic, two pictures
# taking turns use InOutCubic (the names are QEasingCurve.Type names; the
# helper in views/ui/motion.py turns them into curves). Loops (spin, blink,
# chase, cursor, auto-rotation) and the Mode page wave keep their own timing.
MOTION_INSTANT_MS = 120                  # 瞬时: fades, colour changes, a picture swapping
MOTION_SHORT_MS = 180                    # 短: press and bounce back, snap, one step of dots
MOTION_MEDIUM_MS = 260                   # 中: cross-fades between two pictures, settling back
MOTION_LONG_MS = 460                     # 长: camera moves, entrances, a whole page arriving
MOTION_CURVE_IN = "OutCubic"             # coming in, pressing
MOTION_CURVE_SWAP = "InOutCubic"         # two pictures taking turns

# Dot matrix slider (-12 答复第 2 件, "照四档").
DOT_SLIDER_RISE_MS = MOTION_SHORT_MS     # the bulge appearing
DOT_SLIDER_SETTLE_MS = MOTION_MEDIUM_MS  # the bulge settling back
DOT_SLIDER_SNAP_MS = MOTION_SHORT_MS     # a five-stop slider sliding onto its stop

# Page change (design decision, changed after the designer 11-20 真屏验收: the page is empty
# and its cards slide in from the side of the tab you went to, "按页签方向").
# 8 / 48 / 0.20 s are the Agent's values, not tried by hand yet.
PAGE_OLD_SHIFT = 8                       # the old page slides this far the other way while fading
PAGE_SLIDE_DISTANCE = 48                 # each card slides in from this far out on the tab's side
PAGE_FADE_SHARE = 1 / 3                  # a card is fully shown after a third of its slide
PAGE_STAGGER_MS = 200                    # last card starts this much later than the first

# Banners slide in from the right (-15 第 3 处, 丙-3) and line up with the wall (-08 第 4 件).
BANNER_OVERSHOOT = 10                    # slides about 10 past its place, then back
BANNER_SAVED_HIDE_MS = 1600              # "已存进设备" stays this long, then goes (-15 第 1 处)
BANNER_SAVED_RISE = 6                    # ... fading out while rising this much
BANNER_FAILED_HIDE_MS = 4000             # "豆包没有响应" goes by itself after 4 s (-15 第 2 处)
# A banner changing what it says changes length like the iPhone Dynamic Island
# (the designer 11-20 真屏验收): the pill stretches or shrinks with a small bounce, the
# old words fade first and the new ones come in once there is room.
BANNER_MORPH_MS = MOTION_LONG_MS         # the whole change, bounce included
BANNER_MORPH_OVERSHOOT = 6               # the end runs at most this far past, then back
BANNER_MORPH_OLD_SHARE = 0.35            # old words gone within the first 35 %
BANNER_MORPH_NEW_FROM = 0.3              # new words start at 30 % ...
BANNER_MORPH_NEW_SHARE = 0.5             # ... and are fully in 50 % later

# 「让你的 AI 先帮忙查」 (自助诊断提示词, design ticket).
DIAGNOSIS_STEP_GAP = 16                  # between the three steps
DIAGNOSIS_STEP_TEXT_GAP = 10             # step number to its words; the kind buttons line up with the words
DIAGNOSIS_KIND_GAP = 8                   # between the problem-kind buttons
DIAGNOSIS_COPIED_HOLD_MS = 2400          # the pressed button says "已复制…" this long, then its name again

# Check mark sizes: only three in the whole Console (the designer 11-20 真屏验收: "页面里绿勾的
# 大小要统一一下……只做 2 到 3 种不同的大小"; the Prompt top bar's 8 → 16, his choice).
CHECK_SIZE_LARGE = 20                    # banners
CHECK_SIZE = 16                          # rows, bars, the 48 ring, the firmware steps
CHECK_SIZE_SMALL = 12                    # small places: the 36 ring, diagnostics rows

# Check mark pop when permission comes back (-15 第 5 处): 0.6 → 1.12 → 1.
CHECK_POP_FROM = 0.6
CHECK_POP_PEAK = 1.12

# "待处理" light blinking twice before staying on (-15 第 7 处; borrowed from the
# device's two-buzz haptic: 250 ms on, 300 ms off). Console-only: the firmware
# light stays on without blinking.
ATTENTION_BLINK_ON_MS = 250
ATTENTION_BLINK_OFF_MS = 300

# Lighting tile opening wide (-08 答复第 3 件; Agent values the designer approved in the demo).
TILE_OPEN_MS = 240
TILE_OPEN_OVERSHOOT = 50                 # the right edge runs about 50 past, then back
TILE_OPEN_CURVE = (0.3, 2.3, 0.55, 1.0)  # cubic-bezier of the bounce
TILE_CONTENT_OUT_SHARE = 0.3             # closing: the open content is gone in the first 30 %
TILE_SHADOW = (0, 0, 0, 90)              # faint shadow of an open tile
TILE_SHADOW_BLUR = 24
TILE_SHADOW_OFFSET = 6                   # ... cast this far down

# 全部方案: press a screen card and drag it to a new place (design decision, 单 design ticket
# 第 2 节). the designer settled the look in design decision (单 design ticket 答复): keep 1.04 and
# 中 0.26 s; no black shadow (it hardly showed on the dark page), the outline brightens one
# step instead, the same step as a wall tile lying open over its neighbour (design decision).
OVERVIEW_LIFT_SCALE = 1.04               # a picked-up card grows this much
OVERVIEW_LIFT_EDGE = WALL_EDGE_RAISED    # ... and its outline brightens to this (from MODE_BAR_EDGE)
OVERVIEW_LIFT_MS = MOTION_SHORT_MS       # picking it up
OVERVIEW_SETTLE_MS = MOTION_MEDIUM_MS    # putting it down, and the others making room
OVERVIEW_AUTOSCROLL_EDGE = 48            # holding a card this close to an edge scrolls the row
OVERVIEW_AUTOSCROLL_STEP = 12            # ... this far per frame

# Connecting steps in the Mode bar's right cell (design decision, 单 design ticket 答复 11-22;
# the Agent's values, approved with the v1 定稿 demo). The cell changes width like the iPhone
# Dynamic Island: a spring of about 0.46 s running about 3 % past; old words rise and fade,
# new ones rise in from below; title and hint each on their own.
CONNECT_STEPS_SPRING_MS = MOTION_LONG_MS     # 0.46 s
CONNECT_STEPS_OVERSHOOT = 0.03               # of the cell's new width
CONNECT_STEPS_TEXT_OUT_MS = 140              # old words gone
CONNECT_STEPS_TEXT_IN_MS = 220               # new words in
CONNECT_STEPS_TEXT_RISE = 6                  # how far words move while they fade (at scale 1)
CONNECT_STEPS_CHECK_MS = 120                 # ring all lit → green check
CONNECT_STEPS_RING = 36                      # the 16-dot ring (design decision)
CONNECT_STEPS_HEAD_GLOW_FLOOR = 0.5          # the reached dot breathes no dimmer than half lit
# The cream "蓝牙连接" button folds into the round button where it stands when connecting
# starts: same spring as the steps cell; its words are gone in the first part, the cream
# turns round-button grey over the next.
BLE_FOLD_MS = CONNECT_STEPS_SPRING_MS
BLE_FOLD_TEXT_SHARE = 0.35                   # of BLE_FOLD_MS: words faded out
BLE_FOLD_COLOUR_SHARE = 0.6                  # of BLE_FOLD_MS: cream → MODE_BAR_ROUND

QSS = {
    name: str(value)
    for name, value in globals().items()
    if name.isupper() and isinstance(value, (str, int))
}


def stylesheet(template: str) -> str:
    """Fill `$NAME` / `${NAME}` placeholders in a QSS template from the tokens.

    `Template` keeps QSS braces intact; an unknown name raises KeyError, so a
    typo fails at import instead of silently painting nothing.
    """
    return Template(template).substitute(QSS)
