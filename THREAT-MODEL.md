# JudgeForge Threat Model & Security Posture

This document provides a realistic, defensible security evaluation of JudgeForge. It enumerates the primary attack vectors, evaluated impacts, current mitigations, and known remaining limitations across all operational tiers.

---

## 1. Threat Matrix

### 1. Peer Judge Score Disclosure
* **Threat**: A judge attempts to read or infer other judges' scores or comments before or during deliberation to adjust their own scores (anchoring or bandwagon effect).
* **Impact**: Compromises independent evaluation; destroys the statistical assumption of uncorrelated reviewer error required for Z-score normalization.
* **Current Mitigation**: Strict backend query filtering in `app/routers/judging.py` (`list_judge_scores`). When `current_user.role == 'judge'`, the server forcibly overwrites the query filter to `judge_id == current_user.judge_profile.id`. Any explicit request for peer judge IDs triggers `HTTP 403 Forbidden`. Public APIs (`/api/results`) return only aggregated normalized/raw totals and omit reviewer IDs, individual ballots, and private comments.
* **Remaining Limitation**: If an organizer screen-shares during live deliberation, or if multiple judges share physical terminals, peer scores may be visually observed. Database-level read access by infrastructure admins bypasses application RBAC.

### 2. Unauthorized Judge Access
* **Threat**: An unauthenticated caller or attacker attempts to access the evaluation dashboard or submit ratings.
* **Impact**: Tampering with competition standings and prize allocations.
* **Current Mitigation**: All judge routes (`/judge`, `POST /api/judge/scores`, `GET /api/judge/scores`) require authenticated sessions (`require_judge` / `get_current_user`) verifying that the session cookie maps to a valid user account with the `judge`, `organizer`, or `admin` role.
* **Remaining Limitation**: Session tokens rely on standard cookie storage. If session tokens are intercepted over non-TLS connections (e.g., local Wi-Fi without HTTPS in local self-hosted setups), session hijacking is possible. HTTPS termination should be handled by a reverse proxy (e.g. Caddy, Nginx).

### 3. Participant Acting as Judge (Role Escalation)
* **Threat**: A participant modifies their client-side session or API payload to invoke `/api/judge/scores` or score their own project.
* **Impact**: Self-awarding maximum scores or sabotaging competitors.
* **Current Mitigation**: Backend RBAC enforcement in `submit_or_update_score` rejects any non-judge caller with `HTTP 403 Forbidden`. Judge profiles are decoupled from participant profiles; `judge_id` is resolved strictly from the authenticated database session, never accepted from user-supplied JSON payload fields.
* **Remaining Limitation**: If an administrator incorrectly promotes a participant user account to the `judge` role via the RBAC management interface, that user gains judge privileges.

### 4. Cross-Track / Out-of-Scope Judge Access
* **Threat**: A judge assigned to Track A attempts to score projects in Track B where they may have personal interest or lack domain competence.
* **Impact**: Unqualified evaluations and track-level bias.
* **Current Mitigation**: `submit_or_update_score` checks whether `judge_profile.tracks` is configured. If track assignments exist, the project's `track_id` must match one of the assigned tracks, or the server returns `HTTP 403 Forbidden`. The judge dashboard table also filters available submissions strictly to assigned tracks.
* **Remaining Limitation**: If an organizer does not assign specific tracks to a judge (`tracks` is empty), the judge is treated as a generalist and can evaluate all tracks by default.

### 5. Deadline Gaming (Late Submissions)
* **Threat**: Participants attempt to bypass the submission deadline using direct API calls (`POST /api/projects` or `POST /api/v1/projects`), manipulated client clocks, or parameter tampering.
* **Impact**: Unfair advantage over teams who complied with the deadline.
* **Current Mitigation**: Server-authoritative UTC timestamp verification in `app/routers/projects.py` and `app/routers/api_v1.py`. When an event has a configured `submissions_close`, the server evaluates `datetime.now(timezone.utc) > event.submissions_close`. Any attempt to create or finalize a project returns `HTTP 400 Bad Request` regardless of client payload. Attempted deadline violations are recorded in `audit_logs`.
* **Remaining Limitation**: Submissions finalized seconds before the deadline with long upload times could be rejected if the request payload finishes processing after the timestamp. Organizers have administrative bypass permissions.

