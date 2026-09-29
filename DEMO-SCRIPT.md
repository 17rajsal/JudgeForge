# JudgeForge Five-Minute Demo Plan & Recording Script

> **Target runtime**: ~5 minutes.
> **Format**: Live screen recording with clear voiceover. No slides or simulated output. Keep `evt_01` (Sample Hack 2026) closed and perform live lifecycle on an open future event.

---

## Demo Timing & Sequence

### 00:00 – 00:20 | Introduction & Self-Hosting
* **Visual**: Terminal showing `docker compose ps` and `docker inspect --format '{{.State.Health.Status}}' judgeforge-app`.
* **Narration**:
  > "Welcome to JudgeForge — a self-hosted, offline-capable hackathon operating system focused on fair, auditable judging. It requires zero cloud runtime dependencies, zero CDNs, and launches in seconds via a single command with persistent SQLite storage and local static assets."

### 00:20 – 00:50 | Organizer Setup: Event, Tracks & Rubric
* **Visual**: Browser at `http://localhost:8000/workspace`. Switch to Organizer (`org_7f2a`).
* **Action**:
  - Show event creation modal: Title, future deadline, tracks (`AI Agents`, `Edge Systems`).
  - Open `/organizer`: show Rubric Criteria & Weights configuration (Functionality, Quality, Innovation) and dynamic total weight badge.
* **Narration**:
  > "Organizers can configure competition deadlines, tracks, category prizes, and customize rubric weights that dynamically feed into the normalization engine."

### 00:50 – 01:15 | Participant Workspace: Team Formation & Invites
* **Visual**: Switch to Participant (`prt_2e88`).
* **Action**:
  - Create team for the open event.
  - Generate high-entropy invite URL.
  - Demonstrate automatic team membership derivation without manual participant re-typing.
* **Narration**:
  > "Participants manage teams in their workspace. Team invite links are protected with cryptographic tokens, and team rosters are automatically derived across all project records."

### 01:15 – 02:00 | Rich Project Submission & Draft Workflow
* **Visual**: Participant submission form at `/submit?event_id=...`.
* **Action**:
  - Show automatic team indicator and member chips.
  - Enter project metadata: Title, Tagline, Track, Architecture Summary, Long Description, Tech Stack tags, GitHub URL, Live Demo URL, Video URL.
  - Upload local pitch deck (`.pdf` / `.pptx`) — highlight safe persistent storage under `data/uploads`.
  - Click **Save Draft** (not yet public).
  - Click **Submit Final Entry** with confirmation modal locking the entry for judging.
* **Narration**:
  > "JudgeForge features a rich submission pipeline: taglines, Markdown architecture descriptions, tech tags, direct repository and demo links, and safe local presentation deck uploads with traversal protection. Teams can iterate on private drafts and finalize with confirmation before the deadline."

### 02:00 – 02:20 | Project Detail & Public Gallery
* **Visual**: `/projects/{id}` and public gallery at `/projects`.
* **Action**:
  - Showcase prominent action toolbar: **View GitHub Repository**, **Open Live Demo**, **Watch Demo Video**, **View / Download Pitch Deck**, and **Verification Certificate**.
* **Narration**:
  > "Submitted entries appear in the gallery. The detail view provides evaluators and visitors immediate one-click access to all deliverables."

### 02:20 – 03:05 | Judge Console: Calibrated Rubric & Peer Isolation
* **Visual**: Switch to Judge A (`jdg_a_91bc`) and open `/judge`.
* **Action**:
  - Point out assigned submission count, completion bar, and track scope.
  - Click **Score Now** on the new project.
  - Highlight project asset links right inside the evaluation drawer.
  - Demonstrate explicit 1–5 scoring anchors:
    - *Functionality*: Broken $\rightarrow$ Working MVP $\rightarrow$ Complete & polished.
    - *Code & Design Quality*: Fragile $\rightarrow$ Clean $\rightarrow$ Production-grade.
    - *Innovation & Creativity*: Clone $\rightarrow$ Creative $\rightarrow$ Breakthrough.
  - Adjust sliders: watch live **Weighted Raw Score Preview** update dynamically (`sum(score × weight) / sum(weights)`).
  - Show confidentiality banner: *"Your evaluation is private. Peer judge scores and comments are isolated by the backend."*
  - Switch briefly to Judge B (`jdg_b_44de`) to prove that Judge A's scores and notes are completely invisible.
  - Click **Save & Next** to advance smoothly to the next assigned submission.
