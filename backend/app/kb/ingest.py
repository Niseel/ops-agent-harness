"""Chunk the knowledge-base documents and index them in Qdrant (spec: Knowledge base search).

One point per `##` section. Ingest is skipped when the documents and the
embedding model are unchanged. If embeddings fail, the index holds BM25 vectors
only; the next ingest tries embeddings again.
"""

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from qdrant_client import models

from app.config import cfg
from app.kb.sparse import doc_vector

log = logging.getLogger("app.kb")


@dataclass(frozen=True)
class Chunk:
    doc_id: str
    title: str
    section: str
    text: str

    @property
    def indexed(self) -> str:
        """What is embedded and tokenized: every chunk of a runbook then matches its service name."""
        return f"{self.title}\n{self.section}\n{self.text}"


def load_chunks(docs_dir: Path) -> list[Chunk]:
    chunks = []
    for path in sorted(docs_dir.glob("*.md")):
        title, section, lines = path.stem, "Overview", []
        sections: list[tuple[str, list[str]]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
            elif line.startswith("## "):
                sections.append((section, lines))
                section, lines = line[3:].strip(), []
            else:
                lines.append(line)
        sections.append((section, lines))
        for name, body in sections:
            text = "\n".join(body).strip()
            if text:  # an empty Overview (heading right after the title) is not a chunk
                chunks.append(Chunk(path.stem, title, name, text))
    return chunks


def content_hash(docs_dir: Path, embed_model: str) -> str:
    digest = hashlib.sha256()
    for path in sorted(docs_dir.glob("*.md")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    digest.update(embed_model.encode())
    return digest.hexdigest()


async def ingest(kb, docs_dir: Path) -> dict:
    """Index `docs_dir` into the collection unless it is current. Qdrant errors propagate."""
    chunks = load_chunks(docs_dir)
    if not chunks:
        raise ValueError(f"no knowledge-base documents in {docs_dir}")
    name, client, digest = cfg.kb.collection, kb.client, content_hash(docs_dir, kb.embedder.model)

    if await client.collection_exists(name):
        first, _ = await client.scroll(name, limit=1, with_payload=True)
        payload = first[0].payload if first else {}
        # A BM25-only index (embed_model null) is never skipped, so dense vectors arrive once embeddings answer.
        if payload.get("content_hash") == digest and payload.get("embed_model"):
            result = {"status": "skipped", "mode": "hybrid", "chunks": len(chunks)}
            log.info("knowledge base unchanged: %s", result)
            return result

    try:
        dense = await kb.embedder.embed([c.indexed for c in chunks])
        # Checked before the old collection is deleted: a short or empty reply must not wipe a working index.
        if len(dense) != len(chunks) or not dense[0] or len({len(v) for v in dense}) != 1:
            raise ValueError(f"embedder returned {len(dense)} vectors of mixed or zero size for {len(chunks)} chunks")
    except Exception as exc:
        log.warning("embeddings failed (%s: %s); indexing BM25 only", type(exc).__name__, exc)
        dense = None

    if await client.collection_exists(name):
        await client.delete_collection(name)
    await client.create_collection(
        name,
        vectors_config={"dense": models.VectorParams(size=len(dense[0]), distance=models.Distance.COSINE)}
        if dense
        else {},
        sparse_vectors_config={"bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)},
    )
    points = []
    for i, chunk in enumerate(chunks):
        indices, values = doc_vector(chunk.indexed)
        vector = {"bm25": models.SparseVector(indices=indices, values=values)}
        if dense:
            vector["dense"] = dense[i]
        payload = {
            "doc_id": chunk.doc_id,
            "title": chunk.title,
            "section": chunk.section,
            "text": chunk.text,
            "content_hash": digest,
            "embed_model": kb.embedder.model if dense else None,
        }
        points.append(models.PointStruct(id=i, vector=vector, payload=payload))
    await client.upsert(name, points=points)
    result = {"status": "rebuilt", "mode": "hybrid" if dense else "sparse_only", "chunks": len(chunks)}
    log.info("knowledge base indexed: %s", result)
    return result
