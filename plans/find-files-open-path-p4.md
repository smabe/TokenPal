# find-files-open-path-p4 — Windows Search index backend for `find_files`

You are phase `p4` of the `find-files-open-path` plan. This phase delivers, as one commit, the Windows Search (SystemIndex) backend for `find_files`, written on a Mac against Microsoft Learn and pywin32 documentation, unit-tested for its SQL builder and escaping, and guarded so any COM failure drops to the p2 walk. It cannot be executed here; the operator will verify it on the AMD desktop later (a follow-up issue is filed at ship). p2 has shipped `tokenpal/actions/find_files.py` with a single platform dispatch point.

## Locked decisions
See the master `plans/find-files-open-path.md`. The decisions binding this phase:
- The COM code lives in `tokenpal/util/windows_search.py`, outside the `tokenpal.actions` package walk, with a `try: import win32com.client, pythoncom except ImportError` guard in the `tokenpal/senses/app_awareness/win32_apps.py:15-20` style and no `type: ignore` (`mypy --ignore-missing-imports` per CLAUDE.md).
- Every COM call runs inside `asyncio.to_thread`, and the worker function calls `pythoncom.CoInitialize()` first ("initialize the COM libraries for the calling thread", https://timgolden.me.uk/pywin32-docs/pythoncom__CoInitialize_meth.html) and creates and uses the ADODB objects inside that same thread; cross-thread use would need interface marshaling (`CoMarshalInterThreadInterfaceInStream` in the pythoncom index), which this plan avoids by construction.
- Any `Exception` raised by the COM body (`pywintypes.com_error`, https://timgolden.me.uk/pywin32-docs/com_error.html, is the expected one on `Connection.Open`/`Recordset.Open`; an `AttributeError` from a wrong late-bound name is the other) means "index backend unavailable" → log at DEBUG, return `None`, and `find_files` falls to the walk. Zero rows is a real answer, not a fallback trigger.
- No character stripping. The user term is escaped for each context it enters: in the LIKE literal `'` → `''` and each of `%`, `_`, `[` → `[%]`, `[_]`, `[[]`; in the CONTAINS literal `'` → `''` and `"` → `""` (`-search-sql-like`, `-search-sql-contains`, `-search-sql-literals`).
- `kind` uses p2's enum: `document` → `System.Kind = 'document'`; `image` → `System.Kind = 'picture'`; `code` and `pdf` → `System.FileExtension IN (...)` from p2's extension sets; `any` → no clause.

## Work
- Scope trace: DIRECT — the issue names Windows Search; the operator asked for it built now and verified later.
- `tokenpal/util/windows_search.py` — new. Shape (proposal):
  ```python
  _CONNECTION = "Provider=Search.CollatorDSO;Extended Properties='Application=Windows';"
  # https://learn.microsoft.com/en-us/windows/win32/search/using-sql-and-aqs-to-query-the-index

  def build_sql(term: str, roots: Sequence[Path], kind: str, since_days: int | None, limit: int) -> str
  # SELECT TOP {limit} System.ItemPathDisplay, System.DateModified FROM SystemIndex
  # WHERE (System.FileName LIKE '%{t}%' OR CONTAINS('"{t}*"'))
  #   AND (SCOPE='file:C:/Users/x/Documents' OR SCOPE='file:...')      -- deep scope, '/' separators
  #   [AND System.Kind = 'document']  |  [AND System.Kind = 'picture']  |  [AND System.FileExtension IN ('.py', ...)]
  #   [AND System.DateModified >= DATEADD(DAY, -{since_days}, GETGMTDATE())]
  # ORDER BY System.DateModified DESC
  # Citations: -search-sql-select, -search-sql-folderdepth (SCOPE), -search-sql-like,
  # -search-sql-contains, props-system-kind, props-system-fileextension, -search-sql-dateadd,
  # -search-sql-orderby, all under https://learn.microsoft.com/en-us/windows/win32/search/

  def search(term, roots, kind, since_days, limit) -> list[tuple[str, float]] | None
  # sync; CoInitialize; Dispatch("ADODB.Connection").Open(_CONNECTION); Recordset.Open(sql, conn);
  # iterate rs.EOF / rs.Fields.Item("System.ItemPathDisplay").Value / rs.MoveNext();
  # DateModified -> POSIX float; com_error or missing pywin32 -> None.
  ```
  `since_days` = ceil(seconds / 86400) of the p2 `modified_within` value (Windows Search dates are day-granular in this form; note it in the docstring).
- `tokenpal/actions/find_files.py` — in `_run_backend` (p2's single dispatch point), add the `windows` branch: `hits = await asyncio.to_thread(windows_search.search, ...)`; `None` → the existing walk. Results still pass the shared post-filter.
- `tests/test_util/test_windows_search.py` — new; pure SQL builder tests, runnable on any host:
  1. term `it's 100% [do"ne]` → the LIKE literal is `'%it''s 100[%] [[]do"ne]%'` and the CONTAINS literal is `'"it''s 100% [do""ne]*"'`; nothing is stripped.
  2. two roots → two `SCOPE='file:C:/...'` terms with forward slashes, OR'd, parenthesized.
  3. `kind="image"` → `System.Kind = 'picture'`; `kind="document"` → `System.Kind = 'document'`; `kind="code"` → `System.FileExtension IN (...)` using the p2 extension set; `kind="pdf"` → `IN ('.pdf')`; `kind="any"` → no kind clause.
  4. `since_days=2` → `DATEADD(DAY, -2, GETGMTDATE())`; `None` → absent.
  5. `limit=50` → `SELECT TOP 50`.
- `tests/test_actions/test_find_files.py` — add: on `windows` with `windows_search.search` patched to return `None` → the walk runs (temp tree result appears); patched to return a list with one path outside the roots → that path is filtered out.

## Decisions & findings
### Decision: zero rows is not a fallback trigger  *(status: active)*
- **Rationale:** a folder outside the crawl scope returns no rows with no error (research: consistent with `ISearchCrawlScopeManager::IncludedInCrawlScope` existing, [unverified] in practice). Falling back on zero rows would double the cost of every honest "no matches". The operator's AMD-desktop verification is the place to learn whether the default roots are indexed; if not, the follow-up decides between crawl-scope checks and always-walk.
- **Evidence:** master Background findings (Windows Search).

### Decision: unverified code ships isolated  *(status: active)*
- **Rationale:** one module, one dispatch line, one guard; reverting or fixing it on the AMD desktop touches nothing else.

## Failure modes to anticipate
- `win32com.client.Dispatch` object attribute access is late-bound; `rs.Fields.Item("System.ItemPathDisplay").Value` is the documented ADO shape but the pywin32 mapping is [unverified] here. Keep every COM line inside `search` so a wrong attribute name surfaces as one `com_error`/`AttributeError` caught and logged at DEBUG, returning `None`.
- Microsoft Learn's `DATEADD` example reads `System.DateModified <= DATEADD(DAY, -5, GETGMTDATE())` under a heading about the last five days; this plan uses `>=` (modified after the cutoff), which is what the words mean. The direction is [unverified] until the AMD-desktop run; the p2 walk is the reference behavior to compare against.
- `System.DateModified` comes back as a `pywintypes.datetime`; convert with `.timestamp()` inside a `try`.
- Do not `CoUninitialize` in a pool thread you did not create; pywin32 tolerates repeated `CoInitialize`.

## Done criteria
- The five SQL tests and the two dispatch tests run and pass on this Mac; `pytest` green; `ruff` and `mypy` clean with the import guard (no `type: ignore`).
- `tokenpal/util/windows_search.py` header comment states it is unverified on a real Windows host as of the commit; the follow-up issue is filed at `/plan ship`, not in this phase.
- Observable on this host: `python -c "from tokenpal.util import windows_search; print(windows_search.search('x', [], 'any', None, 5))"` prints `None` (missing pywin32 → graceful) rather than raising.
