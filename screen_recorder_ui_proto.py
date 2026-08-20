# -*- coding: utf-8 -*-
"""
轻量录屏 · 布局原型（UI 仅演示，不接真实录制）
========================================================
苏小蓝提议的「左侧导航 + 右侧详情」现代布局验证版：
- 无边框圆角窗口 + 柔和投影（沿用正式版质感）
- 顶部菜单栏仅保留红绿灯（程序名已移至左侧导航顶部的品牌区：图标 + 轻量录屏 + ScreenRecorder）
- 上方区域：左侧导航（录制范围 / 录制参数 / 偏好 / 保存位置，精致选中态）+ 右侧详情（QStackedWidget 切换）
- 下方区域：保留原布局卡片的「录制区」——状态 + 当前配置摘要 + 开始录制按钮
- 复用正式版的浅色 macOS 主题、字号与卡片样式；控件不绑定真实逻辑

运行：双击 run_ui_proto.bat  或  python screen_recorder_ui_proto.py
"""

import ctypes
import ctypes.wintypes as wt
import hashlib
import os
import subprocess
import sys


# 自定义 Windows MSG 结构，避免不同 PySide6/ctypes 版本下解析差异。
class _NativeMSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_size_t),
        ("time", ctypes.c_ulong),
        ("pt_x", ctypes.c_long),
        ("pt_y", ctypes.c_long),
    ]

from PySide6.QtCore import (
    Qt, QRectF, QRect, QPoint, Signal, QUrl, QEvent, QFileInfo, QTimer,
)
from PySide6.QtGui import (
    QIcon, QFont, QColor, QPainter, QPen, QBrush, QPixmap, QPainterPath,
    QDesktopServices, QMouseEvent, QCursor,
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QComboBox, QPushButton, QLineEdit, QCheckBox,
    QFrame, QVBoxLayout, QHBoxLayout, QGridLayout, QStackedWidget, QScrollArea,
    QGraphicsDropShadowEffect, QSizePolicy, QMessageBox, QLayout,
    QInputDialog, QFileIconProvider,
)

# ----------------------------- 主题（浅色 macOS 风，与正式版一致） -----------------------------
THEME = {
    "bg": "#f2f2f7",
    "card": "#ffffff",
    "text": "#1d1d1f",
    "sub": "#8e8e93",
    "accent": "#0a84ff",
    "rec": "#ff3b30",
    "rec_dark": "#d70015",
    "ok": "#34c759",
    "line": "#e3e3e8",
    "section_bg": "#f8f8fa",
    "section_border": "#ececf0",
    "input_bg": "#ffffff",
    "input_border": "#e3e3e8",
    "hover_bg": "#e9e9ee",
    "hover_border": "#d8d8dd",
    "pressed_bg": "#d9d9de",
    "selected_bg": "#d6e7ff",
    "selected_border": "#a8caff",
    "disabled_text": "#b0b0b5",
    "history_row_bg": "#fbfbfd",
    "history_row_border": "#e6e6ec",
    "history_thumb_bg": "#f0f0f5",
    "danger_bg": "#ffe5e3",
    "danger_text": "#d70015",
    "menu_bg": "#f9f9fb",
    "menu_border": "#d8d8dd",
    "scroll_handle": "#d1d1d6",
    "checkbox_border": "#c7c7cc",
}

# 字号（与正式版一致）
UI_FONT_SIZE_PT = 12
UI_FONT_LETTER_SPACING = 0.3
# 控件统一宽度（便于上下对齐 / 后续移植）
CTRL_BTN_W = 56


def _heading_font(size_pt, weight=QFont.Weight.Medium):
    f = QFont()
    try:
        from PySide6.QtGui import QFontDatabase
        f.setFamilies(["PingFang SC", "Microsoft YaHei", "Segoe UI", "system-ui",
                       "Helvetica Neue", "Arial"])
    except Exception:
        f.setFamily("Microsoft YaHei")
    f.setPointSize(size_pt)
    f.setWeight(weight)
    f.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    try:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, UI_FONT_LETTER_SPACING)
    except Exception:
        pass
    return f


def _app_icon():
    try:
        base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(base, "assets", "icon.png")
        if os.path.exists(path):
            return QIcon(path)
    except Exception:
        pass
    pm = QPixmap(32, 32)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QBrush(QColor(THEME["rec"])))
    p.setPen(Qt.NoPen)
    p.drawEllipse(4, 4, 24, 24)
    p.end()
    return QIcon(pm)


def _load_save_dir():
    """尝试读取正式程序 config.json 的 save_dir；失败则回退到默认桌面录屏目录。"""
    try:
        base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
        cfg_path = os.path.join(base, "ScreenRecorder_app", "config.json")
        if os.path.exists(cfg_path):
            import json
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            d = cfg.get("save_dir", "")
            if d and os.path.isdir(d):
                return d
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "Desktop", "录屏")


def _fmt_size(n):
    """人类可读的文件大小。"""
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024
        i += 1
    return f"{n:.1f} {units[i]}"


def _ffmpeg_path():
    """寻找可用的 ffmpeg 可执行文件。"""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(base, "ffmpeg.exe"),
        os.path.join(base, "ScreenRecorder_app", "ffmpeg.exe"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    # 尝试 PATH
    for name in ("ffmpeg.exe", "ffmpeg"):
        for path_dir in os.environ.get("PATH", "").split(os.pathsep):
            p = os.path.join(path_dir, name)
            if os.path.exists(p):
                return p
    return None


def _thumb_cache_dir():
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    d = os.path.join(base, ".proto_thumbs")
    os.makedirs(d, exist_ok=True)
    return d


def _video_thumb(path):
    """用 ffmpeg 提取视频第 1 秒画面作为缩略图，返回 QPixmap（失败返回 None）。"""
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        return None
    key = hashlib.md5((path + str(os.path.getmtime(path))).encode("utf-8")).hexdigest()
    out = os.path.join(_thumb_cache_dir(), f"{key}.jpg")
    if os.path.exists(out):
        pm = QPixmap(out)
        if not pm.isNull():
            return pm
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-ss", "00:00:01", "-i", path,
        "-vf", "scale=176:112:force_original_aspect_ratio=decrease,pad=176:112:(ow-iw)/2:(oh-ih)/2:black",
        "-frames:v", "1", "-q:v", "2", out,
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8)
        if result.returncode == 0 and os.path.exists(out):
            pm = QPixmap(out)
            if not pm.isNull():
                return pm
    except Exception:
        pass
    return None


