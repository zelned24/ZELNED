# Zel.NeD 🎮

**High-speed wireless file transfer for Nintendo 3DS / 3DS XL**

Zel.NeD replaces FTP with a custom binary protocol (ZELNED v1) that combines:
- **LZ4 on-the-fly compression** — up to 60% less data over Wi-Fi
- **Automatic 3DS discovery** — no need to type the IP address manually (UDP beacon)
- **CRC32 integrity per chunk** — corrupted blocks are retransmitted automatically
- **Transfer resume** — Wi-Fi dropped at 400 MB? Reconnect and continue from there
- **File queue** — drag multiple files/folders and send them all unattended
- **Bandwidth throttle** — play online while transferring at a configured KB/s limit

---

## Benchmarks (Old 3DS XL, 802.11n 2.4 GHz)

| Method | Real Speed | Effective Speed |
|--------|-----------|----------------|
| FTPd (traditional) | ~800 KB/s | 800 KB/s |
| **Zel.NeD** | ~800 KB/s | **~1.6–2.0 MB/s** (LZ4 ~55% saving) |

---

## Project Structure

```
ZelNeD/
├── pc/                     # PC Client (Python 3.11+)
│   ├── zelned_gui.py       # Modern dark-mode GUI (CustomTkinter)
│   ├── zelned_cli.py       # Command-line interface
│   ├── protocol.py         # ZELNED v1 protocol engine
│   ├── discovery.py        # UDP auto-discovery listener
│   ├── queue_manager.py    # File queue with priorities
│   ├── resume_state.py     # Resume state persistence
│   └── requirements.txt
└── 3ds/                    # 3DS Receiver (.3dsx homebrew)
    ├── Makefile            # devkitARM + libctru + citro2d
    └── source/
        ├── main.cpp
        ├── net_receiver.cpp/hpp   # TCP server, ZELNED protocol
        ├── net_beacon.cpp/hpp     # UDP beacon broadcaster
        ├── integrity.cpp/hpp      # CRC32 (ARM11 optimized)
        ├── fs_writer.cpp/hpp      # Atomic SD writes, path guard
        ├── resume.cpp/hpp         # Resume state on SD card
        ├── ui.cpp/hpp             # Dual-screen citro2d UI
        ├── protocol.hpp           # Shared struct definitions
        └── lz4/                   # Official LZ4 library
```

---

## PC Client — Quick Start

```bash
# Install dependencies
pip install -r pc/requirements.txt

# Open GUI
python pc/zelned_gui.py

# Or use CLI
python pc/zelned_cli.py --discover                          # Find your 3DS on LAN
python pc/zelned_cli.py --ip 192.168.1.47 game.cia          # Send a file
python pc/zelned_cli.py --ip 192.168.1.47 --throttle 400 game.cia  # Limit to 400 KB/s
```

---

## 3DS Receiver — Build

### Requirements
- [devkitPro](https://devkitpro.org/wiki/Getting_Started) with `3ds-dev` package
- `libctru` + `citro2d` (installed via `dkp-pacman -S 3ds-dev`)

```bash
cd 3ds/
make
# Output: ZelNeD.3dsx
```

### Install on 3DS
1. Copy `ZelNeD.3dsx` to `sdmc:/3ds/ZelNeD.3dsx`
2. Launch via Homebrew Launcher (Rosalina menu on Luma3DS)
3. The bottom screen shows the IP address — enter it in the PC client

---

## Protocol — ZELNED v1

```
PC ──UDP broadcast──> 3DS (auto-discovery, port 9504)
PC <──beacon reply─── 3DS (name + IP)
PC ──TCP connect────> 3DS (port 9503)
PC ──session header─> 3DS (flags: LZ4 | CRC32 | RESUME, throttle, queue_count)
   <──ACK / SD_FULL──
For each file:
  PC ──file header──> 3DS (path, size, resume_offset, total_chunks)
     <──ready────────
  For each 64KB chunk:
    PC ──[LZ4 compressed chunk + CRC32]──> 3DS
       <──[ACK: OK / NACK: resend / FS_ERROR]──
  On disconnect: resume state saved on SD (sdmc:/ZelNeD/.resume/)
  On reconnect:  PC sends resume_offset, 3DS skips to that chunk
```

---

## Safety

- **Writes exclusively to `sdmc:/`** — NAND, CTR-NAND, and internal partitions are completely unreachable
- **Path traversal guard** — any path containing `..`, `nand:`, `twln:`, or `bis:` is rejected
- **Atomic writes** — files land as `.zelned_tmp` and are renamed to the final name only after CRC verification
- **Free space check** — before accepting any file, the 3DS verifies there is enough SD space

---

## License

MIT — free to use, modify, and distribute.

---

*Built with ♥ for the Nintendo 3DS homebrew community.*
