"""OpenLineage-style run events for every pipeline job.

Each job (land a bronze table, conform a silver table, build a gold product, train a model) emits a
START and a COMPLETE (or FAIL) `RunEvent` with its input and output datasets, a schema facet, output
row counts and a data-quality facet from the contract checks. The events follow the OpenLineage 2-0-2
RunEvent shape, so they can be posted to Microsoft Purview, Marquez, Databricks Unity Catalog or
Google Dataplex lineage endpoints; here they are written to `out/lineage/events.jsonl`.

Run ids are UUIDv5 of the job name and the run sequence and event times are synthetic, so the file is
identical on every run.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PRODUCER = "https://github.com/jagadishmazure-jpg/Jagadish-agentic-data-layer"
SCHEMA_URL = "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent"
NAMESPACE = "wrenfield-adl"
_NS = uuid.uuid5(uuid.NAMESPACE_URL, PRODUCER)


@dataclass
class Lineage:
    dataset_namespace: str = "lake://local"
    events: list[dict[str, Any]] = field(default_factory=list)
    _seq: int = 0

    def _time(self) -> str:
        self._seq += 1
        h, rem = divmod(self._seq, 3600)
        m, s = divmod(rem, 60)
        return f"2030-01-01T{h:02d}:{m:02d}:{s:02d}Z"  # synthetic clock, not a real date

    def _dataset(self, name: str, schema: list[tuple[str, str]] | None = None, rows: int | None = None, dq: dict | None = None) -> dict:
        ds: dict[str, Any] = {"namespace": self.dataset_namespace, "name": name, "facets": {}}
        if schema:
            ds["facets"]["schema"] = {
                "_producer": PRODUCER,
                "_schemaURL": "https://openlineage.io/spec/facets/1-1-1/SchemaDatasetFacet.json",
                "fields": [{"name": n, "type": t} for n, t in schema],
            }
        if rows is not None or dq:
            ds["outputFacets"] = {}
            if rows is not None:
                ds["outputFacets"]["outputStatistics"] = {"_producer": PRODUCER, "rowCount": rows}
        if dq:
            ds["facets"]["dataQualityAssertions"] = {"_producer": PRODUCER, "assertions": dq["assertions"]}
        return ds

    def run(self, job: str, inputs: list[str], outputs: list[dict[str, Any]], status: str = "COMPLETE", facets: dict | None = None) -> str:
        run_id = str(uuid.uuid5(_NS, f"{job}#{len(self.events)}"))
        base = {"producer": PRODUCER, "schemaURL": SCHEMA_URL, "job": {"namespace": NAMESPACE, "name": job, "facets": {}}}
        self.events.append(
            {
                **base,
                "eventType": "START",
                "eventTime": self._time(),
                "run": {"runId": run_id, "facets": {}},
                "inputs": [self._dataset(i) for i in inputs],
                "outputs": [],
            }
        )
        outs = [self._dataset(o["name"], o.get("schema"), o.get("rows"), o.get("dq")) for o in outputs]
        self.events.append(
            {
                **base,
                "eventType": status,
                "eventTime": self._time(),
                "run": {"runId": run_id, "facets": facets or {}},
                "inputs": [self._dataset(i) for i in inputs],
                "outputs": outs,
            }
        )
        return run_id

    def edges(self) -> list[tuple[str, str, str]]:
        out = []
        for e in self.events:
            if e["eventType"] != "COMPLETE":
                continue
            for i in e["inputs"]:
                for o in e["outputs"]:
                    out.append((i["name"], e["job"]["name"], o["name"]))
        return out

    def upstream(self, dataset: str) -> set[str]:
        """Every dataset that `dataset` was derived from, transitively."""
        parents: dict[str, set[str]] = {}
        for i, _, o in self.edges():
            parents.setdefault(o, set()).add(i)
        seen: set[str] = set()
        todo = [dataset]
        while todo:
            for p in parents.get(todo.pop(), ()):
                if p not in seen:
                    seen.add(p)
                    todo.append(p)
        return seen

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(e, sort_keys=True) + "\n" for e in self.events))


def validate_event(e: dict[str, Any]) -> list[str]:
    """Structural check against the required RunEvent fields (a subset of the JSON schema)."""
    problems = []
    for k in ("eventType", "eventTime", "producer", "schemaURL", "run", "job", "inputs", "outputs"):
        if k not in e:
            problems.append(f"missing {k}")
    if e.get("eventType") not in {"START", "RUNNING", "COMPLETE", "ABORT", "FAIL", "OTHER"}:
        problems.append("bad eventType")
    try:
        uuid.UUID(e.get("run", {}).get("runId", ""))
    except ValueError:
        problems.append("runId is not a UUID")
    if not {"namespace", "name"} <= set(e.get("job", {})):
        problems.append("job needs namespace and name")
    for ds in e.get("inputs", []) + e.get("outputs", []):
        if not {"namespace", "name"} <= set(ds):
            problems.append("dataset needs namespace and name")
    return problems
