"""Fixed-size page I/O with a redo write-ahead log for crash recovery."""
from __future__ import annotations

import os
import struct
import zlib
from pathlib import Path

from page import PAGE_SIZE


_WAL_MAGIC = b"MDBWAL1\0"
_WAL_HEADER = struct.Struct(">8sQI")  # magic, page id, CRC32


class DiskManager:
    """Read and write fixed 4 KiB pages, recovering durable page images on startup."""

    def __init__(self, db_filename: str | os.PathLike[str]):
        self.db_filename = os.fspath(db_filename)
        self.wal_filename = self.db_filename + ".wal"
        if not os.path.exists(self.db_filename):
            with open(self.db_filename, "wb"):
                pass
        self.file = open(self.db_filename, "r+b")
        self.file.seek(0, os.SEEK_END)
        self.num_pages = self.file.tell() // PAGE_SIZE
        try:
            self._recover_wal()
            self.file.seek(0, os.SEEK_END)
            file_size = self.file.tell()
            if file_size % PAGE_SIZE:
                raise ValueError(
                    f"Database file size must be a multiple of {PAGE_SIZE} bytes; got {file_size}"
                )
            self.num_pages = file_size // PAGE_SIZE
        except Exception:
            self.file.close()
            raise

    def _append_wal(self, page_id: int, page_data: bytes) -> None:
        checksum = zlib.crc32(page_data) & 0xFFFFFFFF
        with open(self.wal_filename, "ab") as wal:
            wal.write(_WAL_HEADER.pack(_WAL_MAGIC, page_id, checksum))
            wal.write(page_data)
            wal.flush()
            os.fsync(wal.fileno())

    @property
    def wal_size(self) -> int:
        return os.path.getsize(self.wal_filename) if os.path.exists(self.wal_filename) else 0

    def log_page(self, page_id: int, page_data: bytes) -> None:
        """Force a full-page redo image before a dirty buffer page is unpinned."""
        if not isinstance(page_id, int) or isinstance(page_id, bool) or page_id < 0:
            raise ValueError("page_id must be a non-negative integer")
        if page_id >= self.num_pages:
            raise IndexError(f"Page {page_id} is not allocated")
        if not isinstance(page_data, (bytes, bytearray, memoryview)) or len(page_data) != PAGE_SIZE:
            length = len(page_data) if hasattr(page_data, "__len__") else "unknown"
            raise ValueError(f"Page data size must be exactly {PAGE_SIZE} bytes, got {length}")
        self._append_wal(page_id, page_data)

    def _recover_wal(self) -> None:
        if not os.path.exists(self.wal_filename):
            return
        valid_end = 0
        with open(self.wal_filename, "r+b") as wal:
            while True:
                record_start = wal.tell()
                header = wal.read(_WAL_HEADER.size)
                if not header:
                    valid_end = record_start
                    break
                if len(header) < _WAL_HEADER.size:
                    valid_end = record_start
                    break  # A crash may leave only a partial tail record.
                magic, page_id, expected_crc = _WAL_HEADER.unpack(header)
                if magic != _WAL_MAGIC:
                    raise ValueError(f"Invalid WAL signature at byte {record_start}")
                page_data = wal.read(PAGE_SIZE)
                if len(page_data) < PAGE_SIZE:
                    valid_end = record_start
                    break
                actual_crc = zlib.crc32(page_data) & 0xFFFFFFFF
                if actual_crc != expected_crc:
                    raise ValueError(f"WAL checksum mismatch at byte {record_start}")
                if page_id > self.num_pages:
                    raise ValueError(f"WAL page {page_id} would create a gap in the database file")
                self.file.seek(page_id * PAGE_SIZE)
                self.file.write(page_data)
                self.num_pages = max(self.num_pages, page_id + 1)
                valid_end = wal.tell()

            # Repair a torn final WAL record only after replaying earlier records.
            wal.seek(0, os.SEEK_END)
            if wal.tell() != valid_end:
                wal.truncate(valid_end)
                wal.flush()
                os.fsync(wal.fileno())

        # Data pages must reach stable storage before their redo records are removed.
        self.file.flush()
        os.fsync(self.file.fileno())
        with open(self.wal_filename, "wb") as wal:
            wal.flush()
            os.fsync(wal.fileno())

    def read_page(self, page_id: int) -> bytes:
        """Read one allocated page."""
        if not isinstance(page_id, int) or isinstance(page_id, bool) or page_id < 0:
            raise ValueError("page_id must be a non-negative integer")
        if page_id >= self.num_pages:
            raise IndexError(f"Page {page_id} is not allocated")
        self.file.seek(page_id * PAGE_SIZE)
        data = self.file.read(PAGE_SIZE)
        if len(data) != PAGE_SIZE:
            raise ValueError(f"Page {page_id} is truncated: expected {PAGE_SIZE} bytes, got {len(data)}")
        return data

    def write_page(self, page_id: int, page_data: bytes) -> None:
        """Force a redo record before writing the corresponding database page."""
        if not isinstance(page_id, int) or isinstance(page_id, bool) or page_id < 0:
            raise ValueError("page_id must be a non-negative integer")
        if page_id > self.num_pages:
            raise IndexError("page writes cannot create gaps in the page file")
        if not isinstance(page_data, bytes) or len(page_data) != PAGE_SIZE:
            length = len(page_data) if hasattr(page_data, "__len__") else "unknown"
            raise ValueError(f"Page data size must be exactly {PAGE_SIZE} bytes, got {length}")

        self._append_wal(page_id, page_data)
        self.file.seek(page_id * PAGE_SIZE)
        self.file.write(page_data)
        self.file.flush()
        if page_id >= self.num_pages:
            self.num_pages = page_id + 1

    def allocate_page(self) -> int:
        """Allocate a valid empty page; its page image is logged before it is written."""
        page_id = self.num_pages
        from page import Page
        self.write_page(page_id, Page(page_id).to_bytes())
        return page_id

    def checkpoint(self) -> None:
        """Persist all page writes before clearing redo records."""
        self.file.flush()
        os.fsync(self.file.fileno())
        with open(self.wal_filename, "wb") as wal:
            wal.flush()
            os.fsync(wal.fileno())

    def close(self) -> None:
        """Close the page file without discarding any pending redo records."""
        file = getattr(self, "file", None)
        if file is not None and not file.closed:
            file.flush()
            file.close()

    def __del__(self):
        self.close()
