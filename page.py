import struct

PAGE_SIZE = 4096
HEADER_SIZE = 8  # page_id (4B), num_slots (2B), free_space_offset (2B)
SLOT_SIZE = 4    # offset (2B), length (2B)

class Page:
    """
    Slotted Page implementation for MiniDB.

    Layout:
    +-----------------------------------------------------------------------+
    | Header (8B) | Slot Array (4B each) ... -> | ... <- Record Data        |
    | page_id,    | Slot 0: (offset, length)    |                           |
    | num_slots,  | Slot 1: (offset, length)    |                           |
    | free_offset | ...                         |                           |
    +-----------------------------------------------------------------------+
    """
    def __init__(self, page_id: int):
        if not isinstance(page_id, int) or isinstance(page_id, bool) or not 0 <= page_id <= 0xFFFFFFFF:
            raise ValueError("page_id must fit in an unsigned 32-bit integer")
        self.page_id = page_id
        self.data = bytearray(PAGE_SIZE)
        self.num_slots = 0
        self.free_space_offset = PAGE_SIZE

        # Write initial header
        self._write_header()

    def _write_header(self):
        """Sync header values into the page bytearray."""
        struct.pack_into(">IHH", self.data, 0, self.page_id, self.num_slots, self.free_space_offset)

    def _read_header(self):
        """Read header values from the page bytearray."""
        self.page_id, self.num_slots, self.free_space_offset = struct.unpack_from(">IHH", self.data, 0)

    def get_free_space(self) -> int:
        """Calculate total unallocated contiguous space in the page."""
        used_header_and_slots = HEADER_SIZE + (self.num_slots * SLOT_SIZE)
        return self.free_space_offset - used_header_and_slots

    def insert_record(self, record_bytes: bytes) -> int:
        """
        Inserts a byte record into the page using the slotted layout.
        Returns slot_id on success, or -1 if page doesn't have enough space.
        """
        if not isinstance(record_bytes, (bytes, bytearray)):
            raise TypeError("record_bytes must be bytes")
        rec_len = len(record_bytes)
        if rec_len == 0:
            raise ValueError("empty records cannot be represented in a slotted page")
        if rec_len > 0xFFFF:
            raise ValueError("record is too large for a slotted-page slot")

        # Find if we can reuse an empty slot (offset=0, length=0)
        reused_slot_id = -1
        for i in range(self.num_slots):
            offset, length = self._get_slot(i)
            if length == 0:
                reused_slot_id = i
                break

        needed_space = rec_len + (0 if reused_slot_id != -1 else SLOT_SIZE)
        if self.get_free_space() < needed_space:
            return -1  # Page full

        # Determine slot_id
        if reused_slot_id != -1:
            slot_id = reused_slot_id
        else:
            slot_id = self.num_slots
            self.num_slots += 1

        # Allocate tuple data from free space pointer downward
        new_offset = self.free_space_offset - rec_len
        self.free_space_offset = new_offset

        # Copy data bytes into page buffer
        self.data[new_offset : new_offset + rec_len] = record_bytes

        # Update slot entry and header
        self._set_slot(slot_id, new_offset, rec_len)
        self._write_header()

        return slot_id

    def get_record(self, slot_id: int) -> bytes | None:
        """
        Retrieves the record bytes for a given slot_id.
        Returns None if slot_id is invalid or record has been deleted.
        """
        if slot_id < 0 or slot_id >= self.num_slots:
            return None

        offset, length = self._get_slot(slot_id)
        if length == 0:
            return None  # Tombstoned / deleted slot

        return bytes(self.data[offset : offset + length])

    def delete_record(self, slot_id: int) -> bool:
        """
        Deletes a record by tombstoning its slot entry (setting length to 0).
        Returns True if deleted, False if slot_id was invalid or already deleted.
        """
        if slot_id < 0 or slot_id >= self.num_slots:
            return False

        offset, length = self._get_slot(slot_id)
        if length == 0:
            return False  # Already deleted

        # Tombstone slot by clearing offset and length
        self._set_slot(slot_id, 0, 0)
        self._write_header()
        return True

    def _get_slot(self, slot_id: int) -> tuple[int, int]:
        """Helper to read (offset, length) for a given slot_id."""
        slot_pos = HEADER_SIZE + (slot_id * SLOT_SIZE)
        return struct.unpack_from(">HH", self.data, slot_pos)

    def _set_slot(self, slot_id: int, offset: int, length: int):
        """Helper to write (offset, length) for a given slot_id."""
        slot_pos = HEADER_SIZE + (slot_id * SLOT_SIZE)
        struct.pack_into(">HH", self.data, slot_pos, offset, length)

    def to_bytes(self) -> bytes:
        """Serialize page bytearray."""
        self._write_header()
        return bytes(self.data)

    @classmethod
    def from_bytes(cls, page_bytes: bytes, expected_page_id: int | None = None) -> "Page":
        """Deserialize a page and reject inconsistent headers or slot pointers."""
        if len(page_bytes) != PAGE_SIZE:
            raise ValueError(f"serialized page must be exactly {PAGE_SIZE} bytes")
        if not any(page_bytes):
            raise ValueError("serialized page is uninitialized or corrupt")

        page_id, num_slots, free_space_offset = struct.unpack_from(">IHH", page_bytes, 0)
        if expected_page_id is not None and page_id != expected_page_id:
            raise ValueError(
                f"page header id {page_id} does not match requested page {expected_page_id}"
            )
        minimum_free_offset = HEADER_SIZE + num_slots * SLOT_SIZE
        if not minimum_free_offset <= free_space_offset <= PAGE_SIZE:
            raise ValueError("invalid page header: free-space offset overlaps the header or exceeds the page")

        live_ranges = []
        for slot_id in range(num_slots):
            slot_pos = HEADER_SIZE + slot_id * SLOT_SIZE
            offset, length = struct.unpack_from(">HH", page_bytes, slot_pos)
            if length == 0:
                if offset != 0:
                    raise ValueError(f"invalid deleted slot {slot_id}: expected zero offset")
                continue
            if offset < free_space_offset or offset + length > PAGE_SIZE:
                raise ValueError(f"invalid slot {slot_id}: record lies outside the data region")
            live_ranges.append((offset, offset + length))

        live_ranges.sort()
        for previous, current in zip(live_ranges, live_ranges[1:]):
            if previous[1] > current[0]:
                raise ValueError("invalid page: live records overlap")

        page = cls(page_id)
        page.data = bytearray(page_bytes)
        page.num_slots = num_slots
        page.free_space_offset = free_space_offset
        return page
