"""Rule/incident REST APIs and what configuration changes do to active incidents (PostgreSQL)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.models.alert import AlertRule, Incident, IncidentEvent
from app.models.enums import (
    AlertRuleType,
    AlertSeverity,
    AlertState,
    IncidentEventType,
    IncidentStatus,
    ResolutionReason,
)
from app.models.resource import ResourceStatus as S
from tests.helpers import (
    events,
    feed,
    feed_all,
    fetch_rule,
    incidents,
    insert_resource,
    make_rule,
)

pytestmark = pytest.mark.integration

Factory = sessionmaker[Session]
X, H = S.DOWN, S.HEALTHY
API = "/api/v1"
STATUS_RULE = {"name": "Down", "rule_type": "STATUS", "status_condition": "DOWN"}


@pytest.fixture
def api(pg_client: TestClient) -> TestClient:
    return pg_client


def new_resource(api: TestClient, name: str = "svc") -> str:
    response = api.post(
        f"{API}/resources", json={"name": name, "url": "https://example.com/health"}
    )
    assert response.status_code == 201
    return response.json()["id"]


def new_rule(api: TestClient, resource_id: str, **body) -> dict:
    response = api.post(f"{API}/resources/{resource_id}/alert-rules", json={**STATUS_RULE, **body})
    assert response.status_code == 201, response.text
    return response.json()


def open_incident_for(factory: Factory, **rule_kwargs) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A resource with one rule and one OPEN incident. Returns (resource, rule, incident) ids."""
    rid = insert_resource(factory, name="Orders")
    rule_id = make_rule(factory, rid, AlertRuleType.STATUS, name="Orders down", **rule_kwargs)
    feed(factory, rid, X)
    (incident,) = incidents(factory)
    return rid, rule_id, incident.id


# --- Rule CRUD ------------------------------------------------------------------------------


def test_create_rule_returns_a_fresh_rule_with_defaults(api: TestClient):
    rid = new_resource(api)
    response = api.post(f"{API}/resources/{rid}/alert-rules", json=STATUS_RULE)

    assert response.status_code == 201
    rule = response.json()
    assert response.headers["location"] == f"/api/v1/alert-rules/{rule['id']}"
    assert rule["resource_id"] == rid and rule["rule_type"] == "STATUS"
    assert rule["status_condition"] == "DOWN" and rule["severity"] == "WARNING"
    assert rule["is_enabled"] is True and rule["recovery_successes"] == 1
    # Starts blank: nothing is replayed from history.
    assert (rule["current_state"], rule["failure_streak"], rule["recovery_streak"]) == (
        "INSUFFICIENT_DATA",
        0,
        0,
    )
    assert rule["last_evaluated_at"] is None and rule["last_triggered_at"] is None
    assert rule["consecutive_failures"] is None and rule["threshold"] is None
    assert api.get(f"{API}/alert-rules/{rule['id']}").json() == rule


def test_each_rule_type_round_trips_its_configuration(api: TestClient):
    rid = new_resource(api)
    failures = new_rule(
        api,
        rid,
        name="f",
        rule_type="CONSECUTIVE_FAILURES",
        status_condition=None,
        consecutive_failures=4,
        recovery_successes=2,
        severity="CRITICAL",
    )
    latency = new_rule(
        api,
        rid,
        name="l",
        rule_type="LATENCY_THRESHOLD",
        status_condition=None,
        operator="GREATER_THAN_OR_EQUAL",
        threshold=1500.5,
        consecutive_failures=3,
    )
    assert (failures["consecutive_failures"], failures["recovery_successes"]) == (4, 2)
    assert failures["severity"] == "CRITICAL" and failures["threshold"] is None
    assert (latency["operator"], latency["threshold"]) == ("GREATER_THAN_OR_EQUAL", 1500.5)
    assert latency["status_condition"] is None


def test_creating_a_rule_for_an_unknown_resource_is_404(api: TestClient):
    response = api.post(f"{API}/resources/{uuid.uuid4()}/alert-rules", json=STATUS_RULE)
    assert response.status_code == 404
    assert response.json() == {"detail": "Resource not found"}
    assert api.get(f"{API}/resources/{uuid.uuid4()}/alert-rules").status_code == 404


