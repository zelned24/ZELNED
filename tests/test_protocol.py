import unittest
import struct
import zlib
import time
import tempfile
from pathlib import Path
import lz4.block as lz4b

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "pc"))

from protocol import (
    MAGIC, VERSION, TCP_PORT, CHUNK_SIZE_RAW,
    FMT_SESSION, SESSION_SIZE,
    FMT_FILE, FILE_HDR_SIZE,
    FMT_CHUNK, CHUNK_HDR_SIZE,
    FMT_ACK, ACK_SIZE,
    FMT_TELEM_ACK, TELEM_ACK_SIZE,
    ACK_OK, ACK_NACK, ACK_ERROR_FS, ACK_WITH_TELEMETRY,
    FLAG_LZ4, FLAG_CRC32, FLAG_RESUME, FLAG_TELEMETRY, FLAG_BENCHMARK_NET,
    ThrottleController
)
from analyzer import BottleneckAnalyzer, Bottleneck
from auto_tuner import AutoTuner, TransferParams
from resume_state import ResumeState, _session_id
from queue_manager import QueueManager, QueueItem, ItemStatus


class TestZelNedProtocol(unittest.TestCase):

    def test_session_header_pack_unpack(self):
        packed = struct.pack(FMT_SESSION, MAGIC, VERSION, FLAG_LZ4 | FLAG_CRC32 | FLAG_TELEMETRY, 2048, 5)
        self.assertEqual(len(packed), SESSION_SIZE)
        self.assertEqual(SESSION_SIZE, 16)

        magic, ver, flags, throttle, count = struct.unpack(FMT_SESSION, packed)
        self.assertEqual(magic, b"ZELNED")
        self.assertEqual(ver, 1)
        self.assertEqual(flags, FLAG_LZ4 | FLAG_CRC32 | FLAG_TELEMETRY)
        self.assertEqual(throttle, 2048)
        self.assertEqual(count, 5)

    def test_file_header_pack_unpack(self):
        packed = struct.pack(FMT_FILE, 0, 10485760, 65536, 160, 18)
        self.assertEqual(len(packed), FILE_HDR_SIZE)
        self.assertEqual(FILE_HDR_SIZE, 23)

        ftype, uncomp_sz, offset, chunks, plen = struct.unpack(FMT_FILE, packed)
        self.assertEqual(ftype, 0)
        self.assertEqual(uncomp_sz, 10485760)
        self.assertEqual(offset, 65536)
        self.assertEqual(chunks, 160)
        self.assertEqual(plen, 18)

    def test_chunk_header_and_crc(self):
        data = b"Hello, 3DS! " * 5000  # compressible data
        comp = lz4b.compress(data, store_size=False)
        crc = zlib.crc32(comp) & 0xFFFFFFFF

        hdr = struct.pack(FMT_CHUNK, 0, len(comp), len(data), crc)
        self.assertEqual(len(hdr), CHUNK_HDR_SIZE)
        self.assertEqual(CHUNK_HDR_SIZE, 16)

        idx, comp_sz, uncomp_sz, read_crc = struct.unpack(FMT_CHUNK, hdr)
        self.assertEqual(idx, 0)
        self.assertEqual(comp_sz, len(comp))
        self.assertEqual(uncomp_sz, len(data))
        self.assertEqual(read_crc, crc)

    def test_telemetry_ack_struct(self):
        # Format: <IBHHHBBH (chunk_idx, status, t_net, t_cpu, t_sd, wifi_bars, cpu_pct, reserved)
        self.assertEqual(TELEM_ACK_SIZE, 15)
        raw = struct.pack(FMT_TELEM_ACK, 42, ACK_WITH_TELEMETRY, 5000, 1200, 3400, 3, 25, 0)
        self.assertEqual(len(raw), 15)

        idx, status, t_net, t_cpu, t_sd, bars, cpu_pct, res = struct.unpack(FMT_TELEM_ACK, raw)
        self.assertEqual(idx, 42)
        self.assertEqual(status, ACK_WITH_TELEMETRY)
        self.assertEqual(t_net, 5000)
        self.assertEqual(t_cpu, 1200)
        self.assertEqual(t_sd, 3400)
        self.assertEqual(bars, 3)
        self.assertEqual(cpu_pct, 25)
        self.assertEqual(res, 0)

    def test_protocol_flag_constants(self):
        self.assertEqual(FLAG_TELEMETRY, 0x08)
        self.assertEqual(FLAG_BENCHMARK_NET, 0x10)
        self.assertEqual(ACK_WITH_TELEMETRY, 4)

    def test_adaptive_compression_logic(self):
        # Test compressible payload
        text = b"Repeated pattern" * 4000
        comp = lz4b.compress(text, store_size=False)
        self.assertLess(len(comp), len(text) * 0.98)

        # Test random uncompressible payload
        import os
        rand = os.urandom(65536)
        comp_rand = lz4b.compress(rand, store_size=False)
        is_savings_low = len(comp_rand) >= len(rand) * 0.98
        self.assertTrue(is_savings_low)

    def test_throttle_controller(self):
        throttle = ThrottleController(kbps=100)
        t0 = time.monotonic()
        throttle.consume(100 * 1024)
        throttle.consume(50 * 1024)
        elapsed = time.monotonic() - t0
        self.assertGreaterEqual(elapsed, 0.4)

    def test_resume_state_lifecycle(self):
        rs = ResumeState("192.168.1.50", "cia/test.cia")
        rs.clear()
        self.assertEqual(rs.load(), 0)

        rs.save(524288)
        self.assertEqual(rs.load(), 524288)

        rs.clear()
        self.assertEqual(rs.load(), 0)


