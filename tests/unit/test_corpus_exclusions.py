"""The corpus is kept apart from the native cases — docs/incident-corpus.md § Amendment 2026-10-24."""
from __future__ import annotations

import json

import pytest
import yaml

from tools import corpus_exclusions as ce

POD = {"apiVersion": "v1", "kind": "Pod",
       "metadata": {"name": "payment-service-6d8f9b7c4-xk9p2", "namespace": "production"},
       "spec": {"containers": [{"name": "app", "envFrom": [{"secretRef": {"name": "payment-db-secret"}}]}]}}
EVENT = {"kind": "Event", "metadata": {"name": "e1", "namespace": "production"},
         "message": 'Error: secret "payment-db-secret" not found in the payment namespace',
         "involvedObject": {"kind": "Pod", "name": "payment-service-6d8f9b7c4-xk9p2"}}
KAFKA = {"kind": "StatefulSet", "metadata": {"name": "kafka", "namespace": "data"}}


@pytest.fixture
def repo(tmp_path):
    native = tmp_path / "native"
    (native / "h001_crashloopbackoff/kube").mkdir(parents=True)
    (native / "h001_crashloopbackoff/kube/pod.yaml").write_text(yaml.safe_dump_all([POD, EVENT]))
    (native / "h001_crashloopbackoff/expect.json").write_text(json.dumps({"query": "cache-service is down"}))
    (native / "h011_statefulset_pvc_stuck/kube").mkdir(parents=True)
    (native / "h011_statefulset_pvc_stuck/kube/sts.yaml").write_text(yaml.safe_dump(KAFKA))
    return tmp_path


def _source(repo, name, data):
    d = repo / "legacy" / name
    d.mkdir(parents=True)
    (d / "input.json").write_text(json.dumps(data))
    return d


def _check(repo, name, data):
    return ce.check_source(_source(repo, name, data), ce.native_facts(repo / "native"))


def test_the_pod_prefix():
    assert ce.pod_prefix("payment-service-6d8f9b7c4-xk9p2") == "payment-service"
    assert ce.pod_prefix("payment-service-6d8f9b7c4") == "payment-service"
    assert ce.pod_prefix("kafka-2") == "kafka"
    assert ce.pod_prefix("coredns-787d4945fb-8xp2k") == "coredns"
    assert ce.pod_prefix("api-gateway") == "api-gateway"          # no hash: unchanged


def test_a_sound_source_is_kept(repo):                                              # control
    v = _check(repo, "021_cache", {"namespace": "production", "events": [
        {"object": "Pod/cache-service-7c9d8b6f5-ab12c", "message": "Back-off restarting failed container cache"}]})
    assert v.kept, v


def test_a_source_with_the_name_of_a_native_case_is_excluded(repo):
    v = _check(repo, "001_crashloopbackoff", {"namespace": "production"})
    assert not v.kept and v.rule.startswith("1")


@pytest.mark.parametrize("data", [
    {"events": [{"object": "Pod/payment-service-55c4d8f9b-zz9q1"}]},           # pod prefix
    {"helm_drift": {"release": "payment-service"}},                            # a workload by release
    {"spec": {"envFrom": [{"secretRef": {"name": "payment-db-secret"}}]}},     # a Secret
    {"anchors": ["Pod/production/payment-service-55c4d8f9b-zz9q1: x [schema]"]},
])
def test_a_source_naming_a_native_resource_is_excluded(repo, data):
    v = _check(repo, "021_x", data)
    assert not v.kept and v.rule == "2 resource" and "h001" in v.match


def test_a_generic_namespace_does_not_exclude_but_a_particular_one_does(repo):
    assert _check(repo, "021_x", {"namespace": "production"}).kept                 # control
    v = _check(repo, "022_y", {"namespace": "data"})
    assert not v.kept and v.rule == "2 namespace" and "h011" in v.match


def test_a_verbatim_event_message_excludes(repo):
    v = _check(repo, "021_x", {"events": [{"message": 'Error: secret "payment-db-secret" not found in the payment namespace'}]})
    assert not v.kept and v.rule == "3 verbatim line"
    short = _check(repo, "022_y", {"events": [{"message": "not found"}]})        # too short to count
    assert short.kept