# ----------------------------- 简单图标绘制（左侧导航用） -----------------------------
class NavIcon(QLabel):
    """根据 key 用 QPainter 画一个 20x20 的单色图标。"""

    KEYS = ("range", "params", "pref", "save", "history")

    def __init__(self, key, color="#8e8e93", parent=None):
        super().__init__(parent)
        self.key = key
        self.color = QColor(color)
        self.setFixedSize(22, 22)

    def set_color(self, color):
        self.color = QColor(color)
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = self.color
        pen = QPen(c)
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.RoundCap)
        w, h = self.width(), self.height()
        if self.key == "range":  # 屏幕 + 内嵌框
            pen.setStyle(Qt.SolidLine)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(3, 4, w - 6, h - 8, 2, 2)
            pen.setWidthF(1.2)
            p.setPen(pen)
            p.drawRoundedRect(7, 8, w - 14, h - 16, 1.5, 1.5)
        elif self.key == "params":  # 两条滑块
            p.setPen(pen)
            p.setBrush(c)
            y1, y2 = 8, 15
            p.drawLine(4, y1, 16, y1)
            p.drawLine(6, y2, 18, y2)
            p.setPen(Qt.NoPen)
            p.drawEllipse(13, y1 - 2.4, 4.8, 4.8)
            p.drawEllipse(3, y2 - 2.4, 4.8, 4.8)
        elif self.key == "pref":  # 齿轮（圆 + 齿）
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            cx, cy = w / 2, h / 2
            p.drawEllipse(cx - 4, cy - 4, 8, 8)
            p.drawEllipse(cx - 1.6, cy - 1.6, 3.2, 3.2)
            import math
            for i in range(8):
                a = math.pi / 4 * i
                x1 = cx + math.cos(a) * 6.5
                y1 = cy + math.sin(a) * 6.5
                x2 = cx + math.cos(a) * 9.5
                y2 = cy + math.sin(a) * 9.5
                p.drawLine(x1, y1, x2, y2)
        elif self.key == "save":  # 文件夹
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(3, 7, w - 6, h - 10, 2, 2)
            p.drawLine(3, 10, 9, 10)
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(6, 13, w - 12, 3, 1, 1)
        elif self.key == "history":  # 时钟（历史记录）
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            cx, cy = w / 2, h / 2
            p.drawEllipse(cx - 6, cy - 6, 12, 12)
            p.setPen(pen)
            p.drawLine(cx, cy, cx, cy - 4.5)
            p.drawLine(cx, cy, cx + 3, cy + 1.5)
        p.end()


# ----------------------------- 录制圆环（还原正式版 RecordingRing 画法） -----------------------------
class ProtoRing(QWidget):
    """底部录制区左侧的圆形录制按钮，忠实还原正式版 RecordingRing 的静态外观。"""
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(116, 116)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.clicked.emit()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        cx = cy = w / 2.0
        R = w / 2.0 - 16

        def ellipse(radius):
            return QRectF(cx - radius, cy - radius, radius * 2, radius * 2)

        # 轨道圆环
        p.setPen(QPen(QColor(THEME["line"]), 7, Qt.SolidLine, Qt.RoundCap))
        p.drawEllipse(ellipse(R))
        # 中心红色圆点（开始按钮隐喻，idle 态）
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(THEME["rec"]))
        p.drawEllipse(ellipse(R - 16))
        p.end()


# ----------------------------- 标题栏 -----------------------------
class TitleBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.setFixedHeight(46)
        self.setStyleSheet("background:transparent;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 0, 14, 0)
        lay.setSpacing(8)

        for color, fn in (
                ("#ff5f57", lambda: QApplication.quit()),
                ("#febc2e", self.parent.showMinimized if parent else (lambda: None)),
                ("#28c840", self._toggle_max)):
            b = QPushButton()
            b.setFixedSize(14, 14)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                f"QPushButton{{background:{color};border:none;border-radius:7px;}}"
                f"QPushButton:hover{{border:1px solid rgba(0,0,0,0.15);}}")
            b.clicked.connect(fn)
            lay.addWidget(b)
        lay.addSpacing(10)
        lay.addStretch(1)
        self._drag = None

    def _toggle_max(self):
        try:
            if self.parent.isMaximized():
                self.parent.showNormal()
            else:
                self.parent.showMaximized()
        except Exception:
            pass

    def mouseDoubleClickEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._toggle_max()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._drag = ev.globalPosition().toPoint() - self.parent.pos()

    def mouseMoveEvent(self, ev):
        if self._drag is not None:
            self.parent.move(ev.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, ev):
        self._drag = None


# ----------------------------- 左侧导航项 -----------------------------
class NavItem(QWidget):
    """左侧导航项：选中态由 paintEvent 统一绘制药丸背景（完整覆盖图标+文字），
    并在左侧绘制圆角强调竖条，避免 QSS 动态属性在某些 DPI/样式下不生效。"""
    clicked = Signal(int)

    def __init__(self, index, key, label, parent=None):
        super().__init__(parent)
        self.index = index
        self.key = key
        self._selected = False
        self._hover = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(44)
        self.icon = NavIcon(key)
        self.icon.setStyleSheet("background:transparent;")
        self.label = QLabel(label)
        self.label.setFont(_heading_font(13, QFont.Weight.Medium))
        self.label.setStyleSheet(f"background:transparent;color:{THEME['text']};")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(12)
        lay.addWidget(self.icon)
        lay.addWidget(self.label)
        lay.addStretch(1)
        self._apply()

    def set_selected(self, v):
        self._selected = v
        self._apply()
        self.update()

    def _apply(self):
        if self._selected:
            self.label.setStyleSheet(f"background:transparent;color:{THEME['accent']};")
            self.icon.set_color(THEME["accent"])
        else:
            self.label.setStyleSheet(f"background:transparent;color:{THEME['text']};")
            self.icon.set_color(THEME["sub"])

    def enterEvent(self, ev):
        self._hover = True
        self.update()
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._hover = False
        self.update()
        super().leaveEvent(ev)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect()
        # 药丸底：紧贴整个项，完整覆盖图标+文字
        pill = r.adjusted(3, 3, -3, -3)
        if self._selected:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(THEME["selected_bg"]))
            p.drawRoundedRect(pill, 10, 10)
            # 左侧圆角强调竖条（药丸形）
            p.setBrush(QColor(THEME["accent"]))
            p.drawRoundedRect(QRectF(7, (r.height() - 18) / 2.0, 4, 18), 2, 2)
        elif self._hover:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(THEME["hover_bg"]))
            p.drawRoundedRect(pill, 10, 10)
        p.end()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.clicked.emit(self.index)