class TestBottleneckAnalyzer(unittest.TestCase):

    def test_network_bottleneck_detection(self):
        analyzer = BottleneckAnalyzer(window=20)
        for _ in range(15):
            analyzer.feed(t_net_us=8000, t_cpu_us=500, t_sd_us=1000, wifi_bars=1, cpu_load_pct=5)
        res = analyzer.classify()
        self.assertEqual(res.bottleneck, Bottleneck.NETWORK)
        self.assertIn("Wi-Fi", res.recommendation())

    def test_cpu_bottleneck_detection(self):
        analyzer = BottleneckAnalyzer(window=20)
        for _ in range(15):
            analyzer.feed(t_net_us=1000, t_cpu_us=7000, t_sd_us=1000, wifi_bars=3, cpu_load_pct=78)
        res = analyzer.classify()
        self.assertEqual(res.bottleneck, Bottleneck.CPU)
        self.assertIn("CPU", res.recommendation())

    def test_sd_bottleneck_detection(self):
        analyzer = BottleneckAnalyzer(window=20)
        for _ in range(15):
            analyzer.feed(t_net_us=1000, t_cpu_us=500, t_sd_us=8000, wifi_bars=3, cpu_load_pct=5)
        res = analyzer.classify()
        self.assertEqual(res.bottleneck, Bottleneck.SD_CARD)
        self.assertIn("SD", res.recommendation())

    def test_balanced_profile(self):
        analyzer = BottleneckAnalyzer(window=20)
        for _ in range(15):
            analyzer.feed(t_net_us=3000, t_cpu_us=2800, t_sd_us=3200, wifi_bars=3, cpu_load_pct=30)
        res = analyzer.classify()
        self.assertEqual(res.bottleneck, Bottleneck.BALANCED)


class TestAutoTuner(unittest.TestCase):

    def test_cpu_bottleneck_disables_lz4(self):
        tuner = AutoTuner()
        # Feed 25 chunks of heavy CPU load
        for _ in range(25):
            tuner.feed_telemetry(t_net_us=500, t_cpu_us=8000, t_sd_us=500, wifi_bars=3, cpu_load_pct=89, nack_count=0)
        params = tuner.get_params()
        self.assertFalse(params.use_lz4)

    def test_cooldown_and_history(self):
        tuner = AutoTuner()
        for _ in range(25):
            tuner.feed_telemetry(t_net_us=8000, t_cpu_us=500, t_sd_us=500, wifi_bars=1, cpu_load_pct=5)
        history = tuner.get_history()
        self.assertGreater(len(history), 0)


class TestQueueManager(unittest.TestCase):

    def test_queue_flow(self):
        qm = QueueManager()
        self.assertEqual(len(qm.snapshot()), 0)

        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"content" * 100)
            f_path = Path(f.name)

        try:
            count = qm.add_path(f_path, "test")
            self.assertEqual(count, 1)
            items = qm.snapshot()
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].status, ItemStatus.PENDING)

            next_item = qm.next_pending()
            self.assertIsNotNone(next_item)

            qm.mark_done(next_item)
            self.assertIsNone(qm.next_pending())
            total, done, bytes_total = qm.total_stats()
            self.assertEqual(done, 1)
            self.assertEqual(total, 1)
        finally:
            f_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
