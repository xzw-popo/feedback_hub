"""Resumable, coordinated vector-index synchronization contracts."""
from __future__ import annotations

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from feedback_hub import db
from feedback_hub.vector_index.config import VectorIndexConfig
from feedback_hub.vector_index.repository import VectorRepository
from feedback_hub.vector_index.shards import ShardStore
from feedback_hub.vector_index import sync as sync_module
from feedback_hub.vector_index.sync import active_index_config, rebuild_index, sync_pending


def _feedback(feedback_id: str, text: str, ts_ms: int) -> dict[str, object]:
    return {
        "feedback_id": feedback_id, "conversation_id": f"conversation-{feedback_id}",
        "msg_seq": 0, "channel": "wetype", "ts_ms": ts_ms, "platform": "iOS",
        "appversion": "1.2.3", "user_vid": "user", "service_vid": 1,
        "external_chat_url": None, "keyboard_source": "", "device_name": "",
        "channelid": "", "enginever": "", "msgtype": "text", "text": text,
        "tags": "", "raw_json": "{}", "pulled_at": 1,
    }


class DeterministicEncoder:
    def __init__(self, dimension: int = 2) -> None:
        self.dimension = dimension
        self.document_count = 0

    def encode_documents(self, texts: list[str]) -> np.ndarray:
        self.document_count += len(texts)
        vectors = np.zeros((len(texts), self.dimension), dtype=np.float32)
        for index, text in enumerate(texts):
            vectors[index, sum(text.encode("utf-8")) % self.dimension] = 1.0
        return vectors


class FailOnceEncoder(DeterministicEncoder):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    def encode_documents(self, texts: list[str]) -> np.ndarray:
        if not self.failed:
            self.failed = True
            raise RuntimeError("encoder failed")
        return super().encode_documents(texts)


class FailOnSecondCallEncoder(DeterministicEncoder):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def encode_documents(self, texts: list[str]) -> np.ndarray:
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("second chunk failed")
        return super().encode_documents(texts)


@pytest.fixture
def sync_fixture(tmp_path):
    config = replace(
        VectorIndexConfig(), db_path=tmp_path / "feedback.db", data_dir=tmp_path / "vectors",
        dimension=2, model_version="m1", shard_size=2, compact_after_shards=99,
    )
    connection = db.connect(config.db_path)
    db.init_schema(connection)
    rows = [_feedback(f"f{index}", f"text-{index}", index) for index in range(1, 6)]
    for row in rows:
        assert db.upsert_feedback(connection, row)
    connection.execute(
        "INSERT INTO feedback_source_coverage (channel, start_ts_ms, end_ts_ms, completed_at_ms) "
        "VALUES ('wetype', 0, 5, 50)"
    )
    connection.commit()
    repo = VectorRepository(connection)
    repo.init_schema()
    store = ShardStore.from_config(config)

    class Fixture:
        pass

    fixture = Fixture()
    fixture.config, fixture.repo, fixture.shards, fixture.feedback_rows = config, repo, store, rows
    yield fixture
    repo.close()


def test_sync_retries_rows_left_unpublished_after_encoder_failure(sync_fixture):
    with pytest.raises(RuntimeError, match="encoder failed"):
        sync_pending(sync_fixture.config, encoder=FailOnceEncoder())

    assert sync_fixture.repo.pending_count("m1") == 5
    result = sync_pending(sync_fixture.config, encoder=DeterministicEncoder())

    assert result.vectorized_count == 5
    assert sync_fixture.repo.pending_count("m1") == 0
    row = sync_fixture.repo.connection.execute(
        "SELECT status, vectorized_count FROM embedding_sync_run ORDER BY started_at_ms LIMIT 1"
    ).fetchone()
    assert tuple(row) == ("failed", 0)


def test_repeated_sync_does_not_reembed_existing_ids(sync_fixture):
    encoder = DeterministicEncoder()

    sync_pending(sync_fixture.config, encoder=encoder)
    sync_pending(sync_fixture.config, encoder=encoder)

    assert encoder.document_count == len(sync_fixture.feedback_rows)


