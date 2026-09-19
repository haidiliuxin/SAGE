from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .enums import LLMApiStyle, PlannerType, SchedulerType


def _as_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _parse_planner_type(raw: str) -> PlannerType:
    value = raw.strip().lower()
    if value == "adaptive":
        raise RuntimeError(
            "SAGE_PLANNER_TYPE=adaptive 已弃用：自适应能力位于调度层。"
            "请改用 SAGE_SCHEDULER_TYPE（fixed / round_robin / "
            "heuristic_bandit / ucb / cost_aware_ucb / thompson），"
            "并把 SAGE_PLANNER_TYPE 设为 mock、rule 或 llm。"
        )
    try:
        return PlannerType(value)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in PlannerType)
        raise RuntimeError(
            f"SAGE_PLANNER_TYPE={raw!r} 不受支持，可选值：{allowed}"
        ) from exc


def _parse_scheduler_type(raw: str) -> SchedulerType:
    try:
        return SchedulerType(raw.strip().lower())
    except ValueError as exc:
        allowed = ", ".join(item.value for item in SchedulerType)
        raise RuntimeError(
            f"SAGE_SCHEDULER_TYPE={raw!r} 不受支持，可选值：{allowed}"
        ) from exc


def _parse_path_list(raw: str, variable: str) -> tuple[Path, ...]:
    """逗号分隔的路径列表（规则文件、种子词表）。"""
    items = tuple(_as_path(item.strip()) for item in raw.split(",") if item.strip())
    return items


def _parse_optional_int(raw: str | None, variable: str) -> int | None:
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{variable}={raw!r} 不是整数") from exc
    if value <= 0:
        raise ValueError(f"{variable} 必须大于 0")
    return value


def _parse_mask_list(raw: str, variable: str) -> tuple[str, ...]:
    """逗号分隔的掩码列表（如 ?d?d?d?d,?l?l?l?l）。"""
    items = tuple(item.strip() for item in raw.split(",") if item.strip())
    for index, item in enumerate(items):
        if "\n" in item or "\r" in item:
            raise ValueError(f"{variable}[{index}] 必须为单行掩码")
    return items


def _parse_batch_size(raw: str, variable: str = "SAGE_DECISION_BATCH_SIZE") -> int:
    """批次大小：批越大，每批的 hashcat 进程启动开销摊得越薄。"""
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{variable}={raw!r} 不是整数") from exc
    if value <= 0:
        raise ValueError(f"{variable} 必须大于 0")
    if value > 100_000:
        raise ValueError(f"{variable} 不能超过 100000")
    return value


