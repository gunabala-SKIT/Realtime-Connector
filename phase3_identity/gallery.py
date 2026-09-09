"""Employee gallery backed by ChromaDB (embedded, persistent).

Chroma stores the 512-d ArcFace embeddings and the employee metadata, and does
the cosine nearest-neighbour search. This replaces the earlier SQLite + in-memory
matrix; the public interface (``match`` / ``list`` / ``upsert`` / ``disable``) is
unchanged so nothing else in Phase 3/4 needed to move.

Embedded ``PersistentClient`` is single-process. The pipeline and the enrolment
CLI use it; the dashboard/API reads employees from a mirror table in the events
DB instead of opening Chroma a second time. For true multi-process use, run
``chroma run --path <dir>`` and point ``GALLERY_CHROMA_URL`` at it.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class Employee:
    code: str
    name: str
    department: str | None
    status: str
    n_images: int


@dataclass
class Match:
    employee: Employee
    score: float


def _emp(code: str, md: dict) -> Employee:
    return Employee(
        code=code,
        name=md.get("name", code),
        department=(md.get("department") or None),
        status=md.get("status", "active"),
        n_images=int(md.get("n_images", 0)),
    )


class EmployeeGallery:
    COLLECTION = "employees"

    def __init__(self, location: str | Path) -> None:
        import chromadb
        from chromadb.config import Settings

        url = os.getenv("GALLERY_CHROMA_URL")
        if url:  # host:port of a `chroma run` server
            host, _, port = url.partition(":")
            self.client = chromadb.HttpClient(host=host or "localhost", port=int(port or 8000))
        else:
            path = Path(location)
            path.mkdir(parents=True, exist_ok=True)
            self.client = chromadb.PersistentClient(
                path=str(path),
                settings=Settings(anonymized_telemetry=False),
            )
        self.coll = self.client.get_or_create_collection(
            name=self.COLLECTION, metadata={"hnsw:space": "cosine"}
        )

    # ------------------------------------------------------------------ read
    def reload(self) -> None:  # kept for API compatibility; Chroma is always fresh
        pass

    def __len__(self) -> int:
        return len(self.list())

    def list(self) -> list[Employee]:
        got = self.coll.get(where={"status": "active"})
        return [_emp(c, m) for c, m in zip(got["ids"], got["metadatas"])]

    # --------------------------------------------------------------- matching
    def match(self, embedding: np.ndarray) -> Match | None:
        if self.coll.count() == 0:
            return None
        res = self.coll.query(
            query_embeddings=[np.asarray(embedding, dtype=np.float32).tolist()],
            n_results=1,
            where={"status": "active"},
        )
        ids = res["ids"][0]
        if not ids:
            return None
        distance = float(res["distances"][0][0])       # cosine distance, 0..2
        md = res["metadatas"][0][0]
        return Match(_emp(ids[0], md), 1.0 - distance)  # -> cosine similarity

    # ----------------------------------------------------------------- write
    def upsert(self, code: str, name: str, department: str | None,
               embedding: np.ndarray, n_images: int) -> str:
        emb = np.asarray(embedding, dtype=np.float32)
        emb = emb / (np.linalg.norm(emb) + 1e-9)
        existing = self.coll.get(ids=[code])
        created_at = (existing["metadatas"][0].get("created_at")
                      if existing["ids"] else None) or time.time()
        self.coll.upsert(
            ids=[code],
            embeddings=[emb.tolist()],
            metadatas=[{
                "name": name,
                "department": department or "",
                "status": "active",
                "n_images": int(n_images),
                "created_at": float(created_at),
                "updated_at": time.time(),
            }],
        )
        return code

    def disable(self, code: str) -> bool:
        got = self.coll.get(ids=[code])
        if not got["ids"]:
            return False
        md = dict(got["metadatas"][0])
        md["status"] = "disabled"
        self.coll.update(ids=[code], metadatas=[md])
        return True

    def close(self) -> None:  # PersistentClient has no explicit close
        pass
