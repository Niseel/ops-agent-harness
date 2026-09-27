"""BM25 sparse vectors (spec: Knowledge base search).

A document value is the BM25 term weight without IDF; Qdrant multiplies in the
IDF at query time (`Modifier.IDF`), so the collection never stores corpus
statistics. A query value is 1.0 per distinct token.
"""

import re
import zlib
from collections import Counter

from app.config import cfg

Sparse = tuple[list[int], list[float]]  # (indices, values)


def tokens(text: str) -> list[str]:
    # No stop words and no stemming: IDF makes common words cheap. `payments-api` gives `payments` and `api`.
    return re.findall(r"[a-z0-9_]+", text.lower())


def _index(token: str) -> int:
    return zlib.crc32(token.encode())


def doc_vector(text: str) -> Sparse:
    words = tokens(text)
    tf = Counter(_index(w) for w in words)  # tokens that share an index add up, so indices stay unique
    k1, b, avg_len = cfg.bm25.k1, cfg.bm25.b, cfg.bm25.avg_doc_len
    norm = k1 * (1 - b + b * len(words) / avg_len)
    indices = sorted(tf)
    return indices, [tf[i] * (k1 + 1) / (tf[i] + norm) for i in indices]


def query_vector(text: str) -> Sparse:
    indices = sorted({_index(w) for w in tokens(text)})
    return indices, [1.0] * len(indices)
