import hashlib
import html
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from app.models import Project, Event, Team


def compute_certificate_fingerprint(
    event_id: str,
    project_id: str,
    recipient_name: str,
    award_title: str,
) -> str:
    """Computes a deterministic cryptographic SHA-256 fingerprint for a certificate."""
    raw = f"CERT:{event_id}:{project_id}:{recipient_name}:{award_title}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_certificate_svg(
    event_name: str,
    project_title: str,
    recipient_name: str,
    award_title: str,
    date_str: str,
    fingerprint: str,
) -> str:
    """
    Generates a standalone, dependency-free vector SVG certificate.
    Uses standard system typography and offline-safe inline styling.
    """
    safe_event = html.escape(event_name)
    safe_project = html.escape(project_title)
    safe_recipient = html.escape(recipient_name)
    safe_award = html.escape(award_title)
    safe_date = html.escape(date_str)
    fp_display = f"{fingerprint[:16]}...{fingerprint[-16:]}"

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 700" width="1000" height="700">
  <defs>
    <linearGradient id="bgGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#ffffff"/>
      <stop offset="50%" stop-color="#f8fafc"/>
      <stop offset="100%" stop-color="#f1f5f9"/>
    </linearGradient>
    <linearGradient id="goldGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#d97706"/>
      <stop offset="50%" stop-color="#f59e0b"/>
      <stop offset="100%" stop-color="#b45309"/>
    </linearGradient>
    <linearGradient id="indigoGrad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#4f46e5"/>
      <stop offset="100%" stop-color="#7c3aed"/>
    </linearGradient>
    <filter id="cardShadow" x="-5%" y="-5%" width="110%" height="110%">
      <feDropShadow dx="0" dy="8" stdDeviation="16" flood-color="#0f172a" flood-opacity="0.08"/>
    </filter>
  </defs>

  <style>
    .serif {{ font-family: Georgia, 'Times New Roman', serif; }}
    .sans {{ font-family: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif; }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }}
  </style>

  <!-- Background Canvas -->
  <rect width="1000" height="700" fill="#0f172a"/>
  
  <!-- Certificate Sheet -->
  <rect x="30" y="30" width="940" height="640" rx="12" fill="url(#bgGrad)" stroke="#e2e8f0" stroke-width="2" filter="url(#cardShadow)"/>
  
  <!-- Outer Double Border -->
  <rect x="46" y="46" width="908" height="608" rx="8" fill="none" stroke="#cbd5e1" stroke-width="1.5"/>
  <rect x="52" y="52" width="896" height="596" rx="6" fill="none" stroke="url(#indigoGrad)" stroke-width="3"/>
  <rect x="58" y="58" width="884" height="584" rx="4" fill="none" stroke="#e2e8f0" stroke-width="1"/>

  <!-- Corner Flourishes -->
  <path d="M 68 88 L 88 68 M 68 98 L 98 68 M 68 108 L 108 68" stroke="#cbd5e1" stroke-width="1.5" stroke-linecap="round"/>
  <path d="M 932 88 L 912 68 M 932 98 L 902 68 M 932 108 L 892 68" stroke="#cbd5e1" stroke-width="1.5" stroke-linecap="round"/>
  <path d="M 68 612 L 88 632 M 68 602 L 98 632 M 68 592 L 108 632" stroke="#cbd5e1" stroke-width="1.5" stroke-linecap="round"/>
  <path d="M 932 612 L 912 632 M 932 602 L 902 632 M 932 592 L 892 632" stroke="#cbd5e1" stroke-width="1.5" stroke-linecap="round"/>

  <!-- Organization Brand & Watermark Badge -->
  <g transform="translate(500, 115)">
    <circle cx="0" cy="0" r="28" fill="#e0e7ff"/>
    <path d="M -12 -8 L 0 -18 L 12 -8 L 12 12 L -12 12 Z" fill="#4f46e5"/>
    <path d="M -6 2 L 0 -4 L 6 2 L 6 10 L -6 10 Z" fill="#ffffff"/>
  </g>

  <!-- Title & Supertitle -->
  <text x="500" y="170" text-anchor="middle" class="sans" font-size="13" font-weight="700" letter-spacing="4" fill="#6366f1">JUDGEFORGE VERIFIED CREDENTIAL</text>
  <text x="500" y="215" text-anchor="middle" class="serif" font-size="34" font-weight="bold" fill="#0f172a">Certificate of Achievement</text>

  <!-- Presentation Clause -->
  <text x="500" y="255" text-anchor="middle" class="sans" font-size="15" fill="#64748b">This official certificate is proudly presented to</text>

  <!-- Recipient -->
  <text x="500" y="305" text-anchor="middle" class="serif" font-size="32" font-weight="bold" fill="#1e1b4b">{safe_recipient}</text>
  <line x1="320" y1="322" x2="680" y2="322" stroke="#e2e8f0" stroke-width="1.5"/>

  <!-- Achievement Description -->
  <text x="500" y="358" text-anchor="middle" class="sans" font-size="15" fill="#475569">
    in recognition of outstanding execution and achievement for project
  </text>
  <text x="500" y="392" text-anchor="middle" class="sans" font-size="20" font-weight="700" fill="#4338ca">"{safe_project}"</text>

  <text x="500" y="425" text-anchor="middle" class="sans" font-size="16" font-weight="600" fill="#059669">
    Honored with: {safe_award}
  </text>

  <text x="500" y="455" text-anchor="middle" class="sans" font-size="14" fill="#64748b">
    at {safe_event}
  </text>

  <!-- Decorative Medallion / Ribbon -->
  <g transform="translate(500, 520)">
    <circle cx="0" cy="0" r="32" fill="url(#goldGrad)" stroke="#ffffff" stroke-width="2"/>
    <circle cx="0" cy="0" r="26" fill="none" stroke="#fef3c7" stroke-width="1" stroke-dasharray="3,2"/>
    <!-- Star icon -->
    <path d="M 0 -14 L 4 -4 L 14 -4 L 6 3 L 9 13 L 0 7 L -9 13 L -6 3 L -14 -4 L -4 -4 Z" fill="#ffffff"/>
  </g>

  <!-- Footer Info & Signatures -->
  <g transform="translate(180, 560)">
    <line x1="0" y1="0" x2="180" y2="0" stroke="#94a3b8" stroke-width="1.5"/>
    <text x="90" y="20" text-anchor="middle" class="sans" font-size="13" font-weight="600" fill="#334155">{safe_date}</text>
    <text x="90" y="35" text-anchor="middle" class="sans" font-size="11" fill="#94a3b8">Date of Issue</text>
  </g>

  <g transform="translate(640, 560)">
    <line x1="0" y1="0" x2="180" y2="0" stroke="#94a3b8" stroke-width="1.5"/>
    <text x="90" y="20" text-anchor="middle" class="serif" font-size="15" font-weight="bold" fill="#334155">JudgeForge Engine</text>
    <text x="90" y="35" text-anchor="middle" class="sans" font-size="11" fill="#94a3b8">Cryptographic Issuer</text>
  </g>

  <!-- Security Verification Footer Bar -->
  <rect x="70" y="605" width="860" height="30" rx="4" fill="#f8fafc" stroke="#e2e8f0" stroke-width="1"/>
  <text x="85" y="624" class="sans" font-size="10.5" font-weight="600" fill="#475569">DIGITAL VERIFICATION FINGERPRINT:</text>
  <text x="320" y="624" class="mono" font-size="10" font-weight="bold" fill="#6366f1">{fp_display}</text>
  <text x="915" y="624" text-anchor="end" class="sans" font-size="10" fill="#94a3b8">SHA-256 Tamper-Evident</text>
