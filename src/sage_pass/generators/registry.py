from __future__ import annotations

from collections.abc import Iterator
from threading import RLock

from .base import GeneratorProtocol


class GeneratorRegistryError(LookupError):
    pass


class DuplicateGeneratorError(GeneratorRegistryError):
    pass


class UnknownGeneratorError(GeneratorRegistryError):
    pass


class GeneratorRegistry:
    """Explicit registry of implemented, stateless generator adapters."""

    def __init__(self) -> None:
        self._generators: dict[str, GeneratorProtocol] = {}
        self._lock = RLock()

    def register(self, generator: GeneratorProtocol) -> None:
        if not isinstance(generator, GeneratorProtocol):
            raise TypeError("generator 必须实现 GeneratorProtocol")
        generator_id = generator.generator_id.strip()
        if not generator_id:
            raise ValueError("generator_id 不能为空")
        with self._lock:
            if generator_id in self._generators:
                raise DuplicateGeneratorError(
                    f"生成器 {generator_id!r} 已注册"
                )
            self._generators[generator_id] = generator

    def get(self, generator_id: str) -> GeneratorProtocol:
        with self._lock:
            try:
                return self._generators[generator_id]
            except KeyError as exc:
                available = ", ".join(self._generators) or "<none>"
                raise UnknownGeneratorError(
                    f"生成器 {generator_id!r} 未注册；当前可用：{available}"
                ) from exc

    def contains(self, generator_id: str) -> bool:
        with self._lock:
            return generator_id in self._generators

    def registered_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._generators)

    def __contains__(self, generator_id: object) -> bool:
        return isinstance(generator_id, str) and self.contains(generator_id)

    def __iter__(self) -> Iterator[str]:
        return iter(self.registered_ids())
