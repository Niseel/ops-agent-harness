# 0003. LLM: raw OpenAI SDK, any OpenAI-compatible provider, fake by default

Status: Accepted · Date: 2026-09-26

## Context

The harness must detect malformed LLM replies precisely: truncated output, empty replies, tool arguments that are not JSON, unknown tool names. Reviewers may not have an API key or a local model server, but must still be able to run the whole flow.

## Options

| Option | Pros | Cons |
|---|---|---|
| Raw `openai` SDK, plain dict messages | Sees the raw reply (`arguments` string, `finish_reason`). Few dependencies. Easy to fake. | No LangChain prebuilt helpers. |
| `langchain-openai` `ChatOpenAI` | LangChain ecosystem, parses invalid tool calls for us. | Hides the raw reply. A fake must implement `BaseChatModel`. More dependencies. |
| Claude native API | Very reliable tool calling. | Needs a paid key for every demo. No local option. |
| Both behind an interface | Flexible. | Twice the adapters and tests for no requirement. |

## Decision

- One client on `openai.AsyncOpenAI` `chat.completions` with `tools`. It works with LM Studio, OpenAI, OpenRouter (including Claude models) and Groq by changing `LLM_*` env vars only.
- Two test doubles:
  - `FakePlanner`: rule-based. Search the KB, check the service, propose an incident if the service is degraded or down, then answer.
  - `ScriptedLLM`: returns a fixed list of replies, for tests.
- The LLM mode is chosen per run (`fake` or `openai`). The default comes from `LLM_DEFAULT=fake`.

## Consequences

- The reply classifier works on raw data, so each malformed case is testable.
- The demo runs offline with no key.
- `FakePlanner` is not intelligent. It exists to show the harness mechanics, not model quality.
- Some local models write tool calls as text in `content` instead of `tool_calls`. We do not parse that; the harness treats it as a final answer or a malformed reply.