def test_unknown_rule_is_404_for_every_operation(api: TestClient):
    rule = uuid.uuid4()
    assert api.get(f"{API}/alert-rules/{rule}").json() == {"detail": "Alert rule not found"}
    assert api.patch(f"{API}/alert-rules/{rule}", json={"name": "x"}).status_code == 404
    assert api.delete(f"{API}/alert-rules/{rule}").status_code == 404


def test_rule_list_is_paginated_in_creation_order(api: TestClient):
    rid = new_resource(api)
    ids = [new_rule(api, rid, name=f"r{i}")["id"] for i in range(5)]
    other = new_resource(api, "other")
    new_rule(api, other, name="elsewhere")

    pages = [
        api.get(f"{API}/resources/{rid}/alert-rules?limit=2&offset={o}").json() for o in (0, 2, 4)
    ]

    assert [p["total"] for p in pages] == [5, 5, 5]
    assert [i["id"] for p in pages for i in p["items"]] == ids
    assert (
        api.get(f"{API}/resources/{new_resource(api, 'empty')}/alert-rules").json()["items"] == []
    )


def test_a_resource_cannot_exceed_the_rule_cap(api: TestClient):
    api.app.state.settings = Settings(_env_file=None, alerting_max_rules_per_resource=2)
    rid = new_resource(api)
    new_rule(api, rid, name="1")
    new_rule(api, rid, name="2")
    response = api.post(f"{API}/resources/{rid}/alert-rules", json={**STATUS_RULE, "name": "3"})
    assert response.status_code == 409 and "at most 2" in response.json()["detail"]


def test_patch_updates_fields_and_validates_the_merged_configuration(api: TestClient):
    rid = new_resource(api)
    rule = new_rule(
        api, rid, name="f", rule_type="CONSECUTIVE_FAILURES", status_condition=None,
        consecutive_failures=3,
    )  # fmt: skip
    url = f"{API}/alert-rules/{rule['id']}"

    renamed = api.patch(url, json={"name": "renamed", "description": "why", "severity": "CRITICAL"})
    assert renamed.status_code == 200
    assert (renamed.json()["name"], renamed.json()["severity"]) == ("renamed", "CRITICAL")
    assert api.patch(url, json={"description": None}).json()["description"] is None

    # Fields foreign to the type, or that would empty a required field, are rejected.
    assert api.patch(url, json={"threshold": 100}).status_code == 422
    assert api.patch(url, json={"consecutive_failures": None}).status_code == 422
    assert api.patch(url, json={"status_condition": "DOWN"}).status_code == 422
    assert api.get(url).json()["consecutive_failures"] == 3


def test_delete_returns_204_then_404(api: TestClient):
    rid = new_resource(api)
    rule = new_rule(api, rid)
    assert api.delete(f"{API}/alert-rules/{rule['id']}").status_code == 204
    assert api.get(f"{API}/alert-rules/{rule['id']}").status_code == 404
    assert api.delete(f"{API}/alert-rules/{rule['id']}").status_code == 404


# --- Configuration changes vs active incidents ----------------------------------------------


def resolved_with(factory: Factory, incident_id: uuid.UUID) -> tuple[Incident, list]:
    (incident,) = [i for i in incidents(factory) if i.id == incident_id]
    return incident, [e.event_type for e in events(factory, incident_id)]


def test_disabling_a_firing_rule_closes_its_incident_and_resets_it(
    api: TestClient, session_factory: Factory
):
    rid, rule_id, incident_id = open_incident_for(session_factory)

    response = api.patch(f"{API}/alert-rules/{rule_id}", json={"is_enabled": False})

    assert response.status_code == 200
    rule = response.json()
    assert (rule["current_state"], rule["failure_streak"]) == ("INSUFFICIENT_DATA", 0)
    incident, types = resolved_with(session_factory, incident_id)
    assert incident.status is IncidentStatus.RESOLVED
    assert incident.resolution_reason is ResolutionReason.RULE_DISABLED
    assert types == [IncidentEventType.OPENED, IncidentEventType.RULE_DISABLED]

    # No longer evaluated while disabled; enabling starts fresh and can fire a *new* incident.
    feed_all(session_factory, rid, [X, X])
    assert fetch_rule(session_factory, rule_id).failure_streak == 0
    api.patch(f"{API}/alert-rules/{rule_id}", json={"is_enabled": True})
    feed(session_factory, rid, X)
    assert [i.status for i in incidents(session_factory)] == [
        IncidentStatus.RESOLVED,
        IncidentStatus.OPEN,
    ]


