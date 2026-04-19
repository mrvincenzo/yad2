"""Tests for yad2_watcher.store"""

from __future__ import annotations

from collections.abc import Generator

import pytest

from yad2_watcher.store import SeenStore


@pytest.fixture
def store(tmp_path) -> Generator[SeenStore, None, None]:
    """In-process SeenStore backed by a temp file (cleaned up after each test)."""
    s = SeenStore(tmp_path / "test.db")
    yield s
    s.close()


class TestIsSeenMarkSeen:
    def test_new_token_is_not_seen(self, store: SeenStore) -> None:
        assert store.is_seen("tok1") is False

    def test_marked_token_is_seen(self, store: SeenStore) -> None:
        store.mark_seen("tok1", neighborhood_id=561, price=7500)
        assert store.is_seen("tok1") is True

    def test_different_tokens_independent(self, store: SeenStore) -> None:
        store.mark_seen("tok1", neighborhood_id=561, price=7500)
        assert store.is_seen("tok2") is False

    def test_mark_seen_idempotent(self, store: SeenStore) -> None:
        """Calling mark_seen twice on the same token should not raise."""
        store.mark_seen("tok1", neighborhood_id=561, price=7500)
        store.mark_seen("tok1", neighborhood_id=561, price=7500)  # no error
        assert store.is_seen("tok1") is True

    def test_multiple_tokens(self, store: SeenStore) -> None:
        tokens = ["a", "b", "c"]
        for t in tokens:
            store.mark_seen(t, neighborhood_id=561, price=6000)
        for t in tokens:
            assert store.is_seen(t) is True


class TestLogRun:
    def test_log_run_success(self, store: SeenStore) -> None:
        store.log_run(neighborhood_id=561, fetched_count=5, new_count=2)
        stats = store.stats()
        assert stats["total_runs"] == 1
        last = stats["recent_runs"][0]
        assert last["neighborhood_id"] == 561
        assert last["fetched"] == 5
        assert last["new"] == 2
        assert last["error"] is None

    def test_log_run_with_error(self, store: SeenStore) -> None:
        store.log_run(neighborhood_id=561, fetched_count=0, new_count=0, error="timeout")
        stats = store.stats()
        last = stats["recent_runs"][0]
        assert last["error"] == "timeout"

    def test_multiple_runs(self, store: SeenStore) -> None:
        for i in range(3):
            store.log_run(neighborhood_id=i, fetched_count=i, new_count=0)
        assert store.stats()["total_runs"] == 3

    def test_recent_runs_capped_at_10(self, store: SeenStore) -> None:
        for i in range(15):
            store.log_run(neighborhood_id=561, fetched_count=i, new_count=0)
        assert len(store.stats()["recent_runs"]) == 10


class TestStats:
    def test_empty_stats(self, store: SeenStore) -> None:
        stats = store.stats()
        assert stats["total_seen"] == 0
        assert stats["total_runs"] == 0
        assert stats["recent_runs"] == []

    def test_total_seen_increments(self, store: SeenStore) -> None:
        store.mark_seen("a", 561, 7000)
        store.mark_seen("b", 561, 7000)
        assert store.stats()["total_seen"] == 2

    def test_duplicate_mark_does_not_increment(self, store: SeenStore) -> None:
        store.mark_seen("a", 561, 7000)
        store.mark_seen("a", 561, 7000)
        assert store.stats()["total_seen"] == 1


class TestContextManager:
    def test_context_manager_closes(self, tmp_path) -> None:
        with SeenStore(tmp_path / "cm.db") as s:
            s.mark_seen("tok", 561, 7000)
            assert s.is_seen("tok") is True
        # After __exit__, connection is closed — further calls would raise
        with pytest.raises(Exception):
            s.is_seen("tok")

    def test_creates_parent_dirs(self, tmp_path) -> None:
        deep = tmp_path / "a" / "b" / "c" / "test.db"
        with SeenStore(deep) as s:
            s.mark_seen("tok", 561, 7000)
        assert deep.exists()
