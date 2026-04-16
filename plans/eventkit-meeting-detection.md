# EventKit + meeting-detection sense

## Context

The buddy has no awareness of video calls. It talks through meetings, makes typing-cadence assumptions while you're on Zoom, and has no concept of "about to start a call" timing. Peer productivity tools fuse calendar + process + camera signals to infer meeting state without a microphone. We want the same signal at TokenPal's privacy bar: no attendee names, no titles, no URLs ever logged or sent to the LLM.

## Approach

New opt-in sense `meeting` that fuses three signals:

1. Calendar scan for an "active" event (now falls inside start..end)
2. Process scan for Zoom, Microsoft Teams, Google Meet (browser title patterns for meet.google.com only, not full title), Slack huddle, Discord call
3. Camera-in-use boolean

Output: `{"likely_in_meeting": bool, "minutes_remaining": int | None, "source": "calendar|process|camera"}`. Summary string says "probably in a meeting" or "meeting in Nm" - never title, never attendees, never URL.

Platform split: macOS first (EventKit via PyObjC + Quartz for camera), Windows stub (Outlook COM + process scan, camera-in-use via MMDevice API), Linux stub (dbus for evolution-data-server + process scan + v4l2 for camera).

## Files

New:
- `tokenpal/senses/meeting/__init__.py` - register_sense
- `tokenpal/senses/meeting/sense.py` - platform dispatch, TTL, privacy filtering
- `tokenpal/senses/meeting/macos_impl.py` - EventKit + Quartz
- `tokenpal/senses/meeting/win_impl.py` - Outlook COM + MMDevice
- `tokenpal/senses/meeting/linux_impl.py` - dbus + v4l2
- `tests/test_senses/test_meeting.py` - fixtures + privacy assertions

Modify:
- `tokenpal/config/schema.py` - add `meeting: bool = False` to SensesConfig
- `config.default.toml` - add `meeting = false` with opt-in note
- `CLAUDE.md` - document sense behavior + privacy bar

Reuse:
- `tokenpal.util.text_guards.contains_sensitive_term` for any string that survives
- `tokenpal.senses.base.AbstractSense` + SenseReading pattern
- TTL pattern from weather/world_awareness (poll 30s, TTL 120s)

## Phases

1. macOS MVP: EventKit calendar read + process scan + camera-in-use + test harness
2. Privacy tests: assert no title / attendee / URL ever appears in summary or data dict
3. Windows stub: Outlook COM + process scan (camera best-effort)
4. Linux stub: dbus + process scan (camera best-effort)
5. Wire to orchestrator so "likely_in_meeting" suppresses observation comments the way sensitive-app filter does

## Verification

- `tokenpal --validate` passes on every platform
- `/senses list` shows meeting flag, `/senses enable meeting` round-trips
- Mock calendar event + Zoom process: sense emits `likely_in_meeting=True`, summary has no title
- Mock sensitive calendar term ("therapy", "interview"): filter strips to generic "probably in a meeting"
- Grep the log output during a fake session: no raw titles, no attendees, no meet URLs
- Brain loop respects meeting state: observations suppressed, freeform silenced, conversation still works

## Done criteria

macOS path end-to-end in one session, Win + Linux paths have stubs that degrade gracefully (no NotImplementedError), privacy tests green, docs updated.
