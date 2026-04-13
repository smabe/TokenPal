# Conversation History UI

## Goal
Show a scrollable chat log on the right side of the screen (full vertical height) that captures all buddy output — observations, conversation replies, and user messages — so nothing disappears when the speech bubble auto-hides.

## Non-goals
- Persisting chat log to disk — display-only, clears on exit
- Changing the brain/orchestrator conversation logic — purely UI
- Rich formatting per message (avatars, colors per role) — plain text with role prefix for v1
- Input history recall (Up arrow for past commands) — park for textual-improvements

## Files to touch
- `tokenpal/ui/textual_overlay.py` — add ChatLog widget (RichLog), new messages (LogUserMessage, LogBuddyMessage), horizontal layout split (left=buddy area, right=chat log)
- `tokenpal/ui/textual_overlay.tcss` — horizontal split layout, chat log styling with border, scroll
- `tokenpal/ui/base.py` — add `log_user_message(text)` and `log_buddy_message(text)` to AbstractOverlay (optional, default no-op)
- `tokenpal/app.py` — pipe user input text to overlay log, pipe brain comments to log alongside speech bubble
- `tests/test_ui/test_textual_overlay.py` — tests for chat log population and scrolling

## Failure modes to anticipate
- Horizontal split squeezing buddy art on narrow terminals — need min-width or collapse behavior
- RichLog auto-scroll fighting with user scrolling up to read history
- Long messages wrapping within the log panel — need to respect panel width
- Clearing log on `/clear` — need a ClearLog message wired through
- Thread safety — buddy messages arrive via post_message, user messages from app thread; RichLog should handle both since message handlers run on app thread
- Chat log showing duplicate content (speech bubble + log both show same text) — this is intentional, not a bug

## Done criteria
- Screen split: left side has buddy/speech/input/status, right side has scrollable chat log
- All buddy comments (observations + conversation replies) appear in the log
- User messages appear in the log prefixed distinctly from buddy messages
- Log auto-scrolls on new messages
- `/clear` clears the chat log
- Speech bubble still works independently (log is supplementary)
- Reasonable on 80-column terminals (log collapses or gets narrow gracefully)
- Tests pass, lint clean

## Parking lot

