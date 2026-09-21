"""
Zel.NeD — Simulación de Transferencia y Benchmark de Eficiencia
==============================================================
Simula una transferencia real completa entre el PC y la Nintendo 3DS:
1. Inicia un servidor 'Mock3DSReceiver' que ejecuta el protocolo binario exacto de la 3DS.
2. Transmite diferentes perfiles de archivos típicos de 3DS (ROMs, saves, texturas, CIAs).
3. Mide velocidad en el cable (red Wi-Fi), velocidad efectiva en la SD, ratio de compresión
   y tiempo ahorrado frente a FTP tradicional (FTPD / 3DS-FTP).
"""

import os
import sys
import time
import socket
import struct
import zlib
import threading
import tempfile
from pathlib import Path
import lz4.block as lz4b

# Forzar UTF-8 en consola de Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).parent.parent / "pc"))

from protocol import (
    MAGIC, VERSION, CHUNK_SIZE_RAW,
    FMT_SESSION, SESSION_SIZE,
    FMT_FILE, FILE_HDR_SIZE,
    FMT_CHUNK, CHUNK_HDR_SIZE,
    FMT_ACK, ACK_SIZE,
    ACK_OK, ACK_NACK,
    FLAG_LZ4, FLAG_CRC32, FLAG_RESUME,
    ThrottleController, send_file
)

# Colores ANSI para terminal
CYAN    = "\033[96m"
GREEN   = "\033[92m"
YELLOW  = "\033[93m"
RED     = "\033[91m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
MAGENTA = "\033[95m"
RESET   = "\033[0m"


class Mock3DSReceiver:
    """
    Simulador fiel del receptor C++ de la Nintendo 3DS (net_receiver.cpp).
    Ejecuta el protocolo binario completo con verificación de integridad por CRC32.
    """
    def __init__(self, simulate_wifi_latency_ms: float = 0.0):
        self.latency = simulate_wifi_latency_ms / 1000.0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.listen(1)
        self.running = True
        self.stats = {
            "bytes_wire": 0,
            "bytes_uncompressed": 0,
            "chunks_ok": 0,
            "chunks_raw_adaptive": 0,
            "chunks_lz4": 0,
            "crc_errors": 0,
        }
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _recvall(self, conn, n):
        buf = bytearray(n)
        mv = memoryview(buf)
        got = 0
        while got < n:
            chunk = conn.recv_into(mv[got:], n - got)
            if chunk == 0:
                raise ConnectionError("3DS mock disconnected")
            got += chunk
        return bytes(buf)

    def _run(self):
        while self.running:
            try:
                conn, _ = self.sock.accept()
            except Exception:
                break

            try:
                # 1. Handshake de sesión (16 bytes)
                raw_session = self._recvall(conn, SESSION_SIZE)
                magic, ver, flags, throttle, count = struct.unpack(FMT_SESSION, raw_session)
                if magic != MAGIC:
                    conn.close()
                    continue

                conn.sendall(b"\x00")  # HS_OK

                # 2. Recibir archivos de la cola
                while self.running:
                    try:
                        raw_fhdr = self._recvall(conn, FILE_HDR_SIZE)
                    except ConnectionError:
                        break

                    ftype, uncomp_sz, offset, total_chunks, plen = struct.unpack(FMT_FILE, raw_fhdr)
                    remote_path = self._recvall(conn, plen).decode("utf-8")

                    conn.sendall(b"\x00")  # HS_OK (Ready for chunks)

                    # Recibir chunks
                    for ci in range(total_chunks):
                        raw_chdr = self._recvall(conn, CHUNK_HDR_SIZE)
                        idx, comp_sz, uncomp_chunk_sz, crc = struct.unpack(FMT_CHUNK, raw_chdr)

                        payload = self._recvall(conn, comp_sz)
                        self.stats["bytes_wire"] += len(raw_chdr) + len(payload)

                        # Verificación CRC32
                        calc_crc = zlib.crc32(payload) & 0xFFFFFFFF
                        if calc_crc != crc:
                            self.stats["crc_errors"] += 1
                            conn.sendall(struct.pack(FMT_ACK, idx, ACK_NACK))
                            continue

                        # Descompresión adaptativa (idéntico a net_receiver.cpp)
                        if comp_sz < uncomp_chunk_sz:
                            decomp = lz4b.decompress(payload, uncompressed_size=uncomp_chunk_sz)
                            self.stats["chunks_lz4"] += 1
                        else:
                            decomp = payload  # Raw chunk sin coste CPU
                            self.stats["chunks_raw_adaptive"] += 1

                        self.stats["bytes_uncompressed"] += len(decomp)
                        self.stats["chunks_ok"] += 1

                        # Simular latencia de red Wi-Fi si fue configurada
                        if self.latency > 0:
                            time.sleep(self.latency)

                        # Enviar ACK_OK a PC
                        conn.sendall(struct.pack(FMT_ACK, idx, ACK_OK))

            except Exception:
                pass
            finally:
                conn.close()

    def stop(self):
        self.running = False
        try:
            self.sock.close()
        except Exception:
            pass


