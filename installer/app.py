"""MySmartBlinds desktop setup wizard. SPDX-License-Identifier: GPL-2.0-or-later"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import queue
import secrets
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import uuid
import webbrowser

from core import (VERSION, FIRMWARE_VERSION, InstallerError, configure, flash,
                  load_backup, open_for_setup, validate_bundle, validate_network)

RESOURCES = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
CHANNEL = "https://bestow-regional.api.smartthings.com/invite/oDM8Q4Bkgp2L"
GUIDE = "https://github.com/tylerdurden94/MySmartBlinds/blob/codex/usb-setup-installer/docs/INSTALLER_GUIDE.md"


class Wizard:
    def __init__(self, root, *, demo=False):
        self.root = root
        self.demo = demo
        self.busy = False
        self.events = queue.Queue()
        self.blinds = []
        self.ports = {}
        self.log = []
        self.address = None
        self.token = tk.StringVar()
        self.token_path = None
        self.mode = tk.StringVar(value="install")
        self.port = tk.StringVar()
        self.ssid = tk.StringVar()
        self.password = tk.StringVar()
        self.consent = tk.BooleanVar()
        self.status = tk.StringVar(value="Ready. Connect one supported ESP32 with a USB data cable.")
        self.backup_label = tk.StringVar(value="No backup selected")
        self.root.title(f"MySmartBlinds Setup · {VERSION}" + (" · DEMO" if demo else ""))
        self.root.geometry("850x790")
        self.root.minsize(760, 690)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        style = ttk.Style(root)
        if sys.platform == "win32":
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Segoe UI" if sys.platform == "win32" else "Helvetica", 20, "bold"))
        style.configure("Heading.TLabel", font=("Segoe UI" if sys.platform == "win32" else "Helvetica", 11, "bold"))
        frame = ttk.Frame(root, padding=22)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="MySmartBlinds Setup", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text="USB firmware installation and home Wi-Fi setup").pack(anchor="w", pady=(3, 12))
        ttk.Label(frame, text="TEST BUILD · Unsigned · Physical USB and blind operation still need tester validation.",
                  foreground="#9b4400", wraplength=790).pack(anchor="w", pady=(0, 12))
        self.tabs = ttk.Notebook(frame)
        self.tabs.pack(fill="both", expand=True)
        self.board = ttk.Frame(self.tabs, padding=18)
        self.settings = ttk.Frame(self.tabs, padding=18)
        self.result = ttk.Frame(self.tabs, padding=18)
        self.tabs.add(self.board, text="1  Board")
        self.tabs.add(self.settings, text="2  Wi-Fi & blinds")
        self.tabs.add(self.result, text="3  Install & finish")
        self.make_board()
        self.make_settings()
        self.make_result()
        ttk.Label(frame, textvariable=self.status, wraplength=790).pack(anchor="w", pady=(12, 5))
        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.pack(fill="x")
        footer = ttk.Frame(frame)
        footer.pack(fill="x", pady=(12, 0))
        self.help_button = ttk.Button(footer, text="Setup guide", command=lambda: webbrowser.open(GUIDE))
        self.help_button.pack(side="left")
        self.diagnostic_button = ttk.Button(footer, text="Save test report", command=self.save_report)
        self.diagnostic_button.pack(side="left", padx=8)
        ttk.Label(footer, text=VERSION).pack(side="right")
        self.mode_changed()
        if demo:
            self.port.set("Demo ESP32 (no hardware access)")
            self.ports[self.port.get()] = "DEMO"
            from core import Blind
            self.blinds = [Blind("00000000-0000-4000-8000-000000000001", "Demo room", "Demo blind",
                                 "06:05:04:03:02:01", bytes(range(6)))]
            self.ssid.set("Demo network")
            self.password.set("demo-password")
            self.blind_list.insert("end", "Demo room / Demo blind")
            self.blind_list.selection_set(0)
            self.backup_label.set("Synthetic demo data — no backup loaded")
        else:
            self.refresh_ports()
        self.root.after(100, self.pump)

    def paragraph(self, parent, text):
        ttk.Label(parent, text=text, wraplength=700, justify="left").pack(anchor="w", pady=(0, 12))

    def make_board(self):
        self.paragraph(self.board, "Use a classic ESP32-WROOM USB development board with at least 4 MB flash. "
                       "ESP32-S3, C3, C6, S2 and ESP8266 are not supported by this firmware.")
        ttk.Label(self.board, text="Choose an action", style="Heading.TLabel").pack(anchor="w", pady=(4, 8))
        for value, label in [("install", "Install firmware and set up this board"),
                             ("update", "Update firmware only — keep existing Wi-Fi, token and blinds"),
                             ("setup", "Set up existing bridge — replace Wi-Fi, token and blind assignments")]:
            ttk.Radiobutton(self.board, text=label, value=value, variable=self.mode,
                            command=self.mode_changed).pack(anchor="w", pady=5)
        ttk.Label(self.board, text="USB board", style="Heading.TLabel").pack(anchor="w", pady=(20, 8))
        row = ttk.Frame(self.board)
        row.pack(fill="x")
        self.port_picker = ttk.Combobox(row, textvariable=self.port, state="readonly", width=58)
        self.port_picker.pack(side="left", fill="x", expand=True)
        self.refresh_button = ttk.Button(row, text="Refresh", command=self.refresh_ports)
        self.refresh_button.pack(side="left", padx=(8, 0))
        self.paragraph(self.board, "Connect one board at a time. If it is missing, try a USB data cable, "
                       "another USB port, or the USB driver recommended by the board manufacturer. "
                       "Close PlatformIO and other serial monitors.")
        ttk.Separator(self.board).pack(fill="x", pady=12)
        self.paragraph(self.board, "For a new installation, use a spare board. Installing firmware replaces its "
                       "current application. Routine updates preserve this bridge's settings and require "
                       "the matching partition layout. No full-flash erase is performed.")
        ttk.Button(self.board, text="Continue", command=lambda: self.tabs.select(
            self.result if self.mode.get() == "update" else self.settings)).pack(anchor="e", pady=8)

    def make_settings(self):
        self.paragraph(self.settings, "These details are sent directly to the ESP32 over USB. "
                       "The installer does not upload your backup, passwords or blind keys.")
        ttk.Label(self.settings, text="Home Wi-Fi (2.4 GHz)", style="Heading.TLabel").pack(anchor="w")
        form = ttk.Frame(self.settings)
        form.pack(fill="x", pady=(10, 18))
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="Network name").grid(row=0, column=0, sticky="w", padx=(0, 12), pady=5)
        ttk.Entry(form, textvariable=self.ssid).grid(row=0, column=1, sticky="ew", pady=5)
        ttk.Label(form, text="Password").grid(row=1, column=0, sticky="w", pady=5)
        self.password_entry = ttk.Entry(form, textvariable=self.password, show="•")
        self.password_entry.grid(row=1, column=1, sticky="ew", pady=5)
        self.show_password = tk.BooleanVar()
        ttk.Checkbutton(form, text="Show", variable=self.show_password,
                        command=lambda: self.password_entry.configure(show="" if self.show_password.get() else "•")
                        ).grid(row=1, column=2, padx=(8, 0))
        row = ttk.Frame(self.settings)
        row.pack(fill="x", pady=(0, 8))
        ttk.Button(row, text="Choose Android app backup…", command=self.choose_backup).pack(side="left")
        ttk.Label(row, textvariable=self.backup_label).pack(side="left", padx=12)
        self.paragraph(self.settings, "Select only the blinds this board should control. Assign each blind to one "
                       "bridge. Other imported blinds remain disabled here. This setup does not move the blinds.")
        self.blind_list = tk.Listbox(self.settings, selectmode="multiple", exportselection=False,
                                     height=10, activestyle="none")
        self.blind_list.pack(fill="both", expand=True)
        self.paragraph(self.settings, "A compatible backup is required for this setup wizard. Export it from the "
                       "original Android app. It must contain 1–10 blinds; iOS exports are unverified.")
        ttk.Button(self.settings, text="Continue", command=lambda: self.tabs.select(self.result)).pack(anchor="e")

    def make_result(self):
        self.summary = tk.StringVar()
        ttk.Label(self.result, textvariable=self.summary, wraplength=700, justify="left").pack(anchor="w", pady=(0, 14))
        ttk.Checkbutton(self.result, text="I selected the intended board and understand the replacement described above.",
                        variable=self.consent).pack(anchor="w", pady=(0, 14))
        self.start_button = ttk.Button(self.result, text="Install and set up", command=self.start)
        self.start_button.pack(anchor="w", pady=(0, 12))
        self.activity = tk.Text(self.result, height=7, wrap="word", state="disabled")
        self.activity.pack(fill="both", expand=True, pady=(0, 12))
        ttk.Label(self.result, text="SmartThings bridge token (private)", style="Heading.TLabel").pack(anchor="w")
        token_row = ttk.Frame(self.result)
        token_row.pack(fill="x", pady=(7, 4))
        ttk.Entry(token_row, textvariable=self.token, state="readonly").pack(side="left", fill="x", expand=True)
        self.copy_button = ttk.Button(token_row, text="Copy token", command=self.copy_token)
        self.copy_button.pack(side="left", padx=(8, 0))
        self.token_note = tk.StringVar(value="A separate token recovery file will be saved privately for this setup.")
        ttk.Label(self.result, textvariable=self.token_note, wraplength=700).pack(anchor="w", pady=(0, 10))
        self.paragraph(self.result, "Finish in SmartThings: enroll your hub, install MySmartBlinds ESP Bridge, "
                       "scan nearby, then paste this token into the bridge device's Settings. "
                       "Test one blind before adding more.")
        actions = ttk.Frame(self.result)
        actions.pack(fill="x")
        ttk.Button(actions, text="SmartThings driver invitation", command=lambda: webbrowser.open(CHANNEL)).pack(side="left")
        self.web_button = ttk.Button(actions, text="Open bridge settings", command=self.open_bridge, state="disabled")
        self.web_button.pack(side="left", padx=8)

    def mode_changed(self):
        mode = self.mode.get()
        self.consent.set(False)
        self.start_button.configure(text={"install": "Install and set up", "setup": "Save setup over USB",
                                          "update": "Update firmware"}[mode])
        self.summary.set({
            "install": "Installs the bundled firmware, then replaces the board's Wi-Fi, token and blind "
                       "configuration with your selections. Use a spare supported board for this first test.",
            "setup": "Requires this project's firmware already installed. Replaces the board's Wi-Fi, "
                     "token and all blind assignments. Update the token in SmartThings afterwards.",
            "update": "Updates only the application firmware after checking the partition layout. "
                      "Keeps existing Wi-Fi, token, blind assignments and polling settings. "
                      "The Wi-Fi and backup fields are unused for this action."}[mode])
        self.tabs.tab(self.settings, state="disabled" if mode == "update" else "normal")

    def refresh_ports(self):
        if self.busy or self.demo:
            return
        from serial.tools import list_ports
        try:
            ports = list(list_ports.comports())
        except Exception:
            self.status.set("Could not list USB ports. Reconnect the board and try Refresh.")
            return
        self.ports = {f"{p.device} — {p.description}": p.device for p in ports
                      if "Bluetooth" not in p.description}
        self.port_picker.configure(values=list(self.ports))
        if self.port.get() not in self.ports:
            self.port.set(next(iter(self.ports)) if len(self.ports) == 1 else "")
        self.status.set(f"Found {len(self.ports)} serial port(s). Select the intended ESP32." if self.ports else
                        "No USB board found. Check the data cable and driver, then click Refresh.")

    def choose_backup(self):
        path = filedialog.askopenfilename(title="Choose private Android MySmartBlinds backup",
                                          filetypes=[("JSON backup", "*.json"), ("All files", "*")])
        if not path:
            return
        try:
            items = load_backup(Path(path))
        except InstallerError as error:
            messagebox.showerror("Backup not imported", str(error))
            return
        self.blinds = items
        self.blind_list.delete(0, "end")
        for blind in items:
            self.blind_list.insert("end", f"{blind.room} / {blind.name}")
        self.backup_label.set(f"{len(items)} blind(s) imported privately")

    def report(self, text):
        self.events.put(("log", text))

    def start(self):
        if self.busy:
            return
        mode = self.mode.get()
        try:
            if self.port.get() not in self.ports:
                raise InstallerError("Select the USB port on the Board tab first.")
            if not self.consent.get():
                raise InstallerError("Review the action and check the board confirmation before starting.")
            if mode != "setup":
                validate_bundle(RESOURCES / "firmware")
            if mode != "update":
                validate_network(self.ssid.get(), self.password.get())
                if not self.blinds:
                    raise InstallerError("Choose your Android blind backup on the Wi-Fi & blinds tab.")
            selected = {self.blinds[i].id for i in self.blind_list.curselection()}
            if mode != "update" and not selected:
                raise InstallerError("Select at least one blind this board should control.")
        except InstallerError as error:
            messagebox.showerror("Check setup", str(error))
            return
        self.address = None
        self.web_button.configure(state="disabled")
        self.token.set("")
        self.token_path = None
        token = secrets.token_urlsafe(32) if mode != "update" else ""
        snapshot = (self.ports[self.port.get()], mode, self.ssid.get(), self.password.get(),
                    list(self.blinds), selected, token)
        self.set_busy(True)
        threading.Thread(target=self.work, args=snapshot, daemon=True).start()

    def save_token(self, token):
        if self.demo:
            return None
        if sys.platform == "win32":
            base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MySmartBlinds"
        elif sys.platform == "darwin":
            base = Path.home() / "Library" / "Application Support" / "MySmartBlinds"
        else:
            base = Path.home() / ".config" / "MySmartBlinds"
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = base / f"bridge-token-{uuid.uuid4().hex[:12]}.txt"
        # Persist before any board write, so an interruption cannot lose the token.
        with open(path, "x", encoding="utf-8", opener=lambda p, flags: os.open(p, flags, 0o600)) as handle:
            handle.write(token + "\n")
        return path

    def work(self, port, mode, ssid, password, blinds, selected, token):
        try:
            if mode != "update":
                path = self.save_token(token)
                self.events.put(("token", (token, path)))
            if self.demo:
                for text in ["DEMO: no USB ports opened and no settings saved.", "Simulating firmware verification…",
                             "Simulating USB configuration…"]:
                    self.report(text)
                    time.sleep(0.4)
                address = None
            else:
                if mode != "setup":
                    flash(port, RESOURCES / "firmware", self.report, update_only=mode == "update")
                address = None
                if mode != "update":
                    with open_for_setup(port) as connection:
                        address = configure(connection, ssid, password, token, blinds, selected, self.report)
            self.events.put(("done", (mode, address)))
        except InstallerError as error:
            self.events.put(("error", str(error)))
        except Exception:
            self.events.put(("error", "The operation could not finish. Check USB, drivers and available disk space, "
                                      "then retry. Save the test report if the issue continues."))

    def set_busy(self, busy):
        self.busy = busy
        if busy:
            self.saved_states = []
            def disable(widget):
                for child in widget.winfo_children():
                    if isinstance(child, (ttk.Button, ttk.Entry, ttk.Combobox, ttk.Checkbutton, ttk.Radiobutton, tk.Listbox)):
                        self.saved_states.append((child, str(child.cget("state"))))
                        child.configure(state="disabled")
                    disable(child)
            disable(self.root)
            self.progress.start(15)
        else:
            for widget, state in self.saved_states:
                widget.configure(state=state)
            self.progress.stop()
        self.status.set("Working — keep the ESP32 connected by USB." if busy else "Ready.")

    def pump(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "log":
                    self.log.append(data)
                    self.activity.configure(state="normal")
                    self.activity.insert("end", data + "\n")
                    self.activity.see("end")
                    self.activity.configure(state="disabled")
                    self.status.set(data)
                elif kind == "token":
                    self.token.set(data[0])
                    self.token_path = data[1]
                    self.token_note.set("Private recovery copy: " + str(data[1]) if data[1] else "DEMO token — do not use in SmartThings.")
                elif kind in ("done", "error"):
                    self.set_busy(False)
                    self.password.set("")
                    if kind == "error":
                        self.log.append(data)
                        self.status.set("Stopped. " + data)
                        messagebox.showerror("Setup needs attention", data)
                    else:
                        mode, self.address = data
                        if self.address:
                            self.web_button.configure(state="normal")
                        if self.demo:
                            text = "Demo finished. No hardware was accessed."
                        elif mode == "update":
                            text = "Firmware update verified. Existing settings kept; physical operation still needs testing."
                        elif self.address:
                            text = "USB setup complete. Wi-Fi connected at " + self.address + ". Finish in SmartThings below."
                        else:
                            text = "Settings saved; Wi-Fi connection was not confirmed. Retry setup or check the ESP's setup page."
                        self.log.append(text if not self.address else "USB setup complete; Wi-Fi connected. Address omitted from report.")
                        self.status.set(text)
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    def copy_token(self):
        if self.token.get():
            self.root.clipboard_clear()
            self.root.clipboard_append(self.token.get())
            self.status.set("Token copied. Paste it into the SmartThings bridge device's Settings.")

    def open_bridge(self):
        if self.address:
            webbrowser.open(f"http://{self.address}/setup")

    def save_report(self):
        path = filedialog.asksaveasfilename(title="Save shareable test report", defaultextension=".txt",
                                           initialfile="MySmartBlinds-test-report.txt")
        if not path:
            return
        report = ["MySmartBlinds installer test report", f"Installer: {VERSION}",
                  f"Firmware: {FIRMWARE_VERSION}", f"OS: {platform.system()} {platform.release()} {platform.machine()}",
                  f"Action: {self.mode.get()}", f"Demo: {self.demo}",
                  "Wi-Fi names/passwords, tokens, backup contents, blind names and device identifiers omitted.", "", *self.log]
        try:
            Path(path).write_text("\n".join(report) + "\n", encoding="utf-8")
            self.status.set("Test report saved. Review it before sharing.")
        except OSError:
            messagebox.showerror("Could not save", "Choose another folder for the report.")

    def close(self):
        if self.busy:
            messagebox.showinfo("Operation in progress", "Keep the application open and USB connected until this operation finishes.")
            return
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description="MySmartBlinds USB setup wizard")
    parser.add_argument("--demo", action="store_true", help="Preview without opening USB ports or saving credentials")
    parser.add_argument("--self-test", type=Path, metavar="REPORT", help="Verify the packaged app without touching hardware")
    args = parser.parse_args()
    if args.self_test:
        validate_bundle(RESOURCES / "firmware")
        import esptool
        import serial
        root = tk.Tk()
        root.withdraw()
        wizard = Wizard(root, demo=True)
        root.update()
        for tab in (wizard.board, wizard.settings, wizard.result):
            wizard.tabs.select(tab)
            root.update()
        root.destroy()
        args.self_test.write_text(json.dumps({"status": "passed", "installer": VERSION,
            "firmware": FIRMWARE_VERSION, "os": platform.system(), "arch": platform.machine(),
            "esptool": esptool.__version__, "pyserial": serial.VERSION,
            "gui_created": True, "firmware_hashes_verified": True, "hardware_accessed": False}, indent=2) + "\n")
        return
    root = tk.Tk()
    Wizard(root, demo=args.demo)
    root.mainloop()


if __name__ == "__main__":
    main()
