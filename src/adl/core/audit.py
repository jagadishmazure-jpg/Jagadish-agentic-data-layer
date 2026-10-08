"""Hash-chained audit log of every agent read, denial, proposal, approval and dry-run execution.

Each record carries the SHA-256 of the previous one, so an edited, deleted or re-ordered record breaks
`verify`. On Azure the chain is shipped to an immutable (WORM) blob container and Log Analytics; on GCP
to a locked Cloud Storage bucket and Cloud Logging; on AWS to S3 Object Lock and CloudWatch Logs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

GENESIS = "0" * 64


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"))


@dataclass
class AuditLog:
    domain: str
    records: list[dict] = field(default_factory=list)

    def append(self, actor: str, event: str, data: dict[str, Any]) -> dict:
        prev = self.records[-1]["hash"] if self.records else GENESIS
        rec = {"seq": len(self.records) + 1, "domain": self.domain, "actor": actor, "event": event, "data": data, "prev": prev}
        rec["hash"] = hashlib.sha256(_canon(rec).encode()).hexdigest()
        self.records.append(rec)
        return rec

    def verify(self) -> tuple[bool, str]:
        prev = GENESIS
        for i, rec in enumerate(self.records, 1):
            body = {k: v for k, v in rec.items() if k != "hash"}
            if rec["seq"] != i:
                return False, f"record {i}: sequence {rec['seq']}"
            if rec["prev"] != prev:
                return False, f"record {i}: broken link"
            if hashlib.sha256(_canon(body).encode()).hexdigest() != rec["hash"]:
                return False, f"record {i}: hash mismatch"
            prev = rec["hash"]
        return True, f"{len(self.records)} records verified"

    def events(self, event: str) -> list[dict]:
        return [r for r in self.records if r["event"] == event]
