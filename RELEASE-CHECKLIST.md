# Release Evidence & Submission Checklist

### Verification Evidence
- [x] **Automated Test Suite**: **75/75 tests passed** in pytest (`pytest -q`).
- [x] **Official Acceptance Checker**: **7/7 checks passed** (`python run.py .dogfood.toml` -> claimed T1 T2, verified T1 T2). Real checker output captured in `acceptance-report.txt`.
- [x] **Docker Container Health**: Image built cleanly without cache (`docker compose build --no-cache`) and running container verified healthy via Docker internal healthcheck (`docker inspect --format '{{.State.Health.Status}}' judgeforge-app` -> `healthy`).
- [x] **HTTP Liveness**: `GET /health` returns `{"status": "ok"}` on port 8000.
- [x] **Offline Restart & Persistence**: Verified data persistence across container restarts with persistent volume `judgeforge_data`.
- [x] **Branch Security Review Passed**:
  - Project team authorization enforced (participant blocked with 403 when submitting for a team they do not belong to).
  - Cross-event team/track validation enforced (400 Bad Request on event mismatch).
  - Team privacy & draft protection enforced (team listing excludes emails/user IDs; unsubmitted drafts hidden from non-members and anonymous callers).
  - Maintained Ed25519 digital signature scheme with persistent PKCS8 PEM private key storage.
  - Verifiable records privacy verified (public records prove judge participation without exposing numeric criteria scores or private comments).
- [x] **Official Evaluation Files Untouched**: `spec.md`, `run.py`, `fixtures.json`, and `.dogfood.toml` tier claim (`claimed = ["T1", "T2"]`) remain strictly unmodified.
- [x] **Complete T3 & T4 Implementation**: Community voting (open/authenticated/email-gated), SQLite rate limiting, comment moderation, OpenAPI REST parity at `/docs`, HMAC-SHA256 webhooks, offline SVG certificates, Ed25519 signed judge records, embeddable gallery, and bulk JSON import/export are implemented and documented.
- [x] **Documentation & Licensing**:
  - `README.md` updated with T3/T4 capabilities, honest tier claim explanation, and clean offline setup instructions.
  - `ARCHITECTURE.md` updated with end-to-end architecture flow, T3/T4 modules, and security boundaries.
  - `DATA-MODEL.md` updated with all T3/T4 tables, schemas matching `models.py`, and portability architecture.
  - `JUDGING.md` updated with Ed25519 signed records, hash chain, privacy guarantees, and judging vs voting distinction.
  - `DEMO-SCRIPT.md` updated to a concise 5-minute recording sequence.
  - MIT License present in `LICENSE`.
- [x] **Public Git Repository**: Synced to GitHub at https://github.com/17rajsal/JudgeForge.

---

### Remaining Submission Work
- [ ] Record the 5-minute walkthrough video following the sequence in `DEMO-SCRIPT.md`.
- [ ] Upload the video to a publicly accessible URL (e.g. YouTube unlisted or Loom).
- [ ] Add the real video URL to `README.md` and push to `master`.

---

### Final Operator Verification Commands

Run in the project directory:

```powershell
$env:Path = "C:\Users\rajsa\AppData\Local\Programs\DockerDesktop\resources\bin;" + $env:Path
docker inspect --format '{{.State.Health.Status}}' judgeforge-app
python run.py .dogfood.toml
pytest -v
git status
```
