"""
Zel.NeD — Pokémon Speed & 60 FPS Booster Engine
=================================================
Automates performance patches, fast-forward/instant-text plugins,
and Rosalina quality-of-life cheats for Pokémon titles on Nintendo 3DS.

Supported Generations:
  - Gen 7: Pokémon Ultra Sun, Ultra Moon, Sun, Moon
  - Gen 6: Pokémon Omega Ruby, Alpha Sapphire, X, Y
  - Gen 4/5: Pokémon Platinum, HeartGold/SoulSilver, Black/White (NDS / TWiLight)

Key Features:
  - No-Outlines IPS Patch generator (60 FPS, eliminates battle lag)
  - CTRPluginFramework 3GX plugin manager (Instant text, run speed x2/x3)
  - Rosalina cheat code generator (sdmc:/cheats/<TitleID>.txt)
  - Direct network installation via ZelNeD protocol to 3DS SD card
"""

import os
import struct
import urllib.request
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Callable

# ── IPS Binary Patch Generator ───────────────────────────────────────────────
def make_ips(patches: List[Tuple[int, bytes]]) -> bytes:
    """
    Constructs a standard binary IPS patch file.
    Format:
      - 5 bytes magic: 'PATCH'
      - Records: 3-byte offset (big-endian), 2-byte length (big-endian), data
      - 3 bytes terminator: 'EOF'
    """
    buf = bytearray(b"PATCH")
    for offset, replacement in patches:
        if offset > 0xFFFFFF:
            raise ValueError(f"IPS offset out of 24-bit range: {hex(offset)}")
        buf.extend(offset.to_bytes(3, byteorder="big"))
        buf.extend(len(replacement).to_bytes(2, byteorder="big"))
        buf.extend(replacement)
    buf.extend(b"EOF")
    return bytes(buf)


# ARM32 NOP instruction in little-endian (0xE320F000)
NOP_ARM = bytes([0x00, 0xF0, 0x20, 0xE3])
ZERO_8  = bytes([0x00] * 8)


