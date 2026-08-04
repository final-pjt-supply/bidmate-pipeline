# -*- coding: utf-8 -*-
"""cloudflare_embedder 재시도(백오프+jitter) 동작 테스트.

실제 openai 예외 생성이 번거로워 _RETRYABLE을 RuntimeError로 monkeypatch하고,
time.sleep은 no-op으로 대체해 대기 없이 재시도 로직만 검증한다.
"""
import pytest

from embedding import cloudflare_embedder as ce


class _Item:
    def __init__(self, vec, index):
        self.embedding = vec
        self.index = index


class _Resp:
    def __init__(self, vecs):
        self.data = [_Item(v, i) for i, v in enumerate(vecs)]


class _FakeEmbeddings:
    def __init__(self, fail_times):
        self.fail_times = fail_times
        self.calls = 0

    def create(self, model, input):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("429 simulated")
        return _Resp([[0.1, 0.2]] * len(input))


class _FakeClient:
    def __init__(self, fail_times):
        self.embeddings = _FakeEmbeddings(fail_times)


def test_embed_retries_then_succeeds(monkeypatch):
    fake = _FakeClient(fail_times=2)
    monkeypatch.setattr(ce, "_get_client", lambda: fake)
    monkeypatch.setattr(ce, "_RETRYABLE", (RuntimeError,))
    monkeypatch.setattr(ce.time, "sleep", lambda s: None)

    out = ce.embed([{"text": "hi"}])

    assert fake.embeddings.calls == 3  # 2회 실패 후 성공
    assert out[0]["vector"] == [0.1, 0.2]


def test_embed_gives_up_after_max(monkeypatch):
    fake = _FakeClient(fail_times=999)
    monkeypatch.setattr(ce, "_get_client", lambda: fake)
    monkeypatch.setattr(ce, "_RETRYABLE", (RuntimeError,))
    monkeypatch.setattr(ce.time, "sleep", lambda s: None)
    monkeypatch.setattr(ce, "_MAX_RETRIES", 3)

    with pytest.raises(RuntimeError):
        ce.embed([{"text": "hi"}])
    assert fake.embeddings.calls == 4  # 최초 1 + 재시도 3


def test_embed_empty_returns_empty(monkeypatch):
    # 빈 입력은 client 호출 없이 [] 반환(기존 계약 유지)
    monkeypatch.setattr(ce, "_get_client", lambda: (_ for _ in ()).throw(AssertionError("호출 금지")))
    assert ce.embed([]) == []


def test_embed_maps_vectors_by_index_when_response_reordered(monkeypatch):
    """응답 data가 역순으로 와도 .index로 올바른 청크에 매핑돼야 한다."""
    class _ReorderEmb:
        def create(self, model, input):
            # 입력 i에 벡터 [float(i)]를 주되, data는 역순으로 반환
            items = [_Item([float(i)], i) for i in range(len(input))]
            resp = _Resp([])
            resp.data = list(reversed(items))
            return resp

    class _C:
        def __init__(self):
            self.embeddings = _ReorderEmb()

    monkeypatch.setattr(ce, "_get_client", lambda: _C())
    out = ce.embed([{"text": "a"}, {"text": "b"}, {"text": "c"}])
    assert [o["vector"] for o in out] == [[0.0], [1.0], [2.0]]
