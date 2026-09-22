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
    ACK_OK, ACK_NACK, ACK_ERROR_FS,
    FLAG_LZ4, FLAG_CRC32, FLAG_RESUME,
    ThrottleController
)
from resume_state import ResumeState, _session_id
from queue_manager import QueueManager, QueueItem, ItemStatus


class TestZelNedProtocol(unittest.TestCase):

    def test_session_header_pack_unpack(self):
        packed = struct.pack(FMT_SESSION, MAGIC, VERSION, FLAG_LZ4 | FLAG_CRC32, 2048, 5)
        self.assertEqual(len(packed), SESSION_SIZE)
        self.assertEqual(SESSION_SIZE, 16)

        magic, ver, flags, throttle, count = struct.unpack(FMT_SESSION, packed)
        self.assertEqual(magic, b"ZELNED")
        self.assertEqual(ver, 1)
        self.assertEqual(flags, FLAG_LZ4 | FLAG_CRC32)
        self.assertEqual(throttle, 2048)
        self.assertEqual(count, 5)

    def test_file_header_pack_unpack(self):
        # type(1) + uncompressed_size(8) + resume_offset(8) + total_chunks(4) + path_len(2) = 23 bytes
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

    def test_adaptive_compression_logic(self):
        # Test compressible payload
        text = b"Repeated pattern" * 4000
        comp = lz4b.compress(text, store_size=False)
        self.assertLess(len(comp), len(text) * 0.98)

        # Test random uncompressible payload
        import os
        rand = os.urandom(65536)
        comp_rand = lz4b.compress(rand, store_size=False)
        # Should be bypassed by 98% threshold
        is_savings_low = len(comp_rand) >= len(rand) * 0.98
        self.assertTrue(is_savings_low)

    def test_throttle_controller(self):
        # 100 KB/s throttle -> consuming 50 KB should take ~0.4 - 0.6s on second consume
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
