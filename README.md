# ScreenRecorder

Windows 轻量屏幕录像工具，macOS 风格 UI，基于 **PySide6 (Qt for Python) + ffmpeg 管道** 实现。

## 功能特性

- 录制范围：全屏（主显示器 / 所有显示器）、自定义区域、指定窗口
- 输出格式：MP4 / MOV / MKV / GIF
- 音频：系统声音（WASAPI / dshow 立体声混音）、麦克风、系统 + 麦克风混音
- 实时音频电平条（FFmpeg `astats` 链路驱动，含麦克风降噪与静音告警）
- 录制圆环（呼吸动画）、系统托盘（右键菜单）、全局快捷键
- 高 DPI 清晰文字、配置文件持久化（LocalAppData）

## 环境要求

- Windows 10/11
- Python 3.13 + PySide6 6.22.1（开发/调试用 venv）
- `ffmpeg.exe`（9.0.1 静态构建，**因体积 102MB 不纳入仓库**，需自行放到项目根目录）

## 快速开始

### 运行源码（开发调试）
双击 `run_pyside6.bat`（使用 pyside6 venv 直接运行，改完即看）。

### 打包为 exe（发布）
双击 `build_pyside6.bat`，使用 PyInstaller 打包为单文件 `ScreenRecorder_app/ScreenRecorder.exe`。

> 注：本仓库不含 `ffmpeg.exe` 与打包产物（`build/`、`dist/`、`ScreenRecorder_app/` 等已在 `.gitignore` 中排除）。

## 目录说明

```
screen-recorder/
├── screen_recorder_pyside6_v2.py   # ★ 当前主源码（修改这里）
├── run_pyside6.bat                 # 双击运行源码
├── build_pyside6.bat               # 双击打包
├── ScreenRecorder.spec             # PyInstaller spec
├── assets/                         # 图标资源
├── 协作进度.md                      # 协作改进记录
└── .gitignore
```

## 已知事项

- 源码中部分路径为开发机绝对路径（如 `C:\Users\a3564\...`），在其他机器运行前按需调整为相对路径或配置项。
- 主源码演进过程中保留了若干历史版本（`screen_recorder.py`、`screen_recorder_pyside6.py` 等）作为参考备份。

## 许可证

[MIT](LICENSE) © 2026 苏浅浅