### 6. Duplicate Submissions / Accidental Resubmission
* **Threat**: A team submits multiple conflicting entries or accidentally submits a project twice, leading to race conditions in scoring.
* **Impact**: Fragmented judging scores and database bloat.
* **Current Mitigation**: Frontend disables submission controls once clicked. Backend checks ensure a team can only submit projects within permitted event tracks. Projects have a single canonical identifier.
* **Remaining Limitation**: Multiple browser tabs opened by different team members submitting simultaneously before the deadline could create two separate project records if they belong to different teams.

### 7. Team Invite Abuse & Unauthorized Team Hijacking
* **Threat**: An attacker guesses or enumerates team invite URLs, or a non-member adds themselves to a winning team to claim prizes.
* **Impact**: Unauthorized credit attribution and prize theft.
* **Current Mitigation**: Team invites utilize high-entropy cryptographic tokens (`secrets.token_urlsafe(32)`). Acceptance requires authentication and validates token validity and team capacity.
* **Remaining Limitation**: If a team member accidentally posts their invite link in a public Discord or Slack channel, any authenticated participant who clicks it can join until the team capacity is exhausted.

### 8. Community Vote Stuffing & Sybil Attacks
* **Threat**: Automated bots or coordinated groups cast thousands of community votes for a project to win People's Choice awards.
* **Impact**: Distorts community choice awards and overwhelms database IO.
* **Current Mitigation**:
  - Three distinct voting access modes: `authenticated` (strictly 1 vote per authenticated user ID), `email_gated` (one-time HMAC vouchers / allowlist), and `open` (IP + device fingerprint rate-limiting).
  - Rate limiting enforced via SQLite `rate_limits` table: maximum 5 vote attempts per IP per minute.
  - Active voting window hides intermediate vote tallies to prevent tactical bandwagon voting.
* **Remaining Limitation**: In `open` mode, attackers with residential proxy networks or rotating mobile IP addresses can bypass IP-based rate limiting. For high-stakes community awards, organizers must use `authenticated` or `email_gated` mode.

### 9. Duplicate Voting in Open Mode
* **Threat**: A single user clears browser cookies or uses private browsing to cast multiple votes in `open` voting mode.
* **Impact**: Distorts vote tallies without botnets.
* **Current Mitigation**: Multi-factor fingerprinting combining IP hash, user agent hash, and device tokens stored in `community_votes` table.
* **Remaining Limitation**: Deterministic browser fingerprinting without third-party proprietary tracking scripts is imperfect; determined non-technical users can use different browsers on the same machine or cellular data toggles.

### 10. X-Forwarded-For Spoofing Behind Proxies
* **Threat**: Attackers inject fake client IP headers (`X-Forwarded-For: 1.2.3.4`) to bypass IP rate limits or cast multiple community votes.
* **Impact**: Complete bypass of IP-based anti-abuse controls.
* **Current Mitigation**: IP resolution extracts the rightmost untrusted IP address or falls back to direct client socket host `request.client.host` unless an explicitly configured trusted reverse proxy is present.
* **Remaining Limitation**: In local self-hosted configurations where JudgeForge is exposed directly without a reverse proxy, arbitrary header injection is prevented by reading direct socket address.

### 11. Rate Limiting Evasion & Resource Exhaustion (DoS)
* **Threat**: Rapid requests to `/api/judge/scores`, `/api/upload`, or `/api/projects` cause denial of service.
* **Impact**: Server unavailability during submission deadlines.
* **Current Mitigation**: Persistent SQLite-backed rate limiting with sliding time windows. File upload endpoint limits size to 25MB.
* **Remaining Limitation**: SQLite write locking under extreme concurrent load (hundreds of requests/second) can cause database lock contention. For large hackathons (>1,000 participants), running behind Nginx/Caddy with connection and request rate limits is recommended.