</svg>"""


def verify_certificate_digest(db: Session, fingerprint: str) -> Dict[str, Any]:
    """
    Verifies whether a given SHA-256 fingerprint matches any valid project certificate.
    """
    clean_fp = fingerprint.strip().lower()
    projects = db.query(Project).all()

    for proj in projects:
        event = db.query(Event).filter(Event.id == proj.event_id).first()
        event_name = event.name if event else "Hackathon Event"
        team_name = proj.team.name if proj.team else "Independent Creator"

        possible_awards = [
            "Official Participant",
            "1st Place Winner",
            "2nd Place Winner",
            "3rd Place Winner",
            "Track Winner",
            "Honorable Mention",
            "Best Overall Project",
        ]

        for award in possible_awards:
            fp = compute_certificate_fingerprint(
                event_id=proj.event_id,
                project_id=proj.id,
                recipient_name=team_name,
                award_title=award,
            )
            if fp.lower() == clean_fp:
                return {
                    "valid": True,
                    "event_id": proj.event_id,
                    "event_name": event_name,
                    "project_id": proj.id,
                    "project_title": proj.title,
                    "recipient_name": team_name,
                    "award_title": award,
                    "fingerprint": fp,
                    "message": "Certificate fingerprint is authentic and verified against the local registry.",
                }

    return {
        "valid": False,
        "fingerprint": clean_fp,
        "message": "Certificate fingerprint could not be found or verified in the local registry.",
    }
