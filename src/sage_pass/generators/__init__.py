from __future__ import annotations

from ..enums import StrategyId
from ..transfer import TransferCandidateGenerator
from .base import (
    GeneratorPrepareRequest,
    GeneratorProtocol,
    GeneratorSnapshot,
    GeneratorState,
    GeneratorStateError,
)
from .baseline import BaselineGenerator
from .context import ContextGenerator
from .history import HistoryGenerator
from .hybrid import HybridGenerator
from .pcfg_lite import PCFGLiteGenerator
from .markov import (
    MarkovGenerator,
    MarkovModelError,
    MarkovModelNotConfiguredError,
)
from .pcfg_full import (
    PCFGFullGenerator,
    PCFGModelError,
    PCFGModelNotConfiguredError,
)
from .registry import (
    DuplicateGeneratorError,
    GeneratorRegistry,
    GeneratorRegistryError,
    UnknownGeneratorError,
)
from .rule import RuleGenerator
from .native import NativeOnlyGenerator
from .transfer import PatternKnowledgeGenerator, TransferGenerator


STRATEGY_GENERATOR_IDS: dict[StrategyId, str] = {
    StrategyId.S1: "baseline",
    StrategyId.S2: "rule",
    StrategyId.S3: "pcfg_lite",
    StrategyId.S4: "context",
    StrategyId.S5: "pattern_knowledge",
    # 原生攻击单元：候选由 hashcat 枚举，占位生成器不产出候选。
    StrategyId.S6: "mask",
    StrategyId.S7: "hybrid_mask",
}

# Reserved names are documentation only. They are deliberately not registered.
FUTURE_GENERATOR_IDS = frozenset({"passllm"})


def build_default_registry(
    *,
    transfer_generator: TransferCandidateGenerator | None = None,
    pcfg_ruleset_path: str | None = None,
    markov_ruleset_path: str | None = None,
    markov_order: int = 3,
) -> GeneratorRegistry:
    registry = GeneratorRegistry()
    registry.register(BaselineGenerator())
    registry.register(RuleGenerator())
    registry.register(PCFGLiteGenerator())
    registry.register(PCFGFullGenerator(pcfg_ruleset_path))
    registry.register(MarkovGenerator(
        markov_ruleset_path,
        default_order=markov_order,
    ))
    registry.register(ContextGenerator())
    registry.register(HistoryGenerator())
    registry.register(HybridGenerator(transfer_generator))
    registry.register(PatternKnowledgeGenerator(transfer_generator))
    # Legacy registry ID retained for injected callers and old diagnostics.
    registry.register(TransferGenerator(transfer_generator))
    # 原生攻击单元（掩码/混合）：候选由 hashcat 自己枚举，后端不展开明文空间。
    registry.register(NativeOnlyGenerator("mask", StrategyId.S6))
    registry.register(NativeOnlyGenerator("hybrid_mask", StrategyId.S7))
    return registry


__all__ = [
    "BaselineGenerator",
    "ContextGenerator",
    "HistoryGenerator",
    "HybridGenerator",
    "DuplicateGeneratorError",
    "FUTURE_GENERATOR_IDS",
    "GeneratorPrepareRequest",
    "GeneratorProtocol",
    "GeneratorRegistry",
    "GeneratorRegistryError",
    "GeneratorSnapshot",
    "GeneratorState",
    "GeneratorStateError",
    "MarkovGenerator",
    "MarkovModelError",
    "MarkovModelNotConfiguredError",
    "PCFGLiteGenerator",
    "PCFGFullGenerator",
    "PCFGModelError",
    "PCFGModelNotConfiguredError",
    "PatternKnowledgeGenerator",
    "RuleGenerator",
    "STRATEGY_GENERATOR_IDS",
    "TransferGenerator",
    "UnknownGeneratorError",
    "build_default_registry",
]
