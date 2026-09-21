#!/usr/bin/env python3
"""
Zel.NeD GUI — Modern dark-mode desktop client for Nintendo 3DS file transfer
=============================================================================
Requires: customtkinter>=5.2, tkinterdnd2>=0.3, lz4>=4.0
Run: python zelned_gui.py
"""

import sys
import threading
import time
from pathlib import Path
from typing import Optional

import customtkinter as ctk
from tkinter import filedialog, messagebox

# Try importing tkinterdnd2 (optional — drag-and-drop support)
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _DND_AVAILABLE = True
except ImportError:
    _DND_AVAILABLE = False

from protocol import (
    connect_to_3ds, send_session_header, send_file,
    send_directory_entry, ThrottleController, TCP_PORT, ProtocolError
)
from discovery import DiscoveryListener
from queue_manager import QueueManager, ItemStatus
from resume_state import ResumeState

# ── App Theme ─────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

APP_NAME    = "Zel.NeD"
APP_VERSION = "1.0"
APP_TITLE   = f"⬡ {APP_NAME}  v{APP_VERSION} — Wireless Transfer for Nintendo 3DS"

# Colors
C_BG      = "#1a1a2e"
C_PANEL   = "#16213e"
C_ACCENT  = "#0f3460"
C_BLUE    = "#4fc3f7"
C_GREEN   = "#66bb6a"
C_YELLOW  = "#ffca28"
C_RED     = "#ef5350"
C_PURPLE  = "#9c27b0"
C_TEXT    = "#e0e0e0"
C_DIM     = "#777788"


