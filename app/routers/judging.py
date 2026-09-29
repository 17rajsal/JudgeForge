import datetime
import json
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Score, Judge, Project, RubricCriterion, User, AuditLog, Track
from app.auth import get_current_user, get_current_user_optional

router = APIRouter(tags=["Judging"])
from app.templating import templates


from app.services.scoring import calculate_raw_score, CRITERIA_GUIDANCE


class ScoreSubmissionSchema(BaseModel):
    project_id: str
    functionality: Optional[int] = Field(None, ge=1, le=5)
    quality: Optional[int] = Field(None, ge=1, le=5)
    innovation: Optional[int] = Field(None, ge=1, le=5)
    criteria: Optional[Dict[str, Any]] = None
    criteria_json: Optional[str] = None
    comment: Optional[str] = ""


@router.get("/api/judge/scores")
def list_judge_scores(
    judge: Optional[str] = Query(None, description="Optional judge ID to filter scores"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 1. Enforce RBAC: participant or visitor is blocked
    if current_user.role not in ("judge", "organizer", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Only judges and organizers can access judging scores",
        )

    # 2. Enforce Judge Peer Isolation
    target_judge_id: Optional[str] = None

    if current_user.role == "judge":
        judge_profile = current_user.judge_profile
        if not judge_profile:
            # Fallback check if user ID matches a judge record
            judge_record = db.query(Judge).filter(Judge.email == current_user.email).first()
            if judge_record:
                judge_profile = judge_record
            else:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="No associated judge profile found for this account",
                )

        # Critical security check: A judge can ONLY see their own scores
        if judge and judge != judge_profile.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: Judges cannot inspect peer scores (attempted access to {judge})",
            )

        target_judge_id = judge_profile.id
    else:
        # Organizer can inspect any specific judge's scores or all
        target_judge_id = judge

    query = db.query(Score)
    if target_judge_id:
        query = query.filter(Score.judge_id == target_judge_id)

    scores = query.all()
    results = []
    for sc in scores:
        crit_dict = {}
        if sc.criteria_json:
            try:
                crit_dict = json.loads(sc.criteria_json)
            except Exception:
                crit_dict = {}
        if not crit_dict:
            crit_dict = {
                "functionality": sc.functionality,
                "quality": sc.quality,
                "innovation": sc.innovation,
            }

        results.append({
            "id": sc.id,
            "judge_id": sc.judge_id,
            "project_id": sc.project_id,
            "project_title": sc.project.title if sc.project else "",
            "criteria": crit_dict,
            "functionality": sc.functionality,
            "quality": sc.quality,
            "innovation": sc.innovation,
            "comment": sc.comment or "",
            "submitted_at": sc.submitted_at.isoformat() if sc.submitted_at else None,
        })

    return results


