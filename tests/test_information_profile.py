from __future__ import annotations

import json

import pytest

from sage_pass.candidate_types import CandidateSource
from sage_pass.information import build_information_profile
from sage_pass.planner import LLMPlanner
from sage_pass.schemas import PRIR, TaskContext


@pytest.mark.parametrize(
    ("context", "passwords", "scenario"),
    [
        (TaskContext(), [], "I0"),
        (TaskContext(name="张三"), [], "I1"),
        (TaskContext(), ["OldSecret!1"], "I2"),
        (TaskContext(username="alice"), ["OldSecret!1"], "I3"),
    ],
)
def test_four_information_scenarios(context, passwords, scenario):
    profile = build_information_profile(context, passwords)

    assert profile.scenario == scenario
    assert profile.has_personal_information is bool(context.model_dump(exclude={"description"}) and any(
        value for key, value in context.model_dump().items() if key != "description"
    ))
    assert profile.has_historical_passwords is bool(passwords)
    assert profile.historical_password_count == len(passwords)


def test_profile_contains_types_and_no_plaintext():
    profile = build_information_profile(
        TaskContext(
            name="张三",
            username="alice",
            email_local_part="alice.work",
            phone_suffix="7788",
            birth_year=1999,
            region="天津",
            organization="示例大学",
            interest_words=["摄影"],
            authorized_keywords=["实验室"],
        ),
        ["NeverExposeThis!1999"],
        has_pattern_knowledge=True,
    )

    serialized = profile.model_dump_json()
    assert profile.scenario == "I3"
    assert set(profile.information_types) == {
        "name", "username", "email_local_part", "phone_suffix",
        "birthday_or_year", "region", "organization", "interest_word",
        "authorized_keyword",
    }
    assert profile.has_pattern_knowledge is True
    assert "张三" not in serialized
    assert "alice" not in serialized
    assert "NeverExposeThis" not in serialized


def test_task_api_keeps_historical_passwords_out_of_responses(client, hash_task_payload):
    payload = hash_task_payload | {
        "context": {"username": "private-user"},
        "historical_passwords": ["PrivateOldPassword!"],
    }
    created = client.post("/api/tasks", json=payload)
    assert created.status_code == 201

    response = client.get(f"/api/tasks/{created.json()['task_id']}")
    body = response.json()
    serialized = json.dumps(body, ensure_ascii=False)
    assert "historical_passwords" not in body
    assert "PrivateOldPassword" not in serialized
    assert body["information_profile"]["scenario"] == "I3"
    assert body["information_profile"]["historical_password_count"] == 1


def test_historical_password_cannot_be_ordinary_keyword(client, hash_task_payload):
    payload = hash_task_payload | {
        "context": {"authorized_keywords": ["same-secret"]},
        "historical_passwords": ["same-secret"],
    }
    response = client.post("/api/tasks", json=payload)
    assert response.status_code == 422


def test_historical_passwords_are_rejected_inside_context(client, hash_task_payload):
    payload = hash_task_payload | {
        "context": {"historical_passwords": ["wrong-boundary"]},
    }
    response = client.post("/api/tasks", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["details"]["field"] == (
        "context.historical_passwords"
    )


def test_planner_receives_profile_but_no_sensitive_values():
    class Gateway:
        profile = None

        def create_plan(self, task_profile):
            self.profile = task_profile
            return json.dumps({
                "strategies": [{
                    "strategy_id": "S1", "priority": 1,
                    "time_budget": 1, "candidate_budget": 1,
                    "reason": "baseline", "parameters": {},
                }],
                "warnings": [],
            })

    information_profile = build_information_profile(
        TaskContext(name="Sensitive Name"), ["SensitiveOldPassword"]
    )
    prir = PRIR(
        task_id="T1", target_type="hash", algorithm="md5", salt=False,
        verification_cost="low", context_available=True, time_budget=10,
        candidate_budget=10, confidence=1.0,
        information_profile=information_profile,
    )
    gateway = Gateway()
    LLMPlanner(gateway, model="test").plan(prir)

    serialized = json.dumps(gateway.profile, ensure_ascii=False)
    assert gateway.profile["information_profile"]["scenario"] == "I3"
    assert "Sensitive Name" not in serialized
    assert "SensitiveOldPassword" not in serialized


def test_historical_candidate_source_is_always_redacted():
    source = CandidateSource(
        kind="historical_password",
        original="OldSecret!",
        normalized="oldsecret!",
        template="L8S1",
        components=("length:10",),
    )
    public = source.public_dict()
    assert public["original"] is None
    assert public["normalized"] is None
    assert "OldSecret" not in json.dumps(public)