# ── Pokémon Catalog & Data Definitions ───────────────────────────────────────
POKEMON_GAMES: Dict[str, dict] = {
    # ── Generation 7 ─────────────────────────────────────────────────────────
    "ultra_sun": {
        "name": "Pokémon Ultra Sol",
        "gen": 7,
        "title_id": "00040000001B5000",
        "badge": "☀️ US",
        "versions": ["v1.2 (Recomendada)", "v1.0"],
        "ips_offsets": {
            "v1.2 (Recomendada)": [(0x0032FBA4, NOP_ARM)],
            "v1.0":               [(0x0032E2B8, NOP_ARM)],
        },
        "plugin_url": "https://github.com/JourneyOver/CTRPF-AR-CHEAT-CODES/releases/download/v1.0/actionreplay.3gx",
        "plugin_file": "alolan_ctrpf.3gx",
        "cheats": """[No Outlines (v1.2 - 60 FPS)]
0042FBA4 E320F000

[No Outlines (v1.0 - 60 FPS)]
0042E2B8 E320F000

[Instant Text Speed]
D3000000 00000000
003D1B5C E1A00000
D2000000 00000000

[Hold R to Run Fast x2]
D3000000 00000000
0039D140 EB01E7E7
DD000000 00000100
0039D140 E1A00000
D0000000 00000000

[Fast Battle Animations]
D3000000 00000000
0054F2A0 E1A00000
D2000000 00000000
"""
    },
    "ultra_moon": {
        "name": "Pokémon Ultra Luna",
        "gen": 7,
        "title_id": "00040000001B5100",
        "badge": "🌙 UM",
        "versions": ["v1.2 (Recomendada)", "v1.0"],
        "ips_offsets": {
            "v1.2 (Recomendada)": [(0x0032FBA8, NOP_ARM)],
            "v1.0":               [(0x0032E2B8, NOP_ARM)],
        },
        "plugin_url": "https://github.com/JourneyOver/CTRPF-AR-CHEAT-CODES/releases/download/v1.0/actionreplay.3gx",
        "plugin_file": "alolan_ctrpf.3gx",
        "cheats": """[No Outlines (v1.2 - 60 FPS)]
0042FBA8 E320F000

[No Outlines (v1.0 - 60 FPS)]
0042E2B8 E320F000

[Instant Text Speed]
D3000000 00000000
003D1B5C E1A00000
D2000000 00000000

[Hold R to Run Fast x2]
D3000000 00000000
0039D140 EB01E7E7
DD000000 00000100
0039D140 E1A00000
D0000000 00000000

[Fast Battle Animations]
D3000000 00000000
0054F2A0 E1A00000
D2000000 00000000
"""
    },
    "sun": {
        "name": "Pokémon Sol",
        "gen": 7,
        "title_id": "0004000000164800",
        "badge": "🦁 S",
        "versions": ["v1.2 (Recomendada)", "v1.0"],
        "ips_offsets": {
            "v1.2 (Recomendada)": [(0x0031CFCC, NOP_ARM)],
            "v1.0":               [(0x0031B748, NOP_ARM)],
        },
        "plugin_url": "https://github.com/JourneyOver/CTRPF-AR-CHEAT-CODES/releases/download/v1.0/actionreplay.3gx",
        "plugin_file": "alolan_ctrpf.3gx",
        "cheats": """[No Outlines (v1.2 - 60 FPS)]
0041CFCC E320F000

[No Outlines (v1.0 - 60 FPS)]
0041B748 E320F000

[Instant Text Speed]
D3000000 00000000
003BEE3C E1A00000
D2000000 00000000

[Hold B to Run Fast]
D3000000 00000000
00394000 EB01E7E7
DD000000 00000002
00394000 E1A00000
D0000000 00000000
"""
    },
    "moon": {
        "name": "Pokémon Luna",
        "gen": 7,
        "title_id": "0004000000175E00",
        "badge": "🦇 M",
        "versions": ["v1.2 (Recomendada)", "v1.0"],
        "ips_offsets": {
            "v1.2 (Recomendada)": [(0x0031CFCC, NOP_ARM)],
            "v1.0":               [(0x0031B748, NOP_ARM)],
        },
        "plugin_url": "https://github.com/JourneyOver/CTRPF-AR-CHEAT-CODES/releases/download/v1.0/actionreplay.3gx",
        "plugin_file": "alolan_ctrpf.3gx",
        "cheats": """[No Outlines (v1.2 - 60 FPS)]
0041CFCC E320F000

[No Outlines (v1.0 - 60 FPS)]
0041B748 E320F000

[Instant Text Speed]
D3000000 00000000
003BEE3C E1A00000
D2000000 00000000
"""
    },

    # ── Generation 6 ─────────────────────────────────────────────────────────
    "omega_ruby": {
        "name": "Pokémon Rubí Omega",
        "gen": 6,
        "title_id": "000400000011C400",
        "badge": "🌋 OR",
        "versions": ["v1.4 (Recomendada)", "v1.0"],
        "ips_offsets": {
            "v1.4 (Recomendada)": [(0x0027A140, ZERO_8)],
            "v1.0":               [(0x00279EB4, ZERO_8)],
        },
        "plugin_url": "https://github.com/biometrix76/Pokemon-Multi-Cheat-Plugin/releases/download/v1.0/plugin.3gx",
        "plugin_file": "gen6_ctrpf.3gx",
        "cheats": """[No Outlines (v1.4 - 60 FPS)]
D3000000 00000000
0037A140 00000000
0037A144 00000000
D2000000 00000000

[No Outlines (v1.0 - 60 FPS)]
D3000000 00000000
00379EB4 00000000
00379EB8 00000000
D2000000 00000000

[Instant Text Speed]
D3000000 00000000
003A4E20 E1A00000
D2000000 00000000

[Fast Movement Speed x2]
D3000000 00000000
00392C18 E1A00000
D2000000 00000000
"""
    },
    "alpha_sapphire": {
        "name": "Pokémon Zafiro Alfa",
        "gen": 6,
        "title_id": "000400000011C500",
        "badge": "🌊 AS",
        "versions": ["v1.4 (Recomendada)", "v1.0"],
        "ips_offsets": {
            "v1.4 (Recomendada)": [(0x0027A140, ZERO_8)],
            "v1.0":               [(0x00279EB4, ZERO_8)],
        },
        "plugin_url": "https://github.com/biometrix76/Pokemon-Multi-Cheat-Plugin/releases/download/v1.0/plugin.3gx",
        "plugin_file": "gen6_ctrpf.3gx",
        "cheats": """[No Outlines (v1.4 - 60 FPS)]
D3000000 00000000
0037A140 00000000
0037A144 00000000
D2000000 00000000

[No Outlines (v1.0 - 60 FPS)]
D3000000 00000000
00379EB4 00000000
00379EB8 00000000
D2000000 00000000

[Instant Text Speed]
D3000000 00000000
003A4E20 E1A00000
D2000000 00000000

[Fast Movement Speed x2]
D3000000 00000000
00392C18 E1A00000
D2000000 00000000
"""
    },
    "x": {
        "name": "Pokémon X",
        "gen": 6,
        "title_id": "0004000000055D00",
        "badge": "🦌 X",
        "versions": ["v1.5 (Recomendada)", "v1.0"],
        "ips_offsets": {
            "v1.5 (Recomendada)": [(0x00262ED8, ZERO_8)],
            "v1.0":               [(0x00261D34, ZERO_8)],
        },
        "plugin_url": "https://github.com/biometrix76/Pokemon-Multi-Cheat-Plugin/releases/download/v1.0/plugin.3gx",
        "plugin_file": "gen6_ctrpf.3gx",
        "cheats": """[No Outlines (v1.5 - 60 FPS)]
D3000000 00000000
00362ED8 00000000
00362EDC 00000000
D2000000 00000000

[No Outlines (v1.0 - 60 FPS)]
D3000000 00000000
00361D34 00000000
00361D38 00000000
D2000000 00000000

[Instant Text Speed]
D3000000 00000000
003A1258 E1A00000
D2000000 00000000
"""
    },
    "y": {
        "name": "Pokémon Y",
        "gen": 6,
        "title_id": "0004000000055E00",
        "badge": "🦅 Y",
        "versions": ["v1.5 (Recomendada)", "v1.0"],
        "ips_offsets": {
            "v1.5 (Recomendada)": [(0x00262ED8, ZERO_8)],
            "v1.0":               [(0x00261D34, ZERO_8)],
        },
        "plugin_url": "https://github.com/biometrix76/Pokemon-Multi-Cheat-Plugin/releases/download/v1.0/plugin.3gx",
        "plugin_file": "gen6_ctrpf.3gx",
        "cheats": """[No Outlines (v1.5 - 60 FPS)]
D3000000 00000000
00362ED8 00000000
00362EDC 00000000
D2000000 00000000

[No Outlines (v1.0 - 60 FPS)]
D3000000 00000000
00361D34 00000000
00361D38 00000000
D2000000 00000000

[Instant Text Speed]
D3000000 00000000
003A1258 E1A00000
D2000000 00000000
"""
    },

    # ── Generation 4 & 5 (NDS / TWiLight Menu++) ─────────────────────────────
    "nds_platinum": {
        "name": "Pokémon Platino (NDS)",
        "gen": 4,
        "title_id": "NDS_PLATINUM",
        "badge": "✨ PL",
        "versions": ["Universal NDS"],
        "ips_offsets": {},
        "plugin_url": "",
        "plugin_file": "",
        "cheats": """[Instant Text Speed (Platinum)]
1205FD3E 000046C0
1205FD56 000046C0

[Fast HP Depletion / Instant Battle Bar]
1207F106 000046C0

[Hold B to Run Anywhere]
1205DAA8 000046C0
"""
    },
    "nds_hgss": {
        "name": "Pokémon HeartGold / SoulSilver (NDS)",
        "gen": 4,
        "title_id": "NDS_HGSS",
        "badge": "💖 HGSS",
        "versions": ["Universal NDS"],
        "ips_offsets": {},
        "plugin_url": "",
        "plugin_file": "",
        "cheats": """[Instant Text Speed (HG/SS)]
1202D304 000046C0
1202D31E 000046C0

[Fast HP Depletion / Instant Battle Bar]
12073A9E 000046C0

[Hold B to Fast Walk / Run]
120268AE 000046C0
"""
    },
    "nds_b2w2": {
        "name": "Pokémon Negro 2 / Blanco 2 (NDS)",
        "gen": 5,
        "title_id": "NDS_B2W2",
        "badge": "⚡ B2W2",
        "versions": ["Universal NDS"],
        "ips_offsets": {},
        "plugin_url": "",
        "plugin_file": "",
        "cheats": """[Instant Text Speed (B2W2)]
12002360 000046C0

[Hold B to Fast Walk / Run]
120281F0 000046C0

[Fast Battle / Instant HP]
1207A2B0 000046C0
"""
    }
}


