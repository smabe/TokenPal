# Textual UI Improvements

## Goal
Catalog Textual-enabled improvements now that the framework is in place. Each item is a standalone feature that can be planned and shipped independently.

## Items

### Quick wins
- [ ] **Conversation history panel (#8)** — RichLog/ListView above buddy showing chat back-and-forth
- [ ] **Scrollable speech bubbles** — Wrap SpeechBubbleWidget in VerticalScroll for long responses
- [ ] **Focus management** — Auto-refocus Input on blur so keyboard input never gets lost
- [ ] **Keyboard shortcuts** — F1=/help, Ctrl+L=/clear, Up/Down for input history recall
- [ ] **Color-coded status bar** — Mood-colored segments via Rich markup

### Medium effort
- [ ] **ASCII art from images (#10)** — Per-voice buddy art as swappable widget content
- [ ] **Model pull progress bar** — Real ProgressBar for /model pull instead of persistent bubble
- [ ] **Notification toasts** — app.notify() for easter eggs, milestones, transient errors

### Bigger plays
- [ ] **Tabbed views** — TabbedContent for buddy / conversation log / settings
- [ ] **Mouse support** — Click buddy for interaction, click status bar segments for details

## Parking lot

