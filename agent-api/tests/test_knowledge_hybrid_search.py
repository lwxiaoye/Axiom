"""知识库关键词 / 混合检索：取值归一化、权重解析、两路融合与 search_chunks 分流。

MySQL 全文与 Qdrant 都换成假实现；SQL 本身对线上库的实测见提交说明。
"""
import logging
from types import SimpleNamespace

import pytest

from app.services.knowledge import knowledge_base_service as kb


def test_normalize_retrieval_mode_accepts_every_spelling_in_use():
    # 工作流引擎（SEMANTIC/KEYWORD/HYBRID）、节点原始取值（embedding/fullTextRecall/mixedRecall）、
    # 前端契约（VECTOR）三套拼写都得认，大小写与分隔符不敏感
    assert kb.normalize_retrieval_mode('SEMANTIC') == 'VECTOR'
    assert kb.normalize_retrieval_mode('embedding') == 'VECTOR'
    assert kb.normalize_retrieval_mode('fullTextRecall') == 'KEYWORD'
    assert kb.normalize_retrieval_mode('full_text') == 'KEYWORD'
    assert kb.normalize_retrieval_mode('mixedRecall') == 'HYBRID'
    assert kb.normalize_retrieval_mode(' hybrid ') == 'HYBRID'
    assert kb.normalize_retrieval_mode(None) is None
    assert kb.normalize_retrieval_mode('') is None


def test_normalize_retrieval_mode_unknown_degrades_with_warning(caplog):
    with caplog.at_level(logging.WARNING):
        assert kb.normalize_retrieval_mode('quantum') == 'VECTOR'
    assert any('quantum' in r.getMessage() for r in caplog.records)
    with pytest.raises(ValueError):
        kb.normalize_retrieval_mode('quantum', strict=True)


def test_resolve_weights():
    assert kb.resolve_weights(None, None) == (0.5, 0.5)
    assert kb.resolve_weights(0.7, None) == pytest.approx((0.7, 0.3))
    assert kb.resolve_weights(None, 0.2) == pytest.approx((0.8, 0.2))
    # 两个都给、和不为 1：按比例缩放，排序不变、融合分仍落在 0–1
    assert kb.resolve_weights(0.8, 0.8) == pytest.approx((0.5, 0.5))
    assert kb.resolve_weights(3, -1) == pytest.approx((1.0, 0.0))
    assert kb.resolve_weights(0, 0) == (0.5, 0.5)


def _hit(pid, *, vector=None, keyword=None):
    return {
        'content': f'c-{pid}', 'source': 'd', 'knowledgeId': 'k', 'documentId': 'doc',
        'chunkIndex': 0, 'pointId': pid, 'vectorScore': vector, 'keywordScore': keyword,
        'score': vector if vector is not None else keyword,
    }


def test_fuse_hits_normalizes_each_leg_and_merges_by_point_id():
    vector = [_hit('a', vector=0.9), _hit('b', vector=0.5), _hit('c', vector=0.1)]
    keyword = [_hit('b', keyword=12.0), _hit('d', keyword=3.0)]
    fused = kb.fuse_hits(vector, keyword, 0.5, 0.5)
    by_id = {h['pointId']: h for h in fused}
    # b 两路都命中：向量归一 0.5、关键词归一 1.0
    assert by_id['b']['score'] == pytest.approx(0.75)
    assert by_id['b']['vectorScore'] == 0.5 and by_id['b']['keywordScore'] == 12.0
    # a 只在向量路：归一 1.0 × 0.5；关键词分保持 None 而不是 0
    assert by_id['a']['score'] == pytest.approx(0.5) and by_id['a']['keywordScore'] is None
    assert by_id['d']['score'] == pytest.approx(0.0) and by_id['d']['vectorScore'] is None
    assert [h['pointId'] for h in fused][:2] == ['b', 'a']
    assert len(fused) == 4
    assert all('_vector_norm' not in h and '_keyword_norm' not in h for h in fused)


def test_fuse_hits_single_candidate_counts_as_best():
    fused = kb.fuse_hits([_hit('a', vector=0.2)], [_hit('a', keyword=1.5)], 0.6, 0.4)
    assert fused[0]['score'] == pytest.approx(1.0)


@pytest.fixture
def engine(monkeypatch):
    """假向量库 + 假全文检索；记录两路各自收到的 limit。"""
    calls = {'vector': [], 'keyword': []}

    async def vector_search(ids, query, limit, threshold):
        calls['vector'].append((limit, threshold))
        return [_hit(f'v{i}', vector=1 - i * 0.1) for i in range(min(limit, 5))]

    async def keyword_search(ids, query, limit):
        calls['keyword'].append(limit)
        return [_hit('v1', keyword=9.0), _hit('k0', keyword=4.0)]

    async def saved_settings(ids):
        return {'top_k': 3, 'score_threshold': 0.2, 'retrieval_mode': 'HYBRID',
                'semantic_weight': 0.8, 'keyword_weight': 0.2}

    async def no_rerank():
        return None

    monkeypatch.setattr(kb, '_vector_search', vector_search)
    monkeypatch.setattr(kb, '_keyword_search', keyword_search)
    monkeypatch.setattr(kb, '_load_base_settings', saved_settings)
    monkeypatch.setattr(kb.rerank_service, 'get_active_rerank_config', no_rerank)
    return calls


