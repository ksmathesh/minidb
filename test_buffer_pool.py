import os
import tempfile
import unittest
from disk_manager import DiskManager
from buffer_pool_manager import BufferPoolManager, LRUReplacer, ClockReplacer

class TestBufferPool(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test.db")
        self.disk_manager = DiskManager(self.db_path)

    def tearDown(self):
        self.disk_manager.close()
        self.temp_dir.cleanup()

    def test_disk_manager_rejects_partial_page_files_and_unallocated_reads(self):
        partial_path = os.path.join(self.temp_dir.name, "partial.db")
        with open(partial_path, "wb") as file:
            file.write(b"x" * 17)
        with self.assertRaisesRegex(ValueError, "multiple"):
            DiskManager(partial_path)

        page_id = self.disk_manager.allocate_page()
        with self.assertRaises(IndexError):
            self.disk_manager.read_page(page_id + 1)
        with self.assertRaises(IndexError):
            self.disk_manager.write_page(page_id + 2, b"x" * 4096)

    def test_disk_manager_read_write(self):
        page_id = self.disk_manager.allocate_page()
        test_data = b"X" * 4096
        self.disk_manager.write_page(page_id, test_data)

        read_back = self.disk_manager.read_page(page_id)
        self.assertEqual(read_back, test_data)

    def test_lru_replacer(self):
        lru = LRUReplacer(pool_size=3)
        lru.unpin(0)
        lru.unpin(1)
        lru.unpin(2)

        # Evict least recently used (0)
        self.assertEqual(lru.victim(), 0)
        # Pin 1 so it's not evicted
        lru.pin(1)
        # Next victim is 2
        self.assertEqual(lru.victim(), 2)

    def test_clock_replacer(self):
        clock = ClockReplacer(pool_size=3)
        clock.unpin(0)
        clock.unpin(1)
        clock.unpin(2)

        # Give ref_bit to 0
        clock.pin(0)
        clock.unpin(0)

        # The hand gives each referenced frame one second chance, then returns to frame 0.
        self.assertEqual(clock.victim(), 0)

    def test_bpm_lru_eviction_and_metrics(self):
        # Create 3 frames in buffer pool
        bpm = BufferPoolManager(pool_size=3, disk_manager=self.disk_manager, policy="lru")

        # Allocate 4 pages on disk
        p0 = self.disk_manager.allocate_page()
        p1 = self.disk_manager.allocate_page()
        p2 = self.disk_manager.allocate_page()
        p3 = self.disk_manager.allocate_page()

        # Fetch pages 0, 1, 2 (all misses, filling pool)
        page0 = bpm.fetch_page(p0)
        page1 = bpm.fetch_page(p1)
        page2 = bpm.fetch_page(p2)

        self.assertEqual(bpm.misses, 3)
        self.assertEqual(bpm.hits, 0)

        # Unpin all pages
        bpm.unpin_page(p0)
        bpm.unpin_page(p1)
        bpm.unpin_page(p2)

        # Re-fetch page 0 -> Cache Hit!
        bpm.fetch_page(p0)
        self.assertEqual(bpm.hits, 1)
        bpm.unpin_page(p0)

        # Fetch page 3 -> Cache Miss! Causes LRU eviction of page 1 (since 0 was accessed recently)
        bpm.fetch_page(p3)
        self.assertEqual(bpm.misses, 4)
        self.assertIn(p3, bpm.page_table)
        self.assertNotIn(p1, bpm.page_table)  # Page 1 evicted!

    def test_dirty_page_persistence(self):
        bpm = BufferPoolManager(pool_size=2, disk_manager=self.disk_manager, policy="lru")
        p0 = self.disk_manager.allocate_page()
        p1 = self.disk_manager.allocate_page()
        p2 = self.disk_manager.allocate_page()

        # Load p0 and write data into it
        page0 = bpm.fetch_page(p0)
        slot = page0.insert_record(b"Persistent Data")
        bpm.unpin_page(p0, is_dirty=True)

        # Load p1 and p2 to force eviction of p0
        bpm.fetch_page(p1)
        bpm.unpin_page(p1)
        bpm.fetch_page(p2)
        bpm.unpin_page(p2)

        # Re-fetch p0 from disk -> data must be persisted!
        page0_reloaded = bpm.fetch_page(p0)
        self.assertEqual(page0_reloaded.get_record(slot), b"Persistent Data")

if __name__ == "__main__":
    unittest.main()
