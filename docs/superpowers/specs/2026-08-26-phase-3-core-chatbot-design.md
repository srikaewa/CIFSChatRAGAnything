# Phase 3 Core Chatbot Completion Design

Date: 2026-08-26
Branch: `codex/phase3-core-chatbot`
Base: `40f8f8b` (`codex/phase2-stabilization`)

## Goal

Complete and verify the production chatbot path for LINE, Messenger, and Telegram while preserving the existing FastAPI/Jinja/SQLModel architecture and Phase 2 security controls.

## Scope

Phase 3 covers provider configuration contracts, inbound-to-outbound message flows, deterministic chatbot decision behavior, escalation/admin notification behavior, RAG/fallback behavior, and Test Chat parity. Knowledge graph browser verification, Tailscale portability, Windows installation documentation, README cleanup, and dependency-warning cleanup remain assigned to later phases.

## Provider State Contract

Every supported channel exposes one of four operator-facing states:

- `disabled`: saved channel is disabled; webhook POST returns `{"processed": 0}` without parsing or external work, and Messenger verification returns 403.
- `incomplete`: channel is enabled but one or more required credentials are missing; webhook/verification returns 503 with a stable provider-not-ready message before parsing or external work.
- `ready`: channel is enabled and all required credentials are present; authenticity validation and normal processing are allowed.
- `failed`: channel is enabled and configured, but a persisted provider setup/operational status is failed; webhook/verification returns 503 until configuration or setup is corrected.

Legacy persisted statuses `configured` and `not_configured` remain readable and normalize to `ready` and `incomplete` respectively. New saves persist `disabled`, `ready`, or `incomplete`. Provider setup failures may persist `failed` without exposing raw exception text.

The Channels page must show these states explicitly rather than reducing them to only Ready/Not configured.

## Provider Request Contract

For LINE, Messenger, and Telegram:

1. Resolve saved channel state before reading/parsing the body.
2. Disabled channels stop cleanly as established in Phase 2.
3. Incomplete or failed channels return 503 before adapter parsing or provider API calls.
4. Ready channels validate provider authenticity before JSON parsing.
5. Authenticated malformed JSON returns 400 with a stable message.
6. Unsupported/non-text events return `{"processed": 0}`.
7. Valid text events run the shared chatbot engine, persist the decision, send the reply, and preserve deterministic failure codes when sending fails.

Messenger verification uses the same state contract: disabled returns 403, incomplete/failed returns 503, ready performs token verification.

## Chatbot Decision Contract

The existing `ChatbotEngine` remains the single decision implementation for live channels and Test Chat. The required order is:

1. built-in commands (`help`, `start`)
2. enabled rules ordered by priority, including extra conditions
3. escalation as a property of the matched rule
4. RAG when enabled and no rule matched
5. configured fallback reply

No provider-specific business decision logic is added. Provider adapters only validate/parse provider requests and send replies.

## Escalation and Admin Notification

Phase 3 supports Telegram as the only admin notification destination. LINE and Messenger notification destinations are not implemented in this phase and must not be presented as selectable supported choices.

Assistant configuration rules:

- notification channel must be `telegram`;
- destination may be blank, meaning escalation replies are sent to users but admin notification is skipped;
- a nonblank Telegram chat ID must be a signed integer string (negative group/channel IDs are allowed);
- invalid/tampered values are rejected with stable user-facing configuration errors.

Notification execution returns an explicit internal result (`sent`, `skipped`, or `failed`) rather than relying on unchecked `int()` conversion. A skipped or failed notification never suppresses a successful user reply. Failures append stable event error codes and logs contain provider/context plus error type/status, never raw exception values or credentials.

## RAG and Test Chat

RAG remains behind the existing `RagService` abstraction. Phase 3 verifies:

- RAG answer used when no command/rule matches;
- empty RAG answer falls back;
- RAG exception falls back with `response_generation_failed`;
- knowledge document status continues to expose pending/indexed/failed states and retry behavior;
- Test Chat calls the same `ChatbotEngine` and produces the same decision source/reply for equivalent inputs.

Test Chat intentionally does not send a provider reply or an admin escalation notification; the UI documents this difference.

## Testing Strategy

Use TDD for every behavior change. Add contract-focused tests for channel states, malformed payloads, escalation validation/results, Messenger inbound-to-outbound flow, and Test Chat/live-engine parity. Existing adapter and Phase 2 security tests remain regression coverage.

External LINE, Messenger, Telegram, LLM, and RAG calls are mocked. No real credentials are required.

## Completion Gate

Phase 3 is complete when:

- LINE, Messenger, and Telegram each have a mocked inbound-to-outbound text flow;
- disabled, incomplete, ready, and failed configuration contracts are deterministic and tested;
- malformed authenticated JSON and unsupported events have explicit behavior;
- command, rules/conditions, escalation, RAG, and fallback ordering is deterministic and tested;
- GAP-011 is closed with safe Telegram destination validation and explicit notification outcomes;
- Test Chat parity with the shared engine is proven, with provider-only side effects documented;
- full regression suite, Python compilation, and `git diff --check` pass;
- a Phase 3 verification report records fresh evidence.

## Non-Goals

- New messaging providers.
- Admin notifications through LINE or Messenger.
- Knowledge graph browser verification.
- Tailscale/Windows operational refactoring.
- Database migration framework introduction.
- FastAPI/Jinja/SQLModel/SQLite replacement.
