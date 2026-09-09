"""Storage backends for the file manager.

Two backends are provided:

* ``LocalStorage``   - files on the local filesystem, metadata in SQLite.
                       Used for local development and as a fallback.
* ``SupabaseStorage`` - files in a Supabase Storage bucket, metadata in a
                       Postgres table accessed through PostgREST.
                       Selected automatically when ``SUPABASE_URL`` and
                       ``SUPABASE_SERVICE_KEY`` are set.

Both expose the same small interface used by ``app.py``.
"""

from __future__ import annotations

import mimetypes
import os
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional

import requests


@dataclass
class FileRecord:
    id: str
    name: str
    size: int
    content_type: str
    created_at: str
    storage_path: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("storage_path", None)
        return d


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _guess_type(name: str, fallback: Optional[str] = None) -> str:
    guessed, _ = mimetypes.guess_type(name)
    return guessed or fallback or "application/octet-stream"


# --------------------------------------------------------------------------- #
# Local backend
# --------------------------------------------------------------------------- #
class LocalStorage:
    backend_name = "local"

    def __init__(self, root: str):
        self.root = root
        self.files_dir = os.path.join(root, "uploads")
        os.makedirs(self.files_dir, exist_ok=True)
        self.db_path = os.path.join(root, "files.db")
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS files (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    content_type TEXT NOT NULL,
                    storage_path TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    @staticmethod
    def _row(row: sqlite3.Row) -> FileRecord:
        return FileRecord(
            id=row["id"],
            name=row["name"],
            size=row["size"],
            content_type=row["content_type"],
            created_at=row["created_at"],
            storage_path=row["storage_path"],
        )

    def list_files(self, query: str = "") -> list[FileRecord]:
        with self._conn() as conn:
            if query:
                rows = conn.execute(
                    "SELECT * FROM files WHERE name LIKE ? ORDER BY created_at DESC",
                    (f"%{query}%",),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM files ORDER BY created_at DESC"
                ).fetchall()
        return [self._row(r) for r in rows]

    def get(self, file_id: str) -> Optional[FileRecord]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        return self._row(row) if row else None

    def save(self, name: str, data: bytes, content_type: Optional[str] = None) -> FileRecord:
        file_id = uuid.uuid4().hex
        ext = os.path.splitext(name)[1]
        storage_path = os.path.join(self.files_dir, file_id + ext)
        with open(storage_path, "wb") as fh:
            fh.write(data)
        rec = FileRecord(
            id=file_id,
            name=name,
            size=len(data),
            content_type=_guess_type(name, content_type),
            created_at=_now(),
            storage_path=storage_path,
        )
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO files VALUES (?, ?, ?, ?, ?, ?)",
                (rec.id, rec.name, rec.size, rec.content_type, rec.storage_path, rec.created_at),
            )
        return rec

    def read(self, file_id: str) -> Optional[bytes]:
        rec = self.get(file_id)
        if not rec or not os.path.exists(rec.storage_path):
            return None
        with open(rec.storage_path, "rb") as fh:
            return fh.read()

    def rename(self, file_id: str, new_name: str) -> Optional[FileRecord]:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE files SET name = ?, content_type = ? WHERE id = ?",
                (new_name, _guess_type(new_name), file_id),
            )
            if cur.rowcount == 0:
                return None
        return self.get(file_id)

    def delete(self, file_id: str) -> bool:
        rec = self.get(file_id)
        if not rec:
            return False
        if os.path.exists(rec.storage_path):
            os.remove(rec.storage_path)
        with self._conn() as conn:
            conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
        return True

    def stats(self) -> dict:
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS total FROM files").fetchone()
        return {"count": row["n"], "total_size": row["total"]}


