# -*- coding: utf-8 -*-
"""
轻量录屏工具 · PySide6 全量迁移版 (ScreenRecorder Qt)
========================================================
从 Tkinter 版完整迁移到 PySide6（Qt for Python）：

UI / 交互提升（相较 Tkinter 版）
- 无边框圆角窗口 + 柔和投影，接近真实 macOS 卡片质感
- 录制范围分段按钮、QSS 精细化控件、Hover 态
- 绘制录制圆环（QPainter）：空闲为红色圆点、录制为红色圆环 + 呼吸光晕（中心无计时）
- 系统托盘（右键菜单：显示主窗口 / 开始 / 停止 / 打开保存文件夹 / 退出）
- 可选悬浮窗（置顶、可拖动、计时 + 实时大小 + 停止）—— 沿用此前已验证的样式
- 高 DPI 清晰文字、合理间距与字号、可读性强
- 全局快捷键（WH_KEYBOARD_LL 钩子，窗口最小化/收起仍可用）

录制核心（ffmpeg 命令、硬件编码探测、PrintWindow 窗口捕获、预估大小、
起停 / 校验 / remux、音频设备枚举、配置持久化）忠实沿用原版逻辑，仅将 UI 回调改为 Qt Signal/Slot。

依赖：PySide6（pip install PySide6）。ffmpeg.exe 需与本文件同目录（或 PATH 可找到）。
"""

import os
import re
import sys
import time
import json
import math
import hashlib
import threading
import subprocess
import ctypes
import ctypes.wintypes as wt
import concurrent.futures

from PySide6.QtCore import (
    QObject, Signal, QTimer, Qt, QRect, QRectF, QPoint, QSize, QEvent, QFile, QIODevice,
    QDateTime, QUrl, QFileInfo, QPropertyAnimation, QEasingCurve, Property,
)
from PySide6.QtGui import (
    QIcon, QFont, QColor, QPainter, QPen, QBrush, QAction, QCursor, QPixmap,
    QPainterPath, QScreen, QKeySequence, QShortcut, QTextCharFormat, QTextCursor,
    QDesktopServices, QMouseEvent, QPalette, QImage,
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QComboBox, QPushButton, QLineEdit, QCheckBox,
    QFrame, QVBoxLayout, QHBoxLayout, QGridLayout, QSizePolicy, QSystemTrayIcon, QMenu,
    QFileDialog, QMessageBox, QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QScrollArea, QLayout,
    QStackedWidget, QAbstractItemView, QDialog, QDateTimeEdit, QSpinBox,
    QFileIconProvider,
)

# ------------------------- 高 DPI 适配 -------------------------
def enable_dpi_awareness():
    # PROCESS_PER_MONITOR_DPI_AWARE(2)：按当前显示器 DPI 原生渲染，
    # 避免 PROCESS_SYSTEM_DPI_AWARE(1) 在非 100% 缩放/多屏下的位图拉伸发虚。
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def get_dpi_scale():
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)
        ctypes.windll.user32.ReleaseDC(0, hdc)
        if dpi and dpi > 0:
            return max(1.0, min(3.0, dpi / 96.0))
    except Exception:
        pass
    return 1.0


def virtual_screen():
    try:
        u = ctypes.windll.user32
        x = u.GetSystemMetrics(76)
        y = u.GetSystemMetrics(77)
        w = u.GetSystemMetrics(78)
        h = u.GetSystemMetrics(79)
        if w <= 0 or h <= 0:
            w = u.GetSystemMetrics(0)
            h = u.GetSystemMetrics(1)
            x = y = 0
        return x, y, w, h
    except Exception:
        return 0, 0, 1920, 1080


def primary_monitor_rect():
    try:
        u = ctypes.windll.user32

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT),
                        ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]

        hmon = u.MonitorFromWindow(0, 1)
        if not hmon:
            return None
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if u.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcMonitor
            return (r.left, r.top, r.right - r.left, r.bottom - r.top)
    except Exception:
        pass
    return None


try:
    CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW
except AttributeError:
    CREATE_NO_WINDOW = 0x08000000


# ----------------------------- 预设 / 选项 -----------------------------
QUALITY_PRESETS = {
    "近无损 (CRF 1) · 视觉无损，兼容播放":   {"crf": 1, "preset": "slow", "force_software": True},
    "极清 (CRF 12) · 近原画，极细致":       {"crf": 12, "preset": "slow", "force_software": True},
    "超清 (CRF 18) · 极清晰，细节丰富":     {"crf": 18, "preset": "slow"},
    "高清 (CRF 23) · 清晰，约平台超清画质": {"crf": 23, "preset": "medium"},
    "标准 (CRF 28) · 日常够用，体积适中":   {"crf": 28, "preset": "fast"},
    "流畅 (CRF 34) · 体积小，略有模糊":     {"crf": 34, "preset": "ultrafast"},
}
DEFAULT_QUALITY = "高清 (CRF 23) · 清晰，约平台超清画质"
FPS_OPTIONS = ["10", "15", "24", "30", "60"]
FORMAT_OPTIONS = {
    "MP4 (H.264, 推荐)": "mp4",
    "MKV": "mkv",
    "AVI": "avi",
    "MOV": "mov",
    "GIF (动图)": "gif",
}
DELAY_OPTIONS = {"0.5 秒": 0.5, "1 秒": 1.0, "2 秒": 2.0, "3 秒": 3.0}
AUDIO_SYSTEM_MIC = "系统声音 + 麦克风混音"
# v5.0：astats 元数据实时窗口管线（reset=1 每帧统计窗口；metadata 注入帧元数据，ametadata 打印到 stderr）。
# 经 ffmpeg 实测：每帧输出 lavfi.astats.Overall.RMS_level / Peak_level 键值对，键名见下方正则。
ASTATS_META_CHAIN = "astats=metadata=1:reset=1,ametadata=mode=print"
# 诊断日志开关：开启时约每 0.5s 输出一条 [电平诊断]（RMS/Peak/显示电平/格数）到日志文件，供实测定标用
AUDIO_DEBUG_LOG = True
_RE_ASTATS_PEAK = re.compile(r"lavfi\.astats\.Overall\.Peak_level=(-?\d+\.?\d*)")
_RE_ASTATS_RMS = re.compile(r"lavfi\.astats\.Overall\.RMS_level=(-?\d+\.?\d*)")
ENCODER_OPTIONS = ["默认（自动）", "libx264（软件）", "h264_nvenc", "h264_qsv", "h264_amf"]
BITRATE_OPTIONS = [
    "不设置（CRF 质量）",
    "2 Mbps（较小体积）",
    "5 Mbps（清晰）",
    "8 Mbps（高码率）",
    "12 Mbps（极高码率）",
    "20 Mbps（最高码率）",
]
RESOLUTION_SCALE_OPTIONS = [
    "原始（不缩放）",
    "0.5x（缩小一半）",
    "0.75x（缩小 1/4）",
    "1.25x（放大 1/4）",
    "1.5x（放大一半）",
    "2x（放大一倍）",
]
HOTKEY_OPTIONS = ["F9", "F10", "F11", "F12"]
VK_MAP = {"F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B}
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
VK_F9 = 0x78

# 主题（macOS 风格）
# 浅色 / 深色两套主题；MainWindow 会按 self.col 动态切换。
LIGHT_THEME = {
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
    "menu_bg": "#f9f9fb",
    "menu_border": "#d8d8dd",
    "scroll_handle": "#d1d1d6",
    "checkbox_border": "#c7c7cc",
    "history_row_bg": "#fbfbfd",
    "history_row_border": "#e6e6ec",
    "history_thumb_bg": "#f0f0f5",
    "danger_bg": "#ffe5e3",
    "danger_text": "#d70015",
}

DARK_THEME = {
    "bg": "#1e1e24",
    "card": "#2a2a32",
    "text": "#f2f2f7",
    "sub": "#9a9aa2",
    "accent": "#0a84ff",
    "rec": "#ff453a",
    "rec_dark": "#d70015",
    "ok": "#32d74b",
    "line": "#3a3a44",
    "section_bg": "#24242c",
    "section_border": "#35353f",
    "input_bg": "#1d1d25",
    "input_border": "#3a3a44",
    "hover_bg": "#3a3a44",
    "hover_border": "#4a4a54",
    "pressed_bg": "#464650",
    "selected_bg": "#1e3a5f",
    "selected_border": "#2a5a8f",
    "disabled_text": "#6e6e78",
    "menu_bg": "#2a2a32",
    "menu_border": "#3a3a44",
    "scroll_handle": "#55555f",
    "checkbox_border": "#55555f",
    "history_row_bg": "#24242c",
    "history_row_border": "#35353f",
    "history_thumb_bg": "#1d1d25",
    "danger_bg": "#3a2a2c",
    "danger_text": "#ff6b5e",
}

# 保留旧常量作为浅色默认值，供非 MainWindow 类 / 回退使用
C_BG = LIGHT_THEME["bg"]
C_CARD = LIGHT_THEME["card"]
C_TEXT = LIGHT_THEME["text"]
C_SUB = LIGHT_THEME["sub"]
C_ACCENT = LIGHT_THEME["accent"]
C_REC = LIGHT_THEME["rec"]
C_REC_DARK = LIGHT_THEME["rec_dark"]
C_OK = LIGHT_THEME["ok"]
C_LINE = LIGHT_THEME["line"]

# ------------------------- 自定义快捷键辅助 -------------------------
# 修饰键虚拟键码
MOD_VKS = {"Ctrl": 0x11, "Shift": 0x10, "Alt": 0x12, "Win": 0x5B}
MOD_NAMES = {v: k for k, v in MOD_VKS.items()}
WM_SYSKEYDOWN = 0x0104


def _qt_key_name(key):
    """把 Qt.Key 枚举转成便于展示/解析的键名（不含修饰键）。"""
    try:
        if getattr(Qt, "Key_A") <= key <= getattr(Qt, "Key_Z"):
            return chr(ord('A') + (key - getattr(Qt, "Key_A")))
        if getattr(Qt, "Key_0") <= key <= getattr(Qt, "Key_9"):
            return chr(ord('0') + (key - getattr(Qt, "Key_0")))
        if getattr(Qt, "Key_F1") <= key <= getattr(Qt, "Key_F24"):
            return f"F{key - getattr(Qt, 'Key_F1') + 1}"
    except Exception:
        pass
    names = {
        Qt.Key_Escape: "Esc", Qt.Key_Space: "Space", Qt.Key_Return: "Enter",
        Qt.Key_Enter: "Enter", Qt.Key_Backspace: "Backspace", Qt.Key_Tab: "Tab",
        Qt.Key_Delete: "Delete", Qt.Key_Insert: "Insert", Qt.Key_Home: "Home",
        Qt.Key_End: "End", Qt.Key_PageUp: "PageUp", Qt.Key_PageDown: "PageDown",
        Qt.Key_Left: "Left", Qt.Key_Right: "Right", Qt.Key_Up: "Up", Qt.Key_Down: "Down",
        Qt.Key_CapsLock: "CapsLock", Qt.Key_Print: "PrintScreen",
        Qt.Key_Pause: "Pause", Qt.Key_Menu: "Menu",
    }
    return names.get(key, "")


def _name_to_vk(name):
    """把键名（F9 / A / 0 / Space 等）转成虚拟键码。"""
    name = (name or "").strip()
    if not name:
        return None
    up = name.upper()
    if up in VK_MAP:
        return VK_MAP[up]
    if up in MOD_VKS:
        return MOD_VKS[up]
    if len(up) == 1 and up.isalpha():
        return ord(up.upper())
    if len(up) == 1 and up.isdigit():
        return ord(up)
    if up.startswith("F") and up[1:].isdigit():
        n = int(up[1:])
        if 1 <= n <= 24:
            return 0x70 + n - 1
    extra = {
        "ESC": 0x1B, "SPACE": 0x20, "ENTER": 0x0D, "BACKSPACE": 0x08,
        "TAB": 0x09, "DELETE": 0x2E, "INSERT": 0x2D, "HOME": 0x24,
        "END": 0x23, "PAGEUP": 0x21, "PAGEDOWN": 0x22,
        "LEFT": 0x25, "RIGHT": 0x27, "UP": 0x26, "DOWN": 0x28,
        "CAPSLOCK": 0x14, "PRINTSCREEN": 0x2C, "PAUSE": 0x13, "MENU": 0x12,
    }
    return extra.get(up)


def parse_hotkey(text):
    """解析 'Ctrl+Shift+F9' 形式，返回 (vk, modifiers_tuple) 或 None。"""
    text = (text or "").strip()
    if not text:
        return None
    parts = [p.strip() for p in text.replace("＋", "+").split("+") if p.strip()]
    if not parts:
        return None
    base = parts[-1]
    vk = _name_to_vk(base)
    if vk is None:
        return None
    mods = []
    for p in parts[:-1]:
        p2 = p.lower()
        if p2 in ("ctrl", "control"):
            mods.append(MOD_VKS["Ctrl"])
        elif p2 in ("shift",):
            mods.append(MOD_VKS["Shift"])
        elif p2 in ("alt", "option"):
            mods.append(MOD_VKS["Alt"])
        elif p2 in ("win", "meta", "cmd"):
            mods.append(MOD_VKS["Win"])
        else:
            return None
    return vk, tuple(sorted(set(mods)))


def format_hotkey(vk, mods=()):
    """把虚拟键码和修饰键集合转成展示文本。"""
    mod_names = [MOD_NAMES.get(m) for m in mods if m in MOD_NAMES]
    base = ""
    for name, code in VK_MAP.items():
        if code == vk:
            base = name
            break
    if not base:
        for k, v in MOD_VKS.items():
            if v == vk:
                base = k
                break
    if not base:
        # 常用字母数字 / F 键
        if 0x41 <= vk <= 0x5A:
            base = chr(vk)
        elif 0x30 <= vk <= 0x39:
            base = chr(vk)
        elif 0x70 <= vk <= 0x87:
            base = f"F{vk - 0x70 + 1}"
        else:
            base = {0x1B: "Esc", 0x20: "Space", 0x0D: "Enter", 0x08: "Backspace",
                    0x09: "Tab", 0x2E: "Delete", 0x2D: "Insert", 0x24: "Home",
                    0x23: "End", 0x21: "PageUp", 0x22: "PageDown",
                    0x25: "Left", 0x27: "Right", 0x26: "Up", 0x28: "Down",
                    0x14: "CapsLock", 0x2C: "PrintScreen", 0x13: "Pause"}.get(vk, "")
    if not base:
        return ""
    return "+".join(mod_names + [base])


# ----------------------------- 字体 / 清晰度全局配置 -----------------------------
# 字体栈：Segoe UI 负责拉丁/数字的高清晰度，Microsoft YaHei UI 负责中文 UI。
# 避免使用 MiSans / HarmonyOS 等未预装字体作为首选，防止回退不一致。
UI_FONT_FAMILIES = ["Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei"]
UI_FONT_SIZE_PT = 12                 # 应用级基础字号（正文/表单标签等），标题单独放大
UI_FONT_WEIGHT = QFont.Weight.Normal
UI_FONT_HINTING = QFont.HintingPreference.PreferFullHinting      # 全局强 hinting，让笔画更贴像素网格
UI_FONT_STRATEGY = QFont.StyleStrategy.PreferAntialias
UI_FONT_LETTER_SPACING = 0.3         # 微小字距，缓解 CJK 高密度笔画粘连


def _heading_font(size_pt, weight=QFont.Weight.Medium):
    """构造标题/加粗字体：继承应用级字体栈、字距与抗锯齿策略；
    对标题使用 FullHinting，让粗笔画边缘更锐、不糊。"""
    f = QFont()
    f.setFamilies(UI_FONT_FAMILIES)
    f.setPointSize(size_pt)
    f.setWeight(weight)
    f.setStyleStrategy(UI_FONT_STRATEGY)
    f.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    if UI_FONT_LETTER_SPACING:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, UI_FONT_LETTER_SPACING)
    return f


# ------------------------- 窗口 / 音频枚举 -------------------------
def get_window_title(hwnd):
    try:
        u = ctypes.windll.user32
        u.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
        u.GetWindowTextLengthW.restype = ctypes.c_int
        u.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        u.GetWindowTextW.restype = ctypes.c_int
        length = u.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        u.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value.strip()
    except Exception:
        return ""


def get_window_list():
    results = []

    def enum_cb(hwnd, lParam):
        try:
            u = ctypes.windll.user32
            if not u.IsWindowVisible(hwnd):
                return True
            if u.GetWindowTextLengthW(hwnd) <= 0:
                return True
            title = get_window_title(hwnd)
            if title and not title.lower().startswith("screenrecorder"):
                results.append((int(hwnd), title))
        except Exception:
            pass
        return True

    try:
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        ctypes.windll.user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
    except Exception:
        pass
    return results


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


class _CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD), ("flags", wt.DWORD), ("hCursor", ctypes.c_void_p),
        ("ptScreenPos", wt.POINT),
    ]


