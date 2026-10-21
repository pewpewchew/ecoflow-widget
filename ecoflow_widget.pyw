"""
EcoFlow DELTA 2 Max desktop widget for Windows 11.

- Floating always-on-top mini panel (drag to move, right-click for menu)
- Tray icon with battery % and port toggles
- Data from the official EcoFlow Developer API (cloud)

Run:  pythonw ecoflow_widget.pyw          (normal)
      python  ecoflow_widget.pyw --demo   (fake data, no keys needed)
"""

import json
import logging
import logging.handlers
import os
import queue
import random
import re
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk

import requests

from ecoflow_api import HOSTS, PORTS, AuthError, EcoFlowClient, EcoFlowError
from ecoflow_mqtt import HAVE_PAHO, LiveFeed

APP_NAME = "EcoFlowWidget"
KEYRING_SERVICE = "EcoFlowWidget"
DEMO = "--demo" in sys.argv
NO_TRAY = "--no-tray" in sys.argv or os.environ.get("ECOFLOW_NO_TRAY") == "1"

DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~/.config"), APP_NAME)
os.makedirs(DATA_DIR, exist_ok=True)
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
LAST_DATA_PATH = os.path.join(DATA_DIR, "last_data.json")
MQTT_SAMPLES_PATH = os.path.join(DATA_DIR, "mqtt_samples.json")
# The station only pushes when something changes, so silence on a connected feed means "no change".
LIVE_STALE_SEC = 900       # silent this long even while connected -> treat as stale (same as Home Assistant: 3 x 5 min)
LIVE_QUIET_SEC = 60        # after this, the footer says "no changes for X" instead of "updated Xs ago"
LIVE_LOST_GRACE_SEC = 60   # live connection down this long -> values shown as stale
REST_WHILE_LIVE_SEC = 600  # with a live feed, REST only fills gaps occasionally
FEED_GRACE_SEC = 90         # how long to wait for the first live message before trusting REST

log = logging.getLogger(APP_NAME)
log.setLevel(logging.INFO)
_handler = logging.handlers.RotatingFileHandler(os.path.join(DATA_DIR, "widget.log"), maxBytes=500_000, backupCount=1, encoding="utf-8")
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
log.addHandler(_handler)

# ---------------------------------------------------------------- colors (EcoFlow-style dark)
BG = "#0C0D10"
TILE = "#17191E"
HAIRLINE = "#24272E"
TRACK = "#22252B"
SWITCH_OFF = "#3A3E46"
FG = "#F4F5F7"
FG_DIM = "#8B909A"
FG_FAINT = "#555A63"
ACCENT = "#2F7BFF"
CYAN = "#22D3EE"
AMBER = "#FFB547"
RED = "#FF5A5F"

RING_NORMAL = ("#22D3EE", "#2F7BFF")   # cyan -> blue
RING_LOW = ("#FFB547", "#FF5A5F")      # amber -> red, 20% and below
RING_STALE = ("#4B4F58", "#4B4F58")

