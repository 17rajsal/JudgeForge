# JudgeForge five-minute demo plan

Record the actual application and terminal; do not substitute slides or simulated output. Use a separate open demo event so the official fixture event keeps its original closed deadline. The current app lacks a publish-results workflow; say so explicitly instead of pretending CSV export is public publishing.

0:00–0:35 — Introduce JudgeForge: a local submission and judging portal using FastAPI, SQLite and bundled UI assets. Show the public repository and `docker compose ps`. Show the actual health status from `docker inspect --format '{{.State.Health.Status}}' judgeforge-app`.

0:35–1:10 — Open http://localhost:8000/projects. Show seeded projects, text search, track filtering and project detail. Explain that the fixture event is closed; demonstrate rejection without changing its date.

1:10–2:20 — Sign in as organizer. Open Your workspace. Create a separate event with one track and a future deadline. In a separate browser session register/sign in as a participant, create a team, create an invitation link, and submit a project to the new event. Show project editing. Avoid exposing any real passwords; use only demo accounts.

2:20–3:20 — Show the seeded judge dashboard and scoring screen. Explain track assignments and private score access. Show the acceptance checker denying peer-score and participant access. Do not claim the new event is fully judged unless its judge assignment and scoring were actually completed.

3:20–4:10 — Show organizer progress, rubric weights, normalized results, and CSV export for the fixture data. Explain per-judge normalization and safe handling of zero variance. Mention the current aggregate reporting limitation across events.

4:10–4:45 — Run `python run.py .dogfood.toml` on camera and show all seven results. Mention that the operator also tested restart and acceptance with Wi-Fi disabled after the image was built.

4:45–5:00 — Show docs and MIT license. State honest limits: no distinct admin role, prize configuration, saved drafts or results publication; T3/T4 not claimed. End with the repository URL.

Upload the actual recording to a judge-accessible location, verify playback without your login, and replace the README demo placeholder with that real URL. Do not invent a video link.
