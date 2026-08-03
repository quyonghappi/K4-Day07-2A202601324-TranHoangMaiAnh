from __future__ import annotations

import math
import re
from collections.abc import Callable

from .embeddings import _mock_embed


class FixedSizeChunker:
    """
    Split text into fixed-size chunks with optional overlap.

    Rules:
        - Each chunk is at most chunk_size characters long.
        - Consecutive chunks share overlap characters.
        - The last chunk contains whatever remains.
        - If text is shorter than chunk_size, return [text].
    """

    def __init__(self, chunk_size: int = 300, overlap: int = 50) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than zero")
        if overlap < 0 or overlap >= chunk_size:
            raise ValueError("overlap must be non-negative and smaller than chunk_size")
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk(self, text: str) -> list[str]:
        if not text:
            return []
        if len(text) <= self.chunk_size:
            return [text]

        step = self.chunk_size - self.overlap
        chunks: list[str] = []
        for start in range(0, len(text), step):
            chunk = text[start : start + self.chunk_size]
            chunks.append(chunk)
            if start + self.chunk_size >= len(text):
                break
        return chunks


class SentenceChunker:
    """
    Split text into chunks of at most max_sentences_per_chunk sentences.

    Sentence detection: split on ". ", "! ", "? " or ".\n".
    Strip extra whitespace from each chunk.
    """

    def __init__(self, max_sentences_per_chunk: int = 3) -> None:
        self.max_sentences_per_chunk = max(1, max_sentences_per_chunk)

    def chunk(self, text: str) -> list[str]:
        if not text or not text.strip():
            return []

        # The look-behind keeps the terminator in the sentence.  Whitespace
        # after a terminator is the boundary, so abbreviations and decimals
        # are not split merely because they contain a dot.
        sentences = [
            part.strip()
            for part in re.split(r"(?<=[.!?])[ \t\r\n]+", text.strip())
            if part.strip()
        ]

        return [
            " ".join(sentences[i : i + self.max_sentences_per_chunk]).strip()
            for i in range(0, len(sentences), self.max_sentences_per_chunk)
        ]


class SemanticChunker:
    """Group neighbouring sentences with similar embedding vectors.

    A new chunk starts when the cosine similarity between two neighbouring
    sentences falls below ``similarity_threshold`` or when adding the next
    sentence would exceed ``chunk_size``.  The default mock embedder keeps
    this class usable without optional model/API dependencies; callers should
    inject a real multilingual embedder for meaningful semantic boundaries.
    """

    def __init__(
        self,
        embedding_fn: Callable[[str], list[float]] | None = None,
        similarity_threshold: float = 0.75,
        chunk_size: int = 300,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than zero")
        if not -1.0 <= similarity_threshold <= 1.0:
            raise ValueError("similarity_threshold must be between -1 and 1")
        self.embedding_fn = embedding_fn or _mock_embed
        self.similarity_threshold = similarity_threshold
        self.chunk_size = chunk_size

    def chunk(self, text: str) -> list[str]:
        if not text or not text.strip():
            return []

        sentences = SentenceChunker(max_sentences_per_chunk=1).chunk(text)
        embeddings = [self.embedding_fn(sentence) for sentence in sentences]
        chunks: list[str] = []
        pending = ""

        def flush() -> None:
            nonlocal pending
            if pending:
                chunks.append(pending)
                pending = ""

        for index, sentence in enumerate(sentences):
            # A single oversized sentence cannot be kept semantically intact
            # while respecting the size contract, so split it as a last resort.
            if len(sentence) > self.chunk_size:
                flush()
                chunks.extend(FixedSizeChunker(self.chunk_size, overlap=0).chunk(sentence))
                continue

            if not pending:
                pending = sentence
                continue

            similarity = compute_similarity(embeddings[index - 1], embeddings[index])
            candidate = f"{pending} {sentence}"
            if similarity >= self.similarity_threshold and len(candidate) <= self.chunk_size:
                pending = candidate
            else:
                flush()
                pending = sentence

        flush()
        return chunks


class RecursiveChunker:
    """
    Recursively split text using separators in priority order.

    Default separator priority:
        ["\n\n", "\n", ". ", " ", ""]
    """

    DEFAULT_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]

    def __init__(self, separators: list[str] | None = None, chunk_size: int = 300) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than zero")
        self.separators = self.DEFAULT_SEPARATORS if separators is None else list(separators)
        self.chunk_size = chunk_size

    def chunk(self, text: str) -> list[str]:
        if not text or not text.strip():
            return []
        return self._split(text.strip(), self.separators)

    def _split(self, current_text: str, remaining_separators: list[str]) -> list[str]:
        current_text = current_text.strip()
        if not current_text:
            return []
        if len(current_text) <= self.chunk_size:
            return [current_text]

        # Use the first separator that actually occurs in this text.  This
        # gives paragraphs priority over lines, sentences, and words.
        separator = next(
            (candidate for candidate in remaining_separators if candidate and candidate in current_text),
            None,
        )
        if separator is None:
            # An empty separator means character-level splitting.  It is also
            # the safe fallback when a caller supplies no usable separators.
            return [
                current_text[start : start + self.chunk_size]
                for start in range(0, len(current_text), self.chunk_size)
            ]

        separator_index = remaining_separators.index(separator)
        next_separators = remaining_separators[separator_index + 1 :]
        pieces = [piece.strip() for piece in current_text.split(separator) if piece.strip()]

        chunks: list[str] = []
        pending = ""

        def flush() -> None:
            nonlocal pending
            if pending:
                chunks.append(pending)
                pending = ""

        for piece in pieces:
            if len(piece) > self.chunk_size:
                flush()
                chunks.extend(self._split(piece, next_separators))
                continue

            candidate = piece if not pending else pending + separator + piece
            if len(candidate) <= self.chunk_size:
                pending = candidate
            else:
                flush()
                pending = piece
        flush()
        return chunks


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def compute_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """
    Compute cosine similarity between two vectors.

    cosine_similarity = dot(a, b) / (||a|| * ||b||)

    Returns 0.0 if either vector has zero magnitude.
    """
    norm_a = math.sqrt(sum(value * value for value in vec_a))
    norm_b = math.sqrt(sum(value * value for value in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return _dot(vec_a, vec_b) / (norm_a * norm_b)


class ChunkingStrategyComparator:
    """Run all built-in chunking strategies and compare their results."""

    def compare(self, text: str, chunk_size: int = 200) -> dict:
        strategies = {
            "fixed_size": FixedSizeChunker(chunk_size=chunk_size, overlap=0).chunk(text),
            "by_sentences": SentenceChunker(max_sentences_per_chunk=3).chunk(text),
            "recursive": RecursiveChunker(chunk_size=chunk_size).chunk(text),
            "semantic": SemanticChunker(chunk_size=chunk_size).chunk(text),
        }

        comparison: dict[str, dict] = {}
        for name, chunks in strategies.items():
            lengths = [len(chunk) for chunk in chunks]
            comparison[name] = {
                "count": len(chunks),
                "avg_length": sum(lengths) / len(lengths) if lengths else 0.0,
                "chunks": chunks,
            }
        return comparison
