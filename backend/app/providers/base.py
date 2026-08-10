from abc import ABC, abstractmethod
from typing import Any


class ProviderError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class AIProvider(ABC):
    name: str

    @abstractmethod
    async def structured_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
        schema_name: str,
        model: str,
    ) -> dict[str, Any]:
        raise NotImplementedError
