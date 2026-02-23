"""Calendar sync via AppleScript (default) or EventKit (opt-in).

AppleScript is the default because it works without any permission dialogs,
making it suitable for background/unattended operation. EventKit requires a
one-time interactive permission grant via macOS TCC dialog, so it is only
used when the user has already granted access (detected automatically) or
explicitly opts in via the setup flow.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from giva.db.models import Event
from giva.db.store import Store

log = logging.getLogger(__name__)


def _eventkit_available() -> bool:
    """Check if EventKit framework is importable."""
    try:
        import EventKit  # noqa: F401
        return True
    except ImportError:
        return False


def _eventkit_authorized() -> bool:
    """Check if EventKit access is already authorized (no dialog triggered)."""
    if not _eventkit_available():
        return False
    try:
        import EventKit
        status = EventKit.EKEventStore.authorizationStatusForEntityType_(
            EventKit.EKEntityTypeEvent
        )
        # EKAuthorizationStatusFullAccess (3) or legacy Authorized (2)
        return status in (2, 3)
    except Exception:
        return False


def request_eventkit_access() -> bool:
    """Interactively request EventKit access (triggers macOS TCC dialog).

    Call this from an interactive context (e.g. CLI setup command).
    Returns True if access was granted.
    """
    if not _eventkit_available():
        log.warning("EventKit not available (pyobjc-framework-EventKit not installed)")
        return False

    import EventKit

    event_store = EventKit.EKEventStore.alloc().init()
    granted = [None]

    def callback(g, e):
        granted[0] = g

    event_store.requestFullAccessToEventsWithCompletion_(callback)

    # Wait for user to respond to dialog (up to 60 seconds)
    for _ in range(600):
        if granted[0] is not None:
            break
        time.sleep(0.1)

    if granted[0]:
        log.info("EventKit calendar access granted")
    else:
        log.warning("EventKit calendar access denied")
    return bool(granted[0])


def sync_calendar(
    store: Store,
    past_days: int = 7,
    future_days: int = 30,
) -> int:
    """Sync calendar events.

    Uses EventKit if access is already authorized (fast, native).
    Otherwise uses AppleScript (no dialog needed, background-safe).
    """
    if _eventkit_authorized():
        log.info("Using EventKit for calendar sync (access already granted)")
        return _sync_eventkit(store, past_days, future_days)
    log.info("Using AppleScript for calendar sync (no dialog required)")
    return _sync_applescript(store, past_days, future_days)


def _sync_eventkit(store: Store, past_days: int, future_days: int) -> int:
    """Sync via EventKit (fast, native). Only called when access already granted."""
    import EventKit
    from Foundation import NSDate

    event_store = EventKit.EKEventStore.alloc().init()

    start = NSDate.dateWithTimeIntervalSinceNow_(float(-past_days * 86400))
    end = NSDate.dateWithTimeIntervalSinceNow_(float(future_days * 86400))
    predicate = event_store.predicateForEventsWithStartDate_endDate_calendars_(start, end, None)
    ek_events = event_store.eventsMatchingPredicate_(predicate)

    count = 0
    for ek_event in ek_events or []:
        try:
            uid = ek_event.calendarItemExternalIdentifier() or f"ek-{count}"
            dtstart = _nsdate_to_datetime(ek_event.startDate())
            dtend = _nsdate_to_datetime(ek_event.endDate()) if ek_event.endDate() else None

            attendees = []
            for att in ek_event.attendees() or []:
                attendees.append({
                    "name": att.name() or "",
                    "status": _participant_status(att.participantStatus()),
                })

            event = Event(
                uid=uid,
                calendar_name=ek_event.calendar().title() or "Unknown",
                summary=ek_event.title() or "(no title)",
                description=ek_event.notes() or "",
                location=ek_event.location() or "",
                dtstart=dtstart,
                dtend=dtend,
                all_day=ek_event.isAllDay(),
                organizer=ek_event.organizer().name() if ek_event.organizer() else "",
                attendees=attendees,
                status="CONFIRMED",
            )
            store.upsert_event(event)
            count += 1
        except Exception as e:
            log.warning("Failed to parse event: %s", e)

    store.update_sync_state("calendar", count, "success")
    log.info("Synced %d calendar events via EventKit", count)
    return count


def _nsdate_to_datetime(nsdate) -> datetime:
    """Convert NSDate to Python datetime."""
    # NSDate timeIntervalSince1970 returns seconds since Unix epoch
    timestamp = nsdate.timeIntervalSince1970()
    return datetime.fromtimestamp(timestamp)


def _participant_status(status: int) -> str:
    """Convert EKParticipantStatus to string."""
    mapping = {0: "unknown", 1: "pending", 2: "accepted", 3: "declined", 4: "tentative"}
    return mapping.get(status, "unknown")


def _sync_applescript(store: Store, past_days: int, future_days: int) -> int:
    """Fallback: sync via AppleScript (slower but no PyObjC dependency needed)."""
    from giva.utils.applescript import run_jxa_json

    script = f"""
var cal = Application("Calendar");
var calendars = cal.calendars();
var now = new Date();
var start = new Date(now.getTime() - {past_days} * 86400000);
var end = new Date(now.getTime() + {future_days} * 86400000);
var results = [];
for (var i = 0; i < calendars.length; i++) {{
    var c = calendars[i];
    var calName = c.name();
    try {{
        var events = c.events.whose({{
            _and: [
                {{startDate: {{_greaterThan: start}}}},
                {{startDate: {{_lessThan: end}}}}
            ]
        }})();
        for (var j = 0; j < events.length; j++) {{
            var e = events[j];
            try {{
                results.push({{
                    uid: e.uid(),
                    calendar: calName,
                    summary: e.summary(),
                    description: e.description() || "",
                    location: e.location() || "",
                    start: e.startDate().toISOString(),
                    end: e.endDate() ? e.endDate().toISOString() : "",
                    allDay: e.alldayEvent()
                }});
            }} catch(err) {{}}
        }}
    }} catch(err) {{}}
}}
JSON.stringify(results);
"""
    try:
        events_data = run_jxa_json(script, timeout=180)
    except Exception as e:
        log.error("Calendar AppleScript sync failed: %s", e)
        store.update_sync_state("calendar", 0, f"error: {e}")
        return 0

    count = 0
    for ed in events_data:
        try:
            dtstart = datetime.fromisoformat(ed["start"].replace("Z", "+00:00"))
            dtend = (
                datetime.fromisoformat(ed["end"].replace("Z", "+00:00"))
                if ed.get("end")
                else None
            )
            event = Event(
                uid=ed["uid"],
                calendar_name=ed.get("calendar", "Unknown"),
                summary=ed.get("summary", "(no title)"),
                description=ed.get("description", ""),
                location=ed.get("location", ""),
                dtstart=dtstart,
                dtend=dtend,
                all_day=ed.get("allDay", False),
            )
            store.upsert_event(event)
            count += 1
        except Exception as e:
            log.warning("Failed to parse calendar event: %s", e)

    store.update_sync_state("calendar", count, "success")
    log.info("Synced %d calendar events via AppleScript", count)
    return count
