from app.services.agent_runtime import (
    ContextGovernor,
    ContextWindowPolicy,
    EvidenceSpillStore,
    estimate_tokens,
)


def test_spill_store_returns_bounded_chunks_and_original_evidence_id() -> None:
    store = EvidenceSpillStore(chunk_tokens=256)
    content = "\n".join(f"NOTICE line {index} AP offline" for index in range(600))
    descriptor = store.spill(
        content,
        source_evidence_id="LOG-1",
        title="gw.log",
        locator={"source_file": "gw.log"},
    )

    first = store.resolve(descriptor["handle_id"])

    assert first is not None
    assert first["evidence_id"] == "LOG-1"
    assert first["spill_handle_id"].startswith("SPILL-")
    assert first["continuation_handle"] in store.handle_ids
    assert first["metadata"]["citation_allowed"] is False
    assert estimate_tokens(first["content"]) <= 257

    second = store.resolve(first["continuation_handle"])
    assert second is not None
    assert second["line_start"] > first["line_start"]


def test_context_governor_preserves_required_identities_and_exposes_spills() -> None:
    policy = ContextWindowPolicy(
        context_window_tokens=16_384,
        reserved_output_tokens=2_048,
        safety_margin_tokens=512,
        target_occupancy=0.8,
        max_item_tokens=1_000,
        preview_tokens=128,
        spill_chunk_tokens=256,
    )
    store = EvidenceSpillStore(chunk_tokens=policy.spill_chunk_tokens)
    prompt = {
        "case": {"title": "AP频繁离线"},
        "requirements": ["只引用真实证据"],
        "output_contract": {"type": "object"},
        "mandatory_method_documents": [
            {
                "id": f"METHOD-{index}",
                "title": f"Method {index}",
                "content": "认证失败 AP offline DHCP timeout " * 2_000,
            }
            for index in range(4)
        ],
        "ranked_log_evidence": [
            {
                "evidence_id": f"LOG-{index}",
                "source_file": "gw.log",
                "content": "NOTICE AP offline " * 500,
            }
            for index in range(20)
        ],
        "prior_rounds": [],
        "search_observations": [],
        "fault_tree_items": [
            {"id": f"FT-{index}", "label": f"节点 {index}", "description": "检查链路"}
            for index in range(8)
        ],
    }

    governed, metrics = ContextGovernor(policy).govern(prompt, spill_store=store)

    assert {item["id"] for item in governed["mandatory_method_documents"]} == {
        f"METHOD-{index}" for index in range(4)
    }
    assert {item["id"] for item in governed["fault_tree_items"]} == {
        f"FT-{index}" for index in range(8)
    }
    assert metrics["compaction_count"] > 0
    assert metrics["spill_handle_total"] > 0
    assert governed["context_governance"]["compaction_applied"] is True
    assert all(handle.startswith("SPILL-") for handle in store.handle_ids)


def test_short_prompt_is_not_spilled() -> None:
    policy = ContextWindowPolicy(
        context_window_tokens=16_384,
        reserved_output_tokens=2_048,
    )
    store = EvidenceSpillStore(chunk_tokens=256)
    prompt = {
        "case": {"title": "short"},
        "mandatory_method_documents": [{"id": "M-1", "content": "short"}],
        "ranked_log_evidence": [],
        "prior_rounds": [],
        "search_observations": [],
        "fault_tree_items": [],
    }

    governed, metrics = ContextGovernor(policy).govern(prompt, spill_store=store)

    assert governed["mandatory_method_documents"][0]["content"] == "short"
    assert metrics["spill_handle_total"] == 0
