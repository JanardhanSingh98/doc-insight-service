"""HTTP client for the upstream LLM gateway."""

import httpx

from app.config import get_settings
from app.utils.logging import get_logger

logger = get_logger(__name__)


class LLMClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._client: httpx.AsyncClient | None = None

    async def startup(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=self.settings.llm_base_url,
            timeout=self.settings.llm_timeout_seconds,
        )
        logger.info("llm client ready base_url=%s", self.settings.llm_base_url)

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def complete(self, prompt: str, context: list[str]) -> str:
        payload = {
            "model": self.settings.llm_model,
            "prompt": prompt,
            "context": context,
        }

        client = httpx.AsyncClient(base_url=self.settings.llm_base_url)

        response = await client.post("/completions", json=payload)
        response.raise_for_status()
        return response.json()["completion"]

    async def health(self) -> bool:
        try:
            assert self._client is not None
            response = await self._client.get("/health")
            return response.status_code == 200
        except:  # noqa: E722
            return False


_llm_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
