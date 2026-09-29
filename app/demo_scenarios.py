import datetime
import json
from sqlalchemy.orm import Session
from app.models import (
    Event,
    Track,
    Judge,
    Team,
    TeamMember,
    Project,
    RubricCriterion,
    Score,
    Prize,
    User,
    SessionToken,
    PasswordCredential,
)
from app.passwords import hash_password
from app.services.scoring import DEFAULT_FIVE_CRITERIA

BF_SCORES_DATA = [
    # prj_bf_01 (NovaStack): 1st place
    ("sc_bf_01_1", "jdg_01", "prj_bf_01", 4, 4, 4, 5, 4, "Exceptional system design. Firecracker integration is well-engineered."),
    ("sc_bf_01_2", "jdg_02", "prj_bf_01", 5, 5, 5, 5, 5, "Stunning project! Production-ready architecture and instant boot times."),
    ("sc_bf_01_3", "jdg_03", "prj_bf_01", 5, 4, 4, 5, 4, "Robust engineering and solid benchmark evidence. Clear winner."),

    # prj_bf_02 (ByteForge): 2nd place
    ("sc_bf_02_1", "jdg_01", "prj_bf_02", 4, 3, 4, 4, 3, "Solid WASM memory boundary implementation."),
    ("sc_bf_02_2", "jdg_02", "prj_bf_02", 5, 4, 5, 5, 4, "Brilliant compiler optimizations and clean developer experience."),
    ("sc_bf_02_3", "jdg_03", "prj_bf_02", 4, 4, 4, 4, 4, "Reliable execution sandbox with good unit test coverage."),

    # prj_bf_03 (Neural Nomads): 3rd place
    ("sc_bf_03_1", "jdg_01", "prj_bf_03", 3, 4, 3, 3, 4, "Ambitious federated architecture, though proof-of-concept is somewhat raw."),
    ("sc_bf_03_2", "jdg_02", "prj_bf_03", 4, 5, 4, 4, 4, "Loved the privacy guarantees and decentralized ethos."),
    ("sc_bf_03_3", "jdg_03", "prj_bf_03", 4, 4, 3, 3, 4, "Interesting research direction, good technical progress."),

    # prj_bf_04 (Runtime Rebels): 4th place
    ("sc_bf_04_1", "jdg_01", "prj_bf_04", 3, 3, 3, 3, 3, "Competent io_uring wrapper, standard design patterns."),
    ("sc_bf_04_2", "jdg_02", "prj_bf_04", 4, 4, 4, 4, 3, "High throughput and beautiful C++ codebase."),
    ("sc_bf_04_3", "jdg_03", "prj_bf_04", 4, 3, 3, 3, 3, "Solid low-latency broker implementation."),

    # prj_bf_05 (ZeroDay Studio): 5th place
    ("sc_bf_05_1", "jdg_01", "prj_bf_05", 3, 3, 3, 2, 3, "Fuzzing works but relies heavily on upstream AFL++ heuristics."),
    ("sc_bf_05_2", "jdg_02", "prj_bf_05", 4, 4, 4, 3, 4, "Very useful security pipeline for CI integration."),
    ("sc_bf_05_3", "jdg_03", "prj_bf_05", 3, 3, 3, 3, 3, "Reasonable fuzzing harness."),

    # prj_bf_06 (EdgeCraft): 6th place
    ("sc_bf_06_1", "jdg_01", "prj_bf_06", 2, 3, 2, 3, 2, "Hardware constraints make it tricky to reproduce in evaluation."),
    ("sc_bf_06_2", "jdg_02", "prj_bf_06", 4, 4, 3, 4, 3, "Great IoT physical hardware demonstration."),
    ("sc_bf_06_3", "jdg_03", "prj_bf_06", 3, 3, 3, 3, 2, "Working embedded sensor prototype."),

    # prj_bf_07 (Synthetix Agent): 7th place
    ("sc_bf_07_1", "jdg_01", "prj_bf_07", 3, 2, 2, 2, 2, "Standard prompt engineering wrapper around existing APIs."),
    ("sc_bf_07_2", "jdg_02", "prj_bf_07", 3, 4, 3, 4, 3, "Helpful developer tool that reduces onboarding friction."),
    ("sc_bf_07_3", "jdg_03", "prj_bf_07", 3, 3, 3, 2, 2, "Usable developer onboarding agent."),
]