class ZelNedApp(ctk.CTk):
    """Main application window."""

    def __init__(self):
        super().__init__()

        if _DND_AVAILABLE:
            try:
                TkinterDnD._require(self)
            except Exception:
                pass

        self.title(APP_TITLE)
        self.geometry("720x680")
        self.minsize(640, 560)
        self.configure(fg_color=C_BG)

        # State
        self._queue    = QueueManager()
        self._listener = DiscoveryListener(on_change=self._on_devices_changed)
        self._listener.start()
        self._transfer_thread: Optional[threading.Thread] = None
        self._cancel_flag     = threading.Event()
        self._pause_flag      = threading.Event()
        self._connected_ip: Optional[str] = None
        self._stats_bps: float  = 0.0
        self._stats_ratio: float = 1.0

        self._build_ui()
        self._refresh_devices()
        self._refresh_queue_list()
        self._poll_ui()

    # ── UI Construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        # Header
        header = ctk.CTkFrame(self, fg_color=C_PANEL, corner_radius=12)
        header.pack(fill="x", padx=12, pady=(12, 4))

        ctk.CTkLabel(header, text=f"⬡ {APP_NAME}",
                     font=ctk.CTkFont("Segoe UI", 22, "bold"),
                     text_color=C_BLUE).pack(side="left", padx=16, pady=8)
        ctk.CTkLabel(header, text=f"v{APP_VERSION}  ·  ZELNED Protocol",
                     font=ctk.CTkFont("Segoe UI", 11),
                     text_color=C_DIM).pack(side="left", padx=0, pady=8)

        self._lbl_status = ctk.CTkLabel(header, text="● Idle",
                                        font=ctk.CTkFont("Segoe UI", 11, "bold"),
                                        text_color=C_DIM)
        self._lbl_status.pack(side="right", padx=16)

        # Connection Panel
        conn = ctk.CTkFrame(self, fg_color=C_PANEL, corner_radius=12)
        conn.pack(fill="x", padx=12, pady=4)
        conn.columnconfigure(1, weight=1)

        ctk.CTkLabel(conn, text="Console:", text_color=C_TEXT,
                     font=ctk.CTkFont("Segoe UI", 12)).grid(row=0, column=0, padx=12, pady=8, sticky="w")
        self._combo_devices = ctk.CTkComboBox(conn, values=["Scanning..."],
                                              width=280,
                                              command=self._on_device_selected)
        self._combo_devices.grid(row=0, column=1, padx=4, pady=8, sticky="ew")
        ctk.CTkButton(conn, text="↺", width=36, command=self._refresh_devices,
                      fg_color=C_ACCENT).grid(row=0, column=2, padx=4)

        ctk.CTkLabel(conn, text="IP Manual:", text_color=C_TEXT,
                     font=ctk.CTkFont("Segoe UI", 12)).grid(row=1, column=0, padx=12, pady=(0, 8), sticky="w")
        self._entry_ip = ctk.CTkEntry(conn, placeholder_text="192.168.1.47", width=200)
        self._entry_ip.grid(row=1, column=1, padx=4, pady=(0, 8), sticky="w")

        ctk.CTkLabel(conn, text="Throttle:", text_color=C_TEXT,
                     font=ctk.CTkFont("Segoe UI", 12)).grid(row=2, column=0, padx=12, pady=(0, 8), sticky="w")
        self._combo_throttle = ctk.CTkComboBox(conn,
                                               values=["Unlimited", "600 KB/s", "400 KB/s", "200 KB/s"],
                                               width=160)
        self._combo_throttle.set("Unlimited")
        self._combo_throttle.grid(row=2, column=1, padx=4, pady=(0, 8), sticky="w")

        # Drop zone
        drop = ctk.CTkFrame(self, fg_color=C_ACCENT, corner_radius=12)
        drop.pack(fill="x", padx=12, pady=4)

        self._lbl_drop = ctk.CTkLabel(
            drop,
            text="⤓  Drag files or folders here",
            font=ctk.CTkFont("Segoe UI", 14),
            text_color=C_BLUE,
        )
        self._lbl_drop.pack(pady=14)

        btn_row = ctk.CTkFrame(drop, fg_color="transparent")
        btn_row.pack(pady=(0, 12))
        ctk.CTkButton(btn_row, text="+ Add File", width=130,
                      command=self._browse_files).pack(side="left", padx=6)
        ctk.CTkButton(btn_row, text="+ Add Folder", width=130,
                      command=self._browse_folder).pack(side="left", padx=6)

        if _DND_AVAILABLE:
            self._lbl_drop.drop_target_register(DND_FILES)
            self._lbl_drop.dnd_bind("<<Drop>>", self._on_drop)
            drop.drop_target_register(DND_FILES)
            drop.dnd_bind("<<Drop>>", self._on_drop)

        # Queue list
        queue_frame = ctk.CTkFrame(self, fg_color=C_PANEL, corner_radius=12)
        queue_frame.pack(fill="both", expand=True, padx=12, pady=4)

        q_header = ctk.CTkFrame(queue_frame, fg_color="transparent")
        q_header.pack(fill="x", padx=8, pady=(8, 4))
        ctk.CTkLabel(q_header, text="Transfer Queue",
                     font=ctk.CTkFont("Segoe UI", 13, "bold"),
                     text_color=C_BLUE).pack(side="left")

        q_btns = ctk.CTkFrame(q_header, fg_color="transparent")
        q_btns.pack(side="right")
        ctk.CTkButton(q_btns, text="↑", width=32, command=self._move_up,
                      fg_color=C_ACCENT).pack(side="left", padx=2)
        ctk.CTkButton(q_btns, text="↓", width=32, command=self._move_down,
                      fg_color=C_ACCENT).pack(side="left", padx=2)
        ctk.CTkButton(q_btns, text="✗ Remove", width=90, command=self._remove_selected,
                      fg_color="#5c2020").pack(side="left", padx=2)
        ctk.CTkButton(q_btns, text="Clear done", width=90, command=self._clear_done,
                      fg_color=C_ACCENT).pack(side="left", padx=2)

        self._listbox = ctk.CTkScrollableFrame(queue_frame, fg_color="transparent")
        self._listbox.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._queue_rows: list = []
        self._selected_idx: Optional[int] = None

        # Progress / Stats bar
        prog_frame = ctk.CTkFrame(self, fg_color=C_PANEL, corner_radius=12)
        prog_frame.pack(fill="x", padx=12, pady=(4, 4))

        self._progressbar = ctk.CTkProgressBar(prog_frame, height=14, corner_radius=6)
        self._progressbar.set(0)
        self._progressbar.pack(fill="x", padx=12, pady=(10, 4))

        stats_row = ctk.CTkFrame(prog_frame, fg_color="transparent")
        stats_row.pack(fill="x", padx=12, pady=(0, 8))
        self._lbl_speed    = ctk.CTkLabel(stats_row, text="≈ 0.0 MB/s effective",
                                          font=ctk.CTkFont("Segoe UI", 11), text_color=C_DIM)
        self._lbl_speed.pack(side="left")
        self._lbl_ratio    = ctk.CTkLabel(stats_row, text="LZ4 ratio: —",
                                          font=ctk.CTkFont("Segoe UI", 11), text_color=C_DIM)
        self._lbl_ratio.pack(side="left", padx=16)
        self._lbl_eta      = ctk.CTkLabel(stats_row, text="ETA: —",
                                          font=ctk.CTkFont("Segoe UI", 11), text_color=C_DIM)
        self._lbl_eta.pack(side="right")

        # Action buttons
        act_frame = ctk.CTkFrame(self, fg_color="transparent")
        act_frame.pack(fill="x", padx=12, pady=(0, 12))

        self._btn_send = ctk.CTkButton(act_frame, text="⚡ Send Queue",
                                       font=ctk.CTkFont("Segoe UI", 14, "bold"),
                                       height=44, fg_color=C_BLUE, hover_color="#0288d1",
                                       text_color=C_BG, command=self._start_transfer)
        self._btn_send.pack(side="left", padx=4)

        self._btn_pause = ctk.CTkButton(act_frame, text="⏸ Pause",
                                        height=44, width=100, fg_color=C_ACCENT,
                                        command=self._toggle_pause)
        self._btn_pause.pack(side="left", padx=4)

        self._btn_cancel = ctk.CTkButton(act_frame, text="✗ Cancel",
                                         height=44, width=100, fg_color="#5c2020",
                                         command=self._cancel_transfer)
        self._btn_cancel.pack(side="left", padx=4)

        ctk.CTkButton(act_frame, text="Discover 3DS",
                      height=44, fg_color=C_ACCENT, command=self._refresh_devices).pack(side="right", padx=4)

    # ── Device Discovery ──────────────────────────────────────────────────────

    def _on_devices_changed(self):
        self.after(0, self._refresh_devices)

    def _refresh_devices(self):
        devices = self._listener.devices()
        if devices:
            names = [d.display_name for d in devices]
            self._combo_devices.configure(values=names)
            if not self._combo_devices.get() or self._combo_devices.get() == "Scanning...":
                self._combo_devices.set(names[0])
                self._on_device_selected(names[0])
        else:
            self._combo_devices.configure(values=["No 3DS found — check Zel.NeD is running"])
            self._combo_devices.set("No 3DS found — check Zel.NeD is running")

    def _on_device_selected(self, value: str):
        # Extract IP from "Name  [IP:PORT]"
        if "[" in value and ":" in value:
            ip = value.split("[")[1].split(":")[0]
            self._entry_ip.delete(0, "end")
            self._entry_ip.insert(0, ip)

    # ── Queue Management ──────────────────────────────────────────────────────

    def _browse_files(self):
        paths = filedialog.askopenfilenames(title="Select files to send to 3DS")
        if paths:
            for p in paths:
                self._queue.add_path(Path(p))
            self._refresh_queue_list()

    def _browse_folder(self):
        path = filedialog.askdirectory(title="Select folder to send to 3DS")
        if path:
            self._queue.add_path(Path(path))
            self._refresh_queue_list()

    def _on_drop(self, event):
        raw = event.data
        # Parse paths (may be space-separated or curly-braced on Windows)
        import re
        paths = re.findall(r'\{[^}]+\}|[^\s]+', raw)
        paths = [p.strip("{}") for p in paths]
        for p in paths:
            self._queue.add_path(Path(p))
        self._refresh_queue_list()

    def _refresh_queue_list(self):
        # Clear existing rows
        for widget in self._listbox.winfo_children():
            widget.destroy()
        self._queue_rows.clear()

        items = self._queue.snapshot()
        for idx, item in enumerate(items):
            row = ctk.CTkFrame(self._listbox, fg_color=C_ACCENT if idx == self._selected_idx else C_BG,
                               corner_radius=6)
            row.pack(fill="x", pady=2)
            row.bind("<Button-1>", lambda e, i=idx: self._select_row(i))

            # Status icon
            icons = {
                ItemStatus.PENDING:   ("○", C_DIM),
                ItemStatus.SENDING:   ("▶", C_BLUE),
                ItemStatus.DONE:      ("✓", C_GREEN),
                ItemStatus.FAILED:    ("✗", C_RED),
                ItemStatus.CANCELLED: ("⊘", C_DIM),
                ItemStatus.PAUSED:    ("⏸", C_YELLOW),
            }
            icon, color = icons.get(item.status, ("?", C_DIM))
            ctk.CTkLabel(row, text=icon, text_color=color, width=24,
                         font=ctk.CTkFont("Segoe UI", 13, "bold")).pack(side="left", padx=(8, 4))

            # Name + size
            ctk.CTkLabel(row, text=item.display_name,
                         font=ctk.CTkFont("Segoe UI", 12),
                         text_color=C_TEXT, anchor="w").pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(row, text=item.size_str,
                         font=ctk.CTkFont("Segoe UI", 11),
                         text_color=C_DIM, width=80).pack(side="right", padx=8)

            # Inline progress bar for sending items
            if item.status == ItemStatus.SENDING and item.file_size > 0:
                pb = ctk.CTkProgressBar(row, height=4, width=120, corner_radius=2)
                pb.set(item.progress)
                pb.pack(side="right", padx=4)

            self._queue_rows.append(row)

    def _select_row(self, idx: int):
        self._selected_idx = idx
        self._refresh_queue_list()

    def _move_up(self):
        if self._selected_idx is not None:
            self._queue.move_up(self._selected_idx)
            self._selected_idx = max(0, self._selected_idx - 1)
            self._refresh_queue_list()

    def _move_down(self):
        if self._selected_idx is not None:
            self._queue.move_down(self._selected_idx)
            self._selected_idx = min(len(self._queue.snapshot()) - 1,
                                     self._selected_idx + 1)
            self._refresh_queue_list()

    def _remove_selected(self):
        if self._selected_idx is not None:
            self._queue.cancel_item(self._selected_idx)
            self._selected_idx = None
            self._refresh_queue_list()

    def _clear_done(self):
        self._queue.remove_done()
        self._selected_idx = None
        self._refresh_queue_list()

    # ── Transfer ──────────────────────────────────────────────────────────────

    def _get_ip(self) -> Optional[str]:
        ip = self._entry_ip.get().strip()
        return ip if ip else None

    def _get_throttle_kbps(self) -> int:
        val = self._combo_throttle.get()
        if val.startswith("Unlimited"):
            return 0
        return int(val.split()[0])

    def _start_transfer(self):
        ip = self._get_ip()
        if not ip:
            messagebox.showwarning("No IP", "Enter the 3DS IP address or use 'Discover 3DS'.")
            return
        if not self._queue.snapshot():
            messagebox.showinfo("Empty queue", "Add files or folders first.")
            return
        if self._transfer_thread and self._transfer_thread.is_alive():
            messagebox.showinfo("Busy", "A transfer is already in progress.")
            return

        self._cancel_flag.clear()
        self._pause_flag.clear()
        self._btn_send.configure(state="disabled")
        self._lbl_status.configure(text="● Connecting...", text_color=C_YELLOW)

        throttle_kbps = self._get_throttle_kbps()
        self._transfer_thread = threading.Thread(
            target=self._transfer_worker, args=(ip, throttle_kbps), daemon=True
        )
        self._transfer_thread.start()

    def _transfer_worker(self, ip: str, throttle_kbps: int):
        self._connected_ip = ip
        throttle = ThrottleController(throttle_kbps)

        try:
            sock = connect_to_3ds(ip)
        except (ConnectionRefusedError, TimeoutError, OSError) as e:
            self.after(0, lambda: self._on_transfer_error(f"Cannot connect to {ip}: {e}"))
            return

        items = [i for i in self._queue.snapshot() if i.status == ItemStatus.PENDING]
        total_files = sum(1 for i in items if not i.is_dir)

        try:
            send_session_header(sock, throttle_kbps, total_files, resume=True)
        except ProtocolError as e:
            self.after(0, lambda: self._on_transfer_error(str(e)))
            sock.close()
            return

        self.after(0, lambda: self._lbl_status.configure(
            text=f"● Transferring to {ip}", text_color=C_GREEN))

        _transfer_start = time.monotonic()
        _total_bytes = sum(i.file_size for i in items if not i.is_dir)
        _bytes_sent_total = 0

        for item in items:
            if self._cancel_flag.is_set():
                break

            self._queue.mark_sending(item)
            self.after(0, self._refresh_queue_list)

            if item.is_dir:
                try:
                    send_directory_entry(sock, item.remote_path)
                    self._queue.mark_done(item)
                except ProtocolError:
                    pass
                self.after(0, self._refresh_queue_list)
                continue

            rs = ResumeState(ip, item.remote_path)
            resume_offset = rs.load()

            def _cb(sent, total, eff_mbps, _item=item, _rs=rs):
                nonlocal _bytes_sent_total
                self._queue.update_progress(_item, sent)
                _rs.save(sent)
                _bytes_sent_total = sum(i.bytes_sent for i in self._queue.snapshot() if not i.is_dir)
                progress = _bytes_sent_total / max(_total_bytes, 1)
                elapsed = time.monotonic() - _transfer_start
                eta_s = ((_total_bytes - _bytes_sent_total) / max(_bytes_sent_total / elapsed, 1))

                self.after(0, lambda p=progress, s=eff_mbps, e=eta_s: self._update_stats(p, s, e))

                # Pause support
                while self._pause_flag.is_set() and not self._cancel_flag.is_set():
                    time.sleep(0.1)

            try:
                send_file(sock, item.local_path, item.remote_path, throttle,
                          resume_offset=resume_offset, progress_cb=_cb)
                self._queue.mark_done(item)
                rs.clear()
            except (ProtocolError, ConnectionError, OSError) as e:
                self._queue.mark_failed(item, str(e))
                rs.save(item.bytes_sent)
                self.after(0, lambda msg=str(e): self._on_transfer_error(msg))
                break

            self.after(0, self._refresh_queue_list)

        sock.close()
        self.after(0, self._on_transfer_complete)

    def _update_stats(self, progress: float, eff_mbps: float, eta_s: float):
        self._progressbar.set(progress)
        self._lbl_speed.configure(text=f"≈ {eff_mbps:.2f} MB/s effective")
        eta_str = f"{int(eta_s//60)}m {int(eta_s%60)}s" if eta_s > 0 else "—"
        self._lbl_eta.configure(text=f"ETA: {eta_str}")
        self._refresh_queue_list()

    def _on_transfer_complete(self):
        self._lbl_status.configure(text="● Done", text_color=C_GREEN)
        self._btn_send.configure(state="normal")
        self._progressbar.set(1.0)
        self._refresh_queue_list()

    def _on_transfer_error(self, msg: str):
        self._lbl_status.configure(text="● Error — see console", text_color=C_RED)
        self._btn_send.configure(state="normal")
        messagebox.showerror("Transfer Error", msg)

    def _toggle_pause(self):
        if self._pause_flag.is_set():
            self._pause_flag.clear()
            self._btn_pause.configure(text="⏸ Pause")
            self._lbl_status.configure(text="● Transferring...", text_color=C_GREEN)
        else:
            self._pause_flag.set()
            self._btn_pause.configure(text="▶ Resume")
            self._lbl_status.configure(text="● Paused", text_color=C_YELLOW)

    def _cancel_transfer(self):
        self._cancel_flag.set()
        self._lbl_status.configure(text="● Cancelled", text_color=C_RED)
        self._btn_send.configure(state="normal")

    # ── UI polling ─────────────────────────────────────────────────────────────

    def _poll_ui(self):
        """Refresh UI at ~10 fps while a transfer is running."""
        if self._transfer_thread and self._transfer_thread.is_alive():
            self._refresh_queue_list()
        self.after(100, self._poll_ui)

    def on_closing(self):
        self._cancel_flag.set()
        self._listener.stop()
        self.destroy()


def main():
    app = ZelNedApp()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()


if __name__ == "__main__":
    main()
