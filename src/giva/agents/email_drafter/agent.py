"""Email Drafter agent: generates email drafts from user intent.

Uses the assistant model for high-quality prose. Reads email history
from the DB to match tone and include relevant context.
"""

from __future__ import annotations

from giva.agents.base import AgentManifest, AgentResult, BaseAgent
from giva.agents.email_drafter.prompts import DRAFT_SYSTEM, DRAFT_USER
from giva.config import GivaConfig
from giva.db.store import Store


class EmailDrafterAgent(BaseAgent):
    """Drafts emails using the assistant model with context from email history."""

    def __init__(self):
        super().__init__(AgentManifest(
            agent_id="email_drafter",
            name="Email Drafter",
            description=(
                "Drafts professional emails based on your instructions. "
                "Can reply to existing threads or compose new messages. "
                "Uses your email history to match tone and context."
            ),
            examples=[
                "Draft a reply to Sarah's email about the project deadline",
                "Write an email to John asking about the quarterly report",
                "Compose a follow-up email to the client about the proposal",
                "Help me write a thank-you email to the team",
                "Draft an email declining the meeting invitation",
            ],
            model_tier="assistant",
            supports_streaming=False,
            requires_confirmation=True,
            version="0.1.0",
        ))

    def execute(
        self,
        query: str,
        context: dict,
        store: Store,
        config: GivaConfig,
    ) -> AgentResult:
        """Generate an email draft."""
        sender_context = self._build_sender_context(store)
        thread_context = self._build_thread_context(query, store)

        system = DRAFT_SYSTEM.format(
            sender_context=sender_context,
            thread_context=thread_context,
        )
        user_prompt = DRAFT_USER.format(query=query)

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ]

        raw = self._llm_generate(config, messages, max_tokens=1024, temp=0.7)
        result = self._parse_json_safe(raw)

        if result is None:
            return AgentResult(
                success=True,
                output=f"Here's a draft email:\n\n{raw}",
                artifacts={"raw_draft": raw},
            )

        to = result.get("to", "unknown recipient")
        subject = result.get("subject", "No subject")
        body = result.get("body", "")

        output = (
            f"Here's a draft email:\n\n"
            f"**To:** {to}\n"
            f"**Subject:** {subject}\n\n"
            f"{body}"
        )

        return AgentResult(
            success=True,
            output=output,
            actions=[{
                "type": "email_draft_created",
                "to": to,
                "subject": subject,
            }],
            artifacts={
                "to": to,
                "subject": subject,
                "body": body,
                "reply_to": result.get("reply_to_message_id"),
            },
        )

    def _build_sender_context(self, store: Store) -> str:
        """Build context about the sender from their profile."""
        profile = store.get_profile()
        if not profile:
            return "Sender context: Unknown user."

        parts = []
        if profile.display_name:
            parts.append(f"Sender name: {profile.display_name}")
        if profile.email_address:
            parts.append(f"Sender email: {profile.email_address}")

        pd = profile.profile_data
        if pd.get("job_title"):
            parts.append(f"Title: {pd['job_title']}")
        if pd.get("company"):
            parts.append(f"Company: {pd['company']}")
        if pd.get("communication_style"):
            parts.append(f"Communication style: {pd['communication_style']}")

        return "Sender context:\n" + "\n".join(parts) if parts else ""

    def _build_thread_context(self, query: str, store: Store) -> str:
        """Find relevant email threads for context."""
        try:
            emails = store.search_emails(query, limit=3)
            if not emails:
                return "No relevant email threads found."

            lines = ["Relevant email threads:"]
            for e in emails:
                date_str = (
                    e.date_sent.strftime("%b %d, %Y")
                    if e.date_sent
                    else "unknown"
                )
                lines.append(f"\n- From: {e.from_name or e.from_addr}")
                lines.append(f"  Subject: {e.subject}")
                lines.append(f"  Date: {date_str}")
                if e.body_plain:
                    body = e.body_plain[:300]
                    if len(e.body_plain) > 300:
                        body += "..."
                    lines.append(f"  Body: {body}")

            return "\n".join(lines)
        except Exception:
            return "No relevant email threads found."
