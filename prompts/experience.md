# Experience — Agent's Own Notes

> Initially empty. Append things you have **actually learned** from
> running on this user's data — surprising data quirks, gotchas you
> hit and the workaround, schema details that aren't in
> `data_schema.md`, edge cases worth remembering next time.
>
> Use `update_md(file="experience.md")` to append below the marker.
> Do **not** record:
> - Schema facts that already live in `data_schema.md` (don't duplicate)
> - Analysis methodology — that belongs in user-written `skills/`
> - Health baselines, "normal" ranges, or judgements about the user
> - Conclusions or findings from any single analysis

## Default behaviour rule (applies to every user — do not edit)

**When the user expresses doubt about, disputes, or asks you to re-check an
answer, re-invoke the data tools and re-derive the answer from fresh tool
results in that same turn.** Do not just paraphrase or restate the response
being questioned — that response is the thing under dispute. Claiming to have
re-checked without an accompanying `sql` / `code` / `analyze` call in the same
turn is fabrication. This holds even when the disputed numbers appear earlier in
the chat history: prior history is not evidence for the current turn when the
user is challenging it — only a fresh tool call is.

<!-- Agent: append your real learnings below this line. -->
<!-- Agent: editable content below. -->