def test_the_source_rule_reads_structured_fields_not_prose(repo):
    # a symptom in prose naming the workload is not read from the source; the record is checked whole
    assert _check(repo, "021_x", {"symptom": "payment-service is mentioned in the notes"}).kept


def _record(repo, name, **fields):
    d = repo / "records"
    d.mkdir(exist_ok=True)
    rec = {"id": name, "title": "a cache crash", "family": "crashloop",
           "source": {"kind": "legacy-case", "ref": "cases/021_cache"},
           "symptoms": ["cache-store pods restart every few minutes", "clients time out"],
           "root_cause": "the cache runs out of memory", "cause_location": "production/Deployment/cache-store",
           "remediation": ["kubectl -n production set resources deployment/cache-store --limits=memory=1Gi"],
           **fields}
    (d / f"{name}.yaml").write_text(yaml.safe_dump(rec))
    return d / f"{name}.yaml"


def _check_record(repo, path, source_kept=True):
    sources = {"cases/021_cache": ce.Verdict("cases/021_cache", source_kept)}
    return ce.check_record(path, ce.native_facts(repo / "native"), sources)


def test_a_sound_record_is_kept(repo):                                              # control
    assert _check_record(repo, _record(repo, "inc-0001")).kept


@pytest.mark.parametrize("fields, rule", [
    ({"symptoms": ["payment-service-6d8f9b7c4-xk9p2 restarts", "x"]}, "2 resource"),
    ({"remediation": ["kubectl rollout restart deployment/payment-service"]}, "2 resource"),
    ({"symptoms": ["lookup payment-service.production.svc.cluster.local: i/o timeout", "x"]}, "2 resource"),
    ({"cause_location": "data/StatefulSet/broker"}, "2 namespace"),
    ({"remediation": ["kubectl -n data delete pod broker-0"]}, "2 namespace"),
    ({"evidence": ['Error: secret "payment-db-secret" not found in the payment namespace']}, "2 resource"),
    ({"source": {"kind": "legacy-case", "ref": "tests/integration/cases/h001_crashloopbackoff"}}, "4 source in the native cases"),
])
def test_a_record_that_leaks_a_native_case_is_excluded(repo, fields, rule):
    v = _check_record(repo, _record(repo, "inc-0002", **fields))
    assert not v.kept and v.rule == rule, v


def test_a_record_reusing_a_native_line_is_excluded(repo):
    # the line names no resource of the case itself here: rule 3 alone
    (repo / "native/h001_crashloopbackoff/kube/ev2.yaml").write_text(yaml.safe_dump(
        {"kind": "Event", "metadata": {"name": "e2"}, "message": "Readiness probe failed: HTTP probe failed with statuscode: 503"}))
    v = _check_record(repo, _record(repo, "inc-0003", symptoms=["Readiness probe failed: HTTP probe failed with statuscode: 503", "x"]))
    assert not v.kept and v.rule == "3 verbatim line"


def test_a_short_name_does_not_match_inside_a_longer_one(repo):
    (repo / "native/h001_crashloopbackoff/kube/api.yaml").write_text(yaml.safe_dump(
        {"kind": "Deployment", "metadata": {"name": "api", "namespace": "production"}}))
    rec = _record(repo, "inc-0004", symptoms=["the api-frontend gateway returns 502", "x"])
    assert _check_record(repo, rec).kept                                            # control
    rec = _record(repo, "inc-0005", symptoms=["the api pods return 502", "x"])
    assert not _check_record(repo, rec).kept


def test_a_record_from_an_excluded_source_stays_excluded_once_renamed(repo):
    v = _check_record(repo, _record(repo, "inc-0006"), source_kept=False)
    assert not v.kept and v.rule.startswith("source excluded")


def test_the_cli_fails_on_an_excluded_record(repo):
    _source(repo, "021_cache", {"namespace": "production"})
    _record(repo, "inc-0007", remediation=["kubectl rollout restart deployment/payment-service"])
    sources, records = ce.run(repo / "native", repo / "legacy", repo / "records")
    assert [v.kept for v in sources] == [True] and [v.kept for v in records] == [False]
    assert "## Records (0 kept of 1)" in ce.render(sources, records)
