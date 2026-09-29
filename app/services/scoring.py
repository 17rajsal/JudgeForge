import math
from typing import Dict, List, Any, Optional
from sqlalchemy.orm import Session
from app.models import Project, Score, Judge, RubricCriterion, Track, Team


import json

def calculate_raw_score(score: Score, criteria_weights: Dict[str, float]) -> float:
    """
    Calculates weighted raw score using dynamic criteria from score.criteria_json,
    falling back to legacy columns (functionality, quality, innovation) if criteria_json is absent.
    Matches values against criteria_weights and ignores unknown/unconfigured values.
    """
    scores_dict: Dict[str, float] = {}

    # 1. Parse score.criteria_json safely
    if getattr(score, "criteria_json", None):
        try:
            parsed = json.loads(score.criteria_json)
            if isinstance(parsed, dict):
                scores_dict = {str(k): float(v) for k, v in parsed.items() if isinstance(v, (int, float))}
        except Exception:
            scores_dict = {}

    # 2. For legacy score records: construct equivalent criteria data from legacy columns when criteria_json is absent
    if not scores_dict:
        if score.functionality is not None:
            scores_dict["functionality"] = float(score.functionality)
        if score.quality is not None:
            scores_dict["quality"] = float(score.quality)
        if score.innovation is not None:
            scores_dict["innovation"] = float(score.innovation)

    if not scores_dict:
        return 0.0

    # 3. Calculate weighted raw score using all configured criteria matching criteria_weights
    w_sum = 0.0
    s_sum = 0.0

    for name, val in scores_dict.items():
        if name in criteria_weights:
            w = criteria_weights[name]
            if w > 0:
                s_sum += val * w
                w_sum += w

    # Fallback if no configured criteria matched criteria_weights (e.g. legacy fallback)
    if w_sum == 0.0:
        for name, val in scores_dict.items():
            w = criteria_weights.get(name, 1.0)
            if w > 0:
                s_sum += val * w
                w_sum += w

    if w_sum == 0.0:
        return 0.0
    return round(s_sum / w_sum, 4)


DEFAULT_FIVE_CRITERIA = [
    {
        "name": "functionality",
        "label": "Functionality & Completeness",
        "description": "Does the project actually work and deliver its core promise?",
        "weight": 0.25,
    },
    {
        "name": "innovation",
        "label": "Innovation & Problem Solving",
        "description": "How original and meaningful is the solution?",
        "weight": 0.20,
    },
    {
        "name": "quality",
        "label": "GitHub Code & Engineering Quality",
        "description": "How strong is the actual implementation behind the project?",
        "weight": 0.25,
    },
    {
        "name": "live_demo",
        "label": "Live Demo & Product Experience",
        "description": "How convincing is the working product experience?",
        "weight": 0.20,
    },
    {
        "name": "presentation",
        "label": "Pitch Deck & Presentation",
        "description": "How clearly does the team communicate the problem, solution and value?",
        "weight": 0.10,
    },
]

