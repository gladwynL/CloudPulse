"""Read-only endpoints added for the dashboard: overview, global rule list, name search, active
incident filter (PostgreSQL)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.models.enums import AlertRuleType, AlertSeverity
from app.models.resource import ResourceStatus as S
from tests.helpers import feed, incidents, insert_resource, make_rule

pytestmark = pytest.mark.integration

Factory = sessionmaker[Session]
API = "/api/v1"
X, H = S.DOWN, S.HEALTHY


@pytest.fixture
def api(pg_client: TestClient) -> TestClient:
    return pg_client


# --- Overview -------------------------------------------------------------------------------


def test_overview_of_an_empty_system_is_all_zeros(api: TestClient):
    body = api.get(f"{API}/overview").json()
    assert body == {
        "resources": {
            "total": 0,
            "enabled": 0,
            "disabled": 0,
            "by_status": {"UNKNOWN": 0, "HEALTHY": 0, "DEGRADED": 0, "DOWN": 0},
        },
        "alert_rules": {"total": 0, "enabled": 0, "firing": 0},
        "incidents": {"open": 0, "acknowledged": 0, "active": 0},
        "limits": {"max_rules_per_resource": 20},
    }


def test_overview_counts_are_exact_beyond_one_page(api: TestClient, session_factory: Factory):
    # 230 resources: more than the 200-row page cap, so any page-based count would be wrong.
    for index in range(230):
        status = (S.HEALTHY, S.DEGRADED, S.DOWN, S.UNKNOWN, S.HEALTHY)[index % 5]
        insert_resource(session_factory, name=f"r{index}", status=status, enabled=index % 10 != 0)

    resources = api.get(f"{API}/overview").json()["resources"]

    assert resources["total"] == 230
    assert resources["by_status"] == {"UNKNOWN": 46, "HEALTHY": 92, "DEGRADED": 46, "DOWN": 46}
    assert sum(resources["by_status"].values()) == 230
    assert (resources["enabled"], resources["disabled"]) == (207, 23)


def test_overview_tracks_rules_and_incidents_as_they_change(
    api: TestClient, session_factory: Factory
):
    one = insert_resource(session_factory, name="one")
    two = insert_resource(session_factory, name="two")
    firing = make_rule(session_factory, one, AlertRuleType.STATUS, name="down")
    make_rule(session_factory, one, AlertRuleType.CONSECUTIVE_FAILURES, name="3x")
    make_rule(session_factory, two, AlertRuleType.STATUS, name="off", enabled=False)
    feed(session_factory, one, X)  # the STATUS rule fires, one incident opens

    overview = api.get(f"{API}/overview").json()
    assert overview["alert_rules"] == {"total": 3, "enabled": 2, "firing": 1}
    assert overview["incidents"] == {"open": 1, "acknowledged": 0, "active": 1}
    assert overview["resources"]["by_status"]["DOWN"] == 1

    (incident,) = incidents(session_factory, rule_id=firing)
    api.post(f"{API}/incidents/{incident.id}/acknowledge")
    assert api.get(f"{API}/overview").json()["incidents"] == {
        "open": 0,
        "acknowledged": 1,
        "active": 1,
    }

    feed(session_factory, one, H)  # recovers
    after = api.get(f"{API}/overview").json()
    assert after["alert_rules"]["firing"] == 0
    assert after["incidents"] == {"open": 0, "acknowledged": 0, "active": 0}


def test_overview_exposes_the_configured_rule_limit(api: TestClient):
    from app.core.config import Settings

    api.app.state.settings = Settings(_env_file=None, alerting_max_rules_per_resource=7)
    assert api.get(f"{API}/overview").json()["limits"] == {"max_rules_per_resource": 7}


# --- Global alert-rule list -----------------------------------------------------------------


def test_global_rule_list_includes_resource_names_and_filters(
    api: TestClient, session_factory: Factory
):
    billing = insert_resource(session_factory, name="Billing")
    orders = insert_resource(session_factory, name="Orders")
    make_rule(
        session_factory,
        billing,
        AlertRuleType.STATUS,
        name="b-down",
        severity=AlertSeverity.CRITICAL,
    )
    make_rule(session_factory, orders, AlertRuleType.CONSECUTIVE_FAILURES, name="o-3x")
    make_rule(session_factory, orders, AlertRuleType.STATUS, name="o-off", enabled=False)
    feed(session_factory, billing, X)  # b-down fires

    everything = api.get(f"{API}/alert-rules").json()
    assert [(r["name"], r["resource_name"]) for r in everything["items"]] == [
        ("b-down", "Billing"),
        ("o-3x", "Orders"),
        ("o-off", "Orders"),
    ]
    assert everything["total"] == 3 and everything["limit"] == 50 and everything["offset"] == 0
    first = everything["items"][0]
    assert first["current_state"] == "FIRING" and first["resource_id"] == str(billing)
    assert {"failure_streak", "recovery_streak", "last_evaluated_at"} <= first.keys()

    def names(query: str) -> list[str]:
        return [r["name"] for r in api.get(f"{API}/alert-rules?{query}").json()["items"]]

    assert names("current_state=FIRING") == ["b-down"]
    assert names("severity=CRITICAL") == ["b-down"]
    assert names("is_enabled=false") == ["o-off"]
    assert names(f"resource_id={orders}") == ["o-3x", "o-off"]
    assert names(f"resource_id={orders}&is_enabled=true") == ["o-3x"]
    assert names("current_state=OK&severity=CRITICAL") == []


def test_global_rule_list_is_paginated_with_a_stable_total(
    api: TestClient, session_factory: Factory
):
    rid = insert_resource(session_factory, name="r")
    for index in range(5):
        make_rule(session_factory, rid, AlertRuleType.STATUS, name=f"r{index}")

    pages = [api.get(f"{API}/alert-rules?limit=2&offset={o}").json() for o in (0, 2, 4)]

    assert [p["total"] for p in pages] == [5, 5, 5]
    assert [r["name"] for p in pages for r in p["items"]] == [f"r{i}" for i in range(5)]
    assert api.get(f"{API}/alert-rules").json()["total"] == 5


def test_global_rule_list_of_nothing_is_empty(api: TestClient):
    assert api.get(f"{API}/alert-rules").json() == {
        "items": [],
        "total": 0,
        "limit": 50,
        "offset": 0,
    }


# --- Resource name search -------------------------------------------------------------------


def test_resource_search_is_a_case_insensitive_substring_match(
    api: TestClient, session_factory: Factory
):
    for name in ("Billing API", "billing worker", "Orders", "Payments"):
        insert_resource(session_factory, name=name)

    def search(q: str) -> list[str]:
        return [r["name"] for r in api.get(f"{API}/resources", params={"q": q}).json()["items"]]

    assert search("billing") == ["Billing API", "billing worker"]
    assert search("API") == ["Billing API"]
    assert search("ORDERS") == ["Orders"]
    assert search("ment") == ["Payments"]
    assert search("  billing  ") == ["Billing API", "billing worker"]  # trimmed
    assert search("nothing-like-this") == []
    assert len(search("")) == 4  # blank means no filter


def test_resource_search_treats_wildcards_literally(api: TestClient, session_factory: Factory):
    for name in ("100% uptime", "under_score", "plain"):
        insert_resource(session_factory, name=name)

    def search(q: str) -> list[str]:
        return [r["name"] for r in api.get(f"{API}/resources", params={"q": q}).json()["items"]]

    assert search("%") == ["100% uptime"]
    assert search("_") == ["under_score"]
    assert search("\\") == []  # a backslash is just a character, not an escape


def test_resource_search_combines_with_filters_and_reports_the_filtered_total(
    api: TestClient, session_factory: Factory
):
    insert_resource(session_factory, name="web-1", status=S.DOWN)
    insert_resource(session_factory, name="web-2", status=S.HEALTHY)
    insert_resource(session_factory, name="db-1", status=S.DOWN)

    body = api.get(f"{API}/resources", params={"q": "web", "current_status": "DOWN"}).json()

    assert [r["name"] for r in body["items"]] == ["web-1"]
    assert body["total"] == 1


def test_resource_search_rejects_an_overlong_term(api: TestClient):
    assert api.get(f"{API}/resources", params={"q": "x" * 101}).status_code == 422


# --- Active incident filter -----------------------------------------------------------------


def test_active_filter_selects_open_and_acknowledged_incidents(
    api: TestClient, session_factory: Factory
):
    for name in ("a", "b", "c"):
        rid = insert_resource(session_factory, name=name)
        make_rule(session_factory, rid, AlertRuleType.STATUS, name=f"rule-{name}")
        feed(session_factory, rid, X)
    by_resource = {i.resource_name: i for i in incidents(session_factory)}
    api.post(f"{API}/incidents/{by_resource['b'].id}/acknowledge")
    feed(session_factory, by_resource["c"].resource_id, H)  # c resolves

    def titles(query: str) -> set[str]:
        return {i["resource_name"] for i in api.get(f"{API}/incidents?{query}").json()["items"]}

    assert titles("active=true") == {"a", "b"}
    assert titles("active=false") == {"c"}
    assert titles("status=RESOLVED") == {"c"}
    assert titles("") == {"a", "b", "c"}
    assert titles("active=true&status=ACKNOWLEDGED") == {"b"}
    assert titles("active=true&severity=WARNING") == {"a", "b"}


@pytest.mark.parametrize(
    "query", ["active=true&status=RESOLVED", "active=false&status=OPEN", "active=maybe"]
)
def test_contradictory_active_filters_are_rejected(api: TestClient, query: str):
    response = api.get(f"{API}/incidents?{query}")
    assert response.status_code == 422
    assert all(e["loc"][0] == "query" for e in response.json()["detail"])
