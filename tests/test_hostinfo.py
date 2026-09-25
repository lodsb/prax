import pytest

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


def test_room_says_what_would_have_to_move(monkeypatch: pytest.MonkeyPatch) -> None:
    """The question a batch asks before it starts: an embedder that cannot
    get a device does not fail, it falls back to the CPU and runs at a
    thirtieth of the speed (2026-09-25)."""
    monkeypatch.setattr(
        hostinfo,
        "gpu",
        lambda *a, **k: [
            {"index": 0, "name": "card", "total_mb": 24000, "free_mb": 3000}
        ],
    )
    monkeypatch.setattr(
        hostinfo,
        "holders",
        lambda *a, **k: [
            {"pid": 1, "name": "llama-server", "mb": 20000},
            {"pid": 2, "name": "python", "mb": 900},
        ],
    )
    assert hostinfo.room(2000)["fits"] is True
    assert hostinfo.room(2000)["free_by"] == []

    tight = hostinfo.room(21000)
    assert tight["fits"] is False
    # the biggest first, so the shortest list of things to stop
    assert [h["name"] for h in tight["free_by"]] == ["llama-server"]


def test_room_on_a_host_with_no_card_says_it_does_not_know(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not knowing is not the same as no: the board has no card and no
    contention either."""
    monkeypatch.setattr(hostinfo, "gpu", lambda *a, **k: [])
    r = hostinfo.room(8000)
    assert r["fits"] is None and r["holders"] == [] and r["free_mb"] is None