# 掩码阶梯与混合掩码的默认值（与 Settings 字段默认一致）。
_DEFAULT_MASK_LADDER = (
    "?d?d?d?d",
    "?l?l?l?l?l?l",
    "?l?l?l?l?d?d",
    "?u?l?l?l?l?d?d",
)
_DEFAULT_HYBRID_MASKS = ("?d", "?d?d", "?d?d?d?d", "!", "@", "!?d")


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    upload_dir: Path
    max_upload_bytes: int
    cors_origins: tuple[str, ...]
    hashcat_path: str = "hashcat"
    zip2john_path: str = "zip2john"
    planner_type: PlannerType = PlannerType.MOCK
    scheduler_type: SchedulerType = SchedulerType.HEURISTIC_BANDIT
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    llm_api_style: LLMApiStyle = LLMApiStyle.RESPONSES
    llm_model: str = "gpt-4.1-mini"
    llm_temperature: float = 0.1
    llm_timeout_seconds: float = 15.0
    llm_max_output_tokens: int = 700
    llm_cache_ttl_seconds: int = 900
    llm_cache_max_entries: int = 256
    feedback_minimum_observations: int = 2
    feedback_minimum_tasks: int = 2
    feedback_maximum_patterns_per_scope: int = 500
    feedback_recency_half_life_days: float = 90.0
    feedback_enable_mock: bool = False
    pcfg_variant: str = "pcfg_lite"
    pcfg_ruleset_path: Path | None = None
    s3_generator_id: str | None = None
    s4_generator_id: str | None = None
    markov_ruleset_path: Path | None = None
    markov_order: int = 3
    pdf2john_path: str = "pdf2john"
    office2john_path: str = "office2john"
    extraction_timeout_seconds: float = 30.0
    decision_batch_size: int = 1_000
    # 流式调度每次决策的候选上限：默认与调度粒度一致，保证 Bandit 能在单元内部
    # 重新分配预算；吞吐优先时可调大（如 100000）以摊薄 hashcat 进程启动开销。
    hashcat_stream_batch_size: int = 1_000
    # 原生攻击配置。
    # 规则文件（-r）：用于 S1 的原生"词表 × 规则"攻击（配置了词表时生效）。
    rules_path: Path | None = None
    # 词表 × 规则：默认复用 rules_path；可用 SAGE_WORDLIST_RULES 指定多个规则文件。
    wordlist_rule_paths: tuple[Path, ...] = ()
    # 掩码阶梯（-a 3）：默认覆盖"4 位数字/年份""6 位小写""小写+两位数字""首字母大写+小写+两位数字"。
    mask_ladder: tuple[str, ...] = (
        "?d?d?d?d",
        "?l?l?l?l?l?l",
        "?l?l?l?l?d?d",
        "?u?l?l?l?l?d?d",
    )
    # 混合掩码（-a 6，词表 + 掩码）：覆盖 1 位数字、2 位数字、4 位年份、单符号、符号+数字。
    hybrid_masks: tuple[str, ...] = ("?d", "?d?d", "?d?d?d?d", "!", "@", "!?d")
    # 额外种子词表（例如中文常见口令词表）：内容作为 S1/S2/S3 的种子并进入 Python 候选。
    seed_wordlists: tuple[Path, ...] = ()
    # 自适应切片：把原生攻击（词表 × 规则链 / 掩码 / 词表 × 掩码）切成多个可观测批次，
    # 让调度器每批之后都能按实测产出重新选臂（而不是一次拉完整段键空间）。
    # 代价是每多一批多一次 hashcat 启动（约 2~3 秒），因此首批是"探针"、之后倍增。
    adaptive_slicing: bool = True
    adaptive_probe_divisor: int = 8
    adaptive_min_slice_keys: int = 100_000
    # 探针按"目标 GPU 工作时长"定大小：已知实测速率（键/秒）时用 速率 × 秒数，
    # 否则用 adaptive_probe_keys 作为保守起点。探针只占该单元时间预算的一小部分，
    # 剩下的预算留给后续"提交"批次，避免探针就把预算吃光。
    adaptive_probe_seconds: float = 1.0
    adaptive_probe_keys: int = 2_000_000
    # 探针之后的放大倍数：8.0 = 探针（键空间的 1/divisor）之后一次提交剩余全部，
    # 让每个原生单元只多一次 hashcat 启动开销；调小可得到更多决策点、更多启动开销。
    adaptive_growth: float = 8.0
    # 低内存 / 低显存适配（8GB 显存或主机内存紧张时建议开启）。
    hashcat_optimized: bool = False
    hashcat_kernel_accel: int | None = None
    hashcat_kernel_loops: int | None = None
    hashcat_kernel_threads: int | None = None
    hashcat_device_types: int | None = None
    wordlist_path: Path | None = None
    stop_on_hit: bool = False

    def __post_init__(self) -> None:
        if self.hashcat_stream_batch_size <= 0:
            raise ValueError("hashcat_stream_batch_size 必须大于 0")
        if self.adaptive_probe_divisor <= 0:
            raise ValueError("adaptive_probe_divisor 必须大于 0")
        if self.adaptive_min_slice_keys <= 0:
            raise ValueError("adaptive_min_slice_keys 必须大于 0")
        if self.adaptive_growth < 1.0:
            raise ValueError("adaptive_growth 必须大于等于 1")

    @classmethod
    def from_env(cls) -> "Settings":
        origins = os.getenv(
            "SAGE_CORS_ORIGINS",
            (
                "http://localhost:5173,http://127.0.0.1:5173,"
                "http://localhost:8501,http://127.0.0.1:8501"
            ),
        )
        return cls(
            database_url=os.getenv(
                "SAGE_DATABASE_URL", "sqlite:///./data/sage_pass.db"
            ),
            upload_dir=_as_path(os.getenv("SAGE_UPLOAD_DIR", "./data/uploads")),
            max_upload_bytes=int(os.getenv("SAGE_MAX_UPLOAD_BYTES", "10485760")),
            cors_origins=tuple(item.strip() for item in origins.split(",") if item.strip()),
            hashcat_path=os.getenv("SAGE_HASHCAT_PATH", "hashcat"),
            zip2john_path=os.getenv("SAGE_ZIP2JOHN_PATH", "zip2john"),
            planner_type=_parse_planner_type(
                os.getenv("SAGE_PLANNER_TYPE", "mock")
            ),
            scheduler_type=_parse_scheduler_type(
                os.getenv("SAGE_SCHEDULER_TYPE", "heuristic_bandit")
            ),
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_base_url=os.getenv("OPENAI_BASE_URL") or None,
            llm_api_style=LLMApiStyle(
                os.getenv("SAGE_LLM_API_STYLE", "responses").lower()
            ),
            llm_model=os.getenv("SAGE_LLM_MODEL", "gpt-4.1-mini"),
            llm_temperature=float(os.getenv("SAGE_LLM_TEMPERATURE", "0.1")),
            llm_timeout_seconds=float(os.getenv("SAGE_LLM_TIMEOUT_SECONDS", "15")),
            llm_max_output_tokens=int(os.getenv("SAGE_LLM_MAX_OUTPUT_TOKENS", "700")),
            llm_cache_ttl_seconds=int(os.getenv("SAGE_LLM_CACHE_TTL_SECONDS", "900")),
            llm_cache_max_entries=int(os.getenv("SAGE_LLM_CACHE_MAX_ENTRIES", "256")),
            feedback_minimum_observations=int(
                os.getenv("SAGE_FEEDBACK_MINIMUM_OBSERVATIONS", "2")
            ),
            feedback_minimum_tasks=int(
                os.getenv("SAGE_FEEDBACK_MINIMUM_TASKS", "2")
            ),
            feedback_maximum_patterns_per_scope=int(
                os.getenv("SAGE_FEEDBACK_MAXIMUM_PATTERNS_PER_SCOPE", "500")
            ),
            feedback_recency_half_life_days=float(
                os.getenv("SAGE_FEEDBACK_RECENCY_HALF_LIFE_DAYS", "90")
            ),
            feedback_enable_mock=os.getenv(
                "SAGE_FEEDBACK_ENABLE_MOCK", "false"
            ).strip().lower() in {"1", "true", "yes", "on"},
            pcfg_variant=os.getenv(
                "SAGE_PCFG_VARIANT", "pcfg_lite"
            ).strip().lower(),
            pcfg_ruleset_path=(
                _as_path(value)
                if (value := os.getenv("SAGE_PCFG_RULESET_PATH"))
                else None
            ),
            s3_generator_id=(
                value.strip().lower()
                if (value := os.getenv("SAGE_S3_GENERATOR"))
                else None
            ),
            s4_generator_id=(
                value.strip().lower()
                if (value := os.getenv("SAGE_S4_GENERATOR"))
                else None
            ),
            markov_ruleset_path=(
                _as_path(value)
                if (value := os.getenv("SAGE_MARKOV_RULESET_PATH"))
                else None
            ),
            markov_order=int(os.getenv("SAGE_MARKOV_ORDER", "3")),
            pdf2john_path=os.getenv("SAGE_PDF2JOHN_PATH", "pdf2john"),
            office2john_path=os.getenv(
                "SAGE_OFFICE2JOHN_PATH", "office2john"
            ),
            extraction_timeout_seconds=float(
                os.getenv("SAGE_EXTRACTION_TIMEOUT_SECONDS", "30")
            ),
            decision_batch_size=_parse_batch_size(
                os.getenv("SAGE_DECISION_BATCH_SIZE", "1000")
            ),
            hashcat_stream_batch_size=_parse_batch_size(
                os.getenv(
                    "SAGE_HASHCAT_STREAM_BATCH_SIZE",
                    os.getenv("SAGE_DECISION_BATCH_SIZE", "1000"),
                ),
                "SAGE_HASHCAT_STREAM_BATCH_SIZE",
            ),
            wordlist_path=(
                _as_path(value)
                if (value := os.getenv("SAGE_WORDLIST_PATH"))
                else None
            ),
            adaptive_slicing=os.getenv("SAGE_ADAPTIVE_SLICING", "true")
            .strip()
            .lower()
            in {"1", "true", "yes", "on"},
            adaptive_probe_divisor=_parse_optional_int(
                os.getenv("SAGE_ADAPTIVE_PROBE_DIVISOR", "8"),
                "SAGE_ADAPTIVE_PROBE_DIVISOR",
            )
            or 8,
            adaptive_min_slice_keys=_parse_optional_int(
                os.getenv("SAGE_ADAPTIVE_MIN_SLICE_KEYS", "100000"),
                "SAGE_ADAPTIVE_MIN_SLICE_KEYS",
            )
            or 100_000,
            adaptive_probe_seconds=float(
                os.getenv("SAGE_ADAPTIVE_PROBE_SECONDS", "1.0")
            ),
            adaptive_probe_keys=_parse_optional_int(
                os.getenv("SAGE_ADAPTIVE_PROBE_KEYS", "2000000"),
                "SAGE_ADAPTIVE_PROBE_KEYS",
            )
            or 2_000_000,
            adaptive_growth=float(os.getenv("SAGE_ADAPTIVE_GROWTH", "8.0")),
            rules_path=(
                _as_path(value)
                if (value := os.getenv("SAGE_RULES_PATH"))
                else None
            ),
            mask_ladder=(
                _parse_mask_list(
                    os.getenv("SAGE_MASK_LADDER", ""), "SAGE_MASK_LADDER"
                )
                or _DEFAULT_MASK_LADDER
            ),
            hybrid_masks=(
                _parse_mask_list(
                    os.getenv("SAGE_HYBRID_MASKS", ""), "SAGE_HYBRID_MASKS"
                )
                or _DEFAULT_HYBRID_MASKS
            ),
            wordlist_rule_paths=_parse_path_list(
                os.getenv("SAGE_WORDLIST_RULES", ""), "SAGE_WORDLIST_RULES"
            ),
            seed_wordlists=_parse_path_list(
                os.getenv("SAGE_SEED_WORDLISTS", ""), "SAGE_SEED_WORDLISTS"
            ),
            hashcat_optimized=os.getenv("SAGE_HASHCAT_OPTIMIZED", "false")
            .strip()
            .lower()
            in {"1", "true", "yes", "on"},
            hashcat_kernel_accel=_parse_optional_int(
                os.getenv("SAGE_HASHCAT_KERNEL_ACCEL"), "SAGE_HASHCAT_KERNEL_ACCEL"
            ),
            hashcat_kernel_loops=_parse_optional_int(
                os.getenv("SAGE_HASHCAT_KERNEL_LOOPS"), "SAGE_HASHCAT_KERNEL_LOOPS"
            ),
            hashcat_kernel_threads=_parse_optional_int(
                os.getenv("SAGE_HASHCAT_KERNEL_THREADS"),
                "SAGE_HASHCAT_KERNEL_THREADS",
            ),
            hashcat_device_types=_parse_optional_int(
                os.getenv("SAGE_HASHCAT_DEVICE_TYPES"), "SAGE_HASHCAT_DEVICE_TYPES"
            ),
            stop_on_hit=os.getenv("SAGE_STOP_ON_HIT", "false")
            .strip()
            .lower()
            in {"1", "true", "yes", "on"},
        )
