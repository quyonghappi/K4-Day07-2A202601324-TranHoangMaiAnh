"""Benchmark chunk retrieval, filtering, grounding, and failure cases.

The benchmark deliberately evaluates evidence at *chunk* level.  A matching
``doc_id`` is not enough: the configured evidence string must occur in one of
the retrieved chunks.

Run, for example::

    python bench.py
    $env:EMBEDDING_PROVIDER = "local"; python bench.py --json-out report/benchmark.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

from ingest import build_knowledge_base
from src.agent import KnowledgeBaseAgent
from src.chunking import FixedSizeChunker, RecursiveChunker, SemanticChunker, SentenceChunker
from src.embeddings import LocalEmbedder, MockEmbedder

load_dotenv()

TOP_K = 3

# Evidence is intentionally a phrase from the corpus, rather than merely a
# document id.  ``answer_markers`` are used only by the local extractive
# oracle below; they make the benchmark runnable without an external LLM.
QUERIES = [
    {
        "id": "Q1",
        "question": "Thời hạn gửi yêu cầu trả hàng và hoàn tiền trên Shopee là bao lâu?",
        "metadata_filter": None,
        "gold_doc_id": "k4-returns-policy",
        "evidence": "15 (mười lăm) ngày",
        "coherence_markers": ["15 (mười lăm) ngày", "giao hàng thành công"],
        "answer_markers": ["15", "giao hàng thành công"],
    },
    {
        "id": "Q2",
        "question": "Người bán bị xử lý thế nào khi vi phạm quy định đăng bán sản phẩm bị cấm?",
        "metadata_filter": {"customer_role": "seller"},
        "gold_doc_id": "k4-seller-listing",
        "evidence": "xóa/khóa/tạm ẩn hiển thị sản phẩm",
        "coherence_markers": ["xóa/khóa/tạm ẩn hiển thị sản phẩm", "khóa tài khoản"],
        "answer_markers": ["xóa/khóa/tạm ẩn", "khóa tài khoản"],
    },
    {
        "id": "Q3",
        "question": "Quy định về thời gian phản hồi của Người Bán khi nhận yêu cầu trả hàng là bao lâu?",
        "metadata_filter": None,
        "gold_doc_id": "k4-returns-policy",
        "evidence": "02 ngày lịch",
        "coherence_markers": ["02 ngày lịch", "nhận được thông báo"],
        "answer_markers": ["02 ngày lịch", "nhận được thông báo"],
    },
    {
        "id": "Q4",
        "question": "Điều kiện để Người Mua nhận hoàn tiền khi thanh toán bằng COD là gì?",
        "metadata_filter": None,
        "gold_doc_id": "k4-returns-policy",
        "evidence": "liên kết với các phương thức nhận hoàn tiền hợp lệ",
        "coherence_markers": ["COD", "liên kết với các phương thức nhận hoàn tiền hợp lệ"],
        "answer_markers": ["COD", "liên kết", "hoàn tiền"],
    },
    {
        "id": "Q5",
        "question": "Người dùng có quyền yêu cầu xóa hoặc rút lại sự đồng ý sử dụng dữ liệu cá nhân không?",
        "metadata_filter": None,
        "gold_doc_id": "k4-privacy-policy",
        "evidence": "rút lại sự đồng ý",
        "coherence_markers": ["rút lại sự đồng ý", "dpo.vn@shopee.com"],
        "answer_markers": ["rút lại sự đồng ý", "dpo.vn@shopee.com"],
    },
]


def select_embedder(provider: str | None = None):
    """Select the embedder before any benchmark timing or indexing begins."""
    selected = (provider or os.getenv("EMBEDDING_PROVIDER", "mock")).strip().lower()
    if selected == "mock":
        return MockEmbedder(), "mock (deterministic; no semantic meaning)"
    if selected == "local":
        model = os.getenv("LOCAL_EMBEDDING_MODEL")
        embedder = LocalEmbedder(model_name=model) if model else LocalEmbedder()
        return embedder, f"local ({getattr(embedder, 'model_name', 'sentence-transformers')})"
    raise ValueError("EMBEDDING_PROVIDER must be 'mock' or 'local'")


class _RetrievedStore:
    """Tiny store adapter letting KnowledgeBaseAgent answer fixed retrievals."""

    def __init__(self, results: list[dict[str, Any]]) -> None:
        self.results = results

    def search(self, _question: str, top_k: int = 3) -> list[dict[str, Any]]:
        return self.results[:top_k]


def extractive_llm(prompt: str, markers: list[str]) -> str:
    """Return evidence-bearing sentences; deterministic and API-free."""
    context = prompt.split("Context:\n", 1)[-1].split("\n\nQuestion:", 1)[0]
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", context) if s.strip()]
    hits = [s for s in sentences if any(marker.lower() in s.lower() for marker in markers)]
    return " ".join(hits) if hits else "Không đủ thông tin trong context đã truy xuất."


def _answer_for(results: list[dict[str, Any]], query: dict[str, Any]) -> str:
    agent = KnowledgeBaseAgent(_RetrievedStore(results), lambda prompt: extractive_llm(prompt, query["answer_markers"]))
    return agent.answer(query["question"], top_k=TOP_K)


def _evaluate(results: list[dict[str, Any]], answer: str, query: dict[str, Any]) -> dict[str, Any]:
    context = "\n".join(result["content"] for result in results)
    evidence_in_top3 = query["evidence"].lower() in context.lower()
    evidence_ranks = [i for i, result in enumerate(results, 1) if query["evidence"].lower() in result["content"].lower()]
    top1_evidence = bool(evidence_ranks and evidence_ranks[0] == 1)
    coherent = any(all(marker.lower() in result["content"].lower() for marker in query["coherence_markers"]) for result in results)
    answer_correct = all(marker.lower() in answer.lower() for marker in query["answer_markers"])
    grounded = answer_correct and all(marker.lower() in context.lower() for marker in query["answer_markers"])
    score = 2 if evidence_in_top3 and top1_evidence and coherent and answer_correct and grounded else 1 if evidence_in_top3 else 0
    if not evidence_in_top3:
        failure_reason = "Top-3 không chứa chunk có evidence đặc trưng; doc_id (nếu có) không đủ để coi là đúng."
        suggested_change = "Đổi chunk boundary/overlap hoặc rerank theo cụm evidence; kiểm tra lại query expansion."
    elif not top1_evidence:
        failure_reason = f"Evidence có trong top-3 nhưng không ở top-1 (ranks={evidence_ranks})."
        suggested_change = "Tăng trọng số lexical/evidence hoặc dùng reranker để ưu tiên mật độ thông tin trả lời."
    elif not coherent:
        failure_reason = "Chunk có evidence nhưng không giữ đủ điều kiện/ngoại lệ trong cùng ngữ cảnh."
        suggested_change = "Tăng overlap hoặc dùng sentence/recursive chunking với kích thước phù hợp."
    elif not answer_correct or not grounded:
        failure_reason = "Agent trả lời thiếu marker của gold answer hoặc dùng thông tin ngoài context."
        suggested_change = "Ép agent trích dẫn chunk và thêm kiểm tra grounded-answer trước khi chấp nhận."
    else:
        failure_reason = "Pass: evidence ở top-1, chunk đủ coherent và câu trả lời grounded."
        suggested_change = "Không cần sửa cho query này; tiếp tục kiểm tra trên query/corpus khác."
    return {
        "score": score,
        "evidence_in_top3": evidence_in_top3,
        "evidence_ranks": evidence_ranks,
        "top1_evidence": top1_evidence,
        "coherent": coherent,
        "answer_correct": answer_correct,
        "grounded": grounded,
        "failure_reason": failure_reason,
        "suggested_change": suggested_change,
    }


def _result_view(result: dict[str, Any], query: dict[str, Any]) -> dict[str, Any]:
    content = result["content"]
    return {
        "rank": None,
        "id": result["id"],
        "doc_id": result["metadata"].get("doc_id"),
        "chunk_index": result["metadata"].get("chunk_index"),
        "source": result["metadata"].get("source"),
        "score": round(result["score"], 6),
        "semantic_score": round(result.get("semantic_score", 0.0), 6),
        "keyword_score": round(result.get("keyword_score", 0.0), 6),
        "relevant_evidence": query["evidence"].lower() in content.lower(),
        "content": content,
    }


def run_benchmark(provider: str | None = None, json_out: str | None = None) -> dict[str, Any]:
    embedder, backend = select_embedder(provider)
    strategies = {
        "fixed": lambda: FixedSizeChunker(chunk_size=500, overlap=50),
        "sentence": lambda: SentenceChunker(max_sentences_per_chunk=3),
        "semantic": lambda: SemanticChunker(embedding_fn=embedder, similarity_threshold=0.75, chunk_size=500),
        "recursive": lambda: RecursiveChunker(chunk_size=500),
    }
    report: dict[str, Any] = {"embedder": backend, "mock_limitations": backend.startswith("mock"), "strategies": {}}
    print(f"EMBEDDER: {backend}")
    if report["mock_limitations"]:
        print("LIMITATION: mock is deterministic but not semantic; interpret retrieval scores as technical signals only.")

    for strategy_name, factory in strategies.items():
        store = build_knowledge_base("data/k4_ecommerce", embedding_fn=embedder, chunker=factory(), collection_name=f"benchmark_{strategy_name}")
        strategy_report = {"chunk_count": store.get_collection_size(), "queries": []}
        print(f"\n{'=' * 88}\nSTRATEGY: {strategy_name} | chunks={store.get_collection_size()}\n{'=' * 88}")
        for query in QUERIES:
            variants = [("unfiltered", None)] if not query["metadata_filter"] else [("unfiltered", None), ("filtered", query["metadata_filter"])]
            query_report = {"id": query["id"], "question": query["question"], "gold_doc_id": query["gold_doc_id"], "evidence": query["evidence"], "variants": []}
            for variant, metadata_filter in variants:
                results = store.search(query["question"], top_k=TOP_K) if metadata_filter is None else store.search_with_filter(query["question"], top_k=TOP_K, metadata_filter=metadata_filter)
                answer = _answer_for(results, query)
                evaluation = _evaluate(results, answer, query)
                viewed = [_result_view(result, query) for result in results]
                for rank, item in enumerate(viewed, 1):
                    item["rank"] = rank
                variant_report = {"variant": variant, "metadata_filter": metadata_filter, "top3": viewed, "answer": answer, "evaluation": evaluation}
                query_report["variants"].append(variant_report)
                print(f"\n{query['id']} [{variant}] filter={metadata_filter or 'none'} score={evaluation['score']}/2")
                for item in viewed:
                    print(f"  {item['rank']}. {item['id']} score={item['score']:.3f} evidence={item['relevant_evidence']} source={item['source']}")
                    print(f"     {item['content'][:240].replace(chr(10), ' ')}")
                print(f"  answer: {answer}")
                print(f"  analysis: precision={evaluation['evidence_in_top3']} coherence={evaluation['coherent']} grounding={evaluation['grounded']}")
                print(f"  reason: {evaluation['failure_reason']}")
                print(f"  suggested change: {evaluation['suggested_change']}")
            if len(query_report["variants"]) == 2:
                a = [item["id"] for item in query_report["variants"][0]["top3"]]
                b = [item["id"] for item in query_report["variants"][1]["top3"]]
                query_report["filter_ab_identical"] = a == b
                print(f"  filter A/B identical: {a == b}")
            strategy_report["queries"].append(query_report)
        strategy_report["total_score"] = sum(v["evaluation"]["score"] for q in strategy_report["queries"] for v in q["variants"] if v["variant"] == "filtered" or len(q["variants"]) == 1)
        report["strategies"][strategy_name] = strategy_report

    if json_out:
        output = Path(json_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON report: {output}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("mock", "local"), default=None)
    parser.add_argument("--json-out", help="also write the detailed report as JSON")
    args = parser.parse_args()
    try:
        run_benchmark(provider=args.provider, json_out=args.json_out)
    except ImportError as exc:
        print(f"Local embedder unavailable: {exc}. Install requirements-local.txt or use --provider mock.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
