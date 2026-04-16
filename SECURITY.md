# Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| 1.0.x | Yes |
| < 1.0 | No |

## Reporting a vulnerability

If you believe you have found a security vulnerability in HIME, **please do not open a public GitHub issue**. Instead, report it privately by opening a [GitHub security advisory](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability) on this repository.

When reporting, please include:

- A description of the issue and its potential impact.
- Steps to reproduce, ideally with a minimal proof of concept.
- The affected version / commit hash.
- Any suggested mitigation, if you have one.

We aim to acknowledge new reports within 7 days and provide a remediation plan within 30 days for confirmed vulnerabilities.

## Scope

HIME processes sensitive personal health data and runs an autonomous LLM agent that can execute code, write SQL, and render HTML. We are particularly interested in reports concerning:

- Authentication / authorization bypass on the FastAPI backend.
- Code execution flaws in the `code` tool sandbox or in agent-generated personalised pages.
- SQL injection or path traversal in any tool or API endpoint.
- Leakage of secrets from `.env`, memory DBs, or logs.
- Cross-site scripting in agent-generated HTML served by `/api/personalised-pages/`.
- Messaging gateway authorization issues — unauthorized `chat_id`s interacting with the agent on either Telegram or Feishu, or bypass of the default-deny allowlist.
- **Prompt injection / jailbreak** attacks against the agent that steer it into calling tools in unintended ways — for example, a malicious string in synced health data or user-supplied text coercing the agent to run harmful Python through the `code` tool, write to unauthorised memory tables, or exfiltrate data.
- **Tool-definition fuzzing** — malformed tool arguments (SQL payloads, unsafe imports in `create_page`, resource-exhausting inputs to `code`) that bypass the validation in `backend/agent/tools/` or `page_helpers.py`.
- **Resource-exhaustion / DoS** via agent loops — unbounded `code` execution, runaway `sql` queries, or context-overflow retry storms.

## Out of scope

- Issues that require physical access to the user's machine.
- Self-XSS or social engineering against a user with admin access to their own deployment.
- Vulnerabilities in third-party LLM providers or APIs that HIME calls into. Note that health-related data is sent to whichever LLM provider the operator configures; that provider is outside HIME's trust boundary. See [`PRIVACY.md`](PRIVACY.md) for the full data-flow description.
- Agent hallucinations or factually incorrect analysis that is not caused by a code defect. The `fact_verifier` and evidence buttons surface the tool-call trail, but the agent is a research-grade LLM, not a medical device.

## Privacy notes for operators

HIME is designed to be self-hosted. By default it stores all health data, agent memory, and chat history locally on the operator's machine. The platform does not phone home. If you enable the Telegram gateway, messages are routed through Telegram's infrastructure under the terms of your bot.

If you intend to expose HIME beyond `localhost`, you **must** configure authentication (`API_AUTH_TOKEN`) and tighten `CORS_ORIGINS`. See `docs/DEPLOYMENT.md` for guidance.