def test_max_items_is_resumable_across_multiple_chunks_and_defers_watermark(sync_fixture):
    initial = sync_fixture.shards.publish_manifest([], watermark_ts_ms=7)
    encoder = DeterministicEncoder()

    result = sync_pending(sync_fixture.config, encoder=encoder, max_items=3)

    assert result.vectorized_count == 3
    assert result.pending_count == 2
    assert encoder.document_count == 3
    assert sync_fixture.shards.load_manifest().watermark_ts_ms == initial.watermark_ts_ms
    assert len(sync_fixture.shards.load_manifest().shards) == 2

    resumed = sync_pending(sync_fixture.config, encoder=encoder)

    assert resumed.vectorized_count == 2
    assert resumed.pending_count == 0
    assert encoder.document_count == 5
    assert sync_fixture.shards.load_manifest().watermark_ts_ms == 50


def test_sync_compacts_after_configured_shard_threshold(sync_fixture):
    config = replace(sync_fixture.config, shard_size=1, compact_after_shards=2)

    sync_pending(config, encoder=DeterministicEncoder())

    assert sync_fixture.repo.status().active_shards == 1


def test_empty_resume_retries_pending_compaction_before_success(sync_fixture):
    uncompact = replace(sync_fixture.config, shard_size=1, compact_after_shards=99)
    sync_pending(uncompact, encoder=DeterministicEncoder())
    assert sync_fixture.repo.status().active_shards == 5

    resumed = sync_pending(
        replace(uncompact, compact_after_shards=2), encoder=DeterministicEncoder()
    )

    assert resumed.pending_count == 0
    assert sync_fixture.repo.status().active_shards == 1


def test_final_snapshot_sees_feedback_and_coverage_committed_after_last_chunk(sync_fixture, monkeypatch):
    original = VectorRepository.pending_and_coverage_snapshot

    def inject_new_source_data(repository, model_version):
        assert db.upsert_feedback(repository.connection, _feedback("late", "late text", 6))
        repository.connection.execute(
            "INSERT INTO feedback_source_coverage (channel, start_ts_ms, end_ts_ms, completed_at_ms) "
            "VALUES ('wetype', 5, 6, 60)"
        )
        repository.connection.commit()
        return original(repository, model_version)

    monkeypatch.setattr(VectorRepository, "pending_and_coverage_snapshot", inject_new_source_data)

    result = sync_pending(sync_fixture.config, encoder=DeterministicEncoder())

    assert result.pending_count == 1
    assert sync_fixture.shards.load_manifest().watermark_ts_ms == 0


def test_store_recovery_failure_is_recorded_as_a_failed_run(sync_fixture, monkeypatch):
    monkeypatch.setattr(
        ShardStore, "from_config", classmethod(lambda cls, config: (_ for _ in ()).throw(ValueError("bad manifest")))
    )

    with pytest.raises(ValueError, match="bad manifest"):
        sync_pending(sync_fixture.config, encoder=DeterministicEncoder())

    row = sync_fixture.repo.connection.execute(
        "SELECT status, error_code FROM embedding_sync_run ORDER BY started_at_ms DESC LIMIT 1"
    ).fetchone()
    assert tuple(row) == ("failed", "ValueError")


def test_manifest_crash_after_database_commit_is_recovered_without_reembedding(sync_fixture, monkeypatch):
    original = ShardStore._replace_manifest
    crashed = False

    def fail_once(self, *args, **kwargs):
        nonlocal crashed
        if not crashed:
            crashed = True
            raise RuntimeError("manifest crash")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ShardStore, "_replace_manifest", fail_once)
    encoder = DeterministicEncoder()

    with pytest.raises(RuntimeError, match="manifest crash"):
        sync_pending(sync_fixture.config, encoder=encoder, max_items=2)

    assert sync_fixture.shards.publication_journal_path.exists()
    assert sync_fixture.repo.pending_count("m1") == 3
    recovered = sync_pending(sync_fixture.config, encoder=encoder, max_items=2)

    assert recovered.vectorized_count == 2
    assert encoder.document_count == 4
    assert not sync_fixture.shards.publication_journal_path.exists()