@router.post("/api/judge/scores")
def submit_or_update_score(
    payload: ScoreSubmissionSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in ("judge", "organizer", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Only judges and organizers can submit scores",
        )

    # Resolve judge ID
    if current_user.role == "judge":
        judge_profile = current_user.judge_profile or db.query(Judge).filter(Judge.email == current_user.email).first()
        if not judge_profile:
            raise HTTPException(status_code=403, detail="No judge profile associated with account")
        judge_id = judge_profile.id
    else:
        first_judge = db.query(Judge).first()
        judge_id = first_judge.id if first_judge else "jdg_01"
        judge_profile = first_judge

    project = db.query(Project).filter(Project.id == payload.project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.status != "submitted":
        raise HTTPException(status_code=400, detail="Cannot score an unsubmitted project draft")

    # If caller is a judge, ensure they are assigned to this project's track within this event
    if current_user.role == "judge":
        if judge_profile and judge_profile.tracks:
            event_assigned_tracks = {t.id for t in judge_profile.tracks if t.event_id == project.event_id}
            if event_assigned_tracks and project.track_id not in event_assigned_tracks:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Forbidden: Judge is not assigned to evaluate this project's track",
                )

    # Check event criteria
    event_criteria = db.query(RubricCriterion).filter(RubricCriterion.event_id == project.event_id).all()
    score_dict: Dict[str, int] = {}

    input_criteria = payload.criteria
    if input_criteria is None and payload.criteria_json:
        try:
            parsed = json.loads(payload.criteria_json)
            if isinstance(parsed, dict):
                input_criteria = parsed
        except Exception:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid criteria_json")

    if input_criteria is not None:
        # Dynamic criteria submission: require all configured event criteria
        target_criteria = event_criteria if event_criteria else [
            RubricCriterion(name="functionality", label="Functionality"),
            RubricCriterion(name="quality", label="Quality"),
            RubricCriterion(name="innovation", label="Innovation"),
        ]
        for c in target_criteria:
            val = input_criteria.get(c.name)
            if val is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Missing score for criterion: {c.label or c.name}",
                )
            try:
                val_int = int(val)
            except (ValueError, TypeError):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Score for {c.name} must be an integer between 1 and 5",
                )
            if not (1 <= val_int <= 5):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Score for {c.name} must be between 1 and 5",
                )
            score_dict[c.name] = val_int
    else:
        # Legacy submission path
        if payload.functionality is None or payload.quality is None or payload.innovation is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Scores for functionality, quality, and innovation must be provided between 1 and 5",
            )
        for name, val in [("functionality", payload.functionality), ("quality", payload.quality), ("innovation", payload.innovation)]:
            if not (1 <= val <= 5):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Score for {name} must be between 1 and 5",
                )
            score_dict[name] = val

        for c in event_criteria:
            if c.name not in score_dict:
                score_dict[c.name] = 3

    func_score = score_dict.get("functionality")
    qual_score = score_dict.get("quality")
    innov_score = score_dict.get("innovation")

    # Upsert score
    score = (
        db.query(Score)
        .filter(Score.judge_id == judge_id, Score.project_id == payload.project_id)
        .first()
    )
    is_update = score is not None
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    if not score:
        score = Score(
            judge_id=judge_id,
            project_id=payload.project_id,
            functionality=func_score,
            quality=qual_score,
            innovation=innov_score,
            criteria_json=json.dumps(score_dict),
            comment=payload.comment,
            submitted_at=now_utc,
        )
        db.add(score)
    else:
        score.functionality = func_score
        score.quality = qual_score
        score.innovation = innov_score
        score.criteria_json = json.dumps(score_dict)
        score.comment = payload.comment
        score.submitted_at = now_utc

    db.add(
        AuditLog(
            user_id=current_user.id,
            action="UPDATE_SCORE" if is_update else "CREATE_SCORE",
            target_type="Score",
            target_id=f"{judge_id}:{payload.project_id}",
            details=f"Project: {payload.project_id}, Judge: {judge_id}, Criteria: {score_dict}",
        )
    )

    db.commit()
    db.refresh(score)

    # Append to cryptographic tamper-evident record chain
    try:
        from app.services.verifiable_records import append_verifiable_record
        append_verifiable_record(db, score)
    except Exception:
        pass

    # Dispatch outbound webhook
    try:
        from app.services.webhooks import dispatch_webhook
        if project:
            dispatch_webhook(
                db=db,
                event_id=project.event_id,
                event_type="score.submitted",
                payload={
                    "score_id": score.id,
                    "judge_id": score.judge_id,
                    "project_id": score.project_id,
                    "functionality": score.functionality,
                    "quality": score.quality,
                    "innovation": score.innovation,
                    "criteria": score_dict,
                    "submitted_at": score.submitted_at.isoformat() if score.submitted_at else None,
                },
            )
    except Exception:
        pass

    return {
        "message": "Score saved successfully",
        "id": score.id,
        "project_id": score.project_id,
        "judge_id": score.judge_id,
        "criteria": score_dict,
        "comment": score.comment,
    }


@router.get("/judge", response_class=HTMLResponse)
def view_judge_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    if not user or user.role not in ("judge", "organizer", "admin"):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"user": user, "error": "Please log in as a judge or organizer to view this page."},
        )

    judge = user.judge_profile or db.query(Judge).filter(Judge.email == user.email).first()
    assigned_tracks = judge.tracks if judge else []
    assigned_track_ids = [t.id for t in assigned_tracks]

    # Find projects in assigned tracks (submitted only)
    if assigned_track_ids:
        projects = db.query(Project).filter(Project.status == "submitted", Project.track_id.in_(assigned_track_ids)).all()
    else:
        projects = db.query(Project).filter(Project.status == "submitted").all()

    # Scores by this judge
    judge_id = judge.id if judge else "jdg_01"
    my_scores = {s.project_id: s for s in db.query(Score).filter(Score.judge_id == judge_id).all()}

    event_ids = {t.event_id for t in assigned_tracks if t.event_id}
    if event_ids:
        criteria = db.query(RubricCriterion).filter(RubricCriterion.event_id.in_(event_ids)).all()
    else:
        criteria = db.query(RubricCriterion).all()

    my_raw_scores = {}
    for p in projects:
        sc = my_scores.get(p.id)
        if sc:
            weights = {c.name: c.weight for c in criteria if c.event_id == p.event_id}
            if not weights:
                weights = {c.name: c.weight for c in criteria}
            raw = calculate_raw_score(sc, weights)
            my_raw_scores[p.id] = f"{raw:.2f}"

    return templates.TemplateResponse(
        request=request,
        name="judge_dashboard.html",
        context={
            "user": user,
            "judge": judge,
            "assigned_tracks": assigned_tracks,
            "projects": projects,
            "my_scores": my_scores,
            "my_raw_scores": my_raw_scores,
            "criteria": criteria,
        },
    )


