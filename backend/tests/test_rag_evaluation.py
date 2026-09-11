from app.services.rag_evaluation import (
    RetrievalMetrics,
    assess_context_hits,
    assess_node_hits,
    ragas_row,
)


def test_retrieval_metrics_calculate_hit_mrr_and_recall():
    metrics = RetrievalMetrics()
    metrics.add([2], found=2, expected=3, duration=1.0)
    metrics.add([], found=0, expected=1, duration=3.0)

    result = metrics.summary()
    assert result["hit_rate"] == 0.5
    assert result["mrr"] == 0.25
    assert result["recall"] == 0.5
    assert result["avg_latency_sec"] == 2.0


def test_ragas_row_has_required_new_api_fields():
    row = ragas_row("问题", ["上下文"], "回答", "标准答案")
    assert set(row) == {"user_input", "retrieved_contexts", "response", "reference"}


def test_node_hit_assessment_accepts_functional_alternatives():
    result = assess_node_hits(
        [
            {"node_names": ["UNETLoader", "VAELoader"]},
            {"node_names": ["ImpactConditionalBranch"]},
            {"node_names": ["CR Text Concatenate"]},
        ],
        expected_nodes=["UNETLoader", "VAELoader"],
        expected_any=[
            ["Any Switch (rgthree)", "ImpactConditionalBranch"],
            ["1hew_TextListToString", "CR Text Concatenate"],
        ],
    )

    assert result["found_count"] == 4
    assert result["expected_count"] == 4
    assert result["ranks"] == [1, 2, 3]
    assert result["found_any"] == ["ImpactConditionalBranch", "CR Text Concatenate"]


def test_context_assessment_can_require_system_source():
    result = assess_context_hits(
        [
            {"content": "指令 /w 的仓库伪副本", "source": "repo:one"},
            {"content": "指令 /w：选择工作流模板", "source": "system"},
        ],
        ["指令 /w"],
        required_source="system",
    )

    assert result == {"ranks": [2], "found": ["指令 /w"]}
