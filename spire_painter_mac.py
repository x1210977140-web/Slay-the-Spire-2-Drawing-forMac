import os
import sys
import time
import json
import threading
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk, ImageGrab, ImageDraw, ImageFont, ImageEnhance
import cv2
import numpy as np

try:
    import Quartz
except Exception:
    Quartz = None

try:
    import objc
except Exception:
    objc = None

try:
    from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
except Exception:
    AXIsProcessTrustedWithOptions = None
    kAXTrustedCheckOptionPrompt = None


OUTPUT_DIR = "output_lines"
CONFIG_FILE = "config.json"
MIN_SELECT_SIZE = 10


# -----------------------------------------------------------------------------
# Permission and input helpers
# -----------------------------------------------------------------------------
def check_accessibility_permission(prompt):
    if AXIsProcessTrustedWithOptions is None or kAXTrustedCheckOptionPrompt is None:
        return False
    try:
        return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: bool(prompt)}))
    except Exception:
        return False


def _post_mouse_event(event_type, x, y, button):
    if Quartz is None:
        return
    event = Quartz.CGEventCreateMouseEvent(None, event_type, (float(x), float(y)), button)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def move_mouse(x, y, dragging=False):
    if Quartz is None:
        return
    if dragging:
        _post_mouse_event(
            Quartz.kCGEventRightMouseDragged,
            x,
            y,
            Quartz.kCGMouseButtonRight,
        )
    else:
        _post_mouse_event(
            Quartz.kCGEventMouseMoved,
            x,
            y,
            Quartz.kCGMouseButtonLeft,
        )


def right_click_down(x, y):
    if Quartz is None:
        return
    _post_mouse_event(Quartz.kCGEventRightMouseDown, x, y, Quartz.kCGMouseButtonRight)


def right_click_up(x, y):
    if Quartz is None:
        return
    _post_mouse_event(Quartz.kCGEventRightMouseUp, x, y, Quartz.kCGMouseButtonRight)


class GlobalAbortListener:
    """基于 macOS Event Tap 的全局按键监听器。"""

    KEYCODE_P = 35

    def __init__(self, abort_event):
        self.abort_event = abort_event
        self.tap = None
        self.source = None
        self.run_loop = None
        self.thread = None
        self.running = False
        self.ready = threading.Event()
        self.failed_reason = ""

    def start(self):
        if self.running:
            return True
        if Quartz is None:
            self.failed_reason = "Quartz 不可用，请先安装 pyobjc Quartz 相关依赖。"
            return False

        self.ready.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.ready.wait(timeout=2.0)
        return self.running

    def stop(self):
        if Quartz is None:
            return
        if self.run_loop is not None:
            Quartz.CFRunLoopStop(self.run_loop)
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)
        self.running = False

    def _run(self):
        if objc is not None:
            with objc.autorelease_pool():
                self._run_with_pool()
            return
        self._run_with_pool()

    def _run_with_pool(self):
        self.failed_reason = ""
        mask = Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)

        self.tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            mask,
            self._event_callback,
            None,
        )

        if self.tap is None:
            self.failed_reason = (
                "无法创建全局键盘监听。"
                "请授予输入监控和辅助功能权限。"
            )
            self.running = False
            self.ready.set()
            return

        self.source = Quartz.CFMachPortCreateRunLoopSource(None, self.tap, 0)
        self.run_loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(self.run_loop, self.source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self.tap, True)

        self.running = True
        self.ready.set()

        Quartz.CFRunLoopRun()

        self.running = False
        if self.tap is not None:
            Quartz.CFMachPortInvalidate(self.tap)
            self.tap = None

    def _event_callback(self, _proxy, event_type, event, _refcon):
        if objc is not None:
            with objc.autorelease_pool():
                return self._event_callback_inner(event_type, event)
        return self._event_callback_inner(event_type, event)

    def _event_callback_inner(self, event_type, event):
        if event_type == Quartz.kCGEventKeyDown and self._is_abort_key(event):
            self.abort_event.set()
            print("[中断] 收到全局 P 键，正在停止绘制。")
        return event

    def _is_abort_key(self, event):
        key_text = self._extract_key_text(event)
        if key_text and key_text.lower() == "p":
            return True

        keycode = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
        return int(keycode) == self.KEYCODE_P

    def _extract_key_text(self, event):
        if Quartz is None:
            return ""
        try:
            _, text = Quartz.CGEventKeyboardGetUnicodeString(event, 4, None, None)
            if isinstance(text, str) and text:
                return text[0]
        except Exception:
            pass
        return ""


