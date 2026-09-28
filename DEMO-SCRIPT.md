# JudgeForge five-minute demo plan

Record the actual application and terminal; do not substitute slides or simulated output. Use a separate open demo event so the official fixture event keeps its original closed deadline.

0:00–0:35 — Introduce JudgeForge: a local submission and fair judging portal using FastAPI, SQLite and bundled UI assets with zero cloud dependencies. Show the public repository and `docker compose ps`. Show the actual health status (`healthy`) from `docker inspect --format '{{.State.Health.Status}}' judgeforge-app`.

0:35–1:10 — Open http://localhost:8000/projects. Show seeded projects, text search, track filtering and project detail. Explain that the fixture event is closed; demonstrate deadline rejection without changing its date.

1:10–2:20 — Sign in as organizer/admin. Open Your workspace (`/workspace`). Show event prizes and create a separate event with tracks, prizes, and a future deadline. In a participant browser session, register/sign in, create a team, create an invite link, save a project draft (`status="draft"`), edit the draft, and then explicitly finalize submission (`status="submitted"`). Show that drafts are hidden from the public gallery until finalized. Avoid exposing real passwords; use only demo accounts.

2:20–3:20 — Show the seeded judge dashboard and scoring screen (`/judge`). Explain track assignments and private score access. Show the acceptance checker denying peer-score and participant access.

3:20–4:10 — Show organizer command center (`/organizer`): progress metrics, rubric weights, prize management, and results publication controls. If signed in as Admin (`admin@judgeforge.local`), show user role management. Demonstrate publishing official results (`POST /api/events/{id}/publish`) and show the public results showcase at `/results` with podium rankings, category prizes, and strict judge privacy (no individual scores/identities exposed). Export official CSV.

4:10–4:45 — Run `python run.py .dogfood.toml` on camera and show all seven PASS results. Run `pytest -v` and show 27 passed tests. Mention that the operator also tested restart and acceptance with Wi-Fi disabled after the image was built.

4:45–5:00 — Show docs, data model, and MIT license. State honest limits: T1 and T2 complete with lifecycle extensions; T3/T4 (public voting, community comments, outbound webhooks) deliberately not claimed. End with the repository URL.

Upload the actual recording to a judge-accessible location, verify playback without your login, and replace the README demo placeholder with that real URL. Do not invent a video link.
