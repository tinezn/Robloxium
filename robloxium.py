import customtkinter as ctk
import tkinter.messagebox as messagebox
import tkinter.simpledialog as simpledialog
import json
import threading
import subprocess
import psutil
import os
from launcher_detect import detect_custom_launcher
import time
import random
import requests
from discord import Embed
from datetime import datetime
from pytz import timezone as get_localzone
import win32gui
import win32process
import win32con
import win32api
from mss import mss
from PIL import Image
import pytesseract
import asyncio
import logging
import base64
import queue
import socket
import re
import tkinter as tk
import win32event
import msvcrt
from PIL import Image
import tempfile
import sys
from urllib.parse import quote  # Added for URL encoding
from pathlib import Path
import shutil
import html


try:
    import discord
    from discord.ext import commands, tasks
    DISCORD_AVAILABLE = True
except ImportError:
    DISCORD_AVAILABLE = False
    print("[WARNING] discord.py not installed. Discord features disabled.")

from cryptography.fernet import Fernet
import hashlib
import websockets
from urllib.parse import urlparse, parse_qs
import pyperclip

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# === CONFIGURATION ===
ERROR_SCAN_ENABLED = False
REPORT_CHANNEL_ID = None
BOT_TOKEN = None
ROBLOX_EXE = "RobloxPlayerBeta.exe"
STATE_KEYWORDS = ["Disconnected", "Error Code", "Reconnect", "Kicked", "Lost Connection", "Banned"]

# Load config if exists
CONFIG_FILE = os.path.join("AccountManagerData", "config.json")
os.makedirs("AccountManagerData", exist_ok=True)

default_config = {
    "bot_token": None,
    "channel_id": None,
    "error_scan_enabled": False,
    "last_place_id": "",
    "last_job_id": ""
}

config = default_config.copy()

if os.path.exists(CONFIG_FILE):
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            loaded_config = json.load(f)
        config.update(loaded_config)
    except Exception as e:
        logging.warning(f"Failed to load config.json: {e}")
else:
    # Create default config on first run
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4)
        logging.info("Created default config.json in AccountManagerData/")
    except:
        pass

BOT_TOKEN = config.get("bot_token")
REPORT_CHANNEL_ID = config.get("channel_id")
ERROR_SCAN_ENABLED = config.get("error_scan_enabled", False)

# === GLOBAL STATE ===
tracked_accounts = {}  # username: pid
tracked_hwnds = {}  # username: hwnd
launched_accounts = set()
last_errors = {}  # username: (keyword, timestamp)
error_counter = {}  # username: int
last_status_message = None
_discord_connected = False
_bot_running = False

state_lock = threading.Lock()
launch_lock = threading.Lock()

if getattr(sys, 'frozen', False):
    # Running as compiled .exe
    base_path = os.path.dirname(sys.executable)
    tesseract_path = os.path.join(base_path, "tesseract", "tesseract.exe")
    if os.path.exists(tesseract_path):
        pytesseract.pytesseract.tesseract_cmd = tesseract_path
        os.environ["TESSDATA_PREFIX"] = os.path.join(base_path, "tesseract", "tessdata")

def force_icon(window):
    """Forces the window icon to stay – fixes CTkToplevel icon disappearing bug"""
    try:
        window.iconbitmap("assets/logo.ico")
        # Force Tk to update the icon immediately and prevent overwriting
        window.update_idletasks()
        window.after(50, lambda: window.iconbitmap("assets/logo.ico"))
        window.after(100, lambda: window.iconbitmap("assets/logo.ico"))
        window.after(300, lambda: window.iconbitmap("assets/logo.ico"))
    except Exception as e:
        logging.warning(f"Could not force icon: {e}")

class ToolTip:
    def __init__(self, widget, text, delay=300):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.tip = None
        self.timer = None

        self.widget.bind("<Enter>", self.on_enter)
        self.widget.bind("<Leave>", self.on_leave)
        self.widget.bind("<ButtonPress>", self.on_leave)

    def on_enter(self, event=None):
        self.timer = self.widget.after(self.delay, self.show_tip)

    def on_leave(self, event=None):
        if self.timer:
            self.widget.after_cancel(self.timer)
            self.timer = None
        if self.tip:
            self.tip.destroy()
            self.tip = None

    def show_tip(self):
        if self.tip or not self.widget.winfo_exists():
            return

        x = self.widget.winfo_pointerx() + 15
        y = self.widget.winfo_pointery() + 15

        self.tip = ctk.CTkToplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")

        label = ctk.CTkLabel(
            self.tip,
            text=self.text,
            corner_radius=8,
            fg_color="#1e1e1e",
            text_color="#ffffff",
            padx=10,
            pady=6,
            font=ctk.CTkFont(size=12)
        )
        label.pack()

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

def log(msg):
    logging.info(msg)

def get_active_count():
    count = 0
    for proc in psutil.process_iter(['pid', 'name']):
        if proc.info['name'] == "RobloxPlayerBeta.exe":
            count += 1
    return count

def get_roblox_pids():
    return {p.info['pid'] for p in psutil.process_iter(['pid', 'name']) if p.info['name'] == ROBLOX_EXE}

def wait_for_new_pid(prev_pids, timeout=60):
    start = time.time()
    while time.time() - start < timeout:
        new = get_roblox_pids() - prev_pids
        if new:
            pid = next(iter(new))
            if psutil.pid_exists(pid):
                return pid
        time.sleep(1)
    return None

def get_hwnd_from_pid(pid):
    hwnds = []
    def enum(h, _):
        try:
            _, p = win32process.GetWindowThreadProcessId(h)
            if p == pid and win32gui.IsWindowVisible(h):
                hwnds.append(h)
        except:
            pass
    win32gui.EnumWindows(enum, None)
    return hwnds[0] if hwnds else None

def wait_for_hwnd(pid, timeout=30):
    start = time.time()
    while time.time() - start < timeout:
        hwnd = get_hwnd_from_pid(pid)
        if hwnd:
            return hwnd
        time.sleep(1)
    return None

class DiscordBot:
    def __init__(self, app):
        self.app = app
        intents = discord.Intents.default()
        intents.message_content = True
        self.bot = commands.Bot(command_prefix="!", intents=intents)

        @self.bot.event
        async def on_ready():
            logging.info(f"Discord bot connected as {self.bot.user}")
            self.app.discord_status_label.configure(text="Discord: Active", text_color="#4caf50")
            global _discord_connected
            _discord_connected = True

        @self.bot.command()
        async def ping(ctx):
            await ctx.send("Pong!")

        @self.bot.command()
        async def status(ctx):
            active = get_active_count()
            await ctx.send(f"Current status: {active} active accounts\nAccounts: {', '.join(self.app.manager.accounts.keys())}")

        @self.bot.command()
        async def launch(ctx, username: str):
            if username in self.app.manager.accounts:
                place_id, job_id = self.app.get_server_info()
                self.app.manager.launch_roblox(username, place_id, job_id=job_id)
                await ctx.send(f"Launched {username}")
            else:
                await ctx.send(f"Account {username} not found")

        @self.bot.command()
        async def restart(ctx, username: str):
            if username in self.app.manager.accounts:
                if username in tracked_accounts:
                    try:
                        psutil.Process(tracked_accounts[username]).kill()
                    except:
                        pass
                    with state_lock:
                        tracked_accounts.pop(username, None)
                        tracked_hwnds.pop(username, None)
                with state_lock:
                    launched_accounts.discard(username)
                place_id, job_id = self.app.get_server_info()
                self.app.manager.launch_roblox(username, place_id, job_id=job_id)
                await ctx.send(f"Restarted {username}")
            else:
                await ctx.send(f"Account {username} not found")

        @self.bot.command()
        async def launchall(ctx):
            self.app.launch_all()
            await ctx.send("Launched all accounts")

        @self.bot.command()
        async def killall(ctx):
            self.app.kill_all()
            await ctx.send("Killed all instances")

        @self.bot.command()
        async def toggleocr(ctx):
            self.app.ocr_var.set(not self.app.ocr_var.get())
            self.app.toggle_ocr()
            await ctx.send(f"Error Scan {'enabled' if self.app.ocr_var.get() else 'disabled'}")

    async def send_report(self):
        channel = self.bot.get_channel(REPORT_CHANNEL_ID)
        if not channel:
            logging.warning("Report failed: Channel not found or no permission")
            self.app.report_label.configure(text=" • Report: Failed", text_color="#ffcc00")
            return

        try:
            global last_status_message
            if last_status_message:
                try:
                    old = await channel.fetch_message(last_status_message.id)
                    await old.delete()
                except discord.errors.Forbidden:
                    logging.warning("No permission to delete message")
                except:
                    pass

            with state_lock:
                all_accounts = sorted(
                    launched_accounts |
                    set(tracked_accounts.keys()) |
                    set(last_errors.keys())
                )

                status_lines = []
                for name in all_accounts:
                    pid = tracked_accounts.get(name)
                    hwnd = tracked_hwnds.get(name)
                    error = last_errors.get(name)

                    if error:
                        keyword, _ = error
                        status_lines.append(f"**{name}** - Inactive – {keyword}")
                    elif pid and hwnd and psutil.pid_exists(pid) and win32gui.IsWindow(hwnd):
                        status_lines.append(f"**{name}** - Active")
                    else:
                        status_lines.append(f"**{name}** - Inactive")

            embed = Embed(
                title="Account Status Report",
                description="\n".join(status_lines),
                color=0x00ff00,
                timestamp=datetime.now(get_localzone())
            )
            embed.set_footer(text="Updated on error detection")

            sent_message = await channel.send(embed=embed)
            last_status_message = sent_message
            logging.info("Report sent successfully")
            self.app.report_label.configure(text=" • Report: ON", text_color="#4caf50")

        except Exception as e:
            logging.error(f"Report failed: {e}")
            self.app.report_label.configure(text=" • Report: Failed", text_color="#ffcc00")

