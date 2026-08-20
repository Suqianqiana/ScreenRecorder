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
import threading
import subprocess
import ctypes
import ctypes.wintypes as wt

from PySide6.QtCore import (
    QObject, Signal, QTimer, Qt, QRect, QRectF, QPoint, QSize, QEvent, QFile, QIODevice,
    QDateTime,
)
from PySide6.QtGui import (
    QIcon, QFont, QColor, QPainter, QPen, QBrush, QAction, QCursor, QPixmap,
    QPainterPath, QScreen, QKeySequence, QShortcut, QTextCharFormat, QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QComboBox, QPushButton, QLineEdit, QCheckBox,
    QFrame, QVBoxLayout, QHBoxLayout, QGridLayout, QSizePolicy, QSystemTrayIcon, QMenu,
    QFileDialog, QMessageBox, QGraphicsDropShadowEffect, QScrollArea, QLayout,
    QStackedWidget, QAbstractItemView, QDialog, QDateTimeEdit, QSpinBox,
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
    "hover_bg": "#e5e5ea",
    "pressed_bg": "#d9d9de",
    "selected_bg": "#e8f1ff",
    "disabled_text": "#b0b0b5",
    "menu_bg": "#f9f9fb",
    "menu_border": "#d8d8dd",
    "scroll_handle": "#d1d1d6",
    "checkbox_border": "#c7c7cc",
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
    "pressed_bg": "#464650",
    "selected_bg": "#1e3a5f",
    "disabled_text": "#6e6e78",
    "menu_bg": "#2a2a32",
    "menu_border": "#3a3a44",
    "scroll_handle": "#55555f",
    "checkbox_border": "#55555f",
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
        self.mic = ""
        self.encoder = "默认（自动）"
        self.bitrate = ""
        self.scale = 1.0
        self.max_duration_seconds = 0
        self.segment_seconds = 0
        self.scheduled_start = None
        self.save_dir = self._default_save_dir()
        self.prefix = "录屏"
        self.delay = "1 秒"
        self.auto_open = False

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
        try:
            fps = int(self.fps)
        except Exception:
            fps = 30
        if ext == "gif":
            fps_gif = min(fps, 25)
            return int(w * h * fps_gif * 0.4 * 60), True
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
            cmd += ["-f", "wasapi", "-i", was_dev]
            cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac"]
        elif audio_dev == AUDIO_SYSTEM_MIC:
            sys_dev = self._resolve_system_audio_device()
            if not sys_dev:
                return None, ("未检测到“立体声混音”设备，无法录制系统声音。"
                              "请开启声卡的立体声混音，或改用“无音频”。")
            mic_dev = self._resolve_mic_device()
            if not mic_dev:
                return None, "未检测到可用麦克风，无法使用“系统声音 + 麦克风混音”。"
            cmd += ["-f", "dshow", "-i", f"audio={sys_dev}"]
            cmd += ["-f", "dshow", "-i", f"audio={mic_dev}"]
            cmd += ["-filter_complex",
                    "[1:a:0][2:a:0]amix=inputs=2:duration=first:dropout_transition=3[aout]"]
            cmd += ["-map", "0:v:0", "-map", "[aout]", "-c:a", "aac"]
        else:
            real_audio = audio_dev
            if audio_dev == "系统声音（立体声混音）":
                real_audio = self._resolve_system_audio_device()
                if not real_audio:
                    return None, ("未检测到“立体声混音”设备，无法直接录制系统声音。"
                                  "请开启声卡的立体声混音，或改用“无音频”。")
            cmd += ["-f", "dshow", "-i", f"audio={real_audio}"]
            cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac"]

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
        out_path = os.path.join(save_dir, f"{prefix}_{ts}.{ext}")
        crf = QUALITY_PRESETS.get(self.quality, QUALITY_PRESETS[DEFAULT_QUALITY])["crf"]
        force_mkv = (crf <= 0) and ext != "mkv"
        if ext not in ("mkv", "gif") and not force_mkv:
            raw_path = os.path.join(save_dir, f".{prefix}_{ts}_raw.mkv")
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
        self.label = QLabel("拖拽选择录制区域 · ESC 取消", self)
        self.label.setStyleSheet(
            "color:#fff;background:rgba(10,132,255,0.85);padding:6px 12px;border-radius:8px;font-size:10pt;")
        self.label.adjustSize()
        self.label.move(20, 20)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 90))
        if self.active:
            r = QRect(self.start, self.end).normalized()
            p.setPen(QPen(QColor(C_ACCENT), 2))
            p.setBrush(QColor(10, 132, 255, 40))
            p.drawRect(r)
        p.end()

    def mousePressEvent(self, ev):
        self.start = ev.globalPosition().toPoint()
        self.end = self.start
        self.active = True

    def mouseMoveEvent(self, ev):
        if self.active:
            self.end = ev.globalPosition().toPoint()
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

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(280, 68)
        card = QWidget(self)
        card.setObjectName("fcard")
        card.setGeometry(8, 8, 264, 52)
        card.setStyleSheet("#fcard{background:rgba(28,28,30,0.94);border-radius:16px;}")
        lay = QHBoxLayout(card)
        lay.setContentsMargins(16, 0, 14, 0)
        lay.setSpacing(10)
        self.dot = QLabel("●")
        self.dot.setFixedWidth(16)
        self.dot.setStyleSheet("color:#ff453a;font-size:10pt;")
        self.timer = QLabel("00:00:00")
        self.timer.setMinimumWidth(86)
        self.timer.setStyleSheet("color:#fff;font:600 15px Consolas;")
        self.size = QLabel("")
        self.size.setMinimumWidth(70)
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
        lay.addWidget(self.size, 1)
        lay.addWidget(self.btn)
        self._drag = None
        self.position_topright()

    def position_topright(self):
        try:
            sg = QApplication.primaryScreen().geometry()
            self.move(sg.width() - self.width() - 20, 20)
        except Exception:
            pass

    def set_timer(self, t):
        self.timer.setText(t)

    def set_size(self, s):
        self.size.setText(s)

    def set_idle(self):
        self.timer.setText("00:00:00")
        self.size.setText("")
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

    def mousePressEvent(self, ev):
        self._drag = ev.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, ev):
        if self._drag is not None:
            self.move(ev.globalPosition().toPoint() - self._drag)


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
        lay.addSpacing(10)

        title = QLabel("轻量录屏 · ScreenRecorder")
        title.setStyleSheet(f"color:{self.col['text']};")
        title.setFont(_heading_font(16, QFont.Weight.Medium))
        lay.addWidget(title)
        lay.addStretch(1)
        self._drag = None

    def apply_theme(self, col):
        self.col = col
        for w in self.findChildren(QLabel):
            if w.text() == "轻量录屏 · ScreenRecorder":
                w.setStyleSheet(f"color:{col['text']};")

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
#  无边框窗口缩放热区（轻量子控件，避免全应用事件过滤导致拖动/滚动卡顿）
# =====================================================================
class _ResizeHandle(QWidget):
    def __init__(self, parent, edges, cursor):
        super().__init__(parent)
        self._edges = edges
        self.setCursor(cursor)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            win = self.window()
            if win and win.windowHandle():
                win.windowHandle().startSystemResize(self._edges)
                ev.accept()
                return
        super().mousePressEvent(ev)


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
        self._resize_margin = 20   # 覆盖 18px 透明边距 + 卡片边缘
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
        self.resize(940, 760)
        self.setMinimumSize(860, 680)

    def _center(self):
        try:
            sg = QApplication.primaryScreen().availableGeometry()
            self.move((sg.width() - self.width()) // 2, (sg.height() - self.height()) // 2)
        except Exception:
            pass

    # ---------------- 轻量缩放热区（原生 startSystemResize，不再全控件挂过滤器） ----------------
    def _create_resize_handles(self):
        self._resize_handles = []
        self._layout_resize_handles()

    def _layout_resize_handles(self):
        if not hasattr(self, "_resize_handles"):
            self._resize_handles = []
        m = self._resize_margin
        w = self.width()
        h = self.height()
        rects = [
            QRect(0, 0, m, m),                       # 左上
            QRect(w - m, 0, m, m),                   # 右上
            QRect(0, h - m, m, m),                   # 左下
            QRect(w - m, h - m, m, m),               # 右下
            QRect(0, m, m, h - 2 * m),               # 左
            QRect(w - m, m, m, h - 2 * m),           # 右
            QRect(m, 0, w - 2 * m, m),               # 上
            QRect(m, h - m, w - 2 * m, m),           # 下
        ]
        edges = [
            Qt.Edge.LeftEdge | Qt.Edge.TopEdge,
            Qt.Edge.RightEdge | Qt.Edge.TopEdge,
            Qt.Edge.LeftEdge | Qt.Edge.BottomEdge,
            Qt.Edge.RightEdge | Qt.Edge.BottomEdge,
            Qt.Edge.LeftEdge,
            Qt.Edge.RightEdge,
            Qt.Edge.TopEdge,
            Qt.Edge.BottomEdge,
        ]
        cursors = [
            Qt.SizeFDiagCursor, Qt.SizeBDiagCursor,
            Qt.SizeBDiagCursor, Qt.SizeFDiagCursor,
            Qt.SizeHorCursor, Qt.SizeHorCursor,
            Qt.SizeVerCursor, Qt.SizeVerCursor,
        ]
        if len(self._resize_handles) != len(rects):
            self._resize_handles = []
            for ed, cur in zip(edges, cursors):
                hd = _ResizeHandle(self, ed, cur)
                hd.show()
                hd.raise_()
                self._resize_handles.append(hd)
        for hd, rect in zip(self._resize_handles, rects):
            hd.setGeometry(rect)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._layout_resize_handles()

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
        self.card.setStyleSheet(
            f"#card{{background:{self.col['card']};border-radius:22px;border:1px solid {self.col['line']};}}")
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(36)
        shadow.setColor(QColor(0, 0, 0, 70))
        shadow.setOffset(0, 12)
        self.card.setGraphicsEffect(shadow)
        root.addWidget(self.card)

        card_lay = QVBoxLayout(self.card)
        card_lay.setContentsMargins(0, 0, 0, 0)
        card_lay.setSpacing(0)
        self.title_bar = TitleBar(self, col=self.col)
        card_lay.addWidget(self.title_bar)

        sep = QFrame()
        sep.setObjectName("cardSep")
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color:{self.col['line']};background:{self.col['line']};max-height:1px;")
        card_lay.addWidget(sep)

        # 滚动区（设置表单）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollBar:vertical{background:transparent;width:8px;margin:2px;}"
            f"QScrollBar::handle:vertical{{background:{self.col['scroll_handle']};border-radius:4px;min-height:30px;}}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}")
        body = QWidget()
        body.setObjectName("body")
        body.setStyleSheet("#body{background:transparent;}")
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(28, 20, 28, 18)
        body_lay.setSpacing(14)
        scroll.setWidget(body)
        card_lay.addWidget(scroll, 1)

        # ---- 录制范围（分组卡片） ----
        sec, lay = self._section_card("录制范围")
        self.mode_seg = QHBoxLayout()
        self.mode_seg.setSpacing(6)
        self.mode_btns = {}
        for key, label in (("full", "全屏"), ("full_all", "全屏(多屏)"),
                           ("region", "区域"), ("window", "窗口")):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setFixedHeight(34)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self._set_mode(k))
            self.mode_btns[key] = b
            self.mode_seg.addWidget(b, 1)
        lay.addLayout(self.mode_seg)

        # 区域 / 窗口：使用固定高度 QStackedWidget 内联切换，避免卡片忽大忽小/闪烁
        self.range_extra = QStackedWidget()
        self.range_extra.setFixedHeight(78)
        # 第 0 页：全屏模式提示
        page_full = QWidget()
        ph = QHBoxLayout(page_full)
        ph.setContentsMargins(0, 0, 0, 0)
        full_hint = QLabel("全屏模式无需额外设置")
        full_hint.setProperty("role", "sub")
        full_hint.setStyleSheet(f"color:{self.col['sub']};font-size:10pt;")
        ph.addWidget(full_hint)
        ph.addStretch(1)
        self.range_extra.addWidget(page_full)

        # 第 1 页：区域选择
        self.lbl_region = QLabel("未选择区域")
        self.lbl_region.setProperty("role", "sub")
        self.lbl_region.setStyleSheet(f"color:{self.col['sub']};font-size:10pt;")
        self.btn_region = QPushButton("选择区域")
        self.btn_region.setFixedHeight(30)
        self.btn_region.setCursor(Qt.PointingHandCursor)
        self.btn_region.clicked.connect(self._select_region)
        self.region_box = QWidget()
        rl = QHBoxLayout(self.region_box)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)
        rl.addWidget(self.lbl_region, 1)
        rl.addWidget(self.btn_region)
        self.range_extra.addWidget(self.region_box)

        # 第 2 页：窗口选择
        self.win_combo = QComboBox()
        self.win_combo.setMinimumHeight(30)
        self.win_combo.addItem("（点击刷新窗口列表）")
        self.btn_win_refresh = QPushButton("刷新")
        self.btn_win_refresh.setFixedHeight(30)
        self.btn_win_refresh.setFixedWidth(54)
        self.btn_win_refresh.setCursor(Qt.PointingHandCursor)
        self.btn_win_refresh.clicked.connect(self._refresh_windows)
        self.win_combo.currentIndexChanged.connect(self._on_win_selected)
        self.window_box = QWidget()
        wl = QVBoxLayout(self.window_box)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(6)
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        row1.addWidget(self.win_combo, 1)
        row1.addWidget(self.btn_win_refresh)
        wl.addLayout(row1)
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        lbl_method = QLabel("窗口捕获")
        lbl_method.setProperty("role", "sub")
        lbl_method.setStyleSheet(f"color:{self.col['sub']};font-size:10pt;")
        lbl_method.setFixedWidth(56)
        self.win_method_combo = self._combo(
            ["PrintWindow（兼容旧窗口）", "桌面合成（DXGI/游戏防黑屏）"],
            "PrintWindow（兼容旧窗口）", self._on_window_method_changed)
        self.win_method_combo.setMinimumHeight(30)
        row2.addWidget(lbl_method)
        row2.addWidget(self.win_method_combo, 1)
        wl.addLayout(row2)
        self.range_extra.addWidget(self.window_box)

        lay.addWidget(self.range_extra)
        body_lay.addWidget(sec)

        # ---- 录制参数（分组卡片） ----
        sec, lay = self._section_card("录制参数")
        self.fps_combo = self._combo(FPS_OPTIONS, "30", self._on_setting_changed)
        self.quality_combo = self._combo(list(QUALITY_PRESETS.keys()), DEFAULT_QUALITY, self._on_setting_changed)
        self.quality_combo.setMinimumWidth(240)
        lay.addLayout(self._row2(
            self._labeled("帧率", self.fps_combo, lw=56),
            self._labeled("画质", self.quality_combo, lw=56),
        ))

        # 更多画质控制：编码器 / 自定义码率 / 分辨率缩放
        self.encoder_combo = self._combo(ENCODER_OPTIONS, "默认（自动）", self._on_setting_changed)
        self.encoder_combo.setMinimumHeight(32)
        self.bitrate_combo = self._combo(BITRATE_OPTIONS, "不设置（CRF 质量）", self._on_setting_changed)
        self.bitrate_combo.setMinimumHeight(32)
        lay.addLayout(self._row2(
            self._labeled("编码器", self.encoder_combo, lw=56),
            self._labeled("码率", self.bitrate_combo, lw=56),
        ))
        self.scale_combo = self._combo(RESOLUTION_SCALE_OPTIONS, "原始（不缩放）", self._on_setting_changed)
        self.scale_combo.setMinimumHeight(32)
        lay.addLayout(self._labeled("分辨率", self.scale_combo, lw=56))

        self.audio_combo = self._combo(["无音频", "系统声音（立体声混音）"], "无音频", self._on_setting_changed)
        self.audio_combo.setMinimumHeight(32)
        self.audio_combo.setMinimumWidth(170)
        self.btn_audio_refresh = QPushButton("刷新")
        self.btn_audio_refresh.setFixedHeight(32)
        self.btn_audio_refresh.setFixedWidth(54)
        self.btn_audio_refresh.setCursor(Qt.PointingHandCursor)
        self.btn_audio_refresh.clicked.connect(self._refresh_audio_devices)
        audio_row = QHBoxLayout()
        audio_row.setSpacing(8)
        audio_row.addWidget(self.audio_combo, 1)
        audio_row.addWidget(self.btn_audio_refresh)
        self.fmt_combo = self._combo(list(FORMAT_OPTIONS.keys()), "MP4 (H.264, 推荐)", self._on_setting_changed)
        self.fmt_combo.setMinimumHeight(32)
        self.fmt_combo.setMinimumWidth(150)
        lay.addLayout(self._row2(
            self._labeled("音频", audio_row, lw=56),
            self._labeled("格式", self.fmt_combo, lw=56),
        ))

        # 麦克风选择：仅在“系统声音 + 麦克风混音”时显示
        self.mic_box = QWidget()
        mic_lay = QHBoxLayout(self.mic_box)
        mic_lay.setContentsMargins(0, 0, 0, 0)
        mic_lay.setSpacing(8)
        self.mic_label = QLabel("麦克风")
        self.mic_label.setProperty("role", "sub")
        self.mic_label.setStyleSheet(f"color:{self.col['sub']};font-size:10pt;")
        self.mic_label.setFixedWidth(56)
        self.mic_combo = self._combo(["（自动选择）"], "（自动选择）", self._on_mic_changed)
        self.mic_combo.setMinimumHeight(32)
        mic_lay.addWidget(self.mic_label)
        mic_lay.addWidget(self.mic_combo, 1)
        self.mic_box.setVisible(False)
        lay.addWidget(self.mic_box)
        self.audio_combo.currentTextChanged.connect(self._update_mic_visibility)

        self.delay_combo = self._combo(list(DELAY_OPTIONS.keys()), "1 秒", self._on_delay_changed)
        self.delay_combo.setMinimumHeight(32)
        self.hotkey_edit = QLineEdit("F9")
        self.hotkey_edit.setReadOnly(True)
        self.hotkey_edit.setMinimumHeight(32)
        self.hotkey_edit.setToolTip("点击右侧“设置”可录制自定义快捷键")
        self.btn_hotkey = QPushButton("设置")
        self.btn_hotkey.setFixedHeight(32)
        self.btn_hotkey.setFixedWidth(56)
        self.btn_hotkey.setCursor(Qt.PointingHandCursor)
        self.btn_hotkey.clicked.connect(self._set_hotkey)
        hotkey_row = QHBoxLayout()
        hotkey_row.setSpacing(6)
        hotkey_row.addWidget(self.hotkey_edit, 1)
        hotkey_row.addWidget(self.btn_hotkey)
        lay.addLayout(self._row2(
            self._labeled("开始延迟", self.delay_combo, lw=56),
            self._labeled("快捷键", hotkey_row, lw=56),
        ))
        body_lay.addWidget(sec)

        # ---- 计划 / 分段 ----
        sec, lay = self._section_card("计划 / 分段")
        self.chk_schedule_start = QCheckBox("定时开始")
        self.datetime_start = QDateTimeEdit(QDateTime.currentDateTime().addSecs(60))
        self.datetime_start.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.datetime_start.setCalendarPopup(True)
        self.datetime_start.setMinimumHeight(30)
        self.chk_duration_limit = QCheckBox("时长上限（分钟）")
        self.spin_duration = QSpinBox()
        self.spin_duration.setRange(1, 600)
        self.spin_duration.setValue(10)
        self.spin_duration.setSuffix(" 分钟")
        self.spin_duration.setMinimumHeight(30)
        self.chk_auto_segment = QCheckBox("自动分段（分钟）")
        self.spin_segment = QSpinBox()
        self.spin_segment.setRange(1, 600)
        self.spin_segment.setValue(10)
        self.spin_segment.setSuffix(" 分钟")
        self.spin_segment.setMinimumHeight(30)
        for w in (self.chk_schedule_start, self.chk_duration_limit, self.chk_auto_segment):
            w.setFixedHeight(30)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        grid.addWidget(self.chk_schedule_start, 0, 0)
        grid.addWidget(self.datetime_start, 0, 1)
        grid.addWidget(self.chk_duration_limit, 1, 0)
        grid.addWidget(self.spin_duration, 1, 1)
        grid.addWidget(self.chk_auto_segment, 2, 0)
        grid.addWidget(self.spin_segment, 2, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        for w in (self.chk_schedule_start, self.chk_duration_limit, self.chk_auto_segment):
            w.toggled.connect(self._on_plan_changed)
        self.datetime_start.dateTimeChanged.connect(self._on_plan_changed)
        self.spin_duration.valueChanged.connect(self._on_plan_changed)
        self.spin_segment.valueChanged.connect(self._on_plan_changed)
        body_lay.addWidget(sec)

        # ---- 偏好 + 保存位置（一左一右同一高度） ----
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(14)

        sec_pref, lay_pref = self._section_card("偏好")
        self.chk_dark = QCheckBox("全局深色模式")
        self.chk_autostart = QCheckBox("开机自启")
        self.chk_float = QCheckBox("显示悬浮控制窗")
        self.chk_auto = QCheckBox("完成后自动打开文件夹")
        self.chk_float_stay = QCheckBox("悬浮框常驻（录制结束不返回主界面）")
        self.chk_dark.toggled.connect(self._on_dark_toggled)
        self.chk_autostart.toggled.connect(self._on_autostart_toggled)
        self.chk_auto.toggled.connect(lambda v: self._schedule_save_config())
        self.chk_float.toggled.connect(lambda v: self._schedule_save_config())
        self.chk_float_stay.toggled.connect(self._on_float_stay_toggled)
        for c in (self.chk_dark, self.chk_autostart, self.chk_float,
                  self.chk_auto, self.chk_float_stay):
            c.setFixedHeight(30)
        pref_grid = QGridLayout()
        pref_grid.setContentsMargins(0, 0, 0, 0)
        pref_grid.setHorizontalSpacing(14)
        pref_grid.setVerticalSpacing(2)
        pref_grid.addWidget(self.chk_dark, 0, 0)
        pref_grid.addWidget(self.chk_autostart, 0, 1)
        pref_grid.addWidget(self.chk_float, 1, 0)
        pref_grid.addWidget(self.chk_auto, 1, 1)
        pref_grid.addWidget(self.chk_float_stay, 2, 0, 1, 2)
        pref_grid.setColumnStretch(0, 1)
        pref_grid.setColumnStretch(1, 1)
        lay_pref.addLayout(pref_grid)
        lay_pref.addStretch(1)
        bottom_row.addWidget(sec_pref, 1)

        sec_save, lay_save = self._section_card("保存位置")
        dir_row = QHBoxLayout()
        dir_row.setSpacing(8)
        self.dir_edit = QLineEdit(self.core.save_dir)
        self.dir_edit.setMinimumHeight(32)
        self.dir_edit.textChanged.connect(lambda t: (setattr(self.core, "save_dir", t), self._schedule_save_config()))
        self.btn_browse = QPushButton("浏览")
        self.btn_browse.setFixedHeight(32)
        self.btn_browse.setFixedWidth(56)
        self.btn_browse.setCursor(Qt.PointingHandCursor)
        self.btn_browse.clicked.connect(self._browse_dir)
        dir_row.addWidget(self.dir_edit, 1)
        dir_row.addWidget(self.btn_browse)
        lay_save.addLayout(dir_row)

        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_row.addWidget(self._field_label("前缀"))
        self.prefix_edit = QLineEdit("录屏")
        self.prefix_edit.setMinimumHeight(32)
        self.prefix_edit.setFixedWidth(120)
        self.prefix_edit.textChanged.connect(lambda t: (setattr(self.core, "prefix", t), self._schedule_save_config()))
        name_row.addWidget(self.prefix_edit)
        name_row.addStretch(1)
        self.btn_ffmpeg = QPushButton("指定 ffmpeg")
        self.btn_ffmpeg.setFixedHeight(32)
        self.btn_ffmpeg.setCursor(Qt.PointingHandCursor)
        self.btn_ffmpeg.clicked.connect(self._pick_ffmpeg)
        name_row.addWidget(self.btn_ffmpeg)
        lay_save.addLayout(name_row)
        bottom_row.addWidget(sec_save, 1)

        body_lay.addLayout(bottom_row)

        # ---- 预估 ----
        self.estimate_label = QLabel("预估（约）：—")
        self.estimate_label.setProperty("role", "sub")
        self.estimate_label.setStyleSheet(f"color:{self.col['sub']};font-size:10pt;")
        self.estimate_label.setContentsMargins(4, 0, 4, 0)
        body_lay.addWidget(self.estimate_label)
        body_lay.addStretch(1)

        # ---- 录制控制区（紧凑横向布局，减少纵向占用） ----
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
        self.ring_caption.setStyleSheet(f"color:{self.col['text']};")
        self.ring_caption.setFont(_heading_font(18, QFont.Weight.Medium))
        self.tip_label = QLabel("F9 开始 / 停止 · 录制时自动收起到托盘")
        self.tip_label.setProperty("role", "sub")
        self.tip_label.setStyleSheet(f"color:{self.col['sub']};font-size:10pt;")
        right.addWidget(self.ring_caption)
        right.addWidget(self.tip_label)

        # ---- 操作按钮（完成后可用） ----
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

        ra_lay.addLayout(right, 1)
        card_lay.addWidget(ring_area)

        # ---- 状态栏（透明背景，避免遮住卡片底部圆角） ----
        status = QFrame()
        status.setFrameShape(QFrame.NoFrame)
        status.setStyleSheet("background:transparent;")
        slay = QHBoxLayout(status)
        slay.setContentsMargins(24, 8, 24, 14)
        self.status_label = QLabel("就绪")
        self.status_label.setProperty("role", "status")
        self.status_label.setStyleSheet(f"color:{self.col['sub']};font-size:10pt;")
        self.size_label = QLabel("")
        self.size_label.setProperty("role", "sub")
        self.size_label.setStyleSheet(f"color:{self.col['sub']};font:12px Consolas;")
        self.timer_label = QLabel("00:00:00")
        self.timer_label.setProperty("role", "text")
        self.timer_label.setStyleSheet(f"color:{self.col['text']};font:14px Consolas;")
        slay.addWidget(self.status_label)
        slay.addStretch(1)
        slay.addWidget(self.size_label)
        slay.addSpacing(10)
        slay.addWidget(self.timer_label)
        card_lay.addWidget(status)

        self.setStyleSheet(self._global_qss())
        self._set_mode("full")
        self._create_resize_handles()

    # ---------------- UI 辅助 ----------------
    def _combo(self, items, current, slot):
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
        c.currentTextChanged.connect(slot)
        return c

    def _labeled(self, text, control, lw=64):
        h = QHBoxLayout()
        h.setSpacing(8)
        lbl = QLabel(text)
        lbl.setProperty("role", "sub")
        lbl.setFixedWidth(lw)
        lbl.setStyleSheet(f"color:{self.col['sub']};font-size:10pt;")
        h.addWidget(lbl)
        if isinstance(control, QLayout):
            h.addLayout(control, 1)
        else:
            h.addWidget(control, 1)
        return h

    def _row2(self, left, right):
        h = QHBoxLayout()
        h.setSpacing(18)
        h.addLayout(left)
        h.addLayout(right)
        return h

    def _section_label(self, t):
        l = QLabel(t)
        l.setProperty("role", "heading")
        l.setStyleSheet(f"color:{self.col['text']};margin-top:4px;")
        l.setFont(_heading_font(15, QFont.Weight.Medium))
        return l

    def _field_label(self, t):
        l = QLabel(t)
        l.setProperty("role", "sub")
        l.setStyleSheet(f"color:{self.col['sub']};font-size:10pt;")
        return l

    def _section_card(self, title):
        """创建一个浅灰圆角分组卡片，用于提升设置页的层级感。"""
        frame = QFrame()
        frame.setObjectName("sectionCard")
        frame.setStyleSheet(
            f"#sectionCard{{background:{self.col['section_bg']};"
            f"border:1px solid {self.col['section_border']};border-radius:14px;}}")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        if title:
            lbl = QLabel(title)
            lbl.setProperty("role", "heading")
            lbl.setStyleSheet(f"color:{self.col['text']};")
            lbl.setFont(_heading_font(15, QFont.Weight.Medium))
            lay.addWidget(lbl)
        return frame, lay

    def _global_qss(self):
        c = self.col
        return f"""
        QWidget{{color:{c['text']};font-size:10pt;}}
        #sectionCard{{
            background:{c['section_bg']};border:1px solid {c['section_border']};border-radius:14px;
        }}
        QComboBox{{
            border:1px solid {c['input_border']};border-radius:8px;padding:4px 10px;
            background:{c['input_bg']};selection-color:{c['text']};min-height:22px;
        }}
        QComboBox:hover{{border-color:{c['checkbox_border']};}}
        QComboBox:focus{{border-color:{c['accent']};}}
        QComboBox::drop-down{{border:none;width:24px;}}
        QComboBox QAbstractItemView{{
            border:1px solid {c['input_border']};border-radius:8px;background:{c['input_bg']};
            selection-background-color:{c['selected_bg']};selection-color:{c['text']};padding:4px 6px;
            outline:0px;
        }}
        QLineEdit{{
            border:1px solid {c['input_border']};border-radius:8px;padding:5px 10px;background:{c['input_bg']};
            color:{c['text']};
        }}
        QLineEdit:hover{{border-color:{c['checkbox_border']};}}
        QLineEdit:focus{{border-color:{c['accent']};}}
        QDateTimeEdit, QSpinBox{{
            border:1px solid {c['input_border']};border-radius:8px;padding:4px 10px;
            background:{c['input_bg']};color:{c['text']};min-height:22px;
        }}
        QDateTimeEdit:hover, QSpinBox:hover{{border-color:{c['checkbox_border']};}}
        QDateTimeEdit:focus, QSpinBox:focus{{border-color:{c['accent']};}}
        QDateTimeEdit::drop-down{{border:none;width:24px;}}
        QSpinBox::up-button, QSpinBox::down-button{{
            border:none;background:transparent;width:18px;
        }}
        QPushButton{{
            background:{c['hover_bg']};color:{c['text']};border:none;border-radius:8px;
            font-size:10pt;padding:0 14px;
        }}
        QPushButton:hover{{background:{c['pressed_bg']};}}
        QPushButton:pressed{{background:{c['pressed_bg']};}}
        QPushButton:focus{{outline:none;}}
        QPushButton:disabled{{color:{c['disabled_text']};background:{c['section_bg']};}}
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
            background:transparent;width:8px;margin:2px;
        }}
        QScrollBar::handle:vertical{{
            background:{c['scroll_handle']};border-radius:4px;min-height:30px;
        }}
        QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}
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

        self.card.setStyleSheet(
            f"#card{{background:{self.col['card']};border-radius:22px;"
            f"border:1px solid {self.col['line']};}}")
        for sep in self.findChildren(QFrame):
            if sep.objectName() == "cardSep":
                sep.setStyleSheet(
                    f"color:{self.col['line']};background:{self.col['line']};max-height:1px;")
        for f in self.findChildren(QFrame):
            if f.objectName() == "sectionCard":
                f.setStyleSheet(
                    f"#sectionCard{{background:{self.col['section_bg']};"
                    f"border:1px solid {self.col['section_border']};border-radius:14px;}}")
        if self.title_bar:
            self.title_bar.apply_theme(self.col)
        if self.ring:
            self.ring.col = self.col
            self.ring.update()
        for lbl in self.findChildren(QLabel):
            role = lbl.property("role")
            if role == "sub":
                if lbl is self.size_label:
                    lbl.setStyleSheet(f"color:{self.col['sub']};font:12px Consolas;")
                else:
                    lbl.setStyleSheet(f"color:{self.col['sub']};font-size:10pt;")
            elif role == "text":
                if lbl is self.timer_label:
                    lbl.setStyleSheet(f"color:{self.col['text']};font:14px Consolas;")
                else:
                    lbl.setStyleSheet(f"color:{self.col['text']};")
            elif role == "heading":
                lbl.setStyleSheet(f"color:{self.col['text']};")
            elif role == "status":
                pass  # 状态色由 _refresh_status_color 处理
        self.setStyleSheet(self._global_qss())
        if hasattr(self, "tray_menu") and self.tray_menu:
            c = self.col
            self.tray_menu.setStyleSheet(
                f"QMenu{{background:{c['menu_bg']};border:1px solid {c['menu_border']};"
                f"border-radius:10px;padding:6px;}}"
                f"QMenu::item{{padding:7px 24px;border-radius:6px;color:{c['text']};font-size:10pt;}}"
                f"QMenu::item:selected{{background:{c['selected_bg']};color:{c['accent']};}}"
                f"QMenu::separator{{height:1px;background:{c['line']};margin:4px 10px;}}")
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
            QMessageBox.warning(self, "开机自启", "写入注册表失败，请检查权限后重试。")
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

    def _on_mic_changed(self, *_):
        self._schedule_save_config()

    def _update_mic_visibility(self, *_):
        if hasattr(self, "mic_box"):
            self.mic_box.setVisible(self.audio_combo.currentText() == AUDIO_SYSTEM_MIC)

    def _on_delay_changed(self, *_):
        self.core.delay = self.delay_combo.currentText()
        self._schedule_save_config()

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

    def _on_error(self, msg):
        # ui_timer 常驻，定时开始功能不因错误停止
        self.ring.set_recording(False)
        self.ring_caption.setText("开始录制")
        self.timer_label.setText("00:00:00")
        self.size_label.setText("")
        self._set_controls_enabled(True)
        self._set_actions_enabled(True)
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
        QMessageBox.warning(self, "提示", msg)

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
                  self.win_combo, self.dir_edit, self.prefix_edit,
                  self.delay_combo, self.hotkey_edit, self.btn_hotkey, self.win_method_combo,
                  self.chk_auto, self.chk_float, self.chk_dark, self.chk_autostart,
                  self.chk_float_stay, self.chk_schedule_start, self.datetime_start,
                  self.chk_duration_limit, self.spin_duration,
                  self.chk_auto_segment, self.spin_segment, self.btn_browse,
                  self.btn_region, self.btn_win_refresh, self.btn_audio_refresh,
                  self.btn_ffmpeg):
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

    # ---------------- 悬浮窗 ----------------
    def _show_float(self):
        if self.float_win is None:
            self.float_win = FloatingWidget()
            self.float_win.stop_requested.connect(self._toggle_record)
        self.float_win.position_topright()
        self.float_win.show()

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
        c = self.col
        menu.setStyleSheet(
            f"QMenu{{background:{c['menu_bg']};border:1px solid {c['menu_border']};"
            f"border-radius:10px;padding:6px;}}"
            f"QMenu::item{{padding:7px 24px;border-radius:6px;color:{c['text']};font-size:10pt;}}"
            f"QMenu::item:selected{{background:{c['selected_bg']};color:{c['accent']};}}"
            f"QMenu::separator{{height:1px;background:{c['line']};margin:4px 10px;}}")
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
        self.hotkey = GlobalHotkey(vk=vk, modifiers=mods, callback=self._toggle_record)
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
        self.chk_float.setChecked(cfg["show_float"])
        self.chk_auto.setChecked(cfg["auto_open"])
        self.chk_dark.setChecked(cfg["dark"])
        # 开机自启状态校验：以注册表实际状态为准，避免配置与注册表不一致
        actual_autostart = self._is_autostart_enabled()
        self.chk_autostart.blockSignals(True)
        self.chk_autostart.setChecked(actual_autostart)
        self.chk_autostart.blockSignals(False)
        self.chk_float_stay.setChecked(cfg["float_stay"])
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
        self.core.update_estimate()
        if self.ffmpeg and os.path.exists(self.ffmpeg):
            self.core.refresh_audio_devices()
        else:
            self.status_label.setText("ffmpeg 未找到（录制不可用）")
            self.status_label.setStyleSheet(f"color:{self.col['rec']};font-size:10pt;")
        # 悬浮框常驻：启动后直接显示悬浮控制窗
        if self.chk_float_stay.isChecked():
            self._show_float()


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