def test_a_material_edit_while_firing_closes_the_incident_as_reconfigured(
    api: TestClient, session_factory: Factory
):
    _, rule_id, incident_id = open_incident_for(session_factory)

    response = api.patch(f"{API}/alert-rules/{rule_id}", json={"recovery_successes": 3})

    assert response.json()["current_state"] == "INSUFFICIENT_DATA"
    incident, types = resolved_with(session_factory, incident_id)
    assert incident.resolution_reason is ResolutionReason.RULE_RECONFIGURED
    assert types == [IncidentEventType.OPENED, IncidentEventType.RULE_RECONFIGURED]


def test_a_material_edit_to_a_healthy_rule_resets_its_counters(
    api: TestClient, session_factory: Factory
):
    rid = insert_resource(session_factory)
    rule_id = make_rule(session_factory, rid, consecutive_failures=5)
    feed_all(session_factory, rid, [X, X, X])
    assert fetch_rule(session_factory, rule_id).failure_streak == 3

    api.patch(f"{API}/alert-rules/{rule_id}", json={"consecutive_failures": 4})

    rule = fetch_rule(session_factory, rule_id)
    assert (rule.current_state, rule.failure_streak) == (AlertState.INSUFFICIENT_DATA, 0)
    assert incidents(session_factory) == []


def test_edits_that_do_not_change_evaluation_leave_progress_and_incidents_alone(
    api: TestClient, session_factory: Factory
):
    _, rule_id, incident_id = open_incident_for(session_factory)

    api.patch(f"{API}/alert-rules/{rule_id}", json={"name": "Renamed", "description": "d"})
    api.patch(f"{API}/alert-rules/{rule_id}", json={"severity": "CRITICAL"})
    api.patch(f"{API}/alert-rules/{rule_id}", json={"recovery_successes": 1})  # unchanged value

    assert fetch_rule(session_factory, rule_id).current_state is AlertState.FIRING
    incident, types = resolved_with(session_factory, incident_id)
    assert incident.status is IncidentStatus.OPEN and types == [IncidentEventType.OPENED]
    # History is not rewritten: the incident keeps the name and severity it was opened with.
    assert (incident.rule_name, incident.severity) == ("Orders down", AlertSeverity.WARNING)


def test_deleting_a_firing_rule_resolves_but_keeps_the_incident(
    api: TestClient, session_factory: Factory
):
    _, rule_id, incident_id = open_incident_for(session_factory)
    api.post(f"{API}/incidents/{incident_id}/acknowledge")

    assert api.delete(f"{API}/alert-rules/{rule_id}").status_code == 204

    incident, types = resolved_with(session_factory, incident_id)
    assert incident.status is IncidentStatus.RESOLVED
    assert incident.resolution_reason is ResolutionReason.RULE_DELETED
    assert incident.alert_rule_id is None  # link severed by the database
    assert incident.rule_name == "Orders down" and incident.resource_name == "Orders"
    assert types == [
        IncidentEventType.OPENED,
        IncidentEventType.ACKNOWLEDGED,
        IncidentEventType.RULE_DELETED,
    ]
    assert incident.acknowledged_at is not None


def test_disabling_a_resource_closes_its_incidents_and_resets_its_rules(
    api: TestClient, session_factory: Factory
):
    rid, rule_id, incident_id = open_incident_for(session_factory)

    response = api.patch(f"{API}/resources/{rid}", json={"is_enabled": False})

    assert response.status_code == 200
    incident, types = resolved_with(session_factory, incident_id)
    assert incident.resolution_reason is ResolutionReason.RESOURCE_DISABLED
    assert types == [IncidentEventType.OPENED, IncidentEventType.RESOURCE_DISABLED]
    rule = fetch_rule(session_factory, rule_id)
    assert (rule.current_state, rule.failure_streak, rule.is_enabled) == (
        AlertState.INSUFFICIENT_DATA,
        0,
        True,
    )  # the rule itself stays enabled; the resource isn't being monitored


def test_deleting_a_resource_keeps_incident_history_with_snapshots(
    api: TestClient, session_factory: Factory
):
    rid, rule_id, incident_id = open_incident_for(session_factory)

    assert api.delete(f"{API}/resources/{rid}").status_code == 204

    incident, types = resolved_with(session_factory, incident_id)
    assert incident.status is IncidentStatus.RESOLVED
    assert incident.resolution_reason is ResolutionReason.RESOURCE_DELETED
    assert (incident.resource_id, incident.alert_rule_id) == (None, None)
    assert (incident.resource_name, incident.rule_name) == ("Orders", "Orders down")
    assert types == [IncidentEventType.OPENED, IncidentEventType.RESOURCE_DELETED]
    with session_factory() as session:
        assert session.get(AlertRule, rule_id) is None  # configuration is gone
    # ...but the incident and its timeline remain readable through the API.
    assert api.get(f"{API}/incidents/{incident_id}").json()["resource_name"] == "Orders"
    assert len(api.get(f"{API}/incidents/{incident_id}/events").json()["items"]) == 2


def test_interval_and_url_changes_do_not_disturb_alert_state(
    api: TestClient, session_factory: Factory
):
    rid, rule_id, incident_id = open_incident_for(session_factory)

    api.patch(f"{API}/resources/{rid}", json={"check_interval_seconds": 120})

    assert fetch_rule(session_factory, rule_id).current_state is AlertState.FIRING
    assert resolved_with(session_factory, incident_id)[0].status is IncidentStatus.OPEN


# --- Incident API ---------------------------------------------------------------------------


def test_incident_details_and_timeline(api: TestClient, session_factory: Factory):
    rid, rule_id, incident_id = open_incident_for(session_factory, severity=AlertSeverity.CRITICAL)

    body = api.get(f"{API}/incidents/{incident_id}").json()

    assert set(body) == {
        "id", "resource_id", "alert_rule_id", "resource_name", "rule_name", "rule_type", "title",
        "severity", "status", "triggered_at", "acknowledged_at", "resolved_at",
        "resolution_reason", "last_observed_at", "opened_by_check_id", "created_at", "updated_at",
    }  # fmt: skip
    assert body["status"] == "OPEN" and body["severity"] == "CRITICAL"
    assert body["resource_id"] == str(rid) and body["alert_rule_id"] == str(rule_id)
    assert body["title"] == "CRITICAL: Orders down on Orders"
    assert body["triggered_at"].endswith("Z") and body["resolved_at"] is None
    timeline = api.get(f"{API}/incidents/{incident_id}/events").json()
    assert timeline["incident_id"] == str(incident_id)
    (opened,) = timeline["items"]
    assert (opened["event_type"], opened["actor"]) == ("OPENED", "SYSTEM")
    assert "url" not in str(opened["details"]).lower()


def test_unknown_incident_is_404_everywhere(api: TestClient):
    missing = uuid.uuid4()
    for method, path in (
        ("get", f"{API}/incidents/{missing}"),
        ("post", f"{API}/incidents/{missing}/acknowledge"),
        ("get", f"{API}/incidents/{missing}/events"),
    ):
        response = getattr(api, method)(path)
        assert response.status_code == 404
        assert response.json() == {"detail": "Incident not found"}


def test_acknowledge_is_idempotent_and_never_duplicates_events(
    api: TestClient, session_factory: Factory
):
    _, _, incident_id = open_incident_for(session_factory)

    first = api.post(f"{API}/incidents/{incident_id}/acknowledge")
    again = api.post(f"{API}/incidents/{incident_id}/acknowledge")

    assert first.status_code == again.status_code == 200
    assert first.json()["status"] == "ACKNOWLEDGED"
    assert first.json()["acknowledged_at"] == again.json()["acknowledged_at"]  # not re-stamped
    assert [e.event_type for e in events(session_factory, incident_id)] == [
        IncidentEventType.OPENED,
        IncidentEventType.ACKNOWLEDGED,
    ]


def test_a_resolved_incident_cannot_be_acknowledged(api: TestClient, session_factory: Factory):
    rid, _, incident_id = open_incident_for(session_factory)
    feed(session_factory, rid, H)  # recovers

    response = api.post(f"{API}/incidents/{incident_id}/acknowledge")

    assert response.status_code == 409
    assert "resolved" in response.json()["detail"]
    assert [e.event_type for e in events(session_factory, incident_id)] == [
        IncidentEventType.OPENED,
        IncidentEventType.RECOVERED,
    ]


def seed_incidents(factory: Factory) -> dict[str, uuid.UUID]:
    """Three resources with incidents of different severity/status at known times."""
    ids: dict[str, uuid.UUID] = {}
    base = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    for index, (name, severity, status) in enumerate(
        [
            ("a", AlertSeverity.WARNING, IncidentStatus.OPEN),
            ("b", AlertSeverity.CRITICAL, IncidentStatus.ACKNOWLEDGED),
            ("c", AlertSeverity.CRITICAL, IncidentStatus.RESOLVED),
        ]
    ):
        rid = insert_resource(factory, name=name)
        rule_id = make_rule(factory, rid, name=f"rule-{name}", severity=severity)
        moment = base + timedelta(hours=index)
        with factory() as session:
            incident = Incident(
                resource_id=rid,
                alert_rule_id=rule_id if status is not IncidentStatus.RESOLVED else None,
                resource_name=name,
                rule_name=f"rule-{name}",
                rule_type=AlertRuleType.STATUS,
                title=f"t-{name}",
                severity=severity,
                status=status,
                triggered_at=moment,
                acknowledged_at=moment if status is not IncidentStatus.OPEN else None,
                resolved_at=moment + timedelta(minutes=5)
                if status is IncidentStatus.RESOLVED
                else None,
                resolution_reason=ResolutionReason.RECOVERED
                if status is IncidentStatus.RESOLVED
                else None,
                last_observed_at=moment,
            )
            session.add(incident)
            session.commit()
            ids[name] = incident.id
        ids[f"resource-{name}"] = rid
    return ids


def test_incident_list_is_newest_first_and_filterable(api: TestClient, session_factory: Factory):
    ids = seed_incidents(session_factory)

    everything = api.get(f"{API}/incidents").json()
    assert [i["title"] for i in everything["items"]] == ["t-c", "t-b", "t-a"]
    assert everything["next_cursor"] is None and everything["limit"] == 50

    def titles(query: str) -> list[str]:
        return [i["title"] for i in api.get(f"{API}/incidents?{query}").json()["items"]]

    assert titles("status=OPEN") == ["t-a"]
    assert titles("status=ACKNOWLEDGED") == ["t-b"]
    assert titles("status=RESOLVED") == ["t-c"]
    assert titles("severity=CRITICAL") == ["t-c", "t-b"]
    assert titles("severity=CRITICAL&status=ACKNOWLEDGED") == ["t-b"]
    assert titles(f"resource_id={ids['resource-a']}") == ["t-a"]
    # Window on triggered_at, [start, end): a at 12:00, b at 13:00, c at 14:00.
    assert titles("start=2026-03-10T13:00:00Z") == ["t-c", "t-b"]
    assert titles("end=2026-03-10T13:00:00Z") == ["t-a"]
    assert titles("start=2026-03-10T12:30:00Z&end=2026-03-10T14:00:00Z") == ["t-b"]
    assert titles("status=OPEN&severity=CRITICAL") == []


def test_incident_pagination_is_stable_across_equal_trigger_times(
    api: TestClient, session_factory: Factory
):
    rid = insert_resource(session_factory)
    moment = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    expected = []
    with session_factory() as session:
        for index in range(5):  # identical triggered_at: the id tie-break decides
            incident = Incident(
                resource_id=rid,
                resource_name="r",
                rule_name=f"r{index}",
                rule_type=AlertRuleType.STATUS,
                title=f"t{index}",
                severity=AlertSeverity.WARNING,
                status=IncidentStatus.RESOLVED,
                triggered_at=moment,
                resolved_at=moment,
                resolution_reason=ResolutionReason.RECOVERED,
                last_observed_at=moment,
            )
            session.add(incident)
            session.flush()
            expected.append((moment, incident.id))
        session.commit()
    expected.sort(reverse=True)

    seen, cursor = [], None
    while True:
        query = f"limit=2&cursor={cursor}" if cursor else "limit=2"
        page = api.get(f"{API}/incidents?{query}").json()
        seen += [i["id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == [str(i) for _, i in expected]


def test_incident_with_deleted_links_stays_listable(api: TestClient, session_factory: Factory):
    rid, _, incident_id = open_incident_for(session_factory)
    api.delete(f"{API}/resources/{rid}")

    items = api.get(f"{API}/incidents?status=RESOLVED").json()["items"]

    assert [i["id"] for i in items] == [str(incident_id)]
    assert items[0]["resource_id"] is None and items[0]["alert_rule_id"] is None
    assert api.get(f"{API}/incidents?resource_id={rid}").json()["items"] == []


# --- Database invariants --------------------------------------------------------------------


def raw_incident(**overrides) -> Incident:
    now = datetime(2026, 3, 10, 12, tzinfo=UTC)
    values = dict(
        resource_name="r",
        rule_name="n",
        rule_type=AlertRuleType.STATUS,
        title="t",
        severity=AlertSeverity.WARNING,
        status=IncidentStatus.OPEN,
        triggered_at=now,
        last_observed_at=now,
    )
    values.update(overrides)
    return Incident(**values)


def test_the_database_allows_only_one_active_incident_per_rule(session_factory: Factory):
    rid = insert_resource(session_factory)
    rule_id = make_rule(session_factory, rid)
    with session_factory() as session:
        session.add(raw_incident(resource_id=rid, alert_rule_id=rule_id))
        session.commit()
    with session_factory() as session:
        session.add(
            raw_incident(
                resource_id=rid,
                alert_rule_id=rule_id,
                status=IncidentStatus.ACKNOWLEDGED,
                acknowledged_at=datetime(2026, 3, 10, 12, tzinfo=UTC),
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    # Once the first is resolved, a new active one is fine (history rows are not unique).
    with session_factory() as session:
        session.execute(
            text(
                "UPDATE incidents SET status='RESOLVED', resolved_at=now(), "
                "resolution_reason='RECOVERED'"
            )
        )
        session.add(raw_incident(resource_id=rid, alert_rule_id=rule_id))
        session.commit()
    assert len(incidents(session_factory)) == 2


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": IncidentStatus.RESOLVED},  # resolved without time or reason
        {"status": IncidentStatus.ACKNOWLEDGED},  # acknowledged without a timestamp
        {"resolution_reason": ResolutionReason.RECOVERED},  # reason on an open incident
        {"acknowledged_at": datetime(2026, 3, 10, 12, tzinfo=UTC)},  # open but acknowledged
        {"title": "  "},
    ],
)
def test_incident_status_and_timestamps_must_agree(session_factory: Factory, overrides: dict):
    with session_factory() as session:
        session.add(raw_incident(**overrides))
        with pytest.raises(IntegrityError):
            session.commit()


def test_resolution_cannot_precede_the_trigger(session_factory: Factory):
    with session_factory() as session:
        session.add(
            raw_incident(
                status=IncidentStatus.RESOLVED,
                resolution_reason=ResolutionReason.RECOVERED,
                resolved_at=datetime(2026, 3, 10, 11, tzinfo=UTC),
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.parametrize(
    "overrides",
    [
        {"rule_type": AlertRuleType.STATUS, "status_condition": None},
        {"rule_type": AlertRuleType.STATUS, "consecutive_failures": 3},
        {"rule_type": AlertRuleType.CONSECUTIVE_FAILURES, "consecutive_failures": None},
        {"rule_type": AlertRuleType.CONSECUTIVE_FAILURES, "consecutive_failures": 0},
        {"rule_type": AlertRuleType.CONSECUTIVE_FAILURES, "consecutive_failures": 21},
        {"rule_type": AlertRuleType.LATENCY_THRESHOLD, "consecutive_failures": 3},
        {"recovery_successes": 0},
        {"name": " "},
        {"is_enabled": False, "current_state": AlertState.FIRING},
        {"is_enabled": False, "failure_streak": 2},
        {"current_state": AlertState.FIRING},  # firing without a trigger time
    ],
)
def test_the_database_rejects_contradictory_rule_configuration(
    session_factory: Factory, overrides: dict
):
    rid = insert_resource(session_factory)
    values = dict(
        resource_id=rid,
        name="r",
        rule_type=AlertRuleType.STATUS,
        status_condition="DOWN",
        recovery_successes=1,
    )
    values.update(overrides)
    with session_factory() as session:
        session.add(AlertRule(**values))
        with pytest.raises(IntegrityError):
            session.commit()


def test_events_belong_to_their_incident_and_rules_to_their_resource(session_factory: Factory):
    _, _, incident_id = open_incident_for(session_factory)
    with session_factory() as session:
        assert session.scalars(select(IncidentEvent)).all()
        session.execute(text("DELETE FROM incidents WHERE id = :i"), {"i": incident_id})
        session.commit()
        assert (
            session.scalars(select(IncidentEvent)).all() == []
        )  # events cascade with the incident