def test_rebuild_uses_a_separate_generation_without_clearing_active_index(sync_fixture):
    sync_pending(sync_fixture.config, encoder=DeterministicEncoder())
    old_manifest = sync_fixture.shards.load_manifest()

    result = rebuild_index(
        sync_fixture.config, target_model_version="m2", target_generation_id="m2-first",
        encoder=DeterministicEncoder(),
    )

    active = active_index_config(sync_fixture.config)
    rebuilt_store = ShardStore.from_config(active)
    assert result.pending_count == 0
    assert sync_fixture.shards.load_manifest() == old_manifest
    assert rebuilt_store.load_manifest().model_version.startswith("m2::generation::")
    assert active.model_version.startswith("m2::generation::")


def test_rebuild_requires_a_model_version_distinct_from_the_active_index(sync_fixture):
    sync_pending(sync_fixture.config, encoder=DeterministicEncoder())

    with pytest.raises(ValueError, match="distinct"):
        rebuild_index(
            sync_fixture.config, target_model_version="m1", target_generation_id="same-model",
            encoder=DeterministicEncoder(),
        )


def test_failed_rebuild_leaves_the_prior_active_generation_selected(sync_fixture):
    sync_pending(sync_fixture.config, encoder=DeterministicEncoder())

    with pytest.raises(RuntimeError, match="encoder failed"):
        rebuild_index(
            sync_fixture.config, target_model_version="m2", target_generation_id="failed-m2",
            encoder=FailOnceEncoder(),
        )

    assert active_index_config(sync_fixture.config).model_version == "m1"


def test_promotion_crash_recovers_the_completed_target_generation(sync_fixture, monkeypatch):
    sync_pending(sync_fixture.config, encoder=DeterministicEncoder())
    original = sync_module._atomic_json_replace

    def crash_before_pointer(path, payload, **kwargs):
        if path.name == "active-generation.json":
            raise RuntimeError("promotion crash")
        return original(path, payload)

    monkeypatch.setattr(sync_module, "_atomic_json_replace", crash_before_pointer)
    with pytest.raises(RuntimeError, match="promotion crash"):
        rebuild_index(
            sync_fixture.config, target_model_version="m2", target_generation_id="m2-crash",
            encoder=DeterministicEncoder(),
        )
    monkeypatch.setattr(sync_module, "_atomic_json_replace", original)

    assert active_index_config(sync_fixture.config).model_version.startswith("m2::generation::")
    assert not (sync_fixture.config.data_dir / "generation-promotion-journal.json").exists()


def test_rebuilds_can_promote_m1_then_m2_then_a_new_m1_generation(sync_fixture):
    sync_pending(sync_fixture.config, encoder=DeterministicEncoder())

    rebuild_index(
        sync_fixture.config, target_model_version="m2", target_generation_id="m2-first",
        encoder=DeterministicEncoder(),
    )
    rebuild_index(
        sync_fixture.config, target_model_version="m1", target_generation_id="m1-second",
        encoder=DeterministicEncoder(),
    )

    pointer = (sync_fixture.config.data_dir / "active-generation.json").read_text(encoding="utf-8")
    assert '"model_version":"m1"' in pointer
    assert '"generation_id":"m1-second"' in pointer
    active = active_index_config(sync_fixture.config)
    assert active.data_dir == sync_fixture.config.data_dir / "generations" / "m1-second"
    assert active.model_version != "m1"


def test_rebuild_rejects_a_generation_path_that_escapes_through_a_symlink(sync_fixture, tmp_path):
    generations = sync_fixture.config.data_dir / "generations"
    generations.parent.mkdir(parents=True, exist_ok=True)
    generations.symlink_to(tmp_path / "outside")

    with pytest.raises(ValueError, match="symlink|escapes"):
        rebuild_index(
            sync_fixture.config, target_model_version="m2", target_generation_id="escape",
            encoder=DeterministicEncoder(),
        )

    assert not sync_fixture.config.db_path.exists() or sync_fixture.repo.pending_count("m1") == 5


