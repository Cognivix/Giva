"""Giva CLI - interactive REPL with slash commands and natural language queries."""

from __future__ import annotations

import logging
import sys
import threading

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from giva import __version__
from giva.config import GivaConfig, load_config
from giva.db.store import Store
from giva.intelligence.profile import update_profile, get_profile_summary
from giva.intelligence.proactive import get_suggestions
from giva.intelligence.queries import handle_query
from giva.intelligence.tasks import extract_tasks
from giva.sync.calendar import sync_calendar, request_eventkit_access, _eventkit_authorized
from giva.sync.mail import sync_mail_jxa

console = Console()
log = logging.getLogger(__name__)

# --- Voice state (module-level, toggled by /voice command) ---
_voice_enabled = False
_tts_engine = None
_stt_engine = None
_audio_player = None
_voice_lock = threading.Lock()  # Serializes TTS/STT model access


def _get_tts(config: GivaConfig):
    """Lazy-initialize TTS engine."""
    global _tts_engine
    if _tts_engine is None:
        from giva.audio.tts import TTSEngine

        _tts_engine = TTSEngine(config.voice)
    return _tts_engine


def _get_stt(config: GivaConfig):
    """Lazy-initialize STT engine."""
    global _stt_engine
    if _stt_engine is None:
        from giva.audio.stt import STTEngine

        _stt_engine = STTEngine(config.voice)
    return _stt_engine


def _get_player():
    """Lazy-initialize audio player."""
    global _audio_player
    if _audio_player is None:
        from giva.audio.player import AudioPlayer

        _audio_player = AudioPlayer()
    return _audio_player


def main():
    """Entry point for Giva CLI."""
    config = load_config()

    # Set up logging
    logging.basicConfig(
        level=getattr(logging, config.log_level),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler()],
    )

    # Ensure data directory exists
    config.data_dir.mkdir(parents=True, exist_ok=True)

    # Initialize store
    store = Store(config.db_path)

    # Print banner
    _print_banner(store, config)

    # Set up prompt session with persistent history
    history_path = config.data_dir / "history"
    session = PromptSession(history=FileHistory(str(history_path)))

    # REPL loop
    while True:
        try:
            user_input = session.prompt("giva> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nBye!", style="dim")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            _handle_command(user_input, store, config)
        else:
            _handle_query(user_input, store, config)


def _print_banner(store: Store, config: GivaConfig):
    """Print the startup banner with stats."""
    stats = store.get_stats()
    model_name = config.llm.model.split("/")[-1]

    # Include profile greeting if available
    profile = store.get_profile()
    greeting = ""
    if profile and profile.display_name:
        greeting = f" | Hello, {profile.display_name}"

    console.print()
    console.print(
        Panel(
            f"[bold]Giva[/bold] v{__version__} | Model: {model_name}{greeting}\n"
            f"DB: {stats['emails']} emails, {stats['events']} events | "
            f"{stats['pending_tasks']} pending tasks",
            title="Generative Intelligent Virtual Assistant",
            border_style="blue",
        )
    )

    # Show last sync times
    if stats["syncs"]:
        for s in stats["syncs"]:
            sync_time = s["last_sync"] or "never"
            console.print(
                f"  {s['source']}: last synced {sync_time} ({s['last_status']})",
                style="dim",
            )
    else:
        console.print("  No syncs yet. Run /sync to get started.", style="dim yellow")

    console.print()


def _handle_command(cmd: str, store: Store, config: GivaConfig):
    """Handle slash commands."""
    parts = cmd.split(maxsplit=1)
    command = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    if command in ("/quit", "/exit", "/q"):
        console.print("Bye!", style="dim")
        sys.exit(0)

    elif command == "/sync":
        _cmd_sync(store, config)

    elif command == "/status":
        _cmd_status(store, config)

    elif command == "/history":
        _cmd_history(store)

    elif command == "/clear":
        # Clear conversation history (not email/event data)
        with store._conn() as conn:
            conn.execute("DELETE FROM conversations")
        console.print("Conversation history cleared.", style="dim")

    elif command == "/tasks":
        _cmd_tasks(args, store, config)

    elif command == "/extract":
        _cmd_extract(store, config)

    elif command == "/suggest":
        _cmd_suggest(store, config)

    elif command == "/profile":
        _cmd_profile(store, config)

    elif command == "/onboard":
        _cmd_onboard(store, config)

    elif command == "/reset":
        _cmd_reset(store)

    elif command == "/goals":
        _cmd_goals(args, store, config)

    elif command == "/strategy":
        _cmd_strategy(args, store, config)

    elif command == "/plan":
        _cmd_plan(args, store, config)

    elif command == "/review":
        _cmd_review(store, config)

    elif command == "/voice":
        _cmd_voice(args, config)

    elif command == "/listen":
        _cmd_listen(store, config)

    elif command == "/setup":
        _cmd_setup()

    elif command == "/help":
        _cmd_help()

    else:
        console.print(f"Unknown command: {command}. Type /help for available commands.", style="red")