# ----------------------------- 通用小组件 -----------------------------
def _combo(items, current):
    c = QComboBox()
    c.addItems(items)
    if current in items:
        c.setCurrentText(current)
    c.setMinimumHeight(32)
    return c


def _labeled(text, widget, lw=56):
    row = QHBoxLayout()
    row.setSpacing(8)
    lbl = QLabel(text)
    lbl.setFixedWidth(lw)
    lbl.setStyleSheet(f"color:{THEME['sub']};font-size:10pt;")
    row.addWidget(lbl)
    if isinstance(widget, QLayout):
        row.addLayout(widget, 1)
    else:
        row.addWidget(widget, 1)
    return row


def _row2(a, b):
    row = QHBoxLayout()
    row.setSpacing(14)
    row.addLayout(a)
    row.addLayout(b)
    return row


def _section_card(title):
    sec = QWidget()
    sec.setObjectName("sectionCard")
    sec.setStyleSheet(
        f"#sectionCard{{background:{THEME['section_bg']};"
        f"border:1px solid {THEME['section_border']};border-radius:14px;}}")
    lay = QVBoxLayout(sec)
    lay.setContentsMargins(18, 14, 18, 14)
    lay.setSpacing(12)
    t = QLabel(title)
    t.setStyleSheet(f"color:{THEME['text']};")
    t.setFont(_heading_font(15, QFont.Weight.Medium))
    lay.addWidget(t)
    return sec, lay