AI_SCORES_DATA = [
    # prj_ai_01 (3/3)
    ("sc_ai_01_1", "jdg_01", "prj_ai_01", 4, 5, 4, 4, 4, "Strong agent coordination architecture."),
    ("sc_ai_01_2", "jdg_02", "prj_ai_01", 5, 4, 4, 5, 5, "Impressive live multi-agent negotiation."),
    ("sc_ai_01_3", "jdg_03", "prj_ai_01", 4, 4, 4, 4, 4, "Technically sound and well documented."),

    # prj_ai_02 (2/3)
    ("sc_ai_02_1", "jdg_01", "prj_ai_02", 4, 4, 5, 4, 4, "High performance CUDA kernels."),
    ("sc_ai_02_2", "jdg_02", "prj_ai_02", 4, 4, 4, 4, 4, "Clean vector database API."),

    # prj_ai_03 (1/3)
    ("sc_ai_03_2", "jdg_02", "prj_ai_03", 4, 4, 3, 3, 4, "Practical prompt security guardrails."),

    # prj_ai_04 (DeepTrace): 0 reviews! Intentionally left empty!

    # prj_ai_05 (2/3)
    ("sc_ai_05_2", "jdg_02", "prj_ai_05", 4, 4, 4, 4, 4, "Good workflow abstraction."),
    ("sc_ai_05_3", "jdg_03", "prj_ai_05", 3, 3, 4, 4, 3, "Solid graph scheduler."),

    # prj_ai_06 (2/3)
    ("sc_ai_06_1", "jdg_01", "prj_ai_06", 4, 4, 4, 4, 4, "Kubernetes operator is well structured."),
    ("sc_ai_06_3", "jdg_03", "prj_ai_06", 4, 4, 3, 4, 3, "Speculative decoding benchmarks look promising."),
]


