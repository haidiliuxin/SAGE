from __future__ import annotations

import configparser
import math
from collections.abc import Iterator
from pathlib import Path

from .._vendor.pcfg_cracker.lib_guesser.grammar_io import load_grammar
from .._vendor.pcfg_cracker.lib_guesser.pcfg_grammar import PcfgGrammar
from .._vendor.pcfg_cracker.lib_guesser.priority_queue import PcfgQueue
from ..candidate_types import CandidateRecord, CandidateSource
from ..enums import StrategyId
from .base import GeneratorPrepareRequest, GeneratorState, ReplayableGenerator


PCFG_GUESSER_VERSION = "4.6"
DEFAULT_MAX_CANDIDATE_LENGTH = 1024


class PCFGModelError(ValueError):
    """Raised when a pcfg_cracker-compatible ruleset cannot be loaded."""


class PCFGModelNotConfiguredError(PCFGModelError):
    pass


class PCFGFullGenerator(ReplayableGenerator):
    """Streaming adapter for lakiw/pcfg_cracker probability-ordered guesses.

    The registered instance only retains immutable model configuration. Each
    call to ``prepare``/``restore`` builds an independent grammar, queue and
    iterator inside its run-local state.
    """

    generator_id = "pcfg_full"
    strategy_id = StrategyId.S3

    def __init__(self, ruleset_directory: str | Path | None = None) -> None:
        self.ruleset_directory = (
            Path(ruleset_directory).expanduser().resolve()
            if ruleset_directory is not None
            else None
        )
        self._model_info = (
            _read_model_info(self.ruleset_directory)
            if self.ruleset_directory is not None
            else None
        )
        self.model_version = (
            self._model_info["model_version"]
            if self._model_info is not None
            else "pcfg-cracker:unconfigured"
        )

    def prepare(self, request: GeneratorPrepareRequest) -> GeneratorState:
        self._require_model()
        return self._new_state(
            request,
            restore_data={},
            model_version=self.model_version,
        )

    def _iter_records(self, state: GeneratorState) -> Iterator[CandidateRecord]:
        model_info = self._require_model()
        engine = _load_engine(
            self.ruleset_directory,
            skip_case=bool(state.parameters.get("skip_case", False)),
        )
        queue = PcfgQueue(engine)
        minimum_probability = float(
            state.parameters.get("min_probability", 0.0)
        )
        max_length = int(
            state.parameters.get(
                "max_structure_length", DEFAULT_MAX_CANDIDATE_LENGTH
            )
        )

        while True:
            parse_tree = queue.next()
            if parse_tree is None:
                return
            probability = float(parse_tree["prob"])
            if probability < minimum_probability:
                return
            if probability <= 0.0:
                return
            log_probability = math.log(probability)
            template = "".join(
                transition for transition, _ in parse_tree["pt"]
            )
            for candidate in _iter_parse_tree_guesses(
                engine, parse_tree["pt"]
            ):
                if not _is_usable_candidate(
                    candidate,
                    encoding=model_info["encoding"],
                    max_length=max_length,
                ):
                    continue
                yield CandidateRecord(
                    value=candidate,
                    strategy_id=StrategyId.S3,
                    sources=(CandidateSource(
                        kind="pcfg_full",
                        template=template,
                        probability=probability,
                        log_probability=log_probability,
                        components=(model_info["uuid"],),
                    ),),
                )

    def _require_model(self) -> dict[str, str]:
        if self.ruleset_directory is None or self._model_info is None:
            raise PCFGModelNotConfiguredError(
                "pcfg_full 已注册但未配置 ruleset；请设置 "
                "SAGE_PCFG_RULESET_PATH 或向 PCFGFullGenerator 提供目录"
            )
        return self._model_info


