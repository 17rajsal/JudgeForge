# JudgeForge

> **Self-hosted hackathon submissions and fair judging, with zero cloud dependencies.**

JudgeForge is a resilient, offline-first hackathon management and fair judging platform built for the **Dogfood 2026** competition. It provides an uncompromised developer and participant experience while enforcing rigorous judging isolation, cryptographic peer privacy, deadline validation, and statistical score normalization.

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
- **Defensible Score Normalization**: Combines weighted category scoring with Z-score standardization across judges to eliminate scoring bias between harsh and generous reviewers, featuring mathematical guarantees for zero-variance reviewers.
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
| **Organizer** | Head Organizer (`organizer@judgeforge.local`) | `Authorization: Bearer token_organizer` | `Cookie: session=org_7f2a` |
| **Judge A** | Tomas Varga (`tomas.varga@example.org`, `jdg_01`) | `Authorization: Bearer token_judge_a` | `Cookie: session=jdg_a_91bc` |
| **Judge B** | Wei Lindqvist (`wei.lindqvist@example.org`, `jdg_02`) | `Authorization: Bearer token_judge_b` | `Cookie: session=jdg_b_44de` |
| **Participant** | Priya Nair (`priya1@example.org`, `tm_01`) | `Authorization: Bearer token_participant` | `Cookie: session=prt_2e88` |

*(Fast 1-click login buttons for all 4 roles are also available on `/login`)*.

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

## Known Limitations

- **Single Event Focus**: The data model supports multiple events, but current routes default to the primary fixture event (`evt_01`).
- **T3 & T4 Out of Scope**: Community voting ballots, public comment threads, verifiable cryptographic certificates, and outbound webhooks are planned for future iterations to keep T1 and T2 completely stable and verified.

---

## Offline & Self-Hosting Details

- No runtime internet connection is needed.
- CSS is bundled locally in `app/static/style.css` using modern native system font stacks (`-apple-system`, `BlinkMacSystemFont`, `"Segoe UI"`, `Roboto`).
- JavaScript uses standard browser APIs (`fetch`, `<dialog>`) with zero external libraries.
- Database engine is local SQLite stored in `data/judgeforge.db`.

---

## Screenshots Section

*(Place screenshots of Public Gallery, Project Details, Judge Portal, and Organizer Command Center here)*

---

## Demo Video

*(Link to demo walkthrough video)*

---

## License

This project is licensed under the [MIT License](LICENSE).