@router.get("/judge/projects/{project_id}", response_class=HTMLResponse)
def view_judge_review_project(
    project_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(f"/login?next=/judge/projects/{project_id}", status_code=303)

    if user.role not in ("judge", "organizer", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Only judges and organizers can access this review workspace",
        )

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.status != "submitted":
        raise HTTPException(status_code=400, detail="Cannot review an unsubmitted project draft")

    if user.role == "judge":
        judge = user.judge_profile or db.query(Judge).filter(Judge.email == user.email).first()
        if not judge:
            raise HTTPException(status_code=403, detail="No judge profile associated with account")
        judge_id = judge.id
        if judge.tracks:
            event_assigned_tracks = {t.id for t in judge.tracks if t.event_id == project.event_id}
            if event_assigned_tracks and project.track_id not in event_assigned_tracks:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Forbidden: Judge is not assigned to evaluate this project's track",
                )
    else:
        first_judge = db.query(Judge).first()
        judge = first_judge
        judge_id = first_judge.id if first_judge else "jdg_01"

    # Criteria for event
    criteria = db.query(RubricCriterion).filter(RubricCriterion.event_id == project.event_id).order_by(RubricCriterion.id.asc()).all()
    if not criteria:
        criteria = db.query(RubricCriterion).all()

    total_weight = sum(c.weight for c in criteria) if criteria else 1.0

    criteria_data = []
    for c in criteria:
        pct = round((c.weight / total_weight) * 100) if total_weight > 0 else 0
        guidance = CRITERIA_GUIDANCE.get(c.name, {
            "question": c.description or f"Evaluate {c.label}",
            "options": [
                {"score": 1, "label": "Poor", "desc": "Far below expectations"},
                {"score": 2, "label": "Fair", "desc": "Below expectations with major flaws"},
                {"score": 3, "label": "Good", "desc": "Meets expectations"},
                {"score": 4, "label": "Very Good", "desc": "Exceeds expectations"},
                {"score": 5, "label": "Excellent", "desc": "Outstanding quality"},
            ],
        })
        criteria_data.append({
            "criterion": c,
            "name": c.name,
            "label": c.label,
            "weight": c.weight,
            "pct_weight": pct,
            "guidance": guidance,
        })

    # Existing score
    existing_score = db.query(Score).filter(Score.judge_id == judge_id, Score.project_id == project.id).first()
    existing_criteria = {}
    if existing_score:
        if existing_score.criteria_json:
            try:
                parsed = json.loads(existing_score.criteria_json)
                if isinstance(parsed, dict):
                    existing_criteria = {k: int(v) for k, v in parsed.items() if isinstance(v, (int, float))}
            except Exception:
                pass
        if not existing_criteria:
            if existing_score.functionality is not None:
                existing_criteria["functionality"] = existing_score.functionality
            if existing_score.quality is not None:
                existing_criteria["quality"] = existing_score.quality
            if existing_score.innovation is not None:
                existing_criteria["innovation"] = existing_score.innovation

    existing_raw = (
        calculate_raw_score(existing_score, {c.name: c.weight for c in criteria})
        if existing_score
        else None
    )

    # Next project in queue
    q_proj = db.query(Project).filter(Project.event_id == project.event_id, Project.status == "submitted")
    if user.role == "judge" and judge and judge.tracks:
        assigned_tids = {t.id for t in judge.tracks if t.event_id == project.event_id}
        if assigned_tids:
            q_proj = q_proj.filter(Project.track_id.in_(assigned_tids))
    all_assigned_projects = q_proj.order_by(Project.id.asc()).all()

    all_scores = db.query(Score).filter(Score.judge_id == judge_id).all()
    scored_pids = {s.project_id for s in all_scores}

    other_unreviewed = [p for p in all_assigned_projects if p.id != project.id and p.id not in scored_pids]
    next_project = other_unreviewed[0] if other_unreviewed else None
    if not next_project:
        other_projects = [p for p in all_assigned_projects if p.id != project.id]
        next_project = other_projects[0] if other_projects else None

    completed_count = len([p for p in all_assigned_projects if p.id in scored_pids])

    return templates.TemplateResponse(
        request=request,
        name="judge_review.html",
        context={
            "user": user,
            "judge": judge,
            "project": project,
            "criteria_data": criteria_data,
            "criteria": criteria,
            "existing_score": existing_score,
            "existing_criteria": existing_criteria,
            "existing_raw": existing_raw,
            "next_project": next_project,
            "total_assigned": len(all_assigned_projects),
            "completed_count": completed_count,
            "remaining_count": max(0, len(all_assigned_projects) - completed_count),
        },
    )
