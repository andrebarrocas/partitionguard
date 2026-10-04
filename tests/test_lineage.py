import pytest

from partitionguard.lineage import OpenLineageProvider
from partitionguard.models import Dependency
from partitionguard.planning import DependencyGraph


def event():
    return {
        "eventType": "COMPLETE",
        "eventTime": "2026-01-01T12:00:00Z",
        "producer": "test",
        "run": {"runId": "123"},
        "job": {"namespace": "jobs", "name": "aggregate"},
        "inputs": [{"namespace": "warehouse", "name": "events"}],
        "outputs": [{"namespace": "warehouse", "name": "summary"}],
    }


def test_observed_edges_provenance_and_mapping():
    result = OpenLineageProvider().ingest([event(), event()], {"warehouse/events": "events"})
    graph = DependencyGraph(tuple(Dependency.model_validate(v) for v in result["dependencies"]))
    impact = graph.impact("events")
    assert impact.direct == ("job:jobs/aggregate",)
    assert impact.transitive == ("warehouse/summary",)
    assert len(result["provenance"]) == 2 and result["complete"] is False


@pytest.mark.parametrize("patch", [{"eventType": "FAIL"}, {"inputs": []}, {"job": {"name": "bad"}}])
def test_invalid_or_incomplete_events_fail(patch):
    e = event()
    e.update(patch)
    with pytest.raises(ValueError):
        OpenLineageProvider().ingest(e)


def test_namespace_and_name_collision_does_not_invent_edges():
    e = event()
    e["inputs"] = [{"namespace": "a/b", "name": "c"}, {"namespace": "a", "name": "b/c"}]
    with pytest.raises(ValueError, match="Ambiguous"):
        OpenLineageProvider().ingest(e)


def test_dataset_cannot_become_its_job_via_mapping():
    with pytest.raises(ValueError, match="collides"):
        OpenLineageProvider().ingest(event(), {"warehouse/events": "job:jobs/aggregate"})


@pytest.mark.parametrize(
    "patch",
    [
        {"run": None},
        {"run": {"runId": ""}},
        {"producer": None},
        {"eventTime": "2026-01-01"},
        {"eventTime": None},
        {"inputs": None},
    ],
)
def test_provenance_and_array_shapes_are_required(patch):
    e = event()
    e.update(patch)
    with pytest.raises(ValueError):
        OpenLineageProvider().ingest(e)


def test_one_large_event_cannot_bypass_import_cap():
    e = event()
    e["inputs"] = e["inputs"] * 100000
    with pytest.raises(ValueError, match="observation bound"):
        OpenLineageProvider().ingest(e)


def test_mapping_values_must_be_nonempty_strings():
    with pytest.raises(ValueError, match="mapping"):
        OpenLineageProvider().ingest(event(), {"warehouse/events": []})
