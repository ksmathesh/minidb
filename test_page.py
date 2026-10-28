import struct
import unittest
from page import Page, PAGE_SIZE

class TestPage(unittest.TestCase):
    def test_insert_and_get_record(self):
        page = Page(page_id=1)

        rec1 = b"Hello MiniDB"
        rec2 = b"Slotted Page Test Record"

        slot1 = page.insert_record(rec1)
        slot2 = page.insert_record(rec2)

        self.assertEqual(slot1, 0)
        self.assertEqual(slot2, 1)

        self.assertEqual(page.get_record(slot1), rec1)
        self.assertEqual(page.get_record(slot2), rec2)

    def test_delete_record(self):
        page = Page(page_id=2)
        rec = b"Record to be deleted"

        slot = page.insert_record(rec)
        self.assertEqual(page.get_record(slot), rec)

        # Delete
        success = page.delete_record(slot)
        self.assertTrue(success)
        self.assertIsNone(page.get_record(slot))

        # Re-deleting returns False
        self.assertFalse(page.delete_record(slot))

    def test_slot_reuse(self):
        page = Page(page_id=3)
        rec1 = b"Record 1"
        rec2 = b"Record 2"

        slot1 = page.insert_record(rec1)
        self.assertEqual(slot1, 0)

        # Delete slot 0
        page.delete_record(slot1)

        # Insert new record should reuse slot 0
        slot2 = page.insert_record(rec2)
        self.assertEqual(slot2, 0)
        self.assertEqual(page.get_record(slot2), rec2)

    def test_deserialization_rejects_truncated_and_invalid_pages(self):
        with self.assertRaisesRegex(ValueError, "exactly"):
            Page.from_bytes(b"short")

        raw = bytearray(Page(page_id=4).to_bytes())
        with self.assertRaisesRegex(ValueError, "does not match"):
            Page.from_bytes(raw, expected_page_id=5)

        struct.pack_into(">IHH", raw, 0, 4, 1, 0)
        with self.assertRaisesRegex(ValueError, "invalid page header"):
            Page.from_bytes(raw)

    def test_empty_record_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "empty records"):
            Page(page_id=1).insert_record(b"")

    def test_serialization(self):
        page = Page(page_id=4)
        rec = b"Persistent record"
        slot = page.insert_record(rec)

        serialized = page.to_bytes()
        self.assertEqual(len(serialized), PAGE_SIZE)

        restored_page = Page.from_bytes(serialized)
        self.assertEqual(restored_page.page_id, 4)
        self.assertEqual(restored_page.get_record(slot), rec)

if __name__ == "__main__":
    unittest.main()
