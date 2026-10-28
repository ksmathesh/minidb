from collections import OrderedDict
from disk_manager import DiskManager
from page import Page

class LRUReplacer:
    """Least Recently Used (LRU) Eviction Policy."""
    def __init__(self, pool_size: int):
        self.pool_size = pool_size
        # OrderedDict maintains insertion order of unpinned frames
        self.unpinned_frames: OrderedDict[int, bool] = OrderedDict()

    def pin(self, frame_id: int):
        """Remove frame from unpinned list when pinned."""
        if frame_id in self.unpinned_frames:
            del self.unpinned_frames[frame_id]

    def unpin(self, frame_id: int):
        """Add frame to unpinned list when pin_count reaches 0."""
        if frame_id not in self.unpinned_frames:
            self.unpinned_frames[frame_id] = True

    def victim(self) -> int | None:
        """Returns the least recently used frame_id (first item in OrderedDict)."""
        if not self.unpinned_frames:
            return None
        frame_id, _ = self.unpinned_frames.popitem(last=False)
        return frame_id

    def size(self) -> int:
        return len(self.unpinned_frames)


class ClockReplacer:
    """Clock (Second-Chance) Eviction Policy."""
    def __init__(self, pool_size: int):
        self.pool_size = pool_size
        self.clock_hand = 0
        self.ref_bits = [False] * pool_size
        self.in_replacer = [False] * pool_size

    def pin(self, frame_id: int):
        """Pin frame, setting ref_bit = True (accessed) and removing from unpinned replacer set."""
        self.ref_bits[frame_id] = True
        self.in_replacer[frame_id] = False

    def unpin(self, frame_id: int):
        """Unpin frame, making it eligible for eviction."""
        self.ref_bits[frame_id] = True
        self.in_replacer[frame_id] = True

    def victim(self) -> int | None:
        """
        Scans circular buffer for unpinned frame with ref_bit == False.
        If ref_bit is True, sets to False (second chance) and advances hand.
        """
        if not any(self.in_replacer):
            return None

        scanned = 0
        while scanned < 2 * self.pool_size:
            hand = self.clock_hand
            self.clock_hand = (self.clock_hand + 1) % self.pool_size

            if self.in_replacer[hand]:
                if self.ref_bits[hand]:
                    # Give second chance
                    self.ref_bits[hand] = False
                else:
                    # Found victim with ref_bit == False
                    self.in_replacer[hand] = False
                    return hand
            scanned += 1

        return None


class BufferPoolManager:
    """
    BufferPoolManager manages an array of frames in memory.
    Swaps pages between memory and disk using LRU or Clock eviction.
    Tracks hit/miss metrics for cache pollution analysis.
    """
    def __init__(self, pool_size: int, disk_manager: DiskManager, policy: str = "lru"):
        self.pool_size = pool_size
        self.disk_manager = disk_manager
        self.policy_name = policy.lower()

        if self.policy_name == "lru":
            self.replacer = LRUReplacer(pool_size)
        elif self.policy_name == "clock":
            self.replacer = ClockReplacer(pool_size)
        else:
            raise ValueError(f"Unknown eviction policy: {policy}. Choose 'lru' or 'clock'.")

        self.page_table: dict[int, int] = {}  # page_id -> frame_id
        self.free_list: list[int] = list(range(pool_size))
        self.frames: list[Page | None] = [None] * pool_size
        self.frame_page_ids: list[int | None] = [None] * pool_size
        self.pin_counts: list[int] = [0] * pool_size
        self.is_dirty: list[bool] = [False] * pool_size

        # Metrics
        self.hits = 0
        self.misses = 0

    def fetch_page(self, page_id: int) -> Page | None:
        """
        Fetches a page from buffer pool or disk.
        Increments hit/miss counters and manages frame pinning.
        """
        if page_id in self.page_table:
            # Cache Hit!
            self.hits += 1
            frame_id = self.page_table[page_id]
            self.pin_counts[frame_id] += 1
            self.replacer.pin(frame_id)
            return self.frames[frame_id]

        # Cache Miss!
        self.misses += 1

        # Select frame for new page
        frame_id = self._get_available_frame()
        if frame_id is None:
            return None  # All frames are pinned!

        # Evict old page if frame was occupied
        old_page_id = self.frame_page_ids[frame_id]
        if old_page_id is not None:
            if self.is_dirty[frame_id] and self.frames[frame_id] is not None:
                self.disk_manager.write_page(old_page_id, self.frames[frame_id].to_bytes())
                self.is_dirty[frame_id] = False
            del self.page_table[old_page_id]

        # Read page from disk
        raw_bytes = self.disk_manager.read_page(page_id)
        page = Page.from_bytes(raw_bytes, expected_page_id=page_id)

        # Update frame state
        self.frames[frame_id] = page
        self.frame_page_ids[frame_id] = page_id
        self.page_table[page_id] = frame_id
        self.pin_counts[frame_id] = 1
        self.is_dirty[frame_id] = False
        self.replacer.pin(frame_id)

        return page

    def unpin_page(self, page_id: int, is_dirty: bool = False) -> bool:
        """Unpins a page from the buffer pool."""
        if page_id not in self.page_table:
            return False

        frame_id = self.page_table[page_id]
        if self.pin_counts[frame_id] <= 0:
            return False

        if is_dirty:
            # The full page image must reach the WAL before the caller can report success.
            self.is_dirty[frame_id] = True
            page = self.frames[frame_id]
            if page is not None:
                self.disk_manager.log_page(page_id, page.to_bytes())

        self.pin_counts[frame_id] -= 1
        if self.pin_counts[frame_id] == 0:
            self.replacer.unpin(frame_id)

        # Bound log growth by persisting all dirty frames before truncating the WAL.
        if is_dirty and self.disk_manager.wal_size >= 8 * 1024 * 1024:
            self.flush_all_pages()
        return True

    def new_page(self) -> Page | None:
        """Allocates a new page on disk and brings it into the buffer pool."""
        page_id = self.disk_manager.allocate_page()
        page = self.fetch_page(page_id)
        if page is not None:
            self.unpinned_page_temp = page_id
        return page

    def flush_page(self, page_id: int) -> bool:
        """Flushes a specific page to disk if dirty."""
        if page_id not in self.page_table:
            return False

        frame_id = self.page_table[page_id]
        if self.is_dirty[frame_id] and self.frames[frame_id] is not None:
            self.disk_manager.write_page(page_id, self.frames[frame_id].to_bytes())
            self.is_dirty[frame_id] = False
        return True

    def flush_all_pages(self):
        """Flushes all dirty pages to disk."""
        for page_id in list(self.page_table.keys()):
            self.flush_page(page_id)
        self.disk_manager.checkpoint()

    def _get_available_frame(self) -> int | None:
        if self.free_list:
            return self.free_list.pop(0)
        return self.replacer.victim()

    def get_hit_rate(self) -> float:
        total = self.hits + self.misses
        return (self.hits / total) if total > 0 else 0.0

    def reset_metrics(self):
        self.hits = 0
        self.misses = 0
