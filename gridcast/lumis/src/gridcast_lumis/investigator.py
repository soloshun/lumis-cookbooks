"""The SDK's reference investigator with two experiment-only settings.

Since lumis-sdk 812753a the SDK investigator handles everything the earlier TEMPORARY wrapper
patched (no `parallel_tool_calls`, validation retries, output repair, distinct stop reasons,
reasoning, the run transcript on `messages`). What remains here is experiment configuration:

* `unbounded=True` lifts pydantic-ai's request/tool/output-token caps (the experiment lets the
  model "do its own thing"; the SDK broker budgets still apply and are raised separately);
* OpenRouter usage accounting is requested so every response carries its cost.
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from lumis_sdk.investigation.agent import PydanticInvestigator
from lumis_sdk.investigation.tools import InvestigationTools
from lumis_sdk.runtime.project import ModelSettings
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.settings import ModelSettings as AgentModelSettings
from pydantic_ai.usage import UsageLimits


class ExperimentInvestigator(PydanticInvestigator):
    def __init__(self, model, *, model_settings, retries: int, unbounded: bool) -> None:  # noqa: ANN001
        super().__init__(model, model_settings=model_settings, retries=retries)
        self.unbounded = unbounded

    def usage_limits(self, tools: InvestigationTools) -> UsageLimits:
        if self.unbounded:
            return UsageLimits(request_limit=None, tool_calls_limit=None, output_tokens_limit=None)
        return super().usage_limits(tools)

    def run_settings(self, tools: InvestigationTools) -> AgentModelSettings:
        return {} if self.unbounded else super().run_settings(tools)

    @property
    def transcript(self) -> bytes:
        """The last run's full provider messages, including reasoning parts."""
        return ModelMessagesTypeAdapter.dump_json(self.messages, indent=1)


@asynccontextmanager
async def openrouter_investigator(settings: ModelSettings, *, timeout: float, retries: int = 2,
                                  unbounded: bool = False) -> AsyncIterator[ExperimentInvestigator]:
    import httpx2
    from openai import AsyncOpenAI
    from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
    from pydantic_ai.providers.openrouter import OpenRouterProvider

    if settings.provider != "openrouter":
        raise ValueError("the experiment investigator wraps the OpenRouter provider")
    key = os.environ.get(settings.credential_env)
    if not key:
        raise ValueError("configured investigator credential is absent")
    model_settings = OpenRouterModelSettings(
        openrouter_provider={"allow_fallbacks": False, "require_parameters": True},
        openrouter_usage={"include": True},
    )
    if settings.reasoning is not None:
        model_settings["thinking"] = settings.reasoning
    async with (
        httpx2.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False) as http,
        AsyncOpenAI(api_key=key, max_retries=0, http_client=http,
                    base_url="https://openrouter.ai/api/v1") as client,
    ):
        yield ExperimentInvestigator(
            OpenRouterModel(settings.model, provider=OpenRouterProvider(openai_client=client)),
            model_settings=model_settings, retries=retries, unbounded=unbounded,
        )
