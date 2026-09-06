from __future__ import annotations

from dataclasses import dataclass
from numbers import Real
from typing import Any, Callable

from .enums import StrategyId, TargetType, TaskStatus
from .schemas import PRIR, StrategyItem, StrategyPlan


@dataclass(frozen=True, slots=True)
class PolicyIssue:
    code: str
    message: str
    strategy_id: str | None = None
    parameter: str | None = None


class PolicyValidationError(ValueError):
    """Raised when a plan is syntactically valid but unsafe to execute."""

    def __init__(self, issues: list[PolicyIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__("; ".join(issue.message for issue in issues))


@dataclass(frozen=True, slots=True)
class ParameterRule:
    accepts: Callable[[Any, StrategyItem], bool]
    description: str


def _is_bool(value: Any, _: StrategyItem) -> bool:
    return type(value) is bool


def _bounded_int(minimum: int, maximum: int) -> ParameterRule:
    return ParameterRule(
        accepts=lambda value, _: type(value) is int and minimum <= value <= maximum,
        description=f"整数范围 {minimum}～{maximum}",
    )


def _bounded_number(minimum: float, maximum: float) -> ParameterRule:
    return ParameterRule(
        accepts=lambda value, _: (
            isinstance(value, Real)
            and not isinstance(value, bool)
            and minimum <= float(value) <= maximum
        ),
        description=f"数值范围 {minimum}～{maximum}",
    )


BOOL_RULE = ParameterRule(_is_bool, "布尔值")

STRATEGY_PARAMETER_RULES: dict[StrategyId, dict[str, ParameterRule]] = {
    StrategyId.S1: {},
    StrategyId.S2: {
        "capitalize_first": BOOL_RULE,
        "all_upper": BOOL_RULE,
        "all_lower": BOOL_RULE,
        "common_number_suffix": BOOL_RULE,
        "year_suffix": BOOL_RULE,
        "common_substitution": BOOL_RULE,
        "symbol_suffix": BOOL_RULE,
    },
    StrategyId.S3: {
        "max_templates": _bounded_int(1, 512),
        "min_probability": _bounded_number(0.0, 1.0),
        "max_structure_length": _bounded_int(1, 64),
    },
    StrategyId.S4: {
        "use_keywords": BOOL_RULE,
        "use_pinyin": BOOL_RULE,
        "use_abbreviations": BOOL_RULE,
        "use_years": BOOL_RULE,
        "use_region": BOOL_RULE,
        "use_organization": BOOL_RULE,
        "max_combinations": ParameterRule(
            accepts=lambda value, strategy: (
                type(value) is int
                and 1 <= value <= min(strategy.candidate_budget, 1_000_000)
            ),
            description="整数范围 1～min(策略候选预算, 1000000)",
        ),
    },
}

PASSWORD_TARGETS = frozenset({
    TargetType.HASH,
    TargetType.ZIP,
    TargetType.PDF,
    TargetType.OFFICE,
})

STRATEGY_TARGETS: dict[StrategyId, frozenset[TargetType]] = {
    StrategyId.S1: PASSWORD_TARGETS,
    StrategyId.S2: PASSWORD_TARGETS,
    StrategyId.S3: PASSWORD_TARGETS,
    StrategyId.S4: PASSWORD_TARGETS,
}


class PolicyValidator:
    """Validates an untrusted strategy plan before it reaches an executor."""

    def __init__(
        self,
        *,
        strategy_parameter_rules: dict[
            StrategyId, dict[str, ParameterRule]
        ] | None = None,
    ) -> None:
        self.strategy_parameter_rules = (
            STRATEGY_PARAMETER_RULES
            if strategy_parameter_rules is None
            else strategy_parameter_rules
        )
        self.strategy_whitelist = frozenset(self.strategy_parameter_rules)

    def validate(self, prir: PRIR, plan: StrategyPlan) -> StrategyPlan:
        issues: list[PolicyIssue] = []
        self._validate_envelope(prir, plan, issues)
        self._validate_strategies(prir, plan, issues)
        self._validate_budgets(prir, plan, issues)
        if issues:
            raise PolicyValidationError(issues)
        return plan

    def _validate_envelope(
        self, prir: PRIR, plan: StrategyPlan, issues: list[PolicyIssue]
    ) -> None:
        if plan.task_id != prir.task_id:
            issues.append(PolicyIssue("TASK_MISMATCH", "计划 task_id 与 PRIR 不一致"))
        if plan.status != TaskStatus.PLANNED:
            issues.append(PolicyIssue("INVALID_STATUS", "策略计划状态必须为 planned"))
        if plan.total_time_budget != prir.time_budget:
            issues.append(
                PolicyIssue("BUDGET_MISMATCH", "计划总时间预算必须等于 PRIR 时间预算")
            )
        if not plan.strategies:
            issues.append(PolicyIssue("EMPTY_PLAN", "策略计划不能为空"))

    def _validate_strategies(
        self, prir: PRIR, plan: StrategyPlan, issues: list[PolicyIssue]
    ) -> None:
        seen: set[StrategyId] = set()
        priorities: list[int] = []
        for strategy in plan.strategies:
            strategy_id = strategy.strategy_id
            strategy_label = strategy_id.value
            if strategy_id not in self.strategy_whitelist:
                issues.append(
                    PolicyIssue(
                        "STRATEGY_NOT_ALLOWED",
                        f"策略 {strategy_label} 不在白名单中",
                        strategy_id=strategy_label,
                    )
                )
                continue
            if strategy_id in seen:
                issues.append(
                    PolicyIssue(
                        "DUPLICATE_STRATEGY",
                        f"策略 {strategy_label} 重复出现",
                        strategy_id=strategy_label,
                    )
                )
            seen.add(strategy_id)
            priorities.append(strategy.priority)
            if prir.target_type not in STRATEGY_TARGETS[strategy_id]:
                issues.append(
                    PolicyIssue(
                        "TARGET_NOT_SUPPORTED",
                        f"策略 {strategy_label} 不适用于 {prir.target_type.value} 目标",
                        strategy_id=strategy_label,
                    )
                )
            if strategy_id == StrategyId.S4 and not prir.context_available:
                issues.append(
                    PolicyIssue(
                        "CONTEXT_REQUIRED",
                        "S4 仅适用于具有上下文的任务",
                        strategy_id=strategy_label,
                    )
                )
            if strategy.time_budget <= 0 or strategy.candidate_budget <= 0:
                issues.append(
                    PolicyIssue(
                        "EMPTY_STRATEGY_BUDGET",
                        f"策略 {strategy_label} 的时间和候选预算必须大于 0",
                        strategy_id=strategy_label,
                    )
                )
            self._validate_parameters(strategy, issues)

        expected = list(range(1, len(plan.strategies) + 1))
        if sorted(priorities) != expected:
            issues.append(
                PolicyIssue("INVALID_PRIORITY", "策略优先级必须从 1 连续且不重复")
            )

    def _validate_parameters(
        self, strategy: StrategyItem, issues: list[PolicyIssue]
    ) -> None:
        rules = self.strategy_parameter_rules.get(strategy.strategy_id)
        if rules is None:
            return
        for name, value in strategy.parameters.items():
            rule = rules.get(name)
            if rule is None:
                issues.append(
                    PolicyIssue(
                        "PARAMETER_NOT_ALLOWED",
                        f"策略 {strategy.strategy_id.value} 不允许参数 {name}",
                        strategy_id=strategy.strategy_id.value,
                        parameter=name,
                    )
                )
                continue
            if not rule.accepts(value, strategy):
                issues.append(
                    PolicyIssue(
                        "PARAMETER_OUT_OF_RANGE",
                        (
                            f"策略 {strategy.strategy_id.value} 的参数 {name} "
                            f"必须满足：{rule.description}"
                        ),
                        strategy_id=strategy.strategy_id.value,
                        parameter=name,
                    )
                )

    def _validate_budgets(
        self, prir: PRIR, plan: StrategyPlan, issues: list[PolicyIssue]
    ) -> None:
        allocated_time = sum(item.time_budget for item in plan.strategies)
        allocated_candidates = sum(
            item.candidate_budget for item in plan.strategies
        )
        if allocated_time > prir.time_budget:
            issues.append(
                PolicyIssue(
                    "TIME_BUDGET_EXCEEDED",
                    f"策略时间预算总和 {allocated_time} 超过上限 {prir.time_budget}",
                )
            )
        if allocated_candidates > prir.candidate_budget:
            issues.append(
                PolicyIssue(
                    "CANDIDATE_BUDGET_EXCEEDED",
                    (
                        f"策略候选预算总和 {allocated_candidates} "
                        f"超过上限 {prir.candidate_budget}"
                    ),
                )
            )
