# Textual UI Improvements

## Goal
Catalog Textual-enabled improvements now that the framework is in place. Each item is a standalone feature that can be planned and shipped independently.

## Items

### Quick wins
- [x] **Conversation history panel (#8)** — Shipped as right-side chat log
- [ ] **Scrollable speech bubbles** — Wrap SpeechBubbleWidget in VerticalScroll for long responses
- [x] ~~**Focus management**~~ — Skipped: conflicts with chat log text selection
- [x] **Keyboard shortcuts** — F1=/help, Ctrl+L=/clear
- [x] **Color-coded status bar** — Mood-colored first segment via Rich markup

### Medium effort
- [ ] **ASCII art from images (#10)** — Per-voice buddy art as swappable widget content
- [ ] **Model pull progress bar** — Real ProgressBar for /model pull instead of persistent bubble
- [ ] **Notification toasts** — app.notify() for easter eggs, milestones, transient errors

### Bigger plays
- [ ] **Tabbed views** — TabbedContent for buddy / conversation log / settings
- [ ] **Mouse support** — Click buddy for interaction, click status bar segments for details

## Parking lot

