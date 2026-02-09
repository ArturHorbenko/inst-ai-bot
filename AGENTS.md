# Repository Guidelines

## Project Structure & Module Organization
- `server.py`: FastAPI entrypoint for upload, job tracking, and analysis endpoints.
- `video_processor/`: Python processing pipeline (scene detection, OCR, transcription, summarization, multimodal analysis, DB access, config).
- `web/`: Next.js + TypeScript frontend (`src/app` for routes, `src/components` for UI).
- `auth/`: lightweight auth prototype files.
- `prompts/`: prompt templates used by Node/Python analysis flows.
- `docs/`: planning notes and implementation plans.

## Build, Test, and Development Commands
- `python -m venv venv && source venv/bin/activate && pip install -r requirements.txt`: set up backend dependencies.
- `npm install` and `npm install --workspace=web`: install root + frontend packages.
- `npm run dev:backend`: run FastAPI dev server.
- `npm run dev:frontend`: run Next.js app on `localhost:3000`.
- `npm run dev`: run backend and frontend together.
- `npm run build:frontend`: production build for the web app.
- `npm run lint --workspace=web`: run Next/ESLint checks.

## Coding Style & Naming Conventions
- Python: PEP 8 style, 4-space indentation, `snake_case` functions/modules, `PascalCase` classes.
- TypeScript/React: follow existing style in `web/src` (single quotes, semicolons, functional components, `PascalCase` component filenames).
- Keep modules focused; add new pipeline logic under `video_processor/` instead of expanding `server.py`.

## Testing Guidelines
- Backend test command currently points to a missing file (`npm run test:backend` -> `test_server.py`). Add/update tests before relying on CI.
- Place backend tests under `tests/` using `test_*.py` naming.
- For frontend changes, run `npm run lint --workspace=web` and add component tests when introducing non-trivial logic.

## Commit & Pull Request Guidelines
- Existing history uses short imperative commits (for example: `add web`, `update plan`, `requirements update`). Keep this pattern.
- Use focused commits per concern; avoid mixing backend, frontend, and prompt refactors in one commit.
- PRs should include: purpose, key files changed, local verification steps, and screenshots/GIFs for UI updates.

## Security & Configuration Tips
- Configure secrets through environment variables (`OPENAI_API_KEY`, `TWELVE_LABS_API_KEY`, `MONGODB_URI`, `MONGODB_DB`); never commit secrets.
- Use `.env` locally and sanitize logs/output before sharing traces or sample payloads.
