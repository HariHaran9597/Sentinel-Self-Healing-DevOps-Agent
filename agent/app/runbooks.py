"""Runbook library: parse markdown runbooks, embed them into ChromaDB and
retrieve the best match for an error signature.

Seeding is fingerprinted: the collection is rebuilt only when a runbook file
changes, so the persistent Chroma volume is normally reused as-is.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
from pathlib import Path

import yaml

os_env_set = __import__("os").environ.setdefault
os_env_set("ANONYMIZED_TELEMETRY", "False")

import chromadb  # noqa: E402  (import after telemetry opt-out)

log = logging.getLogger("sentinel.runbooks")

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
SECTION_SPLIT_RE = re.compile(r"(?m)^##\s+")

_query_lock = threading.Lock()  # serialize ONNX embedding calls


def parse_runbook(path: Path) -> dict:
    """Split a runbook into YAML frontmatter metadata and `## sections`."""
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    meta = yaml.safe_load(match.group(1)) if match else {}
    body = text[match.end():] if match else text

    sections: dict[str, str] = {}
    chunks = SECTION_SPLIT_RE.split(body)
    if chunks and chunks[0].strip():
        sections["Overview"] = chunks[0].strip()
    for chunk in chunks[1:]:
        title, _, content = chunk.partition("\n")
        if title.strip():
            sections[title.strip()] = content.strip()
    if not sections:
        sections["Overview"] = body.strip()

    meta = meta or {}
    meta.setdefault("id", path.stem)
    meta.setdefault("title", meta["id"])
    meta.setdefault("error_codes", [])
    return {"meta": meta, "sections": sections, "source": path.name}


class RunbookStore:
    COLLECTION = "runbooks"

    def __init__(self, runbooks_dir: Path, chroma_path: Path) -> None:
        self.dir = Path(runbooks_dir)
        chroma_path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(chroma_path))
        self.fingerprint_file = chroma_path / ".seed_fingerprint"
        self._parsed: dict[str, dict] = {}
        self._load_and_seed()

    # ------------------------------------------------------------------ seed

    def _fingerprint(self) -> str:
        digest = hashlib.sha256()
        for path in sorted(self.dir.glob("*.md")):
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()

    def _load_and_seed(self) -> None:
        docs: list[str] = []
        metadatas: list[dict] = []
        ids: list[str] = []

        for path in sorted(self.dir.glob("*.md")):
            parsed = parse_runbook(path)
            meta = parsed["meta"]
            rb_id = str(meta["id"])
            self._parsed[rb_id] = parsed
            codes_csv = ",".join(str(c) for c in meta.get("error_codes", []))
            for section, content in parsed["sections"].items():
                docs.append(
                    f"Runbook {rb_id}: {meta['title']}\n"
                    f"error codes: {codes_csv}\n{meta.get('summary', '')}\n\n"
                    f"## {section}\n{content}"
                )
                metadatas.append({
                    "rb_id": rb_id,
                    "title": str(meta["title"]),
                    "error_codes": codes_csv,
                    "severity": str(meta.get("severity", "SEV-3")),
                    "fix_action": str(meta.get("fix_action", "manual")),
                    "fix_command": str(meta.get("fix_command", "")),
                    "risk": str(meta.get("risk", "unknown")),
                    "section": section,
                })
                ids.append(f"{rb_id}:{section}")

        current = self._fingerprint()
        stored = ""
        if self.fingerprint_file.exists():
            stored = self.fingerprint_file.read_text().strip()
        collection = self.client.get_or_create_collection(
            self.COLLECTION, metadata={"hnsw:space": "cosine"}
        )
        if stored == current and collection.count() == len(ids) and ids:
            log.info("runbook collection up to date (%d sections)", collection.count())
            return

        try:
            self.client.delete_collection(self.COLLECTION)
        except Exception:
            pass
        collection = self.client.get_or_create_collection(
            self.COLLECTION, metadata={"hnsw:space": "cosine"}
        )
        if ids:
            collection.add(documents=docs, metadatas=metadatas, ids=ids)
        self.fingerprint_file.write_text(current)
        log.info("seeded runbook collection: %d runbooks, %d sections", len(self._parsed), len(ids))

    # --------------------------------------------------------------- retrieve

    def query(self, error_code: str, context: str = "") -> dict | None:
        """Exact error-code match wins; otherwise best semantic match."""
        collection = self.client.get_or_create_collection(
            self.COLLECTION, metadata={"hnsw:space": "cosine"}
        )
        if collection.count() == 0:
            return None
        query_text = f"{error_code} {context}"[:800]
        with _query_lock:
            result = collection.query(query_texts=[query_text], n_results=min(5, collection.count()))
        documents = result["documents"][0]
        metadatas = result["metadatas"][0]
        distances = result["distances"][0]

        exact = [
            i for i, m in enumerate(metadatas)
            if error_code in (m.get("error_codes") or "").split(",")
        ]
        idx = exact[0] if exact else 0
        meta = metadatas[idx]
        rb_id = meta["rb_id"]
        parsed = self._parsed.get(rb_id, {})
        resolution = (parsed.get("sections", {}).get("Resolution", "") or "")[:400]

        return {
            "rb_id": rb_id,
            "title": meta["title"],
            "summary": (parsed.get("meta", {}).get("summary", "")),
            "error_codes": [c for c in (meta.get("error_codes") or "").split(",") if c],
            "severity": meta["severity"],
            "fix_action": meta["fix_action"],
            "fix_command": meta["fix_command"],
            "risk": meta["risk"],
            "resolution": resolution,
            "snippet": documents[idx][:400],
            "distance": float(distances[idx]),
            "exact_match": bool(exact),
        }
