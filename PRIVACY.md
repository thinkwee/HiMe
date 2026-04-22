# HiMe Privacy Policy

_Last updated: 2026-04-11_

HiMe (Health Intelligence Management Engine) is a self-hosted application. When you run HiMe on your own machine, **you** are the operator of the service, and you decide what health data enters the system, where it is stored, and which third parties (if any) it is sent to. This document describes what HiMe does with data by default, so that you can make an informed decision before you run it.

## 1. Who this policy applies to

This policy applies to:

- The official HiMe iPhone app and its watchOS companion app.
- The HiMe self-hosted backend (FastAPI server, React frontend, Watch Exporter service) that users deploy on their own hardware.

It does **not** apply to any third-party LLM provider, messaging platform, or infrastructure provider that you, the operator, choose to connect HiMe to. Those services have their own privacy policies.

## 2. Data HiMe reads from HealthKit

With your explicit permission (granted through the standard iOS HealthKit authorisation sheet), the HiMe iPhone and watchOS apps read the following categories of health data from Apple HealthKit. HiMe only reads; it does not write back to HealthKit.

- **Cardiovascular**: heart rate, resting heart rate, walking heart rate average, heart rate variability (HRV SDNN), heart rate recovery, VO2 max.
- **Respiratory**: blood oxygen saturation (SpO2), respiratory rate.
- **Body**: body mass, body fat percentage, lean body mass, body temperature, basal body temperature.
- **Activity**: step count, distance walked/run, distance cycled, active energy burned, basal energy burned, flights climbed, exercise minutes, stand hours, move minutes, walking speed, walking asymmetry.
- **Workouts**: workout sessions across 14 activity types, with type, duration, energy, and distance.
- **Sleep**: sleep analysis segments (in bed, asleep, core, deep, REM, awake).
- **Mindfulness**: mindful sessions (duration only).
- **Environmental**: headphone audio exposure, environmental audio exposure.
- **Hydration, nutrition, and other optional metrics** supported by HealthKit that the user has enabled in the app.

HiMe does **not** read clinical records, medications, menstrual cycle data, or sexual-activity data from HealthKit.

## 3. Where data is stored

By default, all of your health data is stored in **two places, both of which you control**:

1. **On your iPhone and Apple Watch**, in the operating system's protected HealthKit store and in local app sandboxes. Apple, not HiMe, governs this storage.
2. **On your self-hosted HiMe backend**, in local SQLite files inside the `data/`, `memory/`, and `ios/Server/watch.db` directories of your HiMe installation. These files are on hardware that you operate. HiMe does not upload them anywhere.

HiMe has no central server, no user accounts, no authentication service, and no cloud database operated by the project. There is no "HiMe account" to sign up for.

## 4. Data retention

The HiMe backend applies a **rolling 30-day retention window** to raw health samples by default. Samples older than 30 days are deleted from local storage on a periodic sweep. Derived artefacts (agent reports, the agent's memory database, personalised pages, evidence trails) are retained until you delete them. You can shorten or disable retention in your own deployment, and you can wipe everything at any time with `./hime.sh reset`.

## 5. Data flow to third-party LLM providers

HiMe's core value is an autonomous LLM agent that reasons over your health data. To do that reasoning, the agent sends **health-derived content** (tool-call results, query outputs, numeric summaries, chart data, short text snippets of the user's own messages) to whichever LLM provider you have configured in `.env`.

**Enabling an LLM provider means you are sending health-derived data to that provider.** The agent tries to minimise what is sent (it uses SQL queries and aggregates, not raw sample dumps), but you should assume that any numeric or textual health statistic the agent computes may reach the provider as part of a prompt, and any response from the LLM may reach your local storage.

HiMe currently supports the following 15 providers. You choose one (or more) by setting `DEFAULT_LLM_PROVIDER` and the corresponding API key:

1. Google Gemini
2. Google Vertex AI
3. OpenAI
4. Azure OpenAI
5. Anthropic Claude
6. Mistral
7. Groq
8. DeepSeek
9. xAI (Grok)
10. OpenRouter
11. Perplexity
12. Amazon Bedrock
13. vLLM (self-hosted; no third party unless you expose it)
14. Zhipu AI (GLM)
15. MiniMax

Each provider operates under its own terms of service and privacy policy. HiMe is not a party to the relationship between you and the provider. If you want to keep all inference local, configure the vLLM provider pointed at a model running on your own hardware.

Every LLM API call is logged to `logs/llm_api.csv` on your local machine so that you can audit exactly what was sent and received.

## 6. Messaging gateways (Telegram, Feishu)

HiMe can optionally talk to you through a messaging platform so that you do not have to keep the web dashboard open.

- **Telegram** and **Feishu (Lark)** gateways are **disabled by default**. They are enabled only when you explicitly set the corresponding environment variables and create a bot on the respective platform.
- When enabled, both gateways are **default-deny**: the agent will only talk to chat IDs you explicitly allow. An empty allow-list blocks all inbound chat.
- Messages you exchange with the agent over these platforms pass through that platform's servers, subject to the platform's own privacy policy.

You can disable either gateway at any time by removing its configuration and restarting HiMe.

## 7. Tracking, analytics, and advertising

HiMe contains **no** third-party analytics SDKs, **no** advertising SDKs, **no** crash reporters that phone home, and **no** usage telemetry that is sent to the project. The project has no ability to observe your installation remotely.

HiMe does **not** sell, rent, or share user data with any third party. HiMe has no data to sell, because it never receives your data in the first place.

## 8. Data deletion

Because HiMe is self-hosted, deleting your data is something you do directly on your own device or server.

- On the iPhone app: deleting the app from iOS removes all local HiMe caches and revokes HealthKit permission.
- On the backend: run `./hime.sh reset` to wipe every health sample, agent memory, report, log, and personalised page. You can also delete the `data/`, `memory/`, and `logs/` directories manually.
- HealthKit data itself is managed by iOS; use the Health app to remove it.

## 9. Children

HiMe is not intended for use by children under 13. Apple HealthKit itself imposes age restrictions on data sharing for minors, and HiMe does not attempt to work around them. If you believe a child under 13 has provided health data to a HiMe instance you operate, delete it using the process above.

## 10. Security

- All inter-process communication between HiMe components happens over `localhost` by default.
- When you expose HiMe beyond `localhost`, set `API_AUTH_TOKEN` in `.env` to enforce bearer-token authentication on every API and WebSocket route.
- Personalised pages are sandboxed at import time and served with a strict Content-Security-Policy (`default-src 'self'; frame-ancestors 'none'`).
- SQL passed to the agent's memory tools is validated against a small allow-list of statements.

No software is perfectly secure. HiMe is research-grade software and is provided under the PolyForm Noncommercial License without warranty.

## 11. Medical disclaimer

HiMe is not a medical device and produces no diagnoses. Nothing in HiMe should be interpreted as medical advice. Always consult a qualified clinician for any decision about your health.

## 12. Changes to this policy

If this policy changes, the new version will be committed to the repository and the "Last updated" date at the top will be revised. There is no mailing list to notify you; check the repository for updates.

## 13. Contact

Questions about this policy can be sent to:

`thinkwee2767@gmail.com`
