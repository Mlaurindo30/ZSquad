"""Persistent two-phase SDD dispatch queue.

Receipts are integrity evidence inside the local filesystem ACL trust boundary;
they are not cryptographic proof of a human or model identity.
"""
from __future__ import annotations

import hashlib, json, os, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


class FileSDDDispatcher:
    """Atomic outbox with idempotent enqueue, exclusive claim and execution ack."""

    fields = ("work_id", "stage", "persona", "briefing_sha256")

    def enqueue(self, root: Path, payload: dict[str, Any]) -> dict[str, Any]:
        identity = "\0".join(str(payload[k]) for k in self.fields)
        request_id = "SDD-" + hashlib.sha256(identity.encode()).hexdigest()[:32]
        request = {"request_id": request_id, **payload}
        request_path = root / "requests" / f"{request_id}.json"
        receipt_path = root / "queue-receipts" / f"{request_id}.json"
        request_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        encoded = _bytes(request)
        if request_path.exists() and request_path.read_bytes() != encoded:
            raise ValueError("idempotency conflict")
        if not request_path.exists():
            self._write(request_path, encoded)
        request_ref = request_path.relative_to(root.parent).as_posix()
        receipt = {
            "receipt_id": request_id,
            "status": "accepted",
            "accepted_at": _now(),
            "request_ref": request_ref,
            "request_sha256": hashlib.sha256(encoded).hexdigest(),
        }
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        else:
            self._write(receipt_path, _bytes(receipt))
        return self.verify(root, receipt, expected=payload)

    def claim(self, root: Path, request_id: str, consumer: str) -> dict[str, Any]:
        request = self._request(root, request_id)
        path = root / "claims" / f"{request_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        claim = {"request_id": request_id, "consumer": consumer, "status": "claimed",
                 "claimed_at": _now(), "request_sha256": hashlib.sha256(_bytes(request)).hexdigest()}
        if path.exists():
            old = json.loads(path.read_text(encoding="utf-8"))
            if old.get("consumer") != consumer or old.get("request_sha256") != claim["request_sha256"]:
                raise ValueError("request already claimed by another consumer")
            return old
        self._write(path, _bytes(claim))
        return claim

    def ack(self, root: Path, request_id: str, consumer: str, result: dict[str, Any]) -> dict[str, Any]:
        if result.get("status") != "completed" or not result.get("result_ref"):
            raise ValueError("execution ack requires status=completed and result_ref")
        request = self._request(root, request_id)
        claim = self.claim(root, request_id, consumer)
        receipt = {"receipt_id": f"EXEC-{request_id}", "request_id": request_id,
                   "status": "executed", "consumer": consumer, "executed_at": _now(),
                   "request_sha256": claim["request_sha256"], "result": result,
                   "trust_boundary": "local-filesystem-acl", **{k: request[k] for k in self.fields}}
        path = root / "execution-receipts" / f"{request_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            receipt = json.loads(path.read_text(encoding="utf-8"))
        else:
            self._write(path, _bytes(receipt))
        return self.verify_execution(root, receipt, expected=request)

    def verify(self, root: Path, supplied: dict[str, Any], *, expected: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = str(supplied.get("receipt_id") or supplied.get("request_id") or "")
        canonical_path = root / "queue-receipts" / f"{request_id}.json"
        if not canonical_path.is_file():
            raise ValueError("canonical queue receipt missing")
        receipt = json.loads(canonical_path.read_text(encoding="utf-8"))
        if receipt != supplied:
            raise ValueError("supplied queue receipt differs from canonical receipt")
        request = self._request(root, request_id)
        if receipt.get("status") != "accepted" or receipt.get("receipt_id") != request_id:
            raise ValueError("invalid queue receipt")
        if receipt.get("request_sha256") != hashlib.sha256(_bytes(request)).hexdigest():
            raise ValueError("queue receipt hash mismatch")
        if expected is not None:
            self._bindings(receipt, request, expected)
        return receipt

    def verify_execution(self, root: Path, supplied: dict[str, Any], *, expected: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = str(supplied.get("request_id", ""))
        path = root / "execution-receipts" / f"{request_id}.json"
        if not path.is_file():
            raise ValueError("canonical execution receipt missing")
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if receipt != supplied or receipt.get("status") != "executed" or receipt.get("receipt_id") != f"EXEC-{request_id}":
            raise ValueError("invalid canonical execution receipt")
        request = self._request(root, request_id)
        if receipt.get("request_sha256") != hashlib.sha256(_bytes(request)).hexdigest():
            raise ValueError("execution receipt hash mismatch")
        if expected is not None:
            self._bindings(receipt, request, expected)
        return receipt

    def _bindings(self, receipt: dict[str, Any], request: dict[str, Any], expected: dict[str, Any]) -> None:
        for field in self.fields:
            if field in expected and request.get(field) != expected.get(field):
                raise ValueError(f"receipt binding mismatch: {field}")

    @staticmethod
    def _request(root: Path, request_id: str) -> dict[str, Any]:
        if not request_id or Path(request_id).name != request_id:
            raise ValueError("invalid request id")
        path = root / "requests" / f"{request_id}.json"
        if not path.is_file(): raise ValueError("dispatch request missing")
        request = json.loads(path.read_text(encoding="utf-8"))
        if request.get("request_id") != request_id: raise ValueError("request identity mismatch")
        return request

    @staticmethod
    def _write(path: Path, content: bytes) -> None:
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temp.open("xb") as stream:
                stream.write(content); stream.flush(); os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            if temp.exists(): temp.unlink()
