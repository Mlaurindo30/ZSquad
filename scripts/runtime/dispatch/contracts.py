"""Canonical Data Contracts & Receipts for Host-Native Specialist Dispatch (Milestone R11).

Strictly stdlib-only. Re-exports canonical contracts from receipts.py.
"""

from __future__ import annotations

from scripts.runtime.dispatch.receipts import (
    DispatchStatus,
    HostExecutionBinding,
    HostDispatchResult,
    HostStatusResult,
    compute_evidence_hash,
    mint_dispatch_receipt,
)

__all__ = [
    "DispatchStatus",
    "HostExecutionBinding",
    "HostDispatchResult",
    "HostStatusResult",
    "compute_evidence_hash",
    "mint_dispatch_receipt",
]
