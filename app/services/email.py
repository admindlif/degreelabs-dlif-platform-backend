import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol

from app.core.config import settings


logger = logging.getLogger(__name__)


class EmailBackend(Protocol):
    def send(
        self,
        *,
        to_address: str,
        subject: str,
        html_body: str,
        text_body: str,
    ) -> None:
        ...


class LoggingEmailBackend:
    def send(
        self,
        *,
        to_address: str,
        subject: str,
        html_body: str,
        text_body: str,
    ) -> None:
        logger.info(
            "Email suppressed by logging backend; recipient and body omitted "
            "(subject=%r)",
            subject,
        )


class SMTPEmailBackend:
    def send(
        self,
        *,
        to_address: str,
        subject: str,
        html_body: str,
        text_body: str,
    ) -> None:

        if not settings.smtp_username:
            raise RuntimeError(
                "SMTP_USERNAME is not configured."
            )

        if not settings.smtp_password:
            raise RuntimeError(
                "SMTP_PASSWORD is not configured."
            )

        message = EmailMessage()

        message["Subject"] = subject

        message["From"] = (
            f"{settings.email_from_name} "
            f"<{settings.email_from_address}>"
        )

        message["To"] = to_address

        message.set_content(text_body)

        message.add_alternative(
            html_body,
            subtype="html",
        )

        try:
            with smtplib.SMTP(
                settings.smtp_host,
                settings.smtp_port,
                timeout=30,
            ) as smtp:

                smtp.ehlo()

                if settings.smtp_use_tls:
                    smtp.starttls()
                    smtp.ehlo()

                smtp.login(
                    settings.smtp_username,
                    settings.smtp_password,
                )

                smtp.send_message(message)

            logger.info(
                "Email successfully accepted by the SMTP server; recipient omitted",
            )

        except Exception:
            logger.exception(
                "SMTP email delivery failed; recipient omitted",
            )
            raise


def _get_backend() -> EmailBackend:

    backend = settings.email_backend.strip().lower()

    if backend == "smtp":
        return SMTPEmailBackend()

    if backend == "logging":
        return LoggingEmailBackend()

    raise RuntimeError(
        f"Unsupported EMAIL_BACKEND={settings.email_backend!r}"
    )


def send_email(
    *,
    to_address: str,
    subject: str,
    html_body: str,
    text_body: str,
) -> None:

    backend = _get_backend()

    backend.send(
        to_address=to_address,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )
