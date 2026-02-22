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