# -----------------------------------------------------------------------------
# Crop overlay
# -----------------------------------------------------------------------------
class CropOverlay:
    def __init__(self, master, img_path, callback, cancel_callback):
        self.top = tk.Toplevel(master)
        self.top.title("裁剪线稿（按住拖拽，松开完成）")
        self.top.attributes("-topmost", True)
        self.callback = callback
        self.cancel_callback = cancel_callback
        self.img_path = img_path

        self.original_pil = Image.open(img_path)
        self.display_pil = self.original_pil.copy()

        max_display_size = (1000, 800)
        self.display_pil.thumbnail(max_display_size, Image.Resampling.LANCZOS)

        self.scale_x = self.original_pil.width / max(self.display_pil.width, 1)
        self.scale_y = self.original_pil.height / max(self.display_pil.height, 1)

        self.tk_img = ImageTk.PhotoImage(self.display_pil)

        width = self.display_pil.width
        height = self.display_pil.height
        screen_w = master.winfo_screenwidth()
        screen_h = master.winfo_screenheight()
        center_x = int((screen_w / 2) - (width / 2))
        center_y = int((screen_h / 2) - (height / 2))
        self.top.geometry(f"{width}x{height}+{center_x}+{center_y}")

        self.canvas = tk.Canvas(self.top, width=width, height=height, cursor="crosshair")
        self.canvas.pack()
        self.canvas.create_image(0, 0, image=self.tk_img, anchor=tk.NW)

        self.rect_id = None
        self.start_x = None
        self.start_y = None

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.top.bind("<Escape>", self.on_cancel)
        self.top.protocol("WM_DELETE_WINDOW", self.on_cancel)

    def on_press(self, event):
        self.start_x = event.x
        self.start_y = event.y
        if self.rect_id:
            self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(
            self.start_x,
            self.start_y,
            self.start_x,
            self.start_y,
            outline="blue",
            width=2,
            dash=(4, 4),
        )

    def on_drag(self, event):
        if self.rect_id:
            self.canvas.coords(self.rect_id, self.start_x, self.start_y, event.x, event.y)

    def on_release(self, event):
        if self.start_x is None or self.start_y is None:
            self.on_cancel(None)
            return

        end_x, end_y = event.x, event.y
        rx = min(self.start_x, end_x)
        ry = min(self.start_y, end_y)
        rw = abs(self.start_x - end_x)
        rh = abs(self.start_y - end_y)

        self.top.destroy()

        if rw <= MIN_SELECT_SIZE or rh <= MIN_SELECT_SIZE:
            self.cancel_callback()
            return

        orig_x = int(rx * self.scale_x)
        orig_y = int(ry * self.scale_y)
        orig_w = int(rw * self.scale_x)
        orig_h = int(rh * self.scale_y)

        cropped = self.original_pil.crop((orig_x, orig_y, orig_x + orig_w, orig_y + orig_h))
        output_dir = os.path.dirname(self.img_path)
        timestamp = int(time.time())
        new_path = os.path.join(output_dir, f"cropped_lineart_{timestamp}.png")
        cropped.save(new_path)
        self.callback(new_path)

    def on_cancel(self, _event):
        self.top.destroy()
        self.cancel_callback()


