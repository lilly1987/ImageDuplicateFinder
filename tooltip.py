"""
툴팁 모듈.

Tkinter 위젯에 호버 시 툴팁을 표시하는 기능 제공.
"""

import tkinter as tk


class ToolTip:
    """위젯에 마우스 오버 시 툴팁 표시"""

    def __init__(self, widget, text, delay=500):
        self.widget = widget
        self.text = text
        self.delay = delay  # ms
        self.tip_window = None
        self.id = None
        self.x = 0
        self.y = 0

        widget.bind("<Enter>", self._enter)
        widget.bind("<Leave>", self._leave)
        widget.bind("<Button-1>", self._leave)

    def _enter(self, event=None):
        self.x = event.x_root if event else 0
        self.y = event.y_root if event else 0
        self.id = self.widget.after(self.delay, self._show)

    def _leave(self, event=None):
        if self.id:
            self.widget.after_cancel(self.id)
            self.id = None
        self._hide()

    def _show(self):
        if self.tip_window or not self.text:
            return
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{self.x + 15}+{self.y + 15}")
        label = tk.Label(
            tw, text=self.text,
            background="#ffffe0", foreground="black",
            relief="solid", borderwidth=1,
            font=("Arial", 8),
            padx=4, pady=2,
            wraplength=300, justify="left",
        )
        label.pack()

    def _hide(self):
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None


def add_tooltip(widget, text):
    """위젯에 툴팁 추가"""
    return ToolTip(widget, text)