* **Narration**:
  > "The judge console is designed for focus. Evaluators inspect materials directly in the drawer, score across calibrated 1-to-5 rubric anchors with live weighted calculation previews, and advance seamlessly with Save & Next. Strict server-side RBAC ensures peer judges cannot view or infer each other's ratings."

### 03:05 – 03:50 | Organizer Command Center & Normalization Lab
* **Visual**: Switch back to Organizer (`org_7f2a`) at `/organizer`.
* **Action**:
  - Show **Judging Progress & Deliberation Quota**: Reviews completed vs. expected, unreviewed project warnings, below-target warnings, and judge activity breakdown.
  - Scroll to the **Normalization Lab & Fairness Analysis**:
    - Walk through the 6-step calibration: Multi-criteria composite $\rightarrow$ Judge bias modeling $\rightarrow$ Z-score standardization $\rightarrow$ Rescaling to 1.0–5.0 scale $\rightarrow$ Independent aggregation $\rightarrow$ Zero-variance defense.
    - Highlight the comparison table: Normalized Rank, Raw Rank, and **Rank Movement** indicators ($\uparrow$ green, $\downarrow$ amber, $=$ neutral).
* **Narration**:
  > "In the Command Center, organizers monitor real-time deliberation coverage with clear warnings for unreviewed projects. Below, the Normalization Lab explains the statistical engine: each reviewer's leniency or harshness is standardized via Z-scores and mapped back to the 1-to-5 rubric scale, displaying transparent rank movements caused by strict or lenient grading."

### 03:50 – 04:15 | Publication Experience & Public Results
* **Visual**: Click **Publish Official Results**.
* **Action**:
  - Show publication confirmation modal summarizing review progress and reassuring that individual judge ballots remain strictly private.
  - Confirm publication.
  - Log out and open `/results`.
  - View podium winners, asset links, normalized scores (`X.XX / 5.00`), raw averages, and the 4-phase Fair Scoring Methodology card.
  - Click **Export Official CSV** (`GET /api/export.csv`) and show RFC-compliant CSV with normalized standings.
* **Narration**:
  > "Before publishing, a safety modal summarizes coverage and verifies confidentiality. Once published, the public results page showcases podium winners and project links, alongside our fair scoring methodology. Individual judge notes and ballots remain permanently private."

### 04:15 – 04:35 | Additional Capabilities Overview (T3/T4)
* **Visual**: Rapid tour across:
  - `/docs` — Interactive FastAPI Swagger API documentation.
  - `/audit` — Organizer audit trail filtering system actions.
  - `/api/v1/verifiable-records/public-key` — Ed25519 public key and verifiable judge record hash chain.
  - `/events/{id}/vote` — Community voting with rate-limiting and anti-leak results hiding.
* **Narration**:
  > "JudgeForge also includes extensive production capabilities: full REST API coverage at /docs, cryptographic Ed25519 judge verification records, offline SVG certificates, embeddable gallery widgets, and community choice voting."

### 04:35 – 04:50 | Verification & Acceptance Check
* **Visual**: Terminal execution:
  ```bash
  python run.py .dogfood.toml
  ```
* **Narration**:
  > "Let's run the official hackathon acceptance checker. All seven checks pass cleanly — 7 out of 7 PASS on port 8000."
* **Visual**: Run full test suite:
  ```bash
  pytest -q
  ```
  Highlight all tests passing green.

### 04:50 – 05:00 | Repository & Conclusion
* **Visual**: GitHub repository at https://github.com/17rajsal/JudgeForge and `README.md`.
* **Narration**:
  > "JudgeForge combines operational power, mathematical fairness, and rock-solid offline reliability. Thank you."
