import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Project, Comment, AuditLog, User
from app.auth import get_current_user_optional, require_organizer
from app.rate_limiter import check_rate_limit, get_client_ip

router = APIRouter(prefix="/api/projects/{project_id}/comments", tags=["comments"])


class CommentCreateRequest(BaseModel):
    author_name: Optional[str] = Field(None, max_length=100)
    content: str = Field(..., min_length=1, max_length=2000)


@router.get("")
def list_comments(
    project_id: str,
    db: Session = Depends(get_db),
):
    """Retrieve public comments for a project (user IDs redacted for privacy)."""
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    comments = db.scalars(
        select(Comment)
        .where(Comment.project_id == project_id, Comment.is_flagged == 0)
        .order_by(Comment.created_at.asc())
    ).all()

    return [
        {
            "id": c.id,
            "project_id": c.project_id,
            "author_name": c.author_name,
            "content": c.content,
            "created_at": c.created_at.isoformat(),
        }
        for c in comments
    ]


@router.post("", status_code=201)
def add_comment(
    project_id: str,
    payload: CommentCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """Add a plain-text comment to a project with rate limiting and audit logging."""
    client_ip = get_client_ip(request)

    # Rate limiting: max 10 comments per 5 minutes per IP
    check_rate_limit(db, f"comment:{client_ip}", max_requests=10, window_seconds=300)

    project = db.get(Project, project_id)
    if not project or project.status != "submitted":
        raise HTTPException(status_code=404, detail="Submitted project not found")

    content_clean = payload.content.strip()
    if not content_clean:
        raise HTTPException(status_code=422, detail="Comment content cannot be blank")

    if user:
        author_name = user.name
        user_id = user.id
    else:
        author_name = (payload.author_name or "").strip() or "Community Contributor"
        user_id = None

    comment = Comment(
        project_id=project_id,
        user_id=user_id,
        author_name=author_name,
        content=content_clean,
        is_flagged=0,
    )
    db.add(comment)

    db.add(AuditLog(
        user_id=user_id,
        action="COMMENT_ADDED",
        target_type="project",
        target_id=project_id,
        details=f"Author: {author_name}, IP: {client_ip}",
    ))
    db.commit()
    db.refresh(comment)

    return {
        "id": comment.id,
        "project_id": comment.project_id,
        "author_name": comment.author_name,
        "content": comment.content,
        "created_at": comment.created_at.isoformat(),
    }


# Standalone delete route
comments_admin_router = APIRouter(prefix="/api/comments/{comment_id}", tags=["comments"])


@comments_admin_router.delete("")
def delete_comment(
    comment_id: int,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """Moderation deletion of a comment (Author or Organizer/Admin)."""
    comment = db.get(Comment, comment_id)
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    if not user:
        raise HTTPException(status_code=401, detail="Authentication required to delete comments")

    is_author = comment.user_id and comment.user_id == user.id
    is_moderator = user.role in ["organizer", "admin"]

    if not (is_author or is_moderator):
        raise HTTPException(status_code=403, detail="Not authorized to delete this comment")

    project_id = comment.project_id
    db.delete(comment)
    db.add(AuditLog(
        user_id=user.id,
        action="COMMENT_DELETED",
        target_type="comment",
        target_id=str(comment_id),
        details=f"Deleted from project {project_id} by {user.role} {user.email}",
    ))
    db.commit()

    return {"status": "deleted", "comment_id": comment_id}