DEFAULT_CONFIG = {
    "access_key": "",
    "host": "",
    "sn": "",
    "device_name": "",
    "interval": 15,
    "x": None,
    "y": None,
    "widget_visible": True,
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        log.exception("Could not save config")


def get_secret(access_key):
    if not access_key:
        return ""
    try:
        import keyring
        return keyring.get_password(KEYRING_SERVICE, access_key) or ""
    except Exception:
        log.exception("keyring read failed")
        return ""


def set_secret(access_key, secret):
    import keyring
    keyring.set_password(KEYRING_SERVICE, access_key, secret)


# ---------------------------------------------------------------- data parsing
def _num(q, *keys):
    for key in keys:
        value = q.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def parse_quota(q):
    soc = _num(q, "bms_emsStatus.lcdShowSoc", "pd.soc", "bms_bmsStatus.soc")
    w_in = _num(q, "pd.wattsInSum") or 0
    w_out = _num(q, "pd.wattsOutSum") or 0
    temp = _num(q, "bms_bmsStatus.temp", "bms_bmsStatus.maxCellTemp")
    chg_min = _num(q, "bms_emsStatus.chgRemainTime")
    dsg_min = _num(q, "bms_emsStatus.dsgRemainTime")
    state = _num(q, "pd.chgDsgState")  # 0 idle, 1 discharging, 2 charging

    if state == 2 or (state is None and w_in - w_out > 5):
        mode = "charging"
    elif state == 1 or (state is None and w_out > 0):
        mode = "discharging"
    else:
        mode = "idle"
    if soc is not None and soc >= 100 and mode == "charging" and w_in < 30:
        mode = "full"

    def valid(minutes):
        return minutes if minutes and 0 < minutes < 5999 else None

    ports = {}
    for name, port in PORTS.items():
        value = q.get(port["state_key"])
        ports[name] = None if value is None else bool(int(value))

    usb_keys = ("pd.usb1Watts", "pd.usb2Watts", "pd.qcUsb1Watts", "pd.qcUsb2Watts", "pd.typec1Watts", "pd.typec2Watts")
    usb_vals = [_num(q, k) for k in usb_keys]
    usb_w = sum(v for v in usb_vals if v is not None) if any(v is not None for v in usb_vals) else None
    port_w = {
        "ac": _num(q, "inv.outputWatts"),
        "dc": _num(q, "mppt.outWatts", "pd.carWatts"),
        "usb": usb_w,
    }
    port_w = {k: None if v is None else max(0, int(round(v))) for k, v in port_w.items()}

    return {
        "soc": None if soc is None else int(round(soc)),
        "in": int(round(w_in)),
        "out": int(round(w_out)),
        "temp": temp,
        "mode": mode,
        "chg_min": valid(chg_min),
        "dsg_min": valid(dsg_min),
        "ports": ports,
        "port_w": port_w,
    }


def fmt_minutes(minutes):
    if minutes is None:
        return "--"
    minutes = int(minutes)
    if minutes >= 24 * 60:
        return f"{minutes // 1440}d {(minutes % 1440) // 60}h"
    if minutes >= 60:
        return f"{minutes // 60}h {minutes % 60:02d}m"
    return f"{minutes}m"


def time_label(d):
    if d["mode"] == "charging":
        return "Full in", fmt_minutes(d["chg_min"])
    if d["mode"] == "discharging":
        return "Empty in", fmt_minutes(d["dsg_min"])
    if d["mode"] == "full":
        return "Status", "Full"
    return "Status", "Idle"


def ring_palette(soc, stale=False):
    if soc is None or stale:
        return RING_STALE
    return RING_LOW if soc <= 20 else RING_NORMAL


def duration(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    return f"{seconds // 3600}h {seconds % 3600 // 60}m"


def ago(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    return f"{seconds // 3600}h {seconds % 3600 // 60}m ago"


# ---------------------------------------------------------------- demo client
class DemoClient:
    host = "demo"

    def __init__(self):
        self.soc, self.ports = 76.0, {"inv.cfgAcEnabled": 1, "pd.carState": 0, "pd.dcOutState": 1}

    def list_devices(self, host=None):
        return [{"sn": "R351ZEB4HF000000", "deviceName": "DELTA 2 Max (demo)", "online": 1}]

    detect_host = list_devices

    def quota_all(self, sn):
        w_in = random.choice([0, 0, 412, 398])
        ac = random.randint(90, 130) if self.ports["inv.cfgAcEnabled"] else 0
        dc = 24 if self.ports["pd.carState"] else 0
        usb = 18 if self.ports["pd.dcOutState"] else 0
        w_out = ac + dc + usb
        self.soc = max(3, min(100, self.soc + (0.4 if w_in else -0.3)))
        return {
            "bms_emsStatus.lcdShowSoc": round(self.soc), "pd.wattsInSum": w_in, "pd.wattsOutSum": w_out,
            "bms_bmsStatus.temp": 27, "bms_emsStatus.chgRemainTime": 52, "bms_emsStatus.dsgRemainTime": 803,
            "pd.chgDsgState": 2 if w_in else 1, **self.ports,
            "inv.outputWatts": ac, "mppt.outWatts": dc, "pd.typec1Watts": usb, "pd.usb1Watts": 0,
        }

    def set_quota(self, sn, module_type, operate_type, params, module_sn=None):
        key = {"acOutCfg": "inv.cfgAcEnabled", "mpptCar": "pd.carState", "dcOutCfg": "pd.dcOutState"}[operate_type]
        self.ports[key] = params["enabled"]


# ---------------------------------------------------------------- app
class App:
    def __init__(self):
        self.cfg = load_config()
        self.events = queue.Queue()
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.client_lock = threading.Lock()
        self.client = None
        self.data = None            # parsed dict
        self.quota = {}             # merged raw values (REST seed + live MQTT updates)
        self.last_ok = None         # time of last trustworthy update
        self.last_live = None       # time of last live (MQTT) message
        self.live_state = "off"     # off / connecting / live / disconnected / auth_failed / error
        self.live_since = None      # when the live feed connected
        self.live_lost_at = None    # when the live connection dropped
        self.feed = None
        self.feed_gen = 0
        self.feed_lock = threading.Lock()
        self.feed_started = 0
        self.feed_retry_at = 0
        self._last_dump = 0
        self.online = True
        self.error = None           # (kind, text)
        self.pending = {}           # port -> (value, expires_at)
        self.busy = set()           # ports with a command in flight
        self.tray = None
        self.settings_win = None

        self._init_dpi()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("EcoFlow")
        self.scale = max(1.0, float(self.root.tk.call("tk", "scaling")) / (96 / 72))
        self._init_fonts()
        self._build_widget()
        self._rebuild_client()

        threading.Thread(target=self._poll_loop, daemon=True).start()
        if not NO_TRAY:
            self._start_tray()
        self.root.after(100, self._drain_events)
        self.root.after(1000, self._tick)

        if self.cfg["widget_visible"]:
            self.show_widget()
        if not self._configured():
            self.root.after(300, self.open_settings)

    # ------------------------------------------------ setup helpers
    @staticmethod
    def _init_dpi():
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass

    def S(self, px):
        return int(px * self.scale)

    def _init_fonts(self):
        families = set(tkfont.families(self.root))
        base = next((f for f in ("Segoe UI Variable Text", "Segoe UI", "Inter", "DejaVu Sans") if f in families), "TkDefaultFont")
        disp = next((f for f in ("Segoe UI Variable Display", "Segoe UI", "Inter", "DejaVu Sans") if f in families), base)
        sym = "Segoe UI Symbol" if "Segoe UI Symbol" in families else base
        self.f_name = tkfont.Font(family=disp, size=10, weight="bold")
        self.f_icon = tkfont.Font(family=sym, size=13)
        self.f_close = tkfont.Font(family=sym, size=10)
        self.f_big = tkfont.Font(family=disp, size=30, weight="bold")
        self.f_pct = tkfont.Font(family=disp, size=13, weight="bold")
        self.f_sub = tkfont.Font(family=base, size=9)
        self.f_sub_b = tkfont.Font(family=base, size=9, weight="bold")
        self.f_lbl = tkfont.Font(family=base, size=8)
        self.f_val = tkfont.Font(family=disp, size=13, weight="bold")
        self.f_port = tkfont.Font(family=disp, size=10, weight="bold")
        self.f_foot = tkfont.Font(family=base, size=8)

    def _configured(self):
        return DEMO or bool(self.cfg["access_key"] and self.cfg["sn"] and get_secret(self.cfg["access_key"]))

    def _stop_feed(self):
        with self.feed_lock:
            feed, self.feed = self.feed, None
            self.feed_gen += 1
            self.feed_retry_at = 0
        if feed:
            feed.stop()
        self.live_state, self.last_live, self.live_since = "off", None, None

    def _rebuild_client(self):
        self._stop_feed()
        self.quota = {}
        with self.client_lock:
            if DEMO:
                self.client = DemoClient()
            elif self._configured():
                self.client = EcoFlowClient(self.cfg["access_key"], get_secret(self.cfg["access_key"]), self.cfg["host"] or None)
            else:
                self.client = None
        self.wake.set()

    def post(self, kind, payload=None):
        self.events.put((kind, payload))

    # ------------------------------------------------ widget UI
    # Layout in logical pixels (multiplied by DPI scale at draw time)
    W, H = 312, 428
    CX, CY, R_OUT, STROKE = 156, 142, 82, 12
    TILE_X = (16, 112, 208)
    TILE_W = 88
    STAT_Y, STAT_H = 246, 62
    PORT_Y, PORT_H = 318, 66
    SS = 4  # supersampling for smooth shapes

    def _build_widget(self):
        w = self.win = tk.Toplevel(self.root, bg=BG)
        w.withdraw()
        w.overrideredirect(True)
        w.attributes("-topmost", True)

        S = self.S
        self.cv = cv = tk.Canvas(w, width=S(self.W), height=S(self.H), bg=BG, highlightthickness=0, bd=0)
        cv.pack()
        self.images = {}
        self.hit = {}

        self.images["bg"] = self._render_background()
        cv.create_image(0, 0, image=self.images["bg"], anchor="nw")

        # header
        self.t_name = cv.create_text(S(18), S(26), text="EcoFlow", anchor="w", fill=FG, font=self.f_name)
        self.t_menu = cv.create_text(S(self.W - 50), S(25), text="⋯", fill=FG_DIM, font=self.f_icon)
        self.t_close = cv.create_text(S(self.W - 22), S(25), text="✕", fill=FG_DIM, font=self.f_close)
        self.hit["menu"] = (S(self.W - 64), S(10), S(self.W - 36), S(40))
        self.hit["close"] = (S(self.W - 36), S(10), S(self.W - 8), S(40))

        # ring + center text
        self.i_ring = cv.create_image(S(self.CX), S(self.CY), anchor="center")
        self.t_num = cv.create_text(S(self.CX), S(self.CY - 8), text="--", fill=FG, font=self.f_big, anchor="e")
        self.t_pct = cv.create_text(S(self.CX), S(self.CY - 1), text="%", fill=FG_DIM, font=self.f_pct, anchor="w")
        self.t_mode = cv.create_text(S(self.CX), S(self.CY + 26), text="", fill=FG_DIM, font=self.f_sub)
        self.t_time = cv.create_text(S(self.CX), S(self.CY + 44), text="", fill=FG, font=self.f_sub_b)

        # stat tiles
        self.t_stat = {}
        labels = {"in": "↓  Input", "out": "↑  Output", "temp": "Battery temp"}
        for x, key in zip(self.TILE_X, ("in", "out", "temp")):
            cv.create_text(S(x + 12), S(self.STAT_Y + 19), text=labels[key], anchor="w", fill=FG_DIM, font=self.f_lbl)
            self.t_stat[key] = cv.create_text(S(x + 12), S(self.STAT_Y + 42), text="--", anchor="w", fill=FG, font=self.f_val)

        # port tiles: label, switch, state
        for name in ("on", "off", "busy", "unknown"):
            self.images["sw_" + name] = self._render_switch(name)
        self.port_items = {}
        for x, name in zip(self.TILE_X, PORTS):
            lbl = cv.create_text(S(x + 12), S(self.PORT_Y + 19), text=PORTS[name]["label"], anchor="w", fill=FG, font=self.f_port)
            sw = cv.create_image(S(x + 12), S(self.PORT_Y + 36), anchor="nw", image=self.images["sw_unknown"])
            st = cv.create_text(S(x + 56), S(self.PORT_Y + 47), text="", anchor="w", fill=FG_DIM, font=self.f_lbl)
            self.port_items[name] = (lbl, sw, st)
            self.hit["port_" + name] = (S(x), S(self.PORT_Y), S(x + self.TILE_W), S(self.PORT_Y + self.PORT_H))

        # footer
        fy = self.H - 20
        self.o_dot = cv.create_oval(S(18), S(fy - 3), S(24), S(fy + 3), fill=FG_FAINT, outline="")
        self.t_foot = cv.create_text(S(31), S(fy), text="Connecting...", anchor="w", fill=FG_DIM, font=self.f_foot)

        cv.bind("<ButtonPress-1>", self._on_press)
        cv.bind("<B1-Motion>", self._drag_move)
        cv.bind("<ButtonRelease-1>", self._drag_end)
        cv.bind("<Motion>", self._on_motion)
        cv.bind("<Button-3>", self._popup_menu)

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="Refresh now", command=self.refresh_now)
        self.menu.add_command(label="Settings...", command=self.open_settings)
        self.menu.add_command(label="Open data folder", command=self.open_data_folder)
        self.menu.add_separator()
        self.menu.add_command(label="Hide widget", command=self.hide_widget)
        self.menu.add_command(label="Quit", command=self.quit)

        self._ring_key = None
        self.render()

    # ---- drawing helpers (Pillow, supersampled then scaled down)
    def _px(self, v):
        return int(round(v * self.scale * self.SS))

    def _render_background(self):
        from PIL import Image, ImageDraw, ImageTk
        P = self._px
        img = Image.new("RGB", (P(self.W), P(self.H)), BG)
        d = ImageDraw.Draw(img)
        d.line((P(16), P(48), P(self.W - 16), P(48)), fill=HAIRLINE, width=max(1, P(1)))
        for y, h in ((self.STAT_Y, self.STAT_H), (self.PORT_Y, self.PORT_H)):
            for x in self.TILE_X:
                d.rounded_rectangle((P(x), P(y), P(x + self.TILE_W), P(y + h)), radius=P(14), fill=TILE)
        img = img.resize((self.S(self.W), self.S(self.H)), Image.LANCZOS)
        return ImageTk.PhotoImage(img)

    def _render_ring(self, soc, palette):
        import math
        from PIL import Image, ImageDraw, ImageTk
        P = self._px
        box = self.R_OUT * 2 + 4
        img = Image.new("RGB", (P(box), P(box)), BG)
        d = ImageDraw.Draw(img)
        c = P(box) / 2
        ro, sw = P(self.R_OUT), P(self.STROKE)
        bbox = (c - ro, c - ro, c + ro, c + ro)
        d.ellipse(bbox, outline=TRACK, width=sw)
        if soc:
            ca, cb = palette
            a = tuple(int(ca[i:i + 2], 16) for i in (1, 3, 5))
            b = tuple(int(cb[i:i + 2], 16) for i in (1, 3, 5))

            def col(f):
                return tuple(int(a[i] + (b[i] - a[i]) * f) for i in range(3))

            sweep = 360 * min(soc, 100) / 100
            step = 1.0
            ang = 0.0
            while ang < sweep:
                end = min(sweep, ang + step + 0.6)
                d.arc(bbox, -90 + ang, -90 + end, fill=col(ang / 360), width=sw)
                ang += step
            if soc < 100:
                rm = ro - sw / 2
                for frac_ang, f in ((0, 0), (sweep, sweep / 360)):
                    t = math.radians(-90 + frac_ang)
                    x, y = c + rm * math.cos(t), c + rm * math.sin(t)
                    d.ellipse((x - sw / 2, y - sw / 2, x + sw / 2, y + sw / 2), fill=col(f))
        img = img.resize((self.S(box), self.S(box)), Image.LANCZOS)
        return ImageTk.PhotoImage(img)

    def _render_switch(self, state):
        from PIL import Image, ImageDraw, ImageTk
        P = self._px
        w, h = 36, 20
        img = Image.new("RGB", (P(w), P(h)), TILE)
        d = ImageDraw.Draw(img)
        track = {"on": ACCENT, "off": SWITCH_OFF, "busy": SWITCH_OFF, "unknown": TRACK}[state]
        knob = {"on": "#FFFFFF", "off": "#C9CDD4", "busy": "#6B707A", "unknown": "#4A4F58"}[state]
        d.rounded_rectangle((0, 0, P(w) - 1, P(h) - 1), radius=P(h) / 2, fill=track)
        k = P(h) - P(4)
        kx = {"on": P(w) - P(2) - k, "busy": (P(w) - k) / 2}.get(state, P(2))
        d.ellipse((kx, P(2), kx + k, P(2) + k), fill=knob)
        img = img.resize((self.S(w), self.S(h)), Image.LANCZOS)
        return ImageTk.PhotoImage(img)

    # ---- window placement
    def _round_corners(self):
        if sys.platform != "win32":
            return
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self.win.winfo_id())
            pref = ctypes.c_int(2)  # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
            border = ctypes.c_int(int(HAIRLINE[5:7] + HAIRLINE[3:5] + HAIRLINE[1:3], 16))  # COLORREF is BGR
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border), ctypes.sizeof(border))
        except Exception:
            pass

    def _place_widget(self):
        self.win.update_idletasks()
        ww, wh = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        x, y = self.cfg.get("x"), self.cfg.get("y")
        if x is None or y is None:
            x, y = sw - ww - self.S(24), sh - wh - self.S(72)
        x = max(0, min(int(x), sw - ww))
        y = max(0, min(int(y), sh - wh))
        self.win.geometry(f"+{x}+{y}")

    def show_widget(self):
        self._place_widget()
        self.win.deiconify()
        self.win.lift()
        self.win.attributes("-topmost", True)
        self._round_corners()
        if not self.cfg["widget_visible"]:
            self.cfg["widget_visible"] = True
            save_config(self.cfg)
        self._update_tray()

    def hide_widget(self):
        self.win.withdraw()
        self.cfg["widget_visible"] = False
        save_config(self.cfg)
        self._update_tray()
        if NO_TRAY:
            self.quit()

    def toggle_widget(self):
        if self.win.winfo_viewable():
            self.hide_widget()
        else:
            self.show_widget()

    # ---- mouse
    def _hit_test(self, x, y):
        for name, (x0, y0, x1, y1) in self.hit.items():
            if x0 <= x <= x1 and y0 <= y <= y1:
                return name
        return None

    def _on_motion(self, e):
        target = self._hit_test(e.x, e.y)
        self.cv.config(cursor="hand2" if target else "")
        self.cv.itemconfig(self.t_menu, fill=FG if target == "menu" else FG_DIM)
        self.cv.itemconfig(self.t_close, fill=FG if target == "close" else FG_DIM)

    def _on_press(self, e):
        target = self._hit_test(e.x, e.y)
        self._drag = None
        if target == "close":
            self.hide_widget()
        elif target == "menu":
            self._popup_menu(e)
        elif target and target.startswith("port_"):
            self.toggle_port(target[5:])
        else:
            self._drag = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())

    def _drag_move(self, e):
        if getattr(self, "_drag", None):
            dx, dy = self._drag
            self.win.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")

    def _drag_end(self, e):
        if getattr(self, "_drag", None):
            self._drag = None
            self.cfg["x"], self.cfg["y"] = self.win.winfo_x(), self.win.winfo_y()
            save_config(self.cfg)

    def _popup_menu(self, e):
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    # ------------------------------------------------ rendering
    def port_state(self, name):
        pend = self.pending.get(name)
        if pend and pend[1] > time.time():
            return pend[0]
        if self.data:
            return self.data["ports"].get(name)
        return None

    def stale(self):
        if self.last_live is not None:
            return not self.live_fresh()
        return self.last_ok is None or (time.time() - self.last_ok) > max(90, self.cfg["interval"] * 4)

    def render(self):
        cv, d, stale = self.cv, self.data, self.stale()
        cv.itemconfig(self.t_name, text=self.cfg.get("device_name") or ("DELTA 2 Max (demo)" if DEMO else "EcoFlow"))
        value_fg = FG_DIM if stale else FG

        soc = d["soc"] if d else None
        palette = ring_palette(soc, stale)
        key = (soc, palette)
        if key != self._ring_key:
            self.images["ring"] = self._render_ring(soc or 0, palette)
            cv.itemconfig(self.i_ring, image=self.images["ring"])
            self._ring_key = key

        # center: number + small "%" kept visually centered as one unit
        num = "--" if soc is None else str(soc)
        nw, pw = self.f_big.measure(num), self.f_pct.measure("%") + self.S(2)
        left = self.S(self.CX) - (nw + pw) // 2
        cv.coords(self.t_num, left + nw, self.S(self.CY - 8))
        cv.coords(self.t_pct, left + nw + self.S(2), self.S(self.CY - 15))
        cv.itemconfig(self.t_num, text=num, fill=value_fg)
        cv.itemconfig(self.t_pct, text="%")

        if d:
            mode_text = {"charging": "⚡ Charging", "discharging": "Discharging", "full": "Fully charged", "idle": "Idle"}[d["mode"]]
            lbl, val = time_label(d)
            time_text = {"Full in": f"Full in {val}", "Empty in": f"{val} left"}.get(lbl, "")
            cv.itemconfig(self.t_mode, text=mode_text, fill=CYAN if d["mode"] == "charging" and not stale else FG_DIM)
            cv.itemconfig(self.t_time, text=time_text, fill=value_fg)
            cv.itemconfig(self.t_stat["in"], text=f"{d['in']} W", fill=value_fg)
            cv.itemconfig(self.t_stat["out"], text=f"{d['out']} W", fill=value_fg)
            temp = d["temp"]
            cv.itemconfig(self.t_stat["temp"], text="--" if temp is None else f"{temp:.0f}°C", fill=value_fg)
        else:
            cv.itemconfig(self.t_mode, text="")
            cv.itemconfig(self.t_time, text="")

        for name, (lbl, sw, st) in self.port_items.items():
            state = self.port_state(name)
            watts = d["port_w"].get(name) if d else None
            if name in self.busy:
                img, text = "busy", "..."
            elif state is None:
                img, text = "unknown", ""
            else:
                img = "on" if state else "off"
                # when on, show the port's live watts next to the switch (fall back to "On")
                text = ("On" if watts is None else f"{watts} W") if state else "Off"
            cv.itemconfig(sw, image=self.images["sw_" + img])
            cv.itemconfig(st, text=text, fill=FG if img == "on" and not stale else FG_DIM)
            cv.itemconfig(lbl, fill=FG if state is not None else FG_DIM)

        # status dot + footer
        if self.error and self.error[0] == "auth":
            dot, foot = RED, "Keys rejected - open Settings"
        elif not self._configured():
            dot, foot = FG_FAINT, "Not set up - open Settings"
        elif not self.online:
            dot, foot = AMBER, "Device offline in EcoFlow cloud"
        elif self.live_fresh():
            quiet = time.time() - self.last_live
            dot = ACCENT
            foot = f"Live · updated {ago(quiet)}" if quiet < LIVE_QUIET_SEC else f"Live · no changes for {duration(quiet)}"
        elif self.last_live is not None:
            dot, foot = AMBER, f"Live data paused · last {ago(time.time() - self.last_live)}"
        elif self.error and stale:
            dot, foot = RED, "No connection" + (f" - updated {ago(time.time() - self.last_ok)}" if self.last_ok else "")
        elif self.last_ok is None:
            dot, foot = FG_FAINT, "Connecting to live data..." if self.live_state in ("connecting", "live") else "Connecting..."
        elif self.live_state in ("connecting", "disconnected", "error", "auth_failed") and not DEMO and HAVE_PAHO:
            dot = AMBER
            foot = f"No live feed · snapshot {ago(time.time() - self.last_ok)}"
        elif not HAVE_PAHO and not DEMO:
            dot, foot = AMBER, "Snapshot mode - run install.bat"
        else:
            dot = AMBER if stale else ACCENT
            foot = f"{'Demo' if DEMO else 'Snapshot'} · updated {ago(time.time() - self.last_ok)}"
            if self.error:
                foot += "  (last try failed)"
        cv.itemconfig(self.o_dot, fill=dot)
        cv.itemconfig(self.t_foot, text=foot)

    def _tick(self):
        self.render()
        self.root.after(1000, self._tick)

    # ------------------------------------------------ polling (background thread)
    def live_fresh(self):
        if self.last_live is None:
            return False
        now = time.time()
        if now - self.last_live >= LIVE_STALE_SEC:
            return False
        if self.live_state != "live" and self.live_lost_at and now - self.live_lost_at > LIVE_LOST_GRACE_SEC:
            return False
        return True

    def _ensure_feed(self, client, sn):
        """Start the live MQTT feed if possible (needs paho-mqtt and real keys)."""
        if DEMO or not HAVE_PAHO or not isinstance(client, EcoFlowClient):
            return
        now = time.time()
        with self.feed_lock:
            if self.feed is not None:
                if self.live_state != "auth_failed" or now - self.feed_started < 300:
                    return
                old, self.feed = self.feed, None   # broker refused us: fetch fresh credentials
                self.feed_gen += 1
            else:
                old = None
            if now < self.feed_retry_at:
                return
            gen = self.feed_gen
        if old:
            old.stop()
        try:
            cert = client.certification()
        except Exception as err:
            log.warning("mqtt certification failed: %s", err)
            self.feed_retry_at = now + 120
            self.post("live_state", (gen, "error", str(err)))
            return
        host, user, pw = cert.get("url"), cert.get("certificateAccount"), cert.get("certificatePassword")
        if not (host and user and pw):
            log.warning("mqtt certification incomplete: %s", sorted(cert))
            self.feed_retry_at = now + 300
            return
        feed = LiveFeed(
            host, cert.get("port") or 8883, user, pw, sn,
            on_quota=lambda q, g=gen: self.post("live", (g, q)),
            on_online=lambda o, g=gen: self.post("live_online", (g, o)),
            on_state=lambda st, detail, g=gen: self.post("live_state", (g, st, detail)),
        )
        with self.feed_lock:
            if gen != self.feed_gen:   # settings changed meanwhile
                return
            self.feed, self.feed_started = feed, now
        log.info("starting live feed to %s", host)
        feed.start()

    def _poll_loop(self):
        last_list = 0
        last_rest = 0
        while not self.stop.is_set():
            with self.client_lock:
                client = self.client
            sn = self.cfg.get("sn") or "demo"
            interval = max(5, int(self.cfg.get("interval") or 15))
            if client is not None:
                try:
                    self._ensure_feed(client, sn)
                    if time.time() - last_list > 60:
                        devices = client.list_devices()
                        match = next((x for x in devices if x.get("sn") == sn), None)
                        self.post("online", bool(int(match.get("online", 1))) if match else True)
                        last_list = time.time()
                    if not self.live_fresh() or time.time() - last_rest > REST_WHILE_LIVE_SEC:
                        quota = client.quota_all(sn)
                        last_rest = time.time()
                        self.post("data", quota)
                except AuthError as err:
                    log.warning("auth error: %s", err)
                    self.post("error", ("auth", str(err)))
                except requests.RequestException as err:
                    log.warning("network error: %s", err)
                    self.post("error", ("net", str(err)))
                except EcoFlowError as err:
                    log.warning("api error: %s", err)
                    self.post("error", ("api", str(err)))
                except Exception:
                    log.exception("unexpected poll error")
                    self.post("error", ("api", "unexpected error, see widget.log"))
            self.wake.wait(interval)
            self.wake.clear()

    def refresh_now(self):
        self.wake.set()

    def _drain_events(self):
        changed = False
        try:
            while True:
                kind, payload = self.events.get_nowait()
                changed = True
                if kind == "data":   # REST snapshot
                    now = time.time()
                    self.error = None
                    if self.last_live is None:
                        # no live data yet: the snapshot is all we have
                        self.quota.update(payload)
                        feed_waiting = (self.live_state in ("connecting", "live")
                                        and now - self.feed_started < FEED_GRACE_SEC)
                        if not feed_waiting:
                            self.last_ok = now
                    else:
                        # live feed is the source of truth; the snapshot only fills keys it never sent
                        for key, value in payload.items():
                            self.quota.setdefault(key, value)
                    self._reparse()
                elif kind == "live":
                    gen, values = payload
                    if gen == self.feed_gen:
                        self.quota.update(values)
                        self.last_live = self.last_ok = time.time()
                        self.error = None
                        self._reparse()
                elif kind == "live_state":
                    gen, state, detail = payload
                    if gen == self.feed_gen:
                        if state == "live":
                            self.live_since, self.live_lost_at = time.time(), None
                        elif self.live_state == "live" or self.live_lost_at is None:
                            self.live_lost_at = time.time()
                        self.live_state = state
                elif kind == "live_online":
                    gen, online = payload
                    if gen == self.feed_gen:
                        self.online = online
                elif kind == "online":
                    if not self.live_fresh():
                        self.online = payload
                elif kind == "error":
                    self.error = payload
                elif kind == "cmd_done":
                    name, value, err = payload
                    self.busy.discard(name)
                    if err:
                        self.pending.pop(name, None)
                        messagebox.showerror("EcoFlow", f"Could not switch {PORTS[name]['label']}:\n{err}", parent=self.win)
                    else:
                        self.pending[name] = (value, time.time() + 10)
                elif kind == "call":
                    payload()
        except queue.Empty:
            pass
        if changed:
            self.render()
            self._update_tray()
        self.root.after(100, self._drain_events)

    def _reparse(self):
        try:
            self.data = parse_quota(self.quota)
        except Exception:
            log.exception("parse failed")
            return
        now = time.time()
        if now - self._last_dump > 30:
            self._last_dump = now
            try:
                with open(LAST_DATA_PATH, "w", encoding="utf-8") as f:
                    json.dump(self.quota, f, indent=1, sort_keys=True)
                feed = self.feed
                if feed:
                    with open(MQTT_SAMPLES_PATH, "w", encoding="utf-8") as f:
                        json.dump(feed.samples(), f, indent=1, sort_keys=True)
            except OSError:
                pass

    # ------------------------------------------------ port control
    def toggle_port(self, name):
        if name in self.busy or not self.client:
            return
        current = self.port_state(name)
        if current is None:
            return
        target = 0 if current else 1
        if name == "ac" and not target:
            ok = messagebox.askyesno(
                "Turn off AC output?",
                "This cuts power to everything plugged into the EcoFlow AC sockets.\n\n"
                "If this PC runs from the EcoFlow, it will shut off immediately.\n\nTurn AC off?",
                icon="warning", default="no", parent=self.win)
            if not ok:
                return
        self.busy.add(name)
        self.render()
        sn = self.cfg.get("sn") or "demo"
        cmd = PORTS[name]["command"](sn, target)

        def worker():
            err = None
            try:
                with self.client_lock:
                    client = self.client
                client.set_quota(sn, cmd["module_type"], cmd["operate_type"], cmd["params"], cmd.get("module_sn"))
                log.info("switched %s -> %s", name, target)
            except Exception as e:
                log.warning("switch %s failed: %s", name, e)
                err = str(e)
            self.post("cmd_done", (name, bool(target), err))
            time.sleep(2.5)
            self.wake.set()

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------ tray
    def _tray_image(self):
        from PIL import Image, ImageDraw, ImageFont
        size = 64
        d = self.data
        soc = d["soc"] if d else None
        if soc is None or self.stale():
            color = "#4B4F58"
        elif soc <= 20:
            color = RED
        else:
            color = ACCENT
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=14, fill=color)
        text = "--" if soc is None else str(soc)
        font = None
        fsize = 40 if len(text) <= 2 else 30
        for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
            try:
                font = ImageFont.truetype(name, fsize)
                break
            except OSError:
                continue
        font = font or ImageFont.load_default()
        box = draw.textbbox((0, 0), text, font=font)
        tw, th = box[2] - box[0], box[3] - box[1]
        fill = (255, 255, 255)
        draw.text(((size - tw) / 2 - box[0], (size - th) / 2 - box[1]), text, font=font, fill=fill)
        if d and d["mode"] == "charging" and not self.stale():
            draw.rounded_rectangle((14, size - 8, size - 15, size - 4), radius=2, fill=(255, 255, 255))
        return img

    def _tray_title(self):
        d = self.data
        if not d or d["soc"] is None:
            return "EcoFlow - no data yet"
        lbl, val = time_label(d)
        title = f"EcoFlow {d['soc']}% | In {d['in']} W | Out {d['out']} W | {lbl} {val}"
        if self.stale():
            title += " | stale"
        return title[:127]

    def _start_tray(self):
        import pystray
        from pystray import Menu, MenuItem as Item

        def on_ui(fn):
            return lambda icon, item: self.post("call", fn)

        def port_item(name):
            return Item(PORTS[name]["label"] + " output", on_ui(lambda: self.toggle_port(name)),
                        checked=lambda item: bool(self.port_state(name)),
                        enabled=lambda item: self.port_state(name) is not None and name not in self.busy)

        menu = Menu(
            Item("Show / hide widget", on_ui(self.toggle_widget), default=True),
            Menu.SEPARATOR,
            port_item("ac"), port_item("dc"), port_item("usb"),
            Menu.SEPARATOR,
            Item("Refresh now", on_ui(self.refresh_now)),
            Item("Settings...", on_ui(self.open_settings)),
            Item("Open data folder", on_ui(self.open_data_folder)),
            Menu.SEPARATOR,
            Item("Quit", on_ui(self.quit)),
        )
        self.tray = pystray.Icon(APP_NAME, self._tray_image(), self._tray_title(), menu)
        self.tray.run_detached()

    def _update_tray(self):
        if not self.tray:
            return
        try:
            self.tray.icon = self._tray_image()
            self.tray.title = self._tray_title()
            self.tray.update_menu()
        except Exception:
            log.exception("tray update failed")

    # ------------------------------------------------ settings dialog
    def open_settings(self):
        if self.settings_win and self.settings_win.winfo_exists():
            self.settings_win.lift()
            self.settings_win.focus_force()
            return
        top = self.settings_win = tk.Toplevel(self.root)
        top.title("EcoFlow widget - settings")
        top.attributes("-topmost", True)
        top.resizable(False, False)
        frm = ttk.Frame(top, padding=self.S(16))
        frm.pack(fill="both", expand=True)

        ak = tk.StringVar(value=self.cfg["access_key"])
        sk = tk.StringVar(value=get_secret(self.cfg["access_key"]))
        interval = tk.StringVar(value=str(self.cfg["interval"]))
        status = tk.StringVar(value="Paste both keys from developer-eu.ecoflow.com and press Connect. "
                                    "The Secret Key is kept in Windows Credential Manager, not in a file.")
        devices = []

        ttk.Label(frm, text="Access Key").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm, textvariable=ak, width=44).grid(row=1, column=0, columnspan=2, sticky="we", pady=(0, 8))
        ttk.Label(frm, text="Secret Key").grid(row=2, column=0, sticky="w")
        sk_entry = ttk.Entry(frm, textvariable=sk, width=44, show="•")
        sk_entry.grid(row=3, column=0, columnspan=2, sticky="we", pady=(0, 8))
        show = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm, text="Show", variable=show,
                        command=lambda: sk_entry.config(show="" if show.get() else "•")).grid(row=2, column=1, sticky="e")

        ttk.Label(frm, text="Device (pick from the list, or type the serial number)").grid(row=4, column=0, columnspan=2, sticky="w")
        dev_box = ttk.Combobox(frm, width=42)
        dev_box.grid(row=5, column=0, columnspan=2, sticky="we", pady=(0, 8))
        if self.cfg["sn"]:
            dev_box["values"] = [f"{self.cfg['device_name'] or 'Device'} ({self.cfg['sn']})"]
            dev_box.current(0)
            devices.append({"sn": self.cfg["sn"], "deviceName": self.cfg["device_name"]})

        ttk.Label(frm, text="Refresh every (seconds)").grid(row=6, column=0, sticky="w")
        ttk.Spinbox(frm, from_=10, to=300, increment=5, textvariable=interval, width=6).grid(row=6, column=1, sticky="e")

        ttk.Label(frm, textvariable=status, wraplength=self.S(330), foreground="#555").grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(10, 10))

        btns = ttk.Frame(frm)
        btns.grid(row=8, column=0, columnspan=2, sticky="e")
        connect_btn = ttk.Button(btns, text="Connect")
        save_btn = ttk.Button(btns, text="Save")
        connect_btn.pack(side="left", padx=(0, 6))
        save_btn.pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="Cancel", command=top.destroy).pack(side="left")
        found_host = {"host": self.cfg["host"]}

        def connect():
            a, s = ak.get().strip(), sk.get().strip()
            if not a or not s:
                status.set("Enter both keys first.")
                return
            status.set("Connecting to EcoFlow...")
            connect_btn.state(["disabled"])

            def worker():
                try:
                    client = DemoClient() if DEMO else EcoFlowClient(a, s)
                    found = client.detect_host()
                    result = (client.host, found, None)
                except Exception as e:
                    result = (None, [], e)
                self.post("call", lambda: on_connected(*result))

            threading.Thread(target=worker, daemon=True).start()

        def on_connected(host, found, err):
            if not top.winfo_exists():
                return
            connect_btn.state(["!disabled"])
            if err:
                msg = "Keys rejected by EcoFlow. Check both keys." if isinstance(err, AuthError) else f"Failed: {err}"
                status.set(msg)
                return
            if not found:
                found_host["host"] = host
                status.set("Keys work, but EcoFlow lists no devices for them. Type your serial number "
                           "(SN on the unit or in the app) into Device and press Save to check it directly.")
                dev_box.focus_set()
                return
            found_host["host"] = host
            devices.clear()
            devices.extend(found)
            dev_box["values"] = [
                f"{x.get('deviceName') or x.get('productName') or 'Device'} ({x['sn']})"
                + ("" if int(x.get("online", 1)) else " - offline") for x in found]
            pick = next((i for i, x in enumerate(found) if x["sn"] == self.cfg["sn"]), 0)
            dev_box.current(pick)
            status.set(f"Connected. Found {len(found)} device(s). Press Save.")

        def finish(a, s, sn, name, host, secs):
            if not DEMO:
                try:
                    set_secret(a, s)
                except Exception as e:
                    status.set(f"Could not store the secret key: {e}")
                    return
            self.cfg.update(access_key=a, sn=sn, device_name=name or "EcoFlow", host=host or "", interval=secs)
            save_config(self.cfg)
            self.data, self.last_ok, self.error, self.online = None, None, None, True
            self._rebuild_client()   # also restarts the live feed for the new keys / device
            self.render()
            self._update_tray()
            top.destroy()

        def save():
            a, s = ak.get().strip(), sk.get().strip()
            if not a or not s:
                status.set("Enter both keys first.")
                return
            try:
                secs = max(10, min(300, int(float(interval.get()))))
            except ValueError:
                secs = 15
            text = dev_box.get().strip()
            idx = dev_box.current()
            values = list(dev_box["values"] or [])
            if 0 <= idx < len(devices) and idx < len(values) and text == values[idx]:
                dev = devices[idx]
                finish(a, s, dev["sn"], dev.get("deviceName"), found_host["host"], secs)
                return
            match = re.search(r"[A-Z0-9]{10,}", text.upper().replace("SN:", ""))
            if not match:
                status.set("Press Connect and pick a device, or type the serial number.")
                return
            sn = match.group(0)
            status.set(f"Checking {sn} with EcoFlow...")
            save_btn.state(["disabled"])

            def worker():
                client = DemoClient() if DEMO else EcoFlowClient(a, s)
                ok_host, errors = None, []
                for host in ([found_host["host"]] if found_host["host"] else []) + [h for h in HOSTS if h != found_host["host"]]:
                    client.host = host
                    try:
                        client.quota_all(sn)
                        ok_host = host
                        break
                    except Exception as e:
                        errors.append(e)
                        if isinstance(e, AuthError):
                            break
                self.post("call", lambda: on_checked(a, s, sn, ok_host, errors, secs))

            threading.Thread(target=worker, daemon=True).start()

        def on_checked(a, s, sn, host, errors, secs):
            if not top.winfo_exists():
                return
            save_btn.state(["!disabled"])
            if host:
                log.info("serial %s works on %s", sn, host)
                finish(a, s, sn, "DELTA 2 Max", host, secs)
                return
            err = errors[0] if errors else "unknown error"
            log.warning("serial %s rejected: %s", sn, "; ".join(str(e) for e in errors))
            if isinstance(err, AuthError):
                status.set("Keys rejected by EcoFlow. Check both keys.")
            else:
                status.set(f"EcoFlow says: {err}\n\nThis usually means the station is linked in the EcoFlow app "
                           "under a different account than the one your developer keys belong to.")

        connect_btn.config(command=connect)
        save_btn.config(command=save)
        top.update_idletasks()
        top.geometry(f"+{(top.winfo_screenwidth() - top.winfo_reqwidth()) // 2}+{(top.winfo_screenheight() - top.winfo_reqheight()) // 3}")
        top.focus_force()

    # ------------------------------------------------ misc
    def open_data_folder(self):
        if sys.platform == "win32":
            os.startfile(DATA_DIR)

    def quit(self):
        self.stop.set()
        self.wake.set()
        self._stop_feed()
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
        self.root.after(50, self.root.destroy)

    def run(self):
        self.root.mainloop()


def single_instance():
    """Prevent two copies running at once (Windows only)."""
    if sys.platform != "win32":
        return True
    import ctypes
    ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\EcoFlowWidgetMutex")
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


if __name__ == "__main__":
    if not single_instance():
        sys.exit(0)
    log.info("starting%s", " (demo)" if DEMO else "")
    App().run()
