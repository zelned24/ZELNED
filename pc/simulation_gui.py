"""
Zel.NeD — Visual Interactive Simulation GUI
===========================================
Interfaz gráfica interactiva para visualizar y experimentar la transferencia
de alta velocidad PC <-> Nintendo 3DS en tiempo real:
- Muestra la pantalla virtual dual de la 3DS (Pantalla Superior e Inferior)
- Comparación en vivo: Zel.NeD vs FTP Clásico
- Pruebas con perfiles de archivo reales (Saves, ROMs, CIAs, Wi-Fi limitado)
- Inyección de fallos de CRC en vivo para ver la auto-recuperación NACK
"""

import sys
import time
import socket
import struct
import zlib
import threading
import tempfile
import os
from pathlib import Path
import customtkinter as ctk
import lz4.block as lz4b

sys.path.insert(0, str(Path(__file__).parent.parent / "pc"))

from protocol import (
    MAGIC, VERSION, CHUNK_SIZE_RAW,
    FMT_SESSION, SESSION_SIZE,
    FMT_FILE, FILE_HDR_SIZE,
    FMT_CHUNK, CHUNK_HDR_SIZE,
    FMT_ACK, ACK_SIZE,
    ACK_OK, ACK_NACK,
    FLAG_LZ4, FLAG_CRC32, FLAG_RESUME,
    ThrottleController
)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class VisualMockReceiver:
    """Receptor virtual que ejecuta el protocolo y notifica a la GUI en tiempo real."""
    def __init__(self, on_chunk_cb, on_log_cb):
        self.on_chunk = on_chunk_cb
        self.on_log = on_log_cb
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.listen(1)
        self.running = True
        self.paused = False
        self.inject_crc_error = False

        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _recvall(self, conn, n):
        buf = bytearray(n)
        mv = memoryview(buf)
        got = 0
        while got < n:
            chunk = conn.recv_into(mv[got:], n - got)
            if chunk == 0:
                raise ConnectionError("Mock 3DS disconnected")
            got += chunk
        return bytes(buf)

    def _serve(self):
        while self.running:
            try:
                conn, addr = self.sock.accept()
            except Exception:
                break

            self.on_log("PC conectado desde " + addr[0])
            try:
                # Handshake
                raw_session = self._recvall(conn, SESSION_SIZE)
                conn.sendall(b"\x00")  # HS_OK
                self.on_log("Handshake de sesión ZELNED v1 OK")

                while self.running:
                    # Esperar mientras esté pausado
                    while self.paused and self.running:
                        time.sleep(0.05)

                    try:
                        raw_fhdr = self._recvall(conn, FILE_HDR_SIZE)
                    except ConnectionError:
                        break

                    ftype, uncomp_sz, offset, total_chunks, plen = struct.unpack(FMT_FILE, raw_fhdr)
                    remote_path = self._recvall(conn, plen).decode("utf-8")
                    conn.sendall(b"\x00")  # HS_OK

                    self.on_log(f"Abriendo: sdmc:/{remote_path}")

                    for ci in range(total_chunks):
                        while self.paused and self.running:
                            time.sleep(0.05)

                        raw_chdr = self._recvall(conn, CHUNK_HDR_SIZE)
                        idx, comp_sz, uncomp_chunk_sz, crc = struct.unpack(FMT_CHUNK, raw_chdr)
                        payload = self._recvall(conn, comp_sz)

                        # Verificación CRC o inyección de fallo simulado
                        calc_crc = zlib.crc32(payload) & 0xFFFFFFFF
                        if self.inject_crc_error:
                            self.inject_crc_error = False
                            calc_crc ^= 0xFFFFFFFF  # forzar fallo
                            self.on_log(f"[WARN] Error CRC provocado en chunk #{idx}! Enviando NACK...")

                        if calc_crc != crc:
                            conn.sendall(struct.pack(FMT_ACK, idx, ACK_NACK))
                            continue

                        # Descompresión adaptativa
                        if comp_sz < uncomp_chunk_sz:
                            decomp = lz4b.decompress(payload, uncompressed_size=uncomp_chunk_sz)
                            is_lz4 = True
                        else:
                            decomp = payload
                            is_lz4 = False

                        # Notificar a la GUI
                        self.on_chunk(idx, total_chunks, comp_sz, len(decomp), is_lz4, remote_path)

                        conn.sendall(struct.pack(FMT_ACK, idx, ACK_OK))

                    self.on_log(f"Archivo completado: sdmc:/{remote_path}")
            except Exception as e:
                self.on_log(f"Conexión finalizada ({e})")
            finally:
                conn.close()

    def stop(self):
        self.running = False
        try:
            self.sock.close()
        except Exception:
            pass


class SimulationApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Zel.NeD — Simulador Interactivo de Transferencia (PC <-> Nintendo 3DS)")
        self.geometry("1020x760")
        self.minsize(980, 720)
        self.configure(fg_color="#0d1117")

        self.transfer_thread = None
        self.is_transferring = False
        self.bytes_written = 0
        self.bytes_wire = 0
        self.t_start = 0

        # Iniciar receptor virtual de 3DS
        self.receiver = VisualMockReceiver(
            on_chunk_cb=self._on_chunk_received,
            on_log_cb=self._on_receiver_log
        )

        self._build_ui()

    def _build_ui(self):
        # ── Encabezado Principal ──────────────────────────────────────────────
        header = ctk.CTkFrame(self, fg_color="#161b22", corner_radius=10)
        header.pack(fill="x", padx=16, pady=(14, 10))

        title_lbl = ctk.CTkLabel(
            header,
            text="⬡ ZEL.NED — SIMULADOR DE TRANSFERENCIA DE ALTA VELOCIDAD",
            font=ctk.CTkFont(family="Consolas", size=17, weight="bold"),
            text_color="#4fc3f7"
        )
        title_lbl.pack(side="left", padx=16, pady=10)

        self.status_badge = ctk.CTkLabel(
            header,
            text="● RECEPTOR 3DS ACTIVO",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#66bb6a"
        )
        self.status_badge.pack(side="right", padx=16, pady=10)

        # ── Contenedor de 2 Columnas (Izquierda: Control / Derecha: Pantalla 3DS) ─
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=16, pady=0)

        # ── Columna Izquierda: Controles y Comparativa ────────────────────────
        left_col = ctk.CTkFrame(content, fg_color="#161b22", corner_radius=12, width=440)
        left_col.pack(side="left", fill="both", expand=True, padx=(0, 8), pady=4)
        left_col.pack_propagate(False)

        ctk.CTkLabel(
            left_col,
            text="📦 Configuración de Simulación (PC Sender)",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#e0e0e0"
        ).pack(anchor="w", padx=14, pady=(12, 6))

        # Selector de perfil
        ctk.CTkLabel(left_col, text="Selecciona tipo de archivo a transferir:", text_color="#a0a0b0", font=ctk.CTkFont(size=11)).pack(anchor="w", padx=14, pady=(2, 2))
        self.profile_combo = ctk.CTkComboBox(
            left_col,
            values=[
                "Saves y Texturas (Compresible, 10 MB)",
                "ROM Mixta 3DS (Código + Assets, 15 MB)",
                "Paquete CIA Instalable (Cifrado, 12 MB)",
                "Wi-Fi 3DS Real (Enlace Capped @ 1.8 MB/s, 20 MB)",
            ],
            width=380,
            font=ctk.CTkFont(size=12),
            dropdown_font=ctk.CTkFont(size=12)
        )
        self.profile_combo.set("Saves y Texturas (Compresible, 10 MB)")
        self.profile_combo.pack(padx=14, pady=(0, 10))

        # Botones de Acción
        btn_frame = ctk.CTkFrame(left_col, fg_color="transparent")
        btn_frame.pack(fill="x", padx=14, pady=4)

        self.btn_start = ctk.CTkButton(
            btn_frame,
            text="▶ Iniciar Transferencia",
            command=self.start_simulation,
            fg_color="#0288d1",
            hover_color="#0277bd",
            font=ctk.CTkFont(weight="bold", size=13),
            height=34
        )
        self.btn_start.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self.btn_pause = ctk.CTkButton(
            btn_frame,
            text="⏸ Pausar (SELECT)",
            command=self.toggle_pause,
            fg_color="#37474f",
            hover_color="#455a64",
            font=ctk.CTkFont(size=12),
            height=34,
            width=130
        )
        self.btn_pause.pack(side="left", padx=4)

        self.btn_inject_crc = ctk.CTkButton(
            left_col,
            text="⚡ Inyectar Error CRC (Probar reintento NACK en vivo)",
            command=self.inject_crc_error,
            fg_color="#b71c1c",
            hover_color="#c62828",
            font=ctk.CTkFont(size=11),
            height=28
        )
        self.btn_inject_crc.pack(fill="x", padx=14, pady=6)

        # ── Comparativa en Vivo (Zel.NeD vs FTP) ──────────────────────────────
        comp_frame = ctk.CTkFrame(left_col, fg_color="#0d1117", corner_radius=10)
        comp_frame.pack(fill="both", expand=True, padx=14, pady=10)

        ctk.CTkLabel(
            comp_frame,
            text="⚡ Rendimiento en Vivo vs. FTP Clásico",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#66bb6a"
        ).pack(anchor="w", padx=12, pady=(10, 4))

        # Barra Zel.NeD
        ctk.CTkLabel(comp_frame, text="Zel.NeD (Streaming LZ4 + Pipeline):", font=ctk.CTkFont(size=11), text_color="#4fc3f7").pack(anchor="w", padx=12, pady=(4, 0))
        self.bar_zelned = ctk.CTkProgressBar(comp_frame, height=12, progress_color="#4fc3f7")
        self.bar_zelned.set(0)
        self.bar_zelned.pack(fill="x", padx=12, pady=(2, 2))
        self.lbl_zelned_stat = ctk.CTkLabel(comp_frame, text="0.00 s  |  0.0 MB/s efectivo", font=ctk.CTkFont(size=11, weight="bold"), text_color="#e0e0e0")
        self.lbl_zelned_stat.pack(anchor="w", padx=12)

        # Barra FTP
        ctk.CTkLabel(comp_frame, text="FTP Tradicional (FTPD / 3DS-FTP @ 1.15 MB/s):", font=ctk.CTkFont(size=11), text_color="#ef5350").pack(anchor="w", padx=12, pady=(8, 0))
        self.bar_ftp = ctk.CTkProgressBar(comp_frame, height=12, progress_color="#ef5350")
        self.bar_ftp.set(0)
        self.bar_ftp.pack(fill="x", padx=12, pady=(2, 2))
        self.lbl_ftp_stat = ctk.CTkLabel(comp_frame, text="0.00 s  |  1.15 MB/s", font=ctk.CTkFont(size=11, weight="bold"), text_color="#e0e0e0")
        self.lbl_ftp_stat.pack(anchor="w", padx=12)

        # Multiplicador Badge
        self.speedup_badge = ctk.CTkLabel(
            comp_frame,
            text="Esperando inicio de prueba...",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#ffca28"
        )
        self.speedup_badge.pack(pady=12)

        # ── Columna Derecha: Pantalla Dual Virtual de la 3DS ───────────────────
        right_col = ctk.CTkFrame(content, fg_color="#161b22", corner_radius=12)
        right_col.pack(side="right", fill="both", expand=True, padx=(8, 0), pady=4)

        ctk.CTkLabel(
            right_col,
            text="🎮 Pantalla Dual Virtual de la Nintendo 3DS XL",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#e0e0e0"
        ).pack(anchor="w", padx=14, pady=(12, 6))

        # ── Pantalla Superior 3DS (400x240 simulada) ──────────────────────────
        top_screen = ctk.CTkFrame(right_col, fg_color="#1a1a2e", corner_radius=8, border_width=2, border_color="#30363d")
        top_screen.pack(fill="x", padx=14, pady=6)

        top_hdr = ctk.CTkFrame(top_screen, fg_color="#16213e", height=26)
        top_hdr.pack(fill="x")
        ctk.CTkLabel(top_hdr, text="⬡ Zel.NeD v1.0", font=ctk.CTkFont(size=12, weight="bold"), text_color="#4fc3f7").pack(side="left", padx=8)
        self.top_live_badge = ctk.CTkLabel(top_hdr, text="○ Idle", font=ctk.CTkFont(size=11), text_color="#777788")
        self.top_live_badge.pack(side="right", padx=8)

        self.top_file_lbl = ctk.CTkLabel(top_screen, text="Archivo:  En espera...", font=ctk.CTkFont(size=12), text_color="#ffffff", anchor="w")
        self.top_file_lbl.pack(fill="x", padx=10, pady=(8, 2))

        self.top_progress_bar = ctk.CTkProgressBar(top_screen, height=14, progress_color="#4fc3f7")
        self.top_progress_bar.set(0)
        self.top_progress_bar.pack(fill="x", padx=10, pady=4)

        self.top_pct_lbl = ctk.CTkLabel(top_screen, text="0.0%  (0 / 0 chunks)", font=ctk.CTkFont(size=11), text_color="#4fc3f7", anchor="w")
        self.top_pct_lbl.pack(fill="x", padx=10)

        stats_grid = ctk.CTkFrame(top_screen, fg_color="transparent")
        stats_grid.pack(fill="x", padx=10, pady=6)

        self.lbl_net_speed = ctk.CTkLabel(stats_grid, text="Red Wi-Fi: 0 KB/s", font=ctk.CTkFont(size=11), text_color="#ffffff")
        self.lbl_net_speed.pack(side="left", padx=(0, 15))

        self.lbl_eff_speed = ctk.CTkLabel(stats_grid, text="Efectivo: 0.00 MB/s", font=ctk.CTkFont(size=11, weight="bold"), text_color="#66bb6a")
        self.lbl_eff_speed.pack(side="left", padx=(0, 15))

        self.lbl_lz4_saving = ctk.CTkLabel(stats_grid, text="Ahorro LZ4: 0%", font=ctk.CTkFont(size=11), text_color="#ffca28")
        self.lbl_lz4_saving.pack(side="left")

        # ── Pantalla Inferior 3DS (Touchscreen 320x240 simulada) ──────────────
        bot_screen = ctk.CTkFrame(right_col, fg_color="#1a1a2e", corner_radius=8, border_width=2, border_color="#30363d")
        bot_screen.pack(fill="both", expand=True, padx=14, pady=(6, 12))

        bot_info = ctk.CTkFrame(bot_screen, fg_color="#16213e", height=24)
        bot_info.pack(fill="x")
        ctk.CTkLabel(bot_info, text="IP: 192.168.1.105:9503   |   Wi-Fi: ▂▄▆█ Excelente", font=ctk.CTkFont(size=11), text_color="#66bb6a").pack(side="left", padx=8)

        ctk.CTkLabel(bot_screen, text="Registro en vivo de la 3DS (Bottom Screen Log):", font=ctk.CTkFont(size=11, weight="bold"), text_color="#4fc3f7", anchor="w").pack(fill="x", padx=10, pady=(6, 2))

        self.log_box = ctk.CTkTextbox(
            bot_screen,
            fg_color="#0f111a",
            text_color="#b0bec5",
            font=ctk.CTkFont(family="Consolas", size=10),
            corner_radius=6
        )
        self.log_box.pack(fill="both", expand=True, padx=10, pady=(0, 8))

    def _on_receiver_log(self, msg: str):
        def _update():
            timestamp = time.strftime("%H:%M:%S")
            self.log_box.insert("end", f"[{timestamp}] {msg}\n")
            self.log_box.see("end")
        self.after(0, _update)

    def _on_chunk_received(self, chunk_idx, total_chunks, comp_sz, decomp_sz, is_lz4, fname):
        self.bytes_wire += comp_sz
        self.bytes_written += decomp_sz
        pct = (chunk_idx + 1) / total_chunks if total_chunks > 0 else 0

        elapsed = max(time.perf_counter() - self.t_start, 0.001)
        eff_mbps = (self.bytes_written / (1024 * 1024)) / elapsed
        net_kbps = (self.bytes_wire / 1024) / elapsed
        saving_pct = max(0.0, (1.0 - (self.bytes_wire / max(self.bytes_written, 1))) * 100.0)

        def _update():
            self.top_progress_bar.set(pct)
            self.bar_zelned.set(pct)
            self.top_file_lbl.configure(text=f"Archivo:  {fname}")
            self.top_pct_lbl.configure(text=f"{pct*100:.1f}%  ({chunk_idx+1} / {total_chunks} chunks)")
            self.lbl_net_speed.configure(text=f"Red Wi-Fi: {net_kbps:.0f} KB/s")
            self.lbl_eff_speed.configure(text=f"Efectivo: {eff_mbps:.2f} MB/s")
            self.lbl_zelned_stat.configure(text=f"{elapsed:.2f} s  |  {eff_mbps:.2f} MB/s efectivo")

            if is_lz4:
                self.lbl_lz4_saving.configure(text=f"Ahorro LZ4: {saving_pct:.0f}%", text_color="#ffca28")
            else:
                self.lbl_lz4_saving.configure(text="Bypass LZ4 (Raw)", text_color="#66bb6a")

        self.after(0, _update)

    def toggle_pause(self):
        self.receiver.paused = not self.receiver.paused
        if self.receiver.paused:
            self.btn_pause.configure(text="▶ Reanudar", fg_color="#f57f17")
            self.top_live_badge.configure(text="⏸ PAUSED", text_color="#ffca28")
            self._on_receiver_log("[PAUSE] Transferencia pausada por usuario")
        else:
            self.btn_pause.configure(text="⏸ Pausar (SELECT)", fg_color="#37474f")
            self.top_live_badge.configure(text="● LIVE", text_color="#66bb6a")
            self._on_receiver_log("[RESUME] Transferencia reanudada")

    def inject_crc_error(self):
        self.receiver.inject_crc_error = True
        self._on_receiver_log("[INJECT] Se inyectará un bit corrupto en el próximo chunk")

    def start_simulation(self):
        if self.is_transferring:
            return

        choice = self.profile_combo.get()
        self.is_transferring = True
        self.btn_start.configure(state="disabled")
        self.top_live_badge.configure(text="● LIVE", text_color="#66bb6a")
        self.bytes_written = 0
        self.bytes_wire = 0
        self.t_start = time.perf_counter()

        # Determinar configuración de prueba
        if "Saves" in choice:
            sz = 10 * 1024 * 1024
            ext = ".dat"
            remote = "3ds/saves/game_data.dat"
            data = (b"SAVE_SLOT_01_LEVEL_99_STATS_HP_MP_EXP_GOLD_INVENTORY_" * 12) * (sz // 600)
            throttle_kbps = 0
        elif "ROM" in choice:
            sz = 15 * 1024 * 1024
            ext = ".3dsx"
            remote = "3ds/SampleGame.3dsx"
            data = (b"ARM11_CODE_SECTION_RENDER_AUDIO_VIDEO_TEXTURE_PALETTE_" * 8 + os.urandom(64)) * (sz // 450)
            throttle_kbps = 0
        elif "CIA" in choice:
            sz = 12 * 1024 * 1024
            ext = ".cia"
            remote = "cias/game_update.cia"
            data = os.urandom(sz)
            throttle_kbps = 0
        else:  # Wi-Fi Real Capped
            sz = 20 * 1024 * 1024
            ext = ".dat"
            remote = "3ds/game/data.bin"
            data = (b"TEXTURE_RGBA8888_TILE_BLOCK_PALETTE_STREAM_" * 10) * (sz // 420)
            throttle_kbps = 1800  # Capped a 1.8 MB/s Wi-Fi real 3DS

        # Calcular tiempo teórico de FTP tradicional (@ 1.15 MB/s real)
        ftp_expected_time = (sz / (1024 * 1024)) / 1.15
        self.lbl_ftp_stat.configure(text=f"{ftp_expected_time:.2f} s  |  1.15 MB/s")

        # Iniciar hilo de transferencia
        def _worker():
            temp_f = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
            temp_f.write(data)
            temp_f.close()

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(("127.0.0.1", self.receiver.port))

            session_hdr = struct.pack(FMT_SESSION, MAGIC, VERSION, FLAG_LZ4 | FLAG_CRC32, throttle_kbps, 1)
            sock.sendall(session_hdr)
            sock.recv(1)

            file_hdr = struct.pack(FMT_FILE, 0, len(data), 0, (len(data) + CHUNK_SIZE_RAW - 1) // CHUNK_SIZE_RAW, len(remote.encode("utf-8")))
            sock.sendall(file_hdr)
            sock.sendall(remote.encode("utf-8"))
            sock.recv(1)

            throttle = ThrottleController(throttle_kbps)
            chunk_idx = 0
            with open(temp_f.name, "rb") as f:
                while True:
                    while self.receiver.paused:
                        time.sleep(0.05)

                    raw = f.read(CHUNK_SIZE_RAW)
                    if not raw:
                        break

                    uncomp_sz = len(raw)
                    comp = lz4b.compress(raw, store_size=False)
                    if len(comp) >= uncomp_sz * 0.98:
                        comp = raw  # bypass adaptativo

                    crc = zlib.crc32(comp) & 0xFFFFFFFF
                    chdr = struct.pack(FMT_CHUNK, chunk_idx, len(comp), uncomp_sz, crc)

                    while True:
                        throttle.consume(len(chdr) + len(comp))
                        sock.sendall(chdr)
                        sock.sendall(comp)
                        ack_raw = sock.recv(ACK_SIZE)
                        a_idx, a_stat = struct.unpack(FMT_ACK, ack_raw)
                        if a_stat == ACK_OK:
                            break
                        # NACK recibido -> reintentar mismo chunk

                    chunk_idx += 1

            sock.close()
            os.unlink(temp_f.name)

            t_total = max(time.perf_counter() - self.t_start, 0.001)
            speedup = ftp_expected_time / t_total

            def _done():
                self.is_transferring = False
                self.btn_start.configure(state="normal")
                self.top_live_badge.configure(text="✓ DONE", text_color="#66bb6a")
                self.bar_ftp.set(min(t_total / ftp_expected_time, 1.0))
                self.speedup_badge.configure(
                    text=f"🚀 ¡Zel.NeD completó {speedup:.1f}x MÁS RÁPIDO que FTP!\n"
                         f"Ahorro de tiempo: {ftp_expected_time - t_total:.1f} segundos",
                    text_color="#66bb6a"
                )

            self.after(0, _done)

        self.transfer_thread = threading.Thread(target=_worker, daemon=True)
        self.transfer_thread.start()


if __name__ == "__main__":
    app = SimulationApp()
    app.mainloop()
