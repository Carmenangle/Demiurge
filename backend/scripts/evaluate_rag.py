"""使用本机索引运行固定 RAG 检索基准，不输出密钥。"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services import node_index, rag_evaluation, rag_store, reranker  # noqa: E402
from app.services.rag_backend import EmbedConfig  # noqa: E402


def _load() -> tuple[dict, dict]:
    settings = json.loads(
        (BACKEND_DIR / "data" / "user_state.json").read_text(encoding="utf-8")
    ).get("settings", {})
    cases = json.loads(
        (BACKEND_DIR / "app" / "evaluation" / "rag_retrieval_cases.json")
        .read_text(encoding="utf-8")
    )
    return settings, cases


def _load_repositories() -> list[dict]:
    state = json.loads(
        (BACKEND_DIR / "data" / "user_state.json").read_text(encoding="utf-8")
    )
    return [repo for repo in state.get("repos", []) if isinstance(repo, dict) and repo.get("id")]


def _cfg(settings: dict) -> EmbedConfig:
    embed = settings.get("embedModel", {})
    return EmbedConfig(
        base_url=embed.get("baseUrl", ""),
        api_key=embed.get("apiKey", ""),
        embed_model=embed.get("modelName", "text-embedding-3-small"),
        model_dir=embed.get("modelDir", ""),
        reranker_dir=embed.get("rerankerDir", ""),
        mode=embed.get("mode", "local" if embed.get("modelDir") else "remote"),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", help="可选：写入 UTF-8 JSON 报告")
    parser.add_argument(
        "--prewarm-reranker", action="store_true",
        help="在同一评估进程中加载并验证已配置的 Cross-Encoder",
    )
    args = parser.parse_args()
    settings, cases = _load()
    cfg = _cfg(settings)
    report: dict[str, object] = {
        "node_cases": [], "knowledge_cases": [],
        "repository_system_cases": [], "ragas_rows": [],
    }
    report["reranker"] = {
        "configured": bool(cfg.reranker_dir),
        "prewarmed": False,
        "message": "未要求预热，评估将使用混合检索顺序",
    }
    if args.prewarm_reranker and cfg.reranker_dir:
        started = time.perf_counter()
        ok, message = reranker.probe_model(cfg.reranker_dir)
        report["reranker"] = {
            "configured": True,
            "prewarmed": ok,
            "prewarm_sec": time.perf_counter() - started,
            "message": message,
        }

    node_metrics = rag_evaluation.RetrievalMetrics()
    for case in cases.get("node_cases", []):
        started = time.perf_counter()
        hits = node_index.search(cfg, case["query"], k=10)
        duration = time.perf_counter() - started
        assessment = rag_evaluation.assess_node_hits(
            hits, case["expected_nodes"], case.get("expected_any", []),
        )
        ranks = assessment["ranks"]
        node_metrics.add(
            ranks, assessment["found_count"], assessment["expected_count"], duration,
        )
        report["node_cases"].append({
            "name": case["name"], "rank": min(ranks) if ranks else None,
            "found": assessment["found_nodes"],
            "found_any": assessment["found_any"],
            "expected": sorted(case["expected_nodes"]),
            "expected_any": case.get("expected_any", []),
            "latency_sec": duration,
        })

    knowledge_metrics = rag_evaluation.RetrievalMetrics()
    for case in cases.get("knowledge_cases", []):
        started = time.perf_counter()
        hits = rag_store.retrieve_with_trace("home", cfg, case["query"], k=4)
        duration = time.perf_counter() - started
        expected = case["expected_contexts"]
        assessment = rag_evaluation.assess_context_hits(hits, expected)
        ranks = assessment["ranks"]
        knowledge_metrics.add(ranks, len(assessment["found"]), len(expected), duration)
        report["knowledge_cases"].append({
            "query": case["query"], "rank": min(ranks) if ranks else None,
            "sources": [hit.get("source") for hit in hits], "latency_sec": duration,
        })
        report["ragas_rows"].append(rag_evaluation.ragas_row(
            case["query"], [hit["content"] for hit in hits], reference=case["reference"],
        ))

    repository_metrics = rag_evaluation.RetrievalMetrics()
    for repo in _load_repositories():
        for case in cases.get("knowledge_cases", []):
            started = time.perf_counter()
            hits = rag_store.retrieve_with_trace(repo["id"], cfg, case["query"], k=4)
            duration = time.perf_counter() - started
            expected = case["expected_contexts"]
            assessment = rag_evaluation.assess_context_hits(
                hits, expected, required_source="system",
            )
            ranks = assessment["ranks"]
            repository_metrics.add(
                ranks, len(assessment["found"]), len(expected), duration,
            )
            report["repository_system_cases"].append({
                "repo_id": repo["id"], "repo_name": repo.get("name", ""),
                "query": case["query"], "rank": min(ranks) if ranks else None,
                "found": assessment["found"],
                "sources": [hit.get("source") for hit in hits],
                "latency_sec": duration,
            })

    report["node_metrics"] = node_metrics.summary()
    report["knowledge_metrics"] = knowledge_metrics.summary()
    report["repository_system_metrics"] = repository_metrics.summary()
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(json.dumps({
            "reranker": report["reranker"],
            "node_metrics": report["node_metrics"],
            "knowledge_metrics": report["knowledge_metrics"],
            "repository_system_metrics": report["repository_system_metrics"],
            "output": str(Path(args.output)),
        }, ensure_ascii=False, indent=2))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
