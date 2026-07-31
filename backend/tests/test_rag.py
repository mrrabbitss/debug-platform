from app.services.agentic_search import (
    _balanced_candidate_pool,
    _fuse_module_results,
)
from app.services.knowledge import chunk_document
from app.services.rag import tokenize


def test_tokenizer_keeps_error_codes_and_symbols():
    tokens = tokenize("hostapd error -22 in wifi_set_channel")
    assert "hostapd" in tokens
    assert "-22" in tokens
    assert "wifi_set_channel" in tokens


def test_oversized_markdown_paragraph_is_split_with_bounded_chunks():
    chunks = chunk_document("# 日志\n" + ("NOTICE authentication failure. " * 300))

    assert len(chunks) > 1
    assert all(len(content) <= 1800 for _, content in chunks)
    assert all(heading == "日志" for heading, _ in chunks)


def test_fusion_uses_module_rank_and_balances_dense_candidate_pool():
    module_results = {
        "knowledge": [
            {
                "evidence_id": f"K-{index}",
                "source_type": "knowledge",
                "title": f"Knowledge {index}",
                "content": "",
                "source_score": 1000 - index,
                "metadata": {},
                "paths": [],
            }
            for index in range(100)
        ],
        "memory": [
            {
                "evidence_id": f"M-{index}",
                "source_type": "memory",
                "title": f"Memory {index}",
                "content": "",
                "source_score": 0.001,
                "metadata": {},
                "paths": [],
            }
            for index in range(5)
        ],
    }

    fused = _fuse_module_results(module_results)
    pool = _balanced_candidate_pool(fused, 10)

    assert {item["modules"][0] for item in pool} == {"knowledge", "memory"}
    assert sum(item["modules"][0] == "memory" for item in pool) == 5