CRITERIA_GUIDANCE = {
    "functionality": {
        "question": "Does the project actually work and deliver its core promise?",
        "options": [
            {"score": 1, "label": "Broken", "desc": "Core workflow does not work."},
            {"score": 2, "label": "Partial", "desc": "Some functionality works, but major parts are missing."},
            {"score": 3, "label": "Working MVP", "desc": "Core use case works end-to-end."},
            {"score": 4, "label": "Strong", "desc": "Feature-complete for the hackathon scope with only minor issues."},
            {"score": 5, "label": "Excellent", "desc": "Reliable, polished and convincingly complete."},
        ],
    },
    "innovation": {
        "question": "How original and meaningful is the solution?",
        "options": [
            {"score": 1, "label": "Conventional", "desc": "Mostly a standard implementation with little differentiation."},
            {"score": 2, "label": "Some Originality", "desc": "Contains a few interesting ideas."},
            {"score": 3, "label": "Creative", "desc": "Uses a meaningful or thoughtful approach."},
            {"score": 4, "label": "Distinctive", "desc": "Clearly differentiates itself technically or as a product."},
            {"score": 5, "label": "Exceptional", "desc": "Highly original solution with strong problem-solving insight."},
        ],
    },
    "quality": {
        "question": "How strong is the actual implementation behind the project?",
        "options": [
            {"score": 1, "label": "Weak", "desc": "Very incomplete, unclear or fragile implementation."},
            {"score": 2, "label": "Basic", "desc": "Working code but substantial architecture/quality problems."},
            {"score": 3, "label": "Solid", "desc": "Readable structure and reasonable implementation quality."},
            {"score": 4, "label": "Strong", "desc": "Good architecture, maintainability and engineering decisions."},
            {"score": 5, "label": "Excellent", "desc": "Exceptionally clean, well-structured and production-minded implementation."},
        ],
    },
    "live_demo": {
        "question": "How convincing is the working product experience?",
        "options": [
            {"score": 1, "label": "Not Demonstrable", "desc": "Demo is unavailable or core experience fails."},
            {"score": 2, "label": "Rough", "desc": "Demo works partially but has major UX/reliability issues."},
            {"score": 3, "label": "Good MVP", "desc": "Main workflow is usable and understandable."},
            {"score": 4, "label": "Polished", "desc": "Smooth experience with strong usability."},
            {"score": 5, "label": "Excellent", "desc": "Highly polished, reliable and impressive live product experience."},
        ],
    },
    "presentation": {
        "question": "How clearly does the team communicate the problem, solution and value?",
        "options": [
            {"score": 1, "label": "Unclear", "desc": "Problem and solution are difficult to understand."},
            {"score": 2, "label": "Basic", "desc": "Core idea is explained but lacks clarity/detail."},
            {"score": 3, "label": "Clear", "desc": "Problem, solution and implementation are understandable."},
            {"score": 4, "label": "Strong", "desc": "Well-structured, convincing presentation."},
            {"score": 5, "label": "Excellent", "desc": "Very clear, concise and persuasive presentation backed by strong evidence."},
        ],
    },
}


def get_criteria_weights(db: Session, event_id: Optional[str] = None) -> Dict[str, float]:
    query = db.query(RubricCriterion)
    if event_id:
        query = query.filter(RubricCriterion.event_id == event_id)
    criteria = query.all()
    if not criteria:
        return {"functionality": 1.0, "quality": 1.0, "innovation": 1.0}
    return {c.name: c.weight for c in criteria}


