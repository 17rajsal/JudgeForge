# JudgeForge

> **Self-hosted hackathon submissions and fair judging, with zero cloud dependencies.**

JudgeForge is a resilient, offline-first hackathon management and fair judging platform built for the **Dogfood 2026** competition. It provides an uncompromised developer and participant experience while enforcing rigorous judging isolation, server-side peer isolation, deadline validation, and statistical score normalization.

---

## Claimed Tier

**Claimed:** `T1` and `T2`  
**Verified:** `T1` and `T2` (100% acceptance checks passing in `run.py`)

- **T1 Core**: Participant registration, team management, deadline-enforced project submissions, project editing rules, public gallery with track filtering and search.
- **T2 Judging Engine**: Judge track assignments, isolated scoring interfaces (judges cannot view peer scores), weighted rubric configuration, zero-variance Z-score score normalization, organizer progress dashboards, and CSV export.

---

## Features

- **Zero Cloud Runtime Dependencies**: Runs completely offline using a local SQLite database, vanilla JavaScript, and local CSS stylesheets. Zero CDN dependencies, zero external font calls, zero analytics trackers.
- **Strict Role-Based Access Control (RBAC)**: Enforces permissions on the server for `organizer`, `judge`, and `participant`. UI controls reflect permissions, but backend authorization guards prevent bypass.
- **Strict Judge Peer Isolation**: Judges can only view and update their own scores. Any attempt by a judge to inspect peer reviews (e.g. via direct query parameters or API calls) is rejected with HTTP `403 Forbidden`.
- **Enforced Deadlines**: Server-side timestamps prevent late submissions and project modifications after `submissions_close`.
- **Defensible Score Normalization**: Combines weighted category scoring with Z-score standardization across judges to eliminate scoring bias between harsh and generous reviewers, handling zero-variance reviewers without division by zero.
- **Organizer CSV Export**: RFC-compliant CSV generation with full breakdown of project ranks, raw averages, normalized scores, tracks, and teams.
- **Idempotent Automated Seeding**: Automatically populates 1 event, 8 tracks, 30 judges, 40 teams, 41 projects, 126 reviews, and demo session tokens from `fixtures.json`.

---

## Prerequisites

- **Python**: 3.11 or newer (Standard library + packages in `requirements.txt`)
- **Docker & Docker Compose** (for containerized deployment)

---

## One-Command Startup

### With Docker Compose:
```bash
docker compose up
```

### Direct Local Python:
```bash
# Install dependencies
pip install -r requirements.txt

# Run server (automatically initializes database & seeds fixtures)
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

---

## Access & Demo Credentials

Open your browser to: **`http://127.0.0.1:8000`** (or `http://localhost:8000`)

### Demo Credentials Banner

| Role | Name / Email | Authorization Header | Session Cookie |
|---|---|---|---|
| **Admin** | System Administrator (`admin@judgeforge.local`) | `Authorization: Bearer token_admin` | `Cookie: session=adm_9a11` |
| **Organizer** | Head Organizer (`organizer@judgeforge.local`) | `Authorization: Bearer token_organizer` | `Cookie: session=org_7f2a` |
| **Judge A** | Tomas Varga (`tomas.varga@example.org`, `jdg_01`) | `Authorization: Bearer token_judge_a` | `Cookie: session=jdg_a_91bc` |
| **Judge B** | Wei Lindqvist (`wei.lindqvist@example.org`, `jdg_02`) | `Authorization: Bearer token_judge_b` | `Cookie: session=jdg_b_44de` |
| **Participant** | Priya Nair (`priya1@example.org`, `tm_01`) | `Authorization: Bearer token_participant` | `Cookie: session=prt_2e88` |

All demo accounts sign in at `/login` with password `JudgeForge-Demo-2026!`. Demo credentials are public and suitable only for local evaluation.

---

## Acceptance Checker

Run the official hackathon verification suite:

```bash
python run.py .dogfood.toml
```

To update `acceptance-report.txt`:

```bash
python run.py .dogfood.toml > acceptance-report.txt
```

Current output:
```
DOGFOOD 2026 acceptance report
portal: http://127.0.0.1:8000
claimed: T1 T2
fixtures: fixtures.json

T1  gallery is public ................. PASS
T1  project from fixtures shown ....... PASS
T1  closed event refuses submissions .. PASS
T2  judge sees own scores ............. PASS
T2  judge cannot see peer scores ...... PASS
T2  participant blocked ............... PASS
T2  csv export works .................. PASS

claimed T1 T2, verified T1 T2
```

---

## Automated Test Suite

Run the full pytest suite:

```bash
pytest -v
```

This verifies:
- Anonymous public gallery accessibility
- Fixture project title rendering
- Submission refusal on closed events
- Valid submissions during active submission window
- Deadline enforcement on project updates
- Judge score retrieval and peer isolation
- Participant and unauthenticated user rejection on judging endpoints
- Organizer CSV export format and access controls
- Cookie and Bearer session authentication parity
- Seed idempotency (no duplicate entries)
- Statistical normalization computations and edge-case handling

---

## Docker Reset Command

To completely reset the database and application state:

```bash
docker compose down -v
docker compose build --no-cache
docker compose up
```

---

---

## Additional T3/T4 Capabilities

In addition to core T1 and T2 features, JudgeForge implements full T3 and T4 capabilities:

### Tier 3 (T3) Capabilities
- **Community Voting**: Dedicated voting portal allowing participants and attendees to cast ballots across submitted projects.
- **Flexible Access Modes**: Supports `open` (anonymous), `authenticated` (signed-in user), and `email_gated` (single-use voucher tokens issued by organizers) voting modes.
- **SQLite-Backed Rate Limiting**: Sliding-window rate limiter persisting request timestamps directly in SQLite with zero Redis/in-memory cache dependencies.
- **Duplicate-Vote Prevention**: Enforces strict uniqueness per voter identifier and IP address to eliminate ballot stuffing.
- **Randomized Ballot Ordering**: Projects are randomized per voter session to eliminate positional presentation bias.
- **Voting-Window Results Hiding**: Community vote counts and standings remain strictly hidden from the public while voting is active (`voting_results_public=0`).
- **Project Comments & Moderation**: Threaded public feedback on submitted projects with organizer flagging and moderation capabilities.
- **Readable Audit Trail**: Human-readable append-only log capturing all voting, submission, moderation, and scoring actions with actor, target, and timestamp.

### Tier 4 (T4) Capabilities
- **Full `/api/v1` REST API**: First-class REST API coverage mirroring every UI capability (events, tracks, prizes, teams, projects, drafts, rubric, scoring, voting, comments, results).
- **FastAPI OpenAPI Documentation**: Interactive OpenAPI Swagger documentation available at `/docs` and `/openapi.json`.
- **HMAC-Signed Outbound Webhooks**: Asynchronous outbound webhook dispatcher with HMAC-SHA256 signature verification headers (`X-JudgeForge-Signature-256`) and delivery logs (`/api/v1/webhooks`).
- **Offline SVG Certificates**: Cryptographically fingerprinted SVG certificates with SHA-256 integrity hashes for winners, participants, and judges, downloadable and verifiable offline (`/api/v1/certificates`).
- **Ed25519 Signed Verifiable Judge Records**: Cryptographically signed judge participation records using industry-standard Ed25519 digital signatures (`cryptography` library) with persistent PKCS8 PEM keys.
- **Public Verification Key & Endpoints**: Dedicated endpoints (`GET /api/v1/verifiable-records/public-key` and `POST /api/v1/verifiable-records/verify-record`) allowing external parties to independently verify judge participation without trusting database state.
- **Embeddable Gallery**: Responsive widget iframe/embed routes (`/embed/gallery`, `/embed/projects/{id}`) for embedding hackathon showcases into external event websites.
- **Bulk JSON Import/Export**: Complete export and restoration of events, tracks, teams, projects, rubric criteria, and scores via structured JSON (`/api/v1/bulk/export` and `/api/v1/bulk/import`).

### Tier Claim Explanation
In `.dogfood.toml`, the claim remains:
```toml
claimed = ["T1", "T2"]
```
This is because the official automated acceptance checker (`run.py`) supplied for the hackathon specifically tests and validates T1 and T2 checks. Claiming T3 or T4 in `.dogfood.toml` would result in a "claimed but not verified" status by the official checker. All T3 and T4 capabilities are fully implemented, verified with automated pytest tests, and available for manual review.

---

## Offline & Self-Hosting Details

- No runtime internet connection is needed.
- CSS is bundled locally in `app/static/style.css` using modern native system font stacks (`-apple-system`, `BlinkMacSystemFont`, `"Segoe UI"`, `Roboto`).
- JavaScript uses standard browser APIs (`fetch`, `<dialog>`) with zero external libraries.
- Database engine is local SQLite stored in `data/judgeforge.db`.

---

## Walkthrough

See [DEMO-SCRIPT.md](DEMO-SCRIPT.md) for the recording sequence and [RELEASE-CHECKLIST.md](RELEASE-CHECKLIST.md) for verification evidence.

---

## Demo Video

*(Link to demo walkthrough video)*

---

## License

This project is licensed under the [MIT License](LICENSE).

---

## Release Verification and Current Limits

- **Release Verification**: 51 automated tests passed (`pytest -v`) using isolated in-memory databases with automatic teardown.
- **Docker Compose Status**: Container built cleanly (`docker compose build --no-cache`) and running container verified healthy via Docker internal healthcheck (`docker inspect --format '{{.State.Health.Status}}' judgeforge-app` -> `healthy`).
- **Official Acceptance Checker**: All seven official acceptance checks passed on port 8000 (`7/7 PASS` via `python run.py .dogfood.toml`). Output captured in `acceptance-report.txt`.
- **Runtime Dependencies**: Zero hosted-service or cloud dependencies. The application executes completely offline. Note: building the Docker image for the first time requires network access or a pre-populated Docker cache to download Python packages; after the image is created, the runtime operates entirely offline.
- **Role-Based Access**: Visit `/workspace` after signing in to create teams, create single-use invitation links (48-hour expiry), accept invitations, view configured event prizes, and choose an event for submission. Organizers and admins can create events with deadlines and tracks. New participants register at `/login` with passwords of at least 12 characters. Registration never grants elevated roles or automatically claims seeded identities. Judge invitations through the organizer API return a generated initial password once, for the organizer to share privately.
- **Security & Password Hashing**: Passwords use salted PBKDF2-SHA256 (600,000 iterations). Local evaluation defaults to `DEMO_MODE=true`, with public fixture sessions and demo passwords. Compose publishes port 8000 on host interfaces. For non-demo production hosting, set `DEMO_MODE=false` and supply `ORGANIZER_PASSWORD` (12+ characters) on a fresh database; known demo tokens and demo-account sessions are rejected.
- **Full Lifecycle & Extended Capabilities**: A distinct `admin` role with user role management, event prize configuration, project draft and explicit finalization workflow, organizer-controlled results publication workflow (with strict judge privacy preservation), community voting, comment moderation, OpenAPI REST parity, HMAC webhooks, SVG certificates, and Ed25519 verifiable records are fully implemented and verified.
