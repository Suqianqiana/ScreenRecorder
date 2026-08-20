# -*- coding: utf-8 -*-
"""
轻量录屏工具 · PySide6 现代化精细版 (ScreenRecorder Qt - Modern Acrylic Edition)
================================================================================
UI / 前端全量现代化精细升级：
- 亚克力与毛玻璃微质感（多层渐变、镜面高光边缘、环境暗光）
- 全局环境漫射光晕 (Ambient Aura Glow) & 录制轨道呼吸光环
- 纯净即时交互（无任何悬停/按压位移动效，状态切换干脆高质感）
- 多段微光音频电平跳动计、一体化胶囊分段选择器、玻璃卡片历史列表
- 录制核心与 Win32 底层逻辑 100% 忠实保留
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
    QObject, Signal, QTimer, Qt, QRect, QRectF, QPoint, QPointF, QSize, QEvent, QFile, QIODevice,
    QDateTime, QUrl, QFileInfo, QPropertyAnimation, QEasingCurve, Property,
)
from PySide6.QtGui import (
    QIcon, QFont, QColor, QPainter, QPen, QBrush, QAction, QCursor, QPixmap,
    QPainterPath, QScreen, QKeySequence, QShortcut, QTextCharFormat, QTextCursor,
    QDesktopServices, QMouseEvent, QPalette, QImage, QLinearGradient, QRadialGradient,
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

# =====================================================================
#  现代高阶亚克力 & 弥散光影主题调色板
# =====================================================================
LIGHT_THEME = {
    "bg": "#f1f3f9",               # 侧边栏清爽浅灰底
    "card": "#ffffff",             # 主窗口纯白底
    "text": "#0f172a",             # 高对比深岩文字
    "sub": "#64748b",              # 副标题/说明文字
    "accent": "#2563eb",           # 高级皇家蓝强调色
    "accent_hover": "#1d4ed8",
    "selected_bg": "#dbeafe",      # 导航选中态清爽浅蓝
    "selected_border": "#93c5fd",
    "rec": "#ef4444",              # 录制高亮红
    "rec_glow": "rgba(239, 68, 68, 0.25)",
    "rec_dark": "#dc2626",
    "ok": "#10b981",               # 状态绿
    "line": "#e2e8f0",             # 精致分割线
    "section_bg": "#f8fafc",       # 分组卡片轻底色
    "section_border": "#e2e8f0",   # 分组卡片微边框
    "input_bg": "#ffffff",         # 输入框纯白底
    "input_border": "#cbd5e1",     # 输入框高质感边缘
    "hover_bg": "#f1f5f9",
    "hover_border": "#94a3b8",
    "pressed_bg": "#e2e8f0",
    "disabled_text": "#94a3b8",
    "menu_bg": "#ffffff",
    "menu_border": "#e2e8f0",
    "scroll_handle": "#cbd5e1",
    "checkbox_border": "#94a3b8",
    "history_row_bg": "#ffffff",
    "history_row_border": "#e2e8f0",
    "history_thumb_bg": "#f1f5f9",
    "danger_bg": "#fee2e2",
    "danger_text": "#dc2626",
}

DARK_THEME = {
    "bg": "#0c0e14",
    "card": "#151822",
    "card_grad_start": "rgba(24, 28, 40, 0.94)",
    "card_grad_end": "rgba(15, 17, 26, 0.96)",
    "card_border_top": "rgba(255, 255, 255, 0.16)",
    "card_border_bot": "rgba(255, 255, 255, 0.04)",
    "glow_primary": "rgba(79, 70, 229, 0.14)",
    "glow_accent": "rgba(6, 182, 212, 0.09)",
    "text": "#f8fafc",
    "sub": "#94a3b8",
    "accent": "#4f46e5",
    "accent_hover": "#6366f1",
    "accent_glow": "rgba(79, 70, 229, 0.45)",
    "rec": "#f43f5e",
    "rec_glow": "rgba(244, 63, 94, 0.45)",
    "rec_dark": "#e11d48",
    "ok": "#10b981",
    "line": "rgba(255, 255, 255, 0.08)",
    "section_bg": "rgba(25, 30, 44, 0.55)",
    "section_border": "rgba(255, 255, 255, 0.08)",
    "input_bg": "rgba(18, 22, 33, 0.75)",
    "input_border": "rgba(255, 255, 255, 0.14)",
    "hover_bg": "rgba(255, 255, 255, 0.06)",
    "hover_border": "rgba(255, 255, 255, 0.22)",
    "pressed_bg": "rgba(255, 255, 255, 0.10)",
    "selected_bg": "rgba(79, 70, 229, 0.25)",
    "selected_border": "rgba(99, 102, 241, 0.5)",
    "disabled_text": "#475569",
    "menu_bg": "rgba(21, 24, 34, 0.96)",
    "menu_border": "rgba(255, 255, 255, 0.12)",
    "scroll_handle": "rgba(255, 255, 255, 0.2)",
    "checkbox_border": "rgba(255, 255, 255, 0.25)",
    "history_row_bg": "rgba(24, 28, 42, 0.6)",
    "history_row_border": "rgba(255, 255, 255, 0.07)",
    "history_thumb_bg": "rgba(12, 14, 22, 0.8)",
    "danger_bg": "rgba(225, 29, 72, 0.18)",
    "danger_text": "#fb7185",
}

C_BG = LIGHT_THEME["bg"]
C_CARD = LIGHT_THEME["card"]
C_TEXT = LIGHT_THEME["text"]
C_SUB = LIGHT_THEME["sub"]
C_ACCENT = LIGHT_THEME["accent"]
C_REC = LIGHT_THEME["rec"]
C_REC_DARK = LIGHT_THEME["rec_dark"]
C_OK = LIGHT_THEME["ok"]
C_LINE = LIGHT_THEME["line"]

# ------------------------- 快捷键辅助 -------------------------
MOD_VKS = {"Ctrl": 0x11, "Shift": 0x10, "Alt": 0x12, "Win": 0x5B}
MOD_NAMES = {v: k for k, v in MOD_VKS.items()}
WM_SYSKEYDOWN = 0x0104


def _qt_key_name(key):
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


UI_FONT_FAMILIES = ["Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC"]
UI_FONT_SIZE_PT = 11
UI_FONT_WEIGHT = QFont.Weight.Normal
UI_FONT_HINTING = QFont.HintingPreference.PreferFullHinting
UI_FONT_STRATEGY = QFont.StyleStrategy.PreferAntialias
UI_FONT_LETTER_SPACING = 0.2


def _heading_font(size_pt, weight=QFont.Weight.DemiBold):
    f = QFont()
    f.setFamilies(UI_FONT_FAMILIES)
    f.setPointSize(size_pt)
    f.setWeight(weight)
    f.setStyleStrategy(UI_FONT_STRATEGY)
    f.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    if UI_FONT_LETTER_SPACING:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, UI_FONT_LETTER_SPACING)
    return f


# ------------------------- Win32 窗口枚举与截图 -------------------------
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


class HotkeyEmitter(QObject):
    triggered = Signal()


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
#  录制核心
# =====================================================================
class RecorderCore(QObject):
    status_changed = Signal(str, str)
    log_line = Signal(str)
    estimate_changed = Signal(str)
    started = Signal()
    finished = Signal(str)
    error = Signal(str)
    restart_requested = Signal()
    audio_items_ready = Signal(list)
    audio_level = Signal(float)

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
        self.window_capture_method = "printwindow"
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
        if self.ffmpeg and os.path.exists(self.ffmpeg):
            threading.Thread(target=self._detect_hw_encoder, daemon=True).start()
        self.mode = "full"
        self.fps = "30"
        self.quality = DEFAULT_QUALITY
        self.fmt = "MP4 (H.264, 推荐)"
        self.audio = "无音频"
        self.mic = ""
        self.encoder = "默认（自动）"
        self.bitrate = ""
        self.scale = 1.0
        self.max_duration_seconds = 0
        self.segment_seconds = 0
        self.scheduled_start = None
        self.save_dir = self._default_save_dir()
        self.prefix = "录屏"
        self.name_template = ""
        self.delay = "1 秒"
        self.auto_open = False

    def _render_name_template(self, tmpl):
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
        enc = self.encoder
        if enc == "默认（自动）":
            if q.get("force_software") or crf <= 0:
                enc = "libx264"
            else:
                enc = self._hw_encoder or "libx264"
        elif enc == "libx264（软件）":
            enc = "libx264"
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
                            f"指定窗口：{get_window_title(hwnd) or hwnd}，尺寸 {w}x{h}，桌面合成捕获")
                        cmd = [self.ffmpeg, "-y", "-f", "gdigrab", "-framerate", str(fps),
                               "-rtbufsize", "100M", "-draw_mouse", "1",
                               "-offset_x", str(r.left), "-offset_y", str(r.top),
                               "-video_size", f"{w}x{h}", "-i", "desktop"]
                    else:
                        self._using_raw_window_capture = True
                        self.log_line.emit(
                            f"指定窗口：{get_window_title(hwnd) or hwnd}，尺寸 {w}x{h}，PrintWindow 回退")
                        cmd = [self.ffmpeg, "-y", "-f", "rawvideo", "-pix_fmt", "bgra",
                               "-video_size", f"{w}x{h}", "-framerate", str(fps), "-i", "-"]
                else:
                    self._using_raw_window_capture = True
                    self.log_line.emit(
                        f"指定窗口：{get_window_title(hwnd) or hwnd}，尺寸 {w}x{h}，PrintWindow 抓取")
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
            cmd += ["-f", "wasapi", "-i", was_dev]
            cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-af", "astats"]
        elif audio_dev == AUDIO_SYSTEM_MIC:
            sys_dev = self._resolve_system_audio_device()
            if not sys_dev:
                return None, "未检测到“立体声混音”设备，无法录制系统声音。"
            mic_dev = self._resolve_mic_device()
            if not mic_dev:
                return None, "未检测到可用麦克风，无法使用“系统声音 + 麦克风混音”。"
            cmd += ["-f", "dshow", "-i", f"audio={sys_dev}"]
            cmd += ["-f", "dshow", "-i", f"audio={mic_dev}"]
            cmd += ["-filter_complex",
                    "[1:a:0][2:a:0]amix=inputs=2:duration=first:dropout_transition=3,"
                    "astats[aout]"]
            cmd += ["-map", "0:v:0", "-map", "[aout]", "-c:a", "aac"]
        else:
            real_audio = audio_dev
            if audio_dev == "系统声音（立体声混音）":
                real_audio = self._resolve_system_audio_device()
                if not real_audio:
                    return None, "未检测到“立体声混音”设备，无法录制系统声音。"
            cmd += ["-f", "dshow", "-i", f"audio={real_audio}"]
            cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-af", "astats"]

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
        if self.mic and self.mic in self._audio_devices:
            return self.mic
        system_kw = ["Stereo Mix", "立体声混音", "What U Hear", "virtual-audio-capturer"]
        for dev in self._audio_devices:
            low = dev.lower()
            if any(kw.lower() in low for kw in system_kw):
                continue
            return dev
        return None

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
            if remaining is None or interval < remaining:
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
    def _extract_level(line):
        try:
            m = re.search(r"(?:RMS|Peak) level dB:\s*(-?\d+\.?\d*)", line)
            if not m:
                return None
            db = float(m.group(1))
            return max(0.0, min(1.0, (db + 60.0) / 60.0))
        except Exception:
            return None

    def build_audio_test_command(self):
        audio_dev = self.audio
        if not audio_dev or audio_dev == "无音频":
            return None
        try:
            if audio_dev.startswith("WASAPI: "):
                dev = audio_dev[len("WASAPI: "):]
                return [self.ffmpeg, "-f", "wasapi", "-i", dev,
                        "-af", "astats", "-f", "null", "-"]
            if audio_dev == AUDIO_SYSTEM_MIC:
                mic = self._resolve_mic_device()
                if not mic:
                    return None
                return [self.ffmpeg, "-f", "dshow", "-i", f"audio={mic}",
                        "-af", "astats", "-f", "null", "-"]
            if audio_dev == "系统声音（立体声混音）":
                dev = self._resolve_system_audio_device()
                if not dev:
                    return None
                return [self.ffmpeg, "-f", "dshow", "-i", f"audio={dev}",
                        "-af", "astats", "-f", "null", "-"]
            return [self.ffmpeg, "-f", "dshow", "-i", f"audio={audio_dev}",
                    "-af", "astats", "-f", "null", "-"]
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
                    if line:
                        self._ffmpeg_log.append(line)
                        if len(self._ffmpeg_log) > 300:
                            self._ffmpeg_log.pop(0)
                        lv = self._extract_level(line)
                        if lv is not None:
                            self.audio_level.emit(lv)
            if self._log_buffer.strip():
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
#  区域选择（现代化半透明毛玻璃蒙版 + 锐利十字准星）
# =====================================================================
class RegionSelector(QWidget):
    region_selected = Signal(object)
    _SNAP = 8
    _MAG = 48
    _ZOOM = 2

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
        self._mag_cache = None
        self._win_rects = self._windows_rects()
        self.label = QLabel("拖拽选择录制区域 · ESC 取消", self)
        self.label.setStyleSheet(
            "color:#fff;background:rgba(37,99,235,0.92);padding:6px 14px;border-radius:8px;font-size:10pt;"
            "border:1px solid rgba(255,255,255,0.3);")
        self.label.adjustSize()
        self.label.move(24, 24)

    def _windows_rects(self):
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
                    continue
                rects.append((l, t, r, b))
        except Exception:
            pass
        return rects

    def _snap_point(self, p):
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
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        if self.active:
            r = QRect(self.start, self.end).normalized()
            p.setCompositionMode(QPainter.CompositionMode_Clear)
            p.fillRect(r, Qt.transparent)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)

            p.setPen(QPen(QColor("#38bdf8"), 2))
            p.setBrush(QColor(56, 189, 248, 25))
            p.drawRect(r)

            scale = get_dpi_scale()
            txt = f"{int(r.width() * scale)} × {int(r.height() * scale)}"
            p.setPen(QPen(QColor("#ffffff")))
            p.drawText(r.left() + 8, r.top() - 8, txt)

        mag = self._magnifier(self._mouse)
        if mag is not None:
            mw, mh = mag.width(), mag.height()
            cx, cy = self._mouse.x(), self._mouse.y()
            mx, my = cx + 20, cy + 20
            if mx + mw > self.width():
                mx = cx - mw - 20
            if my + mh > self.height():
                my = cy - mh - 20
            p.setPen(QPen(QColor("#38bdf8"), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(mx - 2, my - 2, mw + 4, mh + 4, 8, 8)
            p.drawPixmap(mx, my, mag)
            p.setPen(QPen(QColor(255, 255, 255, 180), 1))
            p.drawLine(mx + mw // 2, my, mx + mw // 2, my + mh)
            p.drawLine(mx, my + mh // 2, mx + mw, my + mh // 2)
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
#  悬浮窗（现代化深邃亚克力微胶囊）
# =====================================================================
class FloatingWidget(QWidget):
    stop_requested = Signal()
    show_main_requested = Signal()
    open_last_requested = Signal()
    hide_requested = Signal()
    mode_requested = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(340, 72)

        self.card = QWidget(self)
        self.card.setObjectName("fcard")
        self.card.setGeometry(8, 8, 324, 56)
        self.card.setStyleSheet("""
            #fcard {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(26, 30, 44, 0.95), stop:1 rgba(15, 17, 26, 0.96));
                border-radius: 18px;
                border: 1px solid rgba(255, 255, 255, 0.16);
            }
        """)

        lay = QHBoxLayout(self.card)
        lay.setContentsMargins(16, 0, 14, 0)
        lay.setSpacing(10)

        self.dot = QLabel("●")
        self.dot.setFixedWidth(14)
        self.dot.setStyleSheet("color:#f43f5e;font-size:11pt;")

        self.timer = QLabel("00:00:00")
        self.timer.setMinimumWidth(80)
        self.timer.setStyleSheet("color:#f8fafc;font:700 15px Consolas;")

        self.meter = AudioPeakMeter()
        self.meter.setFixedSize(68, 16)

        self.size = QLabel("")
        self.size.setMinimumWidth(56)
        self.size.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.size.setStyleSheet("color:#94a3b8;font:12px Consolas;")

        self.btn = QPushButton("开始")
        self.btn.setFixedSize(68, 34)
        self.btn.setStyleSheet("""
            QPushButton {
                background: #10b981; color: #fff; border: 1px solid rgba(255,255,255,0.2);
                border-radius: 10px; font-size: 13px; font-weight: 600;
            }
            QPushButton:hover { background: #059669; }
        """)
        self.btn.clicked.connect(self.stop_requested.emit)

        lay.addWidget(self.dot)
        lay.addWidget(self.timer)
        lay.addWidget(self.meter)
        lay.addWidget(self.size, 1)
        lay.addWidget(self.btn)

        self._drag = None
        self._level = 0.0
        self._compact = False

        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.setInterval(3000)
        self._idle_timer.timeout.connect(lambda: self._animate_opacity(0.65))
        self.position_topright()

    def position_topright(self):
        try:
            sg = QApplication.primaryScreen().geometry()
            self.move(sg.width() - self.width() - 24, 24)
        except Exception:
            pass

    def _animate_opacity(self, target):
        try:
            anim = QPropertyAnimation(self, b"windowOpacity", self)
            anim.setDuration(260)
            anim.setStartValue(self.windowOpacity())
            anim.setEndValue(target)
            anim.setEasingCurve(QEasingCurve.InOutQuad)
            self._opacity_anim = anim
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
        self._compact = on
        self.meter.setVisible(not on)
        self.size.setVisible(not on)
        self.btn.setVisible(not on)
        if on:
            self.resize(184, 72)
            self.card.setGeometry(8, 8, 168, 56)
        else:
            self.resize(340, 72)
            self.card.setGeometry(8, 8, 324, 56)

    def set_idle(self):
        self.timer.setText("00:00:00")
        self.size.setText("")
        self.meter.set_level(0)
        self.btn.setText("开始")
        self.btn.setEnabled(True)
        self.btn.setStyleSheet("""
            QPushButton {
                background: #10b981; color: #fff; border: 1px solid rgba(255,255,255,0.2);
                border-radius: 10px; font-size: 13px; font-weight: 600;
            }
            QPushButton:hover { background: #059669; }
        """)

    def set_recording(self):
        self.btn.setText("■ 停止")
        self.btn.setEnabled(True)
        self.btn.setStyleSheet("""
            QPushButton {
                background: #f43f5e; color: #fff; border: 1px solid rgba(255,255,255,0.2);
                border-radius: 10px; font-size: 13px; font-weight: 600;
            }
            QPushButton:hover { background: #e11d48; }
        """)

    def set_stopping(self):
        self.btn.setText("停止中…")
        self.btn.setEnabled(False)
        self.btn.setStyleSheet("""
            QPushButton {
                background: #64748b; color: #fff; border: none;
                border-radius: 10px; font-size: 13px; font-weight: 500;
            }
        """)

    def mousePressEvent(self, ev):
        self._drag = ev.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, ev):
        if self._drag is not None:
            self.move(ev.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, ev):
        self._drag = None
        self._snap_to_edge()

    def _snap_to_edge(self):
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

    def enterEvent(self, ev):
        try:
            self._idle_timer.stop()
            self._animate_opacity(0.98)
        except Exception:
            pass
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        try:
            self._idle_timer.start()
        except Exception:
            pass
        super().leaveEvent(ev)

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
#  主录制圆环（亚克力轨道 + 能量呼吸脉冲光晕）
# =====================================================================
class RecordingRing(QWidget):
    clicked = Signal()

    def __init__(self, parent=None, col=None):
        super().__init__(parent)
        self.col = col or LIGHT_THEME
        self.setFixedSize(130, 130)
        self.recording = False
        self._t = "00:00:00"
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._animate)
        self._timer.start()
        self.setCursor(Qt.PointingHandCursor)

    def _animate(self):
        if self.recording:
            self._phase = (self._phase + 0.04) % 1.0
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
        if ev.button() == Qt.LeftButton:
            self.clicked.emit()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        cx = cy = w / 2.0
        R = w / 2.0 - 18

        def ellipse_rect(radius):
            return QRectF(cx - radius, cy - radius, radius * 2, radius * 2)

        if self.recording:
            glow = 0.5 + 0.5 * math.sin(self._phase * 2 * math.pi)
            glow_rad = R + 12 + 6 * glow
            rad_grad = QRadialGradient(QPointF(cx, cy), glow_rad)
            rec_col = QColor(self.col["rec"])
            rad_grad.setColorAt(0.0, QColor(rec_col.red(), rec_col.green(), rec_col.blue(), int(90 * glow + 40)))
            rad_grad.setColorAt(0.7, QColor(rec_col.red(), rec_col.green(), rec_col.blue(), int(30 * glow)))
            rad_grad.setColorAt(1.0, Qt.transparent)
            p.setBrush(rad_grad)
            p.setPen(Qt.NoPen)
            p.drawEllipse(ellipse_rect(glow_rad))

        track_pen = QPen(QColor(self.col["line"]), 8, Qt.SolidLine, Qt.RoundCap)
        p.setPen(track_pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(ellipse_rect(R))

        if self.recording:
            ring_pen = QPen(QColor(self.col["rec"]), 8, Qt.SolidLine, Qt.RoundCap)
            p.setPen(ring_pen)
            p.drawEllipse(ellipse_rect(R))

            p.setPen(Qt.NoPen)
            p.setBrush(QColor(self.col["rec"]))
            sq_size = 18
            p.drawRoundedRect(QRectF(cx - sq_size/2, cy - sq_size/2, sq_size, sq_size), 4, 4)
        else:
            p.setPen(Qt.NoPen)
            center_rad = R - 14
            dot_grad = QRadialGradient(QPointF(cx - 3, cy - 3), center_rad)
            dot_grad.setColorAt(0.0, QColor("#fb7185"))
            dot_grad.setColorAt(0.8, QColor(self.col["rec"]))
            dot_grad.setColorAt(1.0, QColor(self.col["rec_dark"]))
            p.setBrush(dot_grad)
            p.drawEllipse(ellipse_rect(center_rad))

        p.end()


# =====================================================================
#  无边框极简标题栏
# =====================================================================
class TitleBar(QWidget):
    def __init__(self, parent, col=None):
        super().__init__(parent)
        self.parent = parent
        self.col = col or LIGHT_THEME
        self.setFixedHeight(44)
        self.setStyleSheet("background:transparent;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 0, 14, 0)
        lay.setSpacing(9)

        self.btn_close = QPushButton()
        self.btn_min = QPushButton()
        self.btn_max = QPushButton()
        for b, color in (
                (self.btn_close, "#ef4444"),
                (self.btn_min, "#f59e0b"),
                (self.btn_max, "#10b981")):
            b.setFixedSize(13, 13)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                f"QPushButton{{background:{color};border:none;border-radius:6.5px;}}"
                f"QPushButton:hover{{background:{color};border:1px solid rgba(255,255,255,0.4);}}")
        self.btn_close.clicked.connect(lambda: self.parent.hide_to_tray())
        self.btn_min.clicked.connect(lambda: self.parent.showMinimized())
        self.btn_max.clicked.connect(self._toggle_max)
        lay.addWidget(self.btn_close)
        lay.addWidget(self.btn_min)
        lay.addWidget(self.btn_max)
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
#  快捷键录制对话框
# =====================================================================
class HotkeyDialog(QDialog):
    def __init__(self, parent=None, current="", col=None):
        super().__init__(parent)
        self.col = col or LIGHT_THEME
        self.setWindowTitle("设置快捷键")
        self.setModal(True)
        self.setMinimumWidth(340)

        style = """
            QDialog { background: @card@; color: @text@; border-radius: 16px; }
            QLabel { color: @text@; }
            QPushButton {
                background: @hover_bg@; color: @text@;
                border: 1px solid @line@; border-radius: 8px; padding: 7px 20px;
            }
            QPushButton:hover { background: @pressed_bg@; }
        """
        for k, v in self.col.items():
            style = style.replace(f"@{k}@", str(v))
        self.setStyleSheet(style)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)
        tip = QLabel("请按下新的组合快捷键（支持 Ctrl / Shift / Alt / Win）\n按 Esc 退出")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        self.result_text = current or ""
        self.value_label = QLabel(self.result_text or "（未设置）")
        self.value_label.setAlignment(Qt.AlignCenter)

        val_style = """
            font-size: 16pt; font-weight: 700; padding: 12px;
            background: @input_bg@; border: 1px solid @accent@;
            border-radius: 10px; color: @accent@;
        """
        for k, v in self.col.items():
            val_style = val_style.replace(f"@{k}@", str(v))
        self.value_label.setStyleSheet(val_style)
        lay.addWidget(self.value_label)

        self._ok = QPushButton("确定保存")
        self._ok.setStyleSheet(f"background:{self.col['accent']};color:#fff;font-weight:600;")
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
#  左侧精细图标 & 导航项
# =====================================================================
class NavIcon(QLabel):
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
        pen.setWidthF(1.7)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        w, h = self.width(), self.height()
        if self.key == "range":
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(3, 4, w - 6, h - 8, 3, 3)
            pen.setWidthF(1.2)
            p.setPen(pen)
            p.drawRoundedRect(7, 8, w - 14, h - 16, 2, 2)
        elif self.key == "params":
            p.setPen(pen)
            p.setBrush(c)
            y1, y2 = 8, 15
            p.drawLine(4, y1, 16, y1)
            p.drawLine(6, y2, 18, y2)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QRectF(12, y1 - 2.5, 5, 5))
            p.drawEllipse(QRectF(5, y2 - 2.5, 5, 5))
        elif self.key == "plan":
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(3, 5, w - 6, h - 8, 3, 3)
            p.drawLine(6, 3, 6, 6)
            p.drawLine(w - 6, 3, w - 6, 6)
            p.drawLine(3, 10, w - 3, 10)
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawRect(QRectF(7, 13, 3, 3))
            p.drawRect(QRectF(12, 13, 3, 3))
        elif self.key == "pref":
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            cx, cy = w / 2, h / 2
            p.drawEllipse(QRectF(cx - 3.8, cy - 3.8, 7.6, 7.6))
            for i in range(6):
                a = math.pi / 3 * i
                x1 = cx + math.cos(a) * 5.5
                y1 = cy + math.sin(a) * 5.5
                x2 = cx + math.cos(a) * 8.5
                y2 = cy + math.sin(a) * 8.5
                p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        elif self.key == "save":
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(3, 6, w - 6, h - 9, 3, 3)
            p.drawLine(3, 10, 9, 10)
        elif self.key == "history":
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            cx, cy = w / 2, h / 2
            p.drawEllipse(QRectF(cx - 6, cy - 6, 12, 12))
            p.drawLine(QPointF(cx, cy), QPointF(cx, cy - 4.5))
            p.drawLine(QPointF(cx, cy), QPointF(cx + 3.2, cy + 1.2))
        p.end()


class NavItem(QWidget):
    clicked = Signal(int)

    def __init__(self, index, key, label, win, parent=None):
        super().__init__(parent)
        self.index = index
        self.key = key
        self.win = win
        self._selected = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(44)
        self.icon = NavIcon(key, color=win.col["sub"])
        self.icon.setStyleSheet("background:transparent;")
        self.label = QLabel(label)
        self.label.setFont(_heading_font(11, QFont.Weight.Medium))
        self.label.setStyleSheet(f"background:transparent;color:{win.col['text']};")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 0, 14, 0)
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
        col = self.win.col
        if self._selected:
            self.label.setStyleSheet(f"background:transparent;color:{col['accent']};font-weight:600;")
            self.icon.set_color(col["accent"])
        else:
            self.label.setStyleSheet(f"background:transparent;color:{col['text']};font-weight:500;")
            self.icon.set_color(col["sub"])

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect()
        col = self.win.col
        pill = r.adjusted(4, 2, -4, -2)
        if self._selected:
            p.setPen(QPen(QColor(col["selected_border"]), 1))
            p.setBrush(QColor(col["selected_bg"]))
            p.drawRoundedRect(pill, 10, 10)

            p.setPen(Qt.NoPen)
            p.setBrush(QColor(col["accent"]))
            p.drawRoundedRect(QRectF(7, (r.height() - 18) / 2.0, 3.5, 18), 1.75, 1.75)
        p.end()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.clicked.emit(self.index)


# =====================================================================
#  通用模块与辅助控件
# =====================================================================
def _msg_box(icon, title, text, buttons, col):
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
    style = """
        QMessageBox { background-color: @card@; color: @text@; font-size: 10pt; border-radius: 12px; }
        QLabel { color: @text@; background: transparent; font-size: 10pt; }
        QPushButton {
            background-color: @hover_bg@; color: @text@;
            border: 1px solid @input_border@; border-radius: 8px; font-size: 10pt;
            padding: 6px 18px; min-width: 68px;
        }
        QPushButton:hover { background-color: @pressed_bg@; border-color: @accent@; }
    """
    for k, v in c.items():
        style = style.replace(f"@{k}@", str(v))
    box.setStyleSheet(style)
    return box


def _resolve_theme_col(parent, col):
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
    c = QComboBox()
    c.addItems(items)
    if current in items:
        c.setCurrentText(current)
    c.setMinimumHeight(34)
    c.setMaxVisibleItems(10)
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
    frame = QFrame()
    frame.setObjectName("sectionCard")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(18, 16, 18, 16)
    lay.setSpacing(12)
    if title:
        lbl = QLabel(title)
        lbl.setProperty("role", "heading")
        lbl.setFont(_heading_font(13, QFont.Weight.DemiBold))
        lay.addWidget(lbl)
    return frame, lay


# =====================================================================
#  详情页
# =====================================================================
class PageRange(QWidget):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        col = win.col
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(14)
        sec, slay = _section_card("录制范围", col)

        seg_container = QWidget()
        seg_container.setObjectName("segContainer")
        seg_container.setFixedHeight(42)
        seg = QHBoxLayout(seg_container)
        seg.setContentsMargins(4, 4, 4, 4)
        seg.setSpacing(4)

        win.mode_btns = {}
        for key, label in (("full", "全屏"), ("full_all", "全屏(多屏)"),
                           ("region", "区域"), ("window", "窗口")):
            b = QPushButton(label)
            b.setObjectName("segBtn")
            b.setCheckable(True)
            b.setFixedHeight(34)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: win._set_mode(k))
            win.mode_btns[key] = b
            seg.addWidget(b, 1)
        slay.addWidget(seg_container)

        win.range_extra = QStackedWidget()
        win.range_extra.setFixedHeight(78)

        page_full = QWidget()
        ph = QHBoxLayout(page_full)
        ph.setContentsMargins(0, 8, 0, 0)
        full_hint = QLabel("✨ 全屏模式已就绪，可随时通过快捷键开始录制")
        full_hint.setProperty("role", "sub")
        ph.addWidget(full_hint)
        ph.addStretch(1)
        win.range_extra.addWidget(page_full)

        win.lbl_region = QLabel("未选择区域")
        win.lbl_region.setProperty("role", "sub")
        win.btn_region = QPushButton("选择区域")
        win.btn_region.setFixedHeight(32)
        win.btn_region.setCursor(Qt.PointingHandCursor)
        win.btn_region.clicked.connect(win._select_region)
        region_box = QWidget()
        rl = QHBoxLayout(region_box)
        rl.setContentsMargins(0, 8, 0, 0)
        rl.setSpacing(10)
        rl.addWidget(win.lbl_region, 1)
        rl.addWidget(win.btn_region)
        win.range_extra.addWidget(region_box)

        win.win_combo = _combo(["（点击刷新窗口列表）"], "（点击刷新窗口列表）")
        win.win_combo.setMinimumHeight(32)
        win.btn_win_refresh = QPushButton("刷新")
        win.btn_win_refresh.setFixedHeight(32)
        win.btn_win_refresh.setFixedWidth(58)
        win.btn_win_refresh.setCursor(Qt.PointingHandCursor)
        win.btn_win_refresh.clicked.connect(win._refresh_windows)
        win.win_combo.currentIndexChanged.connect(win._on_win_selected)
        window_box = QWidget()
        wl = QVBoxLayout(window_box)
        wl.setContentsMargins(0, 4, 0, 0)
        wl.setSpacing(8)
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        row1.addWidget(win.win_combo, 1)
        row1.addWidget(win.btn_win_refresh)
        wl.addLayout(row1)
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        lbl_method = QLabel("捕获方式")
        lbl_method.setProperty("role", "sub")
        lbl_method.setFixedWidth(56)
        win.win_method_combo = _combo(
            ["PrintWindow（兼容旧窗口）", "桌面合成（DXGI/游戏防黑屏）"],
            "PrintWindow（兼容旧窗口）", win._on_window_method_changed, col=col)
        win.win_method_combo.setMinimumHeight(32)
        row2.addWidget(lbl_method)
        row2.addWidget(win.win_method_combo, 1)
        wl.addLayout(row2)
        win.range_extra.addWidget(window_box)

        slay.addWidget(win.range_extra)
        lay.addWidget(sec)
        lay.addStretch(1)


class PageParams(QWidget):
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
        win.bitrate_combo = _combo(BITRATE_OPTIONS, "不设置（CRF 质量）", win._on_setting_changed, col=col)
        win.scale_combo = _combo(RESOLUTION_SCALE_OPTIONS, "原始（不缩放）", win._on_setting_changed, col=col)
        win.fmt_combo = _combo(list(FORMAT_OPTIONS.keys()), "MP4 (H.264, 推荐)", win._on_setting_changed, col=col)
        win.fmt_combo.setMinimumWidth(150)
        win.audio_combo = _combo(["无音频", "系统声音（立体声混音）"], "无音频", win._on_setting_changed, col=col)
        win.audio_combo.setMinimumWidth(170)

        win.btn_audio_refresh = QPushButton("刷新")
        win.btn_audio_refresh.setFixedHeight(34)
        win.btn_audio_refresh.setFixedWidth(58)
        win.btn_audio_refresh.setCursor(Qt.PointingHandCursor)
        win.btn_audio_refresh.clicked.connect(win._refresh_audio_devices)

        win.btn_audio_test = QPushButton("测试")
        win.btn_audio_test.setFixedHeight(34)
        win.btn_audio_test.setFixedWidth(58)
        win.btn_audio_test.setCursor(Qt.PointingHandCursor)
        win.btn_audio_test.setCheckable(True)
        win.btn_audio_test.setToolTip("点击测试当前录音链路")
        win.btn_audio_test.toggled.connect(win._on_audio_test_toggled)

        audio_row = QHBoxLayout()
        audio_row.setSpacing(8)
        audio_row.addWidget(win.audio_combo, 1)
        audio_row.addWidget(win.btn_audio_refresh)
        audio_row.addWidget(win.btn_audio_test)

        win.delay_combo = _combo(list(DELAY_OPTIONS.keys()), "1 秒", win._on_delay_changed, col=col)
        win.hotkey_edit = QLineEdit("F9")
        win.hotkey_edit.setReadOnly(True)
        win.hotkey_edit.setMinimumHeight(34)
        win.hotkey_edit.setMinimumWidth(110)
        win.hotkey_edit.setToolTip("点击右侧“设置”录制快捷键")
        win.btn_hotkey = QPushButton("设置")
        win.btn_hotkey.setFixedHeight(34)
        win.btn_hotkey.setFixedWidth(58)
        win.btn_hotkey.setCursor(Qt.PointingHandCursor)
        win.btn_hotkey.clicked.connect(win._set_hotkey)

        hotkey_row = QHBoxLayout()
        hotkey_row.setSpacing(6)
        hotkey_row.addWidget(win.hotkey_edit, 1)
        hotkey_row.addWidget(win.btn_hotkey)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 0)
        grid.setColumnStretch(3, 1)

        def _lb(text):
            lbl = QLabel(text)
            lbl.setProperty("role", "sub")
            lbl.setFixedWidth(56)
            return lbl

        grid.addWidget(_lb("帧率"), 0, 0)
        grid.addWidget(win.fps_combo, 0, 1)
        grid.addWidget(_lb("画质"), 0, 2)
        grid.addWidget(win.quality_combo, 0, 3)

        grid.addWidget(_lb("编码器"), 1, 0)
        grid.addWidget(win.encoder_combo, 1, 1)
        grid.addWidget(_lb("码率"), 1, 2)
        grid.addWidget(win.bitrate_combo, 1, 3)

        grid.addWidget(_lb("分辨率"), 2, 0)
        grid.addWidget(win.scale_combo, 2, 1)
        grid.addWidget(_lb("格式"), 2, 2)
        grid.addWidget(win.fmt_combo, 2, 3)

        grid.addWidget(_lb("音频"), 3, 0)
        grid.addLayout(audio_row, 3, 1, 1, 3)

        win.mic_box = QWidget()
        mic_lay = QHBoxLayout(win.mic_box)
        mic_lay.setContentsMargins(0, 0, 0, 0)
        mic_lay.setSpacing(8)
        win.mic_label = QLabel("麦克风")
        win.mic_label.setProperty("role", "sub")
        win.mic_label.setFixedWidth(56)
        win.mic_combo = _combo(["（自动选择）"], "（自动选择）", win._on_mic_changed, col=col)
        win.mic_combo.setMinimumHeight(34)
        mic_lay.addWidget(win.mic_label)
        mic_lay.addWidget(win.mic_combo, 1)
        win.mic_box.setVisible(False)
        grid.addWidget(win.mic_box, 4, 0, 1, 4)
        win.audio_combo.currentTextChanged.connect(win._update_mic_visibility)

        grid.addWidget(_lb("延迟开始"), 5, 0)
        grid.addWidget(win.delay_combo, 5, 1)
        grid.addWidget(_lb("快捷键"), 5, 2)
        grid.addLayout(hotkey_row, 5, 3)

        slay.addLayout(grid)
        lay.addWidget(sec)

        win.estimate_label = QLabel("预估（约）：—")
        win.estimate_label.setProperty("role", "sub")
        win.estimate_label.setStyleSheet("padding-left:4px;")
        lay.addWidget(win.estimate_label)
        lay.addStretch(1)


class PagePlan(QWidget):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        col = win.col
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(14)
        sec, slay = _section_card("计划 / 分段任务", col)

        win.chk_schedule_start = QCheckBox("定时开始")
        win.datetime_start = QDateTimeEdit(QDateTime.currentDateTime().addSecs(60))
        win.datetime_start.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        win.datetime_start.setCalendarPopup(True)
        win.datetime_start.setMinimumHeight(34)

        win.chk_duration_limit = QCheckBox("时长上限（分钟）")
        win.spin_duration = QSpinBox()
        win.spin_duration.setRange(1, 600)
        win.spin_duration.setValue(10)
        win.spin_duration.setSuffix(" 分钟")
        win.spin_duration.setMinimumHeight(34)

        win.chk_auto_segment = QCheckBox("自动分段（分钟）")
        win.spin_segment = QSpinBox()
        win.spin_segment.setRange(1, 600)
        win.spin_segment.setValue(10)
        win.spin_segment.setSuffix(" 分钟")
        win.spin_segment.setMinimumHeight(34)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
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
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        col = win.col
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(14)
        sec, slay = _section_card("偏好与视觉", col)

        win.chk_dark = QCheckBox("深色模式 (Cyber Acrylic)")
        win.chk_autostart = QCheckBox("开机自动启动")
        win.chk_float = QCheckBox("录制时启用悬浮控制窗")
        win.chk_auto = QCheckBox("录制完成后打开文件夹")
        win.chk_float_stay = QCheckBox("悬浮控制窗常驻桌面")
        win.chk_float_mini = QCheckBox("悬浮窗极简模式（微缩胶囊）")

        win.chk_dark.toggled.connect(win._on_dark_toggled)
        win.chk_autostart.toggled.connect(win._on_autostart_toggled)
        win.chk_auto.toggled.connect(lambda v: win._schedule_save_config())
        win.chk_float.toggled.connect(lambda v: win._schedule_save_config())
        win.chk_float_stay.toggled.connect(win._on_float_stay_toggled)
        win.chk_float_mini.toggled.connect(win._on_float_mini_toggled)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
        grid.addWidget(win.chk_dark, 0, 0)
        grid.addWidget(win.chk_autostart, 0, 1)
        grid.addWidget(win.chk_float, 1, 0)
        grid.addWidget(win.chk_auto, 1, 1)
        grid.addWidget(win.chk_float_stay, 2, 0)
        grid.addWidget(win.chk_float_mini, 2, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        slay.addLayout(grid)

        lay.addWidget(sec)
        lay.addStretch(1)


class PageSave(QWidget):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        col = win.col
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(14)
        sec, slay = _section_card("存储与路径", col)

        win.dir_edit = QLineEdit(win.core.save_dir)
        win.dir_edit.setMinimumHeight(34)
        win.dir_edit.textChanged.connect(
            lambda t: (setattr(win.core, "save_dir", t),
                       win._schedule_save_config(), win._update_summary()))
        win.btn_browse = QPushButton("浏览")
        win.btn_browse.setFixedHeight(34)
        win.btn_browse.setFixedWidth(64)
        win.btn_browse.setCursor(Qt.PointingHandCursor)
        win.btn_browse.clicked.connect(win._browse_dir)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(8)
        dir_row.addWidget(win.dir_edit, 1)
        dir_row.addWidget(win.btn_browse)
        slay.addLayout(dir_row)

        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        lbl = QLabel("默认前缀")
        lbl.setProperty("role", "sub")
        name_row.addWidget(lbl)
        win.prefix_edit = QLineEdit("录屏")
        win.prefix_edit.setMinimumHeight(34)
        win.prefix_edit.setFixedWidth(130)
        win.prefix_edit.textChanged.connect(
            lambda t: (setattr(win.core, "prefix", t), win._schedule_save_config()))
        name_row.addWidget(win.prefix_edit)
        name_row.addStretch(1)

        win.btn_ffmpeg = QPushButton("指定 ffmpeg")
        win.btn_ffmpeg.setFixedHeight(34)
        win.btn_ffmpeg.setCursor(Qt.PointingHandCursor)
        win.btn_ffmpeg.clicked.connect(win._pick_ffmpeg)
        name_row.addWidget(win.btn_ffmpeg)
        slay.addLayout(name_row)

        tmpl_row = QHBoxLayout()
        tmpl_row.setSpacing(8)
        lbl_t = QLabel("命名模板")
        lbl_t.setProperty("role", "sub")
        tmpl_row.addWidget(lbl_t)
        win.template_edit = QLineEdit("")
        win.template_edit.setMinimumHeight(34)
        win.template_edit.setPlaceholderText("{date}_{time}_{mode}_{resolution}_{fps}")
        win.template_edit.textChanged.connect(
            lambda t: (setattr(win.core, "name_template", t), win._schedule_save_config()))
        tmpl_row.addWidget(win.template_edit, 1)
        slay.addLayout(tmpl_row)

        lay.addWidget(sec)
        lay.addStretch(1)


# =====================================================================
#  历史记录
# =====================================================================
_VIDEO_EXT = (".mp4", ".mkv", ".avi", ".mov", ".gif", ".webm", ".flv", ".m4v")
_THUMB_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="thumb")


def _thumb_cache_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.dirname(os.path.abspath(sys.argv[0]))
    d = os.path.join(base, "ScreenRecorder", "thumbs")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = os.path.dirname(os.path.abspath(sys.argv[0]))
    return d


def _video_thumb_qimage(path, ffmpeg):
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


class HistoryRow(QWidget):
    thumb_ready = Signal(object)

    def __init__(self, path, page, parent=None):
        super().__init__(parent)
        self.path = path
        self.page = page
        col = page.win.col
        self.setObjectName("histRow")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._editing = False
        self.thumb_ready.connect(self._apply_thumb, Qt.QueuedConnection)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(14)

        self.thumb = QLabel()
        self.thumb.setFixedSize(88, 54)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet(f"background:{col['history_thumb_bg']};border-radius:8px;")
        self._set_fallback_thumb()
        QTimer.singleShot(0, self._load_real_thumb)
        lay.addWidget(self.thumb)

        info = QVBoxLayout()
        info.setSpacing(4)
        self.name_label = QLabel(os.path.basename(path))
        self.name_label.setStyleSheet(
            f"color:{col['text']};font-size:11pt;font-weight:600;background:transparent;")
        self.name_edit = QLineEdit(os.path.basename(path))
        self.name_edit.setStyleSheet(
            f"color:{col['text']};font-size:11pt;font-weight:600;"
            f"background:{col['input_bg']};border:1px solid {col['accent']};"
            f"border-radius:6px;padding:2px 6px;"
        )
        self.name_edit.hide()
        self.name_edit.installEventFilter(self)

        self.meta_label = QLabel(self._meta())
        self.meta_label.setStyleSheet(
            f"color:{col['sub']};font-size:9pt;background:transparent;")
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
        self.b_prev.setFixedWidth(60)
        self.b_rename.setFixedWidth(68)
        self.b_del.setFixedWidth(60)
        self.b_del.setStyleSheet(
            f"QPushButton{{background:{col['danger_bg']};color:{col['danger_text']};border:none;border-radius:8px;}}"
            f"QPushButton:hover{{background:{col['pressed_bg']};}}"
        )
        lay.addWidget(self.b_prev)
        lay.addWidget(self.b_rename)
        lay.addWidget(self.b_del)

        self.b_prev.clicked.connect(self._preview)
        self.b_rename.clicked.connect(self._start_rename)
        self.b_del.clicked.connect(self._delete)

    def _set_fallback_thumb(self):
        provider = QFileIconProvider()
        icon = provider.icon(QFileInfo(self.path))
        icon_pm = icon.pixmap(36, 36)
        if icon_pm and not icon_pm.isNull():
            self.thumb.setPixmap(icon_pm.scaled(36, 36, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _load_real_thumb(self):
        path = self.path
        ffmpeg = self.page.win.ffmpeg

        def worker():
            try:
                img = _video_thumb_qimage(path, ffmpeg)
                if img is not None and not img.isNull():
                    self.thumb_ready.emit(img)
            except Exception:
                pass

        try:
            _THUMB_EXECUTOR.submit(worker)
        except Exception:
            pass

    def _apply_thumb(self, img):
        try:
            if self.thumb is not None and not self.thumb.isHidden():
                self.thumb.setPixmap(
                    QPixmap.fromImage(img).scaled(
                        88, 54, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
        except Exception:
            pass

    def apply_theme(self, col):
        try:
            self.thumb.setStyleSheet(f"background:{col['history_thumb_bg']};border-radius:8px;")
            self.name_label.setStyleSheet(f"color:{col['text']};font-size:11pt;font-weight:600;background:transparent;")
            self.name_edit.setStyleSheet(
                f"color:{col['text']};font-size:11pt;font-weight:600;"
                f"background:{col['input_bg']};border:1px solid {col['accent']};border-radius:6px;padding:2px 6px;"
            )
            self.meta_label.setStyleSheet(f"color:{col['sub']};font-size:9pt;background:transparent;")
            self.b_del.setStyleSheet(
                f"QPushButton{{background:{col['danger_bg']};color:{col['danger_text']};border:none;border-radius:8px;}}"
                f"QPushButton:hover{{background:{col['pressed_bg']};}}"
            )
        except Exception:
            pass

    def eventFilter(self, obj, ev):
        if obj is self.name_edit:
            if ev.type() == QEvent.FocusOut:
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
                self, "删除确认",
                f"确定删除文件 {os.path.basename(self.path)}？\n（该操作不可撤销）",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                self.page.win.col
        ) == QMessageBox.StandardButton.Yes:
            try:
                os.remove(self.path)
                self.page.refresh()
            except Exception as e:
                _msg_warning(self, "删除失败", f"无法删除文件：{e}", self.page.win.col)


class PageHistory(QWidget):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        col = win.col
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(14)

        top = QHBoxLayout()
        top.setSpacing(10)
        title = QLabel("录制历史")
        title.setProperty("role", "heading")
        title.setFont(_heading_font(13, QFont.Weight.DemiBold))
        top.addWidget(title, 1)

        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setFixedHeight(32)
        self.refresh_btn.setFixedWidth(64)
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        top.addWidget(self.refresh_btn)

        self.merge_btn = QPushButton("无损拼接")
        self.merge_btn.setFixedHeight(32)
        self.merge_btn.setFixedWidth(84)
        self.merge_btn.setCursor(Qt.PointingHandCursor)
        self.merge_btn.setToolTip("通过 FFmpeg concat 无损合并多个分段文件")
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
        self.scroll.setStyleSheet(
            "QScrollArea#historyScroll{border:none;background:transparent;}"
            "QScrollArea#historyScroll > QWidget > QWidget{background:transparent;border:none;}")

        self.list_widget = QWidget()
        self.list_widget.setObjectName("historyContainer")
        self.list_widget.setStyleSheet("#historyContainer{background:transparent;}")
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
                files = [
                    os.path.join(d, f) for f in entries
                    if not f.startswith(".")
                    and not f.endswith("_raw.mkv")
                    and f.lower().endswith(_VIDEO_EXT)
                ]
                files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        except Exception as e:
            err = str(e)
        self.note.setText(f"保存目录：{d}（共 {len(files)} 个视频文件）")
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
        try:
            for i in range(self.list_lay.count()):
                item = self.list_lay.itemAt(i)
                w = item.widget() if item else None
                if isinstance(w, HistoryRow):
                    w.apply_theme(col)
        except Exception:
            pass

    def _merge_segments(self):
        try:
            d = self.win.core.save_dir
            if not os.path.isdir(d):
                QMessageBox.warning(self, "合并", "保存目录不存在")
                return
            files, _ = QFileDialog.getOpenFileNames(
                self, "选择要合并的分段视频文件（按时间顺序）", d,
                "视频文件 (*.mp4 *.mkv *.avi *.mov *.ts *.flv);;所有文件 (*)")
            if len(files) < 2:
                if files:
                    QMessageBox.information(self, "合并提示", "请至少选择 2 个文件进行合并")
                return
            base, ext = os.path.splitext(files[0])
            out_path = os.path.join(d, f"{os.path.basename(base)}_已合并{ext}")
            out_path, _ = QFileDialog.getSaveFileName(
                self, "保存合并后的视频", out_path,
                "视频文件 (*.mp4 *.mkv *.avi *.mov *.ts *.flv);;所有文件 (*)")
            if not out_path:
                return
            import tempfile
            list_fd, list_path = tempfile.mkstemp(suffix=".txt", prefix="concat_")
            try:
                with os.fdopen(list_fd, "w", encoding="utf-8") as f:
                    for fp in files:
                        safe = fp.replace("\\", "/").replace("'", "'\\''")
                        f.write(f"file '{safe}'\n")
                ffmpeg = self.win.ffmpeg
                if not ffmpeg or not os.path.exists(ffmpeg):
                    QMessageBox.warning(self, "合并失败", "未找到 ffmpeg 可执行文件")
                    return
                cmd = [
                    ffmpeg, "-y", "-f", "concat", "-safe", "0",
                    "-i", list_path, "-c", "copy", out_path
                ]
                self.note.setText(f"正在无损合并 {len(files)} 个文件...")
                QApplication.processEvents()
                result = subprocess.run(
                    cmd, capture_output=True, text=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if result.returncode == 0 and os.path.exists(out_path):
                    size_mb = os.path.getsize(out_path) / (1024 * 1024)
                    self.note.setText(f"合并完成：{os.path.basename(out_path)}（{size_mb:.1f} MB）")
                    QMessageBox.information(
                        self, "合并完成",
                        f"已合并 {len(files)} 个分段文件\n"
                        f"保存路径：{out_path}")
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
#  多段微光音频电平跳动计
# =====================================================================
class AudioPeakMeter(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(92, 18)
        self._level = 0.0
        self._decay_level = 0.0
        self._bars = 16

    def set_level(self, v):
        try:
            v = max(0.0, min(1.0, float(v)))
        except Exception:
            v = 0.0
        self._level = v
        if self._level > self._decay_level:
            self._decay_level = self._level
        else:
            self._decay_level = max(0.0, self._decay_level - 0.045)
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        gap = 2.0
        bw = max(2.0, (w - (self._bars - 1) * gap) / self._bars)

        for i in range(self._bars):
            thr = i / float(self._bars)
            bh = max(3.0, h * 0.4 + (h * 0.6) * ((i + 1) / self._bars))
            x = i * (bw + gap)
            y = h - bh

            if self._decay_level > thr:
                ratio = (i + 1) / self._bars
                if ratio <= 0.60:
                    color = QColor("#10b981")
                elif ratio <= 0.85:
                    color = QColor("#f59e0b")
                else:
                    color = QColor("#f43f5e")
            else:
                color = QColor(140, 150, 170, 30)

            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(QRectF(x, y, bw, bh), 1.5, 1.5)
        p.end()


# =====================================================================
#  亚克力卡片主容器
# =====================================================================
class AcrylicCardWidget(QWidget):
    def __init__(self, parent=None, col=None):
        super().__init__(parent)
        self.col = col or LIGHT_THEME
        self.setAttribute(Qt.WA_StyledBackground, True)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        c = self.col

        # 1. 纯净底色填充（彻底阻断 DWM 黑底渗透）
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(c["card"]))
        p.drawRoundedRect(r, 18, 18)

        # 2. 仅在深色模式下绘制氛围微光，浅色模式保持纯白通透
        if c.get("bg") == "#0c0e14":
            glow = QRadialGradient(QPointF(r.left() + 120, r.top() + 60), 300)
            glow.setColorAt(0.0, QColor(c.get("glow_primary", "rgba(79,70,229,0.12)")))
            glow.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.setBrush(glow)
            p.drawRoundedRect(r, 18, 18)

        # 3. 极细精致边框
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(c["line"]), 1.0))
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 18, 18)
        p.end()


# =====================================================================
#  主界面
# =====================================================================
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

        self._hotkey_emitter = HotkeyEmitter()
        self._hotkey_emitter.triggered.connect(self._toggle_record, Qt.QueuedConnection)

        self._audio_test_proc = None
        self._audio_test_thread = None
        self._audio_test_active = False

        self._card_geo = None
        self._titlebar_ytop = None
        self._titlebar_ybot = None

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

    def _setup_window(self):
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowSystemMenuHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowTitle("轻量录屏 · ScreenRecorder")
        self.setWindowIcon(self._app_icon())
        self.resize(980, 650)
        self.setMinimumSize(840, 580)
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

    def _set_mouse_tracking_recursive(self, widget):
        for child in widget.findChildren(QWidget):
            child.setMouseTracking(True)

    def _install_event_filter_recursive(self, widget):
        for child in widget.findChildren(QWidget):
            child.installEventFilter(self)

    def _hook_new_child(self, ev):
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
        if getattr(self, "_last_cursor", None) != cur:
            self._last_cursor = cur
            self.setCursor(cur)

    def _hit_test(self, pos):
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
        try:
            h = self.layout().minimumSize().height()
            if h > self.minimumHeight():
                self.setMinimumHeight(h)
        except Exception:
            pass

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
                "window_capture_method": getattr(self.core, "window_capture_method", "printwindow"),
            }
            path = self._config_path()
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception:
            pass

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)

        self.card = AcrylicCardWidget(self, col=self.col)
        self.card.setObjectName("card")

        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(42)
        shadow.setColor(QColor(0, 0, 0, 85))
        shadow.setOffset(0, 14)
        self.card.setGraphicsEffect(shadow)
        self._shadow = shadow
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

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self._build_sidebar(body)
        self._build_detail(body)
        card_lay.addLayout(body, 1)

        rec_card = QWidget()
        rec_card.setObjectName("reccard")
        rec_lay = QVBoxLayout(rec_card)
        rec_lay.setContentsMargins(0, 0, 0, 0)
        rec_lay.setSpacing(0)

        ring_area = QWidget()
        ra_lay = QHBoxLayout(ring_area)
        ra_lay.setContentsMargins(28, 10, 28, 10)
        ra_lay.setSpacing(24)

        self.ring = RecordingRing(col=self.col)
        self.ring.clicked.connect(self._toggle_record)
        ra_lay.addWidget(self.ring, alignment=Qt.AlignVCenter)

        right = QVBoxLayout()
        right.setSpacing(6)
        self.ring_caption = QLabel("开始录制")
        self.ring_caption.setProperty("role", "text")
        self.ring_caption.setFont(_heading_font(16, QFont.Weight.Bold))

        self.tip_label = QLabel("F9 启动 / 停止 · 录制时自动最小化到托盘")
        self.tip_label.setProperty("role", "sub")
        right.addWidget(self.ring_caption)
        right.addWidget(self.tip_label)

        act_row = QHBoxLayout()
        act_row.setSpacing(8)
        self.btn_preview = QPushButton("预览上一条")
        self.btn_openfolder = QPushButton("打开文件夹")
        self.btn_log = QPushButton("日志")
        for b in (self.btn_preview, self.btn_openfolder, self.btn_log):
            b.setFixedHeight(32)
            b.setCursor(Qt.PointingHandCursor)
        self.btn_preview.clicked.connect(lambda: self.core._open_file(self._last_out))
        self.btn_openfolder.clicked.connect(lambda: self.core._open_folder(self._last_out or self.core.save_dir))
        self.btn_log.clicked.connect(self._open_log_file)
        self._set_actions_enabled(False)
        act_row.addWidget(self.btn_preview)
        act_row.addWidget(self.btn_openfolder)
        act_row.addWidget(self.btn_log)
        act_row.addStretch(1)
        right.addLayout(act_row)

        self.summary_label = QLabel("全屏 · 30 fps · 高清(CRF 23) · 无音频 · MP4 · 保存到 …")
        self.summary_label.setProperty("role", "sub")
        right.addWidget(self.summary_label)

        ra_lay.addLayout(right, 1)
        rec_lay.addWidget(ring_area)

        status = QFrame()
        status.setFrameShape(QFrame.NoFrame)
        status.setStyleSheet("background:transparent;")
        slay = QHBoxLayout(status)
        slay.setContentsMargins(24, 6, 24, 12)

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
        self.audio_meter.setToolTip("实时音频电平")

        slay.addWidget(self.status_label)
        slay.addStretch(1)
        slay.addWidget(self.audio_meter)
        slay.addSpacing(10)
        slay.addWidget(self.size_label)
        slay.addSpacing(10)
        slay.addWidget(self.timer_label)

        rec_lay.addWidget(status)
        card_lay.addWidget(rec_card)
        self.rec_card = rec_card

        QApplication.instance().setStyleSheet(self._global_qss())
        QApplication.instance().setPalette(self._app_palette())

        self._resize_margin = 8
        self.card.setMouseTracking(True)
        self._set_mouse_tracking_recursive(self.card)
        self.installEventFilter(self)
        self.card.installEventFilter(self)
        self._install_event_filter_recursive(self.card)

        self._select(0)
        self._lock_min_height()

    def _build_sidebar(self, body):
        side = QVBoxLayout()
        side.setContentsMargins(14, 14, 14, 14)
        side.setSpacing(6)

        brand = QHBoxLayout()
        brand.setContentsMargins(4, 0, 4, 0)
        brand.setSpacing(10)
        brand.setAlignment(Qt.AlignVCenter)

        ICON_SZ = 42
        icon_lbl = QLabel()
        icon_lbl.setFixedSize(ICON_SZ, ICON_SZ)
        icon_lbl.setStyleSheet("background:transparent;")
        pm = self._app_icon().pixmap(ICON_SZ, ICON_SZ)
        if pm and not pm.isNull():
            icon_lbl.setPixmap(pm.scaled(ICON_SZ, ICON_SZ, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        brand.addWidget(icon_lbl)

        name_box = QWidget()
        name_box.setStyleSheet("background:transparent;")
        name_lay = QVBoxLayout(name_box)
        name_lay.setContentsMargins(0, 0, 0, 0)
        name_lay.setSpacing(1)
        cn = QLabel("轻量录屏")
        cn.setProperty("role", "heading")
        cn.setFont(_heading_font(13, QFont.Weight.Bold))
        en = QLabel("ScreenRecorder")
        en.setObjectName("brandEnLbl")
        en.setFont(_heading_font(9, QFont.Weight.Medium))
        self.brand_en_lbl = en
        name_lay.addWidget(cn)
        name_lay.addWidget(en)
        brand.addWidget(name_box, 1)
        side.addLayout(brand)
        side.addSpacing(10)

        sep = QFrame()
        sep.setObjectName("sideSep")
        sep.setFrameShape(QFrame.HLine)
        sep.setFixedHeight(1)
        side.addWidget(sep)
        side.addSpacing(8)

        self.nav_items = []
        for i, (key, label) in enumerate(
                (("range", "录制范围"), ("params", "录制参数"), ("plan", "计划分段"),
                 ("pref", "偏好设置"), ("save", "存储路径"), ("history", "录制历史"))):
            item = NavItem(i, key, label, win=self)
            item.clicked.connect(self._select)
            self.nav_items.append(item)
            side.addWidget(item)
        side.addStretch(1)

        side_wrap = QWidget()
        side_wrap.setObjectName("sideWrap")
        side_wrap.setLayout(side)
        side_wrap.setFixedWidth(210)
        self.side_wrap = side_wrap
        body.addWidget(side_wrap)

    def _build_detail(self, body):
        self.stack = QStackedWidget()
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
        try:
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
            anim.setDuration(140)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Linear)

            def _done():
                try:
                    if w.graphicsEffect() is effect:
                        w.setGraphicsEffect(None)
                except Exception:
                    pass

            anim.finished.connect(_done)
            self._page_anim = anim
            anim.start()
        except Exception:
            self.stack.setCurrentIndex(index)

    def _on_audio_level(self, v):
        try:
            if hasattr(self, "audio_meter"):
                self.audio_meter.set_level(v)
            if self.float_win:
                self.float_win.set_level(v)
        except Exception:
            pass

    def _app_palette(self):
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
        template = """
        QWidget {
            color: __text__;
            font-size: 10pt;
        }
        #card {
            background: __card__;
            border-radius: 18px;
        }
        #sideWrap {
            background: __bg__;
            border-top-left-radius: 18px;
            border-bottom-left-radius: 18px;
            border-right: 1px solid __line__;
        }
        #reccard {
            background: __section_bg__;
            border-top: 1px solid __line__;
            border-bottom-left-radius: 18px;
            border-bottom-right-radius: 18px;
        }
        #sectionCard {
            background: __section_bg__;
            border: 1px solid __section_border__;
            border-radius: 14px;
        }
        #cardSep {
            color: __line__;
            background: __line__;
            max-height: 1px;
        }
        #sideSep {
            color: __line__;
            background: __line__;
        }
        #brandEnLbl {
            color: __sub__;
            background: transparent;
        }
        QLabel[role="heading"] {
            color: __text__;
            font-weight: 600;
        }
        QLabel[role="sub"] {
            color: __sub__;
            font-size: 9.5pt;
        }
        QLabel[role="text"] {
            color: __text__;
            font-size: 10pt;
        }
        #sizeLabel {
            color: __sub__;
            font: 12px Consolas;
        }
        #timerLabel {
            color: __text__;
            font: 15px Consolas;
            font-weight: 700;
        }
        /* 胶囊分段栏 */
        #segContainer {
            background: __input_bg__;
            border: 1px solid __input_border__;
            border-radius: 10px;
        }
        QPushButton#segBtn {
            background: transparent;
            color: __text__;
            border: none;
            border-radius: 7px;
            font-size: 10pt;
            font-weight: 500;
        }
        QPushButton#segBtn:checked {
            background: __accent__;
            color: #ffffff;
            font-weight: 600;
        }
        QPushButton#segBtn:hover:!checked {
            background: __hover_bg__;
        }
        /* 历史列表 */
        QScrollArea#historyScroll {
            border: none;
            background: transparent;
        }
        QScrollArea#historyScroll > QWidget > QWidget {
            background: transparent;
            border: none;
        }
        #historyContainer {
            background: transparent;
        }
        #histRow {
            background: __history_row_bg__;
            border: 1px solid __history_row_border__;
            border-radius: 12px;
        }
        #histRow:hover {
            border-color: __hover_border__;
            background: __hover_bg__;
        }
        /* 下拉与表单控件 */
        QComboBox {
            border: 1px solid __input_border__;
            border-radius: 8px;
            padding: 4px 10px;
            background: __input_bg__;
            color: __text__;
            selection-color: __text__;
            min-height: 24px;
        }
        QComboBox:hover {
            border-color: __hover_border__;
        }
        QComboBox:focus {
            border-color: __accent__;
        }
        QComboBox::drop-down {
            border: none;
            width: 24px;
        }
        QComboBox QAbstractItemView {
            border: 1px solid __menu_border__;
            border-radius: 8px;
            background: __menu_bg__;
            color: __text__;
            selection-background-color: __selected_bg__;
            selection-color: __text__;
            padding: 4px;
            outline: 0px;
        }
        QLineEdit {
            border: 1px solid __input_border__;
            border-radius: 8px;
            padding: 5px 10px;
            background: __input_bg__;
            color: __text__;
        }
        QLineEdit:hover {
            border-color: __hover_border__;
        }
        QLineEdit:focus {
            border-color: __accent__;
        }
        QDateTimeEdit, QSpinBox {
            border: 1px solid __input_border__;
            border-radius: 8px;
            padding: 4px 10px;
            background: __input_bg__;
            color: __text__;
            min-height: 24px;
        }
        QDateTimeEdit:hover, QSpinBox:hover {
            border-color: __hover_border__;
        }
        QDateTimeEdit:focus, QSpinBox:focus {
            border-color: __accent__;
        }
        QDateTimeEdit::drop-down {
            border: none;
            width: 24px;
        }
        QSpinBox::up-button, QSpinBox::down-button {
            border: none;
            background: transparent;
            width: 18px;
        }
        /* 按钮立体清晰 */
        QPushButton {
            background: __input_bg__;
            color: __text__;
            border: 1px solid __input_border__;
            border-radius: 8px;
            font-size: 10pt;
            font-weight: 500;
            padding: 0 14px;
        }
        QPushButton:hover {
            background: __hover_bg__;
            border-color: __hover_border__;
        }
        QPushButton:pressed {
            background: __pressed_bg__;
        }
        QPushButton:disabled {
            color: __disabled_text__;
            background: transparent;
            border-color: __line__;
        }
        QCheckBox {
            color: __text__;
            font-size: 10pt;
            spacing: 8px;
        }
        QCheckBox::indicator {
            width: 17px;
            height: 17px;
            border-radius: 5px;
            border: 1px solid __checkbox_border__;
            background: __input_bg__;
        }
        QCheckBox::indicator:hover {
            border-color: __accent__;
        }
        QCheckBox::indicator:checked {
            background: __accent__;
            border: 1px solid __accent__;
        }
        QMenu {
            background: __menu_bg__;
            border: 1px solid __menu_border__;
            border-radius: 10px;
            padding: 6px;
        }
        QMenu::item {
            padding: 7px 24px;
            border-radius: 6px;
            color: __text__;
            font-size: 10pt;
        }
        QMenu::item:selected {
            background: __selected_bg__;
            color: __accent__;
        }
        QMenu::separator {
            height: 1px;
            background: __line__;
            margin: 4px 8px;
        }
        QToolTip {
            background: __menu_bg__;
            color: __text__;
            border: 1px solid __menu_border__;
            border-radius: 6px;
            padding: 4px 8px;
        }
        QScrollBar:vertical {
            background: transparent;
            width: 6px;
            margin: 2px;
        }
        QScrollBar::handle:vertical {
            background: __scroll_handle__;
            border-radius: 3px;
            min-height: 30px;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0;
        }
        """
        for k, v in c.items():
            template = template.replace(f"__{k}__", str(v))
        return template

    def _apply_theme(self):
        self.col = DARK_THEME if self.dark else LIGHT_THEME
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

        QApplication.instance().setStyleSheet(self._global_qss())
        QApplication.instance().setPalette(self._app_palette())

        if hasattr(self, "card"):
            self.card.col = self.col
            self.card.update()
        if self.title_bar:
            self.title_bar.apply_theme(self.col)
        if self.ring:
            self.ring.col = self.col
            self.ring.update()
        for it in getattr(self, "nav_items", []):
            it._apply()
            it.update()
        if hasattr(self, "page_history"):
            self.page_history.apply_theme(self.col)
        self._set_mode(self.core.mode)
        self._refresh_status_color()

    def _refresh_status_color(self):
        t = self.status_label.text() if hasattr(self, "status_label") else ""
        if "未找到" in t or "失败" in t or "异常" in t:
            color = self.col["rec"]
        elif "已完成" in t or "ffmpeg 已就绪" in t:
            color = self.col["ok"]
        elif "录制中" in t or "即将开始" in t:
            color = self.col["rec"]
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
            _msg_warning(self, "开机自启", "修改注册表自启项失败，请检查权限。", self.col)
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

    def _set_hotkey(self):
        dlg = HotkeyDialog(self, self.hotkey_edit.text(), col=self.col)
        if dlg.exec() == QDialog.Accepted:
            self.hotkey_edit.setText(dlg.result_text)
            self._on_hotkey_changed()

    def _set_mode(self, mode):
        self.core.mode = mode
        for k, b in self.mode_btns.items():
            b.setChecked(k == mode)
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
                    token = scale_text.split("（", 1)[0].strip()
                    self.core.scale = float(token[:-1]) if token.endswith("x") else 1.0
            except Exception:
                self.core.scale = 1.0
        if hasattr(self, "mic_combo"):
            mic_text = self.mic_combo.currentText()
            self.core.mic = "" if mic_text == "（自动选择）" else mic_text
        self.core.update_estimate()
        self._update_summary()

    def _update_summary(self):
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
                f"{self.hotkey_edit.text()} 启动 / 停止 · 录制时自动最小化到托盘")
        self._schedule_save_config()
        self._setup_hotkey()

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
        self.hide_to_tray()

    def _on_auto_restart_requested(self):
        self._write_log_file("[自动分段] 续录下一段")
        self.core.start_record(continuing=True)

    def _on_finished(self, path):
        self._last_out = path
        self.ring.set_recording(False)
        self.ring.set_time("00:00:00")
        self.ring_caption.setText("开始录制")
        self._set_controls_enabled(True)
        self.timer_label.setText("00:00:00")
        self.size_label.setText("")
        self._set_actions_enabled(True)
        if hasattr(self, "audio_meter"):
            self.audio_meter.set_level(0)
        if self.float_win:
            self.float_win.set_level(0)
        if self.chk_float_stay.isChecked():
            self._show_float()
            if self.float_win:
                self.float_win.set_idle()
        else:
            self._hide_float()
            self.showNormal()
            self.raise_()
        self.tray.showMessage("录制已就绪", os.path.basename(path), QSystemTrayIcon.Information, 2000)
        if hasattr(self, "page_history"):
            self.page_history.refresh()

    def _on_error(self, msg):
        self.ring.set_recording(False)
        self.ring_caption.setText("开始录制")
        self.timer_label.setText("00:00:00")
        self.size_label.setText("")
        self._set_controls_enabled(True)
        self._set_actions_enabled(True)
        if hasattr(self, "audio_meter"):
            self.audio_meter.set_level(0)
        if self.float_win:
            self.float_win.set_level(0)
        self._audio_test_active = False
        if self.chk_float_stay.isChecked():
            self._show_float()
            if self.float_win:
                self.float_win.set_idle()
        else:
            self._hide_float()
        self.showNormal()
        self.raise_()
        if not isinstance(msg, str) or not msg.strip():
            msg = "发生未知错误，详情请查看日志。"
        self._write_log_file(f"[错误] {msg}")
        _msg_warning(self, "录制提示", msg, self.col)

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

    def _toggle_record(self):
        if self.core.recording or self.core._finalizing:
            if self.float_win:
                self.float_win.set_stopping()
            self.core.stop_record()
        else:
            self.core.start_record()

    def _ui_tick(self):
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
        try:
            save_dir = self.core.save_dir
            if save_dir and os.path.isdir(save_dir):
                import shutil as _shutil
                usage = _shutil.disk_usage(save_dir)
                free_mb = usage.free / (1024 * 1024)
                if free_mb < 500:
                    self._write_log_file(f"[磁盘保护] 保存分区剩余仅 {free_mb:.0f}MB，自动停止")
                    self.core.stop_record()
                    QMessageBox.warning(
                        self, "磁盘空间不足",
                        f"保存分区剩余空间不足 500 MB，已安全停止录制以保护文件。")
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
        d = QFileDialog.getExistingDirectory(self, "选择录屏保存目录", self.dir_edit.text())
        if d:
            self.dir_edit.setText(d)
            self.core.save_dir = d
            self._schedule_save_config()

    def _pick_ffmpeg(self):
        p = QFileDialog.getOpenFileName(self, "指定 ffmpeg.exe 路径", "", "ffmpeg (ffmpeg.exe)")[0]
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
            self.lbl_region.setText(f"已选区域：{w} × {h}")
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
        if hasattr(self, "audio_meter"):
            self.audio_meter.set_level(0)
        if self.float_win:
            self.float_win.set_level(0)

    def _start_audio_test(self):
        cmd = self.core.build_audio_test_command()
        if not cmd or not self.ffmpeg or not os.path.exists(self.ffmpeg):
            _msg_warning(self, "录音测试", "当前音频模式无法测试（未配置音频源或设备不可用）。", self.col)
            self.btn_audio_test.blockSignals(True)
            self.btn_audio_test.setChecked(False)
            self.btn_audio_test.blockSignals(False)
            self.btn_audio_test.setText("测试")
            return
        try:
            self._audio_test_proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW)
        except Exception as e:
            _msg_warning(self, "录音测试", f"启动测试失败：{e}", self.col)
            self.btn_audio_test.blockSignals(True)
            self.btn_audio_test.setChecked(False)
            self.btn_audio_test.blockSignals(False)
            self.btn_audio_test.setText("测试")
            return
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
                    lv = RecorderCore._extract_level(line)
                    if lv is not None:
                        self.core.audio_level.emit(lv)
            if self._audio_test_active:
                QTimer.singleShot(0, self._on_test_proc_exited)

        self._audio_test_thread = threading.Thread(target=_monitor, daemon=True)
        self._audio_test_thread.start()

    def _on_test_proc_exited(self):
        if not self._audio_test_active:
            return
        try:
            self.btn_audio_test.blockSignals(True)
            self.btn_audio_test.setChecked(False)
            self.btn_audio_test.blockSignals(False)
            self.btn_audio_test.setText("测试")
        except Exception:
            pass
        self._audio_test_active = False
        if hasattr(self, "audio_meter"):
            self.audio_meter.set_level(0)
        if self.float_win:
            self.float_win.set_level(0)

    def _on_audio_test_toggled(self, checked):
        if checked:
            self._start_audio_test()
            if self._audio_test_active:
                self.btn_audio_test.setText("停止")
        else:
            self._stop_audio_test()
            self.btn_audio_test.setText("测试")

    def _show_float(self):
        if self.float_win is None:
            self.float_win = FloatingWidget()
            self.float_win.stop_requested.connect(self._toggle_record)
            self.float_win.show_main_requested.connect(
                lambda: (self.showNormal(), self.raise_(), self.activateWindow()))
            self.float_win.open_last_requested.connect(self._open_last_recording)
            self.float_win.hide_requested.connect(self._hide_float)
            self.float_win.mode_requested.connect(self._set_mode)
            if hasattr(self, "chk_float_mini") and self.chk_float_mini.isChecked():
                self.float_win.set_compact(True)
        self.float_win.position_topright()
        self.float_win.show()

    def _open_last_recording(self):
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

    def _setup_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self._app_icon())
        self.tray.setToolTip("轻量录屏 · ScreenRecorder")
        menu = QMenu()
        self.tray_menu = menu
        a_show = QAction("显示主窗口", self)
        a_show.triggered.connect(self.showNormal)
        a_start = QAction("开始录制", self)
        a_start.triggered.connect(lambda: self.core.start_record())
        a_stop = QAction("停止录制", self)
        a_stop.triggered.connect(lambda: self.core.stop_record())
        a_open = QAction("打开保存文件夹", self)
        a_open.triggered.connect(lambda: self.core._open_folder(self._last_out or self.core.save_dir))
        a_quit = QAction("退出程序", self)
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
        pm = QPixmap(36, 36)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        grad = QRadialGradient(QPointF(18, 18), 16)
        grad.setColorAt(0.0, QColor("#fb7185"))
        grad.setColorAt(1.0, QColor("#e11d48"))
        p.setBrush(grad)
        p.setPen(Qt.NoPen)
        p.drawEllipse(2, 2, 32, 32)
        p.end()
        return QIcon(pm)

    def _quit(self):
        if self.core.recording:
            try:
                self.core.stop_record()
            except Exception:
                pass
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
        ev.ignore()
        self.hide_to_tray()

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
        self.hotkey = GlobalHotkey(vk=vk, modifiers=mods, callback=self._hotkey_emitter.triggered.emit)
        self.hotkey.start()

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

    def _init_settings(self):
        cfg = self._load_config()
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
                f"{self.hotkey_edit.text()} 启动 / 停止 · 录制时自动最小化到托盘")
        self.dir_edit.setText(cfg["save_dir"])
        self.prefix_edit.setText(cfg["prefix"])
        if hasattr(self, "template_edit"):
            self.template_edit.setText(cfg.get("name_template", ""))
            self.core.name_template = cfg.get("name_template", "")
        self.chk_float.setChecked(cfg["show_float"])
        self.chk_auto.setChecked(cfg["auto_open"])
        self.chk_dark.setChecked(cfg["dark"])

        actual_autostart = self._is_autostart_enabled()
        self.chk_autostart.blockSignals(True)
        self.chk_autostart.setChecked(actual_autostart)
        self.chk_autostart.blockSignals(False)

        self.chk_float_stay.setChecked(cfg["float_stay"])
        self.chk_float_mini.setChecked(cfg.get("float_mini", False))

        method_text = ("桌面合成（DXGI/游戏防黑屏）" if cfg["window_capture_method"] == "desktop"
                       else "PrintWindow（兼容旧窗口）")
        self.win_method_combo.setCurrentText(method_text)
        self.core.window_capture_method = cfg["window_capture_method"]

        self.core.fps = cfg["fps"]
        self.core.quality = cfg["quality"]
        self.core.fmt = cfg["fmt"]
        self.core.audio = cfg["audio"]
        self.core.delay = cfg["delay"]
        self.core.save_dir = cfg["save_dir"]
        self.core.prefix = cfg["prefix"]
        self.core.auto_open = cfg["auto_open"]
        self._set_mode(cfg["mode"])
        self.core.audio = cfg["audio"]
        self.core.update_estimate()

        if self.ffmpeg and os.path.exists(self.ffmpeg):
            self.core.refresh_audio_devices()
        else:
            self.status_label.setText("ffmpeg 未找到（录制不可用）")
            self.status_label.setStyleSheet(f"color:{self.col['rec']};font-size:10pt;")

        if self.chk_float_stay.isChecked():
            self._show_float()
        self._select(0)
        if hasattr(self, "page_history"):
            self.page_history.refresh()


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
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    if hasattr(Qt, "AA_SubpixelAntialiasing"):
        QApplication.setAttribute(Qt.AA_SubpixelAntialiasing, True)
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(sys.argv)
    app.setApplicationName("ScreenRecorder Qt")
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
        win.hide_to_tray()
    else:
        win.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()