def _cmd_sync(store: Store, config: GivaConfig):
    """Run mail and calendar sync."""
    def on_mail_progress(synced, filtered, total):
        console.print(
            f"\r  Emails: {synced} kept, {filtered} filtered / {total} total...",
            end="", style="dim",
        )

    console.print("  Syncing emails (headers + LLM filter)...", style="dim")
    try:
        mail_synced, mail_filtered = sync_mail_jxa(
            store, config.mail.mailboxes, config.mail.batch_size,
            on_progress=on_mail_progress,
            config=config,
        )
        console.print(
            f"\r  Emails: {mail_synced} synced, {mail_filtered} filtered out            ",
            style="green",
        )
    except Exception as e:
        console.print(f"  Email sync error: {e}", style="red")

    with console.status("Syncing calendar..."):
        try:
            cal_count = sync_calendar(
                store,
                config.calendar.sync_window_past_days,
                config.calendar.sync_window_future_days,
            )
            console.print(f"  Calendar: {cal_count} events synced", style="green")
        except Exception as e:
            console.print(f"  Calendar sync error: {e}", style="red")

    # Rebuild user profile from latest data
    with console.status("Updating user profile..."):
        try:
            update_profile(store, config)
            console.print("  Profile updated", style="green")
        except Exception as e:
            console.print(f"  Profile update error: {e}", style="yellow")

    console.print("Sync complete.", style="bold green")

    # Auto-trigger onboarding if needed
    from giva.intelligence.onboarding import is_onboarding_needed

    if is_onboarding_needed(store):
        console.print()
        console.print(
            "I'd like to ask you a few questions to personalize your experience.",
            style="bold cyan",
        )
        _cmd_onboard(store, config)


def _cmd_status(store: Store, config: GivaConfig):
    """Show current status."""
    stats = store.get_stats()
    from giva.llm.engine import is_loaded

    table = Table(title="Giva Status")
    table.add_column("Item", style="cyan")
    table.add_column("Value", style="white")

    table.add_row("Emails cached", str(stats["emails"]))
    table.add_row("Events cached", str(stats["events"]))
    table.add_row("Pending tasks", str(stats["pending_tasks"]))
    table.add_row("Model", config.llm.model)
    table.add_row("Filter model", config.llm.filter_model)
    table.add_row("Model loaded", "Yes" if is_loaded() else "No (loads on first query)")
    cal_backend = "EventKit (fast)" if _eventkit_authorized() else "AppleScript (no dialog)"
    table.add_row("Calendar backend", cal_backend)
    table.add_row("Data directory", str(config.data_dir))

    for s in stats["syncs"]:
        table.add_row(f"Sync: {s['source']}", f"{s['last_sync']} ({s['last_status']})")

    console.print(table)


def _cmd_history(store: Store):
    """Show recent conversation history."""
    messages = store.get_recent_messages(limit=20)
    if not messages:
        console.print("No conversation history yet.", style="dim")
        return
    for msg in messages:
        role = msg["role"]
        style = "bold cyan" if role == "user" else "white"
        prefix = "You" if role == "user" else "Giva"
        console.print(f"[{style}]{prefix}:[/{style}] {msg['content'][:200]}")


def _cmd_tasks(args: str, store: Store, config: GivaConfig):
    """Show or manage tasks."""
    parts = args.strip().split()

    if not parts:
        # /tasks -- show pending tasks
        _show_tasks(store)
        return

    action = parts[0].lower()

    if action in ("done", "dismiss") and len(parts) >= 2:
        try:
            task_id = int(parts[1])
        except ValueError:
            console.print(f"Invalid task ID: {parts[1]}", style="red")
            return

        status = "done" if action == "done" else "dismissed"
        if store.update_task_status(task_id, status):
            console.print(f"Task #{task_id} marked as {status}.", style="green")
        else:
            console.print(f"Task #{task_id} not found.", style="red")

    elif action == "all":
        _show_tasks(store, status=None)

    else:
        console.print(
            "Usage: /tasks [done|dismiss N] [all]",
            style="yellow",
        )


