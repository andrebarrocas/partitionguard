"""Bounded local sorting for the original, order-independent fingerprint."""

import heapq
from contextlib import ExitStack, closing, nullcontext
from hashlib import sha256
from tempfile import TemporaryFile

MAX_ROW_BYTES = 1024**2
MAX_PARTITION_BYTES = 128 * 1024**2
SORT_BYTES = 4 * 1024**2
SORT_ROWS = 16384


def read_rows(adapter, sql, parameters):
    stream = getattr(adapter, "iter_rows", None)
    return (
        closing(stream(sql, parameters))
        if stream
        else nullcontext(iter(adapter.fetch(sql, parameters)))
    )


def _frames(file):
    while header := file.read(8):
        yield file.read(int.from_bytes(header, "big"))


def _frame(item):
    return len(item).to_bytes(8, "big") + item


def sorted_fingerprint(encoded):
    """Keep the original length-framed SHA-256, including duplicate rows."""
    count = total = chunk_bytes = 0
    chunk = []
    runs = []
    hasher = sha256()
    with ExitStack() as files:
        for item in encoded:
            size = len(item) + 8
            if size > MAX_ROW_BYTES:
                raise ValueError("Source row exceeds the 1 MiB fingerprint row limit")
            total += size
            if total > MAX_PARTITION_BYTES:
                raise ValueError("Partition exceeds the 128 MiB fingerprint input limit")
            count += 1
            chunk.append(item)
            chunk_bytes += size
            if chunk_bytes >= SORT_BYTES or len(chunk) >= SORT_ROWS:
                file = files.enter_context(TemporaryFile(mode="w+b"))
                for record in sorted(chunk):
                    file.write(_frame(record))
                file.seek(0)
                runs.append(_frames(file))
                chunk.clear()
                chunk_bytes = 0
        for record in heapq.merge(*runs, iter(sorted(chunk))):
            hasher.update(_frame(record))
    return count, hasher.hexdigest()