# ----------------------------- 右侧四个详情页 -----------------------------
class PageRange(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(14)
        sec, slay = _section_card("录制范围")
        seg = QHBoxLayout()
        seg.setSpacing(6)
        self.btns = {}
        for key, label in (("full", "全屏"), ("full_all", "全屏(多屏)"),
                           ("region", "区域"), ("window", "窗口")):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setFixedHeight(34)
            b.setCursor(Qt.PointingHandCursor)
            seg.addWidget(b, 1)
            self.btns[key] = b
        self.btns["full"].setChecked(True)
        slay.addLayout(seg)

        hint = QLabel("全屏模式无需额外设置")
        hint.setStyleSheet(f"color:{THEME['sub']};font-size:10pt;")
        slay.addWidget(hint)

        # 区域选择行
        rl = QHBoxLayout()
        rl.setSpacing(8)
        rlbl = QLabel("未选择区域")
        rlbl.setStyleSheet(f"color:{THEME['sub']};font-size:10pt;")
        rbtn = QPushButton("选择区域")
        rbtn.setFixedHeight(30)
        rl.addWidget(rlbl, 1)
        rl.addWidget(rbtn)
        slay.addLayout(rl)

        # 窗口选择行
        wl = QHBoxLayout()
        wl.setSpacing(8)
        wcombo = _combo(["（点击刷新窗口列表）"], "（点击刷新窗口列表）")
        wcombo.setMinimumHeight(30)
        wbtn = QPushButton("刷新")
        wbtn.setFixedHeight(30)
        wbtn.setFixedWidth(CTRL_BTN_W)
        wl.addWidget(wcombo, 1)
        wl.addWidget(wbtn)
        slay.addLayout(wl)
        lay.addWidget(sec)
        lay.addStretch(1)


class PageParams(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(14)
        sec, slay = _section_card("录制参数")

        # 统一网格：列 = label | control | label | control | button
        # 同列控件宽度严格一致，刷新/设置按钮在最右列上下对齐。
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 0)
        grid.setColumnStretch(3, 1)
        grid.setColumnStretch(4, 0)

        def _lb(text):
            lbl = QLabel(text)
            lbl.setFixedWidth(56)
            lbl.setStyleSheet(f"color:{THEME['sub']};font-size:10pt;")
            return lbl

        def _ctrl_btn(text):
            b = QPushButton(text)
            b.setFixedHeight(32)
            b.setFixedWidth(CTRL_BTN_W)
            return b

        # Row 0: 帧率 / 画质
        fps = _combo(["10", "15", "24", "30", "60"], "30")
        quality = _combo(["近无损 (CRF 1)", "极清 (CRF 12)", "超清 (CRF 18)",
                          "高清 (CRF 23)", "标准 (CRF 28)", "流畅 (CRF 34)"], "高清 (CRF 23)")
        grid.addWidget(_lb("帧率"), 0, 0)
        grid.addWidget(fps, 0, 1)
        grid.addWidget(_lb("画质"), 0, 2)
        grid.addWidget(quality, 0, 3)

        # Row 1: 格式 / 音频 | 刷新按钮与音频下拉相邻，上下和"设置"对齐
        audio = _combo(["无音频", "系统声音（立体声混音）", "系统声音 + 麦克风混音"], "无音频")
        self.audio_combo = audio
        audio_refresh = _ctrl_btn("刷新")
        fmt = _combo(["MP4 (H.264, 推荐)", "MKV", "AVI", "MOV", "GIF (动图)"], "MP4 (H.264, 推荐)")
        grid.addWidget(_lb("格式"), 1, 0)
        grid.addWidget(fmt, 1, 1)
        grid.addWidget(_lb("音频"), 1, 2)
        grid.addWidget(audio, 1, 3)
        grid.addWidget(audio_refresh, 1, 4)

        # Row 2: 麦克风（动态显隐），整行跨所有列
        self.mic_combo = _combo(["（自动选择）", "默认麦克风", "麦克风阵列 (Cirrus)"], "（自动选择）")
        self.mic_widget = QWidget()
        mic_lay = QHBoxLayout(self.mic_widget)
        mic_lay.setContentsMargins(0, 0, 0, 0)
        mic_lay.setSpacing(10)
        mic_lay.addWidget(_lb("麦克风"))
        mic_lay.addWidget(self.mic_combo, 1)
        self.mic_widget.hide()
        grid.addWidget(self.mic_widget, 2, 0, 1, 5)
        audio.currentTextChanged.connect(
            lambda t: self.mic_widget.setVisible(t == "系统声音 + 麦克风混音"))

        # Row 3: 开始延迟 / 快捷键 | 设置按钮在最右列与刷新按钮上下对齐
        delay = _combo(["0.5 秒", "1 秒", "2 秒", "3 秒"], "1 秒")
        hk = QLineEdit("F9")
        hk.setReadOnly(True)
        hk.setMinimumHeight(32)
        hk_set = _ctrl_btn("设置")
        grid.addWidget(_lb("开始延迟"), 3, 0)
        grid.addWidget(delay, 3, 1)
        grid.addWidget(_lb("快捷键"), 3, 2)
        grid.addWidget(hk, 3, 3)
        grid.addWidget(hk_set, 3, 4)

        slay.addLayout(grid)
        slay.addStretch(1)
        lay.addWidget(sec)
        lay.addStretch(1)


class PagePref(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(14)
        sec, slay = _section_card("偏好")
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        items = ["全局深色模式", "开机自启", "显示悬浮控制窗",
                 "完成后自动打开文件夹", "悬浮框常驻（录制结束不返回主界面）"]
        for i, text in enumerate(items):
            c = QCheckBox(text)
            c.setFixedHeight(30)
            r, col = (i // 2, i % 2) if i < 4 else (2, 0)
            if i < 4:
                grid.addWidget(c, r, col)
            else:
                grid.addWidget(c, r, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        slay.addLayout(grid)
        slay.addStretch(1)
        lay.addWidget(sec)
        lay.addStretch(1)


class PageSave(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(14)
        sec, slay = _section_card("保存位置")
        path = QLineEdit(os.path.join(os.path.expanduser("~"), "Videos", "轻量录屏"))
        path.setReadOnly(True)
        path.setMinimumHeight(32)
        browse = QPushButton("浏览")
        browse.setFixedHeight(32)
        browse.setFixedWidth(64)
        pr = QHBoxLayout()
        pr.setSpacing(8)
        pr.addWidget(path, 1)
        pr.addWidget(browse)
        slay.addLayout(pr)
        note = QLabel("文件将保存为 MP4（H.264），命名形如 2026-08-17_21-30-05.mp4")
        note.setStyleSheet(f"color:{THEME['sub']};font-size:10pt;")
        slay.addWidget(note)
        slay.addStretch(1)
        lay.addWidget(sec)
        lay.addStretch(1)


# ----------------------------- 录制文件 / 历史记录 -----------------------------
_VIDEO_EXT = (".mp4", ".mkv", ".avi", ".mov", ".gif", ".webm", ".flv", ".m4v")


class HistoryRow(QWidget):
    """单条录制文件：缩略图 + 文件名（可内联编辑）+ 大小/日期 + 预览 / 重命名 / 删除。"""

    def __init__(self, path, page, parent=None):
        super().__init__(parent)
        self.path = path
        self.page = page
        self.setObjectName("histRow")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet(
            f"#histRow{{background:{THEME['history_row_bg']};"
            f"border:1px solid {THEME['history_row_border']};border-radius:12px;}}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 11, 14, 11)
        lay.setSpacing(12)

        # 缩略图：先显示系统图标占位，再用 QTimer 异步提取真实视频帧，避免阻塞 UI
        self.thumb = QLabel()
        self.thumb.setFixedSize(88, 56)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet(
            f"background:{THEME['history_thumb_bg']};border-radius:8px;")
        self._set_fallback_thumb()
        QTimer.singleShot(0, self._load_real_thumb)
        lay.addWidget(self.thumb)

        # 文件名（可切换为编辑框）+ 元信息
        info = QVBoxLayout()
        info.setSpacing(4)
        self.name_label = QLabel(os.path.basename(path))
        self.name_label.setStyleSheet("color:#1d1d1f;font-size:11pt;font-weight:600;background:transparent;")
        self.name_label.setWordWrap(False)
        self.name_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.name_edit = QLineEdit(os.path.basename(path))
        self.name_edit.setStyleSheet(
            "color:#1d1d1f;font-size:11pt;font-weight:600;"
            "background:#ffffff;border:1px solid #0a84ff;border-radius:5px;padding:2px 6px;")
        self.name_edit.hide()
        self.name_edit.installEventFilter(self)
        meta = QLabel(self._meta())
        meta.setStyleSheet(f"color:{THEME['sub']};font-size:9.5pt;background:transparent;")
        info.addWidget(self.name_label)
        info.addWidget(self.name_edit)
        info.addWidget(meta)
        lay.addLayout(info, 1)

        self.b_prev = QPushButton("预览")
        self.b_rename = QPushButton("重命名")
        self.b_del = QPushButton("删除")
        for b in (self.b_prev, self.b_rename, self.b_del):
            b.setFixedHeight(30)
            b.setCursor(Qt.PointingHandCursor)
        self.b_prev.setFixedWidth(64)
        self.b_rename.setFixedWidth(72)
        self.b_del.setFixedWidth(64)
        self.b_del.setStyleSheet(
            f"QPushButton{{background:{THEME['danger_bg']};color:{THEME['danger_text']};}}"
            f"QPushButton:hover{{background:#ffd2ce;}}")
        lay.addWidget(self.b_prev)
        lay.addWidget(self.b_rename)
        lay.addWidget(self.b_del)
        self.b_prev.clicked.connect(self._preview)
        self.b_rename.clicked.connect(self._start_rename)
        self.b_del.clicked.connect(self._delete)

    def _set_fallback_thumb(self):
        provider = QFileIconProvider()
        icon = provider.icon(QFileInfo(self.path))
        icon_pm = icon.pixmap(40, 40)
        if icon_pm and not icon_pm.isNull():
            self.thumb.setPixmap(icon_pm.scaled(40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _load_real_thumb(self):
        thumb_pm = _video_thumb(self.path)
        if thumb_pm and not thumb_pm.isNull():
            self.thumb.setPixmap(thumb_pm.scaled(88, 56, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))

    def eventFilter(self, obj, ev):
        if obj is self.name_edit and ev.type() == QEvent.KeyPress:
            if ev.key() == Qt.Key_Escape:
                self.name_edit.setText(os.path.basename(self.path))
                self._finish_rename()
                return True
            if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
                self._finish_rename()
                return True
        return super().eventFilter(obj, ev)

    def _meta(self):
        try:
            st = os.stat(self.path)
            dt = __import__("datetime").datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
            return f"{_fmt_size(st.st_size)} · {dt}"
        except Exception:
            return ""

    def _preview(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.path))

    def _start_rename(self):
        self.name_label.hide()
        self.name_edit.show()
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def _finish_rename(self):
        if not self.name_edit.isVisible():
            return
        new = self.name_edit.text().strip()
        base = os.path.basename(self.path)
        if new and new != base:
            if not os.path.splitext(new)[1]:
                new += os.path.splitext(base)[1]
            dst = os.path.join(os.path.dirname(self.path), new)
            if os.path.exists(dst):
                QMessageBox.warning(self, "重命名", "目标文件名已存在。")
                self.name_edit.setText(base)
            else:
                try:
                    os.rename(self.path, dst)
                    self.path = dst
                    self.name_label.setText(new)
                    self.name_edit.setText(new)
                except Exception as e:
                    QMessageBox.warning(self, "重命名", f"重命名失败：{e}")
                    self.name_edit.setText(base)
        self.name_edit.hide()
        self.name_label.show()

    def _delete(self):
        if QMessageBox.question(
                self, "删除",
                f"确定删除 {os.path.basename(self.path)}？\n（将从磁盘直接删除）",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes:
            try:
                os.remove(self.path)
                self.page.refresh()
            except Exception as e:
                QMessageBox.warning(self, "删除", f"删除失败：{e}")


class PageHistory(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(14)

        top = QHBoxLayout()
        top.setSpacing(10)
        title = QLabel("录制文件 / 历史记录")
        title.setStyleSheet("color:#1d1d1f;font-size:15pt;font-weight:600;")
        top.addWidget(title, 1)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setFixedHeight(32)
        self.refresh_btn.setFixedWidth(CTRL_BTN_W)
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        top.addWidget(self.refresh_btn)
        lay.addLayout(top)

        self.note = QLabel("")
        self.note.setStyleSheet(f"color:{THEME['sub']};font-size:10pt;background:transparent;")
        lay.addWidget(self.note)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        self.list_widget = QWidget()
        self.list_widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.list_lay = QVBoxLayout(self.list_widget)
        self.list_lay.setContentsMargins(0, 0, 0, 0)
        self.list_lay.setSpacing(10)
        self.list_lay.addStretch(1)
        self.scroll.setWidget(self.list_widget)
        lay.addWidget(self.scroll, 1)

        self._dir = _load_save_dir()
        self.refresh_btn.clicked.connect(self.refresh)
        self.refresh()

    def refresh(self):
        entries = []
        files = []
        err = ""
        try:
            if os.path.isdir(self._dir):
                entries = [f for f in os.listdir(self._dir) if os.path.isfile(os.path.join(self._dir, f))]
                # 优先显示视频；如果没有匹配视频，则显示所有文件（避免用户看不到任何内容）
                files = [os.path.join(self._dir, f) for f in entries if f.lower().endswith(_VIDEO_EXT)]
                if not files and entries:
                    files = [os.path.join(self._dir, f) for f in entries]
                files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        except Exception as e:
            err = str(e)
        self.note.setText(f"保存目录：{self._dir}（{len(entries)} 个文件）")
        # 清空旧行（包括 stretch）
        while self.list_lay.count():
            item = self.list_lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        if not files:
            empty_text = "暂无录制文件"
            if err:
                empty_text = f"读取目录出错：{err}"
            empty = QLabel(empty_text)
            empty.setStyleSheet(f"color:{THEME['sub']};font-size:11pt;padding:24px;background:transparent;")
            empty.setAlignment(Qt.AlignCenter)
            self.list_lay.addWidget(empty)
            self.list_lay.addStretch(1)
            return
        for p in files:
            self.list_lay.addWidget(HistoryRow(p, page=self))
        self.list_lay.addStretch(1)


# ----------------------------- 主窗口 -----------------------------
class ProtoWindow(QWidget):
    NAV = [("range", "录制范围"), ("params", "录制参数"),
           ("pref", "偏好"), ("save", "保存位置"),
           ("history", "历史记录")]

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowSystemMenuHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowTitle("轻量录屏 · ScreenRecorder")
        self.setWindowIcon(_app_icon())
        self.resize(960, 640)
        self.setMinimumSize(820, 560)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        # 窗口最小尺寸策略：宽度保持下方手动下限 820；高度在 show 后随布局
        # 自适应锁定（_lock_min_height），保证品牌区/分割线/导航/录制区完整，
        # 缩放时不允许把品牌区压缩、分割线顶进图标内部。
        self.card = QWidget()
        self.card.setObjectName("card")
        self.card.setStyleSheet(
            f"#card{{background:{THEME['card']};border-radius:22px;"
            f"border:1px solid {THEME['line']};}}")
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(36)
        shadow.setColor(QColor(0, 0, 0, 70))
        shadow.setOffset(0, 12)
        self.card.setGraphicsEffect(shadow)
        root.addWidget(self.card)

        card_lay = QVBoxLayout(self.card)
        card_lay.setContentsMargins(0, 0, 0, 0)
        card_lay.setSpacing(0)
        self.title_bar = TitleBar(self)
        card_lay.addWidget(self.title_bar)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color:{THEME['line']};background:{THEME['line']};max-height:1px;")
        card_lay.addWidget(sep)

        # 主体：左导航 + 右详情（上半区）
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self._build_sidebar(body)
        self._build_detail(body)
        card_lay.addLayout(body, 1)

        # 底部：录制区卡片（下半区）
        self._build_recording_bar(card_lay)

        self.setStyleSheet(self._global_qss())
        self._select(0)

        # 手动自由缩放准备：对 card 与所有子控件开启鼠标跟踪，
        # 以可见卡片边界为基准，避免透明阴影区导致命中区外移/黑框。
        self._resize_margin = 8
        self._resize_edge = None
        self._resize_start_geo = None
        self._resize_start_pos = None
        self.setMouseTracking(True)
        self.card.setMouseTracking(True)
        self._set_mouse_tracking_recursive(self.card)
        self.installEventFilter(self)
        self.card.installEventFilter(self)
        self._install_event_filter_recursive(self.card)

    def _set_mouse_tracking_recursive(self, widget):
        for child in widget.findChildren(QWidget):
            child.setMouseTracking(True)

    def _install_event_filter_recursive(self, widget):
        for child in widget.findChildren(QWidget):
            child.installEventFilter(self)

    # ---- 左侧导航 ----
    def _build_sidebar(self, body):
        side = QVBoxLayout()
        side.setContentsMargins(16, 16, 16, 16)
        side.setSpacing(6)

        # 品牌区：应用图标 + 中文名 + 英文名
        # 图标再放大，文字与图标间距再收紧，文字整体垂直居中于图标。
        brand = QHBoxLayout()
        brand.setContentsMargins(0, 0, 0, 0)
        brand.setSpacing(6)
        brand.setAlignment(Qt.AlignVCenter)
        ICON_SZ = 68
        icon_lbl = QLabel()
        icon_lbl.setFixedSize(ICON_SZ, ICON_SZ)
        icon_lbl.setAlignment(Qt.AlignCenter)
        icon_lbl.setStyleSheet("background:transparent;")
        pm = _app_icon().pixmap(ICON_SZ, ICON_SZ)
        if pm and not pm.isNull():
            icon_lbl.setPixmap(pm.scaled(ICON_SZ - 4, ICON_SZ - 4,
                                          Qt.KeepAspectRatio, Qt.SmoothTransformation))
        brand.addWidget(icon_lbl, alignment=Qt.AlignVCenter)
        # 文字块：整体高度等于图标，内部两行垂直居中
        name_box = QWidget()
        name_box.setFixedHeight(ICON_SZ)
        name_box.setStyleSheet("background:transparent;")
        name_lay = QVBoxLayout(name_box)
        name_lay.setContentsMargins(0, 0, 0, 0)
        name_lay.setSpacing(0)
        name_lay.addStretch(1)
        cn = QLabel("轻量录屏")
        cn.setFont(_heading_font(18, QFont.Weight.DemiBold))
        cn.setStyleSheet(f"color:{THEME['text']};background:transparent;")
        cn.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        en = QLabel("ScreenRecorder")
        en.setFont(_heading_font(13, QFont.Weight.Medium))
        en.setStyleSheet(f"color:{THEME['sub']};background:transparent;")
        en.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        name_lay.addWidget(cn)
        name_lay.addWidget(en)
        name_lay.addStretch(1)
        brand.addWidget(name_box, alignment=Qt.AlignVCenter)
        brand.addStretch(1)
        # 品牌区包一层固定高度容器（锁死 ICON_SZ）：
        # Qt 布局在空间不足时只压缩“可压缩项”，若品牌区裸挂在 VBox 里，
        # 窗口缩小时它会被压扁、分割线被顶进图标内部（820x560 实测复现）。
        # 包成 fixedHeight 容器后，品牌区与分割线永远锚定在顶部，压缩只发生
        # 在下方内容上，而窗口最小高度由 SetMinimumSize 约束兜底。
        brand_box = QWidget()
        brand_box.setFixedHeight(ICON_SZ)
        brand_box.setStyleSheet("background:transparent;")
        brand_box.setLayout(brand)
        side.addWidget(brand_box)
        side.addSpacing(8)

        # 品牌区与导航之间的细分割线（弱化结构，不抢视觉）
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"color:{THEME['line']};background:{THEME['line']};")
        side.addWidget(sep)
        side.addSpacing(10)

        self.nav_items = []
        for i, (key, label) in enumerate(self.NAV):
            item = NavItem(i, key, label)
            item.clicked.connect(self._select)
            self.nav_items.append(item)
            side.addWidget(item)

        side.addStretch(1)

        side_wrap = QWidget()
        side_wrap.setLayout(side)
        side_wrap.setFixedWidth(220)
        side_wrap.setStyleSheet(f"background:{THEME['bg']};border-top-left-radius:0;border-bottom-left-radius:0;")
        body.addWidget(side_wrap)

    # ---- 右侧详情 ----
    def _build_detail(self, body):
        self.stack = QStackedWidget()
        self.stack.setStyleSheet("background:transparent;border:none;")
        self.pages = [PageRange(), PageParams(), PagePref(), PageSave(), PageHistory()]
        for p in self.pages:
            self.stack.addWidget(p)
        body.addWidget(self.stack, 1)

    # ---- 底部录制区卡片（还原正式版「录制控制区 + 状态栏」布局，并叠加配置摘要状态信息） ----
    def _build_recording_bar(self, parent_lay):
        card = QWidget()
        card.setObjectName("reccard")
        card.setStyleSheet(
            f"#reccard{{background:{THEME['section_bg']};"
            f"border-top:1px solid {THEME['section_border']};"
            f"border-bottom-left-radius:22px;border-bottom-right-radius:22px;}}")
        vlay = QVBoxLayout(card)
        vlay.setContentsMargins(0, 0, 0, 0)
        vlay.setSpacing(0)

        # ===== 录制控制区（忠实还原正式版 ring_area）=====
        ring_area = QWidget()
        ra_lay = QHBoxLayout(ring_area)
        ra_lay.setContentsMargins(28, 12, 28, 12)
        ra_lay.setSpacing(24)
        self.ring = ProtoRing()
        self.ring.clicked.connect(self._on_start)
        ra_lay.addWidget(self.ring, alignment=Qt.AlignVCenter)
        right = QVBoxLayout()
        right.setSpacing(7)
        self.ring_caption = QLabel("开始录制")
        self.ring_caption.setStyleSheet(f"color:{THEME['text']};")
        self.ring_caption.setFont(_heading_font(18, QFont.Weight.Medium))
        self.tip_label = QLabel("F9 开始 / 停止 · 录制时自动收起到托盘")
        self.tip_label.setStyleSheet(f"color:{THEME['sub']};font-size:10pt;")
        # 操作按钮（完成后可用，这里仅展示布局）
        act_row = QHBoxLayout()
        act_row.setSpacing(10)
        self.btn_preview = QPushButton("预览")
        self.btn_openfolder = QPushButton("打开所在文件夹")
        self.btn_log = QPushButton("日志")
        for b in (self.btn_preview, self.btn_openfolder, self.btn_log):
            b.setFixedHeight(34)
            b.setCursor(Qt.PointingHandCursor)
            b.setEnabled(False)
        act_row.addWidget(self.btn_preview)
        act_row.addWidget(self.btn_openfolder)
        act_row.addWidget(self.btn_log)
        # 你丰富了的状态信息显示：当前配置摘要
        self.summary_label = QLabel(
            "全屏 · 30 fps · 高清(CRF 23) · 无音频 · MP4 · 保存到 视频/轻量录屏")
        self.summary_label.setStyleSheet(f"color:{THEME['sub']};font-size:10pt;")
        right.addWidget(self.ring_caption)
        right.addWidget(self.tip_label)
        right.addLayout(act_row)
        right.addWidget(self.summary_label)
        ra_lay.addLayout(right, 1)
        vlay.addWidget(ring_area)

        # ===== 状态栏（忠实还原正式版 status）=====
        status = QFrame()
        status.setFrameShape(QFrame.NoFrame)
        status.setStyleSheet("background:transparent;")
        slay = QHBoxLayout(status)
        slay.setContentsMargins(24, 8, 24, 14)
        self.status_label = QLabel("就绪")
        self.status_label.setStyleSheet(f"color:{THEME['sub']};font-size:10pt;")
        self.size_label = QLabel("")
        self.size_label.setStyleSheet(f"color:{THEME['sub']};font:12px Consolas;")
        self.timer_label = QLabel("00:00:00")
        self.timer_label.setStyleSheet(f"color:{THEME['text']};font:14px Consolas;")
        slay.addWidget(self.status_label)
        slay.addStretch(1)
        slay.addWidget(self.size_label)
        slay.addSpacing(10)
        slay.addWidget(self.timer_label)
        slay.addSpacing(4)
        # QSizeGrip 已移除：手动缩放覆盖四边/四角，且能避免原生缩放产生的黑框
        vlay.addWidget(status)

        parent_lay.addWidget(card)

    def _select(self, index):
        for i, it in enumerate(self.nav_items):
            it.set_selected(i == index)
        self.stack.setCurrentIndex(index)
        self._lock_min_height()

    def showEvent(self, ev):
        super().showEvent(ev)
        self._lock_min_height()

    def _lock_min_height(self):
        """把窗口最小高度锁定为布局实际所需（宽度保持手动下限 820）。

        品牌区、分割线、五个导航项、底部录制区全部固定高度，窗口最小高度
        必须 ≥ 它们之和，否则 Qt 布局会把唯一可压缩的品牌区压扁、把分割线
        顶进图标内部（820x560 实测复现）。只增不减：布局若因内容变化需要
        更高，同步抬升下限。
        """
        try:
            h = self.layout().minimumSize().height()
            if h > self.minimumHeight():
                self.setMinimumHeight(h)
        except Exception:
            pass

    # ---- 手动自由缩放（覆盖四边/四角，对齐可见卡片边缘，无黑框） ----
    def _hit_test(self, pos):
        """返回 (h_edge, v_edge)，h_edge/v_edge 为 -1/0/1 表示左/中/右或上/中/下。

        以可见卡片（card）的全局几何为基准，避免透明阴影区让命中区偏离视觉边缘。
        标题栏区域完全交给拖动，不参与缩放，防止拖动窗口时误触发缩放导致高度暴涨。
        """
        if self.isMaximized() or self.isFullScreen():
            return 0, 0
        tl = self.card.mapToGlobal(self.card.rect().topLeft())
        br = self.card.mapToGlobal(self.card.rect().bottomRight())
        left, top = tl.x(), tl.y()
        right, bottom = br.x(), br.y()
        x, y = pos.x(), pos.y()
        # 标题栏区域内不缩放，避免拖动窗口时误触发
        if self.title_bar is not None:
            tbt = self.title_bar.mapToGlobal(self.title_bar.rect().topLeft()).y()
            tbb = self.title_bar.mapToGlobal(self.title_bar.rect().bottomLeft()).y()
            if tbt <= y <= tbb:
                return 0, 0
        m = self._resize_margin
        h = -1 if x <= left + m else (1 if x >= right - m else 0)
        v = -1 if y <= top + m else (1 if y >= bottom - m else 0)
        if h == 0 and v == 0:
            return 0, 0
        return h, v

    def _cursor_for(self, h, v):
        if h == -1 and v == -1 or h == 1 and v == 1:
            return Qt.SizeFDiagCursor
        if h == -1 and v == 1 or h == 1 and v == -1:
            return Qt.SizeBDiagCursor
        if h != 0:
            return Qt.SizeHorCursor
        if v != 0:
            return Qt.SizeVerCursor
        return Qt.ArrowCursor

    # ---- 可选诊断：设置环境变量 PROTO_DIAG=1 时记录每次尺寸/位置变化的布局数据 ----
    def _diag_path(self):
        base = os.environ.get("TEMP") or os.path.expanduser("~")
        return os.path.join(base, "proto_resize_log.txt")

    def _diag_log(self, tag):
        if not os.environ.get("PROTO_DIAG"):
            return
        try:
            lay = self.layout()
            lmin = lay.minimumSize() if lay else None
            msh = self.minimumSizeHint()
            pg = self.stack.currentWidget() if hasattr(self, "stack") else None
            play = pg.layout() if pg is not None else None
            pmin = play.minimumSize() if play else None
            line = (f"{tag} geo={self.width()}x{self.height()} "
                    f"minW={self.minimumWidth()} minH={self.minimumHeight()} "
                    f"minSizeHint={msh.width()}x{msh.height()} "
                    f"layoutMin={lmin.width() if lmin else '-'}x{lmin.height() if lmin else '-'} "
                    f"page={type(pg).__name__ if pg is not None else '-'} "
                    f"pageLayoutMin={pmin.width() if pmin else '-'}x{pmin.height() if pmin else '-'}\n")
            with open(self._diag_path(), "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._diag_log("resize")

    def moveEvent(self, ev):
        super().moveEvent(ev)
        self._diag_log("move  ")

    def _hook_new_child(self, ev):
        """给运行时新建的控件补上鼠标跟踪 + 事件过滤。

        历史页刷新出的每一行、动态重建的列表项等都是启动之后才创建的，
        若不补挂，鼠标停在这些新控件上时边缘缩放会失效（表现为“缩放时好时坏”）。
        """
        try:
            child = ev.child()
        except Exception:
            return
        if isinstance(child, QWidget):
            child.setMouseTracking(True)
            child.installEventFilter(self)
            self._set_mouse_tracking_recursive(child)
            self._install_event_filter_recursive(child)

    def eventFilter(self, obj, ev):
        et = ev.type()
        # 运行时新增控件的补挂（ChildPolished 时控件已完成 polish，安全）
        if et == QEvent.ChildPolished:
            self._hook_new_child(ev)
            return super().eventFilter(obj, ev)
        # 拦截 card 及其子控件的鼠标事件，确保边缘拖拽不受子控件遮挡影响
        if et in (QEvent.MouseMove, QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
            if obj is self or obj is self.card or self.card.isAncestorOf(obj):
                self._handle_mouse_event(ev)
                if self._resize_edge is not None:
                    return True
        return super().eventFilter(obj, ev)

    def _handle_mouse_event(self, ev):
        if isinstance(ev, QMouseEvent):
            pos = ev.globalPosition().toPoint()
            if ev.type() == QEvent.MouseMove:
                if self._resize_edge is not None:
                    self._do_resize(pos)
                else:
                    self._update_cursor(pos)
            elif ev.type() == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
                h, v = self._hit_test(pos)
                if h != 0 or v != 0:
                    self._resize_edge = (h, v)
                    self._resize_start_geo = self.geometry()
                    self._resize_start_pos = pos
            elif ev.type() == QEvent.MouseButtonRelease and ev.button() == Qt.LeftButton:
                self._resize_edge = None
                self._resize_start_geo = None
                self._resize_start_pos = None
                self._update_cursor(QCursor.pos())

    def mouseMoveEvent(self, ev):
        self._handle_mouse_event(ev)
        super().mouseMoveEvent(ev)

    def mousePressEvent(self, ev):
        self._handle_mouse_event(ev)
        if self._resize_edge is None:
            super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev):
        self._handle_mouse_event(ev)
        super().mouseReleaseEvent(ev)

    def _update_cursor(self, pos):
        if self._resize_edge is not None:
            return
        h, v = self._hit_test(pos)
        self.setCursor(self._cursor_for(h, v))

    def _do_resize(self, pos):
        h, v = self._resize_edge
        geo = QRect(self._resize_start_geo)
        dx = pos.x() - self._resize_start_pos.x()
        dy = pos.y() - self._resize_start_pos.y()
        if h == -1:
            geo.setLeft(geo.left() + dx)
        elif h == 1:
            geo.setRight(geo.right() + dx)
        if v == -1:
            geo.setTop(geo.top() + dy)
        elif v == 1:
            geo.setBottom(geo.bottom() + dy)
        # 限制最小尺寸
        min_w = self.minimumWidth()
        min_h = self.minimumHeight()
        if geo.width() < min_w:
            if h == -1:
                geo.setLeft(geo.right() - min_w)
            else:
                geo.setRight(geo.left() + min_w)
        if geo.height() < min_h:
            if v == -1:
                geo.setTop(geo.bottom() - min_h)
            else:
                geo.setBottom(geo.top() + min_h)
        self.setGeometry(geo)

    def _on_start(self):
        QMessageBox.information(
            self, "演示原型",
            "这是「左侧导航 + 右侧详情」布局的纯 UI 原型，\n未接入真实录制逻辑。\n\n"
            "左侧点击可切换四个设置面板，主窗口因此保持紧凑。")

    def _global_qss(self):
        c = THEME
        return f"""
        QWidget{{color:{c['text']};font-size:10pt;}}
        QComboBox{{
            border:1px solid {c['input_border']};border-radius:8px;padding:4px 10px;
            background:{c['input_bg']};selection-color:{c['text']};min-height:22px;
        }}
        QComboBox:hover{{border-color:{c['checkbox_border']};}}
        QComboBox:focus{{border-color:{c['accent']};}}
        QComboBox::drop-down{{border:none;width:24px;}}
        QComboBox QAbstractItemView{{
            border:1px solid {c['input_border']};border-radius:8px;background:{c['input_bg']};
            selection-background-color:{c['selected_bg']};selection-color:{c['text']};padding:4px 6px;outline:0px;
        }}
        QLineEdit{{
            border:1px solid {c['input_border']};border-radius:8px;padding:5px 10px;
            background:{c['input_bg']};color:{c['text']};
        }}
        QLineEdit:hover{{border-color:{c['checkbox_border']};}}
        QLineEdit:focus{{border-color:{c['accent']};}}
        QPushButton{{
            background:{c['hover_bg']};color:{c['text']};border:1px solid {c['hover_border']};border-radius:8px;
            font-size:10pt;padding:0 14px;
        }}
        QPushButton:hover{{background:{c['pressed_bg']};border-color:{c['hover_border']};}}
        QPushButton:pressed{{background:{c['pressed_bg']};}}
        QPushButton:checked{{
            background:{c['selected_bg']};color:{c['text']};border:1px solid {c['selected_border']};
        }}
        QPushButton:disabled{{color:{c['disabled_text']};background:{c['section_bg']};border-color:{c['section_border']};}}
        QCheckBox{{color:{c['text']};font-size:10pt;spacing:7px;}}
        QCheckBox::indicator{{
            width:16px;height:16px;border-radius:4px;border:1px solid {c['checkbox_border']};
            background:{c['input_bg']};
        }}
        QCheckBox::indicator:hover{{border-color:{c['accent']};}}
        QCheckBox::indicator:checked{{background:{c['accent']};border:1px solid {c['accent']};}}
        QScrollBar:vertical{{background:transparent;width:8px;margin:2px;}}
        QScrollBar::handle:vertical{{background:{c['scroll_handle']};border-radius:4px;min-height:30px;}}
        QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}
        """


# ----------------------------- 入口 -----------------------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setFont(_heading_font(UI_FONT_SIZE_PT))
    win = ProtoWindow()
    win.show()
    sys.exit(app.exec())