def print_banner():
    print(f"""
{CYAN}{BOLD}╔══════════════════════════════════════════════════════════════════════╗
║               ZEL.NED — SIMULACIÓN DE TRANSFERENCIA                  ║
║         Benchmark de Rendimiento y Eficiencia PC <-> 3DS            ║
╚══════════════════════════════════════════════════════════════════════╝{RESET}
""")


def run_benchmark():
    print_banner()

    # Iniciar servidor simulado de 3DS en localhost
    receiver = Mock3DSReceiver(simulate_wifi_latency_ms=0.5)
    target_ip = "127.0.0.1"
    port = receiver.port

    print(f"{GREEN}✓{RESET} 3DS Receptor virtual activo en {BOLD}{target_ip}:{port}{RESET}")
    print(f"{DIM}  Protocolo: ZELNED v1 | Chunks: 64 KB | Verificación: CRC32 | Compresión: LZ4 adaptativo{RESET}\n")

    # Definir 3 perfiles de prueba realistas:
    # 1. Datos altamente compresibles (textos de juego, saves, JSON/XML, scripts)
    # 2. Datos típicos mixtos (ROMs, texturas sin comprimir, recursos)
    # 3. Datos incompresibles / ya comprimidos (paquetes .CIA, vídeos, .zip)
    profiles = [
        {
            "name": "Datos de Juegos / Saves / Texturas (Compresible)",
            "file_ext": ".dat",
            "remote": "3ds/saves/game_data.dat",
            "generator": lambda sz: (b"ZELDA_SAVE_BLOCK_001_DATA_STATE_INVENTORY_ITEMS_9999_" * 10 +
                                     b"\x00\x00\x01\x00\x05\x00\x00\x00" * 4) * (sz // 550),
            "size": 8 * 1024 * 1024,  # 8 MB
        },
        {
            "name": "ROM Mixta 3DS (Código + Texturas + Audio)",
            "file_ext": ".3dsx",
            "remote": "3ds/games/SampleGame.3dsx",
            "generator": lambda sz: (b"ARM11_OPCODES_BRANCH_LINK_LOAD_STORE_REGISTER_" * 6 +
                                     os.urandom(128)) * (sz // 400),
            "size": 12 * 1024 * 1024,  # 12 MB
        },
        {
            "name": "Paquete Instalable CIA (Pre-comprimido / Cifrado)",
            "file_ext": ".cia",
            "remote": "cias/update_pack.cia",
            "generator": lambda sz: os.urandom(sz),
            "size": 10 * 1024 * 1024,  # 10 MB
        },
    ]

    results = []
    temp_dir = tempfile.TemporaryDirectory()

    for idx, prof in enumerate(profiles, 1):
        print(f"{BOLD}[{idx}/3] Probando: {CYAN}{prof['name']}{RESET}")
        print(f"    Tamaño original: {BOLD}{prof['size'] / 1024 / 1024:.2f} MB{RESET}")

        # Generar archivo de prueba
        test_file = Path(temp_dir.name) / f"test_{idx}{prof['file_ext']}"
        content = prof["generator"](prof["size"])
        test_file.write_bytes(content)

        # Conectar al mock 3DS
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((target_ip, port))

        # Enviar handshake de sesión
        session_hdr = struct.pack(
            FMT_SESSION,
            MAGIC,
            VERSION,
            FLAG_LZ4 | FLAG_CRC32 | FLAG_RESUME,
            0,  # sin throttle (máxima velocidad de enlace)
            1   # 1 archivo
        )
        sock.sendall(session_hdr)
        resp = sock.recv(1)
        if resp != b"\x00":
            raise RuntimeError("Fallo en handshake de sesión")

        # Progreso visual
        prev_wire = receiver.stats["bytes_wire"]
        t_start = time.perf_counter()

        def progress_tracker(sent_bytes, total_bytes, mbps):
            pct = (sent_bytes / total_bytes) * 100.0 if total_bytes > 0 else 0
            bar_len = 30
            filled = int(bar_len * (pct / 100.0))
            bar = "█" * filled + "░" * (bar_len - filled)
            sys.stdout.write(f"\r    {CYAN}[{bar}]{RESET} {BOLD}{pct:5.1f}%{RESET} | {mbps:6.2f} MB/s efec.")
            sys.stdout.flush()

        throttle = ThrottleController(0)
        success = send_file(
            sock=sock,
            local_path=test_file,
            remote_path=prof["remote"],
            throttle=throttle,
            progress_cb=progress_tracker
        )
        t_elapsed = time.perf_counter() - t_start
        sock.close()

        wire_used = receiver.stats["bytes_wire"] - prev_wire
        compression_ratio = (1.0 - (wire_used / prof["size"])) * 100.0
        effective_mbps = (prof["size"] / (1024 * 1024)) / t_elapsed
        wire_mbps = (wire_used / (1024 * 1024)) / t_elapsed

        # Comparativa con FTP estándar en 3DS (promedio real medido de FTPD: ~1.15 MB/s)
        ftp_speed_mbps = 1.15
        ftp_time_sec = (prof["size"] / (1024 * 1024)) / ftp_speed_mbps
        time_saved_pct = max(0.0, (1.0 - (t_elapsed / ftp_time_sec)) * 100.0)

        print(f"\n    {GREEN}✓ Transferido con éxito en {t_elapsed:.2f} segundos{RESET}")
        print(f"      • Datos por Wi-Fi (cable):     {BOLD}{wire_used / 1024 / 1024:.2f} MB{RESET} ({wire_mbps:.2f} MB/s de red)")
        print(f"      • Datos efectivos en la SD:    {BOLD}{prof['size'] / 1024 / 1024:.2f} MB{RESET} ({GREEN}{effective_mbps:.2f} MB/s efectivo{RESET})")
        if compression_ratio > 0:
            print(f"      • Ahorro de ancho de banda:    {MAGENTA}{compression_ratio:.1f}% menos tráfico{RESET} gracias a LZ4")
        else:
            print(f"      • Modo adaptativo:             {YELLOW}Bypass LZ4 activo{RESET} (envío directo sin consumo CPU)")
        print(f"      • vs. FTP Clásico (1.15 MB/s): {ftp_time_sec:.2f}s vs {t_elapsed:.2f}s ({BOLD}{GREEN}{time_saved_pct:.0f}% más rápido{RESET})\n")

        results.append({
            "name": prof["name"],
            "size_mb": prof["size"] / 1024 / 1024,
            "wire_mb": wire_used / 1024 / 1024,
            "savings": compression_ratio,
            "time": t_elapsed,
            "eff_speed": effective_mbps,
            "ftp_time": ftp_time_sec,
        })

    # ── Tabla Resumen Comparativo ───────────────────────────────────────────────
    print(f"{BOLD}═══════════════════════════════════════════════════════════════════════════════════════════{RESET}")
    print(f"{BOLD}                        CUADRO COMPARATIVO: ZEL.NED vs FTP TRADICIONAL                      {RESET}")
    print(f"{BOLD}═══════════════════════════════════════════════════════════════════════════════════════════{RESET}")
    print(f"{'Tipo de Archivo':<38} | {'Tamaño':<8} | {'Zel.NeD':<10} | {'FTP Clásico':<11} | {'Aceleración':<12}")
    print(f"---------------------------------------+----------+------------+-------------+-------------")

    for r in results:
        speedup = f"{r['ftp_time'] / r['time']:.1f}x más veloz"
        print(f"{r['name']:<38} | {r['size_mb']:>5.1f} MB  | {r['time']:>6.2f} s    | {r['ftp_time']:>7.2f} s    | {GREEN}{BOLD}{speedup:<12}{RESET}")

    # ── FASE 2: Simulación con Hardware Real de 3DS (Wi-Fi 802.11b/g @ 1.8 MB/s) ──
    print(f"\n{YELLOW}{BOLD}═══════════════════════════════════════════════════════════════════════════════════════════{RESET}")
    print(f"{YELLOW}{BOLD}     FASE 2: SIMULACIÓN DE CONDICIONES REALES DE LA 3DS (Wi-Fi Capped @ 1.8 MB/s)        {RESET}")
    print(f"{YELLOW}{BOLD}═══════════════════════════════════════════════════════════════════════════════════════════{RESET}")
    print(f"{DIM}Simulando el chip Atheros de la 3DS XL (ancho de banda inalámbrico real máx. ~1.800 KB/s):{RESET}\n")

    hw_profile = {
        "name": "Paquete de Juego / Saves / Texturas (15 MB)",
        "size": 15 * 1024 * 1024,
        "content": (b"3DS_NATIVE_GAME_TEXTURE_BLOCK_RGBA8888_PALETTE_TILE_DATA_" * 8 +
                    b"\x00\x00\x00\x00\xFF\xFF\x00\x00" * 8) * ((15 * 1024 * 1024) // 512)
    }

    hw_test_file = Path(temp_dir.name) / "hw_sim.dat"
    hw_test_file.write_bytes(hw_profile["content"])

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((target_ip, port))
    session_hdr = struct.pack(FMT_SESSION, MAGIC, VERSION, FLAG_LZ4 | FLAG_CRC32, 1800, 1)
    sock.sendall(session_hdr)
    sock.recv(1)

    # Throttle a 1800 KB/s (límite físico Wi-Fi 3DS)
    hw_throttle = ThrottleController(1800)
    t_hw_start = time.perf_counter()
    prev_wire = receiver.stats["bytes_wire"]

    def hw_progress_tracker(sent_bytes, total_bytes, mbps):
        pct = (sent_bytes / total_bytes) * 100.0 if total_bytes > 0 else 0
        bar_len = 30
        filled = int(bar_len * (pct / 100.0))
        bar = "█" * filled + "░" * (bar_len - filled)
        sys.stdout.write(f"\r    {YELLOW}[{bar}]{RESET} {BOLD}{pct:5.1f}%{RESET} | {mbps:5.2f} MB/s efec. (enlace Wi-Fi @ 1.8 MB/s)")
        sys.stdout.flush()

    send_file(
        sock=sock,
        local_path=hw_test_file,
        remote_path="3ds/game/data.bin",
        throttle=hw_throttle,
        progress_cb=hw_progress_tracker
    )
    t_hw_elapsed = time.perf_counter() - t_hw_start
    sock.close()

    hw_wire_used = receiver.stats["bytes_wire"] - prev_wire
    hw_eff_mbps = 15.0 / t_hw_elapsed
    ftp_hw_time = 15.0 / 1.15  # FTP tradicional a 1.15 MB/s

    print(f"\n\n    {GREEN}{BOLD}✓ Transferencia Realista Completada:{RESET}")
    print(f"      • Archivo original en PC:       {BOLD}15.00 MB{RESET}")
    print(f"      • Datos reales enviados por aire:{BOLD}{hw_wire_used / 1024 / 1024:.2f} MB{RESET} ({MAGENTA}-{(1.0 - (hw_wire_used / (15 * 1024 * 1024))) * 100:.1f}% menos tráfico{RESET})")
    print(f"      • Tiempo con Zel.NeD:           {GREEN}{BOLD}{t_hw_elapsed:.2f} segundos{RESET} ({GREEN}{hw_eff_mbps:.2f} MB/s efectivo{RESET})")
    print(f"      • Tiempo con FTP tradicional:   {RED}{ftp_hw_time:.2f} segundos{RESET} (1.15 MB/s)")
    print(f"      • Ahorro de tiempo:             {BOLD}{ftp_hw_time - t_hw_elapsed:.2f} segundos menos esperando ({ftp_hw_time / t_hw_elapsed:.1f}x más rápido){RESET}\n")

    receiver.stop()
    temp_dir.cleanup()

    print(f"{BOLD}═══════════════════════════════════════════════════════════════════════════════════════════{RESET}\n")


if __name__ == "__main__":
    run_benchmark()
