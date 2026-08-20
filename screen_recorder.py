# -*- coding: utf-8 -*-
"""
轻量录屏工具 (ScreenRecorder) — macOS 风格
- 纯标准库 (tkinter + subprocess) + ffmpeg 管道，体积小、依赖少
- 支持：全屏(主显示器) / 自定义区域 / 指定窗口 三种录制范围；帧率、画质(清晰度)、音频设备、输出格式
- 音频：无音频 / 系统声音(dshow 立体声混音) / WASAPI 系统声音循环捕获（需含 --enable-wasapi 的 ffmpeg，放至 _internal 即自动启用）
- 高 DPI 适配：进程声明 DPI 感知 + tk scaling，文字清晰不模糊，Tk 坐标与 gdigrab 物理像素一致，区域不会错位
- 录制前可选延时 / 3-2-1 倒计时（避免录到窗口收起的过程）
- 录制时收起到 Windows 任务栏（最小化窗口），并安装全局 F9 低级别键盘钩子，窗口隐藏后仍能一键停止录制
- 指定窗口模式：枚举可见窗口并用 PrintWindow 抓取窗口自身内容（不被其他窗口遮挡）
作者：WorkBuddy 代开发
"""

import os
import re
import sys
import time
import json
import queue
import shutil
import threading
import subprocess
import urllib.request
import zipfile
import ssl
import ctypes
import ctypes.wintypes as wt
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import tkinter.font as tkfont