def capture_window_bgra(hwnd):
    """用 PrintWindow 抓取指定窗口自身内容，返回 (BGRA bytes, w, h)；失败返回 None。"""
    try:
        u = ctypes.windll.user32
        g = ctypes.windll.gdi32
        u.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.RECT)]
        u.GetWindowRect.restype = ctypes.c_int
        r = wt.RECT()
        if not u.GetWindowRect(hwnd, ctypes.byref(r)):
            return None
        w = r.right - r.left
        h = r.bottom - r.top
        if w <= 0 or h <= 0:
            return None
        u.GetDC.argtypes = [ctypes.c_void_p]
        u.GetDC.restype = ctypes.c_void_p
        u.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        u.ReleaseDC.restype = ctypes.c_int
        u.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
        u.PrintWindow.restype = ctypes.c_int
        g.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
        g.CreateCompatibleDC.restype = ctypes.c_void_p
        g.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        g.CreateCompatibleBitmap.restype = ctypes.c_void_p
        g.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        g.SelectObject.restype = ctypes.c_void_p
        g.DeleteObject.argtypes = [ctypes.c_void_p]
        g.DeleteObject.restype = ctypes.c_int
        g.DeleteDC.argtypes = [ctypes.c_void_p]
        g.DeleteDC.restype = ctypes.c_int
        g.BitBlt.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        g.BitBlt.restype = ctypes.c_int
        g.GetDIBits.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
            ctypes.c_void_p, ctypes.POINTER(_BITMAPINFO), ctypes.c_uint]
        g.GetDIBits.restype = ctypes.c_int

        hdc_screen = u.GetDC(0)
        if not hdc_screen:
            return None
        hdc_mem = g.CreateCompatibleDC(hdc_screen)
        if not hdc_mem:
            u.ReleaseDC(0, hdc_screen)
            return None
        hbmp = g.CreateCompatibleBitmap(hdc_screen, w, h)
        if not hbmp:
            g.DeleteDC(hdc_mem)
            u.ReleaseDC(0, hdc_screen)
            return None
        old_bmp = g.SelectObject(hdc_mem, hbmp)
        ok = u.PrintWindow(hwnd, hdc_mem, 2)
        if not ok:
            ok = u.PrintWindow(hwnd, hdc_mem, 0)
        if not ok:
            g.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, r.left, r.top, 0x00CC0020)
        try:
            u.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
            u.GetCursorPos.restype = ctypes.c_int
            u.GetCursorInfo.argtypes = [ctypes.POINTER(_CURSORINFO)]
            u.GetCursorInfo.restype = ctypes.c_int
            g.DrawIconEx.argtypes = [
                ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
            g.DrawIconEx.restype = ctypes.c_int
            ci = _CURSORINFO()
            ci.cbSize = ctypes.sizeof(_CURSORINFO)
            if u.GetCursorInfo(ctypes.byref(ci)) and (ci.flags & 1):
                cx = ci.ptScreenPos.x - r.left
                cy = ci.ptScreenPos.y - r.top
                if 0 <= cx < w and 0 <= cy < h:
                    g.DrawIconEx(hdc_mem, cx, cy, ci.hCursor, 0, 0, 0, 0, 0x0003)
        except Exception:
            pass
        bmi = _BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0
        buf = ctypes.create_string_buffer(w * h * 4)
        g.GetDIBits(hdc_mem, hbmp, 0, h, ctypes.cast(buf, ctypes.c_void_p),
                    ctypes.byref(bmi), 0)
        g.SelectObject(hdc_mem, old_bmp)
        g.DeleteObject(hbmp)
        g.DeleteDC(hdc_mem)
        u.ReleaseDC(0, hdc_screen)
        return (buf.raw[:w * h * 4], w, h)
    except Exception:
        return None


def list_audio_devices(ffmpeg):
    devices = []
    try:
        proc = subprocess.run(
            [ffmpeg, "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="ignore", creationflags=CREATE_NO_WINDOW, timeout=15)
        for line in proc.stderr.splitlines():
            line = line.strip()
            if "(audio)" in line and '"' in line:
                devices.append(line.split('"')[1])
    except Exception:
        pass
    return devices


def list_wasapi_devices(ffmpeg):
    devices = []
    try:
        proc = subprocess.run(
            [ffmpeg, "-list_devices", "true", "-f", "wasapi", "-i", "dummy"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="ignore", creationflags=CREATE_NO_WINDOW, timeout=15)
        for line in proc.stderr.splitlines():
            line = line.strip()
            if "(audio)" in line and '"' in line:
                devices.append(line.split('"')[1])
    except Exception:
        pass
    return devices


# =====================================================================
#  全局快捷键（WH_KEYBOARD_LL 钩子，窗口隐藏/最小化仍可用）
# =====================================================================
class HotkeyEmitter(QObject):
    """把 Win32 Hook 线程的热键事件安全投递到 GUI 主线程（QueuedConnection）。"""
    triggered = Signal()


# (AudioMeterProbe WASAPI 方案已按 v4.0 清单移除：改为解析 FFmpeg 录音链路 astats 实时电平)

class GlobalHotkey:
    def __init__(self, vk=VK_F9, modifiers=(), callback=None):
        self.vk = vk
        self.modifiers = tuple(modifiers or ())
        self.callback = callback
        self._hook = None
        self._handler = None
        self._thread = None
        self._running = False

    def _install_argtypes(self):
        u = ctypes.windll.user32
        u.SetWindowsHookExW.argtypes = [
            ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
        u.SetWindowsHookExW.restype = ctypes.c_void_p
        u.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        u.UnhookWindowsHookEx.restype = ctypes.c_int
        u.CallNextHookEx.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p]
        u.CallNextHookEx.restype = ctypes.c_long
        u.GetMessageW.argtypes = [
            ctypes.POINTER(wt.MSG), ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
        u.GetMessageW.restype = ctypes.c_int
        u.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
        u.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]

    def _mods_ok(self):
        try:
            u = ctypes.windll.user32
            required = set(self.modifiers)
            for vk in MOD_VKS.values():
                down = bool(u.GetAsyncKeyState(vk) & 0x8000)
                if vk in required and not down:
                    return False
                if vk not in required and down:
                    return False
            return True
        except Exception:
            return not self.modifiers

    def _proc(self, nCode, wParam, lParam):
        try:
            if nCode >= 0 and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                vk = ctypes.cast(lParam, ctypes.POINTER(ctypes.c_ulong))[0]
                if vk == self.vk and self._mods_ok():
                    if self.callback:
                        self.callback()
                    return 1
        except Exception:
            pass
        return ctypes.windll.user32.CallNextHookEx(0, nCode, wParam, lParam)

    def start(self):
        if self._running:
            return
        self._install_argtypes()
        u = ctypes.windll.user32
        k = ctypes.windll.kernel32
        k.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        k.GetModuleHandleW.restype = ctypes.c_void_p
        u.CallNextHookEx.restype = ctypes.c_ssize_t
        self._handler = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p)(self._proc)
        self._hook = u.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._handler, k.GetModuleHandleW(None), 0)
        if not self._hook:
            self._hook = u.SetWindowsHookExW(
                WH_KEYBOARD_LL, self._handler, 0, 0)
        if not self._hook:
            return
        self._running = True

        def _loop():
            msg = wt.MSG()
            while self._running and u.GetMessageW(ctypes.byref(msg), 0, 0, 0) != 0:
                u.TranslateMessage(ctypes.byref(msg))
                u.DispatchMessageW(ctypes.byref(msg))

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        u = ctypes.windll.user32
        if self._hook:
            try:
                u.UnhookWindowsHookEx(self._hook)
            except Exception:
                pass
            self._hook = None
        self._handler = None


# =====================================================================
#  录制核心（UI 无关，通过 Qt Signal 通知界面）
# =====================================================================
class RecorderCore(QObject):
    status_changed = Signal(str, str)   # (文本, 颜色)
    log_line = Signal(str)              # 原始日志
    estimate_changed = Signal(str)
    started = Signal()                  # 录制真正开始
    finished = Signal(str)              # 最终文件路径
    error = Signal(str)                 # 错误信息
    restart_requested = Signal()        # 自动分段/窗口尺寸变化后请求继续录制
    audio_items_ready = Signal(list)
    audio_level = Signal(float)         # 实时音频电平 0.0~1.0（解析 FFmpeg astats，与录音链路完全一致）

    def __init__(self, ffmpeg):
        super().__init__()
        self.ffmpeg = ffmpeg
        self.recording = False
        self._finalizing = False
        self._finalize_started = False
        self.proc = None
        self._hw_encoder = None
        self._write_path = None
        self._out_path = None
        self._raw_path = None
        self.region = None
        self._win_hwnd = None
        self._using_raw_window_capture = False
        self._window_capture_hwnd = None
        self._window_capture_size = None
        self._window_capture_fps = 30
        self.window_capture_method = "printwindow"   # printwindow / desktop
        self.start_time = 0
        self._audio_devices = []
        self._wasapi_devices = []
        self._ffmpeg_log = []
        self._log_thread = None
        self._monitor_thread = None
        self._window_capture_thread = None
        self._auto_restart_reason = None
        self._total_recorded = 0
        self._fallback_done = False
        self._segment_timer = None
        self._max_timer = None
        self._ffmpeg_job = self._make_kill_job()
        # 预检测硬件编码器，避免第一次点击“开始录制”时在 UI 线程里跑 ffmpeg 探测导致卡顿
        if self.ffmpeg and os.path.exists(self.ffmpeg):
            threading.Thread(target=self._detect_hw_encoder, daemon=True).start()
        self.mode = "full"
        self.fps = "30"
        self.quality = DEFAULT_QUALITY
        self.fmt = "MP4 (H.264, 推荐)"
        self.audio = "无音频"
        # v5.0：astats 元数据电平解析状态（RMS/Peak 分离窗口，Peak 先到、RMS 齐了出电平）
        self._audio_rms_db = None
        self._audio_peak_db = None
        self._audio_debug_ts = 0.0
        self.mic = ""
        self.mic_denoise = False   # v6.0：麦克风降噪（afftdn），仅作用于含麦克风的采集路径
        # v6.0：音频健康（静音）告警状态（仅对"应有麦克风输入"的模式生效，只告警不自动停止）
        self._audio_expects_mic = False
        self._audio_silent_since = None
        self._audio_silent_warned = False
        self.encoder = "默认（自动）"
        self.bitrate = ""
        self.scale = 1.0
        self.max_duration_seconds = 0
        self.segment_seconds = 0
        self.scheduled_start = None
        self.save_dir = self._default_save_dir()
        self.prefix = "录屏"
        self.name_template = ""   # 命名模板，如 {date}_{time}_{mode}_{resolution}_{fps}；空=旧行为
        self.delay = "1 秒"
        self.auto_open = False

    def _render_name_template(self, tmpl):
        """按命名模板渲染文件名主体（不含扩展名）；非法字符统一清理。"""
        try:
            w, h = self._current_resolution()
            try:
                scale = float(self.scale or 1.0)
            except Exception:
                scale = 1.0
            try:
                fps = int(self.fps)
            except Exception:
                fps = 30
            mode = {"full": "全屏", "full_all": "全屏多屏",
                    "region": "区域", "window": "窗口"}.get(self.mode, "全屏")
            rep = {
                "{date}": time.strftime("%Y%m%d"),
                "{time}": time.strftime("%H%M%S"),
                "{datetime}": time.strftime("%Y%m%d_%H%M%S"),
                "{mode}": mode,
                "{resolution}": f"{int(w * scale)}x{int(h * scale)}",
                "{fps}": str(fps),
                "{prefix}": self._safe_prefix(self.prefix),
            }
            base = tmpl
            for k, v in rep.items():
                base = base.replace(k, v)
            base = self._safe_prefix(base)
            return base or (self._safe_prefix(self.prefix) + "_" + time.strftime("%Y%m%d_%H%M%S"))
        except Exception:
            return self._safe_prefix(self.prefix) + "_" + time.strftime("%Y%m%d_%H%M%S")

    @staticmethod
    def _fmt_size(n):
        try:
            n = int(n)
        except Exception:
            return ""
        if n < 1024:
            return f"{n} B"
        if n < 1024 * 1024:
            return f"{n / 1024:.1f} KB"
        if n < 1024 * 1024 * 1024:
            return f"{n / 1024 / 1024:.1f} MB"
        return f"{n / 1024 / 1024 / 1024:.2f} GB"

    _CRF_BITRATE_FACTOR = {
        1: 0.50, 12: 0.18, 18: 0.12, 23: 0.07, 28: 0.045, 34: 0.025,
    }

    def _current_resolution(self):
        mode = self.mode
        if mode == "region" and self.region:
            return self.region[2], self.region[3]
        if mode == "window":
            if self._window_capture_size:
                return self._window_capture_size
            rect = primary_monitor_rect()
            if rect:
                return rect[2], rect[3]
            return 1920, 1080
        rect = primary_monitor_rect()
        if rect:
            return rect[2], rect[3]
        return 1920, 1080

    def _estimate_size_per_min(self):
        ext = FORMAT_OPTIONS.get(self.fmt, "mp4")
        w, h = self._current_resolution()
        # 分辨率缩放：实际编码分辨率
        scale = float(self.scale or 1.0)
        w = int(w * scale)
        h = int(h * scale)
        try:
            fps = int(self.fps)
        except Exception:
            fps = 30
        if ext == "gif":
            fps_gif = min(fps, 25)
            return int(w * h * fps_gif * 0.4 * 60), True
        # 若手动设置了码率，优先按码率估算；否则使用 CRF 经验因子
        if self.bitrate:
            try:
                video_kbps = int(self.bitrate.replace("k", "").replace("K", ""))
            except Exception:
                video_kbps = 0
        else:
            q = QUALITY_PRESETS.get(self.quality, QUALITY_PRESETS[DEFAULT_QUALITY])
            factor = self._CRF_BITRATE_FACTOR.get(q["crf"], 0.07)
            video_kbps = (w * h * fps) * factor / 1000.0
        audio_kbps = 0 if self.audio == "无音频" else (192 if self.audio == AUDIO_SYSTEM_MIC else 160)
        total_kbps = video_kbps + audio_kbps
        return int(total_kbps * 125 * 60), False

    def update_estimate(self):
        try:
            bpm, is_gif = self._estimate_size_per_min()
            if is_gif:
                self.estimate_changed.emit(f"预估（GIF，约）：{self._fmt_size(bpm)}/分钟")
            else:
                self.estimate_changed.emit(f"预估（约）：{self._fmt_size(bpm)}/分钟")
        except Exception:
            self.estimate_changed.emit("预估：—")

    @staticmethod
    def _safe_prefix(prefix):
        if not prefix:
            return "录屏"
        cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", str(prefix))
        cleaned = cleaned.strip(" .")
        return cleaned or "录屏"

    def _default_save_dir(self):
        d = os.path.join(os.path.expanduser("~"), "Desktop", "录屏")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            d = os.path.dirname(os.path.abspath(sys.argv[0]))
        return d

    def _open_folder(self, path):
        try:
            if path and os.path.exists(path):
                os.startfile(os.path.dirname(os.path.abspath(path)))
        except Exception:
            pass

    def _open_file(self, path):
        try:
            if path and os.path.exists(path):
                os.startfile(path)
        except Exception:
            pass

    # --------------------------- 编码器 ---------------------------
    def _detect_hw_encoder(self):
        if self._hw_encoder is not None:
            return self._hw_encoder
        enc = ""
        try:
            proc = subprocess.run(
                [self.ffmpeg, "-hide_banner", "-encoders"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="ignore", creationflags=CREATE_NO_WINDOW, timeout=10)
            text = proc.stdout or ""
            candidates = [n for n in ("h264_nvenc", "h264_qsv", "h264_amf") if n in text]
            for name in candidates:
                test_cmd = [self.ffmpeg, "-hide_banner", "-f", "lavfi", "-i",
                            "color=c=black:s=320x240:d=0.5", "-c:v", name, "-f", "null", "-"]
                try:
                    t0 = subprocess.run(test_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, encoding="utf-8", errors="ignore",
                                        creationflags=CREATE_NO_WINDOW, timeout=20)
                    if t0.returncode == 0:
                        enc = name
                        break
                except Exception:
                    pass
        except Exception:
            pass
        self._hw_encoder = enc
        return enc

    def _video_encoder_args(self, q):
        crf = int(q.get("crf", 23))
        # 手动编码器选择：默认自动走后台硬件探测，否则按用户选择
        enc = self.encoder
        if enc == "默认（自动）":
            if q.get("force_software") or crf <= 0:
                enc = "libx264"
            else:
                enc = self._hw_encoder or "libx264"
        elif enc == "libx264（软件）":
            enc = "libx264"
        # 手动选了不可用的硬件编码器时回退软件，避免录制直接失败
        if enc not in ("libx264", "h264_nvenc", "h264_qsv", "h264_amf"):
            enc = "libx264"
        if self.bitrate:
            args = ["-c:v", enc, "-b:v", self.bitrate]
            if enc == "libx264":
                args += ["-preset", q.get("preset", "medium")]
            return args
        if enc == "libx264":
            return ["-c:v", "libx264", "-preset", q.get("preset", "slow" if crf <= 0 else "medium"), "-crf", str(crf)]
        if enc == "h264_nvenc":
            return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", str(crf)]
        if enc == "h264_qsv":
            return ["-c:v", "h264_qsv", "-global_quality", str(crf), "-preset", "veryfast"]
        if enc == "h264_amf":
            return ["-c:v", "h264_amf", "-quality", "quality", "-qp_i", str(crf), "-qp_p", str(crf)]
        return ["-c:v", "libx264", "-preset", q.get("preset", "medium"), "-crf", str(crf)]

    # --------------------------- 命令构建 ---------------------------
    def _build_command(self, out_path):
        try:
            fps = int(self.fps)
        except Exception:
            fps = 30
        q = QUALITY_PRESETS.get(self.quality, QUALITY_PRESETS[DEFAULT_QUALITY])
        ext = os.path.splitext(out_path)[1].lstrip('.').lower() or FORMAT_OPTIONS.get(self.fmt, "mp4")
        audio_dev = self.audio

        if self.mode == "window":
            hwnd = self._win_hwnd
            if not hwnd:
                return None, "未选择要录制的窗口"
            try:
                u = ctypes.windll.user32
                u.IsIconic.argtypes = [ctypes.c_void_p]
                u.IsIconic.restype = ctypes.c_int
                u.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.RECT)]
                u.GetWindowRect.restype = ctypes.c_int
                if u.IsIconic(hwnd):
                    return None, "所选窗口处于最小化状态，无法录制"
                r = wt.RECT()
                if not u.GetWindowRect(hwnd, ctypes.byref(r)):
                    return None, "无法获取所选窗口的矩形区域"
                w = r.right - r.left
                h = r.bottom - r.top
                if w <= 0 or h <= 0:
                    return None, "所选窗口尺寸无效"
                try:
                    u.SetForegroundWindow.argtypes = [ctypes.c_void_p]
                    u.SetForegroundWindow.restype = ctypes.c_int
                    u.BringWindowToTop.argtypes = [ctypes.c_void_p]
                    u.BringWindowToTop.restype = ctypes.c_int
                    u.SetForegroundWindow(hwnd)
                    u.BringWindowToTop(hwnd)
                except Exception:
                    pass
                self._window_capture_hwnd = hwnd
                self._window_capture_size = (w, h)
                self._window_capture_fps = fps
                if self.window_capture_method == "desktop":
                    # 桌面合成捕获：不依赖 PrintWindow，直接截取窗口所在桌面区域。
                    # 对 DirectX / 游戏等使用独立交换链的窗口更不容易黑屏。
                    # 注意：gdigrab 只能捕获主屏范围（offset 不能为负、不能越出主屏），
                    # 窗口位于副屏/负坐标时自动回退 PrintWindow，避免 ffmpeg 直接报错。
                    prim = primary_monitor_rect()
                    if prim:
                        px, py, pw, ph = prim
                        primary_ok = (r.left >= px and r.top >= py and
                                      r.right <= px + pw and r.bottom <= py + ph)
                    else:
                        primary_ok = False
                    if primary_ok:
                        self._using_raw_window_capture = False
                        self.log_line.emit(
                            f"指定窗口：{get_window_title(hwnd) or hwnd}，尺寸 {w}x{h}，"
                            f"使用桌面合成捕获（DXGI 兼容）")
                        cmd = [self.ffmpeg, "-y", "-f", "gdigrab", "-framerate", str(fps),
                               "-rtbufsize", "100M", "-draw_mouse", "1",
                               "-offset_x", str(r.left), "-offset_y", str(r.top),
                               "-video_size", f"{w}x{h}", "-i", "desktop"]
                    else:
                        self._using_raw_window_capture = True
                        self.log_line.emit(
                            f"指定窗口：{get_window_title(hwnd) or hwnd}，尺寸 {w}x{h}，"
                            f"窗口超出 gdigrab 可捕获的主屏范围，自动回退 PrintWindow")
                        cmd = [self.ffmpeg, "-y", "-f", "rawvideo", "-pix_fmt", "bgra",
                               "-video_size", f"{w}x{h}", "-framerate", str(fps), "-i", "-"]
                else:
                    self._using_raw_window_capture = True
                    self.log_line.emit(
                        f"指定窗口：{get_window_title(hwnd) or hwnd}，尺寸 {w}x{h}，"
                        f"使用 PrintWindow 抓取")
                    cmd = [self.ffmpeg, "-y", "-f", "rawvideo", "-pix_fmt", "bgra",
                           "-video_size", f"{w}x{h}", "-framerate", str(fps), "-i", "-"]
            except Exception as e:
                return None, f"获取窗口区域失败：{e}"
        else:
            self._using_raw_window_capture = False
            cmd = [self.ffmpeg, "-y", "-f", "gdigrab", "-framerate", str(fps),
                   "-rtbufsize", "100M", "-draw_mouse", "1"]
            if self.mode == "region" and self.region:
                x, y, w, h = self.region
                cmd += ["-offset_x", str(x), "-offset_y", str(y), "-video_size", f"{w}x{h}"]
            else:
                if self.mode == "full_all":
                    x, y, w, h = virtual_screen()
                else:
                    rect = primary_monitor_rect()
                    if rect:
                        x, y, w, h = rect
                    else:
                        w = 1920
                        h = 1080
                        x = y = 0
                if w <= 0 or h <= 0:
                    w, h = 1920, 1080
                cmd += ["-offset_x", str(x), "-offset_y", str(y), "-video_size", f"{w}x{h}"]
            cmd += ["-i", "desktop"]

        if audio_dev == "无音频" or ext == "gif":
            cmd += ["-map", "0:v:0"]
        elif audio_dev.startswith("WASAPI: "):
            was_dev = audio_dev[len("WASAPI: "):]
            # v6.0：音频输入队列扩容，提升双输入实时采集稳定性
            cmd += ["-f", "wasapi", "-thread_queue_size", "4096", "-i", was_dev]
            # astats 实时音量统计（pass-through，不改变音频数据；stderr 周期输出 RMS/Peak dB）
            cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-af", ASTATS_META_CHAIN]
        elif audio_dev == AUDIO_SYSTEM_MIC:
            sys_dev = self._resolve_system_audio_device()
            if not sys_dev:
                return None, ("未检测到“立体声混音”设备，无法录制系统声音。"
                              "请开启声卡的立体声混音，或改用“无音频”。")
            mic_dev = self._resolve_mic_device()
            if not mic_dev:
                return None, "未检测到可用麦克风，无法使用“系统声音 + 麦克风混音”。"
            cmd += ["-f", "dshow", "-thread_queue_size", "4096", "-i", f"audio={sys_dev}"]
            cmd += ["-f", "dshow", "-thread_queue_size", "4096", "-i", f"audio={mic_dev}"]
            # v6.0：仅在麦克风输入支路加降噪（afftdn），系统声音保持原样；不影响 astats 统计
            mic_dn = self._mic_denoise_filter()
            if mic_dn:
                fcomplex = (f"[2:a:0]{mic_dn}[mic];[1:a:0][mic]amix=inputs=2:"
                            f"duration=first:dropout_transition=3,{ASTATS_META_CHAIN}[aout]")
            else:
                fcomplex = ("[1:a:0][2:a:0]amix=inputs=2:duration=first:dropout_transition=3,"
                            f"{ASTATS_META_CHAIN}[aout]")
            # filter_complex 内链入 astats 元数据管线（不能与 -af 混用复杂滤镜图）
            cmd += ["-filter_complex", fcomplex]
            cmd += ["-map", "0:v:0", "-map", "[aout]", "-c:a", "aac"]
        else:
            real_audio = audio_dev
            if audio_dev == "系统声音（立体声混音）":
                real_audio = self._resolve_system_audio_device()
                if not real_audio:
                    return None, ("未检测到“立体声混音”设备，无法直接录制系统声音。"
                                  "请开启声卡的立体声混音，或改用“无音频”。")
            cmd += ["-f", "dshow", "-thread_queue_size", "4096", "-i", f"audio={real_audio}"]
            # v6.0：系统声音（立体声混音）不降噪；具体设备（多为麦克风）按开关加 afftdn
            if audio_dev == "系统声音（立体声混音）" or not self.mic_denoise:
                af = ASTATS_META_CHAIN
            else:
                af = self._mic_denoise_filter() + "," + ASTATS_META_CHAIN
            cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-af", af]

        scale = float(self.scale or 1.0)
        if ext == "gif":
            fps_gif = min(fps, 25)
            cmd += ["-vf",
                    f"fps={fps_gif},scale='trunc(iw*{scale}/2)*2':'trunc(ih*{scale}/2)*2':flags=lanczos,"
                    f"split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse",
                    "-f", "gif"]
        else:
            cmd += self._video_encoder_args(q)
            if scale != 1.0:
                vf = (f"scale=trunc(iw*{scale}/2)*2:trunc(ih*{scale}/2)*2:flags=lanczos,"
                      f"crop=trunc(iw/2)*2:trunc(ih/2)*2")
            else:
                vf = "crop=trunc(iw/2)*2:trunc(ih/2)*2"
            cmd += ["-pix_fmt", "yuv420p", "-vf", vf]
            if ext == "mp4":
                cmd += ["-movflags", "+faststart"]
        cmd += [out_path]
        return cmd, None

    def _resolve_system_audio_device(self):
        keywords = ["Stereo Mix", "立体声混音", "What U Hear", "virtual-audio-capturer"]
        for dev in self._audio_devices:
            low = dev.lower()
            if any(kw.lower() in low for kw in keywords):
                return dev
        return None

    def _resolve_mic_device(self):
        """返回用于“系统声音+麦克风混音”的麦克风设备名。"""
        if self.mic and self.mic in self._audio_devices:
            return self.mic
        system_kw = ["Stereo Mix", "立体声混音", "What U Hear", "virtual-audio-capturer"]
        for dev in self._audio_devices:
            low = dev.lower()
            if any(kw.lower() in low for kw in system_kw):
                continue
            return dev
        return None

    def _mic_denoise_filter(self):
        """麦克风降噪滤镜（轻量 afftdn）；未开启返回空串。拼接时如需续接滤镜请自行加逗号。"""
        if not self.mic_denoise:
            return ""
        return "afftdn=nr=12:nf=-28"

    def _audio_should_have_mic(self):
        """判断当前音频模式是否"理应包含有效麦克风输入"（用于静音告警的判定）。"""
        a = self.audio
        if a in ("无音频", "系统声音（立体声混音）"):
            return False
        if a.startswith("WASAPI: "):
            return False  # WASAPI 默认用于系统声音回环，不强制要求麦克风
        return True  # 具体 dshow 设备（多为麦克风）或 系统声音+麦克风混音

    def _reset_audio_health(self):
        """v6.0：重置音频健康（静音）告警状态，并按当前模式设定是否期望麦克风输入。"""
        self._audio_expects_mic = self._audio_should_have_mic()
        self._audio_silent_since = None
        self._audio_silent_warned = False

    # --------------------------- Job Object (防孤儿进程) ---------------------------
    def _make_kill_job(self):
        try:
            k = ctypes.windll.kernel32
            job = k.CreateJobObjectW(None, None)
            if not job:
                return None
            k.CloseHandle.argtypes = [ctypes.c_void_p]
            k.CloseHandle.restype = ctypes.c_int
            k.SetInformationJobObject.argtypes = [
                ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
            k.SetInformationJobObject.restype = ctypes.c_int

            class _BASIC(ctypes.Structure):
                _fields_ = [
                    ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_void_p),
                    ("MaximumWorkingSetSize", ctypes.c_void_p), ("ActiveProcessLimit", ctypes.c_uint32),
                    ("Affinity", ctypes.c_void_p), ("PriorityClass", ctypes.c_uint32),
                    ("SchedulingClass", ctypes.c_uint32),
                ]

            class _EXT(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", _BASIC), ("IoInfo", ctypes.c_byte * 48),
                    ("ProcessMemoryLimit", ctypes.c_void_p), ("JobMemoryLimit", ctypes.c_void_p),
                    ("PeakProcessMemoryUsed", ctypes.c_void_p), ("PeakJobMemoryUsed", ctypes.c_void_p),
                ]

            ext = _EXT()
            ext.BasicLimitInformation.LimitFlags = 0x00002000
            if not k.SetInformationJobObject(job, 9, ctypes.byref(ext), ctypes.sizeof(ext)):
                k.CloseHandle(job)
                return None
            return job
        except Exception:
            return None

    def _attach_ffmpeg_to_job(self, proc):
        job = self._ffmpeg_job
        if not job or not proc:
            return
        try:
            k = ctypes.windll.kernel32
            k.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
            k.OpenProcess.restype = ctypes.c_void_p
            k.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            k.AssignProcessToJobObject.restype = ctypes.c_int
            h = k.OpenProcess(0x1F0FFF, False, proc.pid)
            if h:
                k.AssignProcessToJobObject(job, h)
                k.CloseHandle(h)
        except Exception:
            pass

    # --------------------------- 开始 / 停止 ---------------------------
    def start_record(self, continuing=False):
        if not continuing:
            self._total_recorded = 0
            self._fallback_done = False
        if self._finalizing:
            self.error.emit("正在处理上一次录制，请稍候…")
            return
        if not self.ffmpeg or not os.path.exists(self.ffmpeg):
            self.error.emit("ffmpeg 尚未就绪：请将 ffmpeg.exe 放到程序同目录后重试。")
            return
        if self.mode == "region" and not self.region:
            self.error.emit("请先选择录制区域。")
            return
        if self.mode == "window" and not self._win_hwnd:
            self.error.emit("请先在“指定窗口”下拉框中选择一个窗口。")
            return
        if self.audio.startswith("WASAPI: ") and not self._wasapi_devices:
            self.error.emit("当前 ffmpeg 未提供 WASAPI 设备，无法使用 WASAPI 系统声音捕获。")
            return

        save_dir = self.save_dir
        try:
            os.makedirs(save_dir, exist_ok=True)
        except Exception:
            save_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        free = shutil_disk_free(save_dir)
        if free and free < 200 * 1024 * 1024:
            self.error.emit(f"保存位置剩余空间不足 200MB（当前约 {self._fmt_size(free)}），请清理磁盘或更换保存目录。")
            return

        ts = time.strftime("%Y%m%d_%H%M%S")
        ext = FORMAT_OPTIONS.get(self.fmt, "mp4")
        prefix = self._safe_prefix(self.prefix)
        # 智能命名模板：配置了模板则按模板渲染；否则沿用旧行为「前缀_时间戳」
        tmpl = (self.name_template or "").strip()
        if tmpl:
            base = self._render_name_template(tmpl)
        else:
            base = f"{prefix}_{ts}"
        out_path = os.path.join(save_dir, f"{base}.{ext}")
        crf = QUALITY_PRESETS.get(self.quality, QUALITY_PRESETS[DEFAULT_QUALITY])["crf"]
        force_mkv = (crf <= 0) and ext != "mkv"
        if ext not in ("mkv", "gif") and not force_mkv:
            raw_path = os.path.join(save_dir, f".{base}_raw.mkv")
            self._raw_path = raw_path
            cmd_path = raw_path
        else:
            self._raw_path = None
            cmd_path = out_path
        self._write_path = cmd_path

        cmd, err = self._build_command(cmd_path)
        if cmd is None:
            self.error.emit(err or "构建录制命令失败")
            return

        self.recording = True
        self._finalizing = False
        self._finalize_started = False
        self._out_path = out_path
        self._ffmpeg_log = []
        self._reset_audio_health()  # v6.0：重置音频健康（静音）告警状态
        self.log_line.emit(f"开始录制，目标={out_path}，实际写入={cmd_path}")
        self.status_changed.emit("准备录制…即将开始", C_REC)
        self._start_delayed(cmd)

    def _start_delayed(self, cmd):
        try:
            sec = float(DELAY_OPTIONS.get(self.delay, 1.0))
        except Exception:
            sec = 1.0
        if sec <= 0:
            self._really_start(cmd)
            return
        if sec < 1:
            self.status_changed.emit("即将开始录制…", C_REC)
            self._delay_timer = QTimer(self)
            self._delay_timer.setSingleShot(True)
            self._delay_timer.timeout.connect(lambda: self._really_start(cmd))
            self._delay_timer.start(int(sec * 1000))
            return
        self._countdown(int(round(sec)), cmd)

    def _countdown(self, n, cmd):
        if not self.recording:
            return
        if n > 0:
            self.status_changed.emit(f"{n} · 即将开始录制", C_REC)
            self._delay_timer = QTimer(self)
            self._delay_timer.setSingleShot(True)
            self._delay_timer.timeout.connect(lambda: self._countdown(n - 1, cmd))
            self._delay_timer.start(1000)
        else:
            self._really_start(cmd)

    def _really_start(self, cmd):
        self._delay_timer = None
        if not self.recording:
            return
        try:
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW)
            self._log_thread = threading.Thread(target=self._pump_log, daemon=True)
            self._log_thread.start()
            self._attach_ffmpeg_to_job(self.proc)
            self.log_line.emit("ffmpeg 已启动: " + " ".join(cmd))
            if self._using_raw_window_capture:
                self._window_capture_thread = threading.Thread(
                    target=self._window_capture_worker,
                    args=(self.proc, self._window_capture_hwnd,
                          self._window_capture_size, self._window_capture_fps),
                    daemon=True)
                self._window_capture_thread.start()
        except FileNotFoundError:
            self.recording = False
            self.error.emit(f"未找到 ffmpeg：{self.ffmpeg}")
            return
        except Exception as e:
            self.recording = False
            self.error.emit(f"启动失败：{e}")
            return

        self.start_time = time.time()
        self.status_changed.emit(f"录制中 → {os.path.basename(self._out_path)}", C_REC)
        self.started.emit()
        self._start_schedule_timers()
        self._monitor_thread = threading.Thread(target=self._monitor_ffmpeg, daemon=True)
        self._monitor_thread.start()

    # ---------------- 录制计划 / 时长上限 / 自动分段 ----------------
    def _start_schedule_timers(self):
        self._stop_schedule_timers()
        remaining = None
        if self.max_duration_seconds > 0:
            remaining = self.max_duration_seconds - self._total_recorded
            if remaining <= 0:
                remaining = 1
            self._max_timer = QTimer(self)
            self._max_timer.setSingleShot(True)
            self._max_timer.timeout.connect(self._on_max_duration_timeout)
            self._max_timer.start(remaining * 1000)
        if self.segment_seconds > 0:
            interval = self.segment_seconds
            if remaining is not None and interval >= remaining:
                # 分段间隔不小于剩余总时长时，不再分段，交给时长上限停止
                pass
            else:
                self._segment_timer = QTimer(self)
                self._segment_timer.setInterval(interval * 1000)
                self._segment_timer.timeout.connect(self._on_segment_timeout)
                self._segment_timer.start()

    def _stop_schedule_timers(self):
        for attr in ("_max_timer", "_segment_timer"):
            t = getattr(self, attr, None)
            if t is not None:
                try:
                    t.stop()
                except Exception:
                    pass
                setattr(self, attr, None)

    def _on_max_duration_timeout(self):
        if self.recording and not self._finalizing:
            self.log_line.emit("已达录制时长上限，自动停止")
            self._auto_restart_reason = None
            self.stop_record()

    def _on_segment_timeout(self):
        if self.recording and not self._finalizing:
            mins = max(1, self.segment_seconds // 60)
            self.log_line.emit(f"自动分段（每 {mins} 分钟），正在保存当前分段并继续")
            self._auto_restart_reason = "segment"
            self.stop_record(auto=True)

    @staticmethod
    def _extract_rms_db(line):
        """从 astats 元数据行解析 Overall RMS dB（无匹配返回 None）。"""
        try:
            m = _RE_ASTATS_RMS.search(line)
            return float(m.group(1)) if m else None
        except Exception:
            return None

    @staticmethod
    def _extract_peak_db(line):
        """从 astats 元数据行解析 Overall Peak dB（无匹配返回 None）。"""
        try:
            m = _RE_ASTATS_PEAK.search(line)
            return float(m.group(1)) if m else None
        except Exception:
            return None

    def feed_audio_meta_line(self, line):
        """单行 astats 元数据 → 状态机：Peak 先记录，RMS 齐了融合出电平。

        v5.0 规则：
        - RMS 为主体响度、Peak 为瞬时峰值（-3dB 留过载头）→ disp = max(rms, peak-3)；
        - 显示映射暂保持 -60~0 dBFS（+6dB 增益与范围收窄属隐患1/4 联动项，待诊断日志实测后定）；
        - 每帧一个完整窗口只出一次电平（Peak 行仅记录，RMS 行触发输出并清窗）。
        返回 0.0~1.0 电平；无匹配/未成对返回 None。
        """
        try:
            peak = self._extract_peak_db(line)
            if peak is not None:
                self._audio_peak_db = peak
                return None
            rms = self._extract_rms_db(line)
            if rms is None:
                return None
            peak = self._audio_peak_db
            self._audio_peak_db = None  # 本窗口结束，等待下一帧
            disp = rms if peak is None else max(rms, peak - 3.0)
            lv = max(0.0, min(1.0, (disp + 60.0) / 60.0))
            # v6.0：音频健康（静音）告警——仅"应有麦克风输入"的模式，且只告警不自动停止
            if self._audio_expects_mic and disp < -75.0:
                now = time.monotonic()
                if self._audio_silent_since is None:
                    self._audio_silent_since = now
                elif (not self._audio_silent_warned) and (now - self._audio_silent_since >= 1.5):
                    self._audio_silent_warned = True
                    self.log_line.emit(
                        "[音频警告] 麦克风已打开但未检测到有效输入"
                        "（请检查 Windows 麦克风权限/是否被静音/输入设备选择是否正确）")
            else:
                self._audio_silent_since = None
                self._audio_silent_warned = False
            # 诊断日志（节流 0.5s）：设备/后端/RMS/Peak/显示电平/格数，供实测定标
            if AUDIO_DEBUG_LOG:
                now = time.monotonic()
                if now - self._audio_debug_ts >= 0.5:
                    self._audio_debug_ts = now
                    p_str = f"{peak:.1f}" if peak is not None else "--"
                    backend = "wasapi" if self.audio.startswith("WASAPI: ") else "dshow"
                    self.log_line.emit(
                        f"[电平诊断] 设备={self.audio} 后端={backend} "
                        f"RMS={rms:.1f}dB  Peak={p_str}dB  "
                        f"显示={disp:.1f}dB  电平={lv:.2f}  格数={round(lv * 16)}/16")
            return lv
        except Exception:
            return None

    def build_audio_test_command(self):
        """构建「录音测试」专用 ffmpeg 命令：打开当前所选音频源，astats 元数据统计并丢弃输出。

        v5.0：与真实录制使用同一设备/同一滤镜链路（混音模式同样 amix），
        保证「测试电平 = 实际录制电平」；仅在测试期间运行，结束后 kill 释放设备。
        """
        audio_dev = self.audio
        if not audio_dev or audio_dev == "无音频":
            return None
        try:
            if audio_dev.startswith("WASAPI: "):
                dev = audio_dev[len("WASAPI: "):]
                return [self.ffmpeg, "-f", "wasapi", "-thread_queue_size", "4096", "-i", dev,
                        "-af", ASTATS_META_CHAIN, "-f", "null", "-"]
            if audio_dev == AUDIO_SYSTEM_MIC:
                sys_dev = self._resolve_system_audio_device()
                mic = self._resolve_mic_device()
                if not sys_dev or not mic:
                    return None
                # v5.0 修复：混音测试必须与真实录制一致（system + mic → amix → astats）
                mic_dn = self._mic_denoise_filter()
                if mic_dn:
                    fcomplex = (f"[1:a:0]{mic_dn}[mic];[0:a:0][mic]amix=inputs=2:"
                                f"duration=first:dropout_transition=3,{ASTATS_META_CHAIN}")
                else:
                    fcomplex = (f"[0:a:0][1:a:0]amix=inputs=2:duration=first:"
                                f"dropout_transition=3,{ASTATS_META_CHAIN}")
                return [self.ffmpeg, "-f", "dshow", "-thread_queue_size", "4096", "-i", f"audio={sys_dev}",
                        "-f", "dshow", "-thread_queue_size", "4096", "-i", f"audio={mic}",
                        "-filter_complex", fcomplex, "-f", "null", "-"]
            if audio_dev == "系统声音（立体声混音）":
                dev = self._resolve_system_audio_device()
                if not dev:
                    return None
                return [self.ffmpeg, "-f", "dshow", "-thread_queue_size", "4096", "-i", f"audio={dev}",
                        "-af", ASTATS_META_CHAIN, "-f", "null", "-"]
            # 具体设备名（dshow，通常为麦克风）：按开关加降噪
            af = ASTATS_META_CHAIN if not self.mic_denoise else (self._mic_denoise_filter() + "," + ASTATS_META_CHAIN)
            return [self.ffmpeg, "-f", "dshow", "-thread_queue_size", "4096", "-i", f"audio={audio_dev}",
                    "-af", af, "-f", "null", "-"]
        except Exception:
            return None

    def _pump_log(self):
        try:
            self._log_buffer = ""
            while True:
                chunk = self.proc.stderr.read(4096)
                if not chunk:
                    break
                self._log_buffer += chunk.decode(errors="ignore")
                if len(self._log_buffer) > 16384:
                    self._log_buffer = self._log_buffer[-16384:]
                while "\n" in self._log_buffer:
                    line, self._log_buffer = self._log_buffer.split("\n", 1)
                    line = line.strip("\r").strip()
                    if not line:
                        continue
                    # v5.0：astats 元数据行（每帧约30行）只用于电平解析，不进可视日志
                    if "lavfi.astats." not in line:
                        self._ffmpeg_log.append(line)
                        if len(self._ffmpeg_log) > 300:
                            self._ffmpeg_log.pop(0)
                    # 实时电平：astats 元数据状态机（后台线程 emit → 主线程 Queued 接收）
                    lv = self.feed_audio_meta_line(line)
                    if lv is not None:
                        self.audio_level.emit(lv)
            if self._log_buffer.strip() and "lavfi.astats." not in self._log_buffer:
                self._ffmpeg_log.append(self._log_buffer.strip())
        except Exception:
            pass

    def _window_capture_worker(self, proc, hwnd, size, fps):
        if not hwnd or not size:
            return
        w, h = size
        interval = 1.0 / max(1, int(fps or 30))
        fail_count = 0
        size_warned = False
        while self.recording and proc.poll() is None:
            started = time.monotonic()
            frame = capture_window_bgra(hwnd)
            if frame is None:
                fail_count += 1
                if fail_count >= 30:
                    self.log_line.emit("指定窗口抓帧连续失败，停止喂帧")
                    try:
                        if proc.stdin:
                            proc.stdin.close()
                    except Exception:
                        pass
                    break
                time.sleep(0.05)
                continue
            fail_count = 0
            data, fw, fh = frame
            if (fw, fh) != (w, h):
                if not size_warned:
                    self.log_line.emit(
                        f"窗口尺寸变化：{fw}x{fh} != {w}x{h}，自动重启录制以适配新尺寸")
                    size_warned = True
                self._auto_restart_reason = "resize"
                try:
                    if proc.stdin:
                        proc.stdin.close()
                except Exception:
                    pass
                break
            try:
                if proc.stdin is None:
                    break
                proc.stdin.write(data)
                proc.stdin.flush()
            except Exception:
                break
            elapsed = time.monotonic() - started
            if elapsed < interval:
                time.sleep(interval - elapsed)

    def _monitor_ffmpeg(self):
        proc = self.proc
        try:
            if proc:
                proc.wait()
        except Exception:
            return
        if self.recording and not self._finalizing:
            if self._auto_restart_reason:
                self.log_line.emit("检测到 ffmpeg 已退出（自动重启场景），进入收尾…")
                self.stop_record(auto=True)
            elif not self._fallback_done and (self._hw_encoder or self.encoder not in ("默认（自动）", "libx264（软件）")):
                # 编码器启动失败/异常退出时，自动回退 libx264 再试一次
                self._fallback_done = True
                self._hw_encoder = None
                self.encoder = "libx264（软件）"
                self._auto_restart_reason = "fallback"
                self.log_line.emit("检测到编码失败，自动切换 libx264 软件编码重试…")
                self.stop_record(auto=True)
            else:
                self.log_line.emit("检测到 ffmpeg 已退出（非用户停止），进入收尾…")
                self.stop_record()

    def stop_record(self, auto=False):
        if not self.recording:
            return
        if self._finalizing:
            return
        if not auto:
            self._auto_restart_reason = None
        # 注意：不在 stop_record 里 stop QTimer，避免后台线程调用导致线程安全问题；
        # 旧定时器在回调里会因 recording/finalizing 状态直接返回，下次 start_record 会统一清理。
        if self.proc is None and getattr(self, "_delay_timer", None) is not None:
            try:
                self._delay_timer.stop()
                self._delay_timer = None
            except Exception:
                pass
            self.recording = False
            self._write_path = None
            self.status_changed.emit("已取消录制", C_SUB)
            return
        if self.proc is None:
            self.recording = False
            self._write_path = None
            self.status_changed.emit("录制状态异常，已重置", C_SUB)
            return

        self._finalizing = True
        self._write_path = None
        proc = self.proc
        try:
            if proc.stdin:
                if self._using_raw_window_capture:
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass
                else:
                    try:
                        proc.stdin.write(b"q")
                        proc.stdin.flush()
                    finally:
                        try:
                            proc.stdin.close()
                        except Exception:
                            pass
        except Exception:
            pass
        # 阻塞等待/收尾放到后台线程，避免点击“停止”时 UI 卡顿
        def stop_worker():
            try:
                proc.wait(timeout=8)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=CREATE_NO_WINDOW, timeout=5)
                    except Exception:
                        pass
            try:
                if self._log_thread:
                    self._log_thread.join(timeout=3)
            except Exception:
                pass
            try:
                if self._window_capture_thread:
                    self._window_capture_thread.join(timeout=3)
                    self._window_capture_thread = None
            except Exception:
                pass

            self.recording = False
            self.proc = None
            dur = int(time.time() - self.start_time)
            self._total_recorded += dur
            self.status_changed.emit("正在校验录制文件…", C_SUB)
            final_path = self._out_path
            raw_path = self._raw_path
            verify_path = raw_path or final_path
            self.log_line.emit(f"停止录制，校验文件={verify_path}，最终文件={final_path}")
            self._finalize_after_proc(verify_path, final_path, raw_path, dur)

        threading.Thread(target=stop_worker, daemon=True).start()

    def _finalize_after_proc(self, verify_path, final_path, raw_path, dur):
        if self._finalize_started:
            return
        self._finalize_started = True

        def verify_worker():
            ok, reason = self._verify_output(verify_path)
            self._on_verify_done(ok, reason, verify_path, final_path, raw_path, dur)

        threading.Thread(target=verify_worker, daemon=True).start()

    def _on_verify_done(self, ok, reason, verify_path, final_path, raw_path, dur):
        if not ok:
            self._finalizing = False
            self._finalize_started = False
            if self._auto_restart_reason == "fallback":
                self._auto_restart_reason = None
                self.log_line.emit("首段编码失败，已切换 libx264 重试")
                self.restart_requested.emit()
                return
            self._auto_restart_reason = None
            self.error.emit(f"录制校验失败：{reason}\n文件：{verify_path}")
            self.status_changed.emit("录制失败，详见提示", C_REC)
            return
        if not raw_path or raw_path == final_path:
            self._finalizing = False
            if self._auto_restart_reason:
                reason = self._auto_restart_reason
                self._auto_restart_reason = None
                self.log_line.emit(f"分段已保存：{final_path}，继续录制")
                self.status_changed.emit("自动分段，继续录制…", C_SUB)
                self.restart_requested.emit()
                return
            self.log_line.emit(f"录制完成：{final_path}")
            self.status_changed.emit(f"已完成：{os.path.basename(final_path)} （{dur}秒）", C_OK)
            self.finished.emit(final_path)
            if self.auto_open:
                self._open_folder(final_path)
            return
        self.status_changed.emit("正在转换封装格式…", C_SUB)

        def remux_worker():
            success, err = self._remux_to_final(raw_path, final_path)
            self._on_remux_done(success, err, raw_path, final_path, dur)

        threading.Thread(target=remux_worker, daemon=True).start()

    def _remux_to_final(self, raw_path, final_path):
        try:
            cmd = [self.ffmpeg, "-y", "-i", raw_path, "-map", "0", "-c", "copy"]
            ext = os.path.splitext(final_path)[1].lstrip('.').lower()
            if ext in ("mp4", "mov"):
                cmd += ["-movflags", "+faststart"]
            cmd += [final_path]
            r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               creationflags=CREATE_NO_WINDOW, timeout=120)
            if r.returncode != 0:
                return False, r.stderr.decode(errors="ignore")[-500:]
            return True, ""
        except Exception as e:
            return False, str(e)

    def _on_remux_done(self, success, err, raw_path, final_path, dur):
        self._finalizing = False
        if success:
            try:
                if os.path.exists(raw_path):
                    os.remove(raw_path)
            except Exception:
                pass
            self._raw_path = None
            self.log_line.emit(f"转换封装完成：{final_path}")
            if self._auto_restart_reason:
                reason = self._auto_restart_reason
                self._auto_restart_reason = None
                self.status_changed.emit("自动分段，继续录制…", C_SUB)
                self.restart_requested.emit()
                return
            self.status_changed.emit(f"已完成：{os.path.basename(final_path)} （{dur}秒）", C_OK)
            self.finished.emit(final_path)
            if self.auto_open:
                self._open_folder(final_path)
        else:
            self._auto_restart_reason = None
            self.log_line.emit(f"转换封装失败：{err}")
            self.status_changed.emit("录制成功，但格式转换失败（MKV 已保留）", C_REC)
            self.finished.emit(raw_path or final_path)

    def _verify_output(self, path):
        if not path or not os.path.exists(path):
            return False, "未生成录制文件"
        try:
            sz = os.path.getsize(path)
        except Exception:
            return False, "无法读取录制文件"
        if sz == 0:
            return False, "文件大小为 0 字节（录制未成功写入）"
        try:
            v = subprocess.run(
                [self.ffmpeg, "-v", "error", "-i", path, "-c", "copy", "-f", "null", "-"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW, timeout=20)
            if v.returncode != 0:
                return False, "文件无法被 ffmpeg 正常解析（可能损坏或不完整）"
        except Exception:
            return False, "文件校验失败"
        return True, ""

    def refresh_audio_devices(self):
        def worker():
            try:
                devs = list_audio_devices(self.ffmpeg)
                was = list_wasapi_devices(self.ffmpeg)
                self._audio_devices = devs
                self._wasapi_devices = was
                items = ["无音频", "系统声音（立体声混音）", AUDIO_SYSTEM_MIC] + devs
                for d in was:
                    items.append("WASAPI: " + d)
                self.audio_items_ready.emit(items)
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()


def shutil_disk_free(path):
    try:
        import shutil
        return shutil.disk_usage(path).free
    except Exception:
        return 0


# =====================================================================
#  区域选择（Qt 全屏透明覆盖 + 橡皮筋矩形）
# =====================================================================
class RegionSelector(QWidget):
    region_selected = Signal(object)   # (x, y, w, h) 物理像素 或 None

    _SNAP = 8      # 吸附阈值（像素）
    _MAG = 44      # 放大镜取样边长
    _ZOOM = 2      # 放大倍数

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setCursor(Qt.CrossCursor)
        vx, vy, vw, vh = virtual_screen()
        self.setGeometry(vx, vy, vw, vh)
        self.start = QPoint()
        self.end = QPoint()
        self.active = False
        self._mouse = QPoint(vx + vw // 2, vy + vh // 2)
        self._mag_cache = None   # ((x, y), pixmap)
        # 框选开始时采集可见窗口矩形（供边缘吸附），排除占满全屏的选区窗自身
        self._win_rects = self._windows_rects()
        self.label = QLabel("拖拽选择录制区域 · ESC 取消", self)
        self.label.setStyleSheet(
            "color:#fff;background:rgba(10,132,255,0.85);padding:6px 12px;border-radius:8px;font-size:10pt;")
        self.label.adjustSize()
        self.label.move(20, 20)

    def _windows_rects(self):
        """枚举可见非最小化窗口矩形（排除面积超过屏幕 80% 的全屏/选区窗口）。"""
        rects = []
        try:
            u = ctypes.windll.user32
            u.EnumWindows.restype = ctypes.c_bool
            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            data = []

            def cb(hwnd, lparam):
                try:
                    if u.IsWindowVisible(hwnd) and not u.IsIconic(hwnd):
                        r = wt.RECT()
                        if u.GetWindowRect(hwnd, ctypes.byref(r)):
                            data.append((r.left, r.top, r.right, r.bottom))
                except Exception:
                    pass
                return True

            u.EnumWindows(WNDENUMPROC(cb), 0)
            vx, vy, vw, vh = virtual_screen()
            screen_area = vw * vh
            for l, t, r, b in data:
                w = max(0, r - l)
                h = max(0, b - t)
                if w <= 0 or h <= 0:
                    continue
                if w * h > screen_area * 0.8:
                    continue  # 排除选区窗自身/全屏窗
                rects.append((l, t, r, b))
        except Exception:
            pass
        return rects

    def _snap_point(self, p):
        """把拖拽点吸附到附近窗口边缘（阈值 8px）。"""
        x, y = p.x(), p.y()
        try:
            for (l, t, r, b) in self._win_rects:
                if abs(x - l) <= self._SNAP:
                    x = l
                elif abs(x - r) <= self._SNAP:
                    x = r
                if abs(y - t) <= self._SNAP:
                    y = t
                elif abs(y - b) <= self._SNAP:
                    y = b
        except Exception:
            pass
        return QPoint(x, y)

    def _magnifier(self, center):
        """光标旁像素级放大镜：对屏幕小区域取样后放大 2 倍（带位置缓存）。"""
        try:
            if self._mag_cache and self._mag_cache[0] == (center.x(), center.y()):
                return self._mag_cache[1]
            s = self._MAG
            screen = QApplication.primaryScreen()
            x = int(center.x() - s // 2)
            y = int(center.y() - s // 2)
            pm = screen.grabWindow(0, x, y, s, s)
            if pm.isNull():
                return None
            big = pm.scaled(s * self._ZOOM, s * self._ZOOM,
                            Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._mag_cache = ((center.x(), center.y()), big)
            return big
        except Exception:
            return None

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 90))
        if self.active:
            r = QRect(self.start, self.end).normalized()
            p.setPen(QPen(QColor(C_ACCENT), 2))
            p.setBrush(QColor(10, 132, 255, 40))
            p.drawRect(r)
            # 尺寸提示
            p.setPen(QColor("#ffffff"))
            p.drawText(r.left() + 6, r.top() - 8,
                       f"{int(r.width() * get_dpi_scale())} × {int(r.height() * get_dpi_scale())}")
        # 放大镜（跟随光标）
        mag = self._magnifier(self._mouse)
        if mag is not None:
            mw, mh = mag.width(), mag.height()
            cx, cy = self._mouse.x(), self._mouse.y()
            mx = cx + 18
            my = cy + 18
            if mx + mw > self.width():
                mx = cx - mw - 18
            if my + mh > self.height():
                my = cy - mh - 18
            p.setPen(QPen(QColor(C_ACCENT), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(mx - 1, my - 1, mw + 2, mh + 2, 6, 6)
            p.drawPixmap(mx, my, mag)
        p.end()

    def mousePressEvent(self, ev):
        self.start = ev.globalPosition().toPoint()
        self.end = self.start
        self._mouse = self.start
        self.active = True
        self.update()

    def mouseMoveEvent(self, ev):
        p = ev.globalPosition().toPoint()
        self._mouse = p
        if self.active:
            self.end = self._snap_point(p)
            self._mag_cache = None
        self.update()

    def mouseReleaseEvent(self, ev):
        if not self.active:
            return
        self.active = False
        r = QRect(self.start, self.end).normalized()
        if r.width() < 10 or r.height() < 10:
            self.region_selected.emit(None)
            self.close()
            return
        scale = get_dpi_scale()
        x = int(r.x() * scale)
        y = int(r.y() * scale)
        w = int(r.width() * scale)
        h = int(r.height() * scale)
        self.region_selected.emit((x, y, w, h))
        self.close()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.region_selected.emit(None)
            self.close()


# =====================================================================
#  悬浮窗（沿用已验证样式：置顶 / 可拖动 / 计时 + 实时大小 + 停止）
# =====================================================================
class FloatingWidget(QWidget):
    stop_requested = Signal()
    show_main_requested = Signal()          # 右键：显示主窗口
    open_last_requested = Signal()          # 右键：打开上一条录像
    hide_requested = Signal()               # 右键：隐藏悬浮窗
    mode_requested = Signal(str)            # 右键：切换录制模式（"full" / "window"）

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(330, 68)
        card = QWidget(self)
        card.setObjectName("fcard")
        card.setGeometry(8, 8, 314, 52)
        card.setStyleSheet("#fcard{background:rgba(28,28,30,0.94);border-radius:16px;}")
        lay = QHBoxLayout(card)
        lay.setContentsMargins(16, 0, 14, 0)
        lay.setSpacing(8)
        self.dot = QLabel("●")
        self.dot.setFixedWidth(14)
        self.dot.setStyleSheet("color:#ff453a;font-size:10pt;")
        self.timer = QLabel("00:00:00")
        self.timer.setMinimumWidth(80)
        self.timer.setStyleSheet("color:#fff;font:600 15px Consolas;")
        self.meter = AudioPeakMeter()
        self.meter.setFixedSize(64, 16)
        self.size = QLabel("")
        self.size.setMinimumWidth(56)
        self.size.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.size.setStyleSheet("color:#c7c7cc;font:12px Consolas;")
        self.btn = QPushButton("开始")
        self.btn.setFixedSize(68, 32)
        self.btn.setStyleSheet(
            "QPushButton{background:#34c759;color:#fff;border:none;border-radius:9px;"
            "font-size:13px;font-weight:500;}"
            "QPushButton:hover{background:#44d767;}")
        self.btn.clicked.connect(self.stop_requested.emit)
        lay.addWidget(self.dot)
        lay.addWidget(self.timer)
        lay.addWidget(self.meter)
        lay.addWidget(self.size, 1)
        lay.addWidget(self.btn)
        self._drag = None
        self._level = 0.0
        self._compact = False  # 极简模式标志
        # 闲时半透明：鼠标离开 3 秒后淡化至 0.6，悬停恢复 0.95
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.setInterval(3000)
        self._idle_timer.timeout.connect(lambda: self._animate_opacity(0.6))
        self.position_topright()

    def position_topright(self):
        try:
            sg = QApplication.primaryScreen().geometry()
            self.move(sg.width() - self.width() - 20, 20)
        except Exception:
            pass

    def _animate_opacity(self, target):
        try:
            anim = QPropertyAnimation(self, b"windowOpacity", self)
            anim.setDuration(250)
            anim.setStartValue(self.windowOpacity())
            anim.setEndValue(target)
            anim.setEasingCurve(QEasingCurve.InOutCubic)
            self._opacity_anim = anim  # 防止被 GC
            anim.start()
        except Exception:
            try:
                self.setWindowOpacity(target)
            except Exception:
                pass

    def set_timer(self, t):
        self.timer.setText(t)

    def set_size(self, s):
        self.size.setText(s)

    def set_level(self, v):
        try:
            v = max(0.0, min(1.0, float(v)))
        except Exception:
            v = 0.0
        self.meter.set_level(v)

    def set_compact(self, on):
        """切换极简胶囊形态：仅保留红点与计时器，隐藏电平条/文件大小/按钮。"""
        self._compact = on
        self.meter.setVisible(not on)
        self.size.setVisible(not on)
        self.btn.setVisible(not on)
        if on:
            self.resize(180, 68)
            card = self.findChild(QWidget, "fcard")
            if card:
                card.setGeometry(8, 8, 164, 52)
        else:
            self.resize(330, 68)
            card = self.findChild(QWidget, "fcard")
            if card:
                card.setGeometry(8, 8, 314, 52)

    def set_idle(self):
        self.timer.setText("00:00:00")
        self.size.setText("")
        self.meter.set_level(0)
        self.btn.setText("开始")
        self.btn.setEnabled(True)
        self.btn.setStyleSheet(
            "QPushButton{background:#34c759;color:#fff;border:none;border-radius:9px;"
            "font-size:13px;font-weight:500;}"
            "QPushButton:hover{background:#44d767;}")

    def set_recording(self):
        self.btn.setText("■ 停止")
        self.btn.setEnabled(True)
        self.btn.setStyleSheet(
            "QPushButton{background:#ff453a;color:#fff;border:none;border-radius:9px;"
            "font-size:13px;font-weight:500;}"
            "QPushButton:hover{background:#ff5e54;}")

    def set_stopping(self):
        self.btn.setText("停止中…")
        self.btn.setEnabled(False)
        self.btn.setStyleSheet(
            "QPushButton{background:#8e8e93;color:#fff;border:none;border-radius:9px;"
            "font-size:13px;font-weight:500;}")

    # ---------------- 拖动 + 贴边吸附 ----------------
    def mousePressEvent(self, ev):
        self._drag = ev.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, ev):
        if self._drag is not None:
            self.move(ev.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, ev):
        self._drag = None
        self._snap_to_edge()

    def _snap_to_edge(self):
        """拖到屏幕四边 20px 内时自动吸附贴边。"""
        try:
            sg = QApplication.primaryScreen().availableGeometry()
            x, y = self.x(), self.y()
            if abs(x - sg.left()) <= 20:
                x = sg.left()
            elif abs((x + self.width()) - sg.right()) <= 20:
                x = sg.right() - self.width() + 1
            if abs(y - sg.top()) <= 20:
                y = sg.top()
            elif abs((y + self.height()) - sg.bottom()) <= 20:
                y = sg.bottom() - self.height() + 1
            self.move(x, y)
        except Exception:
            pass

    # ---------------- 闲时半透明 ----------------
    def enterEvent(self, ev):
        try:
            self._idle_timer.stop()
            self._animate_opacity(0.95)
        except Exception:
            pass
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        try:
            self._idle_timer.start()
        except Exception:
            pass
        super().leaveEvent(ev)

    # ---------------- 右键快捷菜单 ----------------
    def contextMenuEvent(self, ev):
        try:
            menu = QMenu(self)
            a_show = menu.addAction("显示主窗口")
            a_full = menu.addAction("切换为全屏录制")
            a_win = menu.addAction("切换为窗口录制")
            a_open = menu.addAction("打开上一条录像")
            menu.addSeparator()
            a_compact = menu.addAction("极简模式" if not self._compact else "正常模式")
            menu.addSeparator()
            a_hide = menu.addAction("隐藏悬浮窗")
            act = menu.exec(ev.globalPos())
            if act == a_show:
                self.show_main_requested.emit()
            elif act == a_full:
                self.mode_requested.emit("full")
            elif act == a_win:
                self.mode_requested.emit("window")
            elif act == a_open:
                self.open_last_requested.emit()
            elif act == a_compact:
                self.set_compact(not self._compact)
            elif act == a_hide:
                self.hide_requested.emit()
        except Exception:
            pass


# =====================================================================
#  录制圆环（QPainter 绘制：空闲=红圆点，录制=红圆环 + 呼吸光晕）
# =====================================================================
class RecordingRing(QWidget):
    clicked = Signal()

    def __init__(self, parent=None, col=None):
        super().__init__(parent)
        self.col = col or LIGHT_THEME
        self.setFixedSize(150, 150)
        self.recording = False
        self._t = "00:00:00"
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._animate)
        self._timer.start()

    def _animate(self):
        if self.recording:
            self._phase = (self._phase + 0.045) % 1.0
        self.update()

    def set_recording(self, r):
        self.recording = r
        self.update()

    def set_time(self, t):
        if t != self._t:
            self._t = t
            if not self.recording:
                self.update()

    def mousePressEvent(self, ev):
        self.clicked.emit()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        cx = cy = w / 2.0
        R = w / 2.0 - 16

        def ellipse(radius):
            # 修正：drawEllipse(x,y,w,h) 的 (x,y) 是外接矩形左上角，不是圆心
            return QRectF(cx - radius, cy - radius, radius * 2, radius * 2)

        # 录制时的呼吸光晕
        if self.recording:
            glow = 0.5 + 0.5 * math.sin(self._phase * 2 * math.pi)
            p.setBrush(QColor(255, 59, 48, int(28 + 42 * glow)))
            p.setPen(Qt.NoPen)
            p.drawEllipse(ellipse(R + 16))
        # 轨道圆环
        p.setPen(QPen(QColor(self.col["line"]), 7, Qt.SolidLine, Qt.RoundCap))
        p.drawEllipse(ellipse(R))
        if self.recording:
            # 红色状态环（中心不再显示计时，保持简洁）
            p.setPen(QPen(QColor(self.col["rec"]), 7, Qt.SolidLine, Qt.RoundCap))
            p.drawEllipse(ellipse(R))
        else:
            # 中心红色圆点（开始按钮隐喻）
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(self.col["rec"]))
            p.drawEllipse(ellipse(R - 16))
        p.end()


# =====================================================================
#  日志面板（深色 + 分级配色）
# =====================================================================
# =====================================================================
#  标题栏（无边框可拖动 + 最小化 / 收起托盘）
# =====================================================================
class TitleBar(QWidget):
    def __init__(self, parent, col=None):
        super().__init__(parent)
        self.parent = parent
        self.col = col or LIGHT_THEME
        self.setFixedHeight(46)
        self.setStyleSheet("background:transparent;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 0, 14, 0)
        lay.setSpacing(8)

        # macOS 红绿灯：红=关闭到托盘，黄=最小化，绿=最大化/还原
        self.btn_close = QPushButton()
        self.btn_min = QPushButton()
        self.btn_max = QPushButton()
        for b, color in (
                (self.btn_close, "#ff5f57"),
                (self.btn_min, "#febc2e"),
                (self.btn_max, "#28c840")):
            b.setFixedSize(14, 14)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                f"QPushButton{{background:{color};border:none;border-radius:7px;}}"
                f"QPushButton:hover{{background:{color};border:1px solid rgba(0,0,0,0.15);}}")
        self.btn_close.clicked.connect(lambda: self.parent.hide_to_tray())
        self.btn_min.clicked.connect(lambda: self.parent.showMinimized())
        self.btn_max.clicked.connect(self._toggle_max)
        lay.addWidget(self.btn_close)
        lay.addWidget(self.btn_min)
        lay.addWidget(self.btn_max)
        # 标题栏只保留红绿灯，不显示程序中英文名
        lay.addStretch(1)
        self._drag = None

    def apply_theme(self, col):
        self.col = col

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


# =====================================================================
#  自定义快捷键捕获对话框
# =====================================================================
class HotkeyDialog(QDialog):
    def __init__(self, parent=None, current="", col=None):
        super().__init__(parent)
        self.col = col or LIGHT_THEME
        self.setWindowTitle("设置快捷键")
        self.setModal(True)
        self.setMinimumWidth(320)
        self.setStyleSheet(
            f"QDialog{{background:{self.col['card']};color:{self.col['text']};}}"
            f"QLabel{{color:{self.col['text']};}}"
            f"QPushButton{{background:{self.col['hover_bg']};color:{self.col['text']};"
            f"border:none;border-radius:8px;padding:6px 18px;}}"
            f"QPushButton:hover{{background:{self.col['pressed_bg']};}}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(10)
        tip = QLabel("请按下新的快捷键（支持 Ctrl / Shift / Alt / Win 组合）\n按 Esc 取消")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        self.result_text = current or ""
        self.value_label = QLabel(self.result_text or "（未设置）")
        self.value_label.setAlignment(Qt.AlignCenter)
        self.value_label.setStyleSheet(
            f"font-size:16pt;font-weight:600;padding:8px;"
            f"border:1px dashed {self.col['checkbox_border']};border-radius:8px;"
            f"color:{self.col['text']};")
        lay.addWidget(self.value_label)
        self._ok = QPushButton("确定")
        self._ok.setEnabled(False)
        self._ok.clicked.connect(self.accept)
        lay.addWidget(self._ok)

    def keyPressEvent(self, ev):
        key = ev.key()
        if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta):
            return
        if key == Qt.Key_Escape:
            self.reject()
            return
        mods = []
        if ev.modifiers() & Qt.ControlModifier:
            mods.append("Ctrl")
        if ev.modifiers() & Qt.ShiftModifier:
            mods.append("Shift")
        if ev.modifiers() & Qt.AltModifier:
            mods.append("Alt")
        if ev.modifiers() & Qt.MetaModifier:
            mods.append("Win")
        name = _qt_key_name(key)
        if not name:
            return
        text = "+".join(mods + [name])
        if parse_hotkey(text) is None:
            return
        self.result_text = text
        self.value_label.setText(text)
        self._ok.setEnabled(True)


# =====================================================================
#  左侧导航图标 / 导航项（新 UI：左侧导航 + 右侧详情）
# =====================================================================
class NavIcon(QLabel):
    """根据 key 用 QPainter 画一个 22x22 的单色图标（颜色随主题）。"""

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
        elif self.key == "pref":  # 齿轮
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
        elif self.key == "history":  # 时钟
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            cx, cy = w / 2, h / 2
            p.drawEllipse(cx - 6, cy - 6, 12, 12)
            p.setPen(pen)
            p.drawLine(cx, cy, cx, cy - 4.5)
            p.drawLine(cx, cy, cx + 3, cy + 1.5)
        elif self.key == "plan":  # 日历（计划 / 分段）
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(3, 5, w - 6, h - 8, 2, 2)
            # 顶部两个挂点
            p.drawLine(6, 3, 6, 7)
            p.drawLine(w - 6, 3, w - 6, 7)
            # 内部分隔
            p.drawLine(3, 10, w - 3, 10)
            p.drawLine(w / 2, 10, w / 2, h - 3)
        p.end()


class NavItem(QWidget):
    """左侧导航项：选中态药丸背景 + 左侧圆角强调竖条 + 120ms Hover 淡入淡出微交互。"""
    clicked = Signal(int)

    def __init__(self, index, key, label, win, parent=None):
        super().__init__(parent)
        self.index = index
        self.key = key
        self.win = win
        self._selected = False
        self._hover_progress = 0.0
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(44)
        # 120ms Hover 药丸背景渐变（鼠标划过时平滑淡入/淡出）
        self._anim = QPropertyAnimation(self, b"hoverProgress", self)
        self._anim.setDuration(120)
        self._anim.setEasingCurve(QEasingCurve.OutQuad)
        self.icon = NavIcon(key, color=win.col["sub"])
        self.icon.setStyleSheet("background:transparent;")
        self.label = QLabel(label)
        self.label.setFont(_heading_font(13, QFont.Weight.Medium))
        self.label.setStyleSheet(f"background:transparent;color:{win.col['text']};")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(12)
        lay.addWidget(self.icon)
        lay.addWidget(self.label)
        lay.addStretch(1)
        self._apply()

    def get_hover_progress(self):
        return self._hover_progress

    def set_hover_progress(self, val):
        self._hover_progress = val
        self.update()

    hoverProgress = Property(float, get_hover_progress, set_hover_progress)

    def set_selected(self, v):
        self._selected = v
        if v:
            self._anim.stop()
            self._hover_progress = 0.0
        self._apply()
        self.update()

    def _apply(self):
        col = self.win.col
        if self._selected:
            self.label.setStyleSheet(f"background:transparent;color:{col['accent']};")
            self.icon.set_color(col["accent"])
        else:
            self.label.setStyleSheet(f"background:transparent;color:{col['text']};")
            self.icon.set_color(col["sub"])

    def enterEvent(self, ev):
        if not self._selected:
            try:
                self._anim.stop()
                self._anim.setStartValue(self._hover_progress)
                self._anim.setEndValue(1.0)
                self._anim.start()
            except Exception:
                self._hover_progress = 1.0
                self.update()
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        if not self._selected:
            try:
                self._anim.stop()
                self._hover_progress = 0.0  # 强制重置，防止快速切换时进度卡在非零值
                self.update()
            except Exception:
                self._hover_progress = 0.0
                self.update()
        super().leaveEvent(ev)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect()
        col = self.win.col
        pill = r.adjusted(3, 3, -3, -3)
        if self._selected:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(col["selected_bg"]))
            p.drawRoundedRect(pill, 10, 10)
            p.setBrush(QColor(col["accent"]))
            p.drawRoundedRect(QRectF(7, (r.height() - 18) / 2.0, 4, 18), 2, 2)
        elif self._hover_progress > 0.001:
            bg = QColor(col["hover_bg"])
            bg.setAlphaF(max(0.0, min(1.0, bg.alphaF() * self._hover_progress)))
            p.setPen(Qt.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(pill, 10, 10)
        p.end()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.clicked.emit(self.index)


# =====================================================================
#  通用小部件（模块级，供页面类使用；颜色随 win.col）
# =====================================================================
def _msg_box(icon, title, text, buttons, col):
    # 根治：parent=None 斩断与主窗口 WA_TranslucentBackground 的句柄继承链，
    # 否则 Windows DWM 会把对话框 Alpha=0 区域渲染成纯黑（黑底弹窗）。
    box = QMessageBox(None)
    box.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint | Qt.WindowStaysOnTopHint)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(buttons)
    box.setWindowModality(Qt.ApplicationModal)
    box.setAttribute(Qt.WA_TranslucentBackground, False)
    box.setAutoFillBackground(True)
    c = col or LIGHT_THEME
    box.setStyleSheet(f"""
        QMessageBox{{background-color:{c['card']};color:{c['text']};font-size:10pt;}}
        QLabel{{color:{c['text']};background:transparent;font-size:10pt;}}
        QPushButton{{background-color:{c['hover_bg']};color:{c['text']};
            border:1px solid {c['hover_border']};border-radius:8px;font-size:10pt;
            padding:6px 18px;min-width:68px;min-height:20px;}}
        QPushButton:hover{{background-color:{c['pressed_bg']};border-color:{c['hover_border']};}}
        QPushButton:pressed{{background-color:{c['pressed_bg']};}}
    """)
    return box


def _resolve_theme_col(parent, col):
    """回溯 parent（页面/历史行）的主窗口主题色；拿不到则回退浅色。"""
    if isinstance(col, dict):
        return col
    w = parent
    while isinstance(w, QWidget):
        wc = getattr(w, "col", None)
        if isinstance(wc, dict):
            return wc
        w = w.parent()
    return LIGHT_THEME


def _center_on_mainwindow(box, parent):
    """把弹窗大致居中到主窗口（parent=None 时无法自动居中，需手动计算）。"""
    try:
        win = parent.window() if isinstance(parent, QWidget) else None
        if win is not None:
            box.adjustSize()
            s = box.size()
            g = win.geometry()
            box.move(g.center() - QPoint(s.width() // 2, s.height() // 2))
    except Exception:
        pass


def _msg_question(parent, title, text, buttons, col=None):
    box = _msg_box(QMessageBox.Question, title, text, buttons, _resolve_theme_col(parent, col))
    _center_on_mainwindow(box, parent)
    return box.exec()


def _msg_warning(parent, title, text, col=None):
    box = _msg_box(QMessageBox.Warning, title, text, QMessageBox.StandardButton.Ok, _resolve_theme_col(parent, col))
    _center_on_mainwindow(box, parent)
    return box.exec()


def _msg_information(parent, title, text, col=None):
    box = _msg_box(QMessageBox.Information, title, text, QMessageBox.StandardButton.Ok, _resolve_theme_col(parent, col))
    _center_on_mainwindow(box, parent)
    return box.exec()


def _combo(items, current, slot=None, col=None):
    # 回归原正式版实现：不额外设置 Delegate / autoFillBackground / 容器调色板，
    # 避免干扰 QAbstractItemView 的原生绘制；白底 + 选中高亮完全由全局 QSS
    # （QComboBox / QComboBox QAbstractItemView）统一提供。
    c = QComboBox()
    c.addItems(items)
    if current in items:
        c.setCurrentText(current)
    c.setMinimumHeight(32)
    c.setMaxVisibleItems(10)
    # 让下拉列表按像素滚动、统一行高，减少展开时的跳动/卡顿
    try:
        view = c.view()
        view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        view.setUniformItemSizes(True)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    except Exception:
        pass
    if slot is not None:
        c.currentTextChanged.connect(slot)
    return c


def _section_card(title, col):
    """浅灰圆角分组卡片；背景/边框由全局 QSS（#sectionCard）随主题统一换肤。"""
    frame = QFrame()
    frame.setObjectName("sectionCard")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(10)
    if title:
        lbl = QLabel(title)
        lbl.setProperty("role", "heading")
        lbl.setFont(_heading_font(15, QFont.Weight.Medium))
        lay.addWidget(lbl)
    return frame, lay


def _row2(left, right):
    row = QHBoxLayout()
    row.setSpacing(18)
    row.addLayout(left)
    row.addLayout(right)
    return row


def _labeled(text, control, col, lw=56):
    row = QHBoxLayout()
    row.setSpacing(8)
    lbl = QLabel(text)
    lbl.setProperty("role", "sub")
    lbl.setFixedWidth(lw)
    row.addWidget(lbl)
    if isinstance(control, QLayout):
        row.addLayout(control, 1)
    else:
        row.addWidget(control, 1)
    return row


# =====================================================================
#  右侧详情页（录制范围 / 录制参数 / 偏好 / 保存位置 / 历史记录）
# =====================================================================
class PageRange(QWidget):
    """录制范围：全屏 / 全屏(多屏) / 区域 / 窗口 + 内联切换区。"""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        col = win.col
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(14)
        sec, slay = _section_card("录制范围", col)

        # 分段按钮（属性挂到 win，_set_mode 依赖）
        seg = QHBoxLayout()
        seg.setSpacing(6)
        win.mode_btns = {}
        for key, label in (("full", "全屏"), ("full_all", "全屏(多屏)"),
                           ("region", "区域"), ("window", "窗口")):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setFixedHeight(34)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: win._set_mode(k))
            win.mode_btns[key] = b
            seg.addWidget(b, 1)
        slay.addLayout(seg)

        # 区域 / 窗口：固定高度 QStackedWidget 内联切换
        win.range_extra = QStackedWidget()
        win.range_extra.setFixedHeight(78)

        page_full = QWidget()
        ph = QHBoxLayout(page_full)
        ph.setContentsMargins(0, 0, 0, 0)
        full_hint = QLabel("全屏模式无需额外设置")
        full_hint.setProperty("role", "sub")
        ph.addWidget(full_hint)
        ph.addStretch(1)
        win.range_extra.addWidget(page_full)

        win.lbl_region = QLabel("未选择区域")
        win.lbl_region.setProperty("role", "sub")
        win.btn_region = QPushButton("选择区域")
        win.btn_region.setFixedHeight(30)
        win.btn_region.setCursor(Qt.PointingHandCursor)
        win.btn_region.clicked.connect(win._select_region)
        region_box = QWidget()
        rl = QHBoxLayout(region_box)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)
        rl.addWidget(win.lbl_region, 1)
        rl.addWidget(win.btn_region)
        win.range_extra.addWidget(region_box)

        win.win_combo = _combo(["（点击刷新窗口列表）"], "（点击刷新窗口列表）")
        win.win_combo.setMinimumHeight(30)
        win.btn_win_refresh = QPushButton("刷新")
        win.btn_win_refresh.setFixedHeight(30)
        win.btn_win_refresh.setFixedWidth(54)
        win.btn_win_refresh.setCursor(Qt.PointingHandCursor)
        win.btn_win_refresh.clicked.connect(win._refresh_windows)
        win.win_combo.currentIndexChanged.connect(win._on_win_selected)
        window_box = QWidget()
        wl = QVBoxLayout(window_box)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(6)
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        row1.addWidget(win.win_combo, 1)
        row1.addWidget(win.btn_win_refresh)
        wl.addLayout(row1)
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        lbl_method = QLabel("窗口捕获")
        lbl_method.setProperty("role", "sub")
        lbl_method.setFixedWidth(56)
        win.win_method_combo = _combo(
            ["PrintWindow（兼容旧窗口）", "桌面合成（DXGI/游戏防黑屏）"],
            "PrintWindow（兼容旧窗口）", win._on_window_method_changed, col=col)
        win.win_method_combo.setMinimumHeight(30)
        row2.addWidget(lbl_method)
        row2.addWidget(win.win_method_combo, 1)
        wl.addLayout(row2)
        win.range_extra.addWidget(window_box)

        slay.addWidget(win.range_extra)
        lay.addWidget(sec)
        lay.addStretch(1)


class PageParams(QWidget):
    """录制参数：帧率/画质/编码器/码率/分辨率/音频/格式/麦克风/延迟/快捷键 + 计划/分段 + 预估。"""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        col = win.col
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(14)
        sec, slay = _section_card("录制参数", col)

        win.fps_combo = _combo(FPS_OPTIONS, "30", win._on_setting_changed, col=col)
        win.quality_combo = _combo(list(QUALITY_PRESETS.keys()), DEFAULT_QUALITY, win._on_setting_changed, col=col)
        win.quality_combo.setMinimumWidth(240)
        win.encoder_combo = _combo(ENCODER_OPTIONS, "默认（自动）", win._on_setting_changed, col=col)
        win.encoder_combo.setMinimumHeight(32)
        win.bitrate_combo = _combo(BITRATE_OPTIONS, "不设置（CRF 质量）", win._on_setting_changed, col=col)
        win.bitrate_combo.setMinimumHeight(32)
        win.scale_combo = _combo(RESOLUTION_SCALE_OPTIONS, "原始（不缩放）", win._on_setting_changed, col=col)
        win.scale_combo.setMinimumHeight(32)
        win.fmt_combo = _combo(list(FORMAT_OPTIONS.keys()), "MP4 (H.264, 推荐)", win._on_setting_changed, col=col)
        win.fmt_combo.setMinimumHeight(32)
        win.fmt_combo.setMinimumWidth(150)
        win.audio_combo = _combo(["无音频", "系统声音（立体声混音）"], "无音频", win._on_setting_changed, col=col)
        win.audio_combo.setMinimumHeight(32)
        win.audio_combo.setMinimumWidth(170)
        win.btn_audio_refresh = QPushButton("刷新")
        win.btn_audio_refresh.setFixedHeight(32)
        win.btn_audio_refresh.setFixedWidth(54)
        win.btn_audio_refresh.setCursor(Qt.PointingHandCursor)
        win.btn_audio_refresh.clicked.connect(win._refresh_audio_devices)
        win.btn_audio_test = QPushButton("测试")
        win.btn_audio_test.setFixedHeight(32)
        win.btn_audio_test.setFixedWidth(54)
        win.btn_audio_test.setCursor(Qt.PointingHandCursor)
        win.btn_audio_test.setCheckable(True)
        win.btn_audio_test.setToolTip("点击测试录音是否正常（与电平条联动），再点击停止")
        win.btn_audio_test.toggled.connect(win._on_audio_test_toggled)
        audio_row = QHBoxLayout()
        audio_row.setSpacing(8)
        audio_row.addWidget(win.audio_combo, 1)
        audio_row.addWidget(win.btn_audio_refresh)
        audio_row.addWidget(win.btn_audio_test)
        win.delay_combo = _combo(list(DELAY_OPTIONS.keys()), "1 秒", win._on_delay_changed, col=col)
        win.delay_combo.setMinimumHeight(32)
        win.hotkey_edit = QLineEdit("F9")
        win.hotkey_edit.setReadOnly(True)
        win.hotkey_edit.setMinimumHeight(32)
        win.hotkey_edit.setMinimumWidth(110)
        win.hotkey_edit.setToolTip("点击右侧“设置”可录制自定义快捷键")
        win.btn_hotkey = QPushButton("设置")
        win.btn_hotkey.setFixedHeight(32)
        win.btn_hotkey.setFixedWidth(56)
        win.btn_hotkey.setCursor(Qt.PointingHandCursor)
        win.btn_hotkey.clicked.connect(win._set_hotkey)
        hotkey_row = QHBoxLayout()
        hotkey_row.setSpacing(6)
        hotkey_row.addWidget(win.hotkey_edit, 1)
        hotkey_row.addWidget(win.btn_hotkey)

        # 两列网格：左半（label+control 拉伸）| 右半（label+control 靠左），左右各占一半
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 0)
        grid.setColumnStretch(3, 1)

        def _lb(text):
            lbl = QLabel(text)
            lbl.setProperty("role", "sub")
            lbl.setFixedWidth(56)
            return lbl

        # Row 0: 帧率 | 画质
        grid.addWidget(_lb("帧率"), 0, 0)
        grid.addWidget(win.fps_combo, 0, 1)
        grid.addWidget(_lb("画质"), 0, 2)
        grid.addWidget(win.quality_combo, 0, 3)
        # Row 1: 编码器 | 码率
        grid.addWidget(_lb("编码器"), 1, 0)
        grid.addWidget(win.encoder_combo, 1, 1)
        grid.addWidget(_lb("码率"), 1, 2)
        grid.addWidget(win.bitrate_combo, 1, 3)
        # Row 2: 分辨率 | 格式
        grid.addWidget(_lb("分辨率"), 2, 0)
        grid.addWidget(win.scale_combo, 2, 1)
        grid.addWidget(_lb("格式"), 2, 2)
        grid.addWidget(win.fmt_combo, 2, 3)
        # Row 3: 音频占一整行
        grid.addWidget(_lb("音频"), 3, 0)
        grid.addLayout(audio_row, 3, 1, 1, 3)
        # Row 4: 麦克风（整行，动态显隐）
        win.mic_box = QWidget()
        mic_lay = QHBoxLayout(win.mic_box)
        mic_lay.setContentsMargins(0, 0, 0, 0)
        mic_lay.setSpacing(8)
        win.mic_label = QLabel("麦克风")
        win.mic_label.setProperty("role", "sub")
        win.mic_label.setFixedWidth(56)
        win.mic_combo = _combo(["（自动选择）"], "（自动选择）", win._on_mic_changed, col=col)
        win.mic_combo.setMinimumHeight(32)
        mic_lay.addWidget(win.mic_label)
        mic_lay.addWidget(win.mic_combo, 1)
        win.mic_box.setVisible(False)
        grid.addWidget(win.mic_box, 4, 0, 1, 4)
        win.audio_combo.currentTextChanged.connect(win._update_mic_visibility)
        # Row 5: 开始延迟 | 快捷键
        grid.addWidget(_lb("开始延迟"), 5, 0)
        grid.addWidget(win.delay_combo, 5, 1)
        grid.addWidget(_lb("快捷键"), 5, 2)
        grid.addLayout(hotkey_row, 5, 3)

        slay.addLayout(grid)
        lay.addWidget(sec)

        # ---- 预估（计划/分段已移至独立页面） ----
        win.estimate_label = QLabel("预估（约）：—")
        win.estimate_label.setProperty("role", "sub")
        win.estimate_label.setContentsMargins(4, 0, 4, 0)
        lay.addWidget(win.estimate_label)
        lay.addStretch(1)