@pytest.mark.asyncio
async def test_keyword_mode_skips_vector_and_threshold(engine):
    hits = await kb.search_chunks(knowledge_ids=['k'], query='借书期限', top_k=1, score_threshold=0.9,
                                  retrieval_mode='KEYWORD')
    assert engine['vector'] == [] and engine['keyword'] == [1]
    assert [h['pointId'] for h in hits] == ['v1']
    assert hits[0]['score'] == 9.0 and hits[0]['vectorScore'] is None


@pytest.mark.asyncio
async def test_hybrid_mode_widens_candidates_and_fuses(engine):
    hits = await kb.search_chunks(knowledge_ids=['k'], query='q', top_k=2, score_threshold=0.3,
                                  retrieval_mode='mixedRecall', semantic_weight=0.5)
    # 两路候选都按 max(top_k*4, 20) 取；阈值只进向量路
    assert engine['vector'] == [(20, 0.3)] and engine['keyword'] == [20]
    assert len(hits) == 2
    # v1 两路都中：向量归一 (0.9-0.6)/(1.0-0.6)=0.75、关键词归一 1.0 → 0.875，压过纯向量的 v0
    assert hits[0]['pointId'] == 'v1' and hits[0]['score'] == pytest.approx(0.875)
    assert hits[0]['vectorScore'] == pytest.approx(0.9) and hits[0]['keywordScore'] == 9.0
    assert hits[1]['pointId'] == 'v0'


@pytest.mark.asyncio
async def test_mode_and_weights_fall_back_to_saved_settings(engine):
    hits = await kb.search_chunks(knowledge_ids=['k'], query='q')
    # 库设置：HYBRID、top_k=3、权重 0.8/0.2
    assert engine['vector'] == [(20, 0.2)] and engine['keyword'] == [20]
    assert len(hits) == 3
    v1 = next(h for h in hits if h['pointId'] == 'v1')
    assert v1['score'] == pytest.approx(0.8 * 0.75 + 0.2 * 1.0)


@pytest.mark.asyncio
async def test_vector_mode_keeps_legacy_shape(engine):
    hits = await kb.search_chunks(knowledge_ids=['k'], query='q', top_k=2, score_threshold=0.3,
                                  retrieval_mode='VECTOR')
    assert engine['vector'] == [(2, 0.3)] and engine['keyword'] == []
    assert all(h['score'] == h['vectorScore'] and h['keywordScore'] is None for h in hits)


@pytest.mark.asyncio
async def test_hybrid_then_rerank_keeps_both_leg_scores(engine, monkeypatch):
    async def active():
        return SimpleNamespace(model='rr')

    async def fake_rerank(query, documents, *, top_n=None, config=None, **kwargs):
        return [(documents.index('c-k0'), 0.99)]

    monkeypatch.setattr(kb.rerank_service, 'get_active_rerank_config', active)
    monkeypatch.setattr(kb.rerank_service, 'rerank', fake_rerank)
    hits = await kb.search_chunks(knowledge_ids=['k'], query='q', top_k=1, score_threshold=0.3,
                                  retrieval_mode='HYBRID')
    assert [h['pointId'] for h in hits] == ['k0']
    assert hits[0]['score'] == 0.99 and hits[0]['keywordScore'] == 4.0 and hits[0]['vectorScore'] is None


@pytest.mark.asyncio
async def test_rebuild_chunk_rows_pages_through_qdrant_and_skips_existing(monkeypatch):
    """回填：分页 scroll、跳过 MySQL 已有的 point_id（无连字符写法也算同一个点）、停用点照写但 enabled=0。"""
    existing_hex = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
    pages = {
        None: ([
            SimpleNamespace(id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', payload={'content': '旧', 'document_id': 'd', 'chunk_index': 0}),
            SimpleNamespace(id='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', payload={'content': '新1', 'document_id': 'd', 'chunk_index': 1}),
        ], 'page2'),
        'page2': ([
            SimpleNamespace(id='cccccccc-cccc-cccc-cccc-cccccccccccc', payload={'content': '停用', 'document_id': 'd', 'chunk_index': 2, 'enabled': False}),
            SimpleNamespace(id='dddddddd-dddd-dddd-dddd-dddddddddddd', payload={'content': '', 'document_id': 'd', 'chunk_index': 3}),
        ], None),
    }

    class FakeClient:
        async def collection_exists(self, name):
            return True

        async def scroll(self, *, offset=None, **kwargs):
            return pages[offset]

    class FakeResult:
        def scalars(self):
            return self

        def all(self):
            return [existing_hex]

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, stmt):
            return FakeResult()

    inserted = []

    async def insert_rows(rows):
        inserted.extend(rows)
        return len(rows)

    async def active_config():
        return SimpleNamespace(model='emb', dimension=2)

    monkeypatch.setattr(kb, '_get_client', lambda: FakeClient())
    monkeypatch.setattr(kb, 'async_session', lambda: FakeSession())
    monkeypatch.setattr(kb, '_insert_chunk_rows', insert_rows)
    monkeypatch.setattr(kb.embedding_service, 'get_active_embedding_config', active_config)

    written = await kb.rebuild_chunk_rows('k')
    assert written == 2
    assert [(r.point_id, r.chunk_index, r.enabled, r.char_count) for r in inserted] == [
        ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 1, 1, 2),
        ('cccccccc-cccc-cccc-cccc-cccccccccccc', 2, 0, 2),
    ]
    assert all(r.knowledge_id == 'k' and r.document_id == 'd' for r in inserted)
