from hashlib import sha256

import pytest

from partitionguard import workload
from partitionguard.verification import _encoded, _fingerprint


def test_spilled_sort_preserves_original_digest_and_duplicates(monkeypatch):
    rows = [("é", 2), ("a", 1), ("é", 2), (None, True)]
    original = sorted(map(_encoded, rows))
    expected = sha256(
        b"".join(len(item).to_bytes(8, "big") + item for item in original)
    ).hexdigest()
    monkeypatch.setattr(workload, "SORT_ROWS", 1)
    assert _fingerprint(iter(reversed(rows))) == expected


@pytest.mark.parametrize("row_limit,partition_limit", [(10, 100), (100, 30)])
def test_workload_byte_caps_abort_even_with_row_count_in_scope(
    monkeypatch, row_limit, partition_limit
):
    monkeypatch.setattr(workload, "MAX_ROW_BYTES", row_limit)
    monkeypatch.setattr(workload, "MAX_PARTITION_BYTES", partition_limit)
    with pytest.raises(ValueError, match="fingerprint.*limit"):
        _fingerprint(iter([("hello",), ("world",)]))
