# 杀戮尖塔2 画板（macOS）

这是 Windows 版画图辅助工具的 macOS 完整平替版本。

整体流程保持一致：
- 图片或文字转线稿
- 线稿预览并可选裁剪
- 进入全屏“数字琥珀”选区模式
- 右键连笔按轮廓自动绘制
- 任意时刻按全局 `P` 键紧急停止

## 功能特性

- 基于 Tkinter 的桌面界面，支持实时线稿预览
- 三种线稿来源：
  - 外部图片 -> 边缘线稿
  - 输入文字 -> 自适应包围盒线稿
  - 加载已有线稿文件
- 支持当前线稿二次裁剪
- 配置自动记忆（`output_lines/config.json`）：
  - 窗口置顶
  - 线稿精细度
  - 绘制速度
- 基于 macOS Quartz 的底层鼠标事件模拟
- 基于全局 Event Tap 的 `P` 键急停

## 运行环境

- macOS 12+
- Python 3.10+
- 建议安装 Xcode Command Line Tools

安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

## 从源码运行

```bash
python3 spire_painter_mac.py
```

## 必需的 macOS 权限

本工具需要以下三个权限：

1. 辅助功能（Accessibility）
- 用于注入鼠标事件进行绘制。
- 路径：系统设置 -> 隐私与安全性 -> 辅助功能

2. 输入监控（Input Monitoring）
- 用于全局 `P` 键急停。
- 路径：系统设置 -> 隐私与安全性 -> 输入监控

3. 屏幕录制（Screen Recording）
- 用于“数字琥珀”全屏截图。
- 路径：系统设置 -> 隐私与安全性 -> 屏幕录制

如果你在程序运行时才授予权限，请完全退出后重新打开应用。

## 打包为 App（Universal2）

```bash
bash build_mac.sh
```

打包输出：

- `dist/SlaytheSpire2DrawingMac.app`

说明：
- 当前项目不包含代码签名与 Apple Notarization（公证）流程。
- 未签名应用首次启动可能触发 Gatekeeper 提示。

## 常见问题

1. 全局 `P` 不生效
- 重新检查“输入监控 + 辅助功能”权限。
- 必要时删除并重新添加终端/Python 的授权项。

2. 程序显示在绘制，但目标应用没有线条
- 某些应用/游戏会忽略模拟输入事件。
- 降低速度滑块（建议 2-4）后重试。

3. 无法进入暗场全屏选区
- 通常是“屏幕录制”权限未授权导致。

4. 文字线稿字体和预期不一致
- 所选字体不存在时会自动切换到后备字体。

## 项目文件

- `spire_painter_mac.py`：macOS 主程序
- `requirements.txt`：运行依赖
- `build_mac.sh`：Universal2 打包脚本
- `output_lines/`：线稿输出与配置目录