def _show_tasks(store: Store, status: str = "pending"):
    """Display tasks in a rich table."""
    tasks = store.get_tasks(status=status)
    if not tasks:
        label = status or "any status"
        console.print(f"No tasks ({label}). Run /extract to scan for tasks.", style="dim")
        return

    table = Table(title=f"Tasks ({status or 'all'})")
    table.add_column("ID", style="dim", width=4)
    table.add_column("Pri", width=4)
    table.add_column("Title", style="white", min_width=30)
    table.add_column("Source", style="dim", width=8)
    table.add_column("Due", style="cyan", width=12)
    table.add_column("Status", width=10)

    priority_styles = {"high": "bold red", "medium": "yellow", "low": "dim"}

    for t in tasks:
        pri_style = priority_styles.get(t.priority, "white")
        due_str = t.due_date.strftime("%b %d") if t.due_date else "-"
        status_style = (
            "green" if t.status == "done"
            else ("dim" if t.status == "dismissed" else "white")
        )
        table.add_row(
            str(t.id),
            f"[{pri_style}]{t.priority[0].upper()}[/{pri_style}]",
            t.title,
            t.source_type,
            due_str,
            f"[{status_style}]{t.status}[/{status_style}]",
        )

    console.print(table)


def _cmd_extract(store: Store, config: GivaConfig):
    """Manually trigger task extraction."""
    def on_progress(current, total, source_type, tasks_found):
        console.print(
            f"\r  Processing {source_type}s: {current}/{total} "
            f"({tasks_found} tasks found)...",
            end="", style="dim",
        )

    console.print("Extracting tasks from emails and events...", style="dim")
    try:
        count = extract_tasks(store, config, on_progress=on_progress)
        if count > 0:
            console.print(
                f"\r  Extracted {count} new task(s).                              ",
                style="green",
            )
            _show_tasks(store)
        else:
            console.print(
                "\r  No new actionable tasks found.                              ",
                style="dim",
            )
    except Exception as e:
        console.print(f"\n  Extraction error: {e}", style="red")
        log.exception("Task extraction failed")


def _cmd_suggest(store: Store, config: GivaConfig):
    """Stream proactive suggestions."""
    stats = store.get_stats()
    if stats["emails"] == 0 and stats["events"] == 0:
        console.print(
            "No data synced yet. Run [bold]/sync[/bold] first.",
            style="yellow",
        )
        return

    console.print()
    full_text = []
    try:
        with Live(console=console, refresh_per_second=8) as live:
            for token in get_suggestions(store, config):
                full_text.append(token)
                live.update(Markdown("".join(full_text)))
    except Exception as e:
        console.print(f"Error: {e}", style="red")
        log.exception("Suggestions failed")
    console.print()


def _cmd_profile(store: Store, config: GivaConfig):
    """Show or rebuild user profile."""
    profile = store.get_profile()

    if not profile or not profile.email_address:
        console.print("No profile built yet. Building now...", style="dim")
        with console.status("Analyzing email patterns..."):
            try:
                profile = update_profile(store, config)
            except Exception as e:
                console.print(f"Profile build error: {e}", style="red")
                return

    summary = get_profile_summary(store)
    if summary:
        updated = ""
        if profile.updated_at:
            updated = f" (updated {profile.updated_at.strftime('%b %d, %I:%M %p')})"
        console.print(
            Panel(
                summary,
                title=f"User Profile{updated}",
                border_style="cyan",
            )
        )
    else:
        console.print("Profile is empty — not enough email data.", style="dim")


