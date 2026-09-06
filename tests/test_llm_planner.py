from __future__ import annotations

import json
from types import SimpleNamespace

from sage_pass.enums import TargetType, VerificationCost
from sage_pass.planner import (
    LLMPlanner,
    OpenAIChatCompletionsGateway,
    OpenAIResponsesGateway,
)
from sage_pass.schemas import PRIR


class FakeGateway:
    def __init__(self, response: dict | Exception) -> None:
        self.response = response
        self.profiles: list[dict] = []

    def create_plan(self, task_profile: dict) -> str:
        self.profiles.append(task_profile)
        if isinstance(self.response, Exception):
            raise self.response
        return json.dumps(self.response, ensure_ascii=False)


def make_prir(task_id: str = "T001", *, context: bool = True) -> PRIR:
    return PRIR(
        task_id=task_id,
        target_type=TargetType.HASH,
        algorithm="bcrypt",
        salt=True,
        verification_cost=VerificationCost.HIGH,
        context_available=context,
        candidate_space=None,
        time_budget=100,
        candidate_budget=10_000,
        confidence=0.95,
        warnings=[],
    )


def valid_response() -> dict:
    return {
        "strategies": [
            {
                "strategy_id": "S1",
                "priority": 1,
                "time_budget": 20,
                "candidate_budget": 2_000,
                "reason": "先检查高概率口令",
                "parameters": {},
            },
            {
                "strategy_id": "S4",
                "priority": 2,
                "time_budget": 30,
                "candidate_budget": 3_000,
                "reason": "存在上下文",
                "parameters": {},
            },
        ],
        "warnings": [],
    }


def test_llm_planner_sends_only_structured_prir_and_builds_fixed_plan():
    gateway = FakeGateway(valid_response())
    planner = LLMPlanner(gateway, model="test-model")

    plan = planner.plan(make_prir())

    assert plan.planner_type == "llm"
    assert [item.strategy_id for item in plan.strategies] == ["S1", "S4"]
    assert plan.strategies[0].strategy_name == "Baseline"
    assert gateway.profiles == [
        {
            "target_type": "hash",
            "algorithm": "bcrypt",
            "salt": True,
            "verification_cost": "high",
            "context_available": True,
            "candidate_space": None,
            "time_budget": 100,
            "candidate_budget": 10_000,
            "confidence": 0.95,
            "warnings": [],
        }
    ]
    assert "task_id" not in gateway.profiles[0]


def test_llm_planner_cache_reuses_profile_but_rebinds_task_id():
    gateway = FakeGateway(valid_response())
    planner = LLMPlanner(gateway, model="test-model")

    first = planner.plan(make_prir("T001"))
    second = planner.plan(make_prir("T002"))

    assert first.task_id == "T001"
    assert second.task_id == "T002"
    assert len(gateway.profiles) == 1


def test_invalid_or_failed_llm_output_degrades_without_leaking_details():
    response = valid_response()
    response["strategies"][1]["candidate_budget"] = 20_000
    planner = LLMPlanner(FakeGateway(response), model="test-model")

    plan = planner.plan(make_prir())

    assert plan.planner_type == "mock"
    assert plan.warnings[-1] == (
        "LLM Planner 不可用，已降级为 Mock 计划（PolicyValidationError）"
    )


def test_s4_is_rejected_when_profile_has_no_context():
    planner = LLMPlanner(FakeGateway(valid_response()), model="test-model")

    plan = planner.plan(make_prir(context=False))

    assert plan.planner_type == "mock"
    assert [item.strategy_id for item in plan.strategies] == ["S1"]


def test_non_whitelisted_llm_strategy_degrades_to_mock():
    response = valid_response()
    response["strategies"] = [
        {
            "strategy_id": "S5",
            "priority": 1,
            "time_budget": 20,
            "candidate_budget": 1000,
            "reason": "not allowed in week 2",
            "parameters": {},
        }
    ]
    planner = LLMPlanner(FakeGateway(response), model="test-model")

    result = planner.plan(make_prir())

    assert result.planner_type == "mock"
    assert result.warnings[-1].endswith("（PolicyValidationError）")


def test_openai_gateway_sets_structured_output_and_generation_limits():
    calls: list[dict] = []

    class Responses:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(output_text=json.dumps(valid_response()))

    client = SimpleNamespace(responses=Responses())
    gateway = OpenAIResponsesGateway(
        api_key="not-used",
        model="test-model",
        temperature=0.1,
        timeout_seconds=7,
        max_output_tokens=321,
        client=client,
    )

    gateway.create_plan({"target_type": "hash"})

    assert calls[0]["temperature"] == 0.1
    assert calls[0]["max_output_tokens"] == 321
    assert calls[0]["store"] is False
    assert calls[0]["text"]["format"]["type"] == "json_schema"
    assert calls[0]["text"]["format"]["strict"] is True
    assert json.loads(calls[0]["input"]) == {"target_type": "hash"}


def test_chat_completions_gateway_uses_siliconflow_compatible_contract():
    calls: list[dict] = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            message = SimpleNamespace(content=json.dumps(valid_response()))
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=Completions())
    )
    gateway = OpenAIChatCompletionsGateway(
        api_key="not-used",
        model="deepseek-ai/DeepSeek-V4-Flash",
        temperature=0.1,
        timeout_seconds=7,
        max_output_tokens=321,
        base_url="https://api.siliconflow.cn/v1",
        client=client,
    )

    result = gateway.create_plan({"target_type": "hash"})

    assert json.loads(result) == valid_response()
    assert calls[0]["model"] == "deepseek-ai/DeepSeek-V4-Flash"
    assert calls[0]["temperature"] == 0.1
    assert calls[0]["max_tokens"] == 321
    assert calls[0]["stream"] is False
    assert calls[0]["messages"][0]["role"] == "system"
    assert json.loads(calls[0]["messages"][1]["content"]) == {
        "target_type": "hash"
    }
    response_format = calls[0]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
