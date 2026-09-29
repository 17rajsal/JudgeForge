# JudgeForge

> **Self-hosted hackathon submission and fair-judging platform.**

JudgeForge is a resilient, offline-capable hackathon management and fair evaluation platform built for the **Dogfood 2026** competition. It turns hackathon operations into an auditable, statistically defensible workflow: from event creation, team formation, and rich project submissions to peer-isolated judge evaluations, cross-judge Z-score normalization, organizer review coverage monitoring, and transparent public results.

---

## Claimed Tier

**Claimed:** `T1` and `T2`  
**Verified:** `T1` and `T2` (100% acceptance checks passing in `run.py`)

- **T1 Core**: Participant registration, team management, deadline-enforced project submissions, project editing rules, public gallery with track filtering and search.
- **T2 Judging Engine**: Judge track assignments, isolated scoring interfaces (judges cannot view peer scores), weighted rubric configuration, zero-variance Z-score normalization, organizer progress dashboards, and RFC-compliant CSV export.
- **Verification Evidence**: Official acceptance checker 7/7 PASS (`python run.py .dogfood.toml`), full test suite 80/80 PASS (`pytest -q`), and Docker container healthcheck passing (`healthy`).

---

## Key Differentiators

1. **Zero Cloud Runtime Dependencies**: Runtime has no hosted or cloud service dependency. Runs completely offline using a local SQLite database, vanilla JavaScript, and local CSS stylesheets. Zero CDN dependencies, zero external font calls, zero analytics trackers. (Note: A fresh Docker image build from scratch may require standard package downloads if wheels are not cached locally).
2. **Strict Backend-Enforced Role Isolation**: Enforces permissions on the server for `organizer`, `judge`, and `participant`. UI controls reflect permissions, but backend authorization guards prevent bypass.
3. **Strict Judge Peer Isolation**: Judges can only view and update their own scores. Any attempt by a judge to inspect peer reviews (e.g. via direct query parameters or API calls) is rejected with HTTP `403 Forbidden`.
4. **Calibrated Rubric Scoring UX**: 1–5 scoring scale with explicit scoring anchors for Functionality, Quality, and Innovation, live weighted raw-score calculation preview, and "Save & Next" review workflows.
5. **Defensible Statistical Normalization**: Per-judge Z-score standardization ($z = \frac{x - \mu_j}{\sigma_j}$) rescaled to the 1.0–5.0 competition domain ($\mu_{global} + z \cdot \sigma_{global}$), removing individual judge strictness or leniency while handling zero-variance reviews ($\sigma_j = 0$) without division by zero.
6. **Organizer Judging Command Center**: Real-time evaluation progress, deliberation quota tracking, unreviewed project warnings, below-target review warnings, and interactive **Normalization Lab & Fairness Analysis** with rank movement indicators.
7. **Rich Submission Pipeline**: Tagline, architecture overview, Markdown long description, tech stack tags, repository URL, live demo URL, demo video URL, and safe local presentation deck uploads (`.pdf`, `.ppt`, `.pptx` stored locally under `data/uploads`).
8. **Auditable Integrity**: Immutable append-only audit trail and optional Ed25519 digitally signed judge participation records (via `cryptography`) with offline public verification.

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

# Run server (automatically initializes database, schema migrations & seeds fixtures)
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

---

## Access & Demo Credentials

Open your browser to: **`http://127.0.0.1:8000`** (or `http://localhost:8000`)

### Demo Credentials

| Role | Account / Email | Authorization Header | Session Cookie |
|---|---|---|---|
| **Admin** | `admin@judgeforge.local` | `Authorization: Bearer token_admin` | `Cookie: session=adm_9a11` |
| **Organizer** | `organizer@judgeforge.local` | `Authorization: Bearer token_organizer` | `Cookie: session=org_7f2a` |
| **Judge A** | Tomas Varga (`tomas.varga@example.org`, `jdg_01`) | `Authorization: Bearer token_judge_a` | `Cookie: session=jdg_a_91bc` |
| **Judge B** | Wei Lindqvist (`wei.lindqvist@example.org`, `jdg_02`) | `Authorization: Bearer token_judge_b` | `Cookie: session=jdg_b_44de` |
| **Participant** | Priya Nair (`priya1@example.org`, `tm_01`) | `Authorization: Bearer token_participant` | `Cookie: session=prt_2e88` |

All demo accounts sign in at `/login` with preset password `JudgeForge-Demo-2026!`. Demo credentials are for local evaluation. In non-demo production hosting, set `DEMO_MODE=false`.

---

## Role Architecture & Access Isolation

* **ORGANIZER / ADMIN**:
  - Create and configure events, deadlines, tracks, prizes, and rubric criteria weights.
  - Create judge accounts, assign tracks, and monitor deliberation progress in the Command Center.
  - Inspect the audit trail, preview leaderboard rankings before release, and publish/unpublish public results.
  - Export official CSV results and bulk JSON backups.
* **JUDGE**:
  - View only assigned submissions within designated tracks on `/judge`.
  - Evaluate projects using calibrated 1–5 rubric anchors with live weighted preview and quick asset links.
  - Submit and revise own scores; peer scores and comments are strictly isolated by backend authorization.
  - Cannot access unpublished standings or administrative controls.
* **PARTICIPANT**:
  - Register accounts, create teams, generate cryptographic team invite links, and accept invitations.
  - Compose project drafts, update metadata before deadline, and finalize submissions.
  - Review submitted project status and public gallery entries.
* **VISITOR**:
  - Browse public project gallery with track filtering and search.
  - Access published leaderboard rankings and scoring methodology (hidden prior to publication).

---

## End-to-End Hackathon Lifecycle

1. **Event Creation**: Organizer creates event with submission deadline, tracks, prizes, and custom rubric weights (`/workspace` or `POST /api/events`).
2. **Team Formation**: Participant creates team (`/workspace`), copies high-entropy invite URL, and teammates accept while authenticated.
3. **Rich Project Submission**: Team fills project title, tagline, architecture summary, long description, tech stack tags, GitHub URL, live demo, video, and uploads pitch deck (`/submit`). Can save as private draft or finalize before deadline.
4. **Project Gallery**: Finalized projects appear in the public showcase (`/projects`).
5. **Judge Evaluation**: Assigned judges log in to `/judge`, inspect project links directly in the drawer, rate Functionality, Quality, and Innovation with calibrated anchors, preview weighted score, and click **Save & Next**.
6. **Deliberation Monitoring**: Organizer inspects **Judging Progress & Deliberation Quota** (`/organizer`), viewing completion %, unreviewed project warnings, and inactive judge lists.
7. **Fairness Analysis**: Organizer reviews the **Normalization Lab**, examining raw vs. normalized ranks and shift adjustments.
8. **Publication Flow**: Organizer confirms release via summary dialog; results transition from private draft to public.
9. **Public Podium**: Visitors access `/results`, viewing winners, asset links, and the 4-phase scoring methodology card.

---

## API & Integration Guide

JudgeForge includes a complete FastAPI REST API:

- **Interactive Swagger Documentation**: `http://localhost:8000/docs`
- **ReDoc Documentation**: `http://localhost:8000/redoc`
- **OpenAPI JSON Specification**: `http://localhost:8000/openapi.json`

### Key Endpoints

| Area | Method & Endpoint | Description |
|---|---|---|
| **Public Gallery** | `GET /api/projects` | List submitted projects with track filter and search |
| **Project Detail** | `GET /api/projects/{id}` | Retrieve project metadata and asset links |
| **File Upload** | `POST /api/upload` | Upload pitch deck / assets (PDF, PPT, PPTX up to 25MB) |
| **Judging Scores** | `GET /api/judge/scores` | List judge's own evaluations (peer-isolated) |
| **Submit Score** | `POST /api/judge/scores` | Save or update 1–5 rubric ratings |
| **CSV Export** | `GET /api/export.csv?event_id=...` | RFC-compliant export of normalized rankings |
| **Public Results** | `GET /api/results?event_id=...` | Published leaderboard (403 prior to publication) |
| **Bulk Backup** | `GET /api/v1/bulk/export?event_id=...` | Full JSON database export |
| **Bulk Restore** | `POST /api/v1/bulk/import` | Import JSON backup into database |
| **Public Key** | `GET /api/v1/verifiable-records/public-key`| Ed25519 public verification key (hex) |

---

## Data Storage, File Uploads & Backup Guide

### Storage Locations

All application state is stored locally within `DATA_DIR` (default `./data`, mounted as a Docker named volume `judgeforge_data`):

* **Database File**: `data/judgeforge.db` (SQLite with automatic idempotent migrations)
* **Uploaded Presentation Decks**: `data/uploads/` (safe local storage with basename sanitization and traversal protection)
* **Cryptographic Signing Key**: `data/ed25519_private_key.pem` (Ed25519 private key generated once on first boot)

### Backup & Disaster Recovery

To take a complete snapshot of JudgeForge:
```bash
# 1. Back up database and uploads directly
tar -czf judgeforge_backup_$(date +%F).tar.gz data/

# 2. Or export structured JSON via organizer API:
curl -H "Authorization: Bearer token_organizer" http://localhost:8000/api/v1/bulk/export > backup.json
```

To restore:
```bash
tar -xzf judgeforge_backup_YYYY-MM-DD.tar.gz -C ./
```

---

## Acceptance Checker

Run the official hackathon verification suite:

```bash
python run.py .dogfood.toml
```

To generate `acceptance-report.txt`:

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
pytest -q
```

Coverage includes:
- Anonymous public gallery accessibility and search
- Seeded project fixture rendering and backward compatibility
- Submission refusal on closed events (`evt_01`)
- Rich project submission with optional metadata and file uploads
- Automatic team member derivation from membership
- Deadline enforcement on project creation and updates
- Judge score retrieval, track authorization, and strict peer isolation
- Participant scoring rejection (RBAC enforcement)
- Calibrated rubric weighting mathematical verification
- Event-scoped normalized leaderboard calculations
- Pre-publication confidentiality and post-publication transparency
- Judge comment privacy preservation
- Unsafe file upload extension rejection (`.exe`, `.sh`, `.php`)
- Safe presentation deck persistence and serving
- Zero-variance reviewer defense and scale re-mapping
- Full demo lifecycle end-to-end integration

---

## Additional T3/T4 Capabilities

In addition to core T1 and T2 features, JudgeForge implements comprehensive Tier 3 and Tier 4 capabilities (manual-review extras, NOT officially checker-verified):

### Tier 3 (T3) Capabilities
- **Community Voting**: Dedicated voting portal allowing participants and attendees to cast ballots across submitted projects.
- **Flexible Access Modes**: Supports `open` (anonymous rate-limited), `authenticated` (signed-in user), and `email_gated` (single-use voucher tokens issued by organizers) voting modes.
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
- **Ed25519 Signed Verifiable Judge Records**: Cryptographically signed judge participation records using standard Ed25519 digital signatures (`cryptography` library) with persistent PKCS8 PEM keys.
- **Public Verification Key & Endpoints**: Dedicated endpoints (`GET /api/v1/verifiable-records/public-key` and `POST /api/v1/verifiable-records/verify-record`) allowing external parties to independently verify judge participation without trusting database state.
- **Embeddable Gallery**: Responsive widget iframe/embed routes (`/embed/gallery`, `/embed/projects/{id}`) for embedding hackathon showcases into external event websites.
- **Bulk JSON Import/Export**: Complete export and restoration of events, tracks, teams, projects, rubric criteria, and scores via structured JSON (`/api/v1/bulk/export` and `/api/v1/bulk/import`).

### Tier Claim Explanation
In `.dogfood.toml`, the claim remains:
```toml
claimed = ["T1", "T2"]
```
This is because the official automated acceptance checker (`run.py`) supplied for the hackathon specifically tests and validates T1 and T2 checks. Claiming T3 or T4 in `.dogfood.toml` would result in a "claimed but not verified" status by the official checker. All T3 and T4 capabilities are fully implemented, verified with automated tests, and available for manual evaluation.

---

## Known Limitations & Production Recommendations

1. **Single-Node SQLite Architecture**: JudgeForge is engineered for single-server or single-container operation using local SQLite with write-ahead logging (WAL). It comfortably handles hackathons up to several thousand participants. For massive multi-region write concurrency, deploying behind an HTTP reverse proxy (Nginx, Caddy) with request rate limiting is recommended.
2. **TLS / HTTPS Termination**: The application runs an embedded Uvicorn HTTP server. Production deployments should place JudgeForge behind a TLS-terminating reverse proxy with valid certificates.
3. **Statistical Sample Size for Normalization**: Z-score standardization relies on establishing a personal distribution for each judge ($\mu_j$, $\sigma_j$). For best statistical properties, organizers should assign each judge at least 3–4 projects. Single-review assignments default to neutral global standard deviations.
4. **File Macro Inspection**: Local pitch deck uploads validate extensions, file size (25MB), and paths. Uploaded `.pptx` presentations are not scanned for macro binaries; organizers hosting high-security events may pair JudgeForge with external file scanners.

---

## Documentation Links

* [ARCHITECTURE.md](ARCHITECTURE.md) — System architecture, database design, and request flow
* [DATA-MODEL.md](DATA-MODEL.md) — Entity relationships, database schemas, and migration strategy
* [JUDGING.md](JUDGING.md) — Mathematical formulation of rubric scoring and Z-score normalization
* [THREAT-MODEL.md](THREAT-MODEL.md) — Comprehensive security analysis across 15 operational vectors
* [DEMO-SCRIPT.md](DEMO-SCRIPT.md) — 5-minute hackathon walkthrough and recording sequence
* [RELEASE-CHECKLIST.md](RELEASE-CHECKLIST.md) — Verification records and pre-release audit checklist

---

## License

This project is licensed under the [MIT License](LICENSE).
