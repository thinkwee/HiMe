## Summary

What does this PR do and why?

## Scope of changes

- [ ] Backend (Python / FastAPI)
- [ ] Frontend (React / Vite)
- [ ] iOS app
- [ ] watchOS app
- [ ] Agent prompts or tools
- [ ] Messaging gateways
- [ ] Docs / tests / tooling only

## Testing

How did you verify the change works? Include commands, steps, or screenshots.

- [ ] `python -m pytest tests/ -x -q` passes
- [ ] `npm run build` passes (if frontend touched)
- [ ] Manual smoke test on at least one entry point (chat / cron / iOS quick check)

## Checklist

- [ ] No secrets, tokens, API keys, or personal data committed
- [ ] No Chinese characters in code, tests, or committed docs
- [ ] New user-facing strings are added to i18n locale files (frontend `en.json`/`zh.json`, backend `backend/i18n/locales/`, iOS `Localizable.xcstrings`)
- [ ] New env vars are documented in `.env.example`
- [ ] New features respect the three Agent Design Principles in `CLAUDE.md`
- [ ] By submitting this PR I agree to license my contribution under the project's PolyForm Noncommercial License 1.0.0
