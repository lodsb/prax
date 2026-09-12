from prax import hostinfo


def test_memory_numbers_are_sane() -> None:
    m = hostinfo.memory()
    assert set(m) == {
        "ram_total_mb",
        "ram_free_mb",
        "commit_limit_mb",
        "commit_free_mb",
    }
    if m["ram_total_mb"] is not None:
        assert m["ram_total_mb"] > 0
        assert 0 <= (m["ram_free_mb"] or 0) <= m["ram_total_mb"]
        assert 0 <= (m["commit_free_mb"] or 0) <= (m["commit_limit_mb"] or 0)


def test_process_footprint() -> None:
    mb = hostinfo.process_mb()
    assert mb is None or 5 < mb < 100_000