# -----------------------------------------------------------------------------
# Full-screen selection overlay (digital amber)
# -----------------------------------------------------------------------------
class DigitalAmberOverlay:
    def __init__(self, master, target_image_path, callback, cancel_callback):
        self.master = master
        self.target_image_path = target_image_path
        self.callback = callback
        self.cancel_callback = cancel_callback
        self.ready = False

        self.top = tk.Toplevel(master)
        self.top.attributes("-fullscreen", True)
        self.top.attributes("-topmost", True)
        self.top.config(cursor="crosshair")

        self.display_w = max(master.winfo_screenwidth(), 1)
        self.display_h = max(master.winfo_screenheight(), 1)

        try:
            screen_img = ImageGrab.grab(all_screens=True)
        except Exception:
            self.top.destroy()
            messagebox.showerror(
                "屏幕捕获受限",
                "无法截取屏幕。\n"
                "请在 系统设置 -> 隐私与安全性 中授予屏幕录制权限。",
                parent=master,
            )
            self.cancel_callback()
            return

        self.scale_x = screen_img.width / self.display_w
        self.scale_y = screen_img.height / self.display_h

        if screen_img.size != (self.display_w, self.display_h):
            screen_img = screen_img.resize((self.display_w, self.display_h), Image.Resampling.LANCZOS)

        enhancer = ImageEnhance.Brightness(screen_img)
        self.dimmed_img = enhancer.enhance(0.5)

        self.tk_img = ImageTk.PhotoImage(self.dimmed_img)

        self.canvas = tk.Canvas(self.top, width=self.display_w, height=self.display_h, highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.create_image(0, 0, image=self.tk_img, anchor=tk.NW)

        self.rect_id = None
        self.start_x = None
        self.start_y = None

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.top.bind("<Escape>", self.on_cancel)
        self.top.protocol("WM_DELETE_WINDOW", self.on_cancel)

        self.ready = True

    def on_press(self, event):
        self.start_x = event.x
        self.start_y = event.y
        if self.rect_id:
            self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(
            self.start_x,
            self.start_y,
            self.start_x,
            self.start_y,
            outline="red",
            width=2,
        )

    def on_drag(self, event):
        if self.rect_id:
            self.canvas.coords(self.rect_id, self.start_x, self.start_y, event.x, event.y)

    def on_release(self, event):
        if self.start_x is None or self.start_y is None:
            self.on_cancel(None)
            return

        end_x, end_y = event.x, event.y
        rx = min(self.start_x, end_x)
        ry = min(self.start_y, end_y)
        rw = abs(self.start_x - end_x)
        rh = abs(self.start_y - end_y)

        self.top.destroy()

        if rw <= MIN_SELECT_SIZE or rh <= MIN_SELECT_SIZE:
            self.cancel_callback()
            return

        # Convert display-space coordinates back to source screenshot space.
        src_rx = int(rx * self.scale_x)
        src_ry = int(ry * self.scale_y)
        src_rw = int(rw * self.scale_x)
        src_rh = int(rh * self.scale_y)

        self.callback(src_rx, src_ry, src_rw, src_rh, self.target_image_path)

    def on_cancel(self, _event):
        self.top.destroy()
        self.cancel_callback()


# -----------------------------------------------------------------------------
# Main application
# -----------------------------------------------------------------------------
class SpirePainterMacApp:
    def __init__(self, root):
        self.root = root
        self.root.title("杀戮尖塔2 - 数字琥珀画板（macOS）")

        window_width = 980
        window_height = 680
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        center_x = int((screen_width / 2) - (window_width / 2))
        center_y = int((screen_height / 2) - (window_height / 2))
        self.root.geometry(f"{window_width}x{window_height}+{center_x}+{center_y}")

        self.abort_event = threading.Event()
        self.abort_listener = GlobalAbortListener(self.abort_event)

        self.current_lineart_path = None
        self.last_raw_image_path = None
        self.tk_preview_image = None
        self.output_dir = OUTPUT_DIR
        self.output_abs = os.path.abspath(self.output_dir)
        os.makedirs(self.output_abs, exist_ok=True)

        self.config_path = os.path.join(self.output_abs, CONFIG_FILE)
        init_topmost = False
        init_detail = 5
        init_speed = 3

        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    conf = json.load(f)
                init_detail = int(conf.get("detail", 5))
                init_speed = int(conf.get("speed", 3))
            except Exception:
                pass

        self.topmost_var = tk.BooleanVar(value=init_topmost)
        self.root.attributes("-topmost", self.topmost_var.get())

        self.font_map = {
            "苹方（默认）": ["PingFang.ttc", "PingFang SC.ttc"],
            "冬青黑体简体": ["Hiragino Sans GB.ttc"],
            "宋体 SC": ["Songti.ttc", "STSong.ttc"],
            "黑体 SC": ["STHeiti Light.ttc", "STHeiti Medium.ttc"],
            "Arial Unicode（兼容）": ["Arial Unicode.ttf", "Arial Unicode MS.ttf"],
        }
        self.font_dirs = [
            "/System/Library/Fonts",
            "/System/Library/Fonts/Supplemental",
            "/Library/Fonts",
            os.path.expanduser("~/Library/Fonts"),
        ]

        self._build_ui(init_detail, init_speed)

        if not check_accessibility_permission(prompt=True):
            self._show_warning(
                "需要权限",
                "鼠标控制需要辅助功能权限。\n"
                "系统会弹出授权提示，请允许后按需重启应用。",
            )

        self.status_label.config(
            text=(
                "请先准备线稿。\n"
                "开始绘制前将自动启用全局 P 急停。"
            )
        )

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self, init_detail, init_speed):
        self.left_panel = tk.Frame(self.root, width=430)
        self.left_panel.pack(side="left", fill="y", padx=10, pady=10)
        self.left_panel.pack_propagate(False)

        self.right_panel = tk.Frame(self.root, bg="#E0E0E0", bd=2, relief="sunken")
        self.right_panel.pack(side="right", fill="both", expand=True, padx=(0, 10), pady=10)

        top_bar = tk.Frame(self.left_panel)
        top_bar.pack(fill="x", pady=(5, 15))

        self.status_label = tk.Label(
            top_bar,
            text="请先准备线稿。\n（绘制中可随时按 P 紧急停止）",
            fg="blue",
            justify="left",
        )
        self.status_label.pack(side="left")

        self.chk_topmost = tk.Checkbutton(
            top_bar,
            text="窗口置顶（本次有效）",
            variable=self.topmost_var,
            command=self.save_config,
        )
        self.chk_topmost.pack(side="right", anchor="n", pady=5)

        frame1 = tk.LabelFrame(self.left_panel, text="方案A：外部图片", padx=10, pady=10)
        frame1.pack(fill="x", padx=10, pady=(0, 15))

        detail_frame = tk.Frame(frame1)
        detail_frame.pack(fill="x")
        tk.Label(detail_frame, text="线稿精细度（1低=快，10高=慢）：").pack(side="left")

        self.detail_slider = tk.Scale(detail_frame, from_=1, to=10, orient="horizontal", length=130)
        self.detail_slider.set(init_detail)
        self.detail_slider.config(command=self.save_config)
        self.detail_slider.pack(side="left", padx=5)

        btn_frame1 = tk.Frame(frame1)
        btn_frame1.pack(fill="x", pady=(10, 0))

        self.btn_image = tk.Button(btn_frame1, text="1. 选择图片", command=self.select_image)
        self.btn_image.pack(side="left", fill="x", expand=True, padx=(0, 2))

        self.btn_reprocess = tk.Button(
            btn_frame1,
            text="2. 刷新线稿",
            command=self.generate_image_lineart,
            state=tk.DISABLED,
        )
        self.btn_reprocess.pack(side="left", fill="x", expand=True, padx=(2, 0))

        frame2 = tk.LabelFrame(self.left_panel, text="方案B：输入文字", padx=10, pady=10)
        frame2.pack(fill="x", padx=10, pady=(0, 15))

        self.text_input = tk.Entry(frame2)
        self.text_input.insert(0, "输入想画的文字...")
        self.text_input.pack(fill="x", pady=(0, 8))

        font_frame = tk.Frame(frame2)
        font_frame.pack(fill="x", pady=2)
        tk.Label(font_frame, text="字体风格：").pack(side="left")

        self.font_combo = ttk.Combobox(
            font_frame,
            values=list(self.font_map.keys()),
            state="readonly",
            width=22,
        )
        self.font_combo.current(0)
        self.font_combo.pack(side="left", padx=5)

        self.btn_text = tk.Button(frame2, text="生成文字线稿", command=self.process_text)
        self.btn_text.pack(fill="x", pady=(10, 0))

        frame3 = tk.LabelFrame(self.left_panel, text="方案C：现成线稿", padx=10, pady=10)
        frame3.pack(fill="x", padx=10, pady=(0, 15))

        self.btn_load_existing = tk.Button(
            frame3,
            text="打开已保存线稿",
            command=self.load_existing_lineart,
        )
        self.btn_load_existing.pack(fill="x")

        speed_frame = tk.Frame(self.left_panel)
        speed_frame.pack(fill="x", padx=10, pady=(15, 25))
        tk.Label(speed_frame, text="绘制速度（跳帧步长）：", font=("Arial", 9, "bold")).pack(side="left")

        self.speed_slider = tk.Scale(speed_frame, from_=1, to=15, orient="horizontal", length=200)
        self.speed_slider.set(init_speed)
        self.speed_slider.config(command=self.save_config)
        self.speed_slider.pack(side="left", padx=5)

        self.btn_start = tk.Button(
            self.left_panel,
            text="开始绘制（进入数字琥珀）",
            bg="#4CAF50",
            fg="white",
            font=("Arial", 10, "bold"),
            command=self.start_digital_amber,
            state=tk.DISABLED,
            height=2,
        )
        self.btn_start.pack(fill="x", padx=10, pady=(0, 10))

        tk.Label(
            self.right_panel,
            text="实时线稿预览区",
            font=("Arial", 12, "bold"),
            bg="#E0E0E0",
            fg="#333333",
        ).pack(pady=10)

        self.preview_label = tk.Label(
            self.right_panel,
            text="（暂无预览）\n请在左侧生成或选择线稿",
            bg="white",
            fg="gray",
        )
        self.preview_label.pack(fill="both", expand=True, padx=10, pady=5)

        self.btn_crop = tk.Button(
            self.right_panel,
            text="裁剪当前线稿",
            command=self.start_crop,
            state=tk.DISABLED,
        )
        self.btn_crop.pack(fill="x", padx=10, pady=(0, 5))

        self.btn_open_folder = tk.Button(
            self.right_panel,
            text="打开线稿目录",
            command=self.open_output_folder,
        )
        self.btn_open_folder.pack(fill="x", padx=10, pady=(0, 10))

    def set_status(self, text):
        self.root.after(0, lambda: self.status_label.config(text=text))

    def _run_dialog(self, dialog_callable):
        was_topmost = self.topmost_var.get()
        if was_topmost:
            self.root.attributes("-topmost", False)
            self.root.update_idletasks()
        try:
            return dialog_callable()
        finally:
            if was_topmost and self.topmost_var.get():
                self.root.attributes("-topmost", True)

    def _show_error(self, title, message):
        return self._run_dialog(lambda: messagebox.showerror(title, message, parent=self.root))

    def _show_warning(self, title, message):
        return self._run_dialog(lambda: messagebox.showwarning(title, message, parent=self.root))

    def _show_info(self, title, message):
        return self._run_dialog(lambda: messagebox.showinfo(title, message, parent=self.root))

    def _ask_open_filename(self, **kwargs):
        kwargs.setdefault("parent", self.root)
        return self._run_dialog(lambda: filedialog.askopenfilename(**kwargs))

    def save_config(self, *_args):
        if not hasattr(self, "detail_slider") or not hasattr(self, "speed_slider"):
            return

        is_top = self.topmost_var.get()
        self.root.attributes("-topmost", is_top)

        conf = {
            "detail": int(self.detail_slider.get()),
            "speed": int(self.speed_slider.get()),
        }
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(conf, f)
        except Exception as exc:
            print(f"保存配置失败: {exc}")

    def start_crop(self):
        if not self.current_lineart_path:
            return
        CropOverlay(self.root, self.current_lineart_path, self.finish_crop, self.cancel_crop)

    def finish_crop(self, new_cropped_path):
        self.current_lineart_path = new_cropped_path
        self.set_status(f"已生成裁剪线稿。\n{os.path.basename(new_cropped_path)}")
        self.update_preview_panel(new_cropped_path)

    def cancel_crop(self):
        self.set_status("已取消裁剪。")

    def open_output_folder(self):
        try:
            subprocess.run(["open", self.output_abs], check=False)
        except Exception as exc:
            self._show_error("错误", f"无法打开文件夹: {exc}")

    def update_preview_panel(self, image_path):
        if not image_path or not os.path.exists(image_path):
            return

        try:
            img = Image.open(image_path)
            img.thumbnail((500, 450), Image.Resampling.LANCZOS)
            self.tk_preview_image = ImageTk.PhotoImage(img)
            self.preview_label.config(image=self.tk_preview_image, text="", bg="#E0E0E0")
            self.btn_crop.config(state=tk.NORMAL)
        except Exception as exc:
            print(f"预览加载失败: {exc}")

    def select_image(self):
        file_path = self._ask_open_filename(
            title="选择原图片",
            filetypes=[("图片文件", "*.png *.jpg *.jpeg *.bmp *.webp")],
        )
        if file_path:
            self.last_raw_image_path = file_path
            self.btn_reprocess.config(state=tk.NORMAL)
            self.generate_image_lineart()

    def generate_image_lineart(self):
        if not self.last_raw_image_path:
            return

        img = cv2.imdecode(np.fromfile(self.last_raw_image_path, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if img is None:
            self._show_error("读取失败", "无法读取所选图片。")
            return

        detail = int(self.detail_slider.get())

        k_size = int(max(1, (11 - detail) // 2 * 2 + 1))
        if k_size > 1:
            img = cv2.GaussianBlur(img, (k_size, k_size), 0)

        lower_thresh = int(180 - detail * 15)
        upper_thresh = int(250 - detail * 15)

        edges = cv2.Canny(img, lower_thresh, upper_thresh)
        inverted = cv2.bitwise_not(edges)

        save_path = os.path.join(self.output_abs, "last_image_lineart.png")
        cv2.imencode(".png", inverted)[1].tofile(save_path)

        self.current_lineart_path = save_path
        self.set_status(f"图片线稿已生成。\n当前精细度: {detail}")
        self.btn_start.config(state=tk.NORMAL)
        self.update_preview_panel(save_path)

    def _resolve_font_path(self, selected_key):
        selected_candidates = self.font_map.get(selected_key, [])

        fallback_candidates = []
        for names in self.font_map.values():
            fallback_candidates.extend(names)

        search_order = []
        seen = set()

        for name in selected_candidates + fallback_candidates:
            if name in seen:
                continue
            seen.add(name)
            search_order.append(name)

        for font_name in search_order:
            for font_dir in self.font_dirs:
                font_path = os.path.join(font_dir, font_name)
                if os.path.exists(font_path):
                    selected_hit = font_name in selected_candidates
                    return font_path, selected_hit
        return None, False

    def process_text(self):
        text = self.text_input.get().strip()
        if not text:
            self._show_warning("缺少文字", "请先输入文字。")
            return

        selected_font_name = self.font_combo.get()
        font_path, selected_hit = self._resolve_font_path(selected_font_name)

        if not font_path:
            self._show_error(
                "字体缺失",
                "在 macOS 字体目录中未找到可用中文字体。",
            )
            return

        try:
            font = ImageFont.truetype(font_path, 150)
        except Exception as exc:
            self._show_error("字体读取错误", f"无法加载字体:\n{exc}")
            return

        if not selected_hit:
            self._show_info(
                "字体回退",
                "所选字体不可用。\n已自动使用后备字体。",
            )

        dummy_img = Image.new("RGB", (1, 1), "white")
        dummy_draw = ImageDraw.Draw(dummy_img)
        bbox = dummy_draw.textbbox((0, 0), text, font=font)

        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        padding = 24
        canvas_w = int(text_w + padding * 2)
        canvas_h = int(text_h + padding * 2)

        img = Image.new("RGB", (canvas_w, canvas_h), "white")
        draw = ImageDraw.Draw(img)
        draw_x = padding - bbox[0]
        draw_y = padding - bbox[1]
        draw.text((draw_x, draw_y), text, font=font, fill="black")

        gray = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, 100, 200)
        inverted = cv2.bitwise_not(edges)

        save_path = os.path.join(self.output_abs, "last_text_lineart.png")
        cv2.imencode(".png", inverted)[1].tofile(save_path)

        self.current_lineart_path = save_path
        self.set_status(f"文字线稿已生成。\n字体: {os.path.basename(font_path)}")
        self.btn_start.config(state=tk.NORMAL)
        self.update_preview_panel(save_path)

    def load_existing_lineart(self):
        file_path = self._ask_open_filename(
            initialdir=self.output_abs,
            title="选择已保存线稿",
            filetypes=[("图片文件", "*.png *.jpg *.jpeg *.bmp *.webp")],
        )
        if file_path:
            self.current_lineart_path = file_path
            self.set_status(f"已加载线稿。\n{os.path.basename(file_path)}")
            self.btn_start.config(state=tk.NORMAL)
            self.update_preview_panel(file_path)

    def _ensure_runtime_permissions(self):
        if Quartz is None:
            self._show_error(
                "缺少依赖",
                "Quartz 框架不可用。\n请先安装 requirements 依赖。",
            )
            return False

        if not check_accessibility_permission(prompt=True):
            self._show_error(
                "需要权限",
                "鼠标控制需要辅助功能权限。",
            )
            return False

        if not self.abort_listener.running and not self.abort_listener.start():
            self._show_error(
                "全局键盘监听不可用",
                (
                    f"{self.abort_listener.failed_reason}\n\n"
                    "请授予输入监控和辅助功能权限后重试。"
                ),
            )
            return False

        return True

    def start_digital_amber(self):
        if not self.current_lineart_path:
            self._show_warning("缺少线稿", "请先生成或加载线稿再开始绘制。")
            return

        if not self._ensure_runtime_permissions():
            return

        self.root.iconify()
        self.root.after(150, self.launch_overlay)

    def launch_overlay(self):
        overlay = DigitalAmberOverlay(
            self.root,
            self.current_lineart_path,
            self.run_draw_thread,
            self.on_overlay_cancel,
        )
        if not overlay.ready:
            self.root.deiconify()

    def on_overlay_cancel(self):
        self.root.deiconify()
        self.set_status("已取消选区。")

    def run_draw_thread(self, rx, ry, rw, rh, img_path):
        self.root.deiconify()
        draw_thread = threading.Thread(
            target=self.draw_logic,
            args=(rx, ry, rw, rh, img_path),
            daemon=True,
        )
        draw_thread.start()

    def draw_logic(self, rx, ry, rw, rh, img_path):
        self.abort_event.clear()

        if not os.path.exists(img_path):
            self.set_status("线稿文件不存在。")
            return

        time.sleep(0.8)

        img = cv2.imdecode(np.fromfile(img_path, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if img is None:
            self.set_status("线稿图片解码失败。")
            return

        edges = cv2.bitwise_not(img)
        img_h, img_w = edges.shape
        if img_h == 0 or img_w == 0:
            self.set_status("线稿尺寸无效。")
            return

        scale = min(rw / img_w, rh / img_h)
        offset_x = rx + (rw - img_w * scale) / 2
        offset_y = ry + (rh - img_h * scale) / 2

        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
        current_step = max(1, int(self.speed_slider.get()))

        right_is_down = False
        last_x = int(offset_x)
        last_y = int(offset_y)

        self.set_status("正在绘制中... 按 P 可中断。")

        try:
            for contour in contours:
                if self.abort_event.is_set():
                    break
                if len(contour) == 0:
                    continue

                start_x = int(offset_x + contour[0][0][0] * scale)
                start_y = int(offset_y + contour[0][0][1] * scale)
                move_mouse(start_x, start_y, dragging=False)
                last_x, last_y = start_x, start_y
                time.sleep(0.004)

                right_click_down(start_x, start_y)
                right_is_down = True
                time.sleep(0.003)

                for point in contour[1::current_step]:
                    if self.abort_event.is_set():
                        break
                    px = int(offset_x + point[0][0] * scale)
                    py = int(offset_y + point[0][1] * scale)
                    move_mouse(px, py, dragging=True)
                    last_x, last_y = px, py
                    time.sleep(0.0015)

                right_click_up(last_x, last_y)
                right_is_down = False
                time.sleep(0.003)
        finally:
            if right_is_down:
                right_click_up(last_x, last_y)

        if self.abort_event.is_set():
            self.set_status("已通过全局 P 键中断绘制。")
            print("绘制已被用户中断。")
        else:
            self.set_status("绘制已完成。")
            print("绘制已完成。")

    def on_close(self):
        self.abort_listener.stop()
        self.root.destroy()


def main():
    if sys.platform != "darwin":
        raise SystemExit("此版本仅支持 macOS。")

    root = tk.Tk()
    SpirePainterMacApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