# ── Cache & File Management ──────────────────────────────────────────────────
CACHE_DIR = Path(__file__).parent / "cache" / "pokemon"


def get_cache_dir() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR


def generate_no_outlines_patch(game_key: str, version: str) -> Optional[Path]:
    """
    Generates a code.ips binary patch file for the given Pokémon title.
    Returns the path to the generated local temporary file, or None if not applicable.
    """
    game = POKEMON_GAMES.get(game_key)
    if not game or not game.get("ips_offsets"):
        return None

    offsets = game["ips_offsets"].get(version)
    if not offsets:
        # Fall back to first available version
        version = list(game["ips_offsets"].keys())[0]
        offsets = game["ips_offsets"][version]

    ips_bytes = make_ips(offsets)
    out_dir = get_cache_dir() / game_key
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "code.ips"
    out_file.write_bytes(ips_bytes)
    return out_file


def generate_cheats_file(game_key: str) -> Optional[Path]:
    """
    Generates a Rosalina-compatible cheat text file (<TitleID>.txt).
    """
    game = POKEMON_GAMES.get(game_key)
    if not game or not game.get("cheats"):
        return None

    out_dir = get_cache_dir() / game_key
    out_dir.mkdir(parents=True, exist_ok=True)
    title_id = game.get("title_id", game_key)
    out_file = out_dir / f"{title_id}.txt"
    out_file.write_text(game["cheats"].strip() + "\n", encoding="utf-8")
    return out_file


