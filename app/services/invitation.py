"""
Invitation service.

Responsible for:
- Generating secure invitation tokens
- Persisting the hash
- Sending the invitation email

Business rules:
- Raw tokens are never stored or logged.
- Tokens expire after settings.invitation_token_expire_hours.
- One invitation token per user is created per call (existing
  pending tokens are not invalidated — Admin may re-invite).
"""

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import generate_invitation_token, hash_invitation_token
from app.models.user import User
from app.models.user_invitation import UserInvitationToken
from app.repositories.invitation import create_invitation_token
from app.services.email import send_email


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token generation
# ---------------------------------------------------------------------------


def create_invitation_for_user(
    db: Session,
    user: User,
) -> str:
    """
    Generate a secure invitation token for the given user.

    Returns the **raw** token (for inclusion in the activation URL).
    Only the SHA-256 hash is stored in the database.

    The caller is responsible for committing the transaction.
    """
    raw_token = generate_invitation_token()
    token_hash = hash_invitation_token(raw_token)

    expires_at = datetime.now(timezone.utc) + timedelta(
        hours=settings.invitation_token_expire_hours
    )

    invitation = UserInvitationToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires_at,
    )

    create_invitation_token(db, invitation)

    return raw_token


def replace_invitation_for_user(
    db: Session,
    user: User,
) -> str:
    """Invalidate outstanding links and create one replacement invitation."""
    now = datetime.now(timezone.utc)
    outstanding = db.scalars(
        select(UserInvitationToken).where(
            UserInvitationToken.user_id == user.id,
            UserInvitationToken.used_at.is_(None),
        )
    ).all()
    for invitation in outstanding:
        invitation.used_at = now

    return create_invitation_for_user(db, user)


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


def send_invitation_email(
    *,
    user: User,
    raw_token: str,
) -> None:
    """
    Send the account activation email to the invited Fellow.

    The activation URL is constructed from the frontend base URL and the
    raw token.  The raw token is only ever present in this function
    and is not logged.
    """
    activation_url = (
        f"{settings.frontend_base_url}/activate?token={raw_token}"
    )

    subject = "You've been invited to DegreeLabs DLIF as a Fellow"

    text_body = f"""\
Hi {user.first_name},

Your DegreeLabs Impact Fellowship account has been created.

Activate your account and create your password by visiting:

  {activation_url}

This link expires in {settings.invitation_token_expire_hours} hours.

If you weren't expecting this invitation, please contact your programme administrator.

The DegreeLabs Team
"""

    logo_url = settings.email_logo_url
    if not logo_url:
        if settings.frontend_base_url.startswith("https://"):
            logo_url = f"{settings.frontend_base_url.rstrip('/')}/degreelabs-logo.png"
        else:
            logo_url = "https://framerusercontent.com/images/wQtZcQz0JelTmeZro2xPdEUWPwI.png"

    html_body = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>DegreeLabs DLIF Fellow Invitation</title>
</head>
<body style="font-family:sans-serif;color:#1a1a1a;background:#ffffff;margin:0;padding:0;">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr>
      <td align="center" style="padding:48px 16px;">
        <table width="560" cellpadding="0" cellspacing="0"
               style="background:#ffffff;border:1px solid #e5e5e5;border-radius:8px;overflow:hidden;">
          <!-- Header -->
          <tr>
            <td style="background:#ffffff;padding:32px 40px 24px;border-bottom:1px solid #f3f4f6;">
              <img src="{logo_url}"
                   alt="DegreeLabs"
                   width="150"
                   height="32"
                   style="display:block;width:150px;max-width:150px;height:auto;border:0;outline:none;text-decoration:none;" />
            </td>
          </tr>
          <!-- Body -->
          <tr>
            <td style="padding:40px 40px 32px;">
              <h1 style="margin:0 0 16px;font-size:24px;font-weight:700;color:#0f172a;">
                Welcome to DLIF, Fellow {user.first_name}!
              </h1>
              <p style="margin:0 0 24px;font-size:15px;line-height:1.6;color:#374151;">
                Your DegreeLabs Impact Fellowship account has been created.
                To get started, activate your account and create your password.
              </p>
              <!-- CTA -->
              <table cellpadding="0" cellspacing="0">
                <tr>
                  <td style="border-radius:6px;background:#2563eb;">
                    <a href="{activation_url}"
                       style="display:inline-block;padding:14px 28px;font-size:15px;
                              font-weight:600;color:#ffffff;text-decoration:none;
                              border-radius:6px;">
                      Activate Account
                    </a>
                  </td>
                </tr>
              </table>
              <p style="margin:24px 0 0;font-size:13px;color:#6b7280;">
                This link expires in {settings.invitation_token_expire_hours} hours.
                If you weren't expecting this invitation, please ignore this email.
              </p>
            </td>
          </tr>
          <!-- Footer -->
          <tr>
            <td style="padding:24px 40px;border-top:1px solid #f3f4f6;">
              <p style="margin:0;font-size:12px;color:#9ca3af;">
                DegreeLabs &middot; Questions? Contact your programme administrator.
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""

    send_email(
        to_address=user.email,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )
