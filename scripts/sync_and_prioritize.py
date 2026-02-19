"""One-shot script: sync last month of emails+events, then ask for today's top 10 tasks."""

import sys
import time
sys.path.insert(0, "src")

from rich.console import Console
from rich.markdown import Markdown
from rich.live import Live

from giva.config import load_config
from giva.db.store import Store
from giva.sync.mail import sync_mail_jxa
from giva.sync.calendar import sync_calendar
from giva.llm import engine
from giva.llm.prompts import build_system_prompt, format_email_context, format_event_context

console = Console()

def main():
    config = load_config()
    config.data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(config.db_path)

    # --- Phase 1: Sync emails (1000 recent, with LLM filter) ---
    console.print("\n[bold blue]Phase 1: Syncing emails (last ~1000, with LLM filter)...[/bold blue]")
    t0 = time.time()

    def on_progress(synced, filtered, total):
        elapsed = time.time() - t0
        console.print(
            f"\r  [{elapsed:.0f}s] {synced} kept, {filtered} filtered / {total} scanned...",
            end="", style="dim",
        )

    synced, filtered = sync_mail_jxa(
        store,
        mailboxes=["INBOX"],
        batch_size=1000,
        on_progress=on_progress,
        config=config,
    )
    elapsed = time.time() - t0
    console.print(
        f"\n  Done in {elapsed:.0f}s: {synced} emails synced, {filtered} filtered out",
        style="bold green",
    )

    # --- Phase 2: Sync calendar (last 30 days + next 30 days) ---
    console.print("\n[bold blue]Phase 2: Syncing calendar (last 30 days + next 30 days)...[/bold blue]")
    t1 = time.time()
    cal_count = sync_calendar(store, past_days=30, future_days=30)
    console.print(
        f"  Done in {time.time() - t1:.0f}s: {cal_count} events synced",
        style="bold green",
    )

    # --- Phase 3: Ask assistant for today's priorities ---
    console.print("\n[bold blue]Phase 3: Asking assistant for today's top 10 tasks...[/bold blue]")

    # Gather context
    from datetime import datetime, timedelta
    recent_emails = store.get_recent_emails(limit=30)
    upcoming_events = store.get_upcoming_events(days=7)
    past_events = store.get_events_range(
        datetime.now() - timedelta(days=7), datetime.now()
    )

    context_parts = []
    if recent_emails:
        context_parts.append("Recent emails:\n" + format_email_context(recent_emails))
    if upcoming_events:
        context_parts.append("Upcoming events (next 7 days):\n" + format_event_context(upcoming_events))
    if past_events:
        context_parts.append("Recent past events (last 7 days):\n" + format_event_context(past_events))

    context = "\n\n".join(context_parts)

    system = build_system_prompt()
    query = (
        "Based on my recent emails and calendar, identify and prioritize the top 10 tasks "
        "I should focus on today. For each task, explain why it's important and what action "
        "I should take. Consider: upcoming deadlines, unanswered emails that need responses, "
        "meetings I need to prepare for, and follow-ups I might have missed."
    )

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {query}"},
    ]

    console.print()
    full_text = []
    t2 = time.time()
    with Live(console=console, refresh_per_second=8) as live:
        for token in engine.stream_generate(messages, config.llm):
            full_text.append(token)
            live.update(Markdown("".join(full_text)))

    console.print(f"\n[dim]Response generated in {time.time() - t2:.0f}s[/dim]")

    # Stats
    stats = store.get_stats()
    console.print(f"\n[dim]DB: {stats['emails']} emails, {stats['events']} events[/dim]")


if __name__ == "__main__":
    main()