def test_concurrent_pointer_recovery_serializes_a_promotion_journal(sync_fixture, monkeypatch):
    sync_pending(sync_fixture.config, encoder=DeterministicEncoder())
    original = sync_module._atomic_json_replace

    def crash_before_pointer(path, payload, **kwargs):
        if path.name == "active-generation.json":
            raise RuntimeError("promotion crash")
        return original(path, payload)

    monkeypatch.setattr(sync_module, "_atomic_json_replace", crash_before_pointer)
    with pytest.raises(RuntimeError, match="promotion crash"):
        rebuild_index(
            sync_fixture.config, target_model_version="m2", target_generation_id="m2-race",
            encoder=DeterministicEncoder(),
        )
    monkeypatch.setattr(sync_module, "_atomic_json_replace", original)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: active_index_config(sync_fixture.config).data_dir, range(2)))

    assert results == [sync_fixture.config.data_dir / "generations" / "m2-race"] * 2


def test_rebuild_retry_same_generation_only_embeds_unpublished_chunks(sync_fixture):
    failing = FailOnSecondCallEncoder()

    with pytest.raises(RuntimeError, match="second chunk failed"):
        rebuild_index(
            sync_fixture.config, target_model_version="m2", target_generation_id="resume-m2",
            encoder=failing,
        )

    retry = DeterministicEncoder()
    result = rebuild_index(
        sync_fixture.config, target_model_version="m2", target_generation_id="resume-m2",
        encoder=retry,
    )

    assert result.vectorized_count == 3
    assert retry.document_count == 3
    assert list((sync_fixture.config.data_dir / "generations").iterdir()) == [
        sync_fixture.config.data_dir / "generations" / "resume-m2"
    ]


def test_rebuild_requires_an_explicit_generation_id(sync_fixture):
    with pytest.raises(TypeError):
        rebuild_index(sync_fixture.config, target_model_version="m2", encoder=DeterministicEncoder())


def test_pointer_backed_active_model_cannot_be_rebuilt_again(sync_fixture):
    sync_pending(sync_fixture.config, encoder=DeterministicEncoder())
    rebuild_index(
        sync_fixture.config, target_model_version="m2", target_generation_id="m2-active",
        encoder=DeterministicEncoder(),
    )

    with pytest.raises(ValueError, match="distinct"):
        rebuild_index(
            sync_fixture.config, target_model_version="m2", target_generation_id="m2-again",
            encoder=DeterministicEncoder(),
        )

    assert not (sync_fixture.config.data_dir / "generations" / "m2-again").exists()


def test_stale_promotion_journal_cannot_overwrite_a_newer_live_pointer(sync_fixture):
    sync_pending(sync_fixture.config, encoder=DeterministicEncoder())
    rebuild_index(
        sync_fixture.config, target_model_version="m2", target_generation_id="g2",
        encoder=DeterministicEncoder(),
    )
    stale_target = __import__("json").loads(
        (sync_fixture.config.data_dir / "active-generation.json").read_text(encoding="utf-8")
    )
    rebuild_index(
        sync_fixture.config, target_model_version="m3", target_generation_id="g3",
        encoder=DeterministicEncoder(),
    )
    live = __import__("json").loads(
        (sync_fixture.config.data_dir / "active-generation.json").read_text(encoding="utf-8")
    )
    (sync_fixture.config.data_dir / "generation-promotion-journal.json").write_text(
        __import__("json").dumps({
            "schema_version": 1, "expected_previous": None, "target": stale_target,
        }),
        encoding="utf-8",
    )

    active_index_config(sync_fixture.config)

    assert __import__("json").loads(
        (sync_fixture.config.data_dir / "active-generation.json").read_text(encoding="utf-8")
    ) == live
    assert not (sync_fixture.config.data_dir / "generation-promotion-journal.json").exists()