class ModernRobloxManager(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Robloxium")
        self.geometry("1100x680")
        self.minsize(980, 600)
        self.configure(fg_color="#0a0a0a")

        # Load images
        self.iconbitmap("assets/logo.ico")
        banner_pil = Image.open("assets/banner.png")
        self.banner_image = ctk.CTkImage(light_image=banner_pil, dark_image=banner_pil, size=(banner_pil.width // 7, banner_pil.height // 7))  # Scale if necessary; adjust as needed

        self.manager = RobloxAccountManager(password="default")  # Use a secure password in production
        self.settings_window = None
        self.help_window = None
        self.selected_accounts = set()
        self.check_vars = {}
        self.discord_bot = None
        self.account_widgets = {}  # For optimizing populate_accounts
        self.gui_queue = queue.Queue()
        self.connected_clients = {}  # username: websocket
        self.ws_server_thread = None
        self.multi_roblox_handle = None
        self.start_ws_server()

        # Load persistent history
        self.history_file = os.path.join(os.path.dirname(__file__), "AccountManagerData", "history.json")
        os.makedirs(os.path.dirname(self.history_file), exist_ok=True)
        if not os.path.exists(self.history_file):
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump({"place_history": [], "job_history": []}, f, indent=2)
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                self.history = json.load(f)
        except Exception:
            self.history = {"place_history": [], "job_history": []}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.build_ui()
        self.enable_multi_roblox()
        self.populate_accounts()
        self.update_active()
        if BOT_TOKEN and DISCORD_AVAILABLE:
            self.connect_bot()
        self.after(100, self.process_gui_queue)

    def process_gui_queue(self):
        try:
            while not self.gui_queue.empty():
                func = self.gui_queue.get()
                try:
                    func()
                except Exception as e:
                    logging.error(f"GUI queue error: {e}")
        except Exception as e:
            logging.error(f"Queue error: {e}")
        self.after(100, self.process_gui_queue)

    def is_port_in_use(self, port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(('localhost', port)) == 0

    async def handle_client(self, websocket):
        path = websocket.request.path
        query = urlparse(path).query
        params = parse_qs(query)
        username = params.get('name', ['Unknown'])[0]
        user_id = params.get('id', ['0'])[0]

        if username in self.manager.accounts:
            logging.info(f"Client connected: {username} (ID: {user_id})")
            self.connected_clients[username] = websocket
            self.gui_queue.put(self.populate_accounts)

            try:
                async for message in websocket:
                    logging.info(f"Received from {username}: {message}")
                    if message == 'ping':
                        await websocket.send('pong')
                    try:
                        data = json.loads(message)
                        if data.get('Name') == 'error_detected':
                            error_code = data.get('Payload', {}).get('code', 'Unknown')
                            logging.info(f"Error detected for {username}: Code {error_code}")
                            self.gui_queue.put(lambda u=username: self.restart_account(u))
                    except json.JSONDecodeError:
                        logging.warning(f"Invalid JSON from {username}: {message}")
            finally:
                logging.info(f"Client disconnected: {username}")
                self.connected_clients.pop(username, None)
                self.gui_queue.put(self.populate_accounts)
        else:
            logging.warning(f"Unknown client: {username}")
            await websocket.close()

    def start_ws_server(self):
        if self.ws_server_thread:
            return

        if self.is_port_in_use(5242):
            logging.error("Port 5242 is in use. Cannot start WS server.")
            messagebox.showerror("Port Error", "Port 5242 is already in use. Close other instances or programs using it.")
            return

        async def server():
            async with websockets.serve(self.handle_client, "localhost", 5242):
                await asyncio.Future()

        def run_server():
            asyncio.run(server())

        self.ws_server_thread = threading.Thread(target=run_server, daemon=True)
        self.ws_server_thread.start()
        logging.info("WebSocket server started on ws://localhost:5242")

    def on_closing(self):
        for ws in list(self.connected_clients.values()):
            try:
                asyncio.run_coroutine_threadsafe(ws.close(), asyncio.get_event_loop())
            except:
                pass
        config['last_place_id'] = self.place_entry.get().strip()
        config['last_job_id'] = self.job_entry.get().strip()
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4)
        # Save persistent history
        try:
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(self.history, f, indent=2)
        except Exception as e:
            print(f"Failed to save history: {e}")
        self.destroy()

    def build_ui(self):
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        # Main container
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=0, column=0, sticky="nsew", padx=15, pady=15)
        main.grid_columnconfigure(0, weight=4)  # Larger weight for accounts
        main.grid_columnconfigure(1, weight=1)  # Smaller for controls panel
        main.grid_rowconfigure(1, weight=1)

        # ==================== 1. TOP BAR ====================
        top_bar = ctk.CTkFrame(main, height=72, fg_color="#111114", corner_radius=16)
        top_bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        top_bar.grid_propagate(False)

        ctk.CTkLabel(top_bar, image=self.banner_image, text="").pack(side="left", padx=24, pady=20)

        # Right side: Status + Buttons
        right_frame = ctk.CTkFrame(top_bar, fg_color="transparent")
        right_frame.pack(side="right", padx=18, pady=20)

        # Settings & Help buttons
        settings_btn = ctk.CTkButton(
            right_frame, text="⚙", width=36, height=36, corner_radius=20,
            fg_color="#111114", hover_color="#111114", font=ctk.CTkFont(size=20),
            command=self.open_settings
        )
        settings_btn.pack(side="right")
        ToolTip(settings_btn, "Open Settings (Discord Bot Token, etc.)")

        help_btn = ctk.CTkButton(
            right_frame, text="❓", width=36, height=36, corner_radius=18,
            fg_color="#111114", hover_color="#111114", font=ctk.CTkFont(size=18),
            command=self.show_help
        )
        help_btn.pack(side="right", padx=(0, 10))
        ToolTip(help_btn, "Click to view How to Use guide")

        # Status indicators
        self.status_frame = ctk.CTkFrame(right_frame, fg_color="transparent")
        self.status_frame.pack(side="right", padx=(0, 20))

        self.discord_status_label = ctk.CTkLabel(self.status_frame, text="Discord: Offline", font=ctk.CTkFont(size=14), text_color="#ff4444", width=140, anchor="w")
        self.discord_status_label.pack(side="right", padx=(0, 24))

        self.ocr_label = ctk.CTkLabel(self.status_frame, text=" • OCR: OFF", font=ctk.CTkFont(size=14), text_color="#888", width=90, anchor="w")
        self.ocr_label.pack(side="left", padx=(8, 0))

        self.report_label = ctk.CTkLabel(self.status_frame, text=" • Report: OFF", font=ctk.CTkFont(size=14), text_color="#888", width=110, anchor="w")
        self.report_label.pack(side="left", padx=(8, 0))

        self.active_label = ctk.CTkLabel(
            right_frame, text="0 active", font=ctk.CTkFont(size=14, weight="bold"), text_color="#888", width=80, anchor="w"
        )
        self.active_label.pack(side="left")

        # ==================== 2. ACCOUNTS PANEL (LEFT, EXPANDABLE) ====================
        accounts_panel = ctk.CTkFrame(main, fg_color="#111114", corner_radius=16)
        accounts_panel.grid(row=1, column=0, sticky="nsew", pady=(0, 12))
        accounts_panel.grid_columnconfigure(0, weight=1)
        accounts_panel.grid_rowconfigure(1, weight=1)

        # Header
        header = ctk.CTkFrame(accounts_panel, height=48, fg_color="#19191e", corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        ctk.CTkLabel(header, text="Accounts", font=ctk.CTkFont(size=17, weight="bold"),
                     text_color="#cccccc").pack(side="left", padx=20, pady=12)

        # Scrollable accounts list
        self.accounts_frame = ctk.CTkScrollableFrame(
            accounts_panel, fg_color="#0c0c0f", corner_radius=14
        )
        self.accounts_frame.grid(row=1, column=0, sticky="nsew", padx=18, pady=18)

        # ==================== 3. ACCOUNT CONTROLS PANEL (RIGHT) ====================
        controls_panel = ctk.CTkFrame(main, fg_color="#111114", corner_radius=16)
        controls_panel.grid(row=1, column=1, sticky="nsew", pady=(0, 12), padx=(12, 0))
        controls_panel.grid_columnconfigure(0, weight=1)

        # Header
        controls_header = ctk.CTkFrame(controls_panel, height=48, fg_color="#19191e", corner_radius=0)
        controls_header.grid(row=0, column=0, sticky="ew")
        controls_header.grid_propagate(False)
        ctk.CTkLabel(controls_header, text="Account Controls", font=ctk.CTkFont(size=17, weight="bold"),
                     text_color="#cccccc").pack(side="left", padx=20, pady=12)

        # Inputs
        input_frame = ctk.CTkFrame(controls_panel, fg_color="transparent")
        input_frame.grid(row=1, column=0, sticky="nsew", padx=18, pady=10)

        # --- Custom Dropdown for Place ID ---
        # Row for Place ID label and game name label
        place_label_row = ctk.CTkFrame(input_frame, fg_color="transparent")
        place_label_row.pack(anchor="w", fill="x")
        ctk.CTkLabel(place_label_row, text="Place ID").pack(side="left")
        self.place_game_label = ctk.CTkLabel(place_label_row, text="", font=ctk.CTkFont(size=12), text_color="#aaa")
        self.place_game_label.pack(side="left", padx=(8, 0))
        self.place_entry_var = ctk.StringVar(value=config.get("last_place_id", ""))
        place_entry_row = ctk.CTkFrame(input_frame, fg_color="transparent")
        place_entry_row.pack(fill="x", pady=5)
        self.place_entry = ctk.CTkEntry(place_entry_row, textvariable=self.place_entry_var)
        self.place_entry.pack(side="left", fill="x", expand=True)

        def update_place_game_label(event=None):
            place_id = self.place_entry_var.get().strip()
            if place_id.isdigit():
                self.place_game_label.configure(text="Loading...")
                def fetch_and_update():
                    name = RobloxAPI.get_game_name(place_id)
                    if name:
                        self.place_game_label.configure(text=f"{name}")
                    else:
                        self.place_game_label.configure(text="")
                threading.Thread(target=fetch_and_update, daemon=True).start()
            else:
                self.place_game_label.configure(text="")

        # Update label when Place ID changes
        self.place_entry_var.trace_add("write", lambda *a: update_place_game_label())
        # Also update when selecting from dropdown
        def select_place_history(val):
            self.place_entry_var.set(val)
            hide_place_dropdown()
            update_place_game_label()

        update_place_game_label()

        # Place ID dropdown logic (functions must be defined before button)
        self.place_history = list(self.history.get("place_history", []))[-5:]
        self.place_dropdown = None

        # Cache for place_id -> game name
        self.place_id_name_cache = {}

        def show_place_dropdown(event=None):
            if self.place_dropdown:
                self.place_dropdown.destroy()
                self.place_dropdown = None
                return
            if not self.place_history:
                return
            self.place_dropdown = ctk.CTkFrame(input_frame, fg_color="#23232a", corner_radius=8)
            self.place_dropdown.place(in_=self.place_entry, relx=0, rely=1, relwidth=1)

            def make_btn(place_id):
                # Use cached name if available, else fetch
                name = self.place_id_name_cache.get(place_id)
                if name is None:
                    name = RobloxAPI.get_game_name(place_id)
                    self.place_id_name_cache[place_id] = name or "(Unknown)"
                display = name or "(Unknown)"
                btn = ctk.CTkButton(self.place_dropdown, text=display, width=1, height=28, fg_color="#23232a", hover_color="#333348", anchor="w",
                                    font=ctk.CTkFont(size=13), command=lambda v=place_id: select_place_history(v))
                btn.pack(fill="x")

            for val in reversed(self.place_history):
                make_btn(val)

        def hide_place_dropdown(event=None):
            # Store current input in history if valid, not empty, and not duplicate
            val = self.place_entry_var.get().strip()
            if val.isdigit() and val not in self.place_history:
                self.place_history.append(val)
                if len(self.place_history) > 5:
                    self.place_history = self.place_history[-5:]
                # Update persistent history
                self.history["place_history"] = self.place_history
            if self.place_dropdown:
                self.place_dropdown.destroy()
                self.place_dropdown = None

        def select_place_history(val):
            self.place_entry_var.set(val)
            hide_place_dropdown()

        # self.place_entry.bind("<FocusIn>", show_place_dropdown)  # Removed: don't show dropdown on focus
        self.place_entry.bind("<FocusOut>", lambda e: self.after(150, hide_place_dropdown))
        self.place_entry.bind("<Key>", hide_place_dropdown)
        place_dd_btn = ctk.CTkButton(place_entry_row, text="▼", width=32, height=32, fg_color="#23232a", hover_color="#333348", font=ctk.CTkFont(size=14), command=show_place_dropdown)
        place_dd_btn.pack(side="right", padx=(4, 0))

        # (Removed duplicate assignment that overwrites loaded history)

        def show_place_dropdown(event=None):
            if self.place_dropdown:
                self.place_dropdown.destroy()
            if not self.place_history:
                return
            self.place_dropdown = ctk.CTkFrame(input_frame, fg_color="#23232a", corner_radius=8)
            self.place_dropdown.place(in_=self.place_entry, relx=0, rely=1, relwidth=1)
            for val in reversed(self.place_history):
                btn = ctk.CTkButton(self.place_dropdown, text=val, width=1, height=28, fg_color="#23232a", hover_color="#333348", anchor="w",
                                    font=ctk.CTkFont(size=13), command=lambda v=val: select_place_history(v))
                btn.pack(fill="x")

        def hide_place_dropdown(event=None):
            if self.place_dropdown:
                self.place_dropdown.destroy()
                self.place_dropdown = None

        def select_place_history(val):
            self.place_entry_var.set(val)
            hide_place_dropdown()

        self.place_entry.bind("<FocusIn>", show_place_dropdown)
        self.place_entry.bind("<FocusOut>", lambda e: self.after(150, hide_place_dropdown))
        self.place_entry.bind("<Key>", hide_place_dropdown)

        # --- Custom Dropdown for Job ID ---
        ctk.CTkLabel(input_frame, text="Job ID (Optional)").pack(anchor="w")
        self.job_entry_var = ctk.StringVar(value=config.get("last_job_id", ""))
        job_entry_row = ctk.CTkFrame(input_frame, fg_color="transparent")
        job_entry_row.pack(fill="x", pady=5)
        self.job_entry = ctk.CTkEntry(job_entry_row, textvariable=self.job_entry_var)
        self.job_entry.pack(side="left", fill="x", expand=True)

        # Job ID dropdown logic (functions must be defined before button)
        self.job_history = list(self.history.get("job_history", []))[-5:]
        self.job_dropdown = None

        def show_job_dropdown(event=None):
            if self.job_dropdown:
                self.job_dropdown.destroy()
            if not self.job_history:
                return
            self.job_dropdown = ctk.CTkFrame(input_frame, fg_color="#23232a", corner_radius=8)
            self.job_dropdown.place(in_=self.job_entry, relx=0, rely=1, relwidth=1)
            for val in reversed(self.job_history):
                btn = ctk.CTkButton(self.job_dropdown, text=val, width=1, height=28, fg_color="#23232a", hover_color="#333348", anchor="w",
                                    font=ctk.CTkFont(size=13), command=lambda v=val: select_job_history(v))
                btn.pack(fill="x")

        def hide_job_dropdown(event=None):
            # Store current input in history if not empty and not duplicate
            val = self.job_entry_var.get().strip()
            if val and val not in self.job_history:
                self.job_history.append(val)
                if len(self.job_history) > 5:
                    self.job_history = self.job_history[-5:]
                # Update persistent history
                self.history["job_history"] = self.job_history
            if self.job_dropdown:
                self.job_dropdown.destroy()
                self.job_dropdown = None

        def select_job_history(val):
            self.job_entry_var.set(val)
            hide_job_dropdown()

        self.job_entry.bind("<FocusIn>", show_job_dropdown)
        self.job_entry.bind("<FocusOut>", lambda e: self.after(150, hide_job_dropdown))
        self.job_entry.bind("<Key>", hide_job_dropdown)
        job_dd_btn = ctk.CTkButton(job_entry_row, text="▼", width=32, height=32, fg_color="#23232a", hover_color="#333348", font=ctk.CTkFont(size=14), command=show_job_dropdown)
        job_dd_btn.pack(side="right", padx=(4, 0))

        self.job_history = config.get("job_history", [])[-5:]
        self.job_dropdown = None

        def show_job_dropdown(event=None):
            if self.job_dropdown:
                self.job_dropdown.destroy()
            if not self.job_history:
                return
            self.job_dropdown = ctk.CTkFrame(input_frame, fg_color="#23232a", corner_radius=8)
            self.job_dropdown.place(in_=self.job_entry, relx=0, rely=1, relwidth=1)
            for val in reversed(self.job_history):
                btn = ctk.CTkButton(self.job_dropdown, text=val, width=1, height=28, fg_color="#23232a", hover_color="#333348", anchor="w",
                                    font=ctk.CTkFont(size=13), command=lambda v=val: select_job_history(v))
                btn.pack(fill="x")

        def hide_job_dropdown(event=None):
            if self.job_dropdown:
                self.job_dropdown.destroy()
                self.job_dropdown = None

        def select_job_history(val):
            self.job_entry_var.set(val)
            hide_job_dropdown()

        self.job_entry.bind("<FocusIn>", show_job_dropdown)
        self.job_entry.bind("<FocusOut>", lambda e: self.after(150, hide_job_dropdown))
        self.job_entry.bind("<Key>", hide_job_dropdown)

        # Buttons
        btn_frame = ctk.CTkFrame(controls_panel, fg_color="transparent")
        btn_frame.grid(row=2, column=0, sticky="ew", padx=18, pady=10)

        def create_styled_btn(parent, text, cmd, fg="#1a1a1f", hover="#26262e", width=148):
            b = ctk.CTkButton(
                parent, text=text, command=cmd, width=width, height=44,
                corner_radius=12, fg_color=fg, hover_color=hover,
                font=ctk.CTkFont(size=14, weight="bold")
            )
            b.pack(fill="x", pady=5)
            return b

        join_btn = create_styled_btn(btn_frame, "Join Server", self.join_server)
        ToolTip(join_btn, "Join selected accounts to the specified server")

        launch_all_btn = create_styled_btn(btn_frame, "Launch All", self.launch_all)
        ToolTip(launch_all_btn, "Launch every account")

        kill_all_btn = create_styled_btn(btn_frame, "Kill All", self.kill_all)
        ToolTip(kill_all_btn, "Close all running Roblox instances")

        import_btn = create_styled_btn(btn_frame, "Import Cookie", self.import_cookie, fg="#1a6333", hover="#2d8a4d")
        ToolTip(import_btn, "Import account from .ROBLOSECURITY cookie")

        # ==================== 4. CONTROLS (BOTTOM, FIXED) ====================
        bottom_controls = ctk.CTkFrame(main, height=68, fg_color="#131316", corner_radius=14)
        bottom_controls.grid(row=2, column=0, columnspan=2, sticky="ew")
        bottom_controls.grid_propagate(False)
        for i in range(7):
            bottom_controls.grid_columnconfigure(i, weight=1)

        def create_bottom_btn(parent, text, cmd, col, fg="#1a1a1f", hover="#26262e"):
            b = ctk.CTkButton(
                parent, text=text, command=cmd, width=148, height=44,
                corner_radius=12, fg_color=fg, hover_color=hover,
                font=ctk.CTkFont(size=14, weight="bold")
            )
            b.grid(row=0, column=col, padx=9, pady=10)
            return b

        add_btn = create_bottom_btn(bottom_controls, "Add Account", self.add_account_btn, 0)
        ToolTip(add_btn, "Add a new account")

        self.remove_btn = create_bottom_btn(bottom_controls, "Remove", self.remove_accounts, 1, fg="#2d1122", hover="#551133")
        ToolTip(self.remove_btn, "Remove selected accounts")
        self.remove_btn.configure(state="disabled")

        open_browser_btn = create_bottom_btn(bottom_controls, "Open Browser", self.open_browser, 2)
        ToolTip(open_browser_btn, "Open browser for account management")

        copy_nexus_btn = create_bottom_btn(bottom_controls, "Copy Nexus Lua", self.copy_nexus_to_clipboard, 3)
        copy_nexus_btn.configure(state="disabled")
        ToolTip(copy_nexus_btn, "In development: Nexus")

        # Switches on the right
        self.ocr_var = ctk.BooleanVar(value=ERROR_SCAN_ENABLED)
        ocr_switch = ctk.CTkSwitch(
            bottom_controls, text="Error Scan", variable=self.ocr_var,
            progress_color="#eee3f7", button_color="#333", button_hover_color="#555",
            font=ctk.CTkFont(size=13), command=self.toggle_ocr
        )
        ocr_switch.grid(row=0, column=4, padx=20)
        ToolTip(ocr_switch, "Enable error detection")

        self.report_var = ctk.BooleanVar(value=False)
        report_switch = ctk.CTkSwitch(
            bottom_controls, text="Discord Report", variable=self.report_var,
            progress_color="#eee3f7", button_color="#333", button_hover_color="#555",
            font=ctk.CTkFont(size=13), command=self.toggle_report
        )
        report_switch.grid(row=0, column=5, padx=20)
        ToolTip(report_switch, "Automatically send account status report to Discord on error")

    def import_cookie(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Import Cookie")
        dialog.geometry("400x200")
        dialog.configure(fg_color="#0a0a0a")
        dialog.wm_overrideredirect(True)  # Remove titlebar to prevent moving

        # Center to main GUI
        x = self.winfo_rootx() + (self.winfo_width() // 2) - (400 // 2)
        y = self.winfo_rooty() + (self.winfo_height() // 2) - (200 // 2)
        dialog.geometry(f"+{x}+{y}")

        ctk.CTkLabel(dialog, text="Paste .ROBLOSECURITY cookie:").pack(pady=10)

        entry = ctk.CTkEntry(dialog, show="*")
        entry.pack(pady=10, padx=20, fill="x")

        def do_import():
            cookie = entry.get().strip()
            if cookie:
                success, username = self.manager.import_cookie_account(cookie)
                if success:
                    messagebox.showinfo("Success", f"Imported {username}")
                    self.populate_accounts()
                else:
                    messagebox.showerror("Error", "Invalid cookie")
            dialog.destroy()

        import_btn = ctk.CTkButton(dialog, text="Import", command=do_import)
        import_btn.pack(pady=10)

        cancel_btn = ctk.CTkButton(dialog, text="Cancel", command=dialog.destroy)
        cancel_btn.pack()

    def enable_multi_roblox(self):
        """Enable Multi Roblox + 773 fix"""
        # hello programmers! I know you're reading this code, because you want to know how did I implement this feature in Python. (and most importantly, the 773 fix)
        # because of that, I'll leave some comments here to help you understand.
        import subprocess
        import win32event
        import win32api
        
        if self.multi_roblox_handle is not None:
            self.disable_multi_roblox()
        
        try:
            result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq RobloxPlayerBeta.exe'], 
                                  capture_output=True, text=True, encoding='utf-8', errors='replace') # checks running processes
            
            if result.stdout and 'RobloxPlayerBeta.exe' in result.stdout:
                response = messagebox.askquestion( # ask user for permission
                    "Roblox Already Running",
                    "A Roblox instance is already running.\n\n"
                    "To use Multi Roblox, you need to close all Roblox instances first.\n\n"
                    "Do you want to close all Roblox instances now?",
                    icon='warning'
                )
                
                if response == 'yes':
                    subprocess.run(['taskkill', '/F', '/IM', 'RobloxPlayerBeta.exe'], 
                                 capture_output=True, text=True, encoding='utf-8', errors='replace') # closes roblox
                    messagebox.showinfo("Success", "All Roblox instances have been closed.")
                else:
                    return False
            
            # then here's the magic:
            # to enable multi roblox, we create the mutex before roblox creates it.
            # this means, when roblox starts, it cannot be created by roblox again.
            # thus, allowing multiple instances to run. Simple, right? (doesn't fix 773 yet)
            mutex = win32event.CreateMutex(None, True, "ROBLOX_singletonEvent")
            print("[INFO] Multi Roblox activated.")
            
            # check if mutex already existed (GetLastError returns ERROR_ALREADY_EXISTS = 183)
            if win32api.GetLastError() == 183:
                print("[WARNING] Mutex already exists. Taking ownership...")
            
            # now let's get over on the 773 fix part
            # first, we need to find the RobloxCookies.dat file
            cookies_path = os.path.join(
                os.getenv('LOCALAPPDATA'),
                r'Roblox\LocalStorage\RobloxCookies.dat'
            )
            
            cookie_file = None
            if os.path.exists(cookies_path):
                try:
                    # to actually apply the 773 fix, we need to lock the cookies file
                    # this prevents roblox from accessing it, which causes error 773 to not appear
                    # and there, you have it, multi roblox + 773 fix!
                    cookie_file = open(cookies_path, 'r+b')
                    msvcrt.locking(cookie_file.fileno(), msvcrt.LK_NBLCK, os.path.getsize(cookies_path))
                    print("[INFO] Error 773 fix applied.")

                except OSError:
                    print("[ERROR] Could not lock RobloxCookies.dat. It may already be locked.")

            else:
                print("[ERROR] Cookies file not found. 773 fix skipped.")

            self.multi_roblox_handle = {'mutex': mutex, 'file': cookie_file, 'cookies_path': cookies_path}
            return True
        except Exception as e:
            messagebox.showerror("Error", f"Failed to enable Multi Roblox: {str(e)}")
            return False

    def disable_multi_roblox(self):
        """Disable Multi Roblox and release resources"""
        if self.multi_roblox_handle is not None:
            try:
                win32api.CloseHandle(self.multi_roblox_handle['mutex'])
            except Exception as e:
                logging.warning(f"Failed to close mutex: {e}")

            if self.multi_roblox_handle.get('file'):
                try:
                    msvcrt.locking(self.multi_roblox_handle['file'].fileno(), msvcrt.LK_UNLCK, os.path.getsize(self.multi_roblox_handle['cookies_path']))
                    self.multi_roblox_handle['file'].close()
                except Exception as e:
                    logging.warning(f"Failed to unlock/close cookies file: {e}")

            self.multi_roblox_handle = None
            logging.info("Multi Roblox disabled.")

    def copy_nexus_to_clipboard(self):
        nexus_code = """
if Nexus then Nexus:Stop() end

if not game:IsLoaded() then
    task.delay(60, function()
        if NoShutdown then return end

        if not game:IsLoaded() then
            return game:Shutdown();
        end

        local Code = game:GetService'GuiService':GetErrorCode().Value

        if Code >= Enum.ConnectionError.DisconnectErrors.Value then
            return game:Shutdown();
        end
    end)
    
    game.Loaded:Wait()
end

local Nexus = {}
local WSConnect = syn and syn.websocket.connect or
    (Krnl and (function() repeat task.wait() until Krnl.WebSocket and Krnl.WebSocket.connect return Krnl.WebSocket.connect end)()) or
    WebSocket and WebSocket.connect

if not WSConnect then
    if messagebox then
        messagebox(('Nexus encountered an error while launching!%s'):format('Your exploit (' .. (identifyexecutor and identifyexecutor() or 'UNKNOWN') .. ') is not supported'), 'Roblox Account Manager', 0)
    end
    
    return
end

local TeleportService = game:GetService'TeleportService'
local InputService = game:GetService'UserInputService'
local HttpService = game:GetService'HttpService'
local RunService = game:GetService'RunService'
local GuiService = game:GetService'GuiService'
local Players = game:GetService'Players'
local LocalPlayer = Players.LocalPlayer; if not LocalPlayer then repeat LocalPlayer = Players.LocalPlayer task.wait() until LocalPlayer end; task.wait(0.5)

local UGS = UserSettings():GetService'UserGameSettings'
local OldVolume = UGS.MasterVolume

LocalPlayer.OnTeleport:Connect(function(State)
    if State == Enum.TeleportState.Started and Nexus.IsConnected then
        Nexus:Stop() -- Apparently doesn't disconnect websockets on teleport so this has to be here
    end
end)

local Signal = {} do
    Signal.__index = Signal

    function Signal.new()
        local self = setmetatable({ _BindableEvent = Instance.new'BindableEvent' }, Signal)
        
        return self
    end

    function Signal:Connect(Callback)
        assert(typeof(Callback) == 'function', 'function expected, got ' .. typeof(Callback))

        return self._BindableEvent.Event:Connect(Callback)
    end

    function Signal:Fire(...)
        self._BindableEvent:Fire(...)
    end

    function Signal:Wait()
        return self._BindableEvent.Event:Wait()
    end

    function Signal:Disconnect()
        if self._BindableEvent then
            self._BindableEvent:Destroy()
        end
    end
end

do -- Nexus
    local BTN_CLICK = 'ButtonClicked:'

    Nexus.Connected = Signal.new()
    Nexus.Disconnected = Signal.new()
    Nexus.MessageReceived = Signal.new()

    Nexus.Commands = {}
    Nexus.Connections = {}

    Nexus.ShutdownTime = 45
    Nexus.ShutdownOnTeleportError = true

    function Nexus:Send(Command, Payload)
        assert(self.Socket ~= nil, 'websocket is nil')
        assert(self.IsConnected, 'websocket not connected')
        assert(typeof(Command) == 'string', 'Command must be a string, got ' .. typeof(Command))

        if Payload then
            assert(typeof(Payload) == 'table', 'Payload must be a table, got ' .. typeof(Payload))
        end

        local Message = HttpService:JSONEncode {
            Name = Command,
            Payload = Payload
        }

        self.Socket:Send(Message)
    end

    function Nexus:SetAutoRelaunch(Enabled)
        self:Send('SetAutoRelaunch', { Content = Enabled and 'true' or 'false' })
    end
    
    function Nexus:SetPlaceId(PlaceId)
        self:Send('SetPlaceId', { Content = PlaceId })
    end
    
    function Nexus:SetJobId(JobId)
        self:Send('SetJobId', { Content = JobId })
    end

    function Nexus:Echo(Message)
        self:Send('Echo', { Content = Message })
    end

    function Nexus:Log(...)
        local T = {}

        for Index, Value in pairs{ ... } do
            table.insert(T, tostring(Value))
        end

        self:Send('Log', {
            Content = table.concat(T, ' ')
        })
    end

    function Nexus:CreateElement(ElementType, Name, Content, Size, Margins, Table)
        assert(typeof(Name) == 'string', 'string expected on argument #1, got ' .. typeof(Name))
        assert(typeof(Content) == 'string', 'string expected on argument #2, got ' .. typeof(Content))

        assert(Name:find'%W' == nil, 'argument #1 cannot contain whitespace')

        if Size then assert(typeof(Size) == 'table' and #Size == 2, 'table with 2 arguments expected on argument #3, got ' .. typeof(Size)) end
        if Margins then assert(typeof(Margins) == 'table' and #Margins == 4, 'table with 4 arguments expected on argument #4, got ' .. typeof(Margins)) end
        
        local Payload = {
            Name = Name,
            Content = Content,
            Size = Size and table.concat(Size, ','),
            Margin = Margins and table.concat(Margins, ',')
        }

        if Table then
            for Index, Value in pairs(Table) do
                Payload[Index] = Value
            end
        end

        self:Send(ElementType, Payload)
    end

    function Nexus:CreateButton(...)
        return self:CreateElement('CreateButton', ...)
    end

    function Nexus:CreateTextBox(...)
        return self:CreateElement('CreateTextBox', ...)
    end

    function Nexus:CreateNumeric(Name, Value, DecimalPlaces, Increment, Size, Margins)
        return self:CreateElement('CreateNumeric', Name, tostring(Value), Size, Margins, { DecimalPlaces = DecimalPlaces, Increment = Increment })
    end

    function Nexus:CreateLabel(...)
        return self:CreateElement('CreateLabel', ...)
    end

    function Nexus:NewLine(...)
        return self:Send('NewLine')
    end

    function Nexus:GetText(Name)
        return self:WaitForMessage('ElementText:', 'GetText', { Name = Name })
    end

    function Nexus:SetRelaunch(Seconds)
        self:Send('SetRelaunch', { Seconds = Seconds })
    end

    function Nexus:WaitForMessage(Header, Message, Payload)
        if Message then
            task.defer(self.Send, self, Message, Payload)
        end

        local Message

        while true do
            Message = self.MessageReceived:Wait()

            if Message:sub(1, #Header) == Header then
                break
            end
        end

        return Message:sub(#Header + 1)
    end

    function Nexus:Connect(Host, Bypass)
        if not Bypass and self.IsConnected then return 'Ignoring connection request, Nexus is already connected' end

        while true do
            for Index, Connection in pairs(self.Connections) do
                Connection:Disconnect()
            end
    
            table.clear(self.Connections)

            if self.IsConnected then
                self.IsConnected = false
                self.Socket = nil
                self.Disconnected:Fire()
            end

            if self.Terminated then break end

            if not Host then
                Host = 'localhost:5242'
            end

            local Success, Socket = pcall(WSConnect, ('ws://%s/Nexus?name=%s&id=%s&jobId=%s'):format(Host, LocalPlayer.Name, LocalPlayer.UserId, game.JobId))

            if not Success then task.wait(12) continue end

            self.Socket = Socket
            self.IsConnected = true

            table.insert(self.Connections, Socket.OnMessage:Connect(function(Message)
                self.MessageReceived:Fire(Message)
            end))

            table.insert(self.Connections, Socket.OnClose:Connect(function()
                self.IsConnected = false
                self.Disconnected:Fire()
            end))

            self.Connected:Fire()

            while self.IsConnected do
                local Success, Error = pcall(self.Send, self, 'ping')

                if not Success or self.Terminated then
                    break
                end

                task.wait(5)
            end
        end
    end

    function Nexus:Stop()
        self.IsConnected = false
        self.Terminated = true
        self.Disconnected:Fire()

        if self.Socket then
            pcall(function() self.Socket:Close() end)
        end
    end

    function Nexus:AddCommand(Name, Function)
        self.Commands[Name] = Function
    end

    function Nexus:RemoveCommand(Name)
        self.Commands[Name] = nil
    end

    function Nexus:OnButtonClick(Name, Function)
        self:AddCommand('ButtonClicked:' .. Name, Function)
    end

    Nexus.MessageReceived:Connect(function(Message)
        local S = Message:find(' ')

        if S then
            local Command, Message = Message:sub(1, S - 1):lower(), Message:sub(S + 1)

            if Nexus.Commands[Command] then
                local Success, Error = pcall(Nexus.Commands[Command], Message)

                if not Success and Error then
                    Nexus:Log(('Error with command `%s`: %s'):format(Command, Error))
                end
            end
        elseif Nexus.Commands[Message] then
            local Success, Error = pcall(Nexus.Commands[Message], Message)

            if not Success and Error then
                Nexus:Log(('Error with command `%s`: %s'):format(Message, Error))
            end
        end
    end)
end

do -- Default Commands
    Nexus:AddCommand('execute', function(Message)
        local Function, Error = loadstring(Message)
        
        if Function then
            local Env = getfenv(Function)
            
            Env.Player = LocalPlayer
            Env.print = function(...)
                local T = {}

                for Index, Value in pairs{ ... } do
                    table.insert(T, tostring(Value))
                end

                Nexus:Log(table.concat(T, ' '))
            end

            if newcclosure then Env.print = newcclosure(Env.print) end

            local S, E = pcall(Function)

            if not S then
                Nexus:Log(E)
            end
        else
            Nexus:Log(Error)
        end
    end)

    Nexus:AddCommand('teleport', function(Message)
        local S = Message:find(' ')
        local PlaceId, JobId = S and Message:sub(1, S - 1) or Message, S and Message:sub(S + 1)
        
        if JobId then
            TeleportService:TeleportToPlaceInstance(tonumber(PlaceId), JobId)
        else
            TeleportService:Teleport(tonumber(PlaceId))
        end
    end)

    Nexus:AddCommand('rejoin', function(Message)
        TeleportService:TeleportToPlaceInstance(game.PlaceId, game.JobId)
    end)

    Nexus:AddCommand('mute', function()
        if (UGS.MasterVolume - OldVolume) > 0.01 then
            OldVolume = UGS.MasterVolume
        end

        UGS.MasterVolume = 0
    end)

    Nexus:AddCommand('unmute', function()
        UGS.MasterVolume = OldVolume
    end)

    Nexus:AddCommand('performance', function(Message)
        if _PERF then return end
        
        _PERF = true
        _TARGETFPS = 8

        if Message and tonumber(Message) then
            _TARGETFPS = tonumber(Message)
        end

        local OldLevel = settings().Rendering.QualityLevel

        RunService:Set3dRenderingEnabled(false)
        settings().Rendering.QualityLevel = 1

        InputService.WindowFocused:Connect(function()
            RunService:Set3dRenderingEnabled(true)
            settings().Rendering.QualityLevel = OldLevel
            setfpscap(60)
        end)

        InputService.WindowFocusReleased:Connect(function()
            OldLevel = settings().Rendering.QualityLevel

            RunService:Set3dRenderingEnabled(false)
            settings().Rendering.QualityLevel = 1
            setfpscap(_TARGETFPS)
        end)

        setfpscap(_TARGETFPS)
    end)
end

do -- Connections
    GuiService.ErrorMessageChanged:Connect(function()
        if NoShutdown then return end

        local Code = GuiService:GetErrorCode().Value

        if Code >= Enum.ConnectionError.DisconnectErrors.Value then
            if not Nexus.ShutdownOnTeleportError and Code > Enum.ConnectionError.PlacelaunchOtherError.Value then
                return
            end
            
            -- Send error signal instead of shutdown
            Nexus:Send('error_detected', { code = tostring(Code) })
        end
    end)
end

local GEnv = getgenv()
GEnv.Nexus = Nexus
GEnv.performance = Nexus.Commands.performance -- fix the sirmeme error so that people stop being annoying saying "omg performance() doesnt work"

if not Nexus_Version then
    Nexus:Connect()
end
"""
        pyperclip.copy(nexus_code)
        messagebox.showinfo("Copied", "Nexus.lua copied to clipboard. Paste and execute in your executor for each Roblox instance.")

    def populate_accounts(self):
        current_usernames = set(self.manager.accounts.keys())
        sorted_usernames = sorted(current_usernames)  # Cache sorted list

        # Remove widgets for deleted accounts
        for username in list(self.account_widgets.keys()):
            if username not in current_usernames:
                self.account_widgets[username]['card'].destroy()
                del self.account_widgets[username]

        self.check_vars = {u: v for u, v in self.check_vars.items() if u in current_usernames}
        self.selected_accounts = {u for u in self.selected_accounts if u in current_usernames}

        for i, username in enumerate(sorted_usernames):
            data = self.manager.accounts[username]
            with state_lock:
                online = username in launched_accounts and username in tracked_accounts and username in tracked_hwnds and psutil.pid_exists(tracked_accounts[username]) and win32gui.IsWindow(tracked_hwnds[username])
                err = username in last_errors
                connected = username in self.connected_clients

            if username not in self.account_widgets:
                card = ctk.CTkFrame(self.accounts_frame, height=62, corner_radius=12,
                                    fg_color="#141417" if i % 2 == 0 else "#18181c")
                card.pack(fill="x", pady=4, padx=6)
                card.grid_columnconfigure(2, weight=1)

                var = ctk.BooleanVar()
                self.check_vars[username] = var
                checkbox = ctk.CTkCheckBox(card, text="", variable=var, width=20, fg_color="#9d4edd")
                checkbox.grid(row=0, column=0, padx=15, pady=15)

                status_dot = ctk.CTkLabel(card, text="●", font=ctk.CTkFont(size=20))
                status_dot.grid(row=0, column=1, padx=(0, 10))

                name_label = ctk.CTkLabel(card, text=username, font=ctk.CTkFont(size=17, weight="bold"), anchor="w")
                name_label.grid(row=0, column=2, sticky="w", padx=8)

                status_label = ctk.CTkLabel(card, font=ctk.CTkFont(size=13))
                status_label.grid(row=0, column=3, padx=20)

                game_label = ctk.CTkLabel(card, text_color="#aaa", font=ctk.CTkFont(size=13))
                game_label.grid(row=0, column=3, padx=(0, 20), sticky="e")

                restart_btn = ctk.CTkButton(card, text="Restart", width=88, fg_color="#222226", hover_color="#333338")
                restart_btn.grid(row=0, column=4, padx=5)

                kill_btn = ctk.CTkButton(card, text="Kill", width=66, fg_color="#3d1122", hover_color="#6b1a44")
                kill_btn.grid(row=0, column=5, padx=5)

                script_btn = ctk.CTkButton(card, text="Script", width=88, fg_color="#1a6333", hover_color="#2d8a4d")
                script_btn.grid(row=0, column=6, padx=5)

                self.account_widgets[username] = {
                    'card': card,
                    'checkbox': checkbox,
                    'status_dot': status_dot,
                    'name_label': name_label,
                    'status_label': status_label,
                    'game_label': game_label,
                    'restart_btn': restart_btn,
                    'kill_btn': kill_btn,
                    'script_btn': script_btn
                }

                var.trace_add("write", self.update_selection)

            widgets = self.account_widgets[username]
            color = "#00ff88" if online else ("#ff3b5c" if err else "#666")
            widgets['status_dot'].configure(text_color=color)
            status_text = "ONLINE" if online else ("ERROR" if err else "OFFLINE")
            if connected:
                status_text += " (Connected)"
            widgets['status_label'].configure(text=status_text, text_color=color)

            if online:
                game_name = data.get('current_game_name', '')
                widgets['game_label'].configure(text=game_name)
                widgets['restart_btn'].configure(command=lambda n=username: self.run_async(lambda: self.restart_account(n)))
                widgets['kill_btn'].configure(command=lambda n=username: (self.handle_manual_remove(n), self.after(100, self.refresh_full_status)))
                widgets['restart_btn'].grid()
                widgets['kill_btn'].grid()
            else:
                widgets['game_label'].configure(text='')
                widgets['restart_btn'].grid_remove()
                widgets['kill_btn'].grid_remove()

            if connected:
                widgets['script_btn'].configure(command=lambda n=username: self.open_script_editor(n))
                widgets['script_btn'].grid()
            else:
                widgets['script_btn'].grid_remove()

            ToolTip(widgets['script_btn'], "Open script editor for this account")

        self.update_selection()

    def open_script_editor(self, username):
        if username not in self.connected_clients:
            messagebox.showerror("Error", "Account not connected via Nexus. Inject Nexus.lua first.")
            return

        editor_win = ctk.CTkToplevel(self)
        editor_win.title(f"Script Editor - {username}")
        editor_win.geometry("700x500")
        editor_win.minsize(600, 400)
        editor_win.configure(fg_color="#0a0a0a")

        force_icon(editor_win)

        # Main frame with padding
        main_frame = ctk.CTkFrame(editor_win, fg_color="#111114", corner_radius=14)
        main_frame.pack(padx=20, pady=20, fill="both", expand=True)

        # Title label
        title_label = ctk.CTkLabel(main_frame, text="Lua Script Editor", font=ctk.CTkFont(size=18, weight="bold"))
        title_label.pack(pady=(10, 5))

        # Instruction label
        instr_label = ctk.CTkLabel(main_frame, text="Enter Lua code to execute on this Roblox instance", 
                                   font=ctk.CTkFont(size=13), text_color="#aaaaaa")
        instr_label.pack(pady=(0, 10))

        # Editor frame
        editor_frame = ctk.CTkFrame(main_frame, fg_color="#1a1a1a", corner_radius=10)
        editor_frame.pack(fill="both", expand=True, pady=5)

        # Line numbers
        line_frame = ctk.CTkFrame(editor_frame, width=50, fg_color="#2b2b2b", corner_radius=0)
        line_frame.pack(side="left", fill="y")
        line_numbers = tk.Text(line_frame, width=4, padx=8, pady=8, takefocus=0, bd=0, bg="#2b2b2b", fg="#ffffff", 
                               state='disabled', wrap='none', font=("Consolas", 13))
        line_numbers.pack(fill="y")

        # Text box
        text_box = ctk.CTkTextbox(editor_frame, font=ctk.CTkFont(family="Consolas", size=13), wrap='none', 
                                  fg_color="#1a1a1a", text_color="#ffffff", corner_radius=0, border_width=0)
        text_box.pack(side="left", fill="both", expand=True, padx=(0, 0), pady=0)

        # Syntax highlighting setup
        repl_dict = {
            "keyword": (re.compile(r'\b(and|break|do|else|elseif|end|false|for|function|goto|if|in|local|nil|not|or|repeat|return|then|true|until|while)\b'), "#F92672"),
            "string": (re.compile(r'".*?"|\'.*?\'|\[=.*?\=]'), "#E6DB74"),
            "comment": (re.compile(r'--.*'), "#75715E"),
            "number": (re.compile(r'\b\d+\.?\d*\b'), "#AE81FF"),
        }

        for tag, (pattern, color) in repl_dict.items():
            text_box._textbox.tag_config(tag, foreground=color)

        def highlight_syntax(event=None):
            text = text_box.get("1.0", "end")
            for tag in repl_dict:
                text_box._textbox.tag_remove(tag, "1.0", "end")
                for match in repl_dict[tag][0].finditer(text):
                    start = f"1.0 + {match.start()} chars"
                    end = f"1.0 + {match.end()} chars"
                    text_box._textbox.tag_add(tag, start, end)

        def update_line_numbers(event=None):
            line_numbers.config(state='normal')
            line_numbers.delete('1.0', 'end')
            lines = int(text_box.index('end-1c').split('.')[0])
            for i in range(1, lines + 1):
                line_numbers.insert('end', f"{i}\n")
            line_numbers.config(state='disabled')

        def sync_scroll(first, last):
            line_numbers.yview("moveto", first)

        text_box.configure(yscrollcommand=sync_scroll)
        # Note: Removed line_numbers.yscrollcommand to prevent loop and invalid calls

        text_box.bind("<KeyRelease>", lambda e: (highlight_syntax(), update_line_numbers()))

        # Initial update
        highlight_syntax()
        update_line_numbers()

        # Buttons frame
        btn_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        btn_frame.pack(pady=10, fill="x", padx=20)

        execute_btn = ctk.CTkButton(btn_frame, text="Execute", command=lambda: self.execute_script(username, text_box.get("1.0", "end")),
                                    fg_color="#1a6333", hover_color="#2d8a4d", width=140, height=40,
                                    font=ctk.CTkFont(size=14, weight="bold"))
        execute_btn.pack(side="right", padx=(10, 0))

        def save_to_file():
            code = text_box.get("1.0", "end").strip()
            if not code:
                messagebox.showwarning("Empty", "No code to save.")
                return
            file_path = f"scripts/{username}.lua"
            os.makedirs("scripts", exist_ok=True)
            with open(file_path, "w") as f:
                f.write(code)
            messagebox.showinfo("Saved", f"Script saved to {file_path}")

        save_btn = ctk.CTkButton(btn_frame, text="Save to File", command=save_to_file,
                                 fg_color="#2b2b2b", hover_color="#3d3d3d", width=140, height=40,
                                 font=ctk.CTkFont(size=14, weight="bold"))
        save_btn.pack(side="right", padx=(10, 0))

        close_btn = ctk.CTkButton(btn_frame, text="Close", command=editor_win.destroy,
                                  fg_color="#3d1122", hover_color="#6b1a44", width=140, height=40,
                                  font=ctk.CTkFont(size=14, weight="bold"))
        close_btn.pack(side="right")

    def execute_script(self, username, code):
        ws = self.connected_clients.get(username)
        if ws:
            code = code.strip()
            if code:
                asyncio.run_coroutine_threadsafe(ws.send(f"execute {code}"), asyncio.get_event_loop())
                logging.info(f"Executed script on {username}")
                messagebox.showinfo("Success", "Script sent to execute on this account only.")
            else:
                messagebox.showwarning("Empty", "No code to execute.")
        else:
            messagebox.showerror("Error", "Connection lost.")

    def update_selection(self, *args):
        self.selected_accounts = {u for u, v in self.check_vars.items() if v.get()}
        self.remove_btn.configure(state="normal" if self.selected_accounts else "disabled")

    def add_account_btn(self):
        threading.Thread(target=self._add_account_thread).start()

    def _add_account_thread(self):
        if self.manager.add_account(amount=1):  # Limited to 1 for simplicity, can change
            self.after(0, self.populate_accounts)
        else:
            self.after(0, lambda: messagebox.showerror("Error", "Failed to add account(s). Check console for details."))

    def remove_accounts(self):
        if not self.selected_accounts:
            return
        if messagebox.askyesno("Confirm", "Remove selected accounts?"):
            for u in list(self.selected_accounts):
                self.manager.delete_account(u)
            self.populate_accounts()

    def join_server(self):
        place_id, job_id = self.get_server_info()
        if place_id is None:
            return
        if not place_id:
            messagebox.showerror("Error", "Place ID required")
            return
        if not self.selected_accounts:
            messagebox.showerror("Error", "No accounts selected")
            return
        threading.Thread(target=self._join_server_thread, args=(place_id, job_id)).start()

    def _join_server_thread(self, place_id, job_id):
        for username in list(self.selected_accounts):
            self.manager.launch_roblox(username, place_id, job_id=job_id)
            time.sleep(1)  # Reduced delay

    def launch_all(self):
        place_id, job_id = self.get_server_info()
        if place_id is None:
            return
        if not place_id:
            messagebox.showerror("Error", "Place ID required")
            return
        threading.Thread(target=self._launch_all_thread, args=(place_id, job_id)).start()

    def _launch_all_thread(self, place_id, job_id):
        for username in list(self.manager.accounts.keys()):
            self.manager.launch_roblox(username, place_id, job_id=job_id)
            time.sleep(1)  # Reduced delay

    def kill_all(self):
        if not messagebox.askyesno(
            "Confirm Kill All",
            "Are you sure you want to terminate ALL Roblox instances? This will close every running Roblox window."
        ):
            return
        with state_lock:
            for pid in list(tracked_accounts.values()):
                try:
                    psutil.Process(pid).terminate()
                except:
                    pass
            tracked_accounts.clear()
            tracked_hwnds.clear()
            launched_accounts.clear()
        subprocess.call(["taskkill", "/F", "/IM", "RobloxPlayerBeta.exe"])
        messagebox.showinfo("Success", "All Roblox instances terminated")

    def open_browser(self):
        if not self.selected_accounts:
            messagebox.showerror("Error", "No accounts selected")
            return
        for username in self.selected_accounts:
            self.manager.launch_home(username)

    def toggle_ocr(self):
        enabled = self.ocr_var.get()
        self.ocr_label.configure(text=f" • OCR: {'ON' if enabled else 'OFF'}", text_color="#4caf50" if enabled else "#888")
        if enabled:
            threading.Thread(target=self.scan_loop, daemon=True).start()

    def scan_loop(self):
        while self.ocr_var.get():
            with state_lock:
                if not tracked_hwnds:
                    time.sleep(15)
                    continue
            self.check_account_statuses()
            self.check_accounts_for_errors()
            self.gui_queue.put(lambda: self.populate_accounts())
            time.sleep(15)  # Increased interval

    def toggle_report(self):
        enabled = self.report_var.get()
        self.report_label.configure(text=f" • Report: {'ON' if enabled else 'OFF'}", text_color="#4caf50" if enabled else "#888")

    def update_active(self):
        active = get_active_count()
        self.active_label.configure(text=f"{active} active")
        self.after(30000, self.update_active)  # Increased interval


    def get_server_info(self):
        place_id = self.place_entry.get().strip()
        job_input = self.job_entry.get().strip()

        if not job_input:
            return place_id, None

        # Normalize HTML escapes first
        job_norm = html.unescape(job_input).strip()  # ✅ fix &amp;

        import re
        valid_games_url = re.compile(r"^https://www\.roblox\.com/games/\d+/.+\?privateServerLinkCode=\d+$")

        if job_norm.isdigit():
            return place_id, job_norm
        elif valid_games_url.match(job_norm):
            return place_id, job_norm
        else:
            messagebox.showerror(
                "Invalid Job ID",
                "Job ID must be a numeric code or a Roblox private server link in the format:\nhttps://www.roblox.com/games/<placeId>/<name>?privateServerLinkCode=<code>"
            )
            return None, None


    def check_account_statuses(self):
        to_remove = []
        with state_lock:
            for username, pid in list(tracked_accounts.items()):
                try:
                    p = psutil.Process(pid)
                    if p.name() != ROBLOX_EXE or not p.is_running():
                        to_remove.append(username)
                except psutil.NoSuchProcess:
                    to_remove.append(username)
            for u in to_remove:
                tracked_accounts.pop(u, None)
                tracked_hwnds.pop(u, None)
                launched_accounts.discard(u)
                ts = datetime.now().strftime("%H:%M:%S")
                last_errors[u] = ("Process died", ts)
                error_counter[u] = error_counter.get(u, 0) + 1
                logging.info(f"PID missing for {u} → setting error")
                if self.ocr_var.get() and error_counter.get(u, 0) < 4:
                    delay = 5 * (2 ** error_counter.get(u, 0))  # Exponential backoff
                    threading.Thread(target=self._relaunch_account, args=(u, delay)).start()
                if self.report_var.get() and self.discord_bot:
                    asyncio.run_coroutine_threadsafe(self.discord_bot.send_report(), self.discord_bot.bot.loop)

    def _relaunch_account(self, u, delay):
        time.sleep(delay)
        with state_lock:
            server = self.manager.accounts.get(u, {}).get('current_server', {})
            place_id = server.get('place_id')
            job_id = server.get('job_id')
        if place_id:
            self.manager.launch_roblox(u, place_id, job_id=job_id)

    def check_accounts_for_errors(self):
        with state_lock:
            for name, hwnd in list(tracked_hwnds.items()):
                if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
                    continue
                try:
                    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
                    win32gui.SetForegroundWindow(hwnd)
                    win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
                    time.sleep(0.5)
                    img = self.capture_window(hwnd)
                    text = pytesseract.image_to_string(img, config='--psm 6 --oem 3')
                    for kw in STATE_KEYWORDS:
                        if kw.lower() in text.lower():
                            ts = datetime.now().strftime("%H:%M:%S")
                            last_errors[name] = (kw, ts)
                            error_counter[name] = error_counter.get(name, 0) + 1
                            pid = tracked_accounts.pop(name, None)
                            tracked_hwnds.pop(name, None)
                            if pid:
                                try:
                                    psutil.Process(pid).terminate()
                                except:
                                    pass
                            logging.info(f"OCR detected '{kw}' → relaunching {name}")
                            if self.ocr_var.get() and error_counter.get(name, 0) < 4:
                                delay = 5 * (2 ** error_counter.get(name, 0))
                                threading.Thread(target=self._relaunch_account, args=(name, delay)).start()
                            if self.report_var.get() and self.discord_bot:
                                asyncio.run_coroutine_threadsafe(self.discord_bot.send_report(), self.discord_bot.bot.loop)
                            break
                except Exception as e:
                    logging.error(f"OCR failed on {name}: {e}")

            for name in list(last_errors.keys()):
                if name in tracked_accounts:
                    del last_errors[name]

    def capture_window(self, hwnd):
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        width = right - left
        height = bottom - top
        rect = win32gui.GetWindowRect(hwnd)
        left, top = rect[0] + 8, rect[1] + 31  # Adjust for borders
        with mss() as sct:
            monitor = {"top": top, "left": left, "width": width, "height": height}
            sct_img = sct.grab(monitor)
            img = Image.frombytes("RGB", sct_img.size, sct_img.rgb)
        return img

    def run_async(self, func):
        threading.Thread(target=func, daemon=True).start()

    def restart_account(self, name):
        with state_lock:
            self.handle_manual_remove(name)
        server = self.manager.accounts.get(name, {}).get('current_server', {})
        place_id = server.get('place_id')
        job_id = server.get('job_id')
        if place_id:
            self.manager.launch_roblox(name, place_id, job_id=job_id)

    def handle_manual_remove(self, name):
        with state_lock:
            if name in tracked_accounts:
                pid = tracked_accounts[name]
                try:
                    psutil.Process(pid).terminate()
                except:
                    pass
                tracked_accounts.pop(name, None)
            tracked_hwnds.pop(name, None)
            launched_accounts.discard(name)

    def refresh_full_status(self):
        self.check_account_statuses()
        self.populate_accounts()

    def open_settings(self):
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.lift()
            self.settings_window.focus_force()
            return

        settings_win = ctk.CTkToplevel(self)
        self.settings_window = settings_win

        settings_win.title("Settings")
        settings_win.geometry("480x460")
        settings_win.resizable(False, False)
        settings_win.configure(fg_color="#0a0a0a")
        settings_win.attributes("-alpha", 0.98)
        settings_win.attributes("-topmost", False)
        settings_win.transient(self)

        force_icon(settings_win)

        settings_win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() // 2) - (settings_win.winfo_width() // 2)
        y = self.winfo_rooty() + (self.winfo_height() // 2) - (settings_win.winfo_height() // 2)
        settings_win.geometry(f"+{x}+{y}")

        def on_close():
            self.settings_window = None
            settings_win.destroy()

        settings_win.protocol("WM_DELETE_WINDOW", on_close)
        settings_win.bind("<Escape>", lambda e: on_close())

        ctk.CTkLabel(settings_win, text="Settings", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=(28, 20))

        # === Discord Bot Token ===
        token_frame = ctk.CTkFrame(settings_win, fg_color="#111114", corner_radius=14)
        token_frame.pack(padx=40, pady=(0, 12), fill="x")

        ctk.CTkLabel(token_frame, text="Discord Bot Token", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=24, pady=(16, 8))
        self.token_entry = ctk.CTkEntry(token_frame, placeholder_text="Paste token here...", show="•", height=40, corner_radius=10)
        self.token_entry.insert(0, BOT_TOKEN or "")
        self.token_entry.pack(padx=24, pady=(0, 16), fill="x")

        # === Report Channel ID ===
        channel_frame = ctk.CTkFrame(settings_win, fg_color="#111114", corner_radius=14)
        channel_frame.pack(padx=40, pady=(0, 20), fill="x")

        ctk.CTkLabel(channel_frame, text="Report Channel ID", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=24, pady=(16, 8))
        self.channel_entry = ctk.CTkEntry(channel_frame, placeholder_text="Right-click channel → Copy Channel ID", height=40, corner_radius=10)
        self.channel_entry.insert(0, str(REPORT_CHANNEL_ID) if REPORT_CHANNEL_ID else "")
        self.channel_entry.pack(padx=24, pady=(0, 16), fill="x")

        # Status label
        self.settings_status = ctk.CTkLabel(settings_win, text="", font=ctk.CTkFont(size=13), height=32)
        self.settings_status.pack(pady=10)

        # Buttons frame
        btn_frame = ctk.CTkFrame(settings_win, fg_color="transparent")
        btn_frame.pack(pady=16)

        ctk.CTkButton(
            btn_frame, text="Save Settings", width=150, height=44,
            font=ctk.CTkFont(size=14, weight="bold"), fg_color="#2b2b2b", hover_color="#3d3d3d",
            command=self.save_settings
        ).grid(row=0, column=0, padx=14)

        ctk.CTkButton(
            btn_frame, text="Connect Bot", width=150, height=44,
            font=ctk.CTkFont(size=14, weight="bold"), fg_color="#1a6333", hover_color="#2d8a4d",
            text_color="#ffffff", command=self.connect_bot
        ).grid(row=0, column=1, padx=14)

        ctk.CTkButton(
            settings_win, text="Close", width=320, height=44,
            font=ctk.CTkFont(size=14, weight="bold"), fg_color="#333338", hover_color="#44444a",
            command=on_close
        ).pack(pady=(10, 28))

    def save_settings(self):
        global BOT_TOKEN, REPORT_CHANNEL_ID, ERROR_SCAN_ENABLED
        BOT_TOKEN = self.token_entry.get().strip() or None
        try:
            REPORT_CHANNEL_ID = int(self.channel_entry.get().strip()) if self.channel_entry.get().strip() else None
        except ValueError:
            self.settings_status.configure(text="Invalid Channel ID (must be a number)!", text_color="#ff4444")
            return

        ERROR_SCAN_ENABLED = self.ocr_var.get()  # Save OCR toggle state too

        config["bot_token"] = BOT_TOKEN
        config["channel_id"] = REPORT_CHANNEL_ID
        config["error_scan_enabled"] = ERROR_SCAN_ENABLED

        try:
            with open(CONFIG_FILE, "w", encoding='utf-8') as f:
                json.dump(config, f, indent=4)
            self.settings_status.configure(text="Settings saved successfully!", text_color="#4caf50")
        except Exception as e:
            self.settings_status.configure(text=f"Save failed: {e}", text_color="#ff4444")

        # Update OCR switch in case it was changed in settings
        if self.ocr_var.get() != ERROR_SCAN_ENABLED:
            self.ocr_var.set(ERROR_SCAN_ENABLED)
            self.toggle_ocr()

    def connect_bot(self):
        if not DISCORD_AVAILABLE:
            messagebox.showerror("Error", "discord.py not installed")
            return
        if not BOT_TOKEN:
            messagebox.showerror("Error", "Bot token required")
            return
        global _bot_running
        if _bot_running:
            return
        _bot_running = True
        self.discord_bot = DiscordBot(self)
        threading.Thread(target=self.discord_bot.bot.run, args=(BOT_TOKEN,), daemon=True).start()

    def show_help(self):
        help_text = """
Robloxium – Complete Guide (2025)

Managing Accounts
Adding Accounts
• Click "Add Account" at the bottom.
• Browser windows open (up to 3) to Roblox login page.
• Log in to each one.

Removing Accounts
• Check the boxes next to accounts in the list.
• Click "Remove" at the bottom.

Launching and Joining Games
• Enter the Place ID.
• Optional: Add a Roblox share link for the Job ID.
• Check boxes for any old Roblox windows for that accou accounts, then click "Join Server" to start selected ones.
• Or click "Launch All" to start every account.

Stopping Accounts
• You must "Kill" the account if you want to close the program or it will be flagged as a Crash.
• Click "Kill All" to close every Roblox window.
• Or "Restart" to stop and start it again in the same game.

Monitoring and Auto-Relaunch
• Turn on "Error Scan", turned off on default.
• Every 15 seconds, it checks Roblox windows for errors.
• If it finds an error, it closes the window and tries to restart the account (up to 3 times)

Scripting with Nexus
• Click "Copy Nexus Lua".
• In a running Roblox game, paste and run it with an executor tool (like Wave, Seliware, Pottasium).
• This connects the game to the program (shows "Connected").
• Then, click "Script" on the card to open an editor:
• Click "Execute" to run it just in that game.
• "Save to File" saves the code to a file to that specific account (Auto Execute)

Discord Features
• In Settings (gear icon), add your Discord bot token and channel ID.
• Click "Connect Bot" (shows "Discord: Active" at top if successful)

Discord Commands:
Type these in a channel the bot can see (starts with !):
• !ping: Checks if bot is working.
• !status: Shows active count and account list.
• !launch <username>: Starts one account.
• !restart <username>: Stops and restarts one.
• !launchall: Starts all.
• !killall: Stops all.
• !toggleocr: Turns error scan on/off.
        """.strip()
        if self.help_window and self.help_window.winfo_exists():
            self.help_window.lift()
            self.help_window.focus_force()
            return

        dialog = ctk.CTkToplevel(self)
        self.help_window = dialog
        dialog.title("How to Use – Robloxium")
        dialog.geometry("760x680")
        dialog.resizable(False, False)
        dialog.configure(fg_color="#0f0f0f")
        dialog.attributes("-topmost", False)
        dialog.transient(self)

        force_icon(dialog)

        dialog.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() // 2) - (760 // 2)
        y = self.winfo_rooty() + (self.winfo_height() // 2) - (680 // 2)
        dialog.geometry(f"+{x}+{y}")

        def on_close():
            self.help_window = None
            dialog.destroy()

        dialog.protocol("WM_DELETE_WINDOW", on_close)
        dialog.bind("<Escape>", lambda e: on_close())

        ctk.CTkLabel(
            dialog,
            text="How to Use – Robloxium",
            font=ctk.CTkFont(size=19, weight="bold"),
            text_color="#e0e0e0"
        ).pack(pady=(24, 12))

        text_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        text_frame.pack(padx=30, pady=(0, 20), fill="both", expand=True)

        text_box = ctk.CTkTextbox(
            text_frame,
            font=ctk.CTkFont(family="Consolas", size=13),
            wrap="word",
            fg_color="#1a1a1a",
            text_color="#e0e0e0",
            corner_radius=12
        )
        text_box.pack(fill="both", expand=True)

        text_box.insert("0.0", help_text)
        text_box.configure(state="disabled")

"""
Roblox API interaction utilities
Handles authentication, info, and game launching
"""

import os
import time
import random
import requests
import urllib.parse



class RobloxAPI:
    @staticmethod
    def get_game_name(place_id):
        """Fetch game name from Roblox API"""
        if not place_id or not place_id.isdigit():
            return None
        try:
            place_url = f"https://apis.roblox.com/universes/v1/places/{place_id}/universe"
            place_response = requests.get(place_url, timeout=5)
            if place_response.status_code == 200:
                place_data = place_response.json()
                universe_id = place_data.get("universeId")
                if universe_id:
                    game_url = f"https://games.roblox.com/v1/games?universeIds={universe_id}"
                    game_response = requests.get(game_url, timeout=5)
                    if game_response.status_code == 200:
                        game_data = game_response.json()
                        if game_data and game_data.get("data") and len(game_data["data"]) > 0:
                            return game_data["data"][0].get("name", None)
        except:
            pass
        return None
    
    @staticmethod
    def get_username_from_api(roblosecurity_cookie):
        """Get username using Roblox API"""
        try:
            headers = {
                'Cookie': f'.ROBLOSECURITY={roblosecurity_cookie}'
            }
            
            response = requests.get(
                'https://users.roblox.com/v1/users/authenticated',
                headers=headers,
                timeout=3
            )
            
            if response.status_code == 200:
                user_data = response.json()
                return user_data.get('name', 'Unknown')
            
        except Exception as e:
            print(f"Error getting username from API: {e}")
        
        return "Unknown"
    
    @staticmethod
    def get_auth_ticket(roblosecurity_cookie):
        """Get authentication ticket for launching Roblox games"""
        url = "https://auth.roblox.com/v1/authentication-ticket/"
        headers = {
            "User-Agent": "Roblox/WinInet",
            "Referer": "https://www.roblox.com/develop",
            "RBX-For-Gameauth": "true",
            "Content-Type": "application/json",
            "Cookie": f".ROBLOSECURITY={roblosecurity_cookie}"
        }

        try:
            response = requests.post(url, headers=headers, timeout=5)
            if response.status_code == 403 and "x-csrf-token" in response.headers:
                csrf_token = response.headers["x-csrf-token"]
            else:
                print(f"Failed to get CSRF token, status: {response.status_code}")
                return None

            headers["X-CSRF-TOKEN"] = csrf_token
            response2 = requests.post(url, headers=headers, timeout=5)
            if response2.status_code == 200:
                auth_ticket = response2.headers.get("rbx-authentication-ticket")
                if auth_ticket:
                    return auth_ticket
                else:
                    print("Authentication ticket header missing in response.")
                    return None
            else:
                print(f"Failed to get auth ticket, status: {response2.status_code}")
                return None

        except requests.exceptions.RequestException as e:
            print(f"Request failed: {e}")
            return None


    @staticmethod
    def launch_roblox(username, cookie, game_id, job_id=""):
        """Launch Roblox game with specified account"""
        print(f"Getting authentication ticket for {username}...")
        auth_ticket = RobloxAPI.get_auth_ticket(cookie)

        if not auth_ticket:
            print("[ERROR] Failed to get authentication ticket")
            return False

        print("[SUCCESS] Got authentication ticket!")
        browser_tracker_id = random.randint(55393295400, 55393295500)
        launch_time = int(time.time() * 1000)

        # Always URL-encode the auth ticket for the gameinfo segment
        encoded_auth_ticket = urllib.parse.quote(auth_ticket, safe='')

        # If no game_id provided, launch home
        if not game_id:
            url = (
                "roblox-player:1"
                + "+launchmode:play"
                + "+gameinfo:" + encoded_auth_ticket
                + "+launchtime:" + str(launch_time)
                + "+browsertrackerid:" + str(browser_tracker_id)
                + "+robloxLocale:en_us"
                + "+gameLocale:en_us"
            )
            print("Launching Roblox Home...")
            print(f"Account: {username}")
            try:
                os.system(f'start "" "{url}"')
                print("[SUCCESS] Roblox home launched successfully!")
                return True
            except Exception as e:
                print(f"[ERROR] Failed to launch Roblox: {e}")
                return False


        # Extract and classify private server/share code
        link_code = ""
        access_code = ""
        share_code = ""
        share_type = ""
        if job_id:
            ji = job_id.strip()
            if ji.startswith("http://") or ji.startswith("https://"):
                ji = html.unescape(ji)
                try:
                    parsed = urllib.parse.urlparse(ji)
                    qs = urllib.parse.parse_qs(parsed.query)
                    if "code" in qs and qs["code"]:
                        code_val = qs["code"][0]
                        type_val = qs.get("type", [None])[0]
                        # If type=Server, treat as share_code
                        if type_val == "Server":
                            share_code = code_val
                            share_type = type_val
                        elif code_val.isdigit():
                            link_code = code_val
                        else:
                            access_code = code_val
                    else:
                        print("[ERROR] Could not extract 'code' from share URL.")
                        return False
                except Exception as e:
                    print(f"[ERROR] Failed to parse share URL: {e}")
                    return False
            else:
                # Numeric = linkCode, alphanumeric = accessCode
                if ji.isdigit():
                    link_code = ji
                else:
                    access_code = ji

        # Always URL-encode the auth ticket
        encoded_auth_ticket = urllib.parse.quote(auth_ticket, safe='')

        # Build PlaceLauncher URL
        place_launcher_url = (
            "https://assetgame.roblox.com/game/PlaceLauncher.ashx?"
            f"request={'RequestPrivateGame' if (link_code or access_code or share_code) else 'RequestGame'}&"
            f"browserTrackerId={browser_tracker_id}&"
            f"placeId={game_id}&"
            f"isPlayTogetherGame=false"
        )

        if link_code:
            place_launcher_url += f"&linkCode={link_code}"
        
        url = (
            f"roblox-player:1"
            f"+launchmode:play"
            f"+gameinfo:{encoded_auth_ticket}"
            f"+launchtime:{launch_time}"
            f"+placelauncherurl:{urllib.parse.quote(place_launcher_url)}"
            f"+browsertrackerid:{browser_tracker_id}"
            f"+robloxLocale:en_us"
            f"+gameLocale:en_us"
        )

        print("Launching Roblox...")
        print(f"Account: {username}")
        print(f"Game ID (placeId): {game_id}")
        print(repr(place_launcher_url))
        print(repr(url))
        if access_code:
            print(f"Access code: {access_code}")
        if link_code:
            print(f"Link code: {link_code}")

        try:
            launcher_path = detect_custom_launcher()
            if launcher_path:
                # Use the detected launcher to open the roblox-player URL
                subprocess.Popen([launcher_path, url])
                print(f"[SUCCESS] Launched Roblox using custom launcher: {launcher_path}")
            else:
                os.system(f'start "" "{url}"')
                print("[SUCCESS] Roblox launched successfully!")
            return True
        except Exception as e:
            print(f"[ERROR] Failed to launch Roblox: {e}")
            return False
    
    @staticmethod
    def validate_account(username, cookie):
        """Validate if an account's cookie is still valid and show detailed token info"""
        try:
            headers = {
                'Cookie': f'.ROBLOSECURITY={cookie}'
            }
            
            response = requests.get(
                'https://users.roblox.com/v1/users/authenticated',
                headers=headers,
                timeout=3
            )
            
            is_valid = response.status_code == 200
            
            print(f"\n{'='*60}")
            print(f"ACCOUNT VALIDATION: {username}")
            print(f"{'='*60}")
            print(f"Valid: {'Yes' if is_valid else 'No'}")
            
            if cookie:
                if len(cookie) > 60:
                    token_preview = f"{cookie[:50]}...{cookie[-10:]}"
                else:
                    token_preview = cookie
                print(f"Token: {token_preview}")
                print(f"Token Length: {len(cookie)} characters")
            else:
                print("Token: (No token found)")
            
            if is_valid and response.status_code == 200:
                try:
                    user_data = response.json()
                    print(f"User ID: {user_data.get('id', 'Unknown')}")
                    print(f"Display Name: {user_data.get('displayName', 'Unknown')}")
                    print(f"Username: {user_data.get('name', 'Unknown')}")
                except:
                    print("Additional info: Could not retrieve user details")
            else:
                print(f"Status Code: {response.status_code}")
                if response.status_code == 401:
                    print("Reason: Token expired or invalid")
                elif response.status_code == 403:
                    print("Reason: Access forbidden")
                else:
                    print("Reason: Unknown error")
            
            print(f"{'='*60}")
            return is_valid
            
        except Exception as e:
            print(f"\n{'='*60}")
            print(f"ACCOUNT VALIDATION: {username}")
            print(f"{'='*60}")
            print(f"Valid: No")
            if cookie:
                if len(cookie) > 60:
                    token_preview = f"{cookie[:50]}...{cookie[-10:]}"
                else:
                    token_preview = cookie
                print(f"Token: {token_preview}")
            print(f"Error: {str(e)}")
            print(f"{'='*60}")
            return False

"""
Account Manager class
Handles account storage, browser automation, and account management
"""

import os
import sys
import json
import time
import tempfile
import hashlib
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.common.exceptions import WebDriverException


class RobloxAccountManager:
    
    def __init__(self, password=None):
        self.data_folder = "AccountManagerData"
        if not os.path.exists(self.data_folder):
            os.makedirs(self.data_folder)
        
        self.accounts_file = os.path.join(self.data_folder, "saved_accounts.json")
        self.key_file = os.path.join(self.data_folder, "encryption_key.key")
        
        if password:
            digest = hashlib.sha256(password.encode()).digest()
            encoded_key = base64.urlsafe_b64encode(digest)
            self.key = Fernet(encoded_key)
        else:
            if os.path.exists(self.key_file):
                with open(self.key_file, 'rb') as f:
                    self.key = Fernet(f.read())
            else:
                key = Fernet.generate_key()
                with open(self.key_file, 'wb') as f:
                    f.write(key)
                self.key = Fernet(key)
        
        self.accounts = self.load_accounts()
        self.temp_profile_dir = None
        
    def load_accounts(self):
        """Load saved accounts from JSON file"""
        if os.path.exists(self.accounts_file):
            try:
                with open(self.accounts_file, 'r', encoding='utf-8') as f:
                    encrypted_data = json.load(f)
                accounts = {}
                for username, data in encrypted_data.items():
                    data['cookie'] = self._decrypt(data['cookie'])
                    accounts[username] = data
                return accounts
            except Exception as e:
                logging.error(f"[WARNING] Error loading accounts: {e}")
                return {}
        return {}
    
    def save_accounts(self):
        """Save accounts to JSON file"""
        encrypted_data = {}
        for username, data in self.accounts.items():
            enc_data = data.copy()
            enc_data['cookie'] = self._encrypt(data['cookie'])
            encrypted_data[username] = enc_data
        with open(self.accounts_file, 'w', encoding='utf-8') as f:
            json.dump(encrypted_data, f, indent=2, ensure_ascii=False)
    
    def _encrypt(self, text):
        return self.key.encrypt(text.encode()).decode()
    
    def _decrypt(self, enc):
        return self.key.decrypt(enc.encode()).decode()
    
    def create_temp_profile(self):
        """Create a temporary Chrome profile directory"""
        self.temp_profile_dir = tempfile.mkdtemp(prefix="roblox_login_")
        return self.temp_profile_dir
    
    def cleanup_temp_profile(self):
        """Clean up temporary profile directory"""
        if self.temp_profile_dir and os.path.exists(self.temp_profile_dir):
            try:
                import shutil
                shutil.rmtree(self.temp_profile_dir)
            except:
                pass
    
    def setup_chrome_driver(self):
        """Setup Chrome driver with maximum speed optimizations"""
        profile_dir = self.create_temp_profile()
    
        chrome_options = Options()
        chrome_options.add_argument(f"--user-data-dir={profile_dir}")
        chrome_options.add_argument("--no-first-run")
        chrome_options.add_argument("--no-default-browser-check")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
    
        chrome_options.add_argument("--log-level=3")
        chrome_options.add_argument("--silent")
        chrome_options.add_argument("--disable-logging")
        chrome_options.add_argument("--disable-gpu-logging")
        chrome_options.add_argument("--disable-dev-tools")
        chrome_options.add_argument("--no-default-browser-check")
        chrome_options.add_argument("--disable-default-apps")
        chrome_options.add_argument("--disable-web-security")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-logging"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
    
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--disable-plugins")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-features=TranslateUI,BlinkGenPropertyTrees")
        chrome_options.add_argument("--disable-background-timer-throttling")
        chrome_options.add_argument("--disable-renderer-backgrounding")
        chrome_options.add_argument("--disable-backgrounding-occluded-windows")
        chrome_options.add_argument("--disable-component-extensions-with-background-pages")
        chrome_options.add_argument("--disable-ipc-flooding-protection")
        chrome_options.add_argument("--disable-hang-monitor")
        chrome_options.add_argument("--disable-prompt-on-repost")
        chrome_options.add_argument("--disable-domain-reliability")
        chrome_options.add_argument("--disable-component-update")
        chrome_options.add_argument("--disable-background-networking")
        chrome_options.add_argument("--aggressive-cache-discard")
    
        try:
            service = Service()  # Use built-in Selenium Manager
            driver = webdriver.Chrome(service=service, options=chrome_options)
            driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            return driver
        except WebDriverException as e:
            logging.error(f"Error setting up Chrome driver: {e}")
            messagebox.showerror("Browser Error", "Could not launch browser. Ensure Google Chrome is installed and up to date. If issues persist, install chromedriver manually.")
            return None
        except Exception as e:
            logging.error(f"Error setting up Chrome driver: {e}")
            logging.info("Please make sure Google Chrome is installed on your system and up to date.")
            logging.info("Also, ensure Selenium is updated: pip install -U selenium")
            return None
    
    def wait_for_login(self, driver, timeout=300):
        """
        Ultra-fast login detection using ONLY URL method
        """
        logging.info("Please log into your Roblox account")
    
        detector_script = """
        window.ultraFastDetection = {
            detected: false,
            method: null,
            debug: [],
            cleanup: function() {
                if (this.interval) clearInterval(this.interval);
                if (this.observer) this.observer.disconnect();
            }
        };
    
        function instantDetect() {
            const now = Date.now();
            window.ultraFastDetection.debug.push('URL Check at: ' + now);
        
            const url = window.location.href.toLowerCase();
            window.ultraFastDetection.debug.push('Current URL: ' + url);
        
            if (url.includes('/login') or url.includes('/signup') or url.includes('/createaccount')) {
                window.ultraFastDetection.debug.push('Still on login/signup/create page - not logged in');
                return false;
            }
        
            if (url.includes('/home') or url.includes('/games') or 
                url.includes('/catalog') or url.includes('/avatar') or
                url.includes('/discover') or url.includes('/friends') or
                url.includes('/profile') or url.includes('/groups') or
                url.includes('/develop') or url.includes('/create') or
                url.includes('/transactions') or url.includes('/my/avatar') or
                url.includes('/users/profile') or url.includes('roblox.com/users/') and not url.includes('/login')) {
            
                # Add cookie check after URL match
                if (document.cookie.includes('.ROBLOSECURITY')) {
                    window.ultraFastDetection.detected = true
                    window.ultraFastDetection.method = 'url_with_cookie';
                    window.ultraFastDetection.debug.push('✅ DETECTED via URL and cookie! Page: ' + url');
                    window.ultraFastDetection.cleanup();
                    return true;
                } else {
                    window.ultraFastDetection.debug.push('URL matched but no cookie yet - waiting...');
                    return false;
                }
            }
        
            window.ultraFastDetection.debug.push('Not detected - still checking...');
            return false;
        }
    
        instantDetect();
    
        window.ultraFastDetection.interval = setInterval(() => {
            if (instantDetect()) {
                clearInterval(window.ultraFastDetection.interval);
            }
        }, 25);
    
        let lastHref = location.href;
        window.ultraFastDetection.observer = new MutationObserver(() => {
            if (location.href !== lastHref) {
                lastHref = location.href;
                window.ultraFastDetection.debug.push('URL changed to: ' + location.href);
                if (instantDetect()) {
                    clearInterval(window.ultraFastDetection.interval);
                    window.ultraFastDetection.observer.disconnect();
                }
            }
        });
        window.ultraFastDetection.observer.observe(document, {subtree: true, childList: true});
    
        ['beforeunload', 'unload', 'pagehide'].forEach(event => {
            window.addEventListener(event, () => {
                window.ultraFastDetection.cleanup();
            });
        });
        """
    
        try:
            driver.execute_script(detector_script)
            logging.info("[SUCCESS] Detection script injected successfully")
        except Exception as e:
            logging.warning(f"[WARNING] Warning: Could not inject detection script: {e}")
    
        start_time = time.time()
        last_debug_time = 0
    
        while time.time() - start_time < timeout:
            try:
                result = driver.execute_script("return window.ultraFastDetection;")
            
                if result and result.get('detected'):
                    method = result.get('method', 'url_only')
                    logging.info(f"[SUCCESS] LOGIN DETECTED! Method: {method} - Closing browser instantly...")
                    try:
                        driver.execute_script("window.ultraFastDetection.cleanup();")
                    except:
                        pass
                    return True
            
                current_time = time.time()
                if current_time - last_debug_time > 5:
                    last_debug_time = current_time
                    current_url = driver.current_url
                    if ('/home' in current_url or '/games' in current_url or 
                        '/catalog' in current_url or '/avatar' in current_url or
                        '/discover' in current_url or '/friends' in current_url or
                        '/profile' in current_url or '/groups' in current_url or
                        '/develop' in current_url or '/create' in current_url or
                        '/users/profile' in current_url) and '/login' not in current_url and '/createaccount' not in current_url.lower():
                        logging.info("[SUCCESS] LOGIN DETECTED via manual URL check!")
                        return True
                
                time.sleep(0.025)
            
            except WebDriverException:
                try:
                    driver.execute_script("if(window.ultraFastDetection) window.ultraFastDetection.cleanup();")
                except:
                    pass
                return False
    
        logging.warning("[WARNING] Login timeout. Please try again.")
        try:
            driver.execute_script("if(window.ultraFastDetection) window.ultraFastDetection.cleanup();")
        except:
            pass
        return False
    
    def extract_user_info(self, driver):
        """Extract username and cookie with ultra-fast detection"""
        try:
            roblosecurity_cookie = None
            for attempt in range(3):  # Retry up to 3 times
                time.sleep(1)  # Short delay for cookie to settle
                try:
                    cookies = driver.get_cookies()
                    roblosecurity_cookie = next((c['value'] for c in cookies if c['name'] == '.ROBLOSECURITY'), None)
                    if roblosecurity_cookie:
                        break
                except:
                    pass
            if not roblosecurity_cookie:
                logging.error("[ERROR] Cookie not found after retries")
                return None, None
        
            username = None
            try:
                result = driver.execute_script("return window.ultraFastDetection;")
                if result and result.get('username'):
                    username = result.get('username')
                    logging.info(f"[SUCCESS] Username detected from page: {username}")
            except:
                pass
        
            if not username:
                try:
                    username_selectors = [
                        "[data-testid='navigation-user-display-name']",
                        "[data-testid='user-menu-button']",
                        ".font-header-2.text-color-secondary-alt",
                        "#nav-username",
                        ".navigation-user-name"
                    ]
                
                    for selector in username_selectors:
                        try:
                            element = driver.find_element(By.CSS_SELECTOR, selector)
                            if element and element.text.strip():
                                username = element.text.strip()
                                break
                        except:
                            continue
                        
                except Exception:
                    pass
        
            if not username:
                username = RobloxAPI.get_username_from_api(roblosecurity_cookie)
        
            if not username:
                username = "Unknown"
        
            return username, roblosecurity_cookie
        
        except Exception as e:
            logging.error(f"Error extracting user info: {e}")
            return None, None
    
    def add_account(self, amount=1, website="https://www.roblox.com/login", javascript=""):
        """
        Add accounts through browser login with optional Javascript execution
        amount: number of browser instances to open (max 3)
        website: URL to navigate to
        javascript: Javascript code to execute after page load
        """
        amount = min(amount, 3)  # Limit to 3
        
        success_count = 0
        drivers = []
    
        try:
            logging.info(f"Launching {amount} browser instance(s)...")
        
            for i in range(amount):
                driver = self.setup_chrome_driver()
                if not driver:
                    logging.error(f"[ERROR] Failed to setup Chrome driver for instance {i + 1}")
                    continue
            
                window_width = 500
                window_height = 600
            
                screen_width = driver.execute_script("return screen.width;")
                screen_height = driver.execute_script("return screen.height;")
            
                grid_cols = min(3, amount)
                grid_rows = (amount + grid_cols - 1) // grid_cols
            
                col = i % grid_cols
                row = i // grid_cols
            
                x = col * (screen_width // grid_cols) + 10
                y = row * ((screen_height - 100) // grid_rows) + 10
            
                driver.set_window_position(x, y)
                driver.set_window_size(window_width, window_height)
            
                drivers.append(driver)
            
                try:
                    logging.info(f"Opening {website} (instance {i + 1}/{amount})...")
                    driver.get(website)
                
                    if javascript:
                        logging.info(f"Executing Javascript for instance {i + 1}...")
                        try:
                            driver.execute_script(javascript)
                            logging.info(f"[SUCCESS] Javascript executed for instance {i + 1}")
                        except Exception as js_error:
                            logging.warning(f"[WARNING] Javascript execution failed for instance {i + 1}: {js_error}")
                
                except Exception as e:
                    logging.error(f"[ERROR] Error opening browser for instance {i + 1}: {e}")
        
            logging.info(f"All {len(drivers)} browser(s) opened. Waiting for logins...")
        
            completed = [False] * len(drivers)
        
            import threading
        
            def wait_for_instance(driver_index):
                driver = drivers[driver_index]
                try:
                    if self.wait_for_login(driver):
                        username, cookie = self.extract_user_info(driver)
                    
                        if username and cookie:
                            self.accounts[username] = {
                                'username': username,
                                'cookie': cookie,
                                'added_date': time.strftime('%Y-%m-%d %H:%M:%S')
                            }
                            self.save_accounts()
                        
                            # Post-add validation
                            if self.validate_account(username):
                                logging.info(f"[SUCCESS] Successfully added and validated account: {username}")
                                nonlocal success_count
                                success_count += 1
                            else:
                                del self.accounts[username]
                                self.save_accounts()
                                logging.error(f"[ERROR] Account {username} failed validation - removed")
                        else:
                            logging.error(f"[ERROR] Failed to extract account information for instance {driver_index + 1}")
                            messagebox.showwarning("Cookie Error", "Logged in but couldn't fetch cookie. Try again or check network.")
                    else:
                        logging.warning(f"[WARNING] Login timeout for instance {driver_index + 1}")
                except Exception as e:
                    logging.error(f"[ERROR] Error waiting for login on instance {driver_index + 1}: {e}")
                finally:
                    completed[driver_index] = True
                    try:
                        driver.quit()
                    except:
                        pass
        
            threads = []
            for i in range(len(drivers)):
                thread = threading.Thread(target=wait_for_instance, args=(i,))
                thread.start()
                threads.append(thread)
        
            for thread in threads:
                thread.join()
        
            for driver in drivers:
                self.cleanup_temp_profile()
        
            return success_count > 0
            
        except Exception as e:
            logging.error(f"[ERROR] Error during account addition: {e}")
            for driver in drivers:
                try:
                    driver.quit()
                except:
                    pass
            return False
        finally:
            self.cleanup_temp_profile()
    
    def import_cookie_account(self, cookie):
        if not cookie:
            logging.error("[ERROR] Cookie is required")
            return False, None
        
        cookie = cookie.strip()
        
        if not cookie.startswith('_|WARNING:-DO-NOT-SHARE-THIS.--Sharing-this-will-allow-someone-to-log-in-as-you-and-to-steal-your-ROBUX-and-items.|'):
            logging.error("[ERROR] Invalid cookie format")
            return False, None
        
        try:
            username = RobloxAPI.get_username_from_api(cookie)
            if not username or username == "Unknown":
                logging.error("[ERROR] Failed to get username from cookie")
                return False, None
            
            is_valid = RobloxAPI.validate_account(username, cookie)
            if not is_valid:
                logging.error("[ERROR] Cookie is invalid or expired")
                return False, None
            
            self.accounts[username] = {
                'username': username,
                'cookie': cookie,
                'added_date': time.strftime('%Y-%m-%d %H:%M:%S')
            }
            self.save_accounts()
            
            logging.info(f"[SUCCESS] Successfully imported account: {username}")
            return True, username
            
        except Exception as e:
            logging.error(f"[ERROR] Failed to import account: {e}")
            return False, None
    
    def delete_account(self, username):
        """Delete a saved account"""
        if username in self.accounts:
            del self.accounts[username]
            self.save_accounts()
            logging.info(f"[SUCCESS] Deleted account: {username}")
            return True
        else:
            logging.error(f"[ERROR] Account '{username}' not found")
            return False
    
    def get_account_cookie(self, username):
        """Get cookie for a specific account"""
        if username in self.accounts:
            return self.accounts[username]['cookie']
        return None
    
    def validate_account(self, username):
        """Validate if an account's cookie is still valid"""
        cookie = self.get_account_cookie(username)
        if not cookie:
            logging.error(f"[ERROR] Account '{username}' not found")
            return False
        
        is_valid = RobloxAPI.validate_account(username, cookie)
        if not is_valid:
            self.delete_account(username)
        return is_valid
    
    def launch_home(self, username):
        """Safely open Roblox home page with full login session (undetected)"""
        if username not in self.accounts:
            logging.error(f"[ERROR] Account '{username}' not found")
            return False

        cookie = self.accounts[username]['cookie']

        driver = None
        try:
            logging.info(f"Launching secure browser for {username}...")

            # Use the exact same stealthy driver you use for login
            driver = self.setup_chrome_driver()
            if not driver:
                return False

            # Go to Roblox home first (loads domain)
            driver.get("https://www.roblox.com")

            # Inject the .ROBLOSECURITY cookie
            driver.add_cookie({
                'name': '.ROBLOSECURITY',
                'value': cookie,
                'domain': '.roblox.com',
                'path': '/',
                'secure': True,
                'httpOnly': True
            })

            # Optional: inject a tiny anti-detection script
            driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
                "source": """
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => false,
                });
                window.chrome = {
                    runtime: {},
                    loadTimes: () => {},
                    csi: () => {}
                };
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5],
                });
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en'],
                });
                """
            })

            # Now go to home – you will be fully logged in
            driver.get("https://www.roblox.com/home")

            logging.info(f"[SUCCESS] Secure browser opened: {username}")

            # Keep browser open – do NOT quit()
            return True

        except Exception as e:
            logging.error(f"[ERROR] Failed to launch secure browser: {e}")
            messagebox.showerror("Browser Error", f"Failed to open browser:\n{str(e)}")
            if driver:
                try:
                    driver.quit()
                except:
                    pass
            return False
    
    def launch_roblox(self, username, game_id, job_id=""):
        """Launch Roblox game with specified account"""
        if username not in self.accounts:
            logging.error(f"[ERROR] Account '{username}' not found")
            return False
    
        cookie = self.accounts[username]['cookie']

        with state_lock:
            # Kill existing if running
            if username in tracked_accounts:
                try:
                    psutil.Process(tracked_accounts[username]).kill()
                except:
                    pass
                tracked_accounts.pop(username, None)
                tracked_hwnds.pop(username, None)
            launched_accounts.discard(username)

        with launch_lock:
            prev_pids = get_roblox_pids()
            success = RobloxAPI.launch_roblox(username, cookie, game_id, job_id=job_id)
            if success:
                with state_lock:
                    self.accounts[username]['current_server'] = {'place_id': game_id, 'job_id': job_id}
                    try:
                        response = requests.get(f"https://games.roblox.com/v1/games/multiget-place-details?placeIds={game_id}")
                        if response.status_code == 200:
                            data = response.json()
                            if data and 'name' in data[0]:
                                self.accounts[username]['current_game_name'] = data[0]['name']
                    except Exception as e:
                        logging.error(f"Failed to fetch game name: {e}")
                    self.save_accounts()
                pid = wait_for_new_pid(prev_pids)
                if pid:
                    hwnd = wait_for_hwnd(pid)
                    if hwnd:
                        with state_lock:
                            tracked_accounts[username] = pid
                            tracked_hwnds[username] = hwnd
                            launched_accounts.add(username)
                            error_counter[username] = 0
                            if username in last_errors:
                                del last_errors[username]
                        logging.info(f"[PID]: {pid} [HWND]: {hwnd}")
                        return True
                    else:
                        logging.warning(f"HWND not found for {username}")
                else:
                    logging.warning(f"PID not found for {username}")
            with state_lock:
                last_errors[username] = ("Launch failed", datetime.now().strftime("%H:%M:%S"))
        return False

if __name__ == "__main__":
    app = ModernRobloxManager()
    app.mainloop()