def ensure_demo_scenarios(db: Session) -> dict:
    """Idempotently seed rich, realistic demo events:
    1. BuildForge 2026 (evt_buildforge): Completed, published, 7 projects, 5 prizes matching podium & leaderboard.
    2. AI Systems Challenge 2026 (evt_aisystems): In-progress judging, mixed review counts (0, 1, 2, 3), unpublished.
    3. Open Systems Hack 2026 (evt_open_live): Open deadline for live participant submission demo.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    results = {}

    # Ensure Judge 3 (Dr. Aris Thorne) exists for multi-judge panel
    judge_3_user = db.query(User).filter(User.id == "usr_jdg_03").first()
    if not judge_3_user:
        judge_3_user = User(
            id="usr_jdg_03",
            email="aris.thorne@judgeforge.local",
            name="Dr. Aris Thorne",
            role="judge",
        )
        db.add(judge_3_user)
        db.flush()
        db.add(SessionToken(token="token_judge_c", user_id=judge_3_user.id))
        db.add(SessionToken(token="jdg_c_77aa", user_id=judge_3_user.id))
        db.add(PasswordCredential(user_id=judge_3_user.id, password_hash=hash_password("JudgeForge-Demo-2026!"), demo=1))

    judge_3 = db.query(Judge).filter(Judge.id == "jdg_03").first()
    if not judge_3:
        judge_3 = Judge(
            id="jdg_03",
            user_id=judge_3_user.id,
            name="Dr. Aris Thorne",
            email="aris.thorne@judgeforge.local",
        )
        db.add(judge_3)
        db.flush()

    judge_1 = db.query(Judge).filter(Judge.id == "jdg_01").first()
    judge_2 = db.query(Judge).filter(Judge.id == "jdg_02").first()

    # =========================================================================
    # 1. SCENARIO 1: BuildForge 2026 (Completed & Published)
    # =========================================================================
    bf_event = db.query(Event).filter(Event.id == "evt_buildforge").first()
    if not bf_event:
        bf_close = now - datetime.timedelta(days=7)
        bf_pub = now - datetime.timedelta(days=6)
        bf_event = Event(
            id="evt_buildforge",
            name="BuildForge 2026",
            submissions_close=bf_close,
            results_published=1,
            results_published_at=bf_pub,
        )
        db.add(bf_event)
        db.flush()

        # Tracks
        trk_infra = Track(id="trk_bf_infra", event_id=bf_event.id, name="Cloud & Infrastructure")
        trk_ai = Track(id="trk_bf_ai", event_id=bf_event.id, name="AI Agents & Automation")
        trk_tools = Track(id="trk_bf_tools", event_id=bf_event.id, name="Developer Experience")
        db.add_all([trk_infra, trk_ai, trk_tools])
        db.flush()

        # Assign judges to tracks
        for j in [judge_1, judge_2, judge_3]:
            if j:
                existing_ids = {t.id for t in j.tracks}
                for t in [trk_infra, trk_ai, trk_tools]:
                    if t.id not in existing_ids:
                        j.tracks.append(t)
        db.flush()

        # Rubric Criteria (5 default criteria with weights 0.25, 0.20, 0.25, 0.20, 0.10)
        crit_func = RubricCriterion(id="crit_bf_func", event_id=bf_event.id, name="functionality", label="Functionality & Completeness", description="Does the project actually work and deliver its core promise?", weight=0.25, min_score=1, max_score=5)
        crit_innov = RubricCriterion(id="crit_bf_innov", event_id=bf_event.id, name="innovation", label="Innovation & Problem Solving", description="How original and meaningful is the solution?", weight=0.20, min_score=1, max_score=5)
        crit_qual = RubricCriterion(id="crit_bf_qual", event_id=bf_event.id, name="quality", label="GitHub Code & Engineering Quality", description="How strong is the actual implementation behind the project?", weight=0.25, min_score=1, max_score=5)
        crit_demo = RubricCriterion(id="crit_bf_demo", event_id=bf_event.id, name="live_demo", label="Live Demo & Product Experience", description="How convincing is the working product experience?", weight=0.20, min_score=1, max_score=5)
        crit_pres = RubricCriterion(id="crit_bf_pres", event_id=bf_event.id, name="presentation", label="Pitch Deck & Presentation", description="How clearly does the team communicate the problem, solution and value?", weight=0.10, min_score=1, max_score=5)
        db.add_all([crit_func, crit_innov, crit_qual, crit_demo, crit_pres])

        # 5 Placement Prizes matching Top 5 Leaderboard
        prizes = [
            Prize(id="prz_bf_01", event_id=bf_event.id, title="1st Place — Grand Prize", description="Demo Event Prize — Top scoring overall submission across all tracks (simulated for platform evaluation)", amount="$10,000", placement="1st"),
            Prize(id="prz_bf_02", event_id=bf_event.id, title="2nd Place — Runner Up", description="Demo Event Prize — Second place overall across all tracks (simulated for platform evaluation)", amount="$5,000", placement="2nd"),
            Prize(id="prz_bf_03", event_id=bf_event.id, title="3rd Place — Bronze Award", description="Demo Event Prize — Third place overall across all tracks (simulated for platform evaluation)", amount="$2,500", placement="3rd"),
            Prize(id="prz_bf_04", event_id=bf_event.id, title="4th Place — Honorable Mention", description="Demo Event Prize — Fourth place overall across all tracks (simulated for platform evaluation)", amount="$1,000", placement="4th"),
            Prize(id="prz_bf_05", event_id=bf_event.id, title="5th Place — Finalist Award", description="Demo Event Prize — Fifth place overall across all tracks (simulated for platform evaluation)", amount="$500", placement="5th"),
        ]
        db.add_all(prizes)
        db.flush()

        # 7 Projects with Realistic Diverse Teams
        bf_projects_data = [
            {
                "id": "prj_bf_01",
                "title": "NovaStack",
                "tagline": "Self-healing distributed microvm orchestration platform",
                "summary": "NovaStack deploys sub-second microVM instances on bare metal with distributed Raft consensus and zero-overhead memory deduplication.",
                "description": "Problem:\nTraditional cloud orchestration frameworks impose massive runtime overhead and slow cold-start latencies for event-driven workloads.\n\nSolution:\nNovaStack implements a hyper-optimized control plane built on Firecracker and Tokio, offering deterministic boot times under 12ms and autonomous peer-recovery.\n\nArchitecture:\n- Kernel-level eBPF socket filters for instant packet redirection.\n- Lightweight state machine replicating configuration through raft consensus.\n- Offline-first local CLI and Prometheus telemetry exporters.",
                "track_id": trk_infra.id,
                "team_name": "Atlas Labs",
                "team_id": "tm_bf_01",
                "members": ["sarah.lin@atlaslabs.io", "alex.r@atlaslabs.io", "marcus.v@atlaslabs.io"],
                "tech_stack": "Rust, Firecracker, gRPC, Tokio, eBPF",
                "repo_url": "https://github.com/atlas-labs/novastack",
                "demo_url": "https://novastack.run",
                "video_url": "https://youtube.com/watch?v=novastack-demo",
                "pitch_deck_url": "https://atlas-labs.io/deck.pdf",
            },
            {
                "id": "prj_bf_02",
                "title": "ByteForge",
                "tagline": "Compiler-level deterministic sandbox for untrusted code execution",
                "summary": "Safe multi-tenant code evaluation engine using WebAssembly isolation and cycle-counted fuel metering.",
                "description": "Problem:\nExecuting untrusted user code in web platforms typically requires heavyweight Docker containers that waste system memory.\n\nSolution:\nByteForge embeds a secure WebAssembly runtime with static memory limits, fine-grained system call interception, and sub-millisecond execution resets.\n\nArchitecture:\n- Custom LLVM optimization pass compiling untrusted scripts directly to verified bytecode.\n- Deterministic gas and instruction counting to guarantee termination.",
                "track_id": trk_tools.id,
                "team_name": "Kernel Crew",
                "team_id": "tm_bf_02",
                "members": ["david.kim@kernelcrew.org", "elena.r@kernelcrew.org"],
                "tech_stack": "WebAssembly, LLVM, Zig, TypeScript",
                "repo_url": "https://github.com/kernel-crew/byteforge",
                "demo_url": "https://byteforge.dev",
                "video_url": "https://youtube.com/watch?v=byteforge-demo",
                "pitch_deck_url": "",
            },
            {
                "id": "prj_bf_03",
                "title": "Neural Nomads",
                "tagline": "Decentralized federated training protocol for edge foundation models",
                "summary": "Differential privacy-preserving edge aggregation protocol synchronizing weights across heterogeneous consumer devices.",
                "description": "Problem:\nCentralizing sensitive private user data for AI fine-tuning introduces severe privacy and regulatory compliance hazards.\n\nSolution:\nNeural Nomads distributes gradient descent steps locally onto edge hardware, transmitting only differentially private noise-masked tensors to the coordinator.\n\nArchitecture:\n- Secure multi-party aggregation using libp2p mesh transport.\n- Adaptive client dropout tolerance and quantized tensor compression.",
                "track_id": trk_ai.id,
                "team_name": "Neural Nomads",
                "team_id": "tm_bf_03",
                "members": ["priya.p@neuralnomads.ai", "liam.c@neuralnomads.ai", "sofia.m@neuralnomads.ai"],
                "tech_stack": "PyTorch, CUDA, libp2p, Go",
                "repo_url": "https://github.com/neuralnomads/federated-edge",
                "demo_url": "https://nomad-mesh.ai",
                "video_url": "https://youtube.com/watch?v=nomads-demo",
                "pitch_deck_url": "https://neuralnomads.ai/pitch.pdf",
            },
            {
                "id": "prj_bf_04",
                "title": "Runtime Rebels",
                "tagline": "Low-latency streaming event broker with zero-allocation buffers",
                "summary": "C++20 kernel-bypass event log handling up to 10M msg/sec with sub-microsecond P99 tail latency.",
                "description": "High-performance messaging backbone utilizing Linux io_uring and userspace ring buffers to eliminate thread context switches during burst traffic.",
                "track_id": trk_infra.id,
                "team_name": "Runtime Rebels",
                "team_id": "tm_bf_04",
                "members": ["viktor.b@rebels.tech", "chloe.m@rebels.tech"],
                "tech_stack": "C++20, io_uring, DPDK, CMake",
                "repo_url": "https://github.com/runtimerebels/rebels-broker",
                "demo_url": "",
                "video_url": "",
                "pitch_deck_url": "",
            },
            {
                "id": "prj_bf_05",
                "title": "ZeroDay Studio",
                "tagline": "Automated binary fuzzing pipeline with AI-driven exploit mitigation",
                "summary": "Continuous vulnerability discovery harness integrating coverage-guided AFL++ fuzzing and symbolic execution.",
                "description": "Combines compiler sanitizers with automated exploit triage to detect use-after-free and buffer overflows in compiled binaries before production deployment.",
                "track_id": trk_tools.id,
                "team_name": "ZeroDay Studio",
                "team_id": "tm_bf_05",
                "members": ["jamal.w@zeroday.sec", "nadia.n@zeroday.sec"],
                "tech_stack": "Python, Ghidra, AFL++, Docker",
                "repo_url": "https://github.com/zerodaystudio/afl-auto-fuzz",
                "demo_url": "",
                "video_url": "",
                "pitch_deck_url": "https://zeroday.security/deck.pdf",
            },
            {
                "id": "prj_bf_06",
                "title": "EdgeCraft",
                "tagline": "Embedded sensor telemetry synthesis and mesh synchronization",
                "summary": "Ultra-low power IoT mesh protocol syncing spatial sensor readings over LoRaWAN and local BLE ad-hoc networks.",
                "description": "Designed for industrial telemetry in off-grid environments where power and connectivity are constrained. Implements delta-compression and CRDTs.",
                "track_id": trk_infra.id,
                "team_name": "EdgeCraft",
                "team_id": "tm_bf_06",
                "members": ["mateo.s@edgecraft.io", "hannah.a@edgecraft.io"],
                "tech_stack": "Embedded C, FreeRTOS, MQTT, SQLite",
                "repo_url": "",
                "demo_url": "https://edgecraft.io/dashboard",
                "video_url": "",
                "pitch_deck_url": "",
            },
            {
                "id": "prj_bf_07",
                "title": "Synthetix Agent",
                "tagline": "Autonomous multi-agent workflow synthesizer for developer onboarding",
                "summary": "LLM agent cluster that inspects unfamiliar git repositories, generates reproducible devcontainers, and runs automated verification.",
                "description": "Reduces engineer ramp-up time from days to minutes by automatically detecting missing toolchains, parsing dockerfiles, and running test suites in sandboxes.",
                "track_id": trk_ai.id,
                "team_name": "Synthetix Labs",
                "team_id": "tm_bf_07",
                "members": ["rachel.g@synthetix.dev", "tariq.a@synthetix.dev"],
                "tech_stack": "Python, FastAPI, OpenAI, LangChain",
                "repo_url": "https://github.com/synthetix/agent-core",
                "demo_url": "",
                "video_url": "https://youtube.com/watch?v=synthetix-agent",
                "pitch_deck_url": "",
            },
        ]

        for p_info in bf_projects_data:
            # Create Team
            team = Team(id=p_info["team_id"], event_id=bf_event.id, name=p_info["team_name"])
            db.add(team)
            db.flush()
            for m_email in p_info["members"]:
                db.add(TeamMember(team_id=team.id, email=m_email))

            # Create Project
            p = Project(
                id=p_info["id"],
                event_id=bf_event.id,
                team_id=team.id,
                track_id=p_info["track_id"],
                title=p_info["title"],
                tagline=p_info["tagline"],
                summary=p_info["summary"],
                description=p_info["description"],
                tech_stack=p_info["tech_stack"],
                repo_url=p_info["repo_url"] or None,
                demo_url=p_info["demo_url"] or None,
                video_url=p_info["video_url"] or None,
                pitch_deck_url=p_info["pitch_deck_url"] or None,
                status="submitted",
                submitted_at=bf_close - datetime.timedelta(hours=4),
            )
            db.add(p)
        db.flush()

        # Review Scores (Judge 1: Strict, Judge 2: Lenient, Judge 3: Moderate)
        # Formulated so NovaStack is 1st, ByteForge is 2nd, Neural Nomads is 3rd, Runtime Rebels is 4th, ZeroDay is 5th
        scores_data = BF_SCORES_DATA

        for _, j_id, p_id, f, i, q, ld, pres, comment in scores_data:
            c_dict = {
                "functionality": f,
                "innovation": i,
                "quality": q,
                "live_demo": ld,
                "presentation": pres,
            }
            sc = Score(
                judge_id=j_id,
                project_id=p_id,
                functionality=f,
                quality=q,
                innovation=i,
                criteria_json=json.dumps(c_dict),
                comment=comment,
                submitted_at=bf_close - datetime.timedelta(hours=2),
            )
            db.add(sc)
        db.commit()
        results["buildforge"] = "Created BuildForge 2026 (Published, 7 projects, 21 reviews)"
    else:
        results["buildforge"] = "BuildForge 2026 already exists"

    # =========================================================================
    # 2. SCENARIO 2: AI Systems Challenge 2026 (Active Judging In Progress)
    # =========================================================================
    ai_event = db.query(Event).filter(Event.id == "evt_aisystems").first()
    if not ai_event:
        ai_close = now - datetime.timedelta(hours=12)
        ai_event = Event(
            id="evt_aisystems",
            name="AI Systems Challenge 2026",
            submissions_close=ai_close,
            results_published=0,  # UNPUBLISHED
        )
        db.add(ai_event)
        db.flush()

        # Tracks
        trk_auto = Track(id="trk_ai_auto", event_id=ai_event.id, name="Autonomous Systems")
        trk_ai_infra = Track(id="trk_ai_infra", event_id=ai_event.id, name="Model Infrastructure")
        trk_safety = Track(id="trk_ai_safety", event_id=ai_event.id, name="AI Safety & Alignment")
        db.add_all([trk_auto, trk_ai_infra, trk_safety])
        db.flush()

        # Assign judges
        for j in [judge_1, judge_2, judge_3]:
            if j:
                existing_ids = {t.id for t in j.tracks}
                for t in [trk_auto, trk_ai_infra, trk_safety]:
                    if t.id not in existing_ids:
                        j.tracks.append(t)
        db.flush()

        # Criteria
        crit_ai_func = RubricCriterion(id="crit_ai_func", event_id=ai_event.id, name="functionality", label="Functionality & Completeness", description="Does the project actually work and deliver its core promise?", weight=0.25, min_score=1, max_score=5)
        crit_ai_innov = RubricCriterion(id="crit_ai_innov", event_id=ai_event.id, name="innovation", label="Innovation & Problem Solving", description="How original and meaningful is the solution?", weight=0.20, min_score=1, max_score=5)
        crit_ai_qual = RubricCriterion(id="crit_ai_qual", event_id=ai_event.id, name="quality", label="GitHub Code & Engineering Quality", description="How strong is the actual implementation behind the project?", weight=0.25, min_score=1, max_score=5)
        crit_ai_demo = RubricCriterion(id="crit_ai_demo", event_id=ai_event.id, name="live_demo", label="Live Demo & Product Experience", description="How convincing is the working product experience?", weight=0.20, min_score=1, max_score=5)
        crit_ai_pres = RubricCriterion(id="crit_ai_pres", event_id=ai_event.id, name="presentation", label="Pitch Deck & Presentation", description="How clearly does the team communicate the problem, solution and value?", weight=0.10, min_score=1, max_score=5)
        db.add_all([crit_ai_func, crit_ai_innov, crit_ai_qual, crit_ai_demo, crit_ai_pres])

        # Prizes
        db.add_all([
            Prize(id="prz_ai_01", event_id=ai_event.id, title="1st Place — Grand Prize", description="Top scoring overall AI system", amount="$8,000", placement="1st"),
            Prize(id="prz_ai_02", event_id=ai_event.id, title="2nd Place — Runner Up", description="Second place overall", amount="$4,000", placement="2nd"),
            Prize(id="prz_ai_03", event_id=ai_event.id, title="3rd Place — Third Award", description="Third place overall", amount="$2,000", placement="3rd"),
        ])
        db.flush()

        # 6 Projects
        ai_projects_data = [
            ("prj_ai_01", "AgentMesh", "Decentralized consensus protocol for multi-agent negotiation", trk_auto.id, "Distributed Dynamics", "tm_ai_01", "Python, Ray, gRPC, LangChain", "https://github.com/dist-dyn/agent-mesh", "https://mesh.ai.internal"),
            ("prj_ai_02", "CortexDB", "GPU-accelerated vector storage engine with inverted HNSW indexing", trk_ai_infra.id, "Vector Velocity", "tm_ai_02", "C++, CUDA, Python, Docker", "https://github.com/vec-vel/cortexdb", "https://cortexdb.dev"),
            ("prj_ai_03", "PromptSentry", "Real-time context window guardrail inspecting jailbreaks and data exfiltration", trk_safety.id, "Guardrail Security", "tm_ai_03", "Go, Rust, WebAssembly", "https://github.com/guardrail/promptsentry", ""),
            ("prj_ai_04", "DeepTrace", "Continuous LLM latency and token attribution profiler", trk_ai_infra.id, "Observability Ops", "tm_ai_04", "Python, OpenTelemetry, React", "https://github.com/obs-ops/deeptrace", ""),
            ("prj_ai_05", "CognitiveFlow", "Adaptive workflow graph execution engine with dynamic error recovery", trk_auto.id, "Flow State AI", "tm_ai_05", "TypeScript, Node.js, Redis", "https://github.com/flowstate/cognitive-flow", "https://cognitiveflow.app"),
            ("prj_ai_06", "KubeLLM", "Automated model sharding and speculative decoding operator for Kubernetes", trk_ai_infra.id, "Cloud Native AI", "tm_ai_06", "Go, Kubernetes, vLLM", "https://github.com/cloudnative/kubellm", ""),
        ]

        for p_id, title, tag, trk_id, team_name, tm_id, stack, repo, demo in ai_projects_data:
            team = Team(id=tm_id, event_id=ai_event.id, name=team_name)
            db.add(team)
            db.flush()
            db.add(TeamMember(team_id=team.id, email=f"lead@{tm_id}.org"))

            p = Project(
                id=p_id,
                event_id=ai_event.id,
                team_id=team.id,
                track_id=trk_id,
                title=title,
                tagline=tag,
                summary=f"{title} delivers high-reliability tooling for production machine learning deployments.",
                description=f"Detailed architecture and verification methodology for {title}. Implements resilient fallback strategies and benchmark validation.",
                tech_stack=stack,
                repo_url=repo or None,
                demo_url=demo or None,
                status="submitted",
                submitted_at=ai_close - datetime.timedelta(hours=2),
            )
            db.add(p)
        db.flush()

        # MIXED REVIEW STATE:
        # Project 1 (AgentMesh): 3/3 reviews
        # Project 2 (CortexDB): 2/3 reviews
        # Project 3 (PromptSentry): 1/3 review
        # Project 4 (DeepTrace): 0/3 reviews (ZERO reviews -> unreviewed warning alert!)
        # Project 5 (CognitiveFlow): 2/3 reviews
        # Project 6 (KubeLLM): 2/3 reviews
        ai_scores = AI_SCORES_DATA

        for _, j_id, p_id, f, i, q, ld, pres, comment in ai_scores:
            c_dict = {
                "functionality": f,
                "innovation": i,
                "quality": q,
                "live_demo": ld,
                "presentation": pres,
            }
            sc = Score(
                judge_id=j_id,
                project_id=p_id,
                functionality=f,
                quality=q,
                innovation=i,
                criteria_json=json.dumps(c_dict),
                comment=comment,
                submitted_at=now - datetime.timedelta(hours=6),
            )
            db.add(sc)
        db.commit()
        results["aisystems"] = "Created AI Systems Challenge 2026 (Judging in progress, 6 projects, mixed reviews)"
    else:
        results["aisystems"] = "AI Systems Challenge 2026 already exists"

    # =========================================================================
    # 3. SCENARIO 3: Open Systems Hack 2026 (Open for Live Participant Submission)
    # =========================================================================
    open_event = db.query(Event).filter(Event.id == "evt_open_live").first()
    if not open_event:
        open_close = now + datetime.timedelta(days=14)
        open_event = Event(
            id="evt_open_live",
            name="Open Systems Hack 2026",
            submissions_close=open_close,
            results_published=0,
        )
        db.add(open_event)
        db.flush()

        trk_live_main = Track(id="trk_live_main", event_id=open_event.id, name="Open Systems & Developer Tools")
        trk_live_edge = Track(id="trk_live_edge", event_id=open_event.id, name="Edge Infrastructure & Security")
        db.add_all([trk_live_main, trk_live_edge])
        db.flush()

        # Assign judges
        for j in [judge_1, judge_2, judge_3]:
            if j:
                existing_ids = {t.id for t in j.tracks}
                for t in [trk_live_main, trk_live_edge]:
                    if t.id not in existing_ids:
                        j.tracks.append(t)
        db.flush()

        # Criteria (5 default criteria)
        db.add_all([
            RubricCriterion(id="crit_live_func", event_id=open_event.id, name="functionality", label="Functionality & Completeness", description="Does the project actually work and deliver its core promise?", weight=0.25, min_score=1, max_score=5),
            RubricCriterion(id="crit_live_innov", event_id=open_event.id, name="innovation", label="Innovation & Problem Solving", description="How original and meaningful is the solution?", weight=0.20, min_score=1, max_score=5),
            RubricCriterion(id="crit_live_qual", event_id=open_event.id, name="quality", label="GitHub Code & Engineering Quality", description="How strong is the actual implementation behind the project?", weight=0.25, min_score=1, max_score=5),
            RubricCriterion(id="crit_live_demo", event_id=open_event.id, name="live_demo", label="Live Demo & Product Experience", description="How convincing is the working product experience?", weight=0.20, min_score=1, max_score=5),
            RubricCriterion(id="crit_live_pres", event_id=open_event.id, name="presentation", label="Pitch Deck & Presentation", description="How clearly does the team communicate the problem, solution and value?", weight=0.10, min_score=1, max_score=5),
        ])

        # Prizes
        db.add_all([
            Prize(id="prz_live_01", event_id=open_event.id, title="1st Place — Grand Prize", description="Top scoring overall open submission", amount="$5,000", placement="1st"),
            Prize(id="prz_live_02", event_id=open_event.id, title="2nd Place — Runner Up", description="Second place overall", amount="$2,500", placement="2nd"),
        ])
        db.commit()
        results["open_live"] = "Created Open Systems Hack 2026 (Open for live submission)"
    else:
        results["open_live"] = "Open Systems Hack 2026 already exists"

    # Ensure existing demo events have all 5 criteria and updated weights/labels
    for ev_id, prefix in [("evt_buildforge", "crit_bf"), ("evt_aisystems", "crit_ai"), ("evt_open_live", "crit_live")]:
        ev = db.query(Event).filter(Event.id == ev_id).first()
        if ev:
            existing_crit = {c.name: c for c in db.query(RubricCriterion).filter(RubricCriterion.event_id == ev_id).all()}
            for crit_def in DEFAULT_FIVE_CRITERIA:
                c_name = crit_def["name"]
                if c_name not in existing_crit:
                    db.add(RubricCriterion(
                        id=f"{prefix}_{c_name}",
                        event_id=ev_id,
                        name=c_name,
                        label=crit_def["label"],
                        description=crit_def["description"],
                        weight=crit_def["weight"],
                        min_score=1,
                        max_score=5,
                    ))
                else:
                    c = existing_crit[c_name]
                    c.label = crit_def["label"]
                    c.description = crit_def["description"]
                    c.weight = crit_def["weight"]

    # Ensure existing scores in evt_buildforge have criteria_json populated and match podium
    bf_scores = db.query(Score).join(Project).filter(Project.event_id == "evt_buildforge").all()
    bf_score_map = {
        (j_id, p_id): {
            "functionality": f,
            "innovation": i,
            "quality": q,
            "live_demo": ld,
            "presentation": pres,
        }
        for _, j_id, p_id, f, i, q, ld, pres, _ in BF_SCORES_DATA
    }
    for sc in bf_scores:
        key = (sc.judge_id, sc.project_id)
        if key in bf_score_map:
            c_dict = bf_score_map[key]
            sc.criteria_json = json.dumps(c_dict)
            sc.functionality = c_dict["functionality"]
            sc.quality = c_dict["quality"]
            sc.innovation = c_dict["innovation"]
        elif not sc.criteria_json:
            sc.criteria_json = json.dumps({
                "functionality": sc.functionality or 3,
                "innovation": sc.innovation or 3,
                "quality": sc.quality or 3,
                "live_demo": 3,
                "presentation": 3,
            })

    # Ensure existing scores in evt_aisystems have criteria_json populated
    ai_score_map = {
        (j_id, p_id): {
            "functionality": f,
            "innovation": i,
            "quality": q,
            "live_demo": ld,
            "presentation": pres,
        }
        for _, j_id, p_id, f, i, q, ld, pres, _ in AI_SCORES_DATA
    }
    ai_scores_db = db.query(Score).join(Project).filter(Project.event_id == "evt_aisystems").all()
    for sc in ai_scores_db:
        key = (sc.judge_id, sc.project_id)
        if key in ai_score_map:
            c_dict = ai_score_map[key]
            sc.criteria_json = json.dumps(c_dict)
            sc.functionality = c_dict["functionality"]
            sc.quality = c_dict["quality"]
            sc.innovation = c_dict["innovation"]
        elif not sc.criteria_json:
            sc.criteria_json = json.dumps({
                "functionality": sc.functionality or 3,
                "innovation": sc.innovation or 3,
                "quality": sc.quality or 3,
                "live_demo": 3,
                "presentation": 3,
            })

    db.commit()

    return results