# --------------------------------------------------------------------------- #
# Supabase backend (PostgREST + Storage API, no SDK required)
# --------------------------------------------------------------------------- #
class SupabaseStorage:
    backend_name = "supabase"

    def __init__(self, url: str, service_key: str, bucket: str = "files", table: str = "files"):
        self.url = url.rstrip("/")
        self.key = service_key
        self.bucket = bucket
        self.table = table
        self.session = requests.Session()
        self.session.headers.update(
            {"apikey": self.key, "Authorization": f"Bearer {self.key}"}
        )

    # -- helpers ------------------------------------------------------------ #
    @property
    def _rest(self) -> str:
        return f"{self.url}/rest/v1/{self.table}"

    def _object_url(self, path: str) -> str:
        return f"{self.url}/storage/v1/object/{self.bucket}/{path}"

    @staticmethod
    def _row(row: dict) -> FileRecord:
        return FileRecord(
            id=row["id"],
            name=row["name"],
            size=int(row["size"]),
            content_type=row["content_type"],
            created_at=row["created_at"],
            storage_path=row["storage_path"],
        )

    # -- interface ---------------------------------------------------------- #
    def list_files(self, query: str = "") -> list[FileRecord]:
        params = {"select": "*", "order": "created_at.desc"}
        if query:
            params["name"] = f"ilike.*{query}*"
        r = self.session.get(self._rest, params=params, timeout=30)
        r.raise_for_status()
        return [self._row(row) for row in r.json()]

    def get(self, file_id: str) -> Optional[FileRecord]:
        r = self.session.get(
            self._rest, params={"select": "*", "id": f"eq.{file_id}"}, timeout=30
        )
        r.raise_for_status()
        rows = r.json()
        return self._row(rows[0]) if rows else None

    def save(self, name: str, data: bytes, content_type: Optional[str] = None) -> FileRecord:
        file_id = uuid.uuid4().hex
        ext = os.path.splitext(name)[1]
        storage_path = f"{file_id}{ext}"
        ctype = _guess_type(name, content_type)

        up = self.session.post(
            self._object_url(storage_path),
            data=data,
            headers={"Content-Type": ctype, "x-upsert": "false"},
            timeout=120,
        )
        up.raise_for_status()

        payload = {
            "id": file_id,
            "name": name,
            "size": len(data),
            "content_type": ctype,
            "storage_path": storage_path,
            "created_at": _now(),
        }
        ins = self.session.post(
            self._rest,
            json=payload,
            headers={"Prefer": "return=representation", "Content-Type": "application/json"},
            timeout=30,
        )
        if not ins.ok:
            # roll back the uploaded object so we don't leak orphans
            self.session.delete(
                f"{self.url}/storage/v1/object/{self.bucket}",
                json={"prefixes": [storage_path]},
                timeout=30,
            )
            ins.raise_for_status()
        return self._row(ins.json()[0])

    def read(self, file_id: str) -> Optional[bytes]:
        rec = self.get(file_id)
        if not rec:
            return None
        r = self.session.get(self._object_url(rec.storage_path), timeout=120)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.content

    def rename(self, file_id: str, new_name: str) -> Optional[FileRecord]:
        r = self.session.patch(
            self._rest,
            params={"id": f"eq.{file_id}"},
            json={"name": new_name, "content_type": _guess_type(new_name)},
            headers={"Prefer": "return=representation", "Content-Type": "application/json"},
            timeout=30,
        )
        r.raise_for_status()
        rows = r.json()
        return self._row(rows[0]) if rows else None

    def delete(self, file_id: str) -> bool:
        rec = self.get(file_id)
        if not rec:
            return False
        self.session.delete(
            f"{self.url}/storage/v1/object/{self.bucket}",
            json={"prefixes": [rec.storage_path]},
            timeout=30,
        ).raise_for_status()
        self.session.delete(
            self._rest, params={"id": f"eq.{file_id}"}, timeout=30
        ).raise_for_status()
        return True

    def stats(self) -> dict:
        files = self.list_files()
        return {"count": len(files), "total_size": sum(f.size for f in files)}


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #
def build_storage() -> LocalStorage | SupabaseStorage:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")
    if url and key:
        return SupabaseStorage(
            url,
            key,
            bucket=os.environ.get("SUPABASE_BUCKET", "files"),
            table=os.environ.get("SUPABASE_TABLE", "files"),
        )

    root = os.environ.get("FILE_MANAGER_DATA")
    if not root:
        # Vercel / serverless: only /tmp is writable.
        if os.environ.get("VERCEL"):
            root = "/tmp/file-manager"
        else:
            root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    return LocalStorage(root)
