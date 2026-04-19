# Wiki Scraper: Improve Voice Line Yield

**Problem:** Bender got 410 lines vs Mordecai's 7279. Some wikis yield far fewer lines due to format mismatches in the scraper.

**Done criteria:** Re-scraping Bender yields 500+ lines. No regressions on existing voices.

---

## Root Causes

1. **HTML tags not stripped** (`wiki_fetch.py:74-81`)
   - Futurama wraps dialogue in `<poem>` tags
   - `_strip_wiki_markup()` doesn't remove HTML tags
   - Lines starting with `<poem>` fail the dialogue regex at line 99
   - Estimated impact: +40-70 lines for Bender

2. **Dialogue regex rejects names with digits** (`wiki_fetch.py:23`, `transcript_parser.py:15`)
   - Pattern `^([A-Za-z][A-Za-z .'\-]{0,30})` doesn't allow digits
   - Drops lines from "Clerk 1", numbered characters, etc.
   - Minor impact but affects multiple wikis

3. **Fewer transcript pages** (structural, not fixable)
   - Futurama: 163 pages, Regular Show: 280
   - Futurama pages are also shorter on average

## Fixes (in priority order)

### Fix 1: Strip HTML tags (quick win)

`wiki_fetch.py` line 21, add:
```python
_RE_HTML_TAG = re.compile(r"</?[a-z]+[^>]*>", re.IGNORECASE)
```

In `_strip_wiki_markup()` after line 80, add:
```python
text = _RE_HTML_TAG.sub("", text)
```

### Fix 2: Allow digits in character names

`wiki_fetch.py` line 23 and `transcript_parser.py` line 15:
```python
# Before
^([A-Za-z][A-Za-z .'\-]{0,30}):\s+(.+)
# After
^([A-Za-z][A-Za-z0-9 .'\-&,]{0,30}):\s+(.+)
```

### Fix 3: Try alternate category names

Some wikis use `Category:Transcript` (singular) or `Category:Scripts`. Try multiple category names and merge results.

`wiki_fetch.py:list_transcript_pages()` — try a list of categories:
```python
CATEGORIES = ["Category:Transcripts", "Category:Transcript", "Category:Scripts"]
```

### Fix 4: Log diagnostic stats

After scraping, print a summary: pages found, lines matched, lines filtered (too short/long/dupe). Helps diagnose future low-yield wikis without code diving.

## Test Plan

- [ ] Re-scrape Bender, compare line count (target: 500+)
- [ ] Re-scrape Finn, confirm no regression (should stay ~6957)
- [ ] Re-scrape Mordecai, confirm no regression
- [ ] Try a new wiki (e.g., simpsons.fandom.com for Homer) to validate generalization

## Key Files

| File | What | Lines |
|------|------|-------|
| `tokenpal/tools/wiki_fetch.py` | Wiki API + markup stripping | 74-81 (bug), 23 (regex) |
| `tokenpal/tools/transcript_parser.py` | Format detection + line extraction | 15 (regex), 106-120 (filters) |
| `tokenpal/tools/train_voice.py` | Pipeline entry point | 499-525 |