class PagePlan(QWidget):
    """计划 / 分段：定时开始 / 时长上限 / 自动分段。"""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        col = win.col
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(14)
        sec, slay = _section_card("计划 / 分段", col)
        win.chk_schedule_start = QCheckBox("定时开始")
        win.datetime_start = QDateTimeEdit(QDateTime.currentDateTime().addSecs(60))
        win.datetime_start.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        win.datetime_start.setCalendarPopup(True)
        win.datetime_start.setMinimumHeight(30)
        win.chk_duration_limit = QCheckBox("时长上限（分钟）")
        win.spin_duration = QSpinBox()
        win.spin_duration.setRange(1, 600)
        win.spin_duration.setValue(10)
        win.spin_duration.setSuffix(" 分钟")
        win.spin_duration.setMinimumHeight(30)
        win.chk_auto_segment = QCheckBox("自动分段（分钟）")
        win.spin_segment = QSpinBox()
        win.spin_segment.setRange(1, 600)
        win.spin_segment.setValue(10)
        win.spin_segment.setSuffix(" 分钟")
        win.spin_segment.setMinimumHeight(30)
        for w in (win.chk_schedule_start, win.chk_duration_limit, win.chk_auto_segment):
            w.setFixedHeight(30)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        grid.addWidget(win.chk_schedule_start, 0, 0)
        grid.addWidget(win.datetime_start, 0, 1)
        grid.addWidget(win.chk_duration_limit, 1, 0)
        grid.addWidget(win.spin_duration, 1, 1)
        grid.addWidget(win.chk_auto_segment, 2, 0)
        grid.addWidget(win.spin_segment, 2, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        slay.addLayout(grid)
        for w in (win.chk_schedule_start, win.chk_duration_limit, win.chk_auto_segment):
            w.toggled.connect(win._on_plan_changed)
        win.datetime_start.dateTimeChanged.connect(win._on_plan_changed)
        win.spin_duration.valueChanged.connect(win._on_plan_changed)
        win.spin_segment.valueChanged.connect(win._on_plan_changed)
        lay.addWidget(sec)
        lay.addStretch(1)


class PagePref(QWidget):
    """偏好：深色模式 / 开机自启 / 悬浮窗 / 自动打开 / 悬浮常驻。"""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        col = win.col
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(14)
        sec, slay = _section_card("偏好", col)
        win.chk_dark = QCheckBox("全局深色模式")
        win.chk_autostart = QCheckBox("开机自启")
        win.chk_float = QCheckBox("显示悬浮控制窗")
        win.chk_auto = QCheckBox("完成后自动打开文件夹")
        win.chk_float_stay = QCheckBox("悬浮框常驻（录制结束不返回主界面）")
        win.chk_float_mini = QCheckBox("悬浮窗极简模式（仅红点与计时）")
        win.chk_denoise = QCheckBox("麦克风降噪（减少底噪，仅作用于麦克风）")
        win.chk_denoise.setToolTip("开启后用 afftdn 滤镜降低麦克风底噪；"
                                    "仅对麦克风 / 系统+麦克风混音生效，不影响系统声音录制")
        win.chk_dark.toggled.connect(win._on_dark_toggled)
        win.chk_autostart.toggled.connect(win._on_autostart_toggled)
        win.chk_auto.toggled.connect(lambda v: win._schedule_save_config())
        win.chk_float.toggled.connect(lambda v: win._schedule_save_config())
        win.chk_float_stay.toggled.connect(win._on_float_stay_toggled)
        win.chk_float_mini.toggled.connect(win._on_float_mini_toggled)
        win.chk_denoise.toggled.connect(
            lambda v: (setattr(win.core, "mic_denoise", v), win._schedule_save_config()))
        for c in (win.chk_dark, win.chk_autostart, win.chk_float,
                  win.chk_auto, win.chk_float_stay, win.chk_float_mini, win.chk_denoise):
            c.setFixedHeight(30)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(2)
        grid.addWidget(win.chk_dark, 0, 0)
        grid.addWidget(win.chk_autostart, 0, 1)
        grid.addWidget(win.chk_float, 1, 0)
        grid.addWidget(win.chk_auto, 1, 1)
        grid.addWidget(win.chk_float_stay, 2, 0)
        grid.addWidget(win.chk_float_mini, 2, 1)
        grid.addWidget(win.chk_denoise, 3, 0)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        slay.addLayout(grid)
        slay.addStretch(1)
        lay.addWidget(sec)
        lay.addStretch(1)


class PageSave(QWidget):
    """保存位置：目录 / 前缀 / 指定 ffmpeg。"""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        col = win.col
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(14)
        sec, slay = _section_card("保存位置", col)
        win.dir_edit = QLineEdit(win.core.save_dir)
        win.dir_edit.setMinimumHeight(32)
        win.dir_edit.textChanged.connect(
            lambda t: (setattr(win.core, "save_dir", t),
                       win._schedule_save_config(), win._update_summary()))
        win.btn_browse = QPushButton("浏览")
        win.btn_browse.setFixedHeight(32)
        win.btn_browse.setFixedWidth(56)
        win.btn_browse.setCursor(Qt.PointingHandCursor)
        win.btn_browse.clicked.connect(win._browse_dir)
        dir_row = QHBoxLayout()
        dir_row.setSpacing(8)
        dir_row.addWidget(win.dir_edit, 1)
        dir_row.addWidget(win.btn_browse)
        slay.addLayout(dir_row)

        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        lbl = QLabel("前缀")
        lbl.setProperty("role", "sub")
        name_row.addWidget(lbl)
        win.prefix_edit = QLineEdit("录屏")
        win.prefix_edit.setMinimumHeight(32)
        win.prefix_edit.setFixedWidth(120)
        win.prefix_edit.textChanged.connect(
            lambda t: (setattr(win.core, "prefix", t), win._schedule_save_config()))
        name_row.addWidget(win.prefix_edit)
        name_row.addStretch(1)
        win.btn_ffmpeg = QPushButton("指定 ffmpeg")
        win.btn_ffmpeg.setFixedHeight(32)
        win.btn_ffmpeg.setCursor(Qt.PointingHandCursor)
        win.btn_ffmpeg.clicked.connect(win._pick_ffmpeg)
        name_row.addWidget(win.btn_ffmpeg)
        slay.addLayout(name_row)

        # 智能命名模板：{date}_{time}_{mode}_{resolution}_{fps} 等变量（空=旧行为）
        tmpl_row = QHBoxLayout()
        tmpl_row.setSpacing(8)
        lbl_t = QLabel("命名模板")
        lbl_t.setProperty("role", "sub")
        tmpl_row.addWidget(lbl_t)
        win.template_edit = QLineEdit("")
        win.template_edit.setMinimumHeight(32)
        win.template_edit.setPlaceholderText(
            "{date}_{time}_{mode}_{resolution}_{fps}（留空使用「前缀_时间戳」）")
        win.template_edit.textChanged.connect(
            lambda t: (setattr(win.core, "name_template", t), win._schedule_save_config()))
        tmpl_row.addWidget(win.template_edit, 1)
        hint_t = QLabel("变量：{date} {time} {datetime} {mode} {resolution} {fps} {prefix}")
        hint_t.setProperty("role", "sub")
        tmpl_row.addWidget(hint_t)
        slay.addLayout(tmpl_row)
        lay.addWidget(sec)
        lay.addStretch(1)


# =====================================================================
#  历史记录（真实功能：缩略图 / 预览 / 重命名 / 删除 / 自动刷新）
# =====================================================================
_VIDEO_EXT = (".mp4", ".mkv", ".avi", ".mov", ".gif", ".webm", ".flv", ".m4v")


def _thumb_cache_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.dirname(os.path.abspath(sys.argv[0]))
    d = os.path.join(base, "ScreenRecorder", "thumbs")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = os.path.dirname(os.path.abspath(sys.argv[0]))
    return d


def _video_thumb_qimage(path, ffmpeg):
    """用 ffmpeg 提取视频第 1 秒画面为 QImage（线程安全，可在后台线程调用）；失败返回 None。"""
    if not ffmpeg or not os.path.exists(ffmpeg):
        return None
    try:
        key = hashlib.md5((path + str(os.path.getmtime(path))).encode("utf-8")).hexdigest()
    except Exception:
        return None
    out = os.path.join(_thumb_cache_dir(), f"{key}.jpg")
    if os.path.exists(out):
        img = QImage(out)
        if not img.isNull():
            return img
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-ss", "00:00:01", "-i", path,
        "-vf", "scale=176:112:force_original_aspect_ratio=decrease,pad=176:112:(ow-iw)/2:(oh-ih)/2:black",
        "-frames:v", "1", "-q:v", "2", out,
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=8, creationflags=CREATE_NO_WINDOW)
        if result.returncode == 0 and os.path.exists(out):
            img = QImage(out)
            if not img.isNull():
                return img
    except Exception:
        pass
    return None