def _read_model_info(ruleset_directory: Path) -> dict[str, str]:
    if not ruleset_directory.is_dir():
        raise PCFGModelError(
            f"PCFG ruleset 目录不存在：{ruleset_directory}"
        )
    config_path = ruleset_directory / "config.ini"
    parser = configparser.ConfigParser()
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            parser.read_file(handle)
        rule_version = parser.get("TRAINING_PROGRAM_DETAILS", "version")
        encoding = parser.get("TRAINING_DATASET_DETAILS", "encoding")
        model_uuid = parser.get("TRAINING_DATASET_DETAILS", "uuid")
    except (OSError, configparser.Error, KeyError) as exc:
        raise PCFGModelError(
            f"无法读取 PCFG ruleset 配置：{config_path}"
        ) from exc
    try:
        rule_major = int(rule_version.split(".", 1)[0])
        guesser_major = int(PCFG_GUESSER_VERSION.split(".", 1)[0])
    except ValueError as exc:
        raise PCFGModelError(
            f"PCFG ruleset 版本无效：{rule_version!r}"
        ) from exc
    if rule_major < guesser_major:
        raise PCFGModelError(
            f"PCFG ruleset 主版本 {rule_version!r} 与 guesser "
            f"{PCFG_GUESSER_VERSION!r} 不兼容"
        )
    try:
        "".encode(encoding)
    except LookupError as exc:
        raise PCFGModelError(f"PCFG ruleset 编码未知：{encoding!r}") from exc
    return {
        "encoding": encoding,
        "uuid": model_uuid,
        "rule_version": rule_version,
        "model_version": (
            f"pcfg-cracker:{PCFG_GUESSER_VERSION}:{rule_version}:{model_uuid}"
        ),
    }


def _load_engine(
    ruleset_directory: Path | None, *, skip_case: bool
) -> PcfgGrammar:
    if ruleset_directory is None:
        raise PCFGModelNotConfiguredError("pcfg_full ruleset 未配置")
    try:
        grammar, base, ruleset_info = load_grammar(
            ruleset_directory.name,
            str(ruleset_directory),
            PCFG_GUESSER_VERSION,
            True,
            skip_case,
            "Grammar",
        )
    except Exception as exc:
        raise PCFGModelError(
            f"加载 PCFG ruleset 失败：{ruleset_directory}"
        ) from exc

    # PcfgGrammar.__init__ also loads OMEN. pcfg_full deliberately excludes
    # that future Markov generator, while retaining the upstream PCFG methods.
    engine = object.__new__(PcfgGrammar)
    engine.rulename = ruleset_directory.name
    engine.debug = False
    engine.grammar = grammar
    engine.base = base
    engine.ruleset_info = ruleset_info
    engine.encoding = ruleset_info["encoding"]
    return engine


def _iter_parse_tree_guesses(
    engine: PcfgGrammar,
    parse_tree: list[tuple[str, int]],
    current: str = "",
) -> Iterator[str]:
    if not parse_tree:
        yield current
        return
    transition, index = parse_tree[0]
    category = transition[0]
    if category == "M":
        raise PCFGModelError(
            "pcfg_full 不执行 OMEN/Markov 结构；请使用纯 PCFG ruleset"
        )
    values = engine.grammar[transition][index]["values"]
    if category == "C":
        mask_length = len(values[0])
        prefix = current[:-mask_length]
        suffix = current[-mask_length:]
        for mask in values:
            transformed = "".join(
                character if flag == "L" else character.upper()
                for character, flag in zip(suffix, mask, strict=True)
            )
            yield from _iter_parse_tree_guesses(
                engine, parse_tree[1:], prefix + transformed
            )
        return
    for value in values:
        yield from _iter_parse_tree_guesses(
            engine, parse_tree[1:], current + value
        )


def _is_usable_candidate(
    value: object, *, encoding: str, max_length: int
) -> bool:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= max_length
        or "\n" in value
        or "\r" in value
        or "\x00" in value
    ):
        return False
    try:
        value.encode(encoding, errors="strict")
        value.encode("utf-8", errors="strict")
    except (LookupError, UnicodeEncodeError):
        return False
    return True
