# Resume — selected-text-primitive / #52 (briefed 2026-09-04, all three phases committed)

```
#52 (selected-text primitive, read_selection tool, /proofread + /explain) is
implemented on main, unpushed: eb20f7b (p1 primitive), d5172ca (p2 tool),
750a164 (p3 commands), plus three "plans: seal" commits. Plan master:
plans/selected-text-primitive.md (APPROVED 2026-09-03, every phase SHIPPED).
Suite 2222 passed, ruff clean, mypy at the #62 baseline.

What is left is the LIVE checklist — the Mac locked during the run, so nothing
below was observed. Do these with the Qt overlay running (./run.sh), the MTPLX
backend up, and record each observation in the matching shard's
"Decisions & findings" (p1 for 4, p2 for 6, p3 for the rest):

1. /consent → grant desktop_content.
2. TextEdit: select a paragraph with two deliberate typos → click TokenPal's
   chat input → /proofread. Expect "> proofread: N chars from TextEdit", the
   corrected text with a "Changes:" list, then a persona bubble. Then:
   sqlite3 ~/.tokenpal/memory.db "select text from chat_log order by rowid desc limit 5"
   → status line and bubble present, correction absent.
3. /explain on an error message pasted into Notes. Safari: select page text →
   a result. VS Code: expect the nothing-focused message (Chromium apps don't
   expose their selection while inactive), then /proofread <pasted text>.
4. TextEdit with nothing selected → "(whole field — nothing was selected)" in
   the status line and a correction. This also closes p1's Qt-host observation.
5. Messages frontmost → switch → /proofread → "Won't read from that app: it's
   on the sensitive-app list." and no status line.
6. Add [tools] enabled_tools = ["read_selection", "agent_mode"] to
   ~/.tokenpal/config.toml; tokenpal --check lists read_selection;
   /tools describe read_selection → platforms: darwin, safe: True,
   cacheable: false; with a TextEdit selection, /agent tell me what I selected
   → trace "← [desktop content: N chars, not shown]" (N = envelope chars, more
   than the selection), answer in the pane, not in chat_log.
7. /consent revoke desktop_content → /proofread refuses; /proofread teh cat
   still works; re-grant.
8. Note whether the MTPLX reply arrives truncated with thinking on (explicit
   max_tokens shares the budget with reasoning tokens).

Then: /plan ship selected-text-primitive (archives master + shards, disposes
the parking lot — the Windows UI Automation path becomes its own issue), then
/post-ship. Attribution note: the three phase commits carry Co-Authored-By /
Claude-Session trailers because the harness instructed it this session;
amend before pushing if you still want them off.
```
