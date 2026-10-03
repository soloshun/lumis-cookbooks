"""TEMPORARY OpenRouter investigator, injected through the SDK's `investigator=` extension port.

It is the SDK's own `PydanticInvestigator` (same instructions, same two read-only tools, same
usage limits and output contract) with two settings changed, because with lumis-sdk c757a74 the
built-in OpenRouter path cannot run DeepSeek models:

1. `parallel_tool_calls: False` is dropped. The SDK sends it alongside OpenRouter
   `require_parameters: true`; no DeepSeek endpoint advertises that parameter, so OpenRouter
   filters every endpoint out (HTTP 404 "No endpoints found that can handle the requested
   parameters"). Tool execution stays sequential: both tools are registered `sequential=True`.
2. Validation retries (tool arguments and final output) are 2 instead of 0. A single malformed tool call (e.g. an
   empty string in an optional field) otherwise aborts the whole investigation; with retries the
   model gets the validation error back and corrects it.
3. Experiment mode (`unbounded=True`): no pydantic-ai request/tool/output-token caps, so the model
   can "do its own thing". The SDK tool broker's own budgets (lumis.yaml `investigator.budget`,
   evidence-query and character limits) still apply; set them high in the experiment config.
4. An output validator applies the SDK's own acceptance checks (`validate_hypothesis`, known
   evidence/receipt references) *before* the run ends and returns failures to the model with
   `ModelRetry`. The SDK discards the whole output if any hypothesis is invalid (e.g. a git
   receipt named in `evidence_needed`, or a revised hypothesis that reuses the id it registered
   earlier); this lets the model repair it instead. Acceptance rules
   are unchanged; the SDK re-validates everything afterwards.
5. Reasoning is requested (`openrouter_reasoning`) and OpenRouter usage accounting is on
   (`openrouter_usage`); the complete message history, including thinking parts, is kept on
   `self.transcript` even when the run fails (`capture_run_messages`).

Everything else is unchanged: OpenRouter routing fallback disabled, required-parameter support
requested, no transport retries, the credential read from the configured environment variable.
Remove this module once the SDK investigator includes equivalent changes.
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from lumis_sdk.core.contracts import validate_hypothesis
from lumis_sdk.investigation import agent as sdk_agent
from lumis_sdk.investigation.contracts import AgentOutput, Finding
from lumis_sdk.investigation.tools import InvestigationTools
from lumis_sdk.runtime.project import ModelSettings
from lumis_sdk.security.redaction import redact_text
from pydantic_ai import Agent, ModelRetry, RunContext, Tool, capture_run_messages
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.usage import RunUsage, UsageLimits

TOOL_RETRIES = 2


class OpenRouterInvestigator(sdk_agent.PydanticInvestigator):
    def __init__(self, model, *, model_settings=None, unbounded: bool = False) -> None:  # noqa: ANN001
        self.unbounded = unbounded
        self.transcript: bytes = b"[]"
        self.error: str | None = None
        self.agent = Agent(
            model,
            deps_type=InvestigationTools,
            output_type=AgentOutput,
            instructions=sdk_agent.INSTRUCTIONS,
            retries=TOOL_RETRIES,
            tools=[
                Tool(sdk_agent.inspect, sequential=True, strict=True),
                Tool(sdk_agent.probe, sequential=True, strict=True),
            ],
            model_settings=model_settings,
        )

        @self.agent.output_validator
        async def _sdk_acceptance(ctx: RunContext[InvestigationTools], output: AgentOutput) -> AgentOutput:
            tools = ctx.deps
            problems = []
            for hypothesis in output.hypotheses:
                try:
                    validate_hypothesis(hypothesis, tools.context)
                except ValueError as exc:
                    problems.append(f"hypothesis {hypothesis.id}: {exc}")
                registered = tools.candidates.get(hypothesis.id)
                redacted = hypothesis.model_copy(update={"statement": redact_text(hypothesis.statement)})
                if registered is not None and registered != redacted:
                    problems.append(
                        f"hypothesis {hypothesis.id} differs from the version registered earlier; "
                        "return it unchanged or give the revision a new id")
            ids = {h.id for h in output.hypotheses}
            evidence = {e.id for e in tools.context.evidence}
            receipts = {r.id for r in tools.receipts}
            for suggestion in output.suggestions:
                if suggestion.hypothesis_id not in ids:
                    problems.append(f"suggestion references unknown hypothesis {suggestion.hypothesis_id}")
                if not set(suggestion.evidence_ids) <= evidence:
                    problems.append(f"suggestion cites unknown evidence {sorted(set(suggestion.evidence_ids) - evidence)}")
                if not set(suggestion.receipt_ids) <= receipts:
                    problems.append(f"suggestion cites unknown receipts {sorted(set(suggestion.receipt_ids) - receipts)}")
            if problems:
                raise ModelRetry(
                    "Lumis would reject this output: " + "; ".join(problems) + ". evidence_needed may only "
                    "list registered query ids from inspect(catalog); predictions/falsifiers must use "
                    "entity/key pairs those queries observe; cite code/git receipts via suggestion receipt_ids.")
            return output

    async def run(self, tools: InvestigationTools, findings: tuple[Finding, ...],
                  usage: RunUsage) -> AgentOutput:
        prompt = (tools.context.model_dump_json() + "\nDeterministic findings:\n"
                  + "\n".join(finding.model_dump_json() for finding in findings))
        if len(prompt) > tools.budget.max_context_characters:
            raise ValueError("agent bundle exceeds context character budget")
        limits = tools.settings.budget
        usage_limits = (UsageLimits(request_limit=None, tool_calls_limit=None,
                                    output_tokens_limit=None) if self.unbounded else
                        UsageLimits(request_limit=limits.request_limit,
                                    tool_calls_limit=limits.tool_calls_limit,
                                    output_tokens_limit=limits.output_tokens_limit))
        settings = {} if self.unbounded else {"max_tokens": tools.budget.max_model_output_tokens}
        with capture_run_messages() as messages:
            try:
                result = await self.agent.run(prompt, deps=tools, usage=usage,
                                              usage_limits=usage_limits, model_settings=settings)
            except Exception as exc:
                self.error = f"{type(exc).__name__}: {exc}"[:2000]
                raise
            finally:
                self.transcript = ModelMessagesTypeAdapter.dump_json(messages, indent=1)
        return AgentOutput.model_validate(result.output.model_dump())


@asynccontextmanager
async def openrouter_investigator(settings: ModelSettings, *, timeout: float,
                                  unbounded: bool = False, reasoning: str | None = "high",
                                  ) -> AsyncIterator[OpenRouterInvestigator]:
    import httpx2
    from openai import AsyncOpenAI
    from pydantic_ai.models.openrouter import OpenRouterModel
    from pydantic_ai.providers.openrouter import OpenRouterProvider

    if settings.provider != "openrouter":
        raise ValueError("this workaround only wraps the OpenRouter provider")
    key = os.environ.get(settings.credential_env)
    if not key:
        raise ValueError("configured investigator credential is absent")
    async with (
        httpx2.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False) as http,
        AsyncOpenAI(api_key=key, max_retries=0, http_client=http,
                    base_url="https://openrouter.ai/api/v1") as client,
    ):
        model_settings: dict = {
            "openrouter_provider": {"allow_fallbacks": False, "require_parameters": True},
            "openrouter_usage": {"include": True},
        }
        if reasoning:
            model_settings["openrouter_reasoning"] = {"effort": reasoning, "exclude": False}
        yield OpenRouterInvestigator(
            OpenRouterModel(settings.model, provider=OpenRouterProvider(openai_client=client)),
            model_settings=model_settings, unbounded=unbounded,
        )