def compute_leaderboard(db: Session, event_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Computes raw and normalized scores for all projects in an event.
    Applies Z-score normalization per judge:
    1. Computes mean and standard deviation for each judge.
    2. Zero variance edge-case: If a judge gave all projects the same score (sigma == 0),
       their z-score is set to 0.0 (neutral).
    3. Missing scores / incomplete review batches: Projects are averaged across their
       available normalized judge reviews.
    4. Rescales Z-scores back to standard rubric scale using global mean and std.
    """
    if not event_id:
        from app.models import Event
        primary_event = db.get(Event, "evt_01") or db.query(Event).order_by(Event.created_at.asc()).first()
        event_id = primary_event.id if primary_event else None

    criteria_weights = get_criteria_weights(db, event_id)

    # 1. Fetch all scores
    query = db.query(Score)
    if event_id:
        query = query.join(Project).filter(Project.event_id == event_id)
    all_scores = query.all()

    if not all_scores:
        # Return submitted projects with 0 scores
        p_query = db.query(Project).filter(Project.status == "submitted")
        if event_id:
            p_query = p_query.filter(Project.event_id == event_id)
        projects = p_query.all()
        return [
            {
                "project_id": p.id,
                "title": p.title,
                "team_id": p.team_id,
                "team_name": p.team.name if p.team else "",
                "track_id": p.track_id,
                "track_name": p.track.name if p.track else "",
                "review_count": 0,
                "raw_score": 0.0,
                "normalized_score": 0.0,
                "repo_url": p.repo_url or "",
                "summary": p.summary or "",
                "tagline": p.tagline or p.summary or "",
                "demo_url": p.demo_url or "",
                "video_url": p.video_url or "",
                "pitch_deck_url": p.pitch_deck_url or "",
                "tech_stack": p.tech_stack or "",
                "rank": idx + 1,
                "raw_rank": idx + 1,
                "rank_delta": 0,
            }
            for idx, p in enumerate(projects)
        ]

    # Calculate raw score for each score record
    score_raw_map: Dict[int, float] = {}
    judge_scores: Dict[str, List[float]] = {}
    all_raw_values: List[float] = []

    for sc in all_scores:
        raw = calculate_raw_score(sc, criteria_weights)
        score_raw_map[sc.id] = raw
        judge_scores.setdefault(sc.judge_id, []).append(raw)
        all_raw_values.append(raw)

    global_mean = sum(all_raw_values) / len(all_raw_values) if all_raw_values else 3.0
    global_variance = (
        sum((x - global_mean) ** 2 for x in all_raw_values) / len(all_raw_values)
        if all_raw_values
        else 1.0
    )
    global_std = math.sqrt(global_variance) if global_variance > 1e-6 else 1.0

    # 2. Judge stats
    judge_stats: Dict[str, Dict[str, float]] = {}
    for j_id, vals in judge_scores.items():
        n = len(vals)
        mean_val = sum(vals) / n
        var_val = sum((v - mean_val) ** 2 for v in vals) / n
        std_val = math.sqrt(var_val)
        judge_stats[j_id] = {
            "mean": mean_val,
            "std": std_val,
            "count": n,
        }

    # 3. Project accumulation
    project_scores: Dict[str, List[Dict[str, float]]] = {}
    for sc in all_scores:
        raw = score_raw_map[sc.id]
        j_stat = judge_stats.get(sc.judge_id, {"mean": global_mean, "std": 0.0})
        j_std = j_stat["std"]
        j_mean = j_stat["mean"]

        if j_std > 1e-6:
            z = (raw - j_mean) / j_std
        else:
            # Judge gave all projects identical scores (zero variance)
            z = 0.0

        # Map z-score back to global scale
        normalized = global_mean + (z * global_std)
        # Clamp to rubric bounds [1.0, 5.0]
        normalized = max(1.0, min(5.0, normalized))

        project_scores.setdefault(sc.project_id, []).append({
            "raw": raw,
            "normalized": normalized,
        })

    # 4. Fetch submitted projects
    p_query = db.query(Project).filter(Project.status == "submitted")
    if event_id:
        p_query = p_query.filter(Project.event_id == event_id)
    projects = p_query.all()

    results = []
    for p in projects:
        sc_list = project_scores.get(p.id, [])
        count = len(sc_list)
        if count > 0:
            avg_raw = sum(s["raw"] for s in sc_list) / count
            avg_norm = sum(s["normalized"] for s in sc_list) / count
        else:
            avg_raw = 0.0
            avg_norm = 0.0

        results.append({
            "project_id": p.id,
            "title": p.title,
            "team_id": p.team_id,
            "team_name": p.team.name if p.team else "",
            "track_id": p.track_id,
            "track_name": p.track.name if p.track else "",
            "review_count": count,
            "raw_score": round(avg_raw, 4),
            "normalized_score": round(avg_norm, 4),
            "repo_url": p.repo_url or "",
            "summary": p.summary or "",
            "tagline": p.tagline or p.summary or "",
            "demo_url": p.demo_url or "",
            "video_url": p.video_url or "",
            "pitch_deck_url": p.pitch_deck_url or "",
            "tech_stack": p.tech_stack or "",
        })

    # Calculate ranking before normalization (Raw Rank)
    raw_sorted = sorted(results, key=lambda x: (x["raw_score"], x["project_id"]), reverse=True)
    raw_rank_map = {item["project_id"]: idx + 1 for idx, item in enumerate(raw_sorted)}

    # Sort descending by normalized score, then raw score, then project_id
    results.sort(key=lambda x: (x["normalized_score"], x["raw_score"], x["project_id"]), reverse=True)

    for rank, item in enumerate(results, start=1):
        item["rank"] = rank
        item["raw_rank"] = raw_rank_map.get(item["project_id"], rank)
        item["rank_delta"] = item["raw_rank"] - rank

    return results


def generate_results_csv(db: Session, event_id: Optional[str] = None) -> str:
    leaderboard = compute_leaderboard(db, event_id)
    lines = [
        "rank,project_id,title,team_name,track_name,review_count,raw_score,normalized_score,repo_url"
    ]
    for row in leaderboard:
        # Escape commas and quotes for CSV
        def clean(val):
            s = str(val).replace('"', '""')
            if "," in s or '"' in s or "\n" in s:
                return f'"{s}"'
            return s

        lines.append(
            f"{row['rank']},"
            f"{clean(row['project_id'])},"
            f"{clean(row['title'])},"
            f"{clean(row['team_name'])},"
            f"{clean(row['track_name'])},"
            f"{row['review_count']},"
            f"{row['raw_score']:.4f},"
            f"{row['normalized_score']:.4f},"
            f"{clean(row['repo_url'])}"
        )
    return "\n".join(lines) + "\n"