# 缩略图后台线程池：上限 4 并发，避免主线程被 ffmpeg 阻塞（深色切换/历史页刷新的卡顿根因）
_THUMB_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="thumb")


class HistoryRow(QWidget):
    """单条录制文件：缩略图 + 文件名（可内联编辑）+ 大小/日期 + 预览 / 重命名 / 删除。"""

    thumb_ready = Signal(object)   # 后台线程完成 → 主线程回填 QImage（QueuedConnection）

    def __init__(self, path, page, parent=None):
        super().__init__(parent)
        self.path = path
        self.page = page
        col = page.win.col
        self.setObjectName("histRow")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_Hover, True)
        self._editing = False
        # 缩略图回填信号：从后台线程 emit，自动投递到主线程（行销毁后连接自动断开，天然安全）
        self.thumb_ready.connect(self._apply_thumb, Qt.QueuedConnection)
        # 行卡片背景/边框/Hover 由全局 QSS（#histRow / #histRow:hover）统一提供，深浅色自适应
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 11, 14, 11)
        lay.setSpacing(12)

        self.thumb = QLabel()
        self.thumb.setFixedSize(88, 56)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet(
            f"background:{col['history_thumb_bg']};border-radius:8px;")
        self._set_fallback_thumb()
        QTimer.singleShot(0, self._load_real_thumb)
        lay.addWidget(self.thumb)

        info = QVBoxLayout()
        info.setSpacing(4)
        self.name_label = QLabel(os.path.basename(path))
        self.name_label.setStyleSheet(
            f"color:{col['text']};font-size:11pt;font-weight:600;background:transparent;")
        self.name_label.setWordWrap(False)
        self.name_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.name_edit = QLineEdit(os.path.basename(path))
        self.name_edit.setStyleSheet(
            f"color:{col['text']};font-size:11pt;font-weight:600;"
            f"background:{col['input_bg']};border:1px solid {col['accent']};"
            f"border-radius:5px;padding:2px 6px;")
        self.name_edit.hide()
        self.name_edit.installEventFilter(self)
        self.meta_label = QLabel(self._meta())
        self.meta_label.setStyleSheet(
            f"color:{col['sub']};font-size:9.5pt;background:transparent;")
        info.addWidget(self.name_label)
        info.addWidget(self.name_edit)
        info.addWidget(self.meta_label)
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
            f"QPushButton{{background:{col['danger_bg']};color:{col['danger_text']};}}"
            f"QPushButton:hover{{background:{col['pressed_bg']};}}")
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
        # 后台线程池提取缩略图，主线程先显示默认图标（秒级上屏），完成后信号回填
        path = self.path
        ffmpeg = self.page.win.ffmpeg

        def worker():
            try:
                img = _video_thumb_qimage(path, ffmpeg)
                if img is not None and not img.isNull():
                    # 关键：跨线程投递必须走信号（QueuedConnection 投递到主线程）。
                    # 不能从后台线程调用 QTimer.singleShot(0, ...)，那会在后台线程
                    # 执行 GUI 更新（被 Qt 忽略/无效），导致缩略图永远不回填。
                    self.thumb_ready.emit(img)
            except Exception:
                pass

        try:
            _THUMB_EXECUTOR.submit(worker)
        except Exception:
            pass

    def _apply_thumb(self, img):
        # 主线程回填：行可能已被删除（deleteLater），需防御
        try:
            if self.thumb is not None and not self.thumb.isHidden():
                self.thumb.setPixmap(
                    QPixmap.fromImage(img).scaled(
                        88, 56, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
        except Exception:
            pass

    def apply_theme(self, col):
        """主题切换时的原地换肤：只更新颜色/样式，不触磁盘、不重建、不重新生成缩略图。"""
        try:
            self.thumb.setStyleSheet(
                f"background:{col['history_thumb_bg']};border-radius:8px;")
            self.name_label.setStyleSheet(
                f"color:{col['text']};font-size:11pt;font-weight:600;background:transparent;")
            self.name_edit.setStyleSheet(
                f"color:{col['text']};font-size:11pt;font-weight:600;"
                f"background:{col['input_bg']};border:1px solid {col['accent']};"
                f"border-radius:5px;padding:2px 6px;")
            self.meta_label.setStyleSheet(
                f"color:{col['sub']};font-size:9.5pt;background:transparent;")
            self.b_del.setStyleSheet(
                f"QPushButton{{background:{col['danger_bg']};color:{col['danger_text']};}}"
                f"QPushButton:hover{{background:{col['pressed_bg']};}}")
        except Exception:
            pass

    def eventFilter(self, obj, ev):
        if obj is self.name_edit:
            if ev.type() == QEvent.FocusOut:
                # 失焦自动提交，防止编辑框悬挂
                if self._editing:
                    self._finish_rename()
                return True
            if ev.type() == QEvent.KeyPress:
                if ev.key() == Qt.Key_Escape:
                    self._cancel_rename()
                    return True
                if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
                    self._finish_rename()
                    return True
        return super().eventFilter(obj, ev)

    def _meta(self):
        try:
            st = os.stat(self.path)
            dt = __import__("datetime").datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
            # 修复：调用 RecorderCore 的静态方法（模块级 _fmt_size 不存在 → NameError 被吞）
            return f"{RecorderCore._fmt_size(st.st_size)} · {dt}"
        except Exception:
            return ""

    def _preview(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.path))

    def _start_rename(self):
        self._editing = True
        self.name_label.hide()
        self.name_edit.show()
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def _cancel_rename(self):
        self._editing = False
        self.name_edit.setText(os.path.basename(self.path))
        self.name_edit.hide()
        self.name_label.show()

    def _finish_rename(self):
        if not self.name_edit.isVisible():
            self._editing = False
            return
        self._editing = False
        new = self.name_edit.text().strip()
        base = os.path.basename(self.path)
        if new and new != base:
            if not os.path.splitext(new)[1]:
                new += os.path.splitext(base)[1]
            dst = os.path.join(os.path.dirname(self.path), new)
            if os.path.exists(dst):
                _msg_warning(self, "重命名", "目标文件名已存在。", self.page.win.col)
                self.name_edit.setText(base)
            else:
                try:
                    os.rename(self.path, dst)
                    self.path = dst
                    self.name_label.setText(new)
                    self.name_edit.setText(new)
                except Exception as e:
                    _msg_warning(self, "重命名", f"重命名失败：{e}", self.page.win.col)
                    self.name_edit.setText(base)
        self.name_edit.hide()
        self.name_label.show()

    def _delete(self):
        if _msg_question(
                self, "删除",
                f"确定删除 {os.path.basename(self.path)}？\n（将从磁盘直接删除）",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                self.page.win.col
        ) == QMessageBox.StandardButton.Yes:
            try:
                os.remove(self.path)
                self.page.refresh()
            except Exception as e:
                _msg_warning(self, "删除", f"删除失败：{e}", self.page.win.col)


class PageHistory(QWidget):
    """历史记录页：展示 win.core.save_dir 下的录制文件。"""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        col = win.col
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(14)

        top = QHBoxLayout()
        top.setSpacing(10)
        title = QLabel("录制文件 / 历史记录")
        title.setProperty("role", "heading")
        title.setFont(_heading_font(15, QFont.Weight.DemiBold))
        top.addWidget(title, 1)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setFixedHeight(32)
        self.refresh_btn.setFixedWidth(56)
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        top.addWidget(self.refresh_btn)
        self.merge_btn = QPushButton("一键合并")
        self.merge_btn.setFixedHeight(32)
        self.merge_btn.setFixedWidth(80)
        self.merge_btn.setCursor(Qt.PointingHandCursor)
        self.merge_btn.setToolTip("选择多个分段文件，通过 FFmpeg concat 无损拼接")
        self.merge_btn.clicked.connect(self._merge_segments)
        top.addWidget(self.merge_btn)
        lay.addLayout(top)

        self.note = QLabel("")
        self.note.setProperty("role", "sub")
        lay.addWidget(self.note)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("historyScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # 视口必须透明脱底：否则深色模式下滚动区/视口沿用系统浅色底（白底白字）
        self.scroll.setStyleSheet(
            "QScrollArea#historyScroll{border:none;background:transparent;}"
            "QScrollArea#historyScroll > QWidget > QWidget{background:transparent;border:none;}")
        self.list_widget = QWidget()
        self.list_widget.setObjectName("historyContainer")
        self.list_widget.setStyleSheet("#historyContainer{background:transparent;}")
        self.list_widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.list_lay = QVBoxLayout(self.list_widget)
        self.list_lay.setContentsMargins(0, 0, 0, 0)
        self.list_lay.setSpacing(10)
        self.list_lay.addStretch(1)
        self.scroll.setWidget(self.list_widget)
        lay.addWidget(self.scroll, 1)

        self.refresh_btn.clicked.connect(self.refresh)

    def refresh(self):
        col = self.win.col
        d = self.win.core.save_dir
        entries = []
        files = []
        err = ""
        try:
            if os.path.isdir(d):
                entries = [f for f in os.listdir(d) if os.path.isfile(os.path.join(d, f))]
                # 严格过滤：排除隐藏文件（. 开头）与 _raw.mkv 中间文件，仅保留视频扩展名
                files = [
                    os.path.join(d, f) for f in entries
                    if not f.startswith(".")
                    and not f.endswith("_raw.mkv")
                    and f.lower().endswith(_VIDEO_EXT)
                ]
                files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        except Exception as e:
            err = str(e)
        self.note.setText(f"保存目录：{d}（{len(entries)} 个文件）")
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
            empty.setProperty("role", "sub")
            empty.setStyleSheet(f"color:{col['sub']};font-size:11pt;padding:24px;background:transparent;")
            empty.setAlignment(Qt.AlignCenter)
            self.list_lay.addWidget(empty)
            self.list_lay.addStretch(1)
            return
        for p in files:
            self.list_lay.addWidget(HistoryRow(p, page=self))
        self.list_lay.addStretch(1)

    def apply_theme(self, col):
        """主题切换的「原地换肤」：只更新已有行的颜色样式，
        不重新扫描磁盘、不销毁重建行、不触发任何 ffmpeg 缩略图提取（0ms 瞬间切换）。"""
        try:
            rows = 0
            for i in range(self.list_lay.count()):
                item = self.list_lay.itemAt(i)
                w = item.widget() if item else None
                if isinstance(w, HistoryRow):
                    w.apply_theme(col)
                    rows += 1
            if rows:
                self.note.setText(f"保存目录：{self.win.core.save_dir}（{rows} 个文件）")
        except Exception:
            pass

    def _merge_segments(self):
        """分段碎片无损合并器：选择多个分段文件，通过 FFmpeg concat demuxer 无损拼接。"""
        try:
            d = self.win.core.save_dir
            if not os.path.isdir(d):
                QMessageBox.warning(self, "合并", "保存目录不存在")
                return
            files, _ = QFileDialog.getOpenFileNames(
                self, "选择要合并的分段文件（按顺序选择）", d,
                "视频文件 (*.mp4 *.mkv *.avi *.mov *.ts *.flv);;所有文件 (*)")
            if len(files) < 2:
                if files:
                    QMessageBox.information(self, "合并", "请至少选择 2 个文件进行合并")
                return
            # 生成输出文件名
            base, ext = os.path.splitext(files[0])
            out_path = os.path.join(d, f"{os.path.basename(base)}_合并{ext}")
            out_path, _ = QFileDialog.getSaveFileName(
                self, "保存合并后的文件", out_path,
                "视频文件 (*.mp4 *.mkv *.avi *.mov *.ts *.flv);;所有文件 (*)")
            if not out_path:
                return
            # 写入 concat 列表文件
            import tempfile
            list_fd, list_path = tempfile.mkstemp(suffix=".txt", prefix="concat_")
            try:
                with os.fdopen(list_fd, "w", encoding="utf-8") as f:
                    for fp in files:
                        # FFmpeg concat demuxer 要求路径中的单引号转义
                        safe = fp.replace("\\", "/").replace("'", "'\\''")
                        f.write(f"file '{safe}'\n")
                ffmpeg = self.win.ffmpeg
                if not ffmpeg or not os.path.exists(ffmpeg):
                    QMessageBox.warning(self, "合并", "未找到 ffmpeg，请在设置中指定")
                    return
                cmd = [
                    ffmpeg, "-y", "-f", "concat", "-safe", "0",
                    "-i", list_path, "-c", "copy", out_path
                ]
                self.note.setText(f"正在合并 {len(files)} 个文件...")
                QApplication.processEvents()
                result = subprocess.run(
                    cmd, capture_output=True, text=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if result.returncode == 0 and os.path.exists(out_path):
                    size_mb = os.path.getsize(out_path) / (1024 * 1024)
                    self.note.setText(f"合并完成：{os.path.basename(out_path)}（{size_mb:.1f} MB）")
                    QMessageBox.information(
                        self, "合并完成",
                        f"已合并 {len(files)} 个文件\n"
                        f"输出：{os.path.basename(out_path)}（{size_mb:.1f} MB）\n"
                        f"保存位置：{out_path}")
                    self.refresh()
                else:
                    err_msg = result.stderr[-500:] if result.stderr else "未知错误"
                    self.note.setText("合并失败")
                    QMessageBox.warning(self, "合并失败", f"FFmpeg 错误：\n{err_msg}")
            finally:
                try:
                    os.unlink(list_path)
                except Exception:
                    pass
        except Exception as e:
            QMessageBox.warning(self, "合并异常", str(e))


# =====================================================================
#  主界面
# =====================================================================
class AudioPeakMeter(QWidget):
    """带平滑衰减 (Peak Decay) 的多段彩色音频电平跳动条。

    Attack 瞬间冲顶响应，Release 以 0.04 阻尼平滑下落，
    视觉效果细腻流畅，完美覆盖 -60 dBFS 至 0 dBFS 动态范围。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(96, 18)
        self._level = 0.0
        self._decay_level = 0.0
        self._bars = 16
        self._release_step = 0.04   # v5.0：衰减步长参数化，便于后续调参（不叠加数据层 EMA）

    def set_level(self, v):
        try:
            v = max(0.0, min(1.0, float(v)))
        except Exception:
            v = 0.0
        self._level = v
        # 瞬时冲顶，平滑衰减
        if self._level > self._decay_level:
            self._decay_level = self._level
        else:
            self._decay_level = max(0.0, self._decay_level - self._release_step)
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        gap = 2
        bw = max(2.0, (w - (self._bars - 1) * gap) / self._bars)

        for i in range(self._bars):
            # 柱体激活门槛（从第 1 段微弱声音即敏锐点亮）
            thr = i / float(self._bars)
            bh = max(3.0, h * 0.4 + (h * 0.6) * ((i + 1) / self._bars))
            x = i * (bw + gap)
            y = h - bh

            if self._decay_level > thr:
                ratio = (i + 1) / self._bars
                if ratio <= 0.60:
                    color = QColor("#34c759")   # 绿色安全区 (-60 ~ -18 dBFS)
                elif ratio <= 0.85:
                    color = QColor("#ffcc00")   # 黄色预警区 (-18 ~ -6 dBFS)
                else:
                    color = QColor("#ff3b30")   # 红色过载区 (-6 ~ 0 dBFS)
            else:
                color = QColor(140, 140, 150, 40)  # 未激活浅灰底色

            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(QRectF(x, y, bw, bh), 1.5, 1.5)
        p.end()

class MainWindow(QWidget):
    def __init__(self, ffmpeg):
        super().__init__()
        self.ffmpeg = ffmpeg
        self.core = RecorderCore(ffmpeg)
        self.region_selector = None
        self.float_win = None
        self._win_map = {}
        self._last_out = None
        self.hotkey = None
        # 热键跨线程安全投递（Win32 Hook 线程 → 主线程 QueuedConnection）
        self._hotkey_emitter = HotkeyEmitter()
        self._hotkey_emitter.triggered.connect(self._toggle_record, Qt.QueuedConnection)
        # v4.0：放弃 WASAPI Meter，不创建任何音频设备占用。
        # 电平信号来自 FFmpeg 录音/测试进程的 astats 解析（core.audio_level）。
        # 启动软件时绝不打开麦克风（Windows 隐私占用提示消失、内存稳定）。
        self._audio_test_proc = None      # 录音测试进程
        self._audio_test_thread = None    # 录音测试 stderr 读取线程
        self._audio_test_active = False
        # 手动缩放的几何缓存（热路径避免每次 MouseMove 都 mapToGlobal）
        self._card_geo = None
        self._titlebar_ytop = None
        self._titlebar_ybot = None
        # 先读配置里的深色模式，让 _build_ui 一开始就用正确主题
        try:
            self.dark = bool(self._load_config().get("dark", False))
        except Exception:
            self.dark = False
        self.col = DARK_THEME if self.dark else LIGHT_THEME
        self._setup_window()
        self._build_ui()
        self._connect_core()
        self._init_settings()
        self._setup_tray()
        self._setup_hotkey()
        self.ui_timer = QTimer(self)
        self.ui_timer.setInterval(500)
        self.ui_timer.timeout.connect(self._ui_tick)
        self.ui_timer.start()
        self._center()

    # ---------------- 窗口（macOS 风格：无边框 + 圆角 + 阴影） ----------------
    def _setup_window(self):
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowSystemMenuHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowTitle("轻量录屏 · ScreenRecorder")
        self.setWindowIcon(self._app_icon())
        # 紧凑现代布局（与原型一致）：左侧导航 + 右侧详情
        # 恢复苏企鹅第十轮标准默认尺寸：970 x 640
        self.resize(970, 640)
        self.setMinimumSize(820, 560)
        # 手动自由缩放准备（原型式 setGeometry，避免系统原生缩放导致的阴影闪烁）
        self._resize_margin = 8
        self._resize_edge = None
        self._resize_start_geo = None
        self._resize_start_pos = None
        self.setMouseTracking(True)

    def _center(self):
        try:
            sg = QApplication.primaryScreen().availableGeometry()
            self.move((sg.width() - self.width()) // 2, (sg.height() - self.height()) // 2)
        except Exception:
            pass

    # ---------------- 手动自由缩放（原型式：覆盖四边/四角，不进入系统缩放模式，无闪烁） ----------------
    def _set_mouse_tracking_recursive(self, widget):
        for child in widget.findChildren(QWidget):
            child.setMouseTracking(True)

    def _install_event_filter_recursive(self, widget):
        for child in widget.findChildren(QWidget):
            child.installEventFilter(self)

    def _hook_new_child(self, ev):
        """给运行时新建的控件补上鼠标跟踪 + 事件过滤（历史页动态刷新等）。"""
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
        if et == QEvent.ChildPolished:
            self._hook_new_child(ev)
            return super().eventFilter(obj, ev)
        if et in (QEvent.Resize, QEvent.Move):
            # 窗口几何变化时使缩放命中的几何缓存失效（重算一次即可）
            self._card_geo = None
            return super().eventFilter(obj, ev)
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
        cur = self._cursor_for(*self._hit_test(pos))
        # 光标未变化时跳过 setCursor（避免每次 MouseMove 都触发原生调用）
        if getattr(self, "_last_cursor", None) != cur:
            self._last_cursor = cur
            self.setCursor(cur)

    def _hit_test(self, pos):
        """以可见卡片边界为基准返回 (h_edge, v_edge)；标题栏区域完全交给拖动。

        几何缓存：卡片/标题栏的全局坐标在窗口 Move/Resize 时才会变化，
        缓存后热路径（MouseMove）只需做整数比较，避免每次 mapToGlobal。
        """
        if self.isMaximized() or self.isFullScreen():
            return 0, 0
        if self._card_geo is None:
            tl = self.card.mapToGlobal(self.card.rect().topLeft())
            br = self.card.mapToGlobal(self.card.rect().bottomRight())
            self._card_geo = (tl, br)
            if self.title_bar is not None:
                tbt = self.title_bar.mapToGlobal(self.title_bar.rect().topLeft()).y()
                tbb = self.title_bar.mapToGlobal(self.title_bar.rect().bottomLeft()).y()
                self._titlebar_ytop, self._titlebar_ybot = tbt, tbb
        tl, br = self._card_geo
        left, top = tl.x(), tl.y()
        right, bottom = br.x(), br.y()
        x, y = pos.x(), pos.y()
        # 标题栏区域内不缩放，避免拖动窗口时误触发
        if self._titlebar_ytop is not None and self._titlebar_ytop <= y <= self._titlebar_ybot:
            return 0, 0
        m = self._resize_margin
        h = -1 if x <= left + m else (1 if x >= right - m else 0)
        v = -1 if y <= top + m else (1 if y >= bottom - m else 0)
        if h == 0 and v == 0:
            return 0, 0
        return h, v

    @staticmethod
    def _cursor_for(h, v):
        if (h == -1 and v == -1) or (h == 1 and v == 1):
            return Qt.SizeFDiagCursor
        if (h == -1 and v == 1) or (h == 1 and v == -1):
            return Qt.SizeBDiagCursor
        if h != 0:
            return Qt.SizeHorCursor
        if v != 0:
            return Qt.SizeVerCursor
        return Qt.ArrowCursor

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

    def showEvent(self, ev):
        super().showEvent(ev)
        self._lock_min_height()

    def _lock_min_height(self):
        """窗口最小高度锁定为 root 布局实际所需（苏企鹅第十轮锚定语义：只增不减）。

        QStackedWidget 的 minimumSizeHint 取各页面最大值（PageParams 主导 ≈527），
        因此 layout().minimumSize().height() 是稳定值（≈636），不随切页变化；
        用它锁住 minimumHeight 可保证窗口缩小到 820x560 时侧边栏品牌区(68px)/
        分割线(1px)/导航项不被压坏。宽度保持 _setup_window 的 820 手动下限。
        """
        try:
            h = self.layout().minimumSize().height()
            if h > self.minimumHeight():
                self.setMinimumHeight(h)
        except Exception:
            pass

    # ---------------- 配置持久化 ----------------
    def _config_path(self):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        new_dir = os.path.join(base, "ScreenRecorder")
        try:
            os.makedirs(new_dir, exist_ok=True)
        except Exception:
            new_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        new_path = os.path.join(new_dir, "config.json")
        old_path = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "config.json")
        if not os.path.exists(new_path) and os.path.exists(old_path):
            try:
                import shutil as _sh
                _sh.copy2(old_path, new_path)
            except Exception:
                pass
        return new_path

    def _load_config(self):
        d = {}
        try:
            with open(self._config_path(), "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    d = loaded
        except Exception:
            pass
        if parse_hotkey(d.get("hotkey")) is None:
            d["hotkey"] = "F9"
        if d.get("fps") not in FPS_OPTIONS:
            d["fps"] = "30"
        if d.get("quality") not in QUALITY_PRESETS:
            d["quality"] = DEFAULT_QUALITY
        if d.get("fmt") not in FORMAT_OPTIONS:
            d["fmt"] = "MP4 (H.264, 推荐)"
        if d.get("delay") not in DELAY_OPTIONS:
            d["delay"] = "1 秒"
        if not isinstance(d.get("save_dir"), str) or not d["save_dir"].strip():
            d["save_dir"] = self.core._default_save_dir()
        if not isinstance(d.get("prefix"), str) or not d["prefix"].strip():
            d["prefix"] = "录屏"
        if not isinstance(d.get("name_template"), str):
            d["name_template"] = ""
        if not isinstance(d.get("audio"), str):
            d["audio"] = "无音频"
        if not isinstance(d.get("mic"), str):
            d["mic"] = ""
        if d.get("encoder") not in ENCODER_OPTIONS:
            d["encoder"] = "默认（自动）"
        if not isinstance(d.get("bitrate"), str) or d["bitrate"] not in BITRATE_OPTIONS:
            d["bitrate"] = "不设置（CRF 质量）"
        if d.get("scale") not in RESOLUTION_SCALE_OPTIONS:
            d["scale"] = "原始（不缩放）"
        if not isinstance(d.get("max_duration_seconds"), int):
            d["max_duration_seconds"] = 0
        if not isinstance(d.get("segment_seconds"), int):
            d["segment_seconds"] = 0
        if d.get("scheduled_start") is not None and not isinstance(d.get("scheduled_start"), str):
            d["scheduled_start"] = None
        if not isinstance(d.get("show_float"), bool):
            d["show_float"] = True
        if not isinstance(d.get("auto_open"), bool):
            d["auto_open"] = False
        if not isinstance(d.get("dark"), bool):
            d["dark"] = False
        if not isinstance(d.get("autostart"), bool):
            d["autostart"] = False
        if not isinstance(d.get("float_stay"), bool):
            d["float_stay"] = False
        if not isinstance(d.get("float_mini"), bool):
            d["float_mini"] = False
        if not isinstance(d.get("mic_denoise"), bool):
            d["mic_denoise"] = False
        if d.get("window_capture_method") not in ("printwindow", "desktop"):
            d["window_capture_method"] = "printwindow"
        if d.get("mode") not in ("full", "full_all", "region", "window"):
            d["mode"] = "full"
        return d

    def _schedule_save_config(self):
        try:
            if getattr(self, "_save_timer", None) is None:
                self._save_timer = QTimer(self)
                self._save_timer.setSingleShot(True)
                self._save_timer.timeout.connect(self._save_config)
            self._save_timer.stop()
            self._save_timer.start(400)
        except Exception:
            self._save_config()

    def _save_config(self):
        try:
            d = {
                "hotkey": self.hotkey_edit.text() if hasattr(self, "hotkey_edit") else "F9",
                "save_dir": self.dir_edit.text(),
                "prefix": self.prefix_edit.text(),
                "name_template": self.template_edit.text() if hasattr(self, "template_edit") else "",
                "fmt": self.fmt_combo.currentText(),
                "fps": self.fps_combo.currentText(),
                "quality": self.quality_combo.currentText(),
                "audio": self.audio_combo.currentText(),
                "mic": getattr(self.core, "mic", ""),
                "encoder": self.encoder_combo.currentText() if hasattr(self, "encoder_combo") else "默认（自动）",
                "bitrate": self.bitrate_combo.currentText() if hasattr(self, "bitrate_combo") else "不设置（CRF 质量）",
                "scale": self.scale_combo.currentText() if hasattr(self, "scale_combo") else "原始（不缩放）",
                "max_duration_seconds": getattr(self.core, "max_duration_seconds", 0),
                "segment_seconds": getattr(self.core, "segment_seconds", 0),
                "scheduled_start": getattr(self.core, "scheduled_start", None),
                "delay": self.delay_combo.currentText(),
                "mode": self.core.mode,
                "show_float": self.chk_float.isChecked(),
                "auto_open": self.chk_auto.isChecked(),
                "dark": self.chk_dark.isChecked() if hasattr(self, "chk_dark") else self.dark,
                "autostart": self.chk_autostart.isChecked() if hasattr(self, "chk_autostart") else False,
                "float_stay": self.chk_float_stay.isChecked() if hasattr(self, "chk_float_stay") else False,
                "float_mini": self.chk_float_mini.isChecked() if hasattr(self, "chk_float_mini") else False,
                "mic_denoise": self.chk_denoise.isChecked() if hasattr(self, "chk_denoise") else False,
                "window_capture_method": getattr(self.core, "window_capture_method", "printwindow"),
            }
            path = self._config_path()
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception:
            pass

    # ---------------- UI 构建 ----------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        self.card = QWidget()
        self.card.setObjectName("card")
        # 卡片背景/边框由全局 QSS（#card）随主题统一换肤
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(36)
        shadow.setColor(QColor(0, 0, 0, 70))
        shadow.setOffset(0, 12)
        self.card.setGraphicsEffect(shadow)
        self._shadow = shadow  # 卡片阴影（手动缩放时不移除，避免闪烁）
        root.addWidget(self.card)

        card_lay = QVBoxLayout(self.card)
        card_lay.setContentsMargins(0, 0, 0, 0)
        card_lay.setSpacing(0)
        self.title_bar = TitleBar(self, col=self.col)
        card_lay.addWidget(self.title_bar)

        sep = QFrame()
        sep.setObjectName("cardSep")
        sep.setFrameShape(QFrame.HLine)
        card_lay.addWidget(sep)

        # 主体：左侧导航 + 右侧详情
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self._build_sidebar(body)
        self._build_detail(body)
        card_lay.addLayout(body, 1)

        # ---- 底部录制区（原型 reccard 背景：section_bg + 顶部细线 + 底部圆角） ----
        rec_card = QWidget()
        rec_card.setObjectName("reccard")
        # 背景/顶部细线/底部圆角由全局 QSS（#reccard）随主题统一换肤
        rec_lay = QVBoxLayout(rec_card)
        rec_lay.setContentsMargins(0, 0, 0, 0)
        rec_lay.setSpacing(0)

        ring_area = QWidget()
        ra_lay = QHBoxLayout(ring_area)
        ra_lay.setContentsMargins(28, 8, 28, 10)
        ra_lay.setSpacing(24)
        self.ring = RecordingRing(col=self.col)
        self.ring.setFixedSize(116, 116)
        self.ring.clicked.connect(self._toggle_record)
        ra_lay.addWidget(self.ring, alignment=Qt.AlignVCenter)
        right = QVBoxLayout()
        right.setSpacing(7)
        self.ring_caption = QLabel("开始录制")
        self.ring_caption.setProperty("role", "text")
        self.ring_caption.setFont(_heading_font(18, QFont.Weight.Medium))
        self.tip_label = QLabel("F9 开始 / 停止 · 录制时自动收起到托盘")
        self.tip_label.setProperty("role", "sub")
        right.addWidget(self.ring_caption)
        right.addWidget(self.tip_label)

        act_row = QHBoxLayout()
        act_row.setSpacing(10)
        self.btn_preview = QPushButton("预览")
        self.btn_openfolder = QPushButton("打开所在文件夹")
        self.btn_log = QPushButton("日志")
        for b in (self.btn_preview, self.btn_openfolder, self.btn_log):
            b.setFixedHeight(34)
            b.setCursor(Qt.PointingHandCursor)
        self.btn_preview.clicked.connect(lambda: self.core._open_file(self._last_out))
        self.btn_openfolder.clicked.connect(lambda: self.core._open_folder(self._last_out or self.core.save_dir))
        self.btn_log.clicked.connect(self._open_log_file)
        self._set_actions_enabled(False)
        act_row.addWidget(self.btn_preview)
        act_row.addWidget(self.btn_openfolder)
        act_row.addWidget(self.btn_log)
        right.addLayout(act_row)

        # 配置摘要（原型新增的详细状态信息：实时反映当前设置）
        self.summary_label = QLabel("全屏 · 30 fps · 高清(CRF 23) · 无音频 · MP4 · 保存到 …")
        self.summary_label.setProperty("role", "sub")
        right.addWidget(self.summary_label)

        ra_lay.addLayout(right, 1)
        rec_lay.addWidget(ring_area)

        # ---- 状态栏 ----
        status = QFrame()
        status.setFrameShape(QFrame.NoFrame)
        status.setStyleSheet("background:transparent;")
        slay = QHBoxLayout(status)
        slay.setContentsMargins(24, 8, 24, 14)
        self.status_label = QLabel("就绪")
        self.status_label.setProperty("role", "status")
        self.status_label.setStyleSheet(f"color:{self.col['sub']};font-size:10pt;")
        self.size_label = QLabel("")
        self.size_label.setObjectName("sizeLabel")
        self.size_label.setProperty("role", "sub")
        self.timer_label = QLabel("00:00:00")
        self.timer_label.setObjectName("timerLabel")
        self.timer_label.setProperty("role", "text")
        self.audio_meter = AudioPeakMeter()
        self.audio_meter.setToolTip("实时音频电平（默认录制设备）")
        slay.addWidget(self.status_label)
        slay.addStretch(1)
        slay.addWidget(self.audio_meter)
        slay.addSpacing(10)
        slay.addWidget(self.size_label)
        slay.addSpacing(10)
        slay.addWidget(self.timer_label)
        rec_lay.addWidget(status)

        card_lay.addWidget(rec_card)
        self.rec_card = rec_card  # 供 _apply_theme 深色模式刷新

        # 全局 QSS 提升到 QApplication 级：确保所有控件（含 QStackedWidget 内
        # 的 combo、独立 QMessageBox、下拉 popup）都统一继承原版那套样式，
        # 从根上解决“中间控件 setStyleSheet 导致后代继承不到 / 独立窗口黑底”。
        QApplication.instance().setStyleSheet(self._global_qss())
        QApplication.instance().setPalette(self._app_palette())

        # ---- 手动自由缩放布置（原型式：以可见卡片边缘为基准，避免透明阴影区命中外移） ----
        self._resize_margin = 8
        self.card.setMouseTracking(True)
        self._set_mouse_tracking_recursive(self.card)
        self.installEventFilter(self)
        self.card.installEventFilter(self)
        self._install_event_filter_recursive(self.card)

        self._select(0)
        self._lock_min_height()

    # ---------------- 左侧导航 + 右侧详情 ----------------
    def _build_sidebar(self, body):
        side = QVBoxLayout()
        side.setContentsMargins(16, 16, 16, 16)
        side.setSpacing(6)

        # 品牌区（固定高度，缩小时不可压缩）
        brand = QHBoxLayout()
        brand.setContentsMargins(0, 0, 0, 0)
        brand.setSpacing(6)
        brand.setAlignment(Qt.AlignVCenter)
        ICON_SZ = 68
        icon_lbl = QLabel()
        icon_lbl.setFixedSize(ICON_SZ, ICON_SZ)
        icon_lbl.setAlignment(Qt.AlignCenter)
        icon_lbl.setStyleSheet("background:transparent;")
        pm = self._app_icon().pixmap(ICON_SZ, ICON_SZ)
        if pm and not pm.isNull():
            icon_lbl.setPixmap(pm.scaled(ICON_SZ - 4, ICON_SZ - 4,
                                          Qt.KeepAspectRatio, Qt.SmoothTransformation))
        brand.addWidget(icon_lbl, alignment=Qt.AlignVCenter)
        name_box = QWidget()
        name_box.setFixedHeight(ICON_SZ)
        name_box.setStyleSheet("background:transparent;")
        name_lay = QVBoxLayout(name_box)
        name_lay.setContentsMargins(0, 0, 0, 0)
        name_lay.setSpacing(0)
        name_lay.addStretch(1)
        cn = QLabel("轻量录屏")
        cn.setProperty("role", "heading")
        cn.setFont(_heading_font(18, QFont.Weight.DemiBold))
        cn.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        en = QLabel("ScreenRecorder")
        en.setObjectName("brandEnLbl")
        en.setFont(_heading_font(13, QFont.Weight.Medium))
        en.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.brand_en_lbl = en
        name_lay.addWidget(cn)
        name_lay.addWidget(en)
        name_lay.addStretch(1)
        brand.addWidget(name_box, alignment=Qt.AlignVCenter)
        brand.addStretch(1)
        brand_box = QWidget()
        brand_box.setFixedHeight(ICON_SZ)
        brand_box.setStyleSheet("background:transparent;")
        brand_box.setLayout(brand)
        side.addWidget(brand_box)
        side.addSpacing(8)

        # 品牌区与导航之间的细分割线
        sep = QFrame()
        sep.setObjectName("sideSep")
        sep.setFrameShape(QFrame.HLine)
        sep.setFixedHeight(1)
        side.addWidget(sep)
        side.addSpacing(10)

        self.nav_items = []
        for i, (key, label) in enumerate(
                (("range", "录制范围"), ("params", "录制参数"), ("plan", "计划分段"),
                 ("pref", "偏好"), ("save", "保存位置"), ("history", "历史记录"))):
            item = NavItem(i, key, label, win=self)
            item.clicked.connect(self._select)
            self.nav_items.append(item)
            side.addWidget(item)
        side.addStretch(1)

        side_wrap = QWidget()
        side_wrap.setObjectName("sideWrap")
        side_wrap.setLayout(side)
        side_wrap.setFixedWidth(220)
        # 背景由全局 QSS（#sideWrap）随主题统一换肤（ID 选择器限定，不级联子控件）
        self.side_wrap = side_wrap
        body.addWidget(side_wrap)

    def _build_detail(self, body):
        self.stack = QStackedWidget()
        # 加 ID 选择器，严防无选择器样式下穿：此前 "background:transparent;border:none;"
        # 会级联渗透给栈内所有子控件（含各页 QComboBox/QLineEdit/QSpinBox），
        # 导致选择框本体白底与 1px 边框被父级强制清空。
        self.stack.setObjectName("detailStack")
        self.stack.setStyleSheet("#detailStack{background:transparent;border:none;}")
        self.page_range = PageRange(self)
        self.page_params = PageParams(self)
        self.page_plan = PagePlan(self)
        self.page_pref = PagePref(self)
        self.page_save = PageSave(self)
        self.page_history = PageHistory(self)
        for p in (self.page_range, self.page_params, self.page_plan,
                  self.page_pref, self.page_save, self.page_history):
            self.stack.addWidget(p)
        body.addWidget(self.stack, 1)

    def _select(self, index):
        for i, it in enumerate(self.nav_items):
            it.set_selected(i == index)
        if hasattr(self, "stack") and self.stack.currentIndex() != index:
            self._fade_page(index)
        else:
            self.stack.setCurrentIndex(index)
        if index == 5 and hasattr(self, "page_history"):
            self.page_history.refresh()
        self._lock_min_height()

    def _fade_page(self, index):
        """页面切换 120ms 轻量线性淡入：连续快速点击时先停止旧动画并清理旧 Effect，避免闪烁叠加。"""
        try:
            # 互斥：停止并清理上一轮动画/特效，防止快速连击时多个动画叠加污染
            if hasattr(self, "_page_anim") and self._page_anim:
                try:
                    self._page_anim.stop()
                except Exception:
                    pass
                self._page_anim = None
            try:
                old_effect = self.stack.currentWidget().graphicsEffect()
                if isinstance(old_effect, QGraphicsOpacityEffect):
                    self.stack.currentWidget().setGraphicsEffect(None)
            except Exception:
                pass

            w = self.stack.widget(index)
            if w is None:
                self.stack.setCurrentIndex(index)
                return
            effect = QGraphicsOpacityEffect(w)
            w.setGraphicsEffect(effect)
            effect.setOpacity(0.0)
            self.stack.setCurrentIndex(index)
            anim = QPropertyAnimation(effect, b"opacity", self)
            anim.setDuration(120)  # 更短的时长避免卡顿
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Linear)  # 线性过渡，更轻量

            def _done():
                # 动画结束后移除效果，恢复原生绘制，避免残留副作用
                try:
                    if w.graphicsEffect() is effect:
                        w.setGraphicsEffect(None)
                except Exception:
                    pass

            anim.finished.connect(_done)
            self._page_anim = anim  # 防止被 GC
            anim.start()
        except Exception:
            self.stack.setCurrentIndex(index)

    def _on_audio_level(self, v):
        """接收 FFmpeg 录音/测试链路的实时电平，驱动主面板与悬浮窗跳动条（主线程）。"""
        try:
            if hasattr(self, "audio_meter"):
                self.audio_meter.set_level(v)
            if self.float_win:
                self.float_win.set_level(v)
        except Exception:
            pass

    def _app_palette(self):
        """应用级调色板：驱动下拉 popup / 提示框等「浮动控件」的原生背景。

        主窗口是 WA_TranslucentBackground，QSS 的 background 简写在透明表面上常常
        不生效，导致下拉框 popup 与 QMessageBox 渲染成黑底。这些浮动控件的底色由
        QPalette（Base / Highlight / Window 等角色）原生决定，因此必须用调色板显式
        给出不透明颜色，才能稳定恢复「白底 + 高亮」的原正式版样式。
        """
        p = QApplication.palette()
        c = self.col
        p.setColor(QPalette.Window, QColor(c["card"]))
        p.setColor(QPalette.WindowText, QColor(c["text"]))
        p.setColor(QPalette.Base, QColor(c["input_bg"]))
        p.setColor(QPalette.AlternateBase, QColor(c["input_bg"]))
        p.setColor(QPalette.Text, QColor(c["text"]))
        p.setColor(QPalette.Button, QColor(c["hover_bg"]))
        p.setColor(QPalette.ButtonText, QColor(c["text"]))
        p.setColor(QPalette.Highlight, QColor(c["selected_bg"]))
        p.setColor(QPalette.HighlightedText, QColor(c["text"]))
        p.setColor(QPalette.ToolTipBase, QColor(c["menu_bg"]))
        p.setColor(QPalette.ToolTipText, QColor(c["text"]))
        p.setColor(QPalette.Link, QColor(c["accent"]))
        return p

    def _global_qss(self):
        c = self.col
        return f"""
        QWidget{{color:{c['text']};font-size:10pt;}}
        #card{{
            background:{c['card']};border-radius:22px;border:1px solid {c['line']};
        }}
        #reccard{{
            background:{c['section_bg']};border-top:1px solid {c['section_border']};
            border-bottom-left-radius:22px;border-bottom-right-radius:22px;
        }}
        #sectionCard{{
            background:{c['section_bg']};border:1px solid {c['section_border']};border-radius:14px;
        }}
        #sectionCard:hover{{
            border:1px solid {c['hover_border']};
        }}
        #cardSep{{color:{c['line']};background:{c['line']};max-height:1px;}}
        #sideSep{{color:{c['line']};background:{c['line']};}}
        #sideWrap{{background:{c['bg']};}}
        #brandEnLbl{{color:{c['sub']};background:transparent;}}
        QLabel[role="heading"]{{color:{c['text']};}}
        QLabel[role="sub"]{{color:{c['sub']};font-size:10pt;}}
        QLabel[role="text"]{{color:{c['text']};font-size:10pt;}}
        #sizeLabel{{color:{c['sub']};font:12px Consolas;}}
        #timerLabel{{color:{c['text']};font:14px Consolas;}}
        /* 历史记录页：滚动区/视口/容器透明脱底，行卡片深浅色自适应 + 悬浮高亮 */
        QScrollArea#historyScroll{{border:none;background:transparent;}}
        QScrollArea#historyScroll > QWidget > QWidget{{background:transparent;border:none;}}
        #historyContainer{{background:transparent;}}
        #histRow{{
            background:{c['history_row_bg']};border:1px solid {c['history_row_border']};
            border-radius:12px;
        }}
        #histRow:hover{{
            background:{c['hover_bg']};border-color:{c['hover_border']};
        }}
        QComboBox{{
            border:1px solid {c['input_border']};border-radius:8px;padding:4px 10px;
            background:{c['input_bg']};color:{c['text']};selection-color:{c['text']};min-height:22px;
        }}
        QComboBox:hover{{border-color:{c['checkbox_border']};}}
        QComboBox:focus{{border-color:{c['accent']};border-width:2px;padding:3px 9px;}}
        QComboBox::drop-down{{border:none;width:24px;}}
        QComboBox QAbstractItemView{{
            border:1px solid {c['input_border']};border-radius:8px;background:{c['input_bg']};
            color:{c['text']};selection-background-color:{c['selected_bg']};selection-color:{c['text']};
            padding:4px 6px;outline:0px;
        }}
        QLineEdit{{
            border:1px solid {c['input_border']};border-radius:8px;padding:5px 10px;background:{c['input_bg']};
            color:{c['text']};
        }}
        QLineEdit:hover{{border-color:{c['checkbox_border']};}}
        QLineEdit:focus{{border-color:{c['accent']};border-width:2px;padding:4px 9px;}}
        QDateTimeEdit, QSpinBox{{
            border:1px solid {c['input_border']};border-radius:8px;padding:4px 10px;
            background:{c['input_bg']};color:{c['text']};min-height:22px;
        }}
        QDateTimeEdit:hover, QSpinBox:hover{{border-color:{c['checkbox_border']};}}
        QDateTimeEdit:focus, QSpinBox:focus{{border-color:{c['accent']};border-width:2px;padding:3px 9px;}}
        QDateTimeEdit::drop-down{{border:none;width:24px;}}
        QSpinBox::up-button, QSpinBox::down-button{{
            border:none;background:transparent;width:18px;
        }}
        QPushButton{{
            background:{c['hover_bg']};color:{c['text']};border:1px solid {c['hover_border']};border-radius:8px;
            font-size:10pt;padding:0 14px;
        }}
        QPushButton:hover{{background:{c['pressed_bg']};border-color:{c['hover_border']};}}
        QPushButton:pressed{{background:{c['pressed_bg']};}}
        QPushButton:checked{{
            background:{c['selected_bg']};color:{c['text']};border:1px solid {c['selected_border']};
        }}
        QPushButton:focus{{outline:none;}}
        QPushButton:disabled{{color:{c['disabled_text']};background:{c['section_bg']};border-color:{c['section_border']};}}
        QCheckBox{{
            color:{c['text']};font-size:10pt;spacing:7px;
        }}
        QCheckBox::indicator{{
            width:16px;height:16px;border-radius:4px;border:1px solid {c['checkbox_border']};
            background:{c['input_bg']};
        }}
        QCheckBox::indicator:hover{{border-color:{c['accent']};}}
        QCheckBox::indicator:checked{{background:{c['accent']};border:1px solid {c['accent']};}}
        QMenu{{
            background:{c['menu_bg']};border:1px solid {c['menu_border']};border-radius:10px;padding:6px;
        }}
        QMenu::item{{padding:7px 24px;border-radius:6px;color:{c['text']};font-size:10pt;}}
        QMenu::item:selected{{background:{c['selected_bg']};color:{c['accent']};}}
        QMenu::separator{{height:1px;background:{c['line']};margin:4px 10px;}}
        QToolTip{{
            background:{c['menu_bg']};color:{c['text']};border:1px solid {c['menu_border']};
            border-radius:6px;padding:4px 8px;
        }}
        QScrollBar:vertical{{
            background:transparent;width:6px;margin:2px;
        }}
        QScrollBar::handle:vertical{{
            background:{c['scroll_handle']};border-radius:3px;min-height:30px;
        }}
        QScrollBar::handle:vertical:hover{{
            background:{c['hover_bg']};
        }}
        QScrollBar:horizontal{{
            background:transparent;height:6px;margin:2px;
        }}
        QScrollBar::handle:horizontal{{
            background:{c['scroll_handle']};border-radius:3px;min-width:30px;
        }}
        QScrollBar::handle:horizontal:hover{{
            background:{c['hover_bg']};
        }}
        QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}
        QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{{width:0;}}
        QMessageBox{{
            background:{c['card']};color:{c['text']};
        }}
        QMessageBox QLabel{{
            color:{c['text']};background:transparent;font-size:10pt;
        }}
        QMessageBox QPushButton{{
            background:{c['hover_bg']};color:{c['text']};border:1px solid {c['hover_border']};
            border-radius:8px;font-size:10pt;padding:6px 18px;min-width:64px;
        }}
        QMessageBox QPushButton:hover{{
            background:{c['pressed_bg']};border-color:{c['hover_border']};
        }}
        QMessageBox QPushButton:disabled{{
            color:{c['disabled_text']};background:{c['section_bg']};border-color:{c['section_border']};
        }}
        """

    # ---------------- 主题切换 ----------------
    def _apply_theme(self):
        self.col = DARK_THEME if self.dark else LIGHT_THEME
        # 同步旧全局常量，供 TitleBar/RecordingRing/_app_icon 等外部绘制使用
        global C_BG, C_CARD, C_TEXT, C_SUB, C_ACCENT, C_REC, C_REC_DARK, C_OK, C_LINE
        C_BG = self.col["bg"]
        C_CARD = self.col["card"]
        C_TEXT = self.col["text"]
        C_SUB = self.col["sub"]
        C_ACCENT = self.col["accent"]
        C_REC = self.col["rec"]
        C_REC_DARK = self.col["rec_dark"]
        C_OK = self.col["ok"]
        C_LINE = self.col["line"]

        # 性能优化：不再逐个控件循环 setStyleSheet，主题色全部收敛到全局 QSS 与调色板，
        # 仅执行两次应用级更新，让 Qt 在单帧内完成整树重绘（毫秒级切主题）。
        QApplication.instance().setStyleSheet(self._global_qss())
        QApplication.instance().setPalette(self._app_palette())
        if self.title_bar:
            self.title_bar.apply_theme(self.col)
        if self.ring:
            self.ring.col = self.col
            self.ring.update()
        for it in getattr(self, "nav_items", []):
            it.set_selected(it._selected)
            it.update()
        # 历史页「原地换肤」：不重扫盘、不重建行、不触发 ffmpeg（毫秒级瞬间切换）
        if hasattr(self, "page_history"):
            self.page_history.apply_theme(self.col)
        self._set_mode(self.core.mode)
        self._refresh_status_color()

    def _refresh_status_color(self):
        t = self.status_label.text() if hasattr(self, "status_label") else ""
        if "未找到" in t or "失败" in t or "异常" in t:
            color = self.col["rec"]
        elif "已完成" in t:
            color = self.col["ok"]
        elif "录制中" in t or "即将开始" in t:
            color = self.col["rec"]
        elif "ffmpeg 已就绪" in t:
            color = self.col["ok"]
        else:
            color = self.col["sub"]
        self.status_label.setStyleSheet(f"color:{color};font-size:10pt;")

    def _on_dark_toggled(self, checked):
        self.dark = bool(checked)
        self._apply_theme()
        self._schedule_save_config()

    def _on_window_method_changed(self, *_):
        self.core.window_capture_method = (
            "desktop" if self.win_method_combo.currentText().startswith("桌面合成")
            else "printwindow")
        self._schedule_save_config()

    # ---------------- 开机自启 ----------------
    @staticmethod
    def _autostart_command():
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}" --autostart'
        script = os.path.abspath(sys.argv[0])
        return f'"{sys.executable}" "{script}" --autostart'

    def _is_autostart_enabled(self):
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Run",
                                 0, winreg.KEY_READ)
            try:
                val, _ = winreg.QueryValueEx(key, "ScreenRecorder")
                return bool(val) and "ScreenRecorder" in val
            finally:
                winreg.CloseKey(key)
        except FileNotFoundError:
            return False
        except Exception:
            return False

    def _set_autostart(self, enabled):
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Run",
                                 0, winreg.KEY_SET_VALUE)
            if enabled:
                winreg.SetValueEx(key, "ScreenRecorder", 0, winreg.REG_SZ,
                                  self._autostart_command())
            else:
                try:
                    winreg.DeleteValue(key, "ScreenRecorder")
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
            return True
        except Exception:
            return False

    def _on_autostart_toggled(self, checked):
        ok = self._set_autostart(bool(checked))
        if not ok:
            _msg_warning(self, "开机自启", "写入注册表失败，请检查权限后重试。", self.col)
            self.chk_autostart.blockSignals(True)
            self.chk_autostart.setChecked(not checked)
            self.chk_autostart.blockSignals(False)
        self._schedule_save_config()

    def _on_float_stay_toggled(self, checked):
        self._schedule_save_config()
        if checked:
            self._show_float()
        else:
            self._hide_float()

    def _on_float_mini_toggled(self, checked):
        self._schedule_save_config()
        if self.float_win:
            self.float_win.set_compact(checked)

    # ---------------- 自定义快捷键 ----------------
    def _set_hotkey(self):
        dlg = HotkeyDialog(self, self.hotkey_edit.text(), col=self.col)
        if dlg.exec() == QDialog.Accepted:
            self.hotkey_edit.setText(dlg.result_text)
            self._on_hotkey_changed()

    def _set_mode(self, mode):
        self.core.mode = mode
        for k, b in self.mode_btns.items():
            on = (k == mode)
            b.setChecked(on)
            b.setStyleSheet(
                f"QPushButton{{background:{self.col['accent'] if on else self.col['hover_bg']};"
                f"color:{'#fff' if on else self.col['text']};border:none;border-radius:8px;"
                f"font-size:10pt;}}"
                f"QPushButton:hover{{background:{self.col['accent'] if on else self.col['pressed_bg']};}}")
        if mode == "region":
            self.range_extra.setCurrentIndex(1)
        elif mode == "window":
            self.range_extra.setCurrentIndex(2)
        else:
            self.range_extra.setCurrentIndex(0)
        self._schedule_save_config()
        self._on_setting_changed()

    def _on_setting_changed(self, *_):
        self.core.fps = self.fps_combo.currentText()
        self.core.quality = self.quality_combo.currentText()
        self.core.fmt = self.fmt_combo.currentText()
        self.core.audio = self.audio_combo.currentText()
        if hasattr(self, "encoder_combo"):
            self.core.encoder = self.encoder_combo.currentText()
        if hasattr(self, "bitrate_combo"):
            br_text = self.bitrate_combo.currentText()
            if br_text.startswith("不设置"):
                self.core.bitrate = ""
            else:
                try:
                    mbps = float(br_text.split(" ")[0])
                    self.core.bitrate = f"{int(mbps * 1000)}k"
                except Exception:
                    self.core.bitrate = ""
        if hasattr(self, "scale_combo"):
            scale_text = self.scale_combo.currentText()
            try:
                if scale_text.startswith("原始"):
                    self.core.scale = 1.0
                else:
                    token = scale_text.split("（", 1)[0].strip()  # 如 "0.5x"
                    self.core.scale = float(token[:-1]) if token.endswith("x") else 1.0
            except Exception:
                self.core.scale = 1.0
        if hasattr(self, "mic_combo"):
            mic_text = self.mic_combo.currentText()
            self.core.mic = "" if mic_text == "（自动选择）" else mic_text
        self.core.update_estimate()
        self._update_summary()

    def _update_summary(self):
        """底部录制区的配置摘要（原型新增的详细状态信息，实时反映当前设置）。"""
        try:
            if not hasattr(self, "summary_label"):
                return
            mode_map = {"full": "全屏", "full_all": "全屏(多屏)",
                        "region": "区域", "window": "窗口"}
            m = mode_map.get(getattr(self.core, "mode", "full"), "全屏")
            fps = self.fps_combo.currentText() if hasattr(self, "fps_combo") else "30"
            q = self.quality_combo.currentText().split(" ")[0] if hasattr(self, "quality_combo") else ""
            a = self.audio_combo.currentText() if hasattr(self, "audio_combo") else "无音频"
            f = self.fmt_combo.currentText().split(" ")[0] if hasattr(self, "fmt_combo") else "MP4"
            d = self.core.save_dir
            self.summary_label.setText(f"{m} · {fps} fps · {q} · {a} · {f} · 保存到 {d}")
        except Exception:
            pass

    def _on_mic_changed(self, *_):
        self._schedule_save_config()

    def _update_mic_visibility(self, *_):
        if hasattr(self, "mic_box"):
            self.mic_box.setVisible(self.audio_combo.currentText() == AUDIO_SYSTEM_MIC)

    def _on_delay_changed(self, *_):
        self.core.delay = self.delay_combo.currentText()
        self._schedule_save_config()
        self._update_summary()

    def _on_plan_changed(self, *_):
        self.core.max_duration_seconds = (
            self.spin_duration.value() * 60 if self.chk_duration_limit.isChecked() else 0)
        self.core.segment_seconds = (
            self.spin_segment.value() * 60 if self.chk_auto_segment.isChecked() else 0)
        self.core.scheduled_start = (
            self.datetime_start.dateTime().toString("yyyy-MM-dd HH:mm:ss")
            if self.chk_schedule_start.isChecked() else None)
        self._schedule_save_config()

    def _on_hotkey_changed(self, *_):
        if hasattr(self, "tip_label"):
            self.tip_label.setText(
                f"{self.hotkey_edit.text()} 开始 / 停止 · 录制时自动收起到托盘")
        self._schedule_save_config()
        self._setup_hotkey()

    # ---------------- 核心连接 ----------------
    def _connect_core(self):
        self.core.status_changed.connect(self._on_status)
        self.core.estimate_changed.connect(self.estimate_label.setText)
        self.core.started.connect(self._on_started)
        self.core.finished.connect(self._on_finished)
        self.core.error.connect(self._on_error)
        self.core.restart_requested.connect(self._on_auto_restart_requested)
        self.core.audio_items_ready.connect(self._on_audio_items)
        self.core.log_line.connect(self._on_log)
        self.core.audio_level.connect(self._on_audio_level)

    def _on_status(self, text, color):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color:{color};font-size:10pt;")

    def _on_started(self):
        self.ring.set_recording(True)
        self.ring_caption.setText("停止录制")
        self._set_controls_enabled(False)
        self.ui_timer.start()
        if self.chk_float.isChecked() or self.chk_float_stay.isChecked():
            self._show_float()
            if self.float_win:
                self.float_win.set_recording()
        # 开始录制后自动最小化到系统托盘
        self.hide_to_tray()

    def _on_auto_restart_requested(self):
        # 自动分段/窗口尺寸变化后的续录：不经过 _on_finished，不打断 UI 状态
        self._write_log_file("[自动分段] 继续录制下一段")
        self.core.start_record(continuing=True)

    def _on_finished(self, path):
        self._last_out = path
        # ui_timer 常驻，保证“定时开始”在录制结束后仍能继续检查
        self.ring.set_recording(False)
        self.ring.set_time("00:00:00")
        self.ring_caption.setText("开始录制")
        self._set_controls_enabled(True)
        self.timer_label.setText("00:00:00")
        self.size_label.setText("")
        self._set_actions_enabled(True)
        # 录制结束：电平归零（FFmpeg 进程已退出，不再有 astats 信号）
        if hasattr(self, "audio_meter"):
            self.audio_meter.set_level(0)
        if self.float_win:
            self.float_win.set_level(0)
        if self.chk_float_stay.isChecked():
            # 悬浮框常驻：录制结束不返回主界面，悬浮窗继续留在桌面
            self._show_float()
            if self.float_win:
                self.float_win.set_idle()
        else:
            self._hide_float()
            self.showNormal()
            self.raise_()
        self.tray.showMessage("录制完成", os.path.basename(path), QSystemTrayIcon.Information, 2000)
        # 历史记录页跟随实际保存目录自动刷新
        if hasattr(self, "page_history"):
            self.page_history.refresh()

    def _on_error(self, msg):
        # ui_timer 常驻，定时开始功能不因错误停止
        self.ring.set_recording(False)
        self.ring_caption.setText("开始录制")
        self.timer_label.setText("00:00:00")
        self.size_label.setText("")
        self._set_controls_enabled(True)
        self._set_actions_enabled(True)
        # 出错时也归零电平并复位测试按钮
        if hasattr(self, "audio_meter"):
            self.audio_meter.set_level(0)
        if self.float_win:
            self.float_win.set_level(0)
        self._audio_test_active = False
        if self.chk_float_stay.isChecked():
            # 常驻模式下出错也保留悬浮窗，并复位为可开始状态
            self._show_float()
            if self.float_win:
                self.float_win.set_idle()
        else:
            self._hide_float()
        self.showNormal()
        self.raise_()
        if not isinstance(msg, str) or not msg.strip():
            msg = "发生未知错误，详细信息见日志文件。"
        self._write_log_file(f"[错误] {msg}")
        _msg_warning(self, "提示", msg, self.col)

    def _on_log(self, msg):
        self._write_log_file(msg)

    def _on_audio_items(self, items):
        cur = self.audio_combo.currentText()
        self.audio_combo.blockSignals(True)
        self.audio_combo.clear()
        self.audio_combo.addItems(items)
        if cur in items:
            self.audio_combo.setCurrentText(cur)
        self.audio_combo.blockSignals(False)
        # 麦克风列表：过滤掉“立体声混音/What U Hear”等系统声音设备
        sys_kw = ["Stereo Mix", "立体声混音", "What U Hear", "virtual-audio-capturer"]
        mics = [d for d in getattr(self.core, "_audio_devices", [])
                if not any(kw.lower() in d.lower() for kw in sys_kw)]
        self.mic_combo.blockSignals(True)
        self.mic_combo.clear()
        self.mic_combo.addItem("（自动选择）")
        self.mic_combo.addItems(mics)
        if self.core.mic in mics:
            self.mic_combo.setCurrentText(self.core.mic)
        else:
            self.mic_combo.setCurrentText("（自动选择）")
        self.mic_combo.blockSignals(False)
        self._update_mic_visibility()
        self._on_setting_changed()

    def _set_controls_enabled(self, enabled):
        for w in (self.quality_combo, self.fps_combo, self.fmt_combo, self.audio_combo,
                  self.mic_combo, self.encoder_combo, self.bitrate_combo, self.scale_combo,
                  self.win_combo, self.dir_edit, self.prefix_edit, self.template_edit,
                  self.delay_combo, self.hotkey_edit, self.btn_hotkey, self.win_method_combo,
                  self.chk_auto, self.chk_float, self.chk_dark, self.chk_autostart,
                  self.chk_float_stay, self.chk_schedule_start, self.datetime_start,
                  self.chk_duration_limit, self.spin_duration,
                  self.chk_auto_segment, self.spin_segment, self.btn_browse,
                  self.btn_region, self.btn_win_refresh, self.btn_audio_refresh,
                  self.btn_audio_test, self.btn_ffmpeg):
            w.setEnabled(enabled)
        for k, b in self.mode_btns.items():
            b.setEnabled(enabled)

    def _set_actions_enabled(self, enabled):
        self.btn_preview.setEnabled(enabled)
        self.btn_openfolder.setEnabled(enabled)

    # ---------------- 交互 ----------------
    def _toggle_record(self):
        if self.core.recording or self.core._finalizing:
            # 立即给出“停止中”反馈，避免悬浮窗看起来无响应
            if self.float_win:
                self.float_win.set_stopping()
            self.core.stop_record()
        else:
            self.core.start_record()

    def _ui_tick(self):
        # 定时开始：到点后自动启动录制
        if not self.core.recording and getattr(self.core, "scheduled_start", None):
            try:
                dt = QDateTime.fromString(self.core.scheduled_start, "yyyy-MM-dd HH:mm:ss")
                if dt.isValid() and QDateTime.currentDateTime() >= dt:
                    self.core.scheduled_start = None
                    self.chk_schedule_start.setChecked(False)
                    self._schedule_save_config()
                    self.core.start_record()
            except Exception:
                pass
        if not self.core.recording:
            return
        if self.core.proc is None:
            self.timer_label.setText("准备中…")
            self.ring.set_time("准备中…")
            return
        # 智能磁盘熔断保护：剩余空间低于 500MB 时自动安全收尾
        try:
            save_dir = self.core.save_dir
            if save_dir and os.path.isdir(save_dir):
                import shutil as _shutil
                usage = _shutil.disk_usage(save_dir)
                free_mb = usage.free / (1024 * 1024)
                if free_mb < 500:
                    self._write_log_file(f"[磁盘熔断] 保存分区剩余 {free_mb:.0f}MB，触发安全收尾")
                    self.core.stop_record()
                    QMessageBox.warning(
                        self, "磁盘空间不足",
                        f"保存分区仅剩 {free_mb:.0f} MB，已自动停止录制以保护数据。\n"
                        f"请清理磁盘空间后重新录制。")
                    return
        except Exception:
            pass
        try:
            elapsed = int(time.time() - self.core.start_time)
            h = elapsed // 3600
            m = (elapsed % 3600) // 60
            s = elapsed % 60
            t = f"{h:02d}:{m:02d}:{s:02d}"
            self.timer_label.setText(t)
            self.ring.set_time(t)
            live = self.core._write_path or self.core._out_path
            size = ""
            if live and os.path.exists(live):
                size = self.core._fmt_size(os.path.getsize(live))
            self.size_label.setText(size)
            if self.float_win:
                self.float_win.set_timer(t)
                self.float_win.set_size(size)
        except Exception:
            pass

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择保存目录", self.dir_edit.text())
        if d:
            self.dir_edit.setText(d)
            self.core.save_dir = d
            self._schedule_save_config()

    def _pick_ffmpeg(self):
        p = QFileDialog.getOpenFileName(self, "选择 ffmpeg.exe", "", "ffmpeg (ffmpeg.exe)")[0]
        if p:
            self.ffmpeg = os.path.abspath(p)
            self.core.ffmpeg = self.ffmpeg
            self._refresh_ffmpeg_state()

    def _refresh_ffmpeg_state(self):
        if self.ffmpeg and os.path.exists(self.ffmpeg):
            self.status_label.setText("ffmpeg 已就绪")
            self.status_label.setStyleSheet(f"color:{self.col['ok']};font-size:10pt;")
            self.core.refresh_audio_devices()
        else:
            self.status_label.setText("ffmpeg 未找到（录制不可用）")
            self.status_label.setStyleSheet(f"color:{self.col['rec']};font-size:10pt;")

    def _select_region(self):
        self.region_selector = RegionSelector(self)
        self.region_selector.region_selected.connect(self._on_region_selected)
        self.region_selector.show()

    def _on_region_selected(self, region):
        if region:
            self.core.region = region
            _, _, w, h = region
            self.lbl_region.setText(f"区域 {w}×{h}")
        else:
            self.core.region = None
            self.lbl_region.setText("未选择区域")
        self.region_selector = None

    def _refresh_windows(self):
        items = get_window_list()
        self._win_map = {f"{title} [{hwnd}]": hwnd for hwnd, title in items}
        self.win_combo.blockSignals(True)
        self.win_combo.clear()
        self.win_combo.addItem("（请选择窗口）")
        for hwnd, title in items:
            self.win_combo.addItem(f"{title} [{hwnd}]")
        self.win_combo.blockSignals(False)

    def _on_win_selected(self, idx):
        title = self.win_combo.itemText(idx)
        if title and title in getattr(self, "_win_map", {}):
            self.core._win_hwnd = self._win_map[title]
        else:
            self.core._win_hwnd = None

    def _refresh_audio_devices(self):
        self.core.refresh_audio_devices()

    def _stop_audio_test(self):
        """停止录音测试并释放音频设备（v4.0：Stop → Release → Close）。"""
        self._audio_test_active = False
        proc = self._audio_test_proc
        self._audio_test_proc = None
        self._audio_test_thread = None
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=3)
            except Exception:
                pass
        # 归零
        if hasattr(self, "audio_meter"):
            self.audio_meter.set_level(0)
        if self.float_win:
            self.float_win.set_level(0)

    def _start_audio_test(self):
        """启动录音测试：临时打开所选音频源，astats 实时电平；结束后释放设备。"""
        cmd = self.core.build_audio_test_command()
        if not cmd or not self.ffmpeg or not os.path.exists(self.ffmpeg):
            _msg_warning(self, "录音测试", "当前音频模式无法测试（未选音频或无可用设备）。", self.col)
            # 复位按钮
            self.btn_audio_test.blockSignals(True)
            self.btn_audio_test.setChecked(False)
            self.btn_audio_test.blockSignals(False)
            self.btn_audio_test.setText("测试")
            self.btn_audio_test.setStyleSheet("")
            return
        try:
            self._audio_test_proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW)
        except Exception as e:
            _msg_warning(self, "录音测试", f"测试启动失败：{e}", self.col)
            self.btn_audio_test.blockSignals(True)
            self.btn_audio_test.setChecked(False)
            self.btn_audio_test.blockSignals(False)
            self.btn_audio_test.setText("测试")
            self.btn_audio_test.setStyleSheet("")
            return
        self._reset_audio_health()  # v6.0：重置音频健康（静音）告警状态
        self._audio_test_active = True

        def _monitor():
            buf = ""
            proc = self._audio_test_proc
            if proc is None:
                return
            while proc.poll() is None:
                try:
                    chunk = proc.stderr.read(4096)
                except Exception:
                    break
                if not chunk:
                    break
                buf += chunk.decode(errors="ignore")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip("\r").strip()
                    lv = self.core.feed_audio_meta_line(line)
                    if lv is not None:
                        self.core.audio_level.emit(lv)
            # 测试进程提前退出（如设备被占用）时复位
            if self._audio_test_active:
                QTimer.singleShot(0, self._on_test_proc_exited)

        self._audio_test_thread = threading.Thread(target=_monitor, daemon=True)
        self._audio_test_thread.start()

    def _on_test_proc_exited(self):
        """测试进程非用户停止而退出时：复位按钮并提示。"""
        if not self._audio_test_active:
            return
        try:
            self.btn_audio_test.blockSignals(True)
            self.btn_audio_test.setChecked(False)
            self.btn_audio_test.blockSignals(False)
            self.btn_audio_test.setText("测试")
            self.btn_audio_test.setStyleSheet("")
        except Exception:
            pass
        self._audio_test_active = False
        if hasattr(self, "audio_meter"):
            self.audio_meter.set_level(0)
        if self.float_win:
            self.float_win.set_level(0)

    def _on_audio_test_toggled(self, checked):
        """音频测试：点击开始（起 ffmpeg astats 监听），再点击停止并释放。"""
        if checked:
            self._start_audio_test()
            if self._audio_test_active:
                self.btn_audio_test.setText("停止")
                self.btn_audio_test.setStyleSheet(
                    "QPushButton{background:#ff453a;color:#fff;border:none;border-radius:8px;}"
                    "QPushButton:hover{background:#ff5e54;}")
        else:
            self._stop_audio_test()
            self.btn_audio_test.setText("测试")
            self.btn_audio_test.setStyleSheet("")

    # ---------------- 悬浮窗 ----------------
    def _show_float(self):
        if self.float_win is None:
            self.float_win = FloatingWidget()
            self.float_win.stop_requested.connect(self._toggle_record)
            self.float_win.show_main_requested.connect(
                lambda: (self.showNormal(), self.raise_(), self.activateWindow()))
            self.float_win.open_last_requested.connect(self._open_last_recording)
            self.float_win.hide_requested.connect(self._hide_float)
            self.float_win.mode_requested.connect(self._set_mode)
            # 恢复极简模式设置
            if hasattr(self, "chk_float_mini") and self.chk_float_mini.isChecked():
                self.float_win.set_compact(True)
        self.float_win.position_topright()
        self.float_win.show()

    def _open_last_recording(self):
        """打开上一条录制文件（悬浮窗右键菜单）。"""
        try:
            if self._last_out and os.path.exists(self._last_out):
                self.core._open_file(self._last_out)
            elif self.core.save_dir and os.path.isdir(self.core.save_dir):
                self.core._open_folder(self.core.save_dir)
        except Exception:
            pass

    def _hide_float(self):
        if self.float_win:
            self.float_win.hide()

    # ---------------- 托盘 ----------------
    def _setup_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self._app_icon())
        self.tray.setToolTip("轻量录屏 · ScreenRecorder")
        menu = QMenu()
        self.tray_menu = menu
        # 托盘菜单样式由应用级 QSS（QMenu）统一提供，深浅色自动跟随，不再单独设内联样式
        a_show = QAction("显示主窗口", self)
        a_show.triggered.connect(self.showNormal)
        a_start = QAction("开始录制", self)
        a_start.triggered.connect(lambda: self.core.start_record())
        a_stop = QAction("停止录制", self)
        a_stop.triggered.connect(lambda: self.core.stop_record())
        a_open = QAction("打开保存文件夹", self)
        a_open.triggered.connect(lambda: self.core._open_folder(self._last_out or self.core.save_dir))
        a_quit = QAction("退出", self)
        a_quit.triggered.connect(self._quit)
        menu.addAction(a_show)
        menu.addAction(a_start)
        menu.addAction(a_stop)
        menu.addSeparator()
        menu.addAction(a_open)
        menu.addAction(a_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.showNormal() if reason == QSystemTrayIcon.DoubleClick else None)
        self.tray.show()

    def _app_icon(self):
        try:
            if getattr(sys, "frozen", False):
                base = sys._MEIPASS
            else:
                base = os.path.dirname(os.path.abspath(__file__))
            path = os.path.join(base, "assets", "icon.png")
            if os.path.exists(path):
                return QIcon(path)
        except Exception:
            pass
        # fallback red dot
        pm = QPixmap(32, 32)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QBrush(QColor(C_REC)))
        p.setPen(Qt.NoPen)
        p.drawEllipse(4, 4, 24, 24)
        p.end()
        return QIcon(pm)

    def _quit(self):
        if self.core.recording:
            try:
                self.core.stop_record()
            except Exception:
                pass
        # 退出前必须释放录音测试占用的音频设备
        if self._audio_test_active:
            self._stop_audio_test()
        try:
            if self.hotkey:
                self.hotkey.stop()
        except Exception:
            pass
        QApplication.quit()

    def hide_to_tray(self):
        self.hide()

    def closeEvent(self, ev):
        # 关闭按钮 → 收起托盘（不退出），与原始行为一致
        ev.ignore()
        self.hide_to_tray()

    # ---------------- 全局快捷键 ----------------
    def _setup_hotkey(self):
        if self.hotkey:
            try:
                self.hotkey.stop()
            except Exception:
                pass
            self.hotkey = None
        text = self.hotkey_edit.text() if hasattr(self, "hotkey_edit") else "F9"
        parsed = parse_hotkey(text)
        if parsed is None:
            parsed = (VK_F9, ())
        vk, mods = parsed
        # 回调走 HotkeyEmitter：Hook 线程触发 → Qt 队列投递到主线程，杜绝跨线程操作 GUI
        self.hotkey = GlobalHotkey(vk=vk, modifiers=mods, callback=self._hotkey_emitter.triggered.emit)
        self.hotkey.start()

    # ---------------- 日志文件 ----------------
    def _log_path(self):
        base = os.environ.get("LOCALAPPDATA") or os.path.dirname(os.path.abspath(sys.argv[0]))
        d = os.path.join(base, "ScreenRecorder", "logs")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            d = os.path.dirname(os.path.abspath(sys.argv[0]))
        return os.path.join(d, "screen_recorder.log")

    def _write_log_file(self, msg):
        try:
            with open(self._log_path(), "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
        except Exception:
            pass

    def _open_log_file(self):
        path = self._log_path()
        try:
            if os.path.exists(path):
                os.startfile(path)
            else:
                d = os.path.dirname(path)
                os.makedirs(d, exist_ok=True)
                os.startfile(d)
        except Exception:
            pass

    # ---------------- 初始化 ----------------
    def _init_settings(self):
        cfg = self._load_config()
        # 先填控件
        self.fps_combo.setCurrentText(cfg["fps"])
        self.quality_combo.setCurrentText(cfg["quality"])
        self.fmt_combo.setCurrentText(cfg["fmt"])
        if self.audio_combo.findText(cfg["audio"]) >= 0:
            self.audio_combo.setCurrentText(cfg["audio"])
        else:
            self.audio_combo.addItem(cfg["audio"])
            self.audio_combo.setCurrentText(cfg["audio"])
        self.core.mic = cfg.get("mic", "")
        if hasattr(self, "mic_combo") and self.core.mic:
            if self.mic_combo.findText(self.core.mic) >= 0:
                self.mic_combo.setCurrentText(self.core.mic)
        self._update_mic_visibility()
        self.encoder_combo.setCurrentText(cfg["encoder"])
        if cfg.get("bitrate") in BITRATE_OPTIONS:
            self.bitrate_combo.setCurrentText(cfg["bitrate"])
        else:
            # 旧版自定义码率配置自动归到“不设置（CRF 质量）”
            self.bitrate_combo.setCurrentText("不设置（CRF 质量）")
        self.scale_combo.setCurrentText(cfg["scale"])
        if cfg.get("scheduled_start"):
            dt = QDateTime.fromString(cfg["scheduled_start"], "yyyy-MM-dd HH:mm:ss")
            if dt.isValid():
                self.datetime_start.setDateTime(dt)
        self.chk_schedule_start.setChecked(bool(cfg.get("scheduled_start")))
        self.chk_duration_limit.setChecked(cfg["max_duration_seconds"] > 0)
        if cfg["max_duration_seconds"] > 0:
            self.spin_duration.setValue(max(1, cfg["max_duration_seconds"] // 60))
        self.chk_auto_segment.setChecked(cfg["segment_seconds"] > 0)
        if cfg["segment_seconds"] > 0:
            self.spin_segment.setValue(max(1, cfg["segment_seconds"] // 60))
        self.delay_combo.setCurrentText(cfg["delay"])
        self.hotkey_edit.setText(cfg["hotkey"])
        self._on_plan_changed()
        if hasattr(self, "tip_label"):
            self.tip_label.setText(
                f"{self.hotkey_edit.text()} 开始 / 停止 · 录制时自动收起到托盘")
        self.dir_edit.setText(cfg["save_dir"])
        self.prefix_edit.setText(cfg["prefix"])
        if hasattr(self, "template_edit"):
            self.template_edit.setText(cfg.get("name_template", ""))
            self.core.name_template = cfg.get("name_template", "")
        self.chk_float.setChecked(cfg["show_float"])
        self.chk_auto.setChecked(cfg["auto_open"])
        self.chk_dark.setChecked(cfg["dark"])
        # 开机自启状态校验：以注册表实际状态为准，避免配置与注册表不一致
        actual_autostart = self._is_autostart_enabled()
        self.chk_autostart.blockSignals(True)
        self.chk_autostart.setChecked(actual_autostart)
        self.chk_autostart.blockSignals(False)
        self.chk_float_stay.setChecked(cfg["float_stay"])
        self.chk_float_mini.setChecked(cfg.get("float_mini", False))
        # 窗口捕获方式
        method_text = ("桌面合成（DXGI/游戏防黑屏）" if cfg["window_capture_method"] == "desktop"
                       else "PrintWindow（兼容旧窗口）")
        self.win_method_combo.setCurrentText(method_text)
        self.core.window_capture_method = cfg["window_capture_method"]
        # 同步到 core
        self.core.fps = cfg["fps"]
        self.core.quality = cfg["quality"]
        self.core.fmt = cfg["fmt"]
        self.core.audio = cfg["audio"]
        self.core.delay = cfg["delay"]
        self.core.save_dir = cfg["save_dir"]
        self.core.prefix = cfg["prefix"]
        self.core.auto_open = cfg["auto_open"]
        self._set_mode(cfg["mode"])
        # _set_mode 内部会按当前控件刷新 core，音频可能被“无音频”覆盖，这里再恢复保存值
        self.core.audio = cfg["audio"]
        self.core.mic_denoise = cfg.get("mic_denoise", False)
        if hasattr(self, "chk_denoise"):
            self.chk_denoise.setChecked(cfg.get("mic_denoise", False))
        self.core.update_estimate()
        if self.ffmpeg and os.path.exists(self.ffmpeg):
            self.core.refresh_audio_devices()
        else:
            self.status_label.setText("ffmpeg 未找到（录制不可用）")
            self.status_label.setStyleSheet(f"color:{self.col['rec']};font-size:10pt;")
        # 悬浮框常驻：启动后直接显示悬浮控制窗
        if self.chk_float_stay.isChecked():
            self._show_float()
        # 初始导航 + 历史记录页首次刷新（目录跟随 core.save_dir）
        self._select(0)
        if hasattr(self, "page_history"):
            self.page_history.refresh()
        # v4.0：不启动任何音频轮询/占用，电平信号仅来自录制/测试进程的 astats


def find_ffmpeg():
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "ffmpeg.exe"),
                 os.path.join(here, "ScreenRecorder_app", "ffmpeg.exe"),
                 "ffmpeg.exe"):
        if os.path.exists(cand):
            return os.path.abspath(cand)
    return None


def main():
    enable_dpi_awareness()
    # 高 DPI 位图 + 子像素抗锯齿(ClearType) + 非整数缩放透传，
    # 配合 per-monitor DPI awareness 获得最清晰的字体渲染。
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    # 子像素抗锯齿(ClearType)默认已开启；该属性在某些 PySide6 构建中不存在，安全判断避免崩溃
    if hasattr(Qt, "AA_SubpixelAntialiasing"):
        QApplication.setAttribute(Qt.AA_SubpixelAntialiasing, True)
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("ScreenRecorder Qt")
    # 关闭下拉框系统展开动画，改为即时弹出，避免卡顿/跳动
    QApplication.setEffectEnabled(Qt.UI_AnimateCombo, False)
    font = QFont()
    font.setFamilies(UI_FONT_FAMILIES)
    font.setPointSize(UI_FONT_SIZE_PT)
    font.setWeight(UI_FONT_WEIGHT)
    font.setStyleStrategy(UI_FONT_STRATEGY)
    font.setHintingPreference(UI_FONT_HINTING)
    if UI_FONT_LETTER_SPACING:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, UI_FONT_LETTER_SPACING)
    app.setFont(font)
    ffmpeg = find_ffmpeg()
    win = MainWindow(ffmpeg)
    if "--autostart" in sys.argv:
        # 开机自启：直接收起到托盘，不打扰用户
        win.hide_to_tray()
    else:
        win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