def _cmd_onboard(store: Store, config: GivaConfig):
    """Run the onboarding interview (multi-turn LLM conversation)."""
    from giva.intelligence.onboarding import (
        continue_onboarding,
        is_onboarding_needed,
        start_onboarding,
    )

    if not is_onboarding_needed(store):
        console.print("Onboarding already completed.", style="dim")
        try:
            answer = input("Run again? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return
        if answer not in ("y", "yes"):
            return
        # Reset onboarding state to re-run
        store.update_profile_data({
            "onboarding_completed": False,
            "onboarding_step": 0,
            "onboarding_history": [],
        })

    console.print()

    # Stream the first question
    full_text = []
    try:
        with Live(console=console, refresh_per_second=8) as live:
            for token in start_onboarding(store, config):
                full_text.append(token)
                live.update(Markdown("".join(full_text)))
    except Exception as e:
        console.print(f"Onboarding error: {e}", style="red")
        log.exception("Onboarding start failed")
        return
    console.print()

    # Multi-turn conversation loop
    while True:
        try:
            user_input = input("Answer> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nOnboarding paused. Resume with /onboard.", style="dim")
            return

        if not user_input:
            continue

        full_text = []
        try:
            with Live(console=console, refresh_per_second=8) as live:
                for token in continue_onboarding(user_input, store, config):
                    full_text.append(token)
                    live.update(Markdown("".join(full_text)))
        except Exception as e:
            console.print(f"Onboarding error: {e}", style="red")
            log.exception("Onboarding continue failed")
            return
        console.print()

        # Check if onboarding is complete
        profile = store.get_profile()
        if profile and profile.profile_data.get("onboarding_completed"):
            console.print(
                "Onboarding complete! Your preferences have been saved.",
                style="bold green",
            )
            console.print("Run /profile to see your full profile.", style="dim")
            return


def _cmd_reset(store: Store):
    """Reset all Giva data and start fresh."""
    console.print(
        "[bold red]Warning:[/bold red] This will delete ALL synced data "
        "(emails, events, tasks, conversations, profile).",
    )
    try:
        answer = input("Are you sure? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print("Cancelled.", style="dim")
        return

    if answer not in ("y", "yes"):
        console.print("Cancelled.", style="dim")
        return

    try:
        store.reset_all_data()
        console.print("All data cleared.", style="green")
        console.print("Run /sync to re-sync and start onboarding.", style="dim")
    except Exception as e:
        console.print(f"Reset error: {e}", style="red")
        log.exception("Reset failed")


def _cmd_setup():
    """Run one-time setup for optional permissions."""
    console.print("\n[bold]Giva Setup[/bold]", style="blue")
    console.print()

    # Calendar (EventKit)
    if _eventkit_authorized():
        console.print("  ✓ Calendar (EventKit): access already granted", style="green")
    else:
        console.print("  Calendar: EventKit access not yet granted.", style="yellow")
        console.print(
            "    Without it, calendar sync uses AppleScript (slower but no dialog needed).",
            style="dim",
        )
        console.print(
            "    Grant EventKit access for faster sync? A macOS dialog will appear.",
            style="dim",
        )
        try:
            answer = input("    Grant access now? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"

        if answer in ("y", "yes"):
            console.print("    Requesting access... (check for macOS dialog)", style="dim")
            if request_eventkit_access():
                console.print("  ✓ Calendar (EventKit): access granted!", style="green")
            else:
                console.print(
                    "  ✗ Calendar (EventKit): access denied. AppleScript fallback will be used.",
                    style="yellow",
                )
        else:
            console.print("    Skipped. AppleScript fallback will be used.", style="dim")

    console.print()
    console.print("Setup complete. Run /sync to start syncing.", style="bold green")
    console.print()


def _cmd_voice(args: str, config: GivaConfig):
    """Toggle voice mode or set it explicitly."""
    global _voice_enabled
    arg = args.strip().lower()

    if arg == "on":
        _voice_enabled = True
    elif arg == "off":
        _voice_enabled = False
        # Stop any playing audio
        if _audio_player is not None:
            _audio_player.stop()
    else:
        _voice_enabled = not _voice_enabled

    status = "ON" if _voice_enabled else "OFF"
    style = "bold green" if _voice_enabled else "dim"
    console.print(f"Voice mode: {status}", style=style)
    if _voice_enabled:
        console.print(
            f"  TTS: {config.voice.tts_model.split('/')[-1]} | "
            f"STT: {config.voice.stt_model}",
            style="dim",
        )
        console.print("  Use /listen to speak a query via microphone.", style="dim")


def _cmd_listen(store: Store, config: GivaConfig):
    """Record from microphone, transcribe, and handle as a query."""
    console.print("Listening... (speak now, pause to stop)", style="bold cyan")
    try:
        stt = _get_stt(config)
        with _voice_lock:
            text = stt.record_until_silence()
        if not text:
            console.print("No speech detected. Try again.", style="yellow")
            return
        console.print(f"You said: [bold cyan]{text}[/bold cyan]")
        console.print()
        _handle_query(text, store, config)
    except ImportError as e:
        console.print(
            f"Voice dependencies not installed: {e}\n"
            "  Install with: pip install mlx-audio lightning-whisper-mlx sounddevice soundfile",
            style="red",
        )
    except Exception as e:
        console.print(f"Listen error: {e}", style="red")
        log.exception("Listen failed")


def _cmd_goals(args: str, store: Store, config: GivaConfig):
    """Manage goals: list, add, detail, infer, edit, progress, status changes."""
    parts = args.strip().split(maxsplit=1)
    action = parts[0].lower() if parts else ""
    rest = parts[1] if len(parts) > 1 else ""

    if not action:
        # /goals — list active goals
        _show_goals(store)
        return

    if action == "add":
        _goals_add(rest, store)
    elif action == "infer":
        _goals_infer(store, config)
    elif action == "detail" and rest:
        _goals_detail(rest, store)
    elif action in ("done", "pause", "abandon") and rest:
        _goals_status_change(action, rest, store)
    elif action == "progress" and rest:
        _goals_progress(rest, store)
    elif action == "edit" and rest:
        _goals_edit(rest, store)
    else:
        console.print(
            "Usage: /goals [add|infer|detail N|done N|pause N|abandon N"
            "|progress N \"note\"|edit N]",
            style="yellow",
        )


def _show_goals(store: Store):
    """Display active goals in a rich table."""
    goals = store.get_goals(status="active")
    if not goals:
        console.print("No active goals. Use /goals add or /goals infer.", style="dim")
        return

    table = Table(title="Active Goals")
    table.add_column("ID", style="dim", width=4)
    table.add_column("Tier", width=10)
    table.add_column("Pri", width=4)
    table.add_column("Title", style="white", min_width=30)
    table.add_column("Category", style="cyan", width=12)
    table.add_column("Target", style="dim", width=12)
    table.add_column("Children", style="dim", width=8)

    tier_styles = {"long_term": "bold blue", "mid_term": "cyan", "short_term": "white"}
    priority_styles = {"high": "bold red", "medium": "yellow", "low": "dim"}

    for g in goals:
        children = store.get_child_goals(g.id)
        tier_label = g.tier.replace("_", "-")
        tier_style = tier_styles.get(g.tier, "white")
        pri_style = priority_styles.get(g.priority, "white")
        target = g.target_date.strftime("%Y-%m-%d") if g.target_date else "-"

        table.add_row(
            str(g.id),
            f"[{tier_style}]{tier_label}[/{tier_style}]",
            f"[{pri_style}]{g.priority[0].upper()}[/{pri_style}]",
            g.title,
            g.category or "-",
            target,
            str(len(children)) if children else "-",
        )

    console.print(table)


def _goals_add(args: str, store: Store):
    """Interactive goal creation."""
    from giva.db.models import Goal
    from datetime import datetime

    try:
        title = input("Goal title: ").strip()
        if not title:
            console.print("Cancelled.", style="dim")
            return

        console.print("Tier: (1) long-term  (2) mid-term  (3) short-term")
        tier_choice = input("Tier [1]: ").strip()
        tier = {"1": "long_term", "2": "mid_term", "3": "short_term"}.get(
            tier_choice, "long_term"
        )

        category = input("Category (career/personal/health/financial/networking/learning): ").strip()

        console.print("Priority: (1) high  (2) medium  (3) low")
        pri_choice = input("Priority [2]: ").strip()
        priority = {"1": "high", "2": "medium", "3": "low"}.get(pri_choice, "medium")

        target_str = input("Target date (YYYY-MM-DD, or blank): ").strip()
        target_date = None
        if target_str:
            try:
                target_date = datetime.fromisoformat(target_str)
            except ValueError:
                console.print("Invalid date format, skipping.", style="yellow")

        parent_str = input("Parent goal ID (or blank): ").strip()
        parent_id = int(parent_str) if parent_str.isdigit() else None

        description = input("Description (optional): ").strip()

    except (EOFError, KeyboardInterrupt):
        console.print("\nCancelled.", style="dim")
        return

    goal = Goal(
        title=title, tier=tier, description=description,
        category=category, priority=priority,
        target_date=target_date, parent_id=parent_id,
    )
    goal_id = store.add_goal(goal)
    console.print(f"Goal #{goal_id} created: {title}", style="bold green")


def _goals_infer(store: Store, config: GivaConfig):
    """Run LLM goal inference and present for confirmation."""
    from giva.intelligence.goals import infer_goals
    from giva.db.models import Goal

    console.print("Analyzing your data to infer goals...", style="dim")
    try:
        inferred = infer_goals(store, config)
    except Exception as e:
        console.print(f"Goal inference error: {e}", style="red")
        return

    if not inferred:
        console.print("No goals inferred. More data may be needed.", style="dim")
        return

    console.print(f"\nInferred {len(inferred)} potential goal(s):\n", style="bold")
    for i, g in enumerate(inferred, 1):
        tier_label = g.get("tier", "long_term").replace("_", "-")
        console.print(
            f"  {i}. [{tier_label}] {g.get('title', '?')} "
            f"({g.get('category', 'general')})",
            style="cyan",
        )
        if g.get("description"):
            console.print(f"     {g['description']}", style="dim")

    try:
        answer = input("\nAccept these goals? [y/N/select numbers e.g. 1,3] ").strip()
    except (EOFError, KeyboardInterrupt):
        console.print("\nCancelled.", style="dim")
        return

    if answer.lower() in ("y", "yes"):
        indices = list(range(len(inferred)))
    elif answer.lower() in ("n", "no", ""):
        console.print("Goals discarded.", style="dim")
        return
    else:
        try:
            indices = [int(x.strip()) - 1 for x in answer.split(",")]
        except ValueError:
            console.print("Invalid selection.", style="red")
            return

    count = 0
    for idx in indices:
        if 0 <= idx < len(inferred):
            g = inferred[idx]
            from datetime import datetime

            target = None
            if g.get("target_date"):
                try:
                    target = datetime.fromisoformat(g["target_date"])
                except (ValueError, TypeError):
                    pass

            goal = Goal(
                title=g.get("title", ""),
                tier=g.get("tier", "long_term"),
                category=g.get("category", ""),
                description=g.get("description", ""),
                priority=g.get("priority", "medium"),
                target_date=target,
            )
            store.add_goal(goal)
            count += 1

    console.print(f"Created {count} goal(s).", style="bold green")


def _goals_detail(args: str, store: Store):
    """Show detailed view of a specific goal."""
    try:
        goal_id = int(args.strip())
    except ValueError:
        console.print(f"Invalid goal ID: {args}", style="red")
        return

    goal = store.get_goal(goal_id)
    if not goal:
        console.print(f"Goal #{goal_id} not found.", style="red")
        return

    # Build detail panel
    lines = []
    lines.append(f"[bold]{goal.title}[/bold]")
    lines.append(f"Tier: {goal.tier.replace('_', '-')} | Category: {goal.category or 'N/A'}")
    lines.append(f"Status: {goal.status} | Priority: {goal.priority}")
    if goal.target_date:
        lines.append(f"Target: {goal.target_date.strftime('%Y-%m-%d')}")
    if goal.description:
        lines.append(f"\n{goal.description}")

    # Children
    children = store.get_child_goals(goal_id)
    if children:
        lines.append(f"\n[bold]Sub-objectives ({len(children)}):[/bold]")
        for c in children:
            lines.append(f"  - [{c.priority[0].upper()}] {c.title} ({c.status})")

    # Strategies
    strategies = store.get_strategies(goal_id)
    if strategies:
        lines.append(f"\n[bold]Strategies ({len(strategies)}):[/bold]")
        for s in strategies:
            status_style = "green" if s.status == "accepted" else "dim"
            lines.append(
                f"  [{status_style}][{s.status}][/{status_style}] {s.strategy_text[:100]}"
            )

    # Tasks
    tasks = store.get_tasks_for_goal(goal_id)
    if tasks:
        pending = [t for t in tasks if t.status == "pending"]
        done = [t for t in tasks if t.status == "done"]
        lines.append(f"\n[bold]Tasks ({len(done)} done, {len(pending)} pending):[/bold]")
        for t in tasks[:10]:
            status_mark = "[green]✓[/green]" if t.status == "done" else "○"
            lines.append(f"  {status_mark} [{t.priority[0].upper()}] {t.title}")

    # Progress
    progress = store.get_goal_progress(goal_id, limit=5)
    if progress:
        lines.append("\n[bold]Recent Progress:[/bold]")
        for p in progress:
            date_str = p.created_at.strftime("%b %d") if p.created_at else "?"
            lines.append(f"  > {date_str} [{p.source}]: {p.note}")

    console.print(Panel("\n".join(lines), title=f"Goal #{goal_id}", border_style="cyan"))


def _goals_status_change(action: str, args: str, store: Store):
    """Change goal status: done, pause, abandon."""
    try:
        goal_id = int(args.strip())
    except ValueError:
        console.print(f"Invalid goal ID: {args}", style="red")
        return

    status_map = {"done": "completed", "pause": "paused", "abandon": "abandoned"}
    new_status = status_map.get(action, action)

    if store.update_goal_status(goal_id, new_status):
        console.print(f"Goal #{goal_id} marked as {new_status}.", style="green")
    else:
        console.print(f"Goal #{goal_id} not found.", style="red")


def _goals_progress(args: str, store: Store):
    """Add a progress note: /goals progress N "note"."""
    import re as _re

    match = _re.match(r'(\d+)\s+["\']?(.+?)["\']?\s*$', args)
    if not match:
        console.print('Usage: /goals progress N "progress note"', style="yellow")
        return

    goal_id = int(match.group(1))
    note = match.group(2)

    goal = store.get_goal(goal_id)
    if not goal:
        console.print(f"Goal #{goal_id} not found.", style="red")
        return

    store.add_goal_progress(goal_id, note, "user")
    console.print(f"Progress added to goal #{goal_id}.", style="green")


def _goals_edit(args: str, store: Store):
    """Interactive edit of a goal."""
    try:
        goal_id = int(args.strip())
    except ValueError:
        console.print(f"Invalid goal ID: {args}", style="red")
        return

    goal = store.get_goal(goal_id)
    if not goal:
        console.print(f"Goal #{goal_id} not found.", style="red")
        return

    console.print(f"Editing goal #{goal_id}: {goal.title}", style="bold")
    console.print("(Press Enter to keep current value)\n", style="dim")

    try:
        title = input(f"Title [{goal.title}]: ").strip() or goal.title
        description = input(f"Description [{goal.description or 'N/A'}]: ").strip()
        if not description:
            description = goal.description
        category = input(f"Category [{goal.category or 'N/A'}]: ").strip()
        if not category:
            category = goal.category
        target_str = input(
            f"Target date [{goal.target_date.strftime('%Y-%m-%d') if goal.target_date else 'N/A'}]: "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        console.print("\nCancelled.", style="dim")
        return

    updates = {}
    if title != goal.title:
        updates["title"] = title
    if description != goal.description:
        updates["description"] = description
    if category != goal.category:
        updates["category"] = category
    if target_str:
        updates["target_date"] = target_str

    if updates:
        store.update_goal(goal_id, **updates)
        console.print(f"Goal #{goal_id} updated.", style="green")
    else:
        console.print("No changes.", style="dim")


def _cmd_strategy(args: str, store: Store, config: GivaConfig):
    """Generate strategy for a goal: /strategy N."""
    if not args.strip():
        console.print("Usage: /strategy <goal_id>", style="yellow")
        return

    try:
        goal_id = int(args.strip())
    except ValueError:
        console.print(f"Invalid goal ID: {args}", style="red")
        return

    from giva.intelligence.goals import generate_strategy

    console.print()
    full_text = []
    try:
        with Live(console=console, refresh_per_second=8) as live:
            for token in generate_strategy(goal_id, store, config):
                full_text.append(token)
                live.update(Markdown("".join(full_text)))
    except Exception as e:
        console.print(f"Strategy error: {e}", style="red")
        return
    console.print()

    # Offer to accept
    strategies = store.get_strategies(goal_id, status="proposed")
    if strategies:
        try:
            answer = input("Accept this strategy? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return
        if answer in ("y", "yes"):
            # Supersede old accepted strategies
            for s in store.get_strategies(goal_id, status="accepted"):
                store.update_strategy_status(s.id, "superseded")
            store.update_strategy_status(strategies[0].id, "accepted")
            console.print("Strategy accepted.", style="bold green")
        else:
            console.print("Strategy kept as proposed.", style="dim")


def _cmd_plan(args: str, store: Store, config: GivaConfig):
    """Generate or review tactical plans: /plan N or /plan review."""
    if not args.strip():
        console.print("Usage: /plan <objective_id> | /plan review", style="yellow")
        return

    if args.strip().lower() == "review":
        _plan_review(store, config)
        return

    try:
        objective_id = int(args.strip())
    except ValueError:
        console.print(f"Invalid objective ID: {args}", style="red")
        return

    from giva.intelligence.goals import generate_tactical_plan, accept_plan

    console.print()
    full_text = []
    try:
        with Live(console=console, refresh_per_second=8) as live:
            for token in generate_tactical_plan(objective_id, store, config):
                full_text.append(token)
                live.update(Markdown("".join(full_text)))
    except Exception as e:
        console.print(f"Plan error: {e}", style="red")
        return
    console.print()

    # Offer to accept and create tasks
    plan_json = "".join(full_text)
    try:
        answer = input("Create tasks from this plan? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return

    if answer in ("y", "yes"):
        count = accept_plan(plan_json, objective_id, store)
        if count > 0:
            console.print(f"Created {count} task(s) linked to goal #{objective_id}.", style="bold green")
        else:
            console.print("Could not parse tasks from plan.", style="yellow")
    else:
        console.print("Plan noted but no tasks created.", style="dim")


def _plan_review(store: Store, config: GivaConfig):
    """Review status of active tactical plans."""
    from giva.intelligence.daily_review import review_tactical_plans

    console.print()
    full_text = []
    try:
        with Live(console=console, refresh_per_second=8) as live:
            for token in review_tactical_plans(store, config):
                full_text.append(token)
                live.update(Markdown("".join(full_text)))
    except Exception as e:
        console.print(f"Plan review error: {e}", style="red")
    console.print()


def _cmd_review(store: Store, config: GivaConfig):
    """Run daily review: /review."""
    from giva.intelligence.daily_review import generate_review, save_review_response

    console.print()
    full_text = []
    review_id = None
    try:
        gen = generate_review(store, config)
        with Live(console=console, refresh_per_second=8) as live:
            for token in gen:
                full_text.append(token)
                live.update(Markdown("".join(full_text)))
        # Try to get the return value (review_id)
        try:
            review_id = gen.send(None)
        except StopIteration as e:
            review_id = e.value
    except Exception as e:
        console.print(f"Review error: {e}", style="red")
        return
    console.print()

    # Prompt for user response
    try:
        console.print("How did your day go? (type your response, or Enter to skip)", style="bold cyan")
        response = input("Response> ").strip()
    except (EOFError, KeyboardInterrupt):
        console.print("\nReview saved without response.", style="dim")
        return

    if response and review_id:
        summary = save_review_response(review_id, response, store, config)
        console.print(f"\nSummary: {summary}", style="green")
    elif not response:
        console.print("Review saved without response.", style="dim")


def _cmd_help():
    """Show help."""
    help_text = """
**Available commands:**

| Command | Description |
|---------|-------------|
| `/sync` | Sync emails and calendar from Apple Mail/Calendar |
| `/extract` | Extract tasks from unprocessed emails/events via LLM |
| `/tasks` | Show pending tasks |
| `/tasks done N` | Mark task N as completed |
| `/tasks dismiss N` | Dismiss task N |
| `/tasks all` | Show all tasks (including done/dismissed) |
| `/suggest` | Get proactive AI-powered priority suggestions |
| `/profile` | Show your auto-detected user profile |
| `/onboard` | Run (or re-run) the personalization interview |
| `/reset` | Clear all data and start fresh |
| `/goals` | List active goals |
| `/goals add` | Interactive goal creation |
| `/goals infer` | Infer goals from your data via LLM |
| `/goals detail N` | Show goal N with strategies, tasks, progress |
| `/goals done\\|pause\\|abandon N` | Update goal status |
| `/goals progress N "note"` | Add a progress note to goal N |
| `/goals edit N` | Edit goal title/description/category/target |
| `/strategy N` | Generate strategy for goal N |
| `/plan N` | Generate tactical plan for objective N |
| `/plan review` | Review status of active tactical plans |
| `/review` | Run daily review |
| `/voice` | Toggle voice mode (TTS on responses) |
| `/voice on\\|off` | Explicitly enable/disable voice mode |
| `/listen` | Record from mic, transcribe, and query |
| `/setup` | One-time setup for optional permissions (EventKit) |
| `/status` | Show database stats, model status, sync times |
| `/history` | Show recent conversation history |
| `/clear` | Clear conversation history |
| `/help` | Show this help message |
| `/quit` | Exit Giva |

**Natural language queries** — just type your question:
- "Any emails from Sarah this week?"
- "What meetings do I have tomorrow?"
- "Did I follow up on the budget discussion?"
"""
    console.print(Markdown(help_text))


def _handle_query(query: str, store: Store, config: GivaConfig):
    """Handle a natural language query via the LLM.

    When voice mode is enabled, streams tokens to the console AND simultaneously
    synthesizes TTS audio per sentence in a background thread.
    """
    stats = store.get_stats()
    if stats["emails"] == 0 and stats["events"] == 0:
        console.print(
            "No data synced yet. Run [bold]/sync[/bold] first to pull emails and calendar events.",
            style="yellow",
        )
        return

    console.print()
    full_text = []

    if not _voice_enabled:
        # Standard text-only streaming
        try:
            with Live(console=console, refresh_per_second=8) as live:
                for token in handle_query(query, store, config):
                    full_text.append(token)
                    live.update(Markdown("".join(full_text)))
        except Exception as e:
            console.print(f"Error: {e}", style="red")
            log.exception("Query failed")
    else:
        # Voice mode: stream text + synthesize audio per sentence in background
        try:
            from giva.audio.tts import split_sentences

            player = _get_player()
            tts = _get_tts(config)
            sentence_buffer = ""

            def _synth_and_enqueue(text: str):
                """Synthesize a sentence and enqueue for playback (runs in thread)."""
                try:
                    with _voice_lock:
                        audio, sr = tts.synthesize(text)
                    if len(audio) > 0:
                        player.enqueue(audio, sr)
                except Exception as e:
                    log.warning("TTS synthesis error: %s", e)

            with Live(console=console, refresh_per_second=8) as live:
                for token in handle_query(query, store, config):
                    full_text.append(token)
                    live.update(Markdown("".join(full_text)))

                    # Buffer tokens into sentences for TTS
                    sentence_buffer += token
                    sentences = split_sentences(sentence_buffer)
                    if len(sentences) > 1:
                        for sentence in sentences[:-1]:
                            sentence = sentence.strip()
                            if sentence:
                                # Synthesize in background thread
                                t = threading.Thread(
                                    target=_synth_and_enqueue,
                                    args=(sentence,),
                                    daemon=True,
                                )
                                t.start()
                        sentence_buffer = sentences[-1]

            # Synthesize any remaining text
            remainder = sentence_buffer.strip()
            if remainder:
                _synth_and_enqueue(remainder)

            # Wait for all audio to finish playing
            player.wait()

        except ImportError as e:
            console.print(
                f"Voice dependencies not installed: {e}\n"
                "  Install with: pip install mlx-audio lightning-whisper-mlx "
                "sounddevice soundfile",
                style="red",
            )
        except Exception as e:
            console.print(f"Error: {e}", style="red")
            log.exception("Query with voice failed")

    console.print()


if __name__ == "__main__":
    main()
