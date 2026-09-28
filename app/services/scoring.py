import math
from typing import Dict, List, Any, Optional
from sqlalchemy.orm import Session
from app.models import Project, Score, Judge, RubricCriterion, Track, Team


def calculate_raw_score(score: Score, criteria_weights: Dict[str, float]) -> float:
    # Use criteria weights or default 1.0
    w_sum = 0.0
    s_sum = 0.0

    scores_dict = {}
    if score.functionality is not None:
        scores_dict["functionality"] = score.functionality
    if score.quality is not None:
        scores_dict["quality"] = score.quality
    if score.innovation is not None:
        scores_dict["innovation"] = score.innovation

    for name, val in scores_dict.items():
        w = criteria_weights.get(name, 1.0)
        s_sum += val * w
        w_sum += w

    if w_sum == 0.0:
        return 0.0
    return s_sum / w_sum


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
    criteria_weights = get_criteria_weights(db, event_id)

    # 1. Fetch all scores
    query = db.query(Score)
    if event_id:
        query = query.join(Project).filter(Project.event_id == event_id)
    all_scores = query.all()

    if not all_scores:
        # Return all projects with 0 scores
        projects = db.query(Project).all()
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
                "rank": idx + 1,
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

    # 4. Fetch projects
    p_query = db.query(Project)
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
        })

    # Sort descending by normalized score, then raw score, then project_id
    results.sort(key=lambda x: (x["normalized_score"], x["raw_score"]), reverse=True)

    for rank, item in enumerate(results, start=1):
        item["rank"] = rank

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
