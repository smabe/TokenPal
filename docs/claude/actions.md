# Actions / Tools Registry

- `@register_action` is the tool-registry decorator. Each `AbstractAction` subclass declares `action_name`, `description`, `parameters` (JSON Schema), `safe: bool`, `requires_confirm: bool`
- Flags `safe` and `requires_confirm` gate future autonomous LLM tool-calling (safe actions with requires_confirm=False can eventually fire without user prompting)
- Built-ins: `timer`, `system_info`, `open_app`, `do_math`. `do_math` proves the registry end-to-end via the `/math` slash command -- uses an ast walker restricted to `BinOp`/`UnaryOp`/numeric `Constant`, never `eval()`
- `ActionResult.display_url`: when set, the orchestrator surfaces the URL as a clickable link in the chat log via `@click` action (Textual handles the click, opens in browser via `webbrowser.open`). `/ask` sets this to `source_url`
- Tool-use debug logging: `--verbose` shows tool round number, action name, arguments (`fmt_args`), and truncated results. Guarded by `isEnabledFor(DEBUG)` to avoid `json.dumps` overhead in production