### 12. Judge Collusion & Strategic Scoring (Hawk/Dove)
* **Threat**: A judge deliberately gives high scores to favored projects and low scores to all others, or a naturally strict judge unfairly lowers project scores.
* **Impact**: Skewed raw averages and unfair winners.
* **Current Mitigation**:
  - Z-score normalization: Converts raw scores into relative distance from that specific judge's mean ($\mu_j$) scaled by standard deviation ($\sigma_j$). A strict judge's 4.0 becomes a high positive Z-score; a lenient judge's 4.0 becomes a low or negative Z-score.
  - Zero-variance defense: If a colluding judge gives all other projects identical minimum scores ($\sigma_j \le 10^{-6}$), the Z-score is neutralized to $0.0$, mapping to the competition average and neutralizing extreme skew.
* **Remaining Limitation**: Normalization requires each judge to review at least 3–4 projects to establish a meaningful statistical distribution. If a judge reviews only 1 project, their standard deviation is undefined and defaults to global parameters.

### 13. Malicious File Uploads (RCE & Path Traversal)
* **Threat**: An attacker uploads an executable script (`.php`, `.exe`, `.sh`, `.html` with XSS) or uses path traversal (`../../etc/passwd`) via the pitch deck upload endpoint.
* **Impact**: Remote code execution, arbitrary file overwrite, or stored XSS.
* **Current Mitigation**:
  - Whitelist validation: Strictly allowed extensions: `.pdf`, `.ppt`, `.pptx`, `.png`, `.jpg`, `.jpeg`, `.webp`. All other extensions return `HTTP 400 Bad Request`.
  - Filename sanitization: `os.path.basename` strips directory traversal sequences. Server generates a random UUID prefix (`uuid4().hex[:12] + "_" + clean_filename`).
  - Safe storage path: Stored strictly under `DATA_DIR / uploads`. Asset serving verifies that resolved absolute paths remain strictly inside the upload directory.
  - File size cap: Enforced at 25 MB max.
* **Remaining Limitation**: The server does not execute antivirus or ClamAV daemon scans on uploaded PDF/PPTX files. Deep binary inspection of embedded macros in `.pptx` files is not performed.

### 14. Webhook Abuse & SSRF
* **Threat**: An organizer configures a webhook URL targeting internal cloud metadata services (`http://169.254.169.254/latest/meta-data/`) or internal network services.
* **Impact**: Server-Side Request Forgery (SSRF) and credential leakage.
* **Current Mitigation**: Webhook payloads are signed using HMAC-SHA256 with an organizer-defined secret, allowing receivers to verify authenticity. Delivery logs record status codes and response snippets.
* **Remaining Limitation**: The webhook dispatcher does not currently block private RFC-1918 or link-local IP ranges. In enterprise deployments, egress firewall rules should restrict outbound webhook traffic to approved destination ports/hosts.

### 15. Audit Trail Immutability & Limitations
* **Threat**: An administrative attacker alters past audit logs or judge scoring records to conceal tampering.
* **Impact**: Undetected administrative malfeasance.
* **Current Mitigation**:
  - In addition to standard SQLite `audit_logs`, JudgeForge maintains an append-only cryptographic hash chain (`verifiable_judge_records`).
  - Every evaluation is hashed with the previous record's hash (`SHA-256`) and digitally signed using an Ed25519 asymmetric private key (`DATA_DIR/ed25519_private_key.pem`).
  - Public verification key is exposed at `/api/v1/verifiable-records/public-key`, enabling independent offline audit of the hash chain.
* **Remaining Limitation**: An attacker with root OS access or write access to the host SQLite database file could theoretically fork the ledger from an earlier block and regenerate signatures if they also possess the private key on disk. Complete immutability requires mirroring the public key or signed root hashes to an external immutable ledger or witness service.

---

## 2. Summary of Invariant Security Guarantees

1. **Confidential Peer Isolation**: At no point can a judge query or receive another judge's criteria ratings or qualitative feedback via the application.
2. **Pre-Publication Secrecy**: Unauthenticated visitors and participants cannot access leaderboard standings or prize results prior to explicit organizer publication.
3. **Deterministic Normalization**: Competition standings are derived algorithmically without hidden bias or arbitrary rank adjustments.
4. **Offline Resilience**: Cryptographic verification, session management, and rate limiting run 100% locally with zero external network dependencies.
