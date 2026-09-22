import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "pc"))

from pokemon_booster import (
    make_ips, POKEMON_GAMES,
    generate_no_outlines_patch, generate_cheats_file,
    prepare_deployment_queue
)


class TestPokemonBooster(unittest.TestCase):

    def test_make_ips_structure(self):
        # 3-byte offset 0x123456, 4 bytes data
        patch = make_ips([(0x123456, b"\x00\xF0\x20\xE3")])
        self.assertTrue(patch.startswith(b"PATCH"))
        self.assertTrue(patch.endswith(b"EOF"))
        # Header (5) + Offset(3) + Len(2) + Data(4) + EOF(3) = 17 bytes
        self.assertEqual(len(patch), 17)
        self.assertEqual(patch[5:8], bytes([0x12, 0x34, 0x56]))
        self.assertEqual(patch[8:10], bytes([0x00, 0x04]))
        self.assertEqual(patch[10:14], b"\x00\xF0\x20\xE3")

    def test_pokemon_catalog_validity(self):
        self.assertIn("ultra_sun", POKEMON_GAMES)
        self.assertIn("omega_ruby", POKEMON_GAMES)
        self.assertIn("x", POKEMON_GAMES)
        self.assertIn("nds_platinum", POKEMON_GAMES)

        for key, game in POKEMON_GAMES.items():
            self.assertIn("name", game)
            self.assertIn("gen", game)
            self.assertIn("title_id", game)
            self.assertIn("badge", game)
            self.assertIn("cheats", game)

    def test_generate_no_outlines_patch_ultra_sun(self):
        patch_path = generate_no_outlines_patch("ultra_sun", "v1.2 (Recomendada)")
        self.assertIsNotNone(patch_path)
        self.assertTrue(patch_path.exists())
        data = patch_path.read_bytes()
        self.assertTrue(data.startswith(b"PATCH"))
        self.assertTrue(data.endswith(b"EOF"))
        self.assertGreater(len(data), 8)

    def test_generate_no_outlines_patch_omega_ruby(self):
        patch_path = generate_no_outlines_patch("omega_ruby", "v1.4 (Recomendada)")
        self.assertIsNotNone(patch_path)
        self.assertTrue(patch_path.exists())
        data = patch_path.read_bytes()
        self.assertTrue(data.startswith(b"PATCH"))
        self.assertTrue(data.endswith(b"EOF"))

    def test_generate_cheats_file(self):
        cheat_path = generate_cheats_file("ultra_sun")
        self.assertIsNotNone(cheat_path)
        self.assertTrue(cheat_path.exists())
        content = cheat_path.read_text(encoding="utf-8")
        self.assertIn("[No Outlines", content)
        self.assertIn("[Instant Text Speed]", content)

    def test_prepare_deployment_queue(self):
        queue = prepare_deployment_queue(
            "ultra_sun", "v1.2 (Recomendada)",
            use_no_outlines=True, use_plugin=False, use_cheats=True
        )
        self.assertEqual(len(queue), 2)
        local1, remote1, label1 = queue[0]
        self.assertEqual(remote1, "luma/titles/00040000001B5000/code.ips")
        self.assertIn("60 FPS", label1)

        local2, remote2, label2 = queue[1]
        self.assertEqual(remote2, "cheats/00040000001B5000.txt")
        self.assertIn("Cheats", label2)


if __name__ == "__main__":
    unittest.main()