# ------------------------- 高 DPI 适配 -------------------------
def enable_dpi_awareness():
    """声明本进程为【系统级】DPI 感知。
    关键：只有 DPI 感知后，Windows 才不会把整个窗口当成位图拉伸，
    文字/控件才能在物理像素上原生渲染 -> 清晰不模糊；同时 Tk 坐标与
    gdigrab 同为物理像素，区域捕获不会错位。
    （用系统级 awareness=1 而非每显示器 awareness=2，后者会让 Tk 的字体缩放算错。）
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # PROCESS_SYSTEM_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()     # 退回：Win7 兼容
        except Exception:
            pass


# 模块加载阶段即声明 DPI 感知（必须早于任何 Tk 窗口创建）
enable_dpi_awareness()


def get_dpi_scale():
    """返回当前系统 DPI 缩放比（96dpi=1.0；144dpi=1.5；192dpi=2.0 …）。"""
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        if dpi and dpi > 0:
            return max(1.0, min(3.0, dpi / 96.0))
    except Exception:
        pass
    return 1.0


def apply_crisp_fonts(root):
    """让 Tk 以系统 DPI 正确缩放，并指定清晰无模糊的字体（中文用微软雅黑，等宽用 Consolas）。"""
    try:
        root.tk.call("tk", "scaling", round(get_dpi_scale(), 3))
    except Exception:
        pass
    try:
        fam = "Microsoft YaHei UI"   # Win10/11 自带、字型清晰；缺失时 Tk 自动回退
        df = tkfont.nametofont("TkDefaultFont")
        df.configure(family=fam, size=13)
        tkfont.nametofont("TkTextFont").configure(family=fam, size=13)
        tkfont.nametofont("TkHeadingFont").configure(family=fam, size=14)
        tkfont.nametofont("TkMenuFont").configure(family=fam, size=13)
        tkfont.nametofont("TkFixedFont").configure(family="Consolas", size=14)
    except Exception:
        pass


# ------------------------- 物理像素坐标（高 DPI 下与 gdigrab 一致） -------------------------
def virtual_screen():
    """虚拟屏幕（所有显示器合并）的物理像素范围 (x, y, w, h)。"""
    try:
        _user32 = ctypes.windll.user32
        SM_XVIRTUALSCREEN = 76
        SM_YVIRTUALSCREEN = 77
        SM_CXVIRTUALSCREEN = 78
        SM_CYVIRTUALSCREEN = 79
        x = _user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        y = _user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        w = _user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        h = _user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        if w <= 0 or h <= 0:
            w = _user32.GetSystemMetrics(0)
            h = _user32.GetSystemMetrics(1)
            x = y = 0
        return x, y, w, h
    except Exception:
        return 0, 0, 1920, 1080


def primary_monitor_rect():
    """主显示器的物理像素矩形 (x, y, w, h)；拿不到则返回 None。"""
    try:
        _user32 = ctypes.windll.user32

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT),
                        ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]

        MONITOR_DEFAULTTOPRIMARY = 1
        hmon = _user32.MonitorFromWindow(0, MONITOR_DEFAULTTOPRIMARY)
        if not hmon:
            return None
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if _user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcMonitor
            return (r.left, r.top, r.right - r.left, r.bottom - r.top)
    except Exception:
        pass
    return None


# Windows 下隐藏子进程控制台窗口，避免闪烁
try:
    CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW
except AttributeError:
    CREATE_NO_WINDOW = 0x08000000


# ----------------------------- 画质预设 -----------------------------
QUALITY_PRESETS = {
    "近无损 (CRF 1) · 视觉无损，兼容播放":   {"crf": 1, "preset": "slow", "force_software": True},
    "极清 (CRF 12) · 近原画，极细致":       {"crf": 12, "preset": "slow", "force_software": True},
    "超清 (CRF 18) · 极清晰，细节丰富":     {"crf": 18, "preset": "slow"},
    "高清 (CRF 23) · 清晰，约平台超清画质": {"crf": 23, "preset": "medium"},
    "标准 (CRF 28) · 日常够用，体积适中":   {"crf": 28, "preset": "fast"},
    "流畅 (CRF 34) · 体积小，略有模糊":     {"crf": 34, "preset": "ultrafast"},
}
# 默认画质（键名随上方修改需同步）
DEFAULT_QUALITY = "高清 (CRF 23) · 清晰，约平台超清画质"
FPS_OPTIONS = ["10", "15", "24", "30", "60"]
FORMAT_OPTIONS = {
    "MP4 (H.264, 推荐)": "mp4",
    "MKV": "mkv",
    "AVI": "avi",
    "MOV": "mov",
    "GIF (动图)": "gif",
}
# 全局停止快捷键候选（WH_KEYBOARD_LL 的 vkCode）
HOTKEY_OPTIONS = ["F9", "F10", "F11", "F12"]
VK_MAP = {"F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B}
FFMPEG_DOWNLOAD_URLS = [
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
]


def ffmpeg_cache_dir():
    d = os.path.join(os.path.expanduser("~"), "AppData", "Local", "ScreenRecorder")
    os.makedirs(d, exist_ok=True)
    return d


def get_ffmpeg_path():
    """定位 ffmpeg：优先打包目录/同目录，其次用户缓存目录（递归），找不到返回 None。"""
    dirs = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        dirs.append(sys._MEIPASS)
    exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    dirs.append(exe_dir)
    # onedir 打包（PyInstaller 6.x）会把 --add-binary 放进 _internal 子目录
    dirs.append(os.path.join(exe_dir, "_internal"))
    dirs.append(ffmpeg_cache_dir())
    for d in dirs:
        c = os.path.join(d, "ffmpeg.exe")
        if os.path.exists(c):
            return c
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for root, _, files in os.walk(d):
            if "ffmpeg.exe" in files:
                return os.path.join(root, "ffmpeg.exe")
    return None


def _urlopen_with_fallback(req, timeout=240):
    # 安全起见：只使用系统证书链，绝不降级为不验证证书。
    # ffmpeg 是二进制可执行文件，必须防止 MITM 替换。
    return urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context())


def _safe_extract_zip(zip_path, dest_dir):
    """安全解压：阻止 Zip-Slip 路径穿越。"""
    base = os.path.realpath(dest_dir)
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            target = os.path.realpath(os.path.join(dest_dir, info.filename))
            if not (target == base or target.startswith(base + os.sep)):
                raise RuntimeError(f"非法解压路径: {info.filename}")
        z.extractall(dest_dir)


def download_and_extract_ffmpeg(dest_dir, progress_cb=None):
    os.makedirs(dest_dir, exist_ok=True)
    last_err = None
    for url in FFMPEG_DOWNLOAD_URLS:
        zip_path = os.path.join(dest_dir, "_ffmpeg.zip")
        part_path = zip_path + ".part"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with _urlopen_with_fallback(req, timeout=240) as r:
                total = r.length or 0
                done = 0
                # 流式写入临时文件，避免把 100+MB 压缩包全部读入内存
                with open(part_path, "wb") as f:
                    while True:
                        buf = r.read(65536)
                        if not buf:
                            break
                        f.write(buf)
                        done += len(buf)
                        if progress_cb:
                            progress_cb(done, total)
            os.replace(part_path, zip_path)
            _safe_extract_zip(zip_path, dest_dir)
            try:
                os.remove(zip_path)
            except OSError:
                pass
            for root, _, files in os.walk(dest_dir):
                if "ffmpeg.exe" in files:
                    return os.path.join(root, "ffmpeg.exe")
            raise FileNotFoundError("压缩包中未找到 ffmpeg.exe")
        except Exception as e:
            last_err = e
            # 清理可能残留的临时文件
            for p in (part_path, zip_path):
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except OSError:
                    pass
            continue
    raise last_err or RuntimeError("ffmpeg 下载失败")


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
                name = line.split('"')[1]
                devices.append(name)
    except Exception:
        pass
    return devices


def list_wasapi_devices(ffmpeg):
    """枚举 WASAPI 音频端点（需含 --enable-wasapi 的 ffmpeg）。
    输出格式与 dshow 一致：「"设备名" (audio)」。不含 wasapi 的构建会返回空列表（不报错）。"""
    devices = []
    try:
        proc = subprocess.run(
            [ffmpeg, "-list_devices", "true", "-f", "wasapi", "-i", "dummy"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="ignore", creationflags=CREATE_NO_WINDOW, timeout=15)
        for line in proc.stderr.splitlines():
            line = line.strip()
            if "(audio)" in line and '"' in line:
                name = line.split('"')[1]
                devices.append(name)
    except Exception:
        pass
    return devices


# ----------------------------- 开始/停止热键 -----------------------------
# 全局 F9 低级别键盘钩子（GlobalHotkey）在任意时刻（含窗口收起到任务栏）都能
# 触发开始 / 停止；窗口可见时另用 Tk 的 <F9> 绑定作为冗余兜底。


# ----------------------------- 区域选择 -----------------------------
class RegionSelector:
    def __init__(self, parent):
        self.parent = parent
        self.region = None
        self.start_x = self.start_y = 0
        self.cur = None

        x, y, w, h = virtual_screen()
        self.vx, self.vy, self.vw, self.vh = x, y, w, h

        self.win = tk.Toplevel(parent)
        self.win.attributes("-alpha", 0.35)
        self.win.attributes("-topmost", True)
        self.win.overrideredirect(True)
        self.win.geometry(f"{w}x{h}+{x}+{y}")
        self.win.configure(cursor="crosshair")
        self.win.configure(background="gray")

        self.canvas = tk.Canvas(self.win, bg="gray", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.win.bind("<ButtonPress-1>", self.on_press)
        self.win.bind("<B1-Motion>", self.on_drag)
        self.win.bind("<ButtonRelease-1>", self.on_release)
        self.win.bind("<Escape>", lambda e: self.cancel())
        self.win.focus_force()

    def on_press(self, event):
        self.start_x, self.start_y = event.x_root, event.y_root
        if self.cur:
            self.canvas.delete(self.cur)
        self.cur = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#ff3b30", width=2, fill="")

    def on_drag(self, event):
        self.canvas.coords(self.cur, self.start_x - self.vx, self.start_y - self.vy,
                           event.x_root - self.vx, event.y_root - self.vy)

    def on_release(self, event):
        x1, y1 = self.start_x, self.start_y
        x2, y2 = event.x_root, event.y_root
        rx, ry = min(x1, x2), min(y1, y2)
        rw, rh = abs(x2 - x1), abs(y2 - y1)
        if rw < 10 or rh < 10:
            self.region = None
            self.win.destroy()
            return
        self.region = (rx, ry, rw, rh)
        self.win.destroy()

    def cancel(self):
        self.region = None
        self.win.destroy()


# ----------------------------- 全局热键（录制时窗口收起也能停止） -----------------------------
# 方案说明：放弃系统托盘图标（Shell_NotifyIcon 在部分环境表现不稳定、易报错且点击
# 无响应）。改为“最小化到任务栏 + 全局 F9 低级别键盘钩子”方案——窗口收起到任务栏后，
# 按 F9 仍能在全局一键停止录制，简单可靠、零托盘依赖。
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
VK_F9 = 0x78


class GlobalHotkey:
    """低级别键盘钩子（WH_KEYBOARD_LL）监听全局 F9：即使主窗口已最小化 / 收起到
    任务栏，仍可一键停止录制。钩子在独立守护线程的消息循环中运行。"""

    def __init__(self, vk=VK_F9, callback=None):
        self.vk = vk
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

    def _proc(self, nCode, wParam, lParam):
        try:
            if nCode >= 0 and wParam == WM_KEYDOWN:
                # lParam -> KBDLLHOOKSTRUCT*，其首字段为 vkCode
                vk = ctypes.cast(lParam, ctypes.POINTER(ctypes.c_ulong))[0]
                if vk == self.vk:
                    if self.callback:
                        self.callback()
                    return 1  # 吞掉按键，避免系统默认行为 / 重复触发
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
        # 低级别钩子可用“模块句柄”或 NULL 安装，两种约定都尝试以兼容不同环境
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


def enum_visible_windows():
    """枚举当前可见的顶层窗口，返回 [(标题, hwnd), ...]（标题非空）。"""
    out = []
    try:
        u = ctypes.windll.user32
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        u.IsWindowVisible.argtypes = [ctypes.c_void_p]
        u.IsWindowVisible.restype = ctypes.c_int
        u.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
        u.GetWindowTextLengthW.restype = ctypes.c_int
        u.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        u.GetWindowTextW.restype = ctypes.c_int
        u.EnumWindows.argtypes = [EnumWindowsProc, ctypes.c_void_p]
        u.EnumWindows.restype = ctypes.c_int

        def cb(hwnd, lparam):
            if u.IsWindowVisible(hwnd):
                length = u.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    u.GetWindowTextW(hwnd, buf, length + 1)
                    t = buf.value.strip()
                    if t:
                        out.append((t, int(hwnd)))
            return True

        u.EnumWindows(EnumWindowsProc(cb), 0)
    except Exception:
        pass
    return out


def get_window_title(hwnd):
    """获取指定窗口的当前标题（可能已变化，用于日志/校验）。"""
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


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD),
        ("biWidth", wt.LONG),
        ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG),
        ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", _BITMAPINFOHEADER),
        ("bmiColors", wt.DWORD * 3),
    ]


class _CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("flags", wt.DWORD),
        ("hCursor", ctypes.c_void_p),
        ("ptScreenPos", wt.POINT),
    ]


def capture_window_bgra(hwnd):
    """用 PrintWindow 抓取指定窗口自身内容（不被其他窗口遮挡）。

    返回 (BGRA bytes, width, height)；失败返回 None。
    PrintWindow 失败时回退到屏幕 BitBlt，保证仍能录到窗口区域。
    """
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

        # PW_RENDERFULLCONTENT 让窗口把自身内容绘制到内存 DC；
        # 部分窗口不支持时退到普通 PrintWindow，再不行才回退屏幕 BitBlt。
        ok = u.PrintWindow(hwnd, hdc_mem, 2)
        if not ok:
            ok = u.PrintWindow(hwnd, hdc_mem, 0)
        if not ok:
            g.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, r.left, r.top, 0x00CC0020)  # SRCCOPY

        # 把鼠标指针画进窗口帧（保留原 gdigrab draw_mouse 的体验）
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
            if u.GetCursorInfo(ctypes.byref(ci)) and (ci.flags & 1):  # CURSOR_SHOWING
                cur_x = ci.ptScreenPos.x - r.left
                cur_y = ci.ptScreenPos.y - r.top
                if 0 <= cur_x < w and 0 <= cur_y < h:
                    g.DrawIconEx(hdc_mem, cur_x, cur_y, ci.hCursor, 0, 0, 0, 0, 0x0003)  # DI_NORMAL
        except Exception:
            pass

        bmi = _BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h  # top-down DIB
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0  # BI_RGB
        buf = ctypes.create_string_buffer(w * h * 4)
        got = g.GetDIBits(
            hdc_mem, hbmp, 0, h, ctypes.cast(buf, ctypes.c_void_p),
            ctypes.byref(bmi), 0)

        g.SelectObject(hdc_mem, old_bmp)
        g.DeleteObject(hbmp)
        g.DeleteDC(hdc_mem)
        u.ReleaseDC(0, hdc_screen)

        if got == 0:
            return None
        return bytes(buf.raw), w, h
    except Exception:
        return None


# ----------------------------- 配色（macOS 风） -----------------------------
BG = "#f2f2f7"
CARD = "#ffffff"
BORDER = "#e3e3e8"
TEXT = "#1d1d1f"
SUB = "#8e8e93"
RED = "#ff3b30"
RED_DARK = "#d70015"
ACCENT = "#007aff"
ACCENT_ACTIVE = "#0060df"
SUCCESS = "#28c840"
FONT_TITLE = ("Microsoft YaHei UI", 21, "bold")
FONT_BTN = ("Microsoft YaHei UI", 17, "bold")
FONT_TIP = ("Microsoft YaHei UI", 13)
FONT_MONO = ("Consolas", 17)
# 开始延迟选项（秒）：可选手动倒计时，避免录到窗口隐藏过程
DELAY_OPTIONS = {"0.5 秒": 0.5, "1 秒": 1.0, "2 秒": 2.0, "3 秒": 3.0}


class ScreenRecorderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("轻量录屏")
        self.root.resizable(False, False)
        self.root.configure(bg=BG)

        # 关键：在创建任何控件前设置 DPI 缩放 + 清晰字体
        apply_crisp_fonts(root)

        # 读取持久化配置（快捷键 / 保存目录 / 前缀 / 格式等）
        self.config = self._load_config()
        self.hotkey_var = tk.StringVar(value=self.config.get("hotkey", "F9"))

        self.ffmpeg = get_ffmpeg_path()
        self._hw_encoder = None
        self.proc = None
        self.recording = False
        self._finalizing = False
        self.start_time = 0
        self.region = None
        self._timer_after_id = None
        self._monitor_thread = None
        self._audio_devices = []
        self._wasapi_devices = []     # WASAPI 端点列表（需含 --enable-wasapi 的 ffmpeg）
        self._audio_refresh_seq = 0
        self._ffmpeg_log = []
        self._log_buffer = ""
        self._log_thread = None
        self._out_path = None
        self._last_out = None     # 最近一次成功录制的文件，用于“预览/打开文件夹”按钮
        self._start_after_id = None  # 延迟真正开始录制的 after 任务 id
        self._win_hwnd = None        # 指定窗口模式选中的窗口句柄
        self._ui_queue = queue.Queue()
        self._save_after_id = None
        self._log_lock = threading.Lock()
        self._log_dir = os.path.join(os.path.expanduser("~"), "AppData", "Local", "ScreenRecorder", "logs")
        try:
            os.makedirs(self._log_dir, exist_ok=True)
        except Exception:
            self._log_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        self._log_path = os.path.join(self._log_dir, "screen_recorder.log")
        self._raw_path = None
        self._using_raw_window_capture = False
        self._window_capture_hwnd = None
        self._window_capture_size = None
        self._window_capture_fps = 30
        self._window_capture_thread = None
        self._float_win = None
        self._float_timer = None
        self._float_drag_offset = None

        self._style_ui()
        self._build_ui()
        self._log("程序启动")

        # 全局热键：窗口收起到任务栏后也能一键停止录制（替代不稳定的托盘图标）
        try:
            self.hotkey = GlobalHotkey(
                vk=VK_MAP.get(self.hotkey_var.get(), VK_F9),
                callback=lambda: self.root.after(0, self.toggle_record),
            )
            self.hotkey.start()
        except Exception:
            self.hotkey = None

        if self.ffmpeg is None:
            self.lbl_ffmpeg.config(text="ffmpeg 未就绪", foreground=RED_DARK)
            self.status.config(text="首次使用：正在下载 ffmpeg 编解码器…", foreground=RED_DARK)
            threading.Thread(target=self._ensure_ffmpeg, daemon=True).start()
        else:
            self.lbl_ffmpeg.config(text="ffmpeg 已就绪", foreground="#1d9b4e")
            threading.Thread(target=self._detect_hw_encoder, daemon=True).start()
            self._refresh_audio_devices()

        # 创建“关闭即杀”的 Job Object：把 ffmpeg 子进程纳入后，主进程无论正常退出
        # 还是被强杀/崩溃，系统都会自动结束 ffmpeg，避免留下孤儿抓屏进程（曾导致鼠标闪烁）
        self._ffmpeg_job = self._make_kill_job()

        # 统一 UI 更新队列：所有后台线程只往队列放任务，由主线程 Tk 事件循环执行
        self.root.after(100, self._drain_ui_queue)

    # --------------------------- 样式 ---------------------------
    def _style_ui(self):
        try:
            style = ttk.Style()
            style.theme_use("clam")
            style.configure(".", font=("Microsoft YaHei UI", 10))
            style.configure("TLabel", background=BG, foreground=TEXT,
                            font=("Microsoft YaHei UI", 10))
            style.configure("TFrame", background=BG)
            style.configure("Card.TFrame", background=CARD,
                            relief="flat", borderwidth=0)
            # 按钮：白底、细边框，悬停/按下有 macOS 式浅灰过渡
            style.configure("TButton", background=CARD, foreground=TEXT,
                            bordercolor=BORDER, lightcolor=CARD, darkcolor=CARD,
                            focuscolor=CARD, padding=(12, 8),
                            font=("Microsoft YaHei UI", 10))
            style.map("TButton",
                      background=[("active", "#f5f5f7"), ("pressed", "#e8e8ed")],
                      bordercolor=[("active", "#c7c7cc"), ("pressed", "#aeaeb2")])
            # 下拉框：白色底、蓝色箭头/聚焦边框，避免只读态灰色禁用底色
            style.configure("TCombobox", background=CARD, foreground=TEXT,
                            fieldbackground=CARD, bordercolor=BORDER,
                            lightcolor=CARD, darkcolor=CARD, arrowcolor=ACCENT,
                            padding=(8, 7), font=("Microsoft YaHei UI", 10))
            style.map("TCombobox",
                      fieldbackground=[("readonly", CARD), ("focus", "#ffffff")],
                      bordercolor=[("focus", ACCENT), ("hover", "#c7c7cc")],
                      arrowcolor=[("focus", ACCENT), ("active", ACCENT_ACTIVE)])
            # 输入框：聚焦时显示 macOS 蓝边框
            style.configure("TEntry", background=CARD, foreground=TEXT,
                            fieldbackground=CARD, bordercolor=BORDER,
                            lightcolor=CARD, darkcolor=CARD, padding=(8, 7),
                            font=("Microsoft YaHei UI", 10))
            style.map("TEntry",
                      bordercolor=[("focus", ACCENT)],
                      fieldbackground=[("focus", "#ffffff")])
            # 单选：使用 macOS 蓝色选中指示
            style.configure("TRadiobutton", background=CARD, foreground=TEXT,
                            indicatorcolor=ACCENT, focuscolor=ACCENT,
                            font=("Microsoft YaHei UI", 10))
            style.map("TRadiobutton",
                      indicatorcolor=[("selected", ACCENT)],
                      background=[("active", CARD)])
            # 复选：同样使用 macOS 蓝色
            style.configure("TCheckbutton", background=CARD, foreground=TEXT,
                            indicatorcolor=ACCENT, focuscolor=ACCENT,
                            font=("Microsoft YaHei UI", 10))
            style.map("TCheckbutton",
                      indicatorcolor=[("selected", ACCENT)],
                      background=[("active", CARD)])
        except Exception:
            pass

    # --------------------------- 线程安全 UI 更新 ---------------------------
    def _post_ui(self, fn):
        """从任意线程安全地向主线程投递一个 UI 更新任务。"""
        try:
            self._ui_queue.put(fn)
        except Exception:
            pass

    def _drain_ui_queue(self):
        try:
            while True:
                fn = self._ui_queue.get_nowait()
                try:
                    fn()
                except Exception:
                    pass
        except queue.Empty:
            pass
        except Exception:
            pass
        try:
            self.root.after(100, self._drain_ui_queue)
        except Exception:
            pass

    # --------------------------- 日志 ---------------------------
    def _log(self, msg):
        """写入本地日志文件，便于排查问题。"""
        try:
            with self._log_lock:
                with open(self._log_path, "a", encoding="utf-8") as f:
                    f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
        except Exception:
            pass

    def _open_log(self):
        """界面按钮：直接打开日志文件；文件不存在则打开日志目录。"""
        try:
            if os.path.exists(self._log_path):
                os.startfile(self._log_path)
            else:
                os.makedirs(self._log_dir, exist_ok=True)
                os.startfile(self._log_dir)
        except Exception as e:
            messagebox.showerror("错误", f"无法打开日志：{e}")

    # --------------------------- UI 构建 ---------------------------
    def _build_ui(self):
        # 顶部标题栏（macOS 风：左侧标题 + 右侧交通灯圆点）
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=18, pady=(13, 7))
        tk.Label(header, text="轻量录屏", bg=BG, fg=TEXT,
                 font=FONT_TITLE).pack(side="left")

        dots = tk.Frame(header, bg=BG)
        dots.pack(side="right")
        for color in ("#ff5f57", "#febc2e", "#28c840"):
            cv = tk.Canvas(dots, width=12, height=12, bg=BG, highlightthickness=0, bd=0)
            cv.create_oval(1, 1, 11, 11, fill=color, outline="")
            cv.pack(side="right", padx=3)

        # 设置卡片
        card = tk.Frame(self.root, bg=CARD, highlightbackground=BORDER,
                        highlightthickness=1)
        card.pack(fill="x", padx=14, pady=(0, 12))

        pad = {"padx": 14, "pady": 5}
        self._pad = pad

        # 录制范围
        f_mode = tk.Frame(card, bg=CARD)
        f_mode.pack(fill="x", **pad)
        tk.Label(f_mode, text="录制范围", bg=CARD, fg=SUB, width=8,
                 anchor="w").pack(side="left")
        self.mode = tk.StringVar(value="full")
        ttk.Radiobutton(f_mode, text="全屏（主显示器）", variable=self.mode,
                        value="full", command=self._on_mode_change).pack(side="left", padx=6)
        ttk.Radiobutton(f_mode, text="全屏（所有显示器）", variable=self.mode,
                        value="full_all", command=self._on_mode_change).pack(side="left", padx=6)
        ttk.Radiobutton(f_mode, text="自定义区域", variable=self.mode,
                        value="region", command=self._on_mode_change).pack(side="left", padx=6)
        ttk.Radiobutton(f_mode, text="指定窗口", variable=self.mode,
                        value="window", command=self._on_mode_change).pack(side="left", padx=6)
        self.btn_select_region = ttk.Button(f_mode, text="选择区域", command=self._select_region)
        self.btn_select_region.pack(side="left", padx=6)
        self.lbl_region = tk.Label(f_mode, text="未选择区域", bg=CARD, fg=SUB)
        self.lbl_region.pack(side="left", padx=6)

        # 指定窗口（仅“指定窗口”模式显示）
        self.f_win = tk.Frame(card, bg=CARD)
        tk.Label(self.f_win, text="窗口", bg=CARD, fg=SUB, width=8,
                 anchor="w").pack(side="left")
        self.win_var = tk.StringVar()
        self.win_combo = ttk.Combobox(self.f_win, textvariable=self.win_var,
                                      values=[], width=20, state="readonly")
        self.win_combo.pack(side="left", padx=6)
        self.win_combo.bind("<<ComboboxSelected>>", lambda e: self._on_window_selected())
        ttk.Button(self.f_win, text="刷新列表", command=self._refresh_windows).pack(side="left", padx=6)
        self.lbl_win = tk.Label(self.f_win, text="未选择窗口", bg=CARD, fg=SUB)
        self.lbl_win.pack(side="left", padx=6)
        self._win_map = {}

        # 帧率 / 画质
        f_param1 = tk.Frame(card, bg=CARD)
        f_param1.pack(fill="x", **pad)
        tk.Label(f_param1, text="帧率", bg=CARD, fg=SUB, width=8, anchor="w").pack(side="left")
        self.fps = tk.StringVar(value=self.config.get("fps", "30"))
        ttk.Combobox(f_param1, textvariable=self.fps, values=FPS_OPTIONS,
                     width=8, state="readonly").pack(side="left", padx=6)
        tk.Label(f_param1, text="画质", bg=CARD, fg=SUB, width=6, anchor="w").pack(side="left", padx=(12, 0))
        self.quality = tk.StringVar(value=self.config.get("quality", DEFAULT_QUALITY))
        ttk.Combobox(f_param1, textvariable=self.quality,
                     values=list(QUALITY_PRESETS.keys()), width=32,
                     state="readonly").pack(side="left", padx=6)

        # 音频 / 格式
        f_param2 = tk.Frame(card, bg=CARD)
        f_param2.pack(fill="x", **pad)
        tk.Label(f_param2, text="音频", bg=CARD, fg=SUB, width=8, anchor="w").pack(side="left")
        self.audio_var = tk.StringVar(value=self.config.get("audio", "无音频"))
        self.audio_combo = ttk.Combobox(f_param2, textvariable=self.audio_var,
                                        values=["无音频"], width=24, state="readonly")
        self.audio_combo.pack(side="left", padx=6)
        ttk.Button(f_param2, text="刷新", width=5, command=self._refresh_audio_devices).pack(side="left", padx=(0, 6))
        tk.Label(f_param2, text="格式", bg=CARD, fg=SUB, width=6, anchor="w").pack(side="left", padx=(12, 0))
        self.fmt_var = tk.StringVar(value=self.config.get("fmt", "MP4 (H.264, 推荐)"))
        ttk.Combobox(f_param2, textvariable=self.fmt_var,
                     values=list(FORMAT_OPTIONS.keys()), width=16,
                     state="readonly").pack(side="left", padx=6)

        # 实时预估大小（随帧率/画质/音频/格式/录制范围变化，纯估算仅供参考）
        f_est = tk.Frame(card, bg=CARD)
        f_est.pack(fill="x", **pad)
        tk.Label(f_est, text="预估大小", bg=CARD, fg=SUB, width=8, anchor="w").pack(side="left")
        self.estimate_var = tk.StringVar(value="预估：—")
        tk.Label(f_est, textvariable=self.estimate_var, bg=CARD, fg=TEXT, anchor="w").pack(side="left", padx=6)

        # 偏好：悬浮控制窗 / 完成后自动打开文件夹
        f_pref = tk.Frame(card, bg=CARD)
        f_pref.pack(fill="x", **pad)
        tk.Label(f_pref, text="偏好", bg=CARD, fg=SUB, width=8, anchor="w").pack(side="left")
        self.show_float_var = tk.BooleanVar(value=self.config.get("show_float", True))
        ttk.Checkbutton(f_pref, text="显示悬浮控制窗", variable=self.show_float_var,
                        command=self._schedule_save_config).pack(side="left", padx=6)
        self.auto_open_var = tk.BooleanVar(value=self.config.get("auto_open", False))
        ttk.Checkbutton(f_pref, text="完成后自动打开文件夹", variable=self.auto_open_var,
                        command=self._schedule_save_config).pack(side="left", padx=6)

        # 开始延迟（录前倒计时，避免录到窗口隐藏过程）
        f_delay = tk.Frame(card, bg=CARD)
        f_delay.pack(fill="x", **pad)
        tk.Label(f_delay, text="开始延迟", bg=CARD, fg=SUB, width=8, anchor="w").pack(side="left")
        self.delay_var = tk.StringVar(value=self.config.get("delay", "1 秒"))
        ttk.Combobox(f_delay, textvariable=self.delay_var,
                     values=list(DELAY_OPTIONS.keys()), width=12,
                     state="readonly").pack(side="left", padx=6)
        tk.Label(f_delay, text="（录前倒计时，避免录到窗口）", bg=CARD, fg=SUB).pack(side="left", padx=6)

        # 快捷键（全局停止键，录制中缩到任务栏也有效）
        f_hotkey = tk.Frame(card, bg=CARD)
        f_hotkey.pack(fill="x", **pad)
        tk.Label(f_hotkey, text="快捷键", bg=CARD, fg=SUB, width=8, anchor="w").pack(side="left")
        ttk.Combobox(f_hotkey, textvariable=self.hotkey_var,
                     values=HOTKEY_OPTIONS, width=10, state="readonly").pack(side="left", padx=6)
        tk.Label(f_hotkey, text="（开始/停止录制）", bg=CARD, fg=SUB).pack(side="left", padx=6)
        self.hotkey_var.trace_add("write", lambda *a: self._on_hotkey_change())

        # 保存位置
        f_out = tk.Frame(card, bg=CARD)
        f_out.pack(fill="x", **pad)
        tk.Label(f_out, text="保存", bg=CARD, fg=SUB, width=8, anchor="w").pack(side="left")
        self.save_dir = tk.StringVar(value=self.config.get("save_dir", self._default_save_dir()))
        self.entry_dir = ttk.Entry(f_out, textvariable=self.save_dir)
        self.entry_dir.pack(side="left", padx=6, fill="x", expand=True)
        ttk.Button(f_out, text="浏览", command=self._choose_dir).pack(side="left", padx=6)
        tk.Label(f_out, text="前缀", bg=CARD, fg=SUB).pack(side="left", padx=(6, 2))
        self.prefix = tk.StringVar(value=self.config.get("prefix", "录屏"))
        self.entry_prefix = ttk.Entry(f_out, textvariable=self.prefix, width=8)
        self.entry_prefix.pack(side="left", padx=2)
        self.prefix.trace_add("write", lambda *a: self._schedule_save_config())
        # 其余设置项变更即持久化（保存目录/前缀/格式/帧率/画质/延迟/音频）
        self.save_dir.trace_add("write", lambda *a: self._schedule_save_config())
        self.fmt_var.trace_add("write", lambda *a: self._schedule_save_config())
        self.fps.trace_add("write", lambda *a: self._schedule_save_config())
        self.quality.trace_add("write", lambda *a: self._schedule_save_config())
        self.delay_var.trace_add("write", lambda *a: self._schedule_save_config())
        self.audio_var.trace_add("write", lambda *a: self._schedule_save_config())
        # 设置变化即刷新“实时预估大小”
        self.fps.trace_add("write", lambda *a: self._update_estimate())
        self.quality.trace_add("write", lambda *a: self._update_estimate())
        self.fmt_var.trace_add("write", lambda *a: self._update_estimate())
        self.audio_var.trace_add("write", lambda *a: self._update_estimate())
        self.lbl_ffmpeg = tk.Label(f_out, text="检测中…", fg=SUB)
        self.lbl_ffmpeg.pack(side="left", padx=6)
        ttk.Button(f_out, text="指定", command=self._pick_ffmpeg).pack(side="left", padx=2)

        # 录制按钮区（红色大圆圈）
        f_rec = tk.Frame(self.root, bg=BG)
        f_rec.pack(fill="x", pady=(0, 2))
        self.canvas = tk.Canvas(f_rec, width=118, height=118, bg=BG,
                                highlightthickness=0, cursor="hand2")
        self.canvas.pack()
        self.canvas.bind("<Button-1>", lambda e: self.toggle_record())
        self.rec_label = tk.Label(f_rec, text="开始录制", bg=BG, fg=TEXT,
                                  font=FONT_BTN)
        self.rec_label.pack()
        self.tip_label = tk.Label(f_rec, text="F9 开始/停止 · 录制时缩到任务栏（按 F9 可随时停止）", bg=BG, fg=SUB,
                                  font=FONT_TIP)
        self.tip_label.pack()
        # 录制成功后的“预览 / 打开所在文件夹”操作按钮（默认禁用，成功后才可点）
        self.f_actions = tk.Frame(self.root, bg=BG)
        self.f_actions.pack(fill="x", padx=14, pady=(4, 8))
        self.btn_preview = ttk.Button(
            self.f_actions, text="预览", state="disabled",
            command=lambda: self._open_file(self._last_out))
        self.btn_preview.pack(side="left", padx=(0, 7), fill="x", expand=True)
        self.btn_openfolder = ttk.Button(
            self.f_actions, text="打开所在文件夹", state="disabled",
            command=lambda: self._open_folder(self._last_out))
        self.btn_openfolder.pack(side="left", padx=(7, 0), fill="x", expand=True)
        self.btn_log = ttk.Button(
            self.f_actions, text="日志", command=self._open_log)
        self.btn_log.pack(side="left", padx=(7, 0), fill="x", expand=True)
        self._draw_button(idle=True)

        # 状态 + 实时大小 + 计时
        f_status = tk.Frame(self.root, bg=BG)
        f_status.pack(fill="x", padx=14, pady=(2, 10))
        self.status = tk.Label(f_status, text="就绪", fg=SUB, bg=BG, anchor="w")
        self.status.pack(side="left")
        self.timer = tk.Label(f_status, text="00:00:00", fg=TEXT, bg=BG,
                              font=FONT_MONO)
        self.timer.pack(side="right")
        # 录制过程中实时显示文件大小（右侧，计时器左边）
        self.size_live = tk.Label(f_status, text="", fg=SUB, bg=BG,
                                  font=FONT_MONO, anchor="e")
        self.size_live.pack(side="right", padx=(0, 14))

        # 初始刷新一次“实时预估大小”
        self._update_estimate()

        # 全局钩子已常驻；此处再绑定 Tk 对应按键作为窗口可见时的冗余兜底
        self.root.bind(f"<{self.hotkey_var.get()}>", lambda e: self.toggle_record())

    # --------------------------- 红色圆形按钮 ---------------------------
    def _draw_button(self, idle=True):
        self.canvas.delete("all")
        cx, cy, R = 59, 59, 48
        if idle:
            self.canvas.create_oval(cx - R, cy - R, cx + R, cy + R,
                                    fill=RED, outline="", width=0)
            self.canvas.create_oval(cx - R + 9, cy - R + 9, cx + R - 9, cy + R - 9,
                                    outline="#ff6f67", width=4)
            self.rec_label.config(text="开始录制")
        else:
            self.canvas.create_oval(cx - R, cy - R, cx + R, cy + R,
                                    fill=RED_DARK, outline="", width=0)
            s = 36
            self.canvas.create_rectangle(cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2,
                                         fill="#ffffff", outline="", width=0)
            self.rec_label.config(text="停止录制")

    # --------------------------- 配置持久化 ---------------------------
    def _config_path(self):
        # 优先使用用户 LocalAppData，避免程序装在只读目录时配置无法保存
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        new_dir = os.path.join(base, "ScreenRecorder")
        try:
            os.makedirs(new_dir, exist_ok=True)
        except Exception:
            new_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        new_path = os.path.join(new_dir, "config.json")
        old_path = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "config.json")
        # 首次运行迁移旧版 exe 同目录配置
        if not os.path.exists(new_path) and os.path.exists(old_path):
            try:
                shutil.copy2(old_path, new_path)
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

        # 白名单归一化，防止损坏/手改配置导致启动或录制崩溃
        if d.get("hotkey") not in HOTKEY_OPTIONS:
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
            d["save_dir"] = self._default_save_dir()
        if not isinstance(d.get("prefix"), str) or not d["prefix"].strip():
            d["prefix"] = "录屏"
        if not isinstance(d.get("audio"), str):
            d["audio"] = "无音频"
        if not isinstance(d.get("show_float"), bool):
            d["show_float"] = True
        if not isinstance(d.get("auto_open"), bool):
            d["auto_open"] = False
        return d

    def _schedule_save_config(self):
        """配置防抖：避免每次按键都同步写盘。"""
        if getattr(self, "_save_after_id", None):
            try:
                self.root.after_cancel(self._save_after_id)
            except Exception:
                pass
        self._save_after_id = self.root.after(400, self._save_config)

    def _save_config(self):
        tmp = None
        try:
            d = {
                "hotkey": self.hotkey_var.get(),
                "save_dir": self.save_dir.get(),
                "prefix": self.prefix.get(),
                "fmt": self.fmt_var.get(),
                "fps": self.fps.get(),
                "quality": self.quality.get(),
                "audio": self.audio_var.get(),
                "delay": self.delay_var.get(),
                "show_float": self.show_float_var.get(),
                "auto_open": self.auto_open_var.get(),
            }
            path = self._config_path()
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            tmp = None
        except Exception:
            try:
                if tmp and os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

    def _on_hotkey_change(self):
        """快捷键下拉变更：重绑 Tk 键 + 重启全局钩子 + 持久化。"""
        try:
            key = self.hotkey_var.get()
            vk = VK_MAP.get(key, VK_F9)
            for k in HOTKEY_OPTIONS:
                try:
                    self.root.unbind(f"<{k}>")
                except Exception:
                    pass
            self.root.bind(f"<{key}>", lambda e: self.toggle_record())
            if getattr(self, "hotkey", None):
                try:
                    self.hotkey.stop()
                except Exception:
                    pass
            self.hotkey = GlobalHotkey(vk=vk,
                                       callback=lambda: self.root.after(0, self.toggle_record))
            self.hotkey.start()
        except Exception:
            pass
        # 同步更新底部提示文案，让快捷键名称始终一致
        try:
            self.tip_label.config(
                text=f"{key} 开始/停止 · 录制时缩到任务栏（按 {key} 可随时停止）")
        except Exception:
            pass
        self._save_config()

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

    # --------------------------- 实时预估大小 ---------------------------
    # CRF → 画面码率系数（bits/pixel/frame 的近似）。数值越大表示画质越高、体积越大。
    _CRF_BITRATE_FACTOR = {
        1: 0.50,    # 近无损 (CRF 1)
        12: 0.18,   # 极清 (CRF 12)
        18: 0.12,   # 超清 (CRF 18)
        23: 0.07,   # 高清 (CRF 23)
        28: 0.045,  # 标准 (CRF 28)
        34: 0.025,  # 流畅 (CRF 34)
    }

    def _current_resolution(self):
        """预估用的当前录制分辨率 (w, h)。"""
        mode = self.mode.get()
        if mode == "region" and self.region:
            return self.region[2], self.region[3]
        if mode == "window":
            if self._window_capture_size:
                return self._window_capture_size
            rect = primary_monitor_rect()
            if rect:
                return rect[2], rect[3]
            return 1920, 1080
        if mode == "full_all":
            vs = virtual_screen()
            if vs:
                return vs[2], vs[3]
            return 1920, 1080
        rect = primary_monitor_rect()
        if rect:
            return rect[2], rect[3]
        return 1920, 1080

    def _estimate_size_per_min(self):
        """返回 (每分钟字节数, 是否 GIF)。纯估算，仅供参考。"""
        fmt_label = self.fmt_var.get()
        ext = FORMAT_OPTIONS.get(fmt_label, "mp4")
        w, h = self._current_resolution()
        try:
            fps = int(self.fps.get())
        except Exception:
            fps = 30
        if ext == "gif":
            fps_gif = min(fps, 25)
            # GIF 调色板 + LZW 压缩，每像素约 0.4 字节/帧（粗略中值），体积随画面复杂度变化很大
            bytes_per_min = int(w * h * fps_gif * 0.4 * 60)
            return bytes_per_min, True
        q = QUALITY_PRESETS.get(self.quality.get(), QUALITY_PRESETS[DEFAULT_QUALITY])
        factor = self._CRF_BITRATE_FACTOR.get(q["crf"], 0.07)
        video_kbps = (w * h * fps) * factor / 1000.0
        audio_kbps = 0 if self.audio_var.get() == "无音频" else 160
        total_kbps = video_kbps + audio_kbps
        # 1 kbps = 1000 bit/s = 125 byte/s
        bytes_per_min = int(total_kbps * 125 * 60)
        return bytes_per_min, False

    def _update_estimate(self):
        """刷新“实时预估大小”显示，随帧率/画质/音频/格式/录制范围变化。"""
        try:
            bpm, is_gif = self._estimate_size_per_min()
            if is_gif:
                self.estimate_var.set(f"预估（GIF，约）：{self._fmt_size(bpm)}/分钟")
            else:
                self.estimate_var.set(f"预估（约）：{self._fmt_size(bpm)}/分钟")
        except Exception:
            self.estimate_var.set("预估：—")

    @staticmethod
    def _safe_prefix(prefix):
        """清洗输出文件名前缀，防止路径穿越和非法文件名字符。"""
        if not prefix:
            return "录屏"
        # 去掉路径分隔符、盘符和 Windows 非法字符
        cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", str(prefix))
        cleaned = cleaned.strip(" .")
        return cleaned or "录屏"

    def _open_file(self, path):
        try:
            if path and os.path.exists(path):
                os.startfile(path)
        except Exception:
            pass

    def _open_folder(self, path):
        try:
            if path and os.path.exists(path):
                os.startfile(os.path.dirname(os.path.abspath(path)))
        except Exception:
            pass

    def _maybe_auto_open(self, path):
        """如果用户勾选了“完成后自动打开文件夹”，录制完成后打开所在目录。"""
        try:
            if getattr(self, "auto_open_var", None) and self.auto_open_var.get():
                self._open_folder(path)
        except Exception:
            pass

    # --------------------------- 辅助 ---------------------------
    def _default_save_dir(self):
        d = os.path.join(os.path.expanduser("~"), "Desktop", "录屏")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            d = os.path.dirname(os.path.abspath(sys.argv[0]))
        return d

    def _on_mode_change(self):
        mode = self.mode.get()
        if mode == "region":
            self.f_win.pack_forget()
            if self.region is None:
                self._select_region()
            else:
                x, y, w, h = self.region
                self.lbl_region.config(text=f"区域 {w}×{h} @({x},{y})")
        elif mode in ("full", "full_all"):
            self.f_win.pack_forget()
            if self.region is None:
                self.lbl_region.config(text="未选择区域")
            else:
                x, y, w, h = self.region
                self.lbl_region.config(text=f"区域 {w}×{h} @({x},{y})")
        elif mode == "window":
            self.f_win.pack(fill="x", **self._pad)
            if not self._win_map:
                self._refresh_windows()
        self._update_estimate()

    def _refresh_windows(self):
        """刷新可见窗口列表到“指定窗口”下拉框。"""
        try:
            wins = enum_visible_windows()
        except Exception:
            wins = []
        self._win_map = {}
        display = []
        for title, hwnd in wins:
            # 不要把本程序窗口/悬浮控制窗列进去
            if "轻量录屏" in title or "ScreenRecorder 控制" in title:
                continue
            # 用“标题 [hwnd]”作为显示文本，避免同标题窗口互相覆盖
            label = f"{title} [{hwnd}]"
            self._win_map[label] = hwnd
            display.append(label)
        if display:
            self.win_combo.configure(values=display)
            self.win_combo.set(display[0])
            self.win_var.set(display[0])
        else:
            self.win_combo.configure(values=[])
            self.win_combo.set("")
            self._win_hwnd = None
            self.lbl_win.config(text="未找到可见窗口")
            return
        self._on_window_selected()

    def _on_window_selected(self):
        """根据下拉框当前选中的窗口，记录其句柄并预览矩形。"""
        label = self.win_var.get()
        hwnd = self._win_map.get(label) if label else None
        self._win_hwnd = hwnd
        if not hwnd:
            self.lbl_win.config(text="未选择窗口" if not label else "窗口句柄缺失")
            return
        try:
            u = ctypes.windll.user32
            # 显式声明参数类型，避免 64 位窗口句柄被截断
            u.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.RECT)]
            u.GetWindowRect.restype = ctypes.c_int
            u.IsIconic.argtypes = [ctypes.c_void_p]
            u.IsIconic.restype = ctypes.c_int
            r = wt.RECT()
            if u.GetWindowRect(hwnd, ctypes.byref(r)):
                w = r.right - r.left
                h = r.bottom - r.top
                self.lbl_win.config(text=f"{w}×{h} @({r.left},{r.top})")
                self._window_capture_size = (w, h)
                self._update_estimate()
            else:
                self.lbl_win.config(text="获取窗口矩形失败")
        except Exception:
            self.lbl_win.config(text="获取窗口失败")

    def _select_region(self):
        self.root.update_idletasks()
        self.root.iconify()
        self.root.after(250, self._open_selector)

    def _open_selector(self):
        sel = RegionSelector(self.root)
        self.root.wait_window(sel.win)
        self.root.deiconify()
        if sel.region:
            self.region = sel.region
            x, y, w, h = sel.region
            self.lbl_region.config(text=f"区域 {w}×{h} @({x},{y})")
            self._update_estimate()
        else:
            self.lbl_region.config(text="未选择区域")
            if self.mode.get() == "region":
                messagebox.showwarning("提示", "未选择区域，请重新选择或切换到全屏。")

    def _choose_dir(self):
        d = filedialog.askdirectory(initialdir=self.save_dir.get())
        if d:
            self.save_dir.set(d)
            self._save_config()

    def _pick_ffmpeg(self):
        init_dir = os.path.dirname(self.ffmpeg) if self.ffmpeg else os.path.expanduser("~")
        p = filedialog.askopenfilename(
            title="选择 ffmpeg.exe", initialdir=init_dir,
            filetypes=[("ffmpeg 可执行文件", "ffmpeg.exe"), ("所有文件", "*.*")])
        if p:
            try:
                subprocess.run([p, "-version"], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW, timeout=10)
                self.ffmpeg = p
                self.lbl_ffmpeg.config(text="ffmpeg 已指定", foreground="#1d9b4e")
                self.status.config(text="就绪（ffmpeg 已指定）", foreground=SUB)
                self._refresh_audio_devices()
            except Exception as e:
                messagebox.showerror("错误", f"无法运行指定的 ffmpeg.exe：\n{e}")

    def _ensure_ffmpeg(self):
        try:
            dest = ffmpeg_cache_dir()
            self.ffmpeg = download_and_extract_ffmpeg(
                dest, progress_cb=lambda done, total: self._update_dl_status(done, total))
            subprocess.run([self.ffmpeg, "-version"], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW, timeout=15)
            self._post_ui(lambda: self.lbl_ffmpeg.config(text="ffmpeg 已就绪", foreground="#1d9b4e"))
            self._post_ui(lambda: self.status.config(text="就绪", foreground=SUB))
            self._log("ffmpeg 就绪")
            self._detect_hw_encoder()
            self._refresh_audio_devices()
        except Exception as e:
            self.ffmpeg = None
            self._log(f"ffmpeg 下载失败: {e}")
            self._post_ui(lambda: self.lbl_ffmpeg.config(text="ffmpeg 失败", foreground=RED_DARK))
            self._post_ui(lambda: self.status.config(
                text=f"ffmpeg 下载失败：{e}。请点击“指定”手动选择 ffmpeg.exe。",
                foreground=RED_DARK))

    def _update_dl_status(self, done, total):
        if total:
            pct = done * 100 // total
            self._post_ui(lambda: self.status.config(
                text=f"首次使用：正在下载 ffmpeg 编解码器… {pct}%", foreground=RED_DARK))

    def _refresh_audio_devices(self):
        self._audio_refresh_seq += 1
        seq = self._audio_refresh_seq

        def worker():
            try:
                devs = list_audio_devices(self.ffmpeg)
                was = list_wasapi_devices(self.ffmpeg)
                items = ["无音频", "系统声音（立体声混音）"] + devs
                for d in was:
                    items.append("WASAPI: " + d)

                def update():
                    if seq != self._audio_refresh_seq:
                        return
                    self._audio_devices = devs
                    self._wasapi_devices = was
                    self.audio_combo.configure(values=items)
                    if self.audio_var.get() not in items:
                        self.audio_var.set("无音频")
                self._post_ui(update)
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    def _resolve_system_audio_device(self):
        keywords = ["Stereo Mix", "立体声混音", "What U Hear", "virtual-audio-capturer"]
        for dev in self._audio_devices:
            low = dev.lower()
            if any(kw.lower() in low for kw in keywords):
                return dev
        return None

    # --------------------------- 窗口隐藏/恢复（最小化到系统任务栏） ---------------------------
    def _hide_window(self):
        # 录制时收起到 Windows 任务栏（最小化窗口，保留任务栏按钮），由全局 F9 钩子负责停止录制，
        # 不再依赖系统托盘；先短暂延时避免录到窗口收起过程（见 _really_start 的延时逻辑）。
        try:
            self.root.iconify()
        except Exception:
            try:
                self.root.withdraw()
            except Exception:
                pass

    def _show_window(self):
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(120, lambda: self.root.attributes("-topmost", False))
        except Exception:
            pass

    # --------------------------- 悬浮录制控制窗 ---------------------------
    def _show_float_window(self):
        """录制时显示一个置顶小悬浮窗：录制时长 + 停止按钮。"""
        try:
            if self._float_win is not None:
                try:
                    self._float_win.deiconify()
                    self._float_win.lift()
                    self._float_win.attributes("-topmost", True)
                except Exception:
                    pass
                return
            win = tk.Toplevel(self.root)
            win.title("ScreenRecorder 控制")
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            try:
                win.attributes("-toolwindow", True)
            except Exception:
                pass
            win.configure(bg="#1d1d1f")

            frame = tk.Frame(win, bg="#2c2c2e", highlightthickness=1,
                             highlightbackground="#3a3a3c")
            frame.pack(fill="both", expand=True)
            self._float_timer = tk.Label(
                frame, text="00:00", bg="#2c2c2e", fg="white",
                font=("Consolas", 16, "bold"))
            self._float_timer.pack(side="left", padx=(14, 8), pady=8)
            stop_btn = tk.Button(
                frame, text="■ 停止", command=self.stop_record,
                bg="#ff3b30", fg="white", activebackground="#d70015",
                activeforeground="white", relief="flat", bd=0,
                font=("Microsoft YaHei UI", 10, "bold"), cursor="hand2",
                padx=12, pady=5)
            stop_btn.pack(side="left", padx=(0, 12), pady=8)

            # 拖动：按住非按钮区域可移动悬浮窗（按钮保持“点击停止”语义）
            for widget in (win, frame, self._float_timer):
                widget.bind("<Button-1>", self._float_start_drag, add="+")
                widget.bind("<B1-Motion>", self._float_drag, add="+")

            win.update_idletasks()
            vx, vy, vw, vh = virtual_screen()
            x = vx + vw - win.winfo_reqwidth() - 24
            y = vy + vh - win.winfo_reqheight() - 60
            win.geometry(f"+{x}+{y}")
            self._float_win = win
            self._log("已显示悬浮录制控制窗")
        except Exception as e:
            self._log(f"显示悬浮控制窗失败：{e}")
            self._float_win = None

    def _float_start_drag(self, event):
        if self._float_win is None:
            return
        try:
            self._float_drag_offset = (
                event.x_root - self._float_win.winfo_rootx(),
                event.y_root - self._float_win.winfo_rooty(),
            )
        except Exception:
            self._float_drag_offset = None

    def _float_drag(self, event):
        if self._float_win is None or self._float_drag_offset is None:
            return
        try:
            x = event.x_root - self._float_drag_offset[0]
            y = event.y_root - self._float_drag_offset[1]
            self._float_win.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def _update_float_window(self):
        if self._float_win is None:
            return
        try:
            if self.recording and self.start_time:
                elapsed = int(time.time() - self.start_time)
            else:
                elapsed = 0
            h = elapsed // 3600
            m = (elapsed % 3600) // 60
            s = elapsed % 60
            self._float_timer.config(text=f"{h:02d}:{m:02d}:{s:02d}")
        except Exception:
            pass

    def _hide_float_window(self):
        if self._float_win is not None:
            try:
                self._float_win.destroy()
            except Exception:
                pass
        self._float_win = None
        self._float_timer = None
        self._float_drag_offset = None

    # --------------------------- 录制控制 ---------------------------
    def toggle_record(self):
        if self.recording:
            self.stop_record()
        else:
            self.start_record()

    def _detect_hw_encoder(self):
        """检测可用的硬件 H.264 编码器，结果缓存。

        不再只看 ffmpeg -encoders 是否列出该编码器（很多驱动环境列得出但初始化失败），
        而是对候选编码器做 0.5 秒空白画面试编码，确认硬件/驱动真的能工作才启用。
        """
        if self._hw_encoder is not None:
            return self._hw_encoder
        enc = ""
        try:
            proc = subprocess.run(
                [self.ffmpeg, "-hide_banner", "-encoders"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="ignore", creationflags=CREATE_NO_WINDOW, timeout=10)
            text = proc.stdout or ""
            candidates = [name for name in ("h264_nvenc", "h264_qsv", "h264_amf") if name in text]
            self._log(f"候选硬件编码器: {', '.join(candidates) if candidates else '无'}")
            for name in candidates:
                # 用 lavfi color 源做极短试编码，不写入磁盘；若 GPU/驱动不可用会立刻失败
                test_cmd = [
                    self.ffmpeg, "-hide_banner", "-f", "lavfi", "-i",
                    "color=c=black:s=320x240:d=0.5", "-c:v", name,
                    "-f", "null", "-"
                ]
                try:
                    t0 = subprocess.run(
                        test_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="ignore",
                        creationflags=CREATE_NO_WINDOW, timeout=20)
                    if t0.returncode == 0:
                        enc = name
                        self._log(f"硬件编码器 {name} 试编码通过")
                        break
                    else:
                        self._log(f"硬件编码器 {name} 试编码失败 (code={t0.returncode})")
                except Exception as e:
                    self._log(f"硬件编码器 {name} 试编码异常: {e}")
        except Exception as e:
            self._log(f"检测硬件编码器异常: {e}")
        self._hw_encoder = enc
        if enc:
            self._log(f"将使用硬件编码器: {enc}")
        else:
            self._log("将使用软件编码器 libx264")
        return self._hw_encoder

    def _video_encoder_args(self, q):
        """根据可用编码器返回视频编码参数。"""
        crf = int(q.get("crf", 23))
        # 极清/近无损预设强制走 libx264，保证低 CRF 的清晰度与兼容性
        # （硬件编码器的低质量参数不一定支持，且质量通常不如 x264 slow）
        if q.get("force_software") or crf <= 0:
            return ["-c:v", "libx264", "-preset", q.get("preset", "slow"), "-crf", str(crf)]
        hw = self._detect_hw_encoder()
        if hw == "h264_nvenc":
            return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", str(crf)]
        if hw == "h264_qsv":
            return ["-c:v", "h264_qsv", "-global_quality", str(crf), "-preset", "veryfast"]
        if hw == "h264_amf":
            return ["-c:v", "h264_amf", "-quality", "quality", "-qp_i", str(crf), "-qp_p", str(crf)]
        return ["-c:v", "libx264", "-preset", q.get("preset", "medium"), "-crf", str(crf)]

    def _build_command(self, out_path):
        # 防御性取值：即使配置/UI 被异常修改也不会直接崩溃
        try:
            fps = int(self.fps.get())
        except Exception:
            fps = 30
        q = QUALITY_PRESETS.get(self.quality.get(), QUALITY_PRESETS[DEFAULT_QUALITY])
        # 以实际输出文件扩展名为准：MKV 中间文件会按 mkv 逻辑处理，避免误加 mp4 专用参数
        ext = os.path.splitext(out_path)[1].lstrip('.').lower() or FORMAT_OPTIONS.get(self.fmt_var.get(), "mp4")
        audio_dev = self.audio_var.get()

        if self.mode.get() == "window":
            hwnd = getattr(self, "_win_hwnd", None)
            if not hwnd:
                messagebox.showwarning("提示", "未选择要录制的窗口，请先在“指定窗口”下拉框中选择。")
                return None
            try:
                u = ctypes.windll.user32
                u.IsIconic.argtypes = [ctypes.c_void_p]
                u.IsIconic.restype = ctypes.c_int
                u.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.RECT)]
                u.GetWindowRect.restype = ctypes.c_int
                # 最小化窗口没有有效可见区域，PrintWindow/gdigrab 都会失败，提前拦截
                if u.IsIconic(hwnd):
                    messagebox.showwarning(
                        "提示", "所选窗口处于最小化状态，无法录制。\n请先将其还原（不要最小化）后再试。")
                    return None
                r = wt.RECT()
                if not u.GetWindowRect(hwnd, ctypes.byref(r)):
                    messagebox.showwarning("提示", "无法获取所选窗口的矩形区域，录制取消。")
                    return None
                w = r.right - r.left
                h = r.bottom - r.top
                if w <= 0 or h <= 0:
                    messagebox.showwarning("提示", "所选窗口尺寸无效（可能为最小化窗口），录制取消。")
                    return None
                # 尽量把目标窗口带到前台，减少被其他窗口遮挡的观感
                try:
                    u.SetForegroundWindow.argtypes = [ctypes.c_void_p]
                    u.SetForegroundWindow.restype = ctypes.c_int
                    u.BringWindowToTop.argtypes = [ctypes.c_void_p]
                    u.BringWindowToTop.restype = ctypes.c_int
                    u.SetForegroundWindow(hwnd)
                    u.BringWindowToTop(hwnd)
                except Exception:
                    pass

                # 真正“只录指定窗口”：用 PrintWindow 抓窗口自身内容，再以 rawvideo 喂给 ffmpeg。
                # 这不会像 gdigrab 截屏矩形那样把重叠/背景窗口一起录进去。
                self._window_capture_hwnd = hwnd
                self._window_capture_size = (w, h)
                self._window_capture_fps = fps
                self._using_raw_window_capture = True
                self._log(f"指定窗口：{get_window_title(hwnd) or hwnd}，尺寸 {w}x{h}，使用 PrintWindow 抓取")
                cmd = [self.ffmpeg, "-y", "-f", "rawvideo", "-pix_fmt", "bgra",
                       "-video_size", f"{w}x{h}", "-framerate", str(fps), "-i", "-"]
            except Exception as e:
                messagebox.showwarning("提示", f"获取窗口区域失败：{e}")
                return None
        else:
            self._using_raw_window_capture = False
            cmd = [self.ffmpeg, "-y", "-f", "gdigrab", "-framerate", str(fps),
                   "-rtbufsize", "100M", "-draw_mouse", "1"]
            if self.mode.get() == "region" and self.region:
                x, y, w, h = self.region
                cmd += ["-offset_x", str(x), "-offset_y", str(y), "-video_size", f"{w}x{h}"]
            else:
                # 全屏：默认主显示器；full_all 录制所有显示器的虚拟屏幕
                if self.mode.get() == "full_all":
                    x, y, w, h = virtual_screen()
                else:
                    rect = primary_monitor_rect()
                    if rect:
                        x, y, w, h = rect
                    else:
                        w = self.root.winfo_screenwidth()
                        h = self.root.winfo_screenheight()
                        x = y = 0
                if w <= 0 or h <= 0:
                    w, h = 1920, 1080
                cmd += ["-offset_x", str(x), "-offset_y", str(y), "-video_size", f"{w}x{h}"]
            cmd += ["-i", "desktop"]
        if audio_dev == "无音频" or ext == "gif":
            # GIF 不支持音频轨道，即便用户选了音频也忽略（避免 ffmpeg 报“gif muxer 无音频”）
            cmd += ["-map", "0:v:0"]
        elif audio_dev.startswith("WASAPI: "):
            # WASAPI 系统声音捕获：需含 --enable-wasapi 的 ffmpeg；
            # 选中“播放设备”即捕获其 loopback（正在播放的声音），无需开启立体声混音
            was_dev = audio_dev[len("WASAPI: "):]
            cmd += ["-f", "wasapi", "-i", was_dev]
            cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac"]
        else:
            real_audio = audio_dev
            if audio_dev == "系统声音（立体声混音）":
                real_audio = self._resolve_system_audio_device()
                if not real_audio:
                    messagebox.showwarning(
                        "未检测到“立体声混音”",
                        "本机没有可用的“立体声混音 (Stereo Mix)”设备，无法直接录制系统声音。\n\n"
                        "请按以下步骤开启（一次性设置，之后即可使用）：\n"
                        "  1) 右键任务栏右侧的音量图标 →“声音设置”\n"
                        "  2) 右侧“更多声音设置”→ 切换到“录制”选项卡\n"
                        "  3) 在空白处右键 → 勾选“显示禁用的设备”\n"
                        "  4) 若出现了“立体声混音”，右键→“启用”；若没有，说明声卡驱动未提供该功能\n\n"
                        "启用后重新打开本程序，在下拉框选“系统声音（立体声混音）”即可。\n"
                        "（注：部分笔记本/外接声卡不提供立体声混音，此类机型暂不支持系统声音录制。）")
                    return None
            cmd += ["-f", "dshow", "-i", f"audio={real_audio}"]
            cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac"]

        if ext == "gif":
            # GIF 动图：单趟滤镜链 split→palettegen→paletteuse 生成并应用调色板，
            # 同时把尺寸缩放到偶数（GIF 不要求 yuv420p，也不走 libx264）
            fps_gif = min(fps, 25)  # 限帧率，避免 GIF 体积失控
            cmd += ["-vf",
                    f"fps={fps_gif},scale='trunc(iw/2)*2':'trunc(ih/2)*2':flags=lanczos,"
                    f"split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse",
                    "-f", "gif"]
        else:
            # 优先使用硬件编码；不可用时回退 libx264。yuv420p + 偶数裁剪保证兼容性
            cmd += self._video_encoder_args(q)
            cmd += ["-pix_fmt", "yuv420p",
                    "-vf", "crop=trunc(iw/2)*2:trunc(ih/2)*2"]
            if ext == "mp4":
                cmd += ["-movflags", "+faststart"]
        cmd += [out_path]
        return cmd

    def _make_kill_job(self):
        """创建“关闭即杀”的 Job Object（JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE）。
        把 ffmpeg 子进程挂到它上面后，主进程退出（含强杀/崩溃，OS 会关闭 Job 句柄）
        即自动结束 ffmpeg，杜绝孤儿抓屏进程在后台吃 CPU 导致鼠标闪烁。"""
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
                    ("LimitFlags", ctypes.c_uint32),
                    ("MinimumWorkingSetSize", ctypes.c_void_p),
                    ("MaximumWorkingSetSize", ctypes.c_void_p),
                    ("ActiveProcessLimit", ctypes.c_uint32),
                    ("Affinity", ctypes.c_void_p),
                    ("PriorityClass", ctypes.c_uint32),
                    ("SchedulingClass", ctypes.c_uint32),
                ]

            class _EXT(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", _BASIC),
                    ("IoInfo", ctypes.c_byte * 48),
                    ("ProcessMemoryLimit", ctypes.c_void_p),
                    ("JobMemoryLimit", ctypes.c_void_p),
                    ("PeakProcessMemoryUsed", ctypes.c_void_p),
                    ("PeakJobMemoryUsed", ctypes.c_void_p),
                ]

            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
            ext = _EXT()
            ext.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not k.SetInformationJobObject(job, 9, ctypes.byref(ext), ctypes.sizeof(ext)):
                k.CloseHandle(job)
                return None
            return job
        except Exception:
            return None

    def _attach_ffmpeg_to_job(self, proc):
        """把已启动的 ffmpeg 子进程加入 Job Object（失败则忽略，不回退）。"""
        job = getattr(self, "_ffmpeg_job", None)
        if not job or not proc:
            return
        try:
            k = ctypes.windll.kernel32
            k.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
            k.OpenProcess.restype = ctypes.c_void_p
            k.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            k.AssignProcessToJobObject.restype = ctypes.c_int
            h = k.OpenProcess(0x1F0FFF, False, proc.pid)  # PROCESS_ALL_ACCESS
            if h:
                k.AssignProcessToJobObject(job, h)
                k.CloseHandle(h)
        except Exception:
            pass

    def start_record(self):
        if getattr(self, "_finalizing", False):
            messagebox.showinfo("提示", "正在处理上一次录制，请稍候…")
            return
        if not self.ffmpeg:
            messagebox.showerror("提示", "ffmpeg 尚未就绪：请等待自动下载完成，或手动将 ffmpeg.exe 放到程序目录后重启。")
            return
        if self.mode.get() == "region" and not self.region:
            messagebox.showwarning("提示", "请先选择录制区域。")
            return
        if self.mode.get() == "window" and not getattr(self, "_win_hwnd", None):
            messagebox.showwarning("提示", "请先在“指定窗口”下拉框中选择一个窗口。")
            return
        # WASAPI 捕获前先确认当前 ffmpeg 确实提供了 WASAPI 设备，否则给出明确提示
        if self.audio_var.get().startswith("WASAPI: ") and not getattr(self, "_wasapi_devices", []):
            messagebox.showwarning(
                "提示",
                "当前 ffmpeg 未提供 WASAPI 设备，无法使用 WASAPI 系统声音捕获。\n"
                "请更换为含 --enable-wasapi 的 ffmpeg 构建：把 ffmpeg.exe 及其 DLL 放到程序目录的 "
                "ScreenRecorder_app\\_internal\\ 下（覆盖原文件）即可。")
            return
        save_dir = self.save_dir.get()
        try:
            os.makedirs(save_dir, exist_ok=True)
        except Exception:
            save_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        # 磁盘空间预检，避免录到一半磁盘满导致文件损坏
        try:
            free = shutil.disk_usage(save_dir).free
            if free < 200 * 1024 * 1024:
                messagebox.showwarning(
                    "磁盘空间不足",
                    f"保存位置剩余空间不足 200MB（当前约 {self._fmt_size(free)}），请清理磁盘或更换保存目录。")
                return
        except Exception:
            pass
        ts = time.strftime("%Y%m%d_%H%M%S")
        ext = FORMAT_OPTIONS.get(self.fmt_var.get(), "mp4")
        # “近无损 (CRF 1)”仍使用 High profile + yuv420p，兼容 MP4/MOV/AVI/MKV，
        # 不会像 CRF 0 那样强制 High 4:4:4 Predictive 导致多数播放器黑屏。
        prefix = self._safe_prefix(self.prefix.get())
        out_path = os.path.join(save_dir, f"{prefix}_{ts}.{ext}")

        # MP4/MOV/AVI 先录 MKV 中间文件，异常中断时更不容易损坏；结束后再无损 remux
        if ext not in ("mkv", "gif"):
            raw_path = os.path.join(save_dir, f".{prefix}_{ts}_raw.mkv")
            self._raw_path = raw_path
            cmd_path = raw_path
        else:
            self._raw_path = None
            cmd_path = out_path
        self._write_path = cmd_path  # 录制中实际被写入的文件，供状态栏实时大小读取

        try:
            cmd = self._build_command(cmd_path)
        except Exception as e:
            messagebox.showerror("错误", f"构建录制命令失败：{e}")
            return
        if cmd is None:
            return

        # 立刻进入“录制中”视觉状态，并隐藏窗口到系统任务栏
        self.recording = True
        self._out_path = out_path
        self._last_out = None
        self._log(f"开始录制，目标={out_path}，实际写入={cmd_path}")
        # 新一次录制：先禁用上次的“预览/打开文件夹”按钮，防止误开旧文件
        if getattr(self, "btn_preview", None):
            self.btn_preview.config(state="disabled")
            self.btn_openfolder.config(state="disabled")
        self._ffmpeg_log = []
        self._draw_button(idle=False)
        self.status.config(text="准备录制…即将开始", foreground=RED_DARK)
        self._hide_window()
        if self.show_float_var.get():
            self._show_float_window()

        # 延迟（可选 3-2-1 倒计时）再真正开始录制，避免把“窗口收起”的过程也录进去
        if getattr(self, "_start_after_id", None):
            try:
                self.root.after_cancel(self._start_after_id)
            except Exception:
                pass
        self._start_delayed(cmd)

    def _start_delayed(self, cmd):
        """按“开始延迟”选项做延时（>=1 秒显示 3-2-1 倒计时），结束后真正开始录制。"""
        try:
            sec = float(DELAY_OPTIONS.get(self.delay_var.get(), 1.0))
        except Exception:
            sec = 1.0
        if sec <= 0:
            self._really_start(cmd)
            return
        # 0.5 秒：不逐秒倒计时，仅短暂延迟
        if sec < 1:
            self.status.config(text="即将开始录制…", foreground=RED_DARK)
            self._start_after_id = self.root.after(
                int(sec * 1000), lambda: self._really_start(cmd))
            return
        self._countdown(int(round(sec)), cmd)

    def _countdown(self, n, cmd):
        if not self.recording:
            return
        if n > 0:
            self.status.config(text=f"{n} · 即将开始录制", foreground=RED_DARK)
            self._start_after_id = self.root.after(
                1000, lambda: self._countdown(n - 1, cmd))
        else:
            self._really_start(cmd)

    def _really_start(self, cmd):
        self._start_after_id = None
        if not self.recording:
            return  # 延迟期间已被用户取消
        try:
            self._ffmpeg_log = []
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW)
            self._log_thread = threading.Thread(target=self._pump_log, daemon=True)
            self._log_thread.start()
            # 纳入 Job Object：主进程退出即由系统自动结束 ffmpeg（防孤儿进程）
            self._attach_ffmpeg_to_job(self.proc)
            self._log("ffmpeg 已启动: " + " ".join(cmd))
            # 指定窗口模式：启动 PrintWindow 抓帧线程，把窗口自身内容喂给 ffmpeg
            if self._using_raw_window_capture:
                self._window_capture_thread = threading.Thread(
                    target=self._window_capture_worker,
                    args=(self.proc, self._window_capture_hwnd,
                          self._window_capture_size, self._window_capture_fps),
                    daemon=True)
                self._window_capture_thread.start()
        except FileNotFoundError:
            self.recording = False
            self._draw_button(idle=True)
            self._hide_float_window()
            self._show_window()
            messagebox.showerror("错误", f"未找到 ffmpeg：{self.ffmpeg}\n请将 ffmpeg.exe 放在程序同目录，或安装 ffmpeg。")
            return
        except Exception as e:
            self.recording = False
            self._draw_button(idle=True)
            self._hide_float_window()
            self._show_window()
            messagebox.showerror("错误", f"启动失败：{e}")
            return

        self.start_time = time.time()
        self._finalizing = False
        self.status.config(text=f"录制中 → {os.path.basename(self._out_path)}", foreground=RED_DARK)
        # 主线程定时刷新计时/文件大小，避免后台线程直接操作 Tk
        self._timer_after_id = self.root.after(500, self._update_timer)
        # 监控 ffmpeg 是否异常退出
        self._monitor_thread = threading.Thread(target=self._monitor_ffmpeg, daemon=True)
        self._monitor_thread.start()

    def _pump_log(self):
        """分块读取 stderr，避免 readline 在 ffmpeg 进度条（\\r 无换行）时阻塞管道。"""
        try:
            self._log_buffer = ""
            while True:
                chunk = self.proc.stderr.read(4096)
                if not chunk:
                    break
                self._log_buffer += chunk.decode(errors="ignore")
                # 防止无换行的进度输出把缓冲区撑得过大
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
        """PrintWindow 抓帧线程：把指定窗口的 BGRA 原始帧写入 ffmpeg stdin。"""
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
                    self._log("指定窗口抓帧连续失败，停止喂帧")
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
                # rawvideo 输入中途不能改分辨率；窗口尺寸变化时跳过该帧，等待恢复
                if not size_warned:
                    self._log(f"窗口尺寸变化：{fw}x{fh} != {w}x{h}，已跳过变化帧（建议录制期间不要缩放窗口）")
                    size_warned = True
                time.sleep(0.02)
                continue
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

    def stop_record(self):
        # 统一清除底部“实时大小”显示（所有停止路径都会经过这里）
        try:
            self.size_live.config(text="")
            self._write_path = None
        except Exception:
            pass
        if not self.recording:
            return
        if self._finalizing:
            return

        # 尚未真正开始（仍在延迟等待中）：取消即可，无需杀进程
        if self.proc is None and getattr(self, "_start_after_id", None):
            try:
                self.root.after_cancel(self._start_after_id)
            except Exception:
                pass
            self._start_after_id = None
            self.recording = False
            self._draw_button(idle=True)
            self._hide_float_window()
            self._show_window()
            self.status.config(text="已取消录制", foreground=SUB)
            return

        # 异常边界：recording=True 但既无 proc 也无延迟任务，直接复位
        if self.proc is None:
            self.recording = False
            self._draw_button(idle=True)
            self._hide_float_window()
            self._show_window()
            self.status.config(text="录制状态异常，已重置", foreground=SUB)
            return

        self._finalizing = True
        self._hide_float_window()

        # 取消主线程计时器
        if getattr(self, "_timer_after_id", None):
            try:
                self.root.after_cancel(self._timer_after_id)
            except Exception:
                pass
            self._timer_after_id = None

        proc = self.proc
        # 优雅退出：gdigrab 模式向 stdin 发送 q；rawvideo 模式 stdin 是视频输入，
        # 不能写 q（会污染帧数据），直接关闭 stdin 表示 EOF。
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
                # 如果仍无法结束，使用 taskkill 强制结束进程树
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
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
        self._draw_button(idle=True)
        dur = int(time.time() - self.start_time)
        self._show_window()
        self.status.config(text="正在校验录制文件…", foreground=SUB)
        final_path = self._out_path
        raw_path = getattr(self, "_raw_path", None)
        verify_path = raw_path or final_path
        self.proc = None
        self._log(f"停止录制，校验文件={verify_path}，最终文件={final_path}")

        # 校验放到后台线程，避免长时间录制的全片校验卡住 UI
        def verify_worker():
            ok, reason = self._verify_output(verify_path)
            self._post_ui(lambda: self._on_verify_done(ok, reason, verify_path, final_path, raw_path, dur))

        threading.Thread(target=verify_worker, daemon=True).start()
        # 注意：_finalizing 保持 True 直到校验/转换完成，防止用户立刻开始下一次录制

    def _on_verify_done(self, ok, reason, verify_path, final_path, raw_path, dur):
        if not ok:
            self._finalizing = False
            self._last_out = None
            self._log(f"录制校验失败：{reason}")
            self._show_ffmpeg_error(verify_path, reason)
            return

        # 没有中间文件（MKV/GIF 直接输出），直接完成
        if not raw_path or raw_path == final_path:
            self._finalizing = False
            self._last_out = final_path
            self.status.config(text=f"已完成：{os.path.basename(final_path)} （{dur}秒）",
                               foreground="#1d9b4e")
            if getattr(self, "btn_preview", None):
                self.btn_preview.config(state="normal")
                self.btn_openfolder.config(state="normal")
            self._log(f"录制完成：{final_path}")
            self._maybe_auto_open(final_path)
            return

        # 有 MKV 中间文件：后台无损 remux 到用户选择的格式
        self.status.config(text="正在转换封装格式…", foreground=SUB)

        def remux_worker():
            success, err = self._remux_to_final(raw_path, final_path)
            self._post_ui(lambda: self._on_remux_done(success, err, raw_path, final_path, dur))

        threading.Thread(target=remux_worker, daemon=True).start()

    def _remux_to_final(self, raw_path, final_path):
        """将 MKV 中间文件无损 remux 为最终封装格式。"""
        try:
            cmd = [self.ffmpeg, "-y", "-i", raw_path, "-map", "0", "-c", "copy"]
            ext = os.path.splitext(final_path)[1].lstrip('.').lower()
            if ext in ("mp4", "mov"):
                cmd += ["-movflags", "+faststart"]
            cmd += [final_path]
            r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               creationflags=CREATE_NO_WINDOW, timeout=120)
            if r.returncode != 0:
                tail = r.stderr.decode(errors="ignore")[-500:]
                return False, tail
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
            self._last_out = final_path
            self.status.config(text=f"已完成：{os.path.basename(final_path)} （{dur}秒）",
                               foreground="#1d9b4e")
            if getattr(self, "btn_preview", None):
                self.btn_preview.config(state="normal")
                self.btn_openfolder.config(state="normal")
            self._log(f"转换封装完成：{final_path}")
            self._maybe_auto_open(final_path)
        else:
            self._last_out = raw_path
            self._log(f"转换封装失败：{err}")
            if getattr(self, "btn_preview", None):
                self.btn_preview.config(state="normal")
                self.btn_openfolder.config(state="normal")
            messagebox.showerror(
                "转换失败",
                f"录制内容已生成，但转换成 {os.path.basename(final_path)} 失败。\n"
                f"原始 MKV 已保留：\n{raw_path}\n\n错误信息：\n{err[-500:] if err else '未知'}")
            self.status.config(text="录制成功，但格式转换失败（MKV 已保留）", foreground=RED_DARK)

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
            # 快速校验：-c copy 只解封装不重编码，避免长时间录制的全片解码卡顿
            v = subprocess.run(
                [self.ffmpeg, "-v", "error", "-i", path, "-c", "copy", "-f", "null", "-"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW, timeout=20)
            if v.returncode != 0:
                return False, "文件无法被 ffmpeg 正常解析（可能损坏或不完整）"
        except subprocess.TimeoutExpired:
            return False, "文件校验超时，可能仍在写入或已损坏"
        except Exception:
            return False, "文件校验失败"
        return True, ""

    def _show_ffmpeg_error(self, path, reason):
        keywords = ("Error", "Invalid", "error", "failed", "Cannot", "could not",
                    "Denied", "Unable", "No such", "not found", "Permission")
        err_lines = [l for l in self._ffmpeg_log if any(k in l for k in keywords)]
        if not err_lines:
            err_lines = self._ffmpeg_log[-15:]
        tail = "\n".join(err_lines[-15:]) or "（ffmpeg 未输出错误信息）"
        self.status.config(text="录制失败，详见弹窗", foreground=RED_DARK)
        messagebox.showerror(
            "录制失败",
            f"{reason}\n\n文件：{path}\n\nffmpeg 关键日志：\n{tail}\n\n"
            "常见排查：\n"
            "· 保存路径是否有写入权限（可改用桌面“录屏”文件夹）\n"
            "· 是否同时选了不存在的音频设备\n"
            "· 以管理员身份运行本程序再试一次")

    def _update_timer(self):
        """主线程定时刷新计时/文件大小。"""
        if not self.recording:
            # 录制已结束：清空白时大小，避免残留
            try:
                self.size_live.config(text="")
            except Exception:
                pass
            self._timer_after_id = None
            return
        try:
            elapsed = int(time.time() - self.start_time)
            h = elapsed // 3600
            m = (elapsed % 3600) // 60
            s = elapsed % 60
            t = f"{h:02d}:{m:02d}:{s:02d}"
            size_str = ""
            live_path = getattr(self, "_write_path", None) or self._out_path
            if live_path and os.path.exists(live_path):
                try:
                    size_str = self._fmt_size(os.path.getsize(live_path))
                except Exception:
                    size_str = ""
            fname = os.path.basename(self._out_path) if self._out_path else ""
            self.timer.config(text=t)
            self.size_live.config(text=size_str)
            self.status.config(text=f"录制中 → {fname}")
            self._update_float_window()
        except Exception:
            pass
        try:
            self._timer_after_id = self.root.after(500, self._update_timer)
        except Exception:
            self._timer_after_id = None

    def _monitor_ffmpeg(self):
        """后台监控 ffmpeg 是否异常退出。"""
        proc = self.proc
        try:
            proc.wait()
        except Exception:
            return
        # 只有非用户主动停止时处理；stop_record 会设置 _finalizing
        if self.recording and not self._finalizing:
            self._post_ui(lambda: self._handle_ffmpeg_crash(proc))

    def _handle_ffmpeg_crash(self, proc):
        if not self.recording or self._finalizing:
            return
        if proc is not self.proc:
            return
        self._log(f"ffmpeg 异常退出，returncode={proc.returncode}")
        self.status.config(text="ffmpeg 异常退出，正在处理…", foreground=RED_DARK)
        self.stop_record()

    def shutdown(self):
        if self.recording:
            try:
                self.stop_record()
            except Exception:
                pass
        self._hide_float_window()
        try:
            if getattr(self, "hotkey", None):
                self.hotkey.stop()
        except Exception:
            pass


def main():
    root = tk.Tk()
    app = ScreenRecorderApp(root)

    def on_close():
        app.shutdown()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