def fetch_plugin_file(game_key: str, progress_cb: Optional[Callable[[str], None]] = None) -> Optional[Path]:
    """
    Retrieves or downloads the official .3gx plugin for the game.
    Caches the file locally in pc/cache/pokemon/ so subsequent installs work offline.
    """
    game = POKEMON_GAMES.get(game_key)
    if not game or not game.get("plugin_url"):
        return None

    filename = game.get("plugin_file", "plugin.3gx")
    out_dir = get_cache_dir() / "plugins"
    out_dir.mkdir(parents=True, exist_ok=True)
    local_plugin = out_dir / filename

    if local_plugin.exists() and local_plugin.stat().st_size > 1024:
        if progress_cb:
            progress_cb(f"Usando plugin local desde caché: {filename}")
        return local_plugin

    url = game["plugin_url"]
    if progress_cb:
        progress_cb(f"Descargando plugin oficial desde GitHub ({filename})...")

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ZelNeD-PokemonBooster/2.1"})
        with urllib.request.urlopen(req, timeout=12.0) as resp:
            data = resp.read()
        local_plugin.write_bytes(data)
        if progress_cb:
            progress_cb(f"Plugin guardado en caché ({len(data)} bytes).")
        return local_plugin
    except Exception as e:
        if progress_cb:
            progress_cb(f"Nota: No se pudo descargar plugin automáticamente ({e}).")
        # If offline or rate-limited, create a minimal placeholder/fallback if local_plugin doesn't exist
        if not local_plugin.exists():
            return None
        return local_plugin


def prepare_deployment_queue(game_key: str, version: str,
                             use_no_outlines: bool = True,
                             use_plugin: bool = True,
                             use_cheats: bool = True,
                             status_cb: Optional[Callable[[str], None]] = None) -> List[Tuple[Path, str, str]]:
    """
    Prepares a list of files to send to the 3DS SD card.
    Each item is a tuple: (local_file_path, remote_sd_path, display_label).
    """
    game = POKEMON_GAMES.get(game_key)
    if not game:
        raise ValueError(f"Juego de Pokémon no reconocido: {game_key}")

    title_id = game["title_id"]
    is_nds = game.get("gen") in (4, 5)
    queue: List[Tuple[Path, str, str]] = []

    # 1. No Outlines IPS Patch (3DS games only)
    if use_no_outlines and not is_nds:
        if status_cb:
            status_cb("Generando parche No-Outlines (60 FPS)...")
        ips_file = generate_no_outlines_patch(game_key, version)
        if ips_file and ips_file.exists():
            remote_path = f"luma/titles/{title_id}/code.ips"
            queue.append((ips_file, remote_path, f"⚡ 60 FPS Patch ({game['name']})"))

    # 2. Rosalina Cheats
    if use_cheats:
        if status_cb:
            status_cb("Generando base de datos de trucos para Rosalina...")
        cheats_file = generate_cheats_file(game_key)
        if cheats_file and cheats_file.exists():
            if is_nds:
                remote_path = f"_nds/TWiLightMenu/cheats/{title_id}.txt"
            else:
                remote_path = f"cheats/{title_id}.txt"
            queue.append((cheats_file, remote_path, f"📜 Rosalina Cheats ({game['name']})"))

    # 3. CTRPluginFramework 3GX Plugin (Gen 6 / 7)
    if use_plugin and not is_nds:
        if status_cb:
            status_cb("Verificando plugin CTRPF de aceleración...")
        plugin_file = fetch_plugin_file(game_key, progress_cb=status_cb)
        if plugin_file and plugin_file.exists():
            remote_path = f"luma/plugins/{title_id}/{game.get('plugin_file', 'plugin.3gx')}"
            queue.append((plugin_file, remote_path, f"🚀 CTRPF Turbo Plugin ({game['name']})"))

    return queue
