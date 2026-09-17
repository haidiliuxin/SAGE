from __future__ import annotations

import configparser
import hashlib
import io
from collections.abc import Iterator
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from .._vendor.pcfg_cracker.lib_guesser.omen.input_file_io import load_rules
from .._vendor.pcfg_cracker.lib_guesser.omen.markov_cracker import MarkovCracker
from .._vendor.pcfg_cracker.lib_guesser.omen.optimizer import Optimizer
from ..candidate_types import CandidateRecord, CandidateSource
from ..enums import StrategyId
from .base import GeneratorPrepareRequest, GeneratorState, ReplayableGenerator


OMEN_MODEL_FILES = (
    "config.txt",
    "alphabet.txt",
    "IP.level",
    "EP.level",
    "CP.level",
    "LN.level",
)
DEFAULT_MARKOV_ORDER = 3
DEFAULT_MAX_LEVEL = 10
DEFAULT_MAX_CANDIDATE_LENGTH = 1024


class MarkovModelError(ValueError):
    """Raised when an OMEN model is absent, invalid or incompatible."""


class MarkovModelNotConfiguredError(MarkovModelError):
    pass


class MarkovGenerator(ReplayableGenerator):
    """Streaming adapter for the MIT-licensed OMEN Markov enumerator.

    ``order`` is the number of preceding characters in the Markov context.
    OMEN calls the resulting context-plus-next-character value ``ngram``, so a
    third-order model has ``ngram = 4`` in its ``config.txt``.
    """

    generator_id = "markov"
    strategy_id = StrategyId.S3

    def __init__(
        self,
        ruleset_directory: str | Path | None = None,
        *,
        default_order: int = DEFAULT_MARKOV_ORDER,
    ) -> None:
        if default_order < 1:
            raise ValueError("Markov order 必须大于 0")
        self.ruleset_directory = (
            Path(ruleset_directory).expanduser().resolve()
            if ruleset_directory is not None
            else None
        )
        self.default_order = default_order
        self._model_info = (
            _read_model_info(self.ruleset_directory)
            if self.ruleset_directory is not None
            else None
        )
        self.model_version = (
            self._model_info["model_version"]
            if self._model_info is not None
            else "omen:unconfigured"
        )

    def prepare(self, request: GeneratorPrepareRequest) -> GeneratorState:
        model_info = self._require_model()
        order = int(request.parameters.get("order", self.default_order))
        if order < 1:
            raise MarkovModelError("Markov order 必须大于 0")
        model_order = int(model_info["order"])
        if order != model_order:
            raise MarkovModelError(
                f"请求参数 order={order}，但 OMEN 模型为 "
                f"order={model_order}；阶数必须在训练时确定并保持一致"
            )
        return self._new_state(
            request,
            restore_data={"order": order},
            model_version=self.model_version,
        )

    def _iter_records(self, state: GeneratorState) -> Iterator[CandidateRecord]:
        model_info = self._require_model()
        grammar = _load_model(self.ruleset_directory)
        optimizer = Optimizer(max_length=4)
        minimum_level = int(state.parameters.get("min_level", 0))
        maximum_level = int(
            state.parameters.get("max_level", DEFAULT_MAX_LEVEL)
        )
        minimum_length = int(state.parameters.get("min_length", 1))
        maximum_length = int(
            state.parameters.get(
                "max_length",
                state.parameters.get(
                    "max_structure_length", DEFAULT_MAX_CANDIDATE_LENGTH
                ),
            )
        )
        if minimum_level < 0 or maximum_level < minimum_level:
            raise MarkovModelError("Markov level 范围无效")
        if minimum_length < 1 or maximum_length < minimum_length:
            raise MarkovModelError("Markov 候选长度范围无效")
        model_maximum_level = int(grammar["max_level"])
        if minimum_level > model_maximum_level:
            raise MarkovModelError(
                f"min_level={minimum_level} 超过模型最大 level "
                f"{model_maximum_level}"
            )
        maximum_level = min(maximum_level, model_maximum_level)
        order = int(state.restore_data["order"])

        for level in range(minimum_level, maximum_level + 1):
            cracker = MarkovCracker(grammar, target_level=level, optimizer=optimizer)
            while True:
                candidate = cracker.next_guess()
                if candidate is None:
                    break
                if not _is_usable_candidate(
                    candidate,
                    encoding=model_info["encoding"],
                    minimum_length=minimum_length,
                    maximum_length=maximum_length,
                ):
                    continue
                yield CandidateRecord(
                    value=candidate,
                    strategy_id=StrategyId.S3,
                    sources=(CandidateSource(
                        kind="markov",
                        template=f"order:{order}",
                        score=float(level),
                        components=(f"omen_level:{level}",),
                    ),),
                )

    def _require_model(self) -> dict[str, str | int]:
        if self.ruleset_directory is None or self._model_info is None:
            raise MarkovModelNotConfiguredError(
                "markov 已注册但未配置 OMEN ruleset；请设置 "
                "SAGE_MARKOV_RULESET_PATH"
            )
        return self._model_info


def _read_model_info(ruleset_directory: Path) -> dict[str, str | int]:
    if not ruleset_directory.is_dir():
        raise MarkovModelError(
            f"OMEN ruleset 目录不存在：{ruleset_directory}"
        )
    missing = [
        name for name in OMEN_MODEL_FILES
        if not (ruleset_directory / name).is_file()
    ]
    if missing:
        raise MarkovModelError(
            "OMEN ruleset 缺少文件：" + ", ".join(missing)
        )
    parser = configparser.ConfigParser()
    try:
        with (ruleset_directory / "config.txt").open(
            "r", encoding="utf-8"
        ) as handle:
            parser.read_file(handle)
        ngram = parser.getint("training_settings", "ngram")
        encoding = parser.get("training_settings", "encoding")
        "".encode(encoding)
    except (OSError, configparser.Error, LookupError, ValueError) as exc:
        raise MarkovModelError("OMEN config.txt 无效") from exc
    if ngram < 2:
        raise MarkovModelError("OMEN ngram 必须至少为 2")
    digest = hashlib.sha256()
    for name in OMEN_MODEL_FILES:
        digest.update(name.encode("ascii"))
        with (ruleset_directory / name).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    fingerprint = digest.hexdigest()
    return {
        "encoding": encoding,
        "ngram": ngram,
        "order": ngram - 1,
        "fingerprint": fingerprint,
        "model_version": f"omen:ngram-{ngram}:{fingerprint}",
    }


def _load_model(ruleset_directory: Path | None) -> dict[str, object]:
    if ruleset_directory is None:
        raise MarkovModelNotConfiguredError("OMEN ruleset 未配置")
    grammar: dict[str, object] = {}
    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
        loaded = load_rules(str(ruleset_directory), grammar)
    if not loaded:
        raise MarkovModelError(
            f"加载 OMEN ruleset 失败：{ruleset_directory}"
        )
    return grammar


def _is_usable_candidate(
    value: object,
    *,
    encoding: str,
    minimum_length: int,
    maximum_length: int,
) -> bool:
    if (
        not isinstance(value, str)
        or not minimum_length <= len(value) <= maximum_length
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
