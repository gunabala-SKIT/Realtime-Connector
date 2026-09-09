"""Employee gallery: SQLite for metadata + embeddings, an in-memory matrix for
fast cosine k-NN. This is the local stand-in for PostgreSQL + pgvector, which
arrives with the backend in Phase 4/5. MVP gallery size is <= 50 (acceptance
doc S2.3), so a plain matrix multiply is ample.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    code         TEXT UNIQUE NOT NULL,
    name         TEXT NOT NULL,
    department   TEXT,
    status       TEXT NOT NULL DEFAULT 'active',
    created_at   REAL NOT NULL,
    n_images     INTEGER NOT NULL DEFAULT 0,
    embedding    BLOB NOT NULL          -- float32[512], L2-normalised mean of enrolment embeddings
);
"""


@dataclass
class Employee:
    id: int
    code: str
    name: str
    department: str | None
    status: str
    n_images: int


@dataclass
class Match:
    employee: Employee
    score: float


class EmployeeGallery:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.execute(_SCHEMA)
        self.conn.commit()
        self._ids: list[int] = []
        self._meta: dict[int, Employee] = {}
        self._matrix = np.zeros((0, 512), dtype=np.float32)
        self.reload()

    # ------------------------------------------------------------------ read
    def reload(self) -> None:
        rows = self.conn.execute(
            "SELECT id, code, name, department, status, n_images, embedding "
            "FROM employees WHERE status = 'active'"
        ).fetchall()
        self._ids = [r[0] for r in rows]
        self._meta = {
            r[0]: Employee(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows
        }
        if rows:
            self._matrix = np.stack(
                [np.frombuffer(r[6], dtype=np.float32) for r in rows]
            )
        else:
            self._matrix = np.zeros((0, 512), dtype=np.float32)

    def __len__(self) -> int:
        return len(self._ids)

    def list(self) -> list[Employee]:
        return list(self._meta.values())

    # --------------------------------------------------------------- matching
    def match(self, embedding: np.ndarray) -> Match | None:
        """Top-1 cosine match, or None if the gallery is empty."""
        if self._matrix.shape[0] == 0:
            return None
        sims = self._matrix @ embedding.astype(np.float32)
        i = int(np.argmax(sims))
        return Match(self._meta[self._ids[i]], float(sims[i]))

    # ----------------------------------------------------------------- write
    def upsert(self, code: str, name: str, department: str | None,
               embedding: np.ndarray, n_images: int) -> int:
        emb = embedding.astype(np.float32)
        emb = emb / (np.linalg.norm(emb) + 1e-9)
        blob = emb.tobytes()
        now = time.time()
        cur = self.conn.execute("SELECT id FROM employees WHERE code = ?", (code,))
        row = cur.fetchone()
        if row:
            self.conn.execute(
                "UPDATE employees SET name=?, department=?, embedding=?, n_images=?, status='active' "
                "WHERE code=?",
                (name, department, blob, n_images, code),
            )
            emp_id = row[0]
        else:
            cur = self.conn.execute(
                "INSERT INTO employees (code, name, department, status, created_at, n_images, embedding) "
                "VALUES (?, ?, ?, 'active', ?, ?, ?)",
                (code, name, department, now, n_images, blob),
            )
            emp_id = int(cur.lastrowid)
        self.conn.commit()
        self.reload()
        return emp_id

    def disable(self, code: str) -> bool:
        cur = self.conn.execute(
            "UPDATE employees SET status='disabled' WHERE code=?", (code,)
        )
        self.conn.commit()
        self.reload()
        return cur.rowcount > 0

    def close(self) -> None:
        self.conn.close()
