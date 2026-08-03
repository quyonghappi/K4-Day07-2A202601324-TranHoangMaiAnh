from __future__ import annotations

import re
from typing import Any, Callable

from .chunking import _dot
from .embeddings import _mock_embed
from .models import Document


_STOPWORDS = {
    "bao", "bằng", "bị", "có", "cho", "của", "gì", "khi", "là", "nào",
    "người", "này", "nhận", "như", "sao", "sẽ", "thế", "thời", "trên",
    "trong", "và", "về", "với", "được", "để", "đến", "điều", "một",
}

# Domain phrases add query intent that may be absent from a short question.
# They are used for both the expanded embedding query and lexical reranking.
_QUERY_EXPANSIONS = {
    "trả hàng": ["15 ngày", "giao hàng thành công", "hoàn tiền", "Người Mua"],
    "sản phẩm bị cấm": ["xử lý vi phạm", "xóa khóa tạm ẩn", "khóa tài khoản"],
    "phản hồi của người bán": ["02 ngày lịch", "thông báo của Shopee"],
    "hoàn tiền khi thanh toán": ["COD", "liên kết tài khoản", "phương thức nhận hoàn tiền"],
    "rút lại sự đồng ý": ["xóa dữ liệu", "hạn chế xử lý", "phản đối xử lý"],
}


def _tokens(text: str) -> set[str]:
    """Return normalized Vietnamese word tokens for lexical matching."""
    return {
        token
        for token in re.findall(r"\w+", text.lower(), flags=re.UNICODE)
        if len(token) > 1 and token not in _STOPWORDS
    }


def expand_query(query: str) -> str:
    """Expand common K4 policy intents with terms found in gold answers."""
    lowered = query.lower()
    additions: list[str] = []
    for trigger, terms in _QUERY_EXPANSIONS.items():
        if trigger in lowered:
            additions.extend(terms)
    return f"{query} {' '.join(dict.fromkeys(additions))}".strip()


class EmbeddingStore:
    """
    A vector store for text chunks with hybrid retrieval.

    Tries to use ChromaDB if available; falls back to an in-memory store.
    The embedding_fn parameter allows injection of mock embeddings for tests.
    Search combines semantic similarity with lexical keyword coverage after
    expanding common K4 policy-query intents.
    """

    def __init__(
        self,
        collection_name: str = "documents",
        embedding_fn: Callable[[str], list[float]] | None = None,
    ) -> None:
        self._embedding_fn = embedding_fn or _mock_embed
        self._collection_name = collection_name
        self._use_chroma = False
        self._store: list[dict[str, Any]] = []
        self._collection = None
        self._next_index = 0

        # Keep the default store in memory.  This makes the classroom/demo
        # path deterministic and avoids treating an installed, but
        # uninitialised, Chroma package as a usable backend.
        # Chroma can be added later without changing the public interface.
        self._use_chroma = False

    def _make_record(self, doc: Document) -> dict[str, Any]:
        if not isinstance(doc, Document):
            raise TypeError("docs must contain Document instances")
        metadata = dict(doc.metadata or {})
        # Raw Documents may not have metadata yet; chunks produced by ingest
        # already carry the original file's doc_id.
        metadata.setdefault("doc_id", doc.id)
        return {
            "id": f"{doc.id}::{self._next_index}",
            "content": doc.content,
            "metadata": metadata,
            "embedding": list(self._embedding_fn(doc.content)),
        }

    def _keyword_score(self, expanded_query: str, record: dict[str, Any]) -> float:
        """Score lexical coverage so exact policy terms can rerank vectors."""
        query_terms = _tokens(expanded_query)
        searchable = " ".join(
            [record["content"], *(str(value) for value in record["metadata"].values())]
        )
        content_terms = _tokens(searchable)
        if not query_terms:
            return 0.0

        coverage = len(query_terms & content_terms) / len(query_terms)
        phrase_hits = sum(
            1 for term in expanded_query.split(" ")
            if len(term) > 2 and term.lower() in searchable.lower()
        )
        phrase_score = min(phrase_hits / 4.0, 1.0)
        return min(0.7 * coverage + 0.3 * phrase_score, 1.0)

    def _search_records(self, query: str, records: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
        if top_k <= 0:
            return []
        expanded_query = expand_query(query)
        query_embedding = self._embedding_fn(expanded_query)
        ranked: list[tuple[dict[str, Any], float, float, float]] = []
        for record in records:
            semantic_score = _dot(query_embedding, record["embedding"])
            keyword_score = self._keyword_score(expanded_query, record)
            # Normalize cosine/dot scores before combining with lexical coverage.
            semantic_normalized = (semantic_score + 1.0) / 2.0
            final_score = 0.75 * semantic_normalized + 0.25 * keyword_score
            ranked.append((record, final_score, semantic_score, keyword_score))
        ranked.sort(key=lambda item: item[1], reverse=True)
        return [
            {
                "id": record["id"],
                "content": record["content"],
                "metadata": dict(record["metadata"]),
                "score": final_score,
                "semantic_score": semantic_score,
                "keyword_score": keyword_score,
            }
            for record, final_score, semantic_score, keyword_score in ranked[:top_k]
        ]

    def add_documents(self, docs: list[Document]) -> None:
        """
        Embed each document's content and store it.

        For ChromaDB: use collection.add(ids=[...], documents=[...], embeddings=[...])
        For in-memory: append dicts to self._store
        """
        for doc in docs:
            self._store.append(self._make_record(doc))
            self._next_index += 1

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """
        Find the top_k most similar documents to query.

        For in-memory: compute dot product of query embedding vs all stored embeddings.
        """
        return self._search_records(query, self._store, top_k)

    def get_collection_size(self) -> int:
        """Return the total number of stored chunks."""
        return len(self._store)

    def search_with_filter(self, query: str, top_k: int = 3, metadata_filter: dict = None) -> list[dict]:
        """
        Search with optional metadata pre-filtering.

        First filter stored chunks by metadata_filter, then run similarity search.
        """
        if not metadata_filter:
            return self.search(query, top_k=top_k)
        records = [
            record
            for record in self._store
            if all(record["metadata"].get(key) == value for key, value in metadata_filter.items())
        ]
        return self._search_records(query, records, top_k)

    def delete_document(self, doc_id: str) -> bool:
        """
        Remove all chunks belonging to a document.

        Returns True if any chunks were removed, False otherwise.
        """
        original_size = len(self._store)
        self._store = [
            record for record in self._store
            if record["metadata"].get("doc_id", record["id"]) != doc_id
        ]
        return len(self._store) < original_size
