"""Offline OpenLineage event ingestion; observations never establish completeness."""

from __future__ import annotations

from datetime import datetime

from .models import Dependency, digest


class OpenLineageProvider:
    API_VERSION = 1

    def ingest(self, source, mapping=None):
        mapping = {} if mapping is None else mapping
        if not isinstance(mapping, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) or not key or not value
            for key, value in mapping.items()
        ):
            raise ValueError("Lineage mapping requires nonempty string identities and aliases")
        events = source if isinstance(source, list) else [source]
        if len(events) > 10000:
            raise ValueError("Event import exceeds 10000-event bound")
        nodes, provenance, raw_identities = {}, [], {}
        observations = 0

        def identity(value):
            if (
                not isinstance(value, dict)
                or not isinstance(value.get("namespace"), str)
                or not isinstance(value.get("name"), str)
            ):
                raise ValueError("OpenLineage identity requires namespace and name")
            if not value["namespace"] or not value["name"]:
                raise ValueError("Empty lineage identities are unsupported")
            key = value["namespace"] + "/" + value["name"]
            pair = (value["namespace"], value["name"])
            if key in raw_identities and raw_identities[key] != pair:
                raise ValueError(
                    "Ambiguous namespace/name concatenation; import these identities separately with unambiguous names"
                )
            raw_identities[key] = pair
            return mapping.get(key, key)

        def node(name, kind):
            if name in nodes and nodes[name]["kind"] != kind:
                raise ValueError(
                    "Dataset identity collides with a job identity; supply a distinct mapping"
                )
            if name not in nodes:
                if len(nodes) >= 100000:
                    raise ValueError("Lineage import exceeds the 100000-node bound")
                nodes[name] = {"kind": kind, "upstream": set()}
            return nodes[name]

        for event in events:
            if not isinstance(event, dict) or event.get("eventType") != "COMPLETE":
                raise ValueError("Only completed OpenLineage RunEvent observations are imported")
            run = event.get("run")
            if (
                not isinstance(run, dict)
                or not isinstance(run.get("runId"), str)
                or not run["runId"]
            ):
                raise ValueError("Completed lineage observations require a runId")
            if not isinstance(event.get("producer"), str) or not event["producer"]:
                raise ValueError("Completed lineage observations require a producer")
            try:
                observed = datetime.fromisoformat(event["eventTime"].replace("Z", "+00:00"))
                if observed.tzinfo is None:
                    raise ValueError("Missing timezone")
            except (KeyError, AttributeError, TypeError, ValueError):
                raise ValueError(
                    "Lineage eventTime must be an ISO timestamp with a timezone"
                ) from None
            if any(not isinstance(event.get(field), list) for field in ("inputs", "outputs")):
                raise ValueError("Lineage inputs and outputs must be arrays")
            observations += len(event["inputs"]) + len(event["outputs"])
            if observations > 100000:
                raise ValueError("Lineage import exceeds the 100000-dataset-observation bound")
            job = "job:" + identity(event.get("job"))
            inputs = tuple(identity(d) for d in event.get("inputs", []))
            outputs = tuple(identity(d) for d in event.get("outputs", []))
            if not inputs or not outputs:
                raise ValueError("Lineage event requires both inputs and outputs")
            for name in inputs:
                node(name, "table")
            node(job, "transformation")["upstream"].update(inputs)
            for name in outputs:
                node(name, "table")["upstream"].add(job)
            provenance.append(
                {
                    "producer": event.get("producer"),
                    "event_time": event.get("eventTime"),
                    "run_id": (event.get("run") or {}).get("runId"),
                    "event_sha256": digest(event),
                }
            )
        return {
            "dependencies": [
                Dependency(
                    name=name, kind=value["kind"], upstream=tuple(sorted(value["upstream"]))
                ).model_dump(mode="json")
                for name, value in sorted(nodes.items())
            ],
            "provenance": provenance,
            "complete": False,
            "limitation": "Completed run observations only; missing or stale consumers cannot be ruled out.",
        }
