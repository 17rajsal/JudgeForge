# JudgeForge Five-Minute Demo Plan

Record the actual application and terminal; do not substitute slides or simulated output. Use a separate open demo event so the official fixture event keeps its original closed deadline. Keep the total video time at approximately 5 minutes.

---

### Timing & Sequence

- **0:00–0:30 — Introduction & Docker Health**
  - Introduce JudgeForge: a self-hosted, offline-first hackathon submissions and fair judging portal built with FastAPI, SQLite, and vanilla assets.
  - Show terminal: run `docker compose ps` showing running container.
  - Run `docker inspect --format '{{.State.Health.Status}}' judgeforge-app` to display the literal `healthy` status.

- **0:30–1:15 — Event, Team, and Submission Workflow**
  - Open `http://localhost:8000/projects`. Show seeded projects, track filtering, and search.
  - Explain that the seeded fixture event (`evt_01`) is closed; demonstrate server-side deadline rejection without altering fixture dates.
  - Open workspace (`/workspace`) as participant: create a team, generate a 48-hour single-use invite link, save a project draft (`status="draft"`), edit it, and explicitly submit/finalize it (`status="submitted"`).
  - Demonstrate that draft projects remain private and are hidden from the public gallery until finalized.

- **1:15–2:15 — Judge Scoring & Strict Peer Isolation**
  - Sign in as Judge A (`tomas.varga@example.org`). Open judge portal (`/judge`).
  - Show track assignments and evaluate a project against weighted rubric criteria (functionality, quality, innovation) with qualitative notes.
  - Demonstrate strict server-side peer isolation: show that Judge A cannot inspect Judge B's scores, and attempts to access peer evaluations return `HTTP 403 Forbidden`.

- **2:15–3:15 — Organizer Command Center, Normalization, & Results**
  - Sign in as Organizer (`organizer@judgeforge.local`). Open `/organizer`.
  - Review judging progress metrics and rubric weight adjustments.
  - Explain Z-score normalization with zero-variance defense: how reviewer variance is standardized to eliminate harsh vs. lenient bias.
  - Show event prizes configuration. Demonstrate publishing official results (`POST /api/events/{id}/publish`).
  - Open public `/results` page showing podium rankings, prize awards, and strict judge privacy (no individual reviewer identities or scores exposed). Download official RFC-compliant CSV export.

- **3:15–4:00 — T3 Community Voting & Comments**
  - Open Community Voting portal (`/events/{id}/vote`).
  - Briefly demonstrate access modes (`open`, `authenticated`, `email_gated` with single-use vouchers), duplicate vote prevention, and randomized ballot ordering.
  - Show that vote counts remain hidden from public view while voting is active.
  - Open a project page, submit a community comment, and show organizer moderation/flagging.

- **4:00–4:30 — T4 REST API, Verifiable Records, Certificates, & Embed**
  - Open FastAPI interactive OpenAPI docs at `http://localhost:8000/docs`.
  - Briefly highlight the full `/api/v1` REST coverage (do not attempt to demo every endpoint).
  - Show Ed25519 public key at `GET /api/v1/verifiable-records/public-key` and demonstrate that public judge records verify participation without leaking raw scores or comments.
  - Show offline SVG certificate generation (`/api/v1/certificates`) with cryptographic SHA-256 integrity fingerprints.
  - Show responsive gallery embed at `http://localhost:8000/embed/gallery`.

- **4:30–4:50 — Verification & Acceptance Check**
  - In terminal, execute the official hackathon acceptance suite:
    ```bash
    python run.py .dogfood.toml
    ```
    Show all 7 checks passing (`7/7 PASS`).
  - In terminal, run the automated test suite:
    ```bash
    pytest -v
    ```
    Show **47 passed** tests across authentication, submissions, judging, isolation, community voting, and enterprise API features.

- **4:50–5:00 — Repository, Documentation, & License**
  - Show GitHub repository, `spec.md`, `ARCHITECTURE.md`, `DATA-MODEL.md`, `JUDGING.md`, and MIT license.
  - Note honest tier claim: `.dogfood.toml` claims `T1` and `T2` to match the official automated checker scope, while full T3 and T4 capabilities are implemented and open for manual review.
  - End on repository URL: https://github.com/17rajsal/JudgeForge

---

### Recording Note
Upload the actual recording to a judge-accessible location, verify playback without personal logins, and update the README demo link with that real URL. Do not invent a simulated video link.
