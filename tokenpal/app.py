"""TokenPal application bootstrap — wires discovery, resolution, and runtime."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
import signal
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tokenpal.actions.base import AbstractAction
from tokenpal.actions.registry import discover_actions, resolve_actions
from tokenpal.brain.memory import MemoryStore
from tokenpal.brain.orchestrator import AgentBridge, Brain, ResearchBridge
from tokenpal.brain.personality import PersonalityEngine
from tokenpal.cli import parse_args, print_version, run_check, run_validate
from tokenpal.commands import CommandDispatcher, CommandResult
from tokenpal.config.cloud_writer import (
    set_cloud_deep,
    set_cloud_enabled,
    set_cloud_model,
    set_cloud_plan,
    set_cloud_search,
    set_cloud_search_layer_enabled,
)
from tokenpal.config.idle_tools_writer import (
    set_idle_rule_enabled,
    set_idle_tools_enabled,
)
from tokenpal.config.loader import load_config
from tokenpal.config.schema import DEFAULT_TOOLS, TokenPalConfig
from tokenpal.config.secrets import (
    clear_brave_key,
    clear_cloud_key,
    clear_tavily_key,
    fingerprint,
    get_brave_key,
    get_cloud_key,
    get_tavily_key,
    set_brave_key,
    set_cloud_key,
    set_tavily_key,
)
from tokenpal.config.senses_writer import (
    add_watch_root,
    remove_watch_root,
    set_sense_enabled,
    set_ssid_label,
)
from tokenpal.config.tools_writer import set_enabled_tools
from tokenpal.llm.base import AbstractLLMBackend
from tokenpal.llm.cloud_backend import ALLOWED_MODELS
from tokenpal.llm.registry import discover_backends, resolve_backend
from tokenpal.nl_commands import match_nl_command
from tokenpal.senses.base import AbstractSense
from tokenpal.senses.registry import discover_senses, resolve_senses
from tokenpal.ui.ascii_renderer import BuddyFrame, SpeechBubble
from tokenpal.ui.base import AbstractOverlay
from tokenpal.ui.registry import discover_overlays, resolve_overlay
from tokenpal.util.logging import setup_logging

log = logging.getLogger(__name__)


def main() -> None:
    args = parse_args()

    if args.version:
        print_version()
        return

    # Load config early so data_dir is available for logging
    config = load_config(config_path=args.config)
    data_dir = Path(config.paths.data_dir).expanduser().resolve()

    setup_logging(verbose=args.verbose, log_dir=data_dir / "logs")

    if args.validate:
        sys.exit(run_validate(config_path=args.config))

    if args.check:
        sys.exit(run_check(config_path=args.config))

    # First-run welcome wizard
    if not args.skip_welcome:
        from tokenpal.first_run import needs_first_run, run_wizard
        if needs_first_run(data_dir):
            run_wizard(data_dir)
            # Reload config in case the wizard wrote weather settings
            config = load_config(config_path=args.config)
            data_dir = Path(config.paths.data_dir).expanduser().resolve()

    log.info("TokenPal starting up...")

    # Discover all plugins
    discover_senses(extra_packages=config.plugins.extra_packages)
    discover_backends()
    discover_overlays()
    discover_actions()

    # Session memory (before senses, so productivity sense can use it)
    memory: MemoryStore | None = None
    if config.memory.enabled:
        memory = MemoryStore(
            db_path=data_dir / "memory.db",
            retention_days=config.memory.retention_days,
        )
        memory.setup()
        memory.record_session_start()

    # Resolve implementations for this platform + config
    sense_flags = {f.name: getattr(config.senses, f.name) for f in dataclasses.fields(config.senses)}
    sense_configs: dict[str, dict[str, Any]] = {}
    if memory:
        sense_configs["productivity"] = {"memory_store": memory}
    sense_configs["weather"] = dataclasses.asdict(config.weather)
    sense_configs["network_state"] = dataclasses.asdict(config.network_state)
    sense_configs["filesystem_pulse"] = dataclasses.asdict(config.filesystem_pulse)
    senses = resolve_senses(
        sense_flags=sense_flags,
        sense_overrides=config.plugins.sense_overrides,
        sense_configs=sense_configs,
    )

    llm_config = dataclasses.asdict(config.llm)
    llm_config["server_mode"] = config.server.mode
    # Backends with a throughput estimator persist their EWMAs keyed by
    # (api_url, model) so a known rig doesn't burn its 3-call bootstrap
    # window on every restart. See plans/gpu-scaling.md.
    if memory:
        llm_config["memory_store"] = memory
    llm = resolve_backend(llm_config)

    ui_config = dataclasses.asdict(config.ui)
    overlay = resolve_overlay(ui_config)

    # Load voice profile if configured
    voice = None
    if config.brain.active_voice:
        from tokenpal.tools.voice_profile import load_profile

        try:
            voice = load_profile(config.brain.active_voice, data_dir / "voices")
            log.info("Loaded voice '%s' (%d lines)", voice.character, len(voice.lines))
        except FileNotFoundError:
            log.warning("Voice '%s' not found — using defaults", config.brain.active_voice)

    personality = PersonalityEngine(config.brain.persona_prompt, voice=voice)

    # Actions (LLM-callable tools)
    actions = []
    if config.actions.enabled:
        action_flags = {
            f.name: getattr(config.actions, f.name)
            for f in dataclasses.fields(config.actions)
            if f.name != "enabled"
        }
        actions = resolve_actions(
            enabled=action_flags,
            optin_allowlist=set(config.tools.enabled_tools),
            default_tools=set(DEFAULT_TOOLS),
        )
        if actions:
            log.info("Loaded %d actions: %s", len(actions), [a.action_name for a in actions])

    # Startup summary (before overlay takes over terminal)
    _print_startup_summary(senses, actions, config)

    # Set up the overlay (must happen on main thread)
    overlay.setup()

    def _agent_log(
        text: str, *, markup: bool = False, url: str | None = None,
    ) -> None:
        if url is not None:
            log.info("ui: %s (url=%s)", text, url)
        else:
            log.info("ui: %s", text)
        overlay.schedule_callback(
            lambda t=text, m=markup, u=url: overlay.log_buddy_message(
                t, markup=m, url=u,
            )
        )

    async def _agent_confirm(tool_name: str, args: dict[str, Any]) -> bool:
        from tokenpal.brain.agent import fmt_args

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[bool] = loop.create_future()

        def _resolve(result: bool) -> None:
            if not fut.done():
                loop.call_soon_threadsafe(fut.set_result, result)

        body = (
            f"Tool wants to run:\n\n"
            f"  {tool_name}({fmt_args(args, max_len=200)})\n\n"
            f"Allow this call?"
        )
        opened = overlay.open_confirm_modal(
            title="Agent confirmation",
            body=body,
            on_result=_resolve,
        )
        if not opened:
            # Console/headless overlays can't gate side effects, so deny.
            log.warning("Overlay has no modal support; auto-denying %s", tool_name)
            return False
        return await fut

    # Build the brain
    brain = Brain(
        senses=senses,
        llm=llm,
        ui_callback=lambda text: _overlay_show(overlay, text),
        personality=personality,
        status_callback=lambda text: overlay.schedule_callback(
            lambda t=text: overlay.update_status(t)
        ),
        mood_callback=(
            (lambda role: overlay.schedule_callback(
                lambda r=role: overlay.set_mood(r)
            ))
            if hasattr(overlay, "set_mood") else None
        ),
        memory=memory,
        actions=actions,
        poll_interval_s=config.brain.poll_interval_s,
        comment_cooldown_s=config.brain.comment_cooldown_s,
        interestingness_threshold=config.brain.interestingness_threshold,
        context_max_tokens=config.brain.context_max_tokens,
        sense_intervals=config.brain.sense_intervals,
        conversation=config.conversation,
        agent_bridge=AgentBridge(
            config=config.agent,
            log_callback=_agent_log,
            confirm_callback=_agent_confirm,
        ),
        research_bridge=ResearchBridge(
            config=config.research,
            cloud_config=config.cloud_llm,
            cloud_search_config=config.cloud_search,
        ),
        log_callback=_agent_log,
        idle_tools_config=config.idle_tools,
        target_latency_s=config.llm.target_latency_s,
        min_tokens_per_path=config.llm.min_tokens_per_path,
        session_summary_config=config.session_summary,
        intent_config=config.intent,
        rage_detect_config=config.rage_detect,
        git_nudge_config=config.git_nudge,
    )

    # Load voice-specific buddy art into the overlay
    def _load_voice_art() -> None:
        idle, idle_alt, talking = personality.voice_frames
        if hasattr(overlay, "_voice_name"):
            overlay._voice_name = personality.voice_name
        if idle:
            frames = BuddyFrame.from_voice("custom", idle, idle_alt, talking)
            mood_frame_sets = BuddyFrame.mood_frame_sets(
                personality.voice_mood_frames,
            )
            if hasattr(overlay, "load_voice_frames"):
                overlay.load_voice_frames(frames, mood_frame_sets or None)
        elif hasattr(overlay, "clear_voice_frames"):
            overlay.clear_voice_frames()

    _load_voice_art()

    # Wire the buddy environment overlay (animated weather/idle/sensitive
    # reactions). Textual overlay uses it; other overlays no-op.
    if hasattr(overlay, "set_environment_provider"):
        overlay.set_environment_provider(brain.environment_snapshot)

    # Slash command dispatcher
    dispatcher = CommandDispatcher()

    def _cmd_help(_args: str) -> CommandResult:
        return CommandResult(dispatcher.help_text())

    def _cmd_clear(_args: str) -> CommandResult:
        overlay.schedule_callback(overlay.hide_speech)
        overlay.clear_log()
        brain.reset_conversation()
        return CommandResult("")

    def _cmd_mood(_args: str) -> CommandResult:
        return CommandResult(f"Mood: {personality.mood}")

    def _cmd_status(_args: str) -> CommandResult:
        sense_names = ", ".join(s.sense_name for s in senses)
        action_names = ", ".join(a.action_name for a in actions)
        return CommandResult(
            f"Model: {llm.model_name} | "
            f"Senses: {sense_names} | "
            f"Actions: {action_names or 'none'}"
        )

    def _cmd_model(args: str) -> CommandResult:
        prev_model = llm.model_name
        result = _handle_model_command(args, llm, overlay, brain, config)
        if llm.model_name != prev_model:
            brain.reset_conversation()
        return result

    def _cmd_voice(args: str) -> CommandResult:
        if not args.strip() and _open_voice_modal():
            return CommandResult("")
        prev_voice = personality.voice_name
        result = _handle_voice_command(
            args, personality, data_dir / "voices", overlay, brain,
            llm, config, on_voice_loaded=_load_voice_art,
        )
        if personality.voice_name != prev_voice:
            brain.reset_conversation()
        return result

    def _run_voice_action(action: Callable[[], CommandResult]) -> None:
        """Run a voice helper and preserve /voice slash semantics — log the
        command result and reset conversation on voice change."""
        prev_voice = personality.voice_name
        result = action()
        if personality.voice_name != prev_voice:
            brain.reset_conversation()
        if result.message:
            overlay.log_buddy_message(result.message)

    def _open_voice_modal() -> bool:
        from tokenpal.tools.voice_profile import (
            list_profile_summaries,
            slugify,
        )
        from tokenpal.ui.voice_modal import (
            VoiceModalResult,
            VoiceModalState,
        )

        voices_dir = data_dir / "voices"
        saved = list_profile_summaries(voices_dir)
        active_summary = None
        if personality.voice_name:
            active_slug = slugify(personality.voice_name)
            active_summary = next(
                (s for s in saved if s.slug == active_slug), None,
            )
        cloud_ready = False
        if config.cloud_llm.enabled:
            try:
                from tokenpal.config.secrets import get_cloud_key
                cloud_ready = bool(get_cloud_key())
            except Exception:
                cloud_ready = False
        state = VoiceModalState(
            active_voice=active_summary, saved=saved,
            cloud_ready=cloud_ready,
            voice_classifier_on=config.cloud_llm.voice_classifier,
        )

        def on_result(result: VoiceModalResult | None) -> None:
            if result is None:
                return
            action = result.action
            payload = result.payload
            if action == "off":
                _run_voice_action(lambda: _voice_off(personality, llm, config))
            elif action == "switch":
                name = payload.get("name", "")
                if name:
                    _run_voice_action(
                        lambda: _voice_switch(
                            name, personality, voices_dir,
                            llm, config, _load_voice_art,
                        )
                    )
            elif action == "train":
                wiki = payload.get("wiki", "")
                character = payload.get("character", "")
                if wiki and character:
                    _run_voice_action(
                        lambda: _start_voice_training(
                            f"{wiki} {character}",
                            personality, voices_dir, overlay, brain,
                            on_voice_loaded=_load_voice_art,
                        )
                    )
            elif action == "finetune":
                name = payload.get("name", "")
                if name:
                    _run_voice_action(
                        lambda: _start_voice_finetune(
                            name, personality, voices_dir, overlay,
                            brain, llm, config,
                        )
                    )
            elif action == "finetune_setup":
                _run_voice_action(
                    lambda: _start_finetune_setup(overlay, config)
                )
            elif action == "regenerate":
                def _do_regen() -> None:
                    _run_voice_action(
                        lambda: _start_voice_regenerate(
                            "", personality, voices_dir, overlay,
                            on_voice_loaded=_load_voice_art,
                        )
                    )
                if not overlay.open_confirm_modal(
                    "Regenerate all voice assets?",
                    "This runs a ~60s LLM job. Continue?",
                    lambda ok: _do_regen() if ok else None,
                ):
                    _do_regen()
            elif action == "ascii":
                _run_voice_action(
                    lambda: _start_voice_regenerate_ascii(
                        "", personality, voices_dir, overlay,
                        on_voice_loaded=_load_voice_art,
                    )
                )
            elif action == "import":
                path = payload.get("path", "")
                if path:
                    _run_voice_action(
                        lambda: _import_gguf(
                            path, personality, voices_dir, overlay, llm,
                        )
                    )
            elif action == "cloud_classifier":
                from tokenpal.config.cloud_writer import (
                    set_cloud_voice_classifier,
                )
                enabled = payload.get("enabled") == "true"
                set_cloud_voice_classifier(enabled)
                config.cloud_llm.voice_classifier = enabled
                status = "Haiku" if enabled else "local model"
                overlay.log_buddy_message(
                    f"ASCII classifier will use {status} on next train."
                )

        return overlay.open_voice_modal(state, on_result)

    def _cmd_server(args: str) -> CommandResult:
        parts = args.split(maxsplit=1)
        subcmd = parts[0].lower() if parts else "status"

        if subcmd == "status":
            state = "connected" if llm.is_reachable else "unreachable"
            return CommandResult(f"Server: {llm.api_url} ({state})")

        if subcmd == "switch":
            if len(parts) < 2:
                return CommandResult("Usage: /server switch <host|local|remote>")
            target = parts[1].strip()
            if target == "local":
                url = "http://localhost:11434/v1"
            elif target == "remote":
                host = config.server.host if config.server.host != "127.0.0.1" else "localhost"
                port = config.server.port
                url = f"http://{host}:{port}/v1"
            elif not target.startswith("http"):
                url = f"http://{target}:8585/v1"
            else:
                url = target.rstrip("/")
                if not url.endswith("/v1"):
                    url += "/v1"

            llm.set_api_url(url)

            # Restore the remembered model / max_tokens for this server, if any.
            # Falls through to whatever the backend already has (global defaults)
            # when we've never seen this server before.
            from tokenpal.config.toml_writer import canon_server_url

            key = canon_server_url(url)
            restored_model: str | None = None
            remembered_model = config.llm.per_server_models.get(key)
            if remembered_model and remembered_model != llm.model_name:
                llm.set_model(remembered_model)
                config.llm.model_name = remembered_model  # status bar mirror
                restored_model = remembered_model
            remembered_tokens = config.llm.per_server_max_tokens.get(key)
            if remembered_tokens and hasattr(llm, "set_max_tokens"):
                llm.set_max_tokens(int(remembered_tokens))

            async def _setup_then_remember() -> None:
                # Run the connect probe first so any auto-adopted model
                # name is the one we persist. Without this, switching to
                # a brand-new server never lands its URL in
                # per_server_models, and the options modal hides it as
                # soon as the user navigates away.
                await llm.setup()
                try:
                    from tokenpal.config.toml_writer import (
                        remember_server_model,
                    )

                    if llm.model_name:
                        remember_server_model(llm.api_url, llm.model_name)
                        config.llm.per_server_models[
                            canon_server_url(llm.api_url)
                        ] = llm.model_name
                except Exception:
                    log.exception(
                        "Failed to persist visited server %s", llm.api_url,
                    )

            asyncio.run_coroutine_threadsafe(
                _setup_then_remember(), brain._loop,
            )
            try:
                from tokenpal.config.toml_writer import update_config

                def _persist(data: dict[str, Any]) -> None:
                    data.setdefault("llm", {})["api_url"] = url

                update_config(_persist)
            except Exception:
                log.exception("Failed to persist /server switch to config.toml")
            suffix = f" [model: {restored_model}]" if restored_model else ""
            return CommandResult(f"Switching to {url} (persisted).{suffix}")

        return CommandResult("Usage: /server [status|switch <host|local|remote>]")

    def _cmd_zip(args: str) -> CommandResult:
        return _handle_zip_command(args)

    def _cmd_cloud(args: str) -> CommandResult:
        parts = args.split(maxsplit=1)
        subcmd = parts[0].lower() if parts else ""
        # Bare /cloud opens the modal picker (covers all options in one UI).
        # Subcommands still work for terse / scriptable use, so power users
        # and tests can skip the modal with /cloud status|enable|disable|etc.
        if subcmd == "" and _open_cloud_modal():
            return CommandResult("")
        return _handle_cloud_command(args, config)

    def _open_cloud_modal() -> bool:
        from tokenpal.ui.cloud_modal import CloudModalResult, CloudModalState

        cfg = config.cloud_llm
        cs = config.cloud_search
        stored_key = get_cloud_key()
        tavily_key = get_tavily_key()
        brave_key = get_brave_key()
        state = CloudModalState(
            enabled=cfg.enabled,
            research_synth=cfg.research_synth,
            research_plan=cfg.research_plan,
            research_deep=cfg.research_deep,
            research_search=cfg.research_search,
            model=cfg.model,
            key_fingerprint=fingerprint(stored_key) if stored_key else None,
            tavily_enabled=cs.enabled,
            tavily_search_depth=cs.search_depth,
            tavily_key_fingerprint=(
                fingerprint(tavily_key) if tavily_key else None
            ),
            brave_key_fingerprint=(
                fingerprint(brave_key) if brave_key else None
            ),
            refine_max_supplemental=cs.refine_max_supplemental,
        )

        def on_save(result: CloudModalResult | None) -> None:
            if result is None:
                return
            _apply_cloud_modal_result(result, config)

        return overlay.open_cloud_modal(state, on_save)

    def _cmd_chatlog(_args: str) -> CommandResult:
        overlay.schedule_callback(overlay.toggle_chat_log)
        return CommandResult("")

    def _open_options_modal() -> bool:
        from tokenpal.config.toml_writer import canon_server_url
        from tokenpal.ui.options_modal import (
            OptionsModalResult,
            OptionsModalState,
            ServerEntry,
        )

        cl = config.chat_log

        # Build known-server list: configured local + configured remote +
        # every key already in per_server_models. Dedup via canon_server_url.
        local_url = "http://localhost:11434/v1"
        remote_host = (
            config.server.host if config.server.host != "127.0.0.1"
            else "localhost"
        )
        remote_url = f"http://{remote_host}:{config.server.port}/v1"
        current_key = canon_server_url(llm.api_url)

        seen: dict[str, ServerEntry] = {}

        def _add(url: str, label: str) -> None:
            key = canon_server_url(url)
            if key in seen:
                return
            remembered = config.llm.per_server_models.get(key)
            model: str | None = remembered
            if model is None and key == current_key:
                # Active server fallback so a fresh machine shows something.
                model = llm.model_name or None
            seen[key] = ServerEntry(url=key, label=label, model=model)

        def _label_from_url(url: str) -> str:
            from urllib.parse import urlparse
            host = urlparse(url).hostname or url
            return host

        _add(local_url, "local")
        _add(remote_url, "remote")
        for persisted_key in config.llm.per_server_models:
            _add(persisted_key, _label_from_url(persisted_key))
        # Always include the currently-active server, even if the user
        # hasn't picked a model yet (no per_server_models entry would
        # exist) and it isn't one of the configured local/remote URLs.
        # Without this, /server switch <custom-host> hides the active
        # server from the picker until the user runs /model.
        if current_key and current_key not in seen:
            _add(llm.api_url, _label_from_url(llm.api_url))

        state = OptionsModalState(
            max_persisted=cl.max_persisted,
            persist_enabled=cl.persist,
            current_api_url=llm.api_url,
            known_servers=tuple(seen.values()),
            current_model=llm.model_name or "",
            available_models=tuple(getattr(llm, "available_models", ())),
            weather_label=config.weather.location_label,
            current_wifi_label="",
        )

        def on_save(result: OptionsModalResult | None) -> None:
            if result is None:
                return

            nav = result.navigate_to
            if nav == "cloud":
                _open_cloud_modal()
                return
            if nav == "senses":
                flag_fields = [
                    f.name for f in dataclasses.fields(config.senses)
                ]
                _open_senses_modal(flag_fields)
                return
            if nav == "tools":
                _open_tools_modal()
                return
            if nav == "voice":
                _open_voice_modal()
                return

            if result.switch_server_to:
                target = result.switch_server_to.strip()
                if target:
                    res = _cmd_server(f"switch {target}")
                    if res.message:
                        overlay.log_buddy_message(res.message)
                # Fall through — a combined server+model pick still needs
                # the model swap. _cmd_server already restored the
                # per-server remembered model; an explicit pending pick
                # just below overrides it.

            if result.switch_model_to:
                target = result.switch_model_to.strip()
                if target and target != (llm.model_name or ""):
                    res = _cmd_model(target)
                    if res.message:
                        overlay.log_buddy_message(res.message)

            if result.switch_server_to or result.switch_model_to:
                return

            if result.set_zip:
                res = _cmd_zip(result.set_zip.strip())
                if res.message:
                    overlay.log_buddy_message(res.message)
                return

            if result.set_wifi_label:
                res = _cmd_wifi(f"label {result.set_wifi_label.strip()}")
                if res.message:
                    overlay.log_buddy_message(res.message)
                return

            # Navigation was None — apply field edits.
            from tokenpal.config.chatlog_writer import (
                clamp_max_persisted,
                set_max_persisted,
            )

            new_max = clamp_max_persisted(result.max_persisted)
            if new_max != cl.max_persisted:
                try:
                    set_max_persisted(new_max)
                    cl.max_persisted = new_max
                    if memory is not None and cl.persist:
                        memory.set_chat_log_max_persisted(new_max)
                    overlay.log_buddy_message(
                        f"/options: saved max_persisted = {new_max}."
                    )
                except OSError as e:
                    overlay.log_buddy_message(
                        f"/options: could not write config: {e}"
                    )
            if result.clear_history:
                if memory is not None:
                    try:
                        memory.clear_chat_log()
                    except Exception as e:
                        log.warning("clear_chat_log failed: %s", e)
                overlay.clear_log()
                overlay.log_buddy_message("/options: chat history cleared.")

        return overlay.open_options_modal(state, on_save)

    def _cmd_options(_args: str) -> CommandResult:
        if _open_options_modal():
            return CommandResult("")
        return CommandResult(
            "/options: modal not available on this overlay."
        )

    def _cmd_ask(args: str) -> CommandResult:
        from tokenpal.config.consent import Category, has_consent

        query = args.strip()
        if not query:
            return CommandResult("Usage: /ask <question>")
        if not has_consent(Category.WEB_FETCHES):
            return CommandResult(
                "/ask needs web_fetches consent. Run /consent."
            )

        def _run_ask() -> None:
            from rich.markup import escape as _esc

            from tokenpal.brain.personality import contains_sensitive_content_term
            from tokenpal.senses.web_search.client import LOG_TRUNCATE_CHARS, search

            try:
                result = search(
                    query,
                    backend=config.web_search.backend,
                    brave_api_key=config.web_search.brave_api_key,
                )
            except Exception:
                log.exception("/ask search failed")
                overlay.schedule_callback(
                    lambda: overlay.log_buddy_message(
                        f"/ask → search failed for '{_esc(query[:LOG_TRUNCATE_CHARS])}'"
                    )
                )
                return

            if result is None:
                overlay.schedule_callback(
                    lambda: overlay.log_buddy_message(
                        f"/ask → no result for '{_esc(query[:LOG_TRUNCATE_CHARS])}'"
                    )
                )
                return

            if (
                contains_sensitive_content_term(result.text)
                or contains_sensitive_content_term(result.title)
            ):
                log.debug(
                    "/ask result filtered (sensitive term) for query: %s",
                    query[:LOG_TRUNCATE_CHARS],
                )
                overlay.schedule_callback(
                    lambda: overlay.log_buddy_message(
                        "/ask → result filtered (sensitive term)"
                    )
                )
                return

            raw = f"/ask -> {_esc(result.title[:200])}\n{_esc(result.text[:500])}"
            src = result.source_url
            overlay.schedule_callback(
                lambda r=raw, u=src: overlay.log_buddy_message(r, markup=True, url=u)
            )

            # Wrap untrusted text in delimiters — basic prompt-injection mitigation.
            backend_name = result.backend.replace('"', "")
            prompt = (
                f"[User ran /ask: {query}]\n"
                f"<search_result backend=\"{backend_name}\">\n"
                f"{result.text}\n"
                f"</search_result>\n"
                "React in character — riff on the result, "
                "ask a follow-up if you want."
            )
            brain.submit_user_input(prompt)

        threading.Thread(target=_run_ask, daemon=True, name="ask-cmd").start()
        return CommandResult(f"Searching: {query[:80]}...")

    def _cmd_gh(args: str) -> CommandResult:
        parts = args.split(maxsplit=1)
        subcmd = parts[0].lower() if parts else "log"

        if subcmd not in ("log", "prs", "issues"):
            return CommandResult("Usage: /gh [log|prs|issues]")

        def _run_gh() -> None:
            result = _handle_gh_command(subcmd, parts[1] if len(parts) > 1 else "")
            if result.error:
                _overlay_show(overlay, result.error)
                return
            output = result.message
            overlay.schedule_callback(lambda: overlay.log_buddy_message(output))
            prompts = {
                "log": "React to these commits — what's been going on in this project?",
                "prs": "Comment on these PRs.",
                "issues": "Comment on these issues.",
            }
            prompt = f"[The user ran /gh {subcmd}:\n{output}\n]\n{prompts[subcmd]}"
            brain.submit_user_input(prompt)

        threading.Thread(target=_run_gh, daemon=True, name="gh-cmd").start()
        return CommandResult("")

    def _open_senses_modal(flag_fields: list[str]) -> bool:
        from tokenpal.ui.selection_modal import SelectionGroup, SelectionItem

        items = tuple(
            SelectionItem(
                value=name,
                label=name,
                initial=getattr(config.senses, name),
            )
            for name in flag_fields
        )
        group = SelectionGroup(
            title="Senses",
            items=items,
            help_text="Toggle senses. Restart required to apply.",
        )

        def on_save(result: dict[str, list[str]] | None) -> None:
            if result is None:
                overlay.log_buddy_message("/senses: cancelled.")
                return
            selected = set(result.get("Senses", []))
            failures: list[str] = []
            changes = 0
            for name in flag_fields:
                want = name in selected
                if getattr(config.senses, name) == want:
                    continue
                try:
                    set_sense_enabled(name, want)
                    changes += 1
                except OSError as e:
                    failures.append(f"{name}: {e}")
            if failures:
                overlay.log_buddy_message(
                    "/senses: some writes failed — " + "; ".join(failures)
                )
            elif changes == 0:
                overlay.log_buddy_message("/senses: no changes.")
            else:
                overlay.log_buddy_message(
                    f"/senses: saved {changes} change(s). Restart TokenPal to apply."
                )

        return overlay.open_selection_modal("Senses", [group], on_save)

    def _open_tools_modal() -> bool:
        from tokenpal.actions.catalog import SECTIONS, default_tool_names
        from tokenpal.ui.selection_modal import SelectionGroup, SelectionItem

        enabled = set(config.tools.enabled_tools)
        defaults = default_tool_names()
        groups: list[SelectionGroup] = []
        for section in SECTIONS:
            items: list[SelectionItem] = []
            for entry in section.entries:
                is_default = entry.name in defaults
                items.append(
                    SelectionItem(
                        value=entry.name,
                        label=f"{entry.name} — {entry.blurb}",
                        initial=is_default or entry.name in enabled,
                        locked=is_default,
                    )
                )
            if not items:
                items.append(
                    SelectionItem(
                        value=f"__placeholder_{section.title.lower()}",
                        label="(nothing yet — lands in a later phase)",
                        initial=False,
                        locked=True,
                    )
                )
            groups.append(
                SelectionGroup(
                    title=section.title,
                    items=tuple(items),
                    help_text=section.description,
                )
            )

        def on_save(result: dict[str, list[str]] | None) -> None:
            if result is None:
                overlay.log_buddy_message("/tools: cancelled.")
                return
            selected: set[str] = set()
            for values in result.values():
                for v in values:
                    if v.startswith("__placeholder_") or v in defaults:
                        continue
                    selected.add(v)
            try:
                path = set_enabled_tools(selected)
            except OSError as e:
                overlay.log_buddy_message(f"/tools: could not write config: {e}")
                return
            overlay.log_buddy_message(
                f"/tools: saved {len(selected)} opt-in tool(s) to {path.name}. "
                "Restart TokenPal to apply."
            )

        return overlay.open_selection_modal("Tools", groups, on_save)

    def _cmd_summary(args: str) -> CommandResult:
        if brain._loop is None:
            return CommandResult("/summary: brain loop not running yet.")
        which = (args.strip().lower() or "yesterday")
        if which not in ("today", "yesterday"):
            return CommandResult("Usage: /summary [today|yesterday]")
        future = asyncio.run_coroutine_threadsafe(
            brain.run_eod_summary(which), brain._loop
        )
        try:
            message = future.result(timeout=30)
        except TimeoutError:
            return CommandResult("/summary: LLM timed out.")
        except Exception as e:
            log.exception("/summary failed")
            return CommandResult(f"/summary failed: {e}")
        if message is None:
            # Bubble already emitted.
            return CommandResult("")
        return CommandResult(message)

    def _cmd_intent(args: str) -> CommandResult:
        from tokenpal.brain.intent import IntentError

        if brain.intent is None:
            return CommandResult("/intent needs memory enabled.")

        parts = args.split(maxsplit=1)
        subcmd = parts[0].lower() if parts else ""

        if subcmd in ("", "status"):
            active = brain.intent.get_raw()
            if active is None:
                return CommandResult(
                    "No intent set. Try /intent finish the auth PR."
                )
            age_s = time.time() - active.started_at
            age_min = int(age_s / 60)
            max_age_h = int(config.intent.max_age_s / 3600)
            expired = age_s > config.intent.max_age_s
            note = " (expired)" if expired else ""
            return CommandResult(
                f"Intent{note}: {active.text}\n"
                f"Set {age_min}m ago. Expires after {max_age_h}h of silence."
            )

        if subcmd == "clear":
            cleared = brain.intent.clear()
            return CommandResult(
                "Intent cleared." if cleared else "No active intent to clear."
            )

        # Anything else: treat the whole args as the intent text
        try:
            active = brain.intent.set(args.strip())
        except IntentError as e:
            return CommandResult(f"/intent: {e}")
        return CommandResult(f"Intent set: {active.text}")

    def _cmd_agent(args: str) -> CommandResult:
        goal = args.strip()
        if not goal:
            return CommandResult("Usage: /agent <goal>")
        if "agent_mode" not in config.tools.enabled_tools:
            return CommandResult(
                "/agent is off. Enable 'agent_mode' in /tools and restart."
            )
        if brain.agent_running:
            return CommandResult("/agent: already running. Wait for the current goal to finish.")
        brain.submit_agent_goal(goal)
        return CommandResult(f"Agent started: {goal[:60]}")

    def _cmd_refine(args: str) -> CommandResult:
        follow_up = args.strip()
        if not follow_up:
            return CommandResult(
                "Usage: /refine <follow-up question>. Re-analyzes the most "
                "recent research's sources with cloud synth; may fetch more "
                "sources if the cached pool doesn't cover the follow-up."
            )
        gate_err = _refine_gate_error(config)
        if gate_err is not None:
            return CommandResult(gate_err)
        brain.submit_refine_question(follow_up)
        return CommandResult(f"Refining: {follow_up[:60]}")

    def _cmd_research(args: str) -> CommandResult:
        from tokenpal.config.consent import Category, has_consent

        question = args.strip()
        if not question:
            return CommandResult("Usage: /research <question>")
        if "research_mode" not in config.tools.enabled_tools:
            return CommandResult(
                "/research is off. Enable 'research_mode' in /tools and restart."
            )
        if not has_consent(Category.RESEARCH_MODE) or not has_consent(Category.WEB_FETCHES):
            return CommandResult(
                "/research needs research_mode + web_fetches consent. Run /consent."
            )
        if brain.research_running:
            return CommandResult("/research: already running. Wait for it to finish.")
        brain.submit_research_question(question)
        return CommandResult(f"Research started: {question[:60]}")

    def _cmd_tools(args: str) -> CommandResult:
        from tokenpal.actions.catalog import SECTIONS, default_tool_names, find_entry
        from tokenpal.actions.registry import _ACTION_REGISTRY

        parts = args.split(maxsplit=1)
        subcmd = parts[0].lower() if parts else ""

        if subcmd == "":
            if _open_tools_modal():
                return CommandResult("")
            subcmd = "list"

        if subcmd == "list":
            defaults = default_tool_names()
            enabled = set(config.tools.enabled_tools)
            rows: list[str] = []
            for section in SECTIONS:
                rows.append(f"  [{section.title}]")
                if not section.entries:
                    rows.append("    (none yet)")
                for entry in section.entries:
                    is_default = entry.name in defaults
                    is_on = is_default or entry.name in enabled
                    mark = "on " if is_on else "off"
                    tag = " (default)" if is_default else ""
                    rows.append(f"    {mark}  {entry.name}{tag}")
            return CommandResult("Tools:\n" + "\n".join(rows))

        if subcmd == "describe":
            target = parts[1].strip() if len(parts) > 1 else ""
            if not target:
                return CommandResult("Usage: /tools describe <name>")
            match = find_entry(target)
            if match is None:
                return CommandResult(f"Unknown tool '{target}'.")
            entry, section = match
            cls = _ACTION_REGISTRY.get(entry.name)
            lines = [
                f"{entry.name} — {entry.blurb}",
                f"  section: {section.title} ({entry.kind})",
            ]
            if entry.consent_category:
                lines.append(f"  consent: {entry.consent_category}")
            if cls is None:
                lines.append("  (no implementation registered yet)")
            else:
                lines.append(f"  platforms: {', '.join(cls.platforms)}")
                lines.append(
                    f"  safe: {cls.safe}, requires_confirm: {cls.requires_confirm}"
                )
                if cls.rate_limit is not None:
                    lines.append(
                        f"  rate_limit: {cls.rate_limit.max_calls}/"
                        f"{cls.rate_limit.window_s:g}s"
                    )
                if not cls.cacheable:
                    lines.append("  cacheable: false")
            return CommandResult("\n".join(lines))

        return CommandResult(
            "Usage: /tools [list|describe <name>] — omit args to open picker."
        )

    def _cmd_consent(args: str) -> CommandResult:
        from tokenpal.config.consent import ALL_CATEGORIES, load_consent, save_consent
        from tokenpal.ui.selection_modal import SelectionGroup, SelectionItem

        parts = args.split(maxsplit=1)
        subcmd = parts[0].lower() if parts else ""

        if subcmd in ("", "edit"):
            current = load_consent()
            items = tuple(
                SelectionItem(value=c, label=c, initial=current.get(c, False))
                for c in ALL_CATEGORIES
            )
            group = SelectionGroup(
                title="Consent",
                items=items,
                help_text="Per-category permissions. Stored at ~/.tokenpal/.consent.json.",
            )

            def on_save(result: dict[str, list[str]] | None) -> None:
                if result is None:
                    overlay.log_buddy_message("/consent: cancelled.")
                    return
                granted = set(result.get("Consent", []))
                flags = {c: (c in granted) for c in ALL_CATEGORIES}
                try:
                    path = save_consent(flags)
                except OSError as e:
                    overlay.log_buddy_message(f"/consent: could not write: {e}")
                    return
                overlay.log_buddy_message(
                    f"/consent: saved to {path.name}. {sum(flags.values())} granted."
                )

            if overlay.open_selection_modal("Consent", [group], on_save):
                return CommandResult("")
            subcmd = "list"

        if subcmd == "list":
            current = load_consent()
            rows = [f"  {'yes' if current[c] else 'no '}  {c}" for c in ALL_CATEGORIES]
            return CommandResult("Consent:\n" + "\n".join(rows))

        return CommandResult("Usage: /consent [list|edit] — omit args to open picker.")

    def _cmd_senses(args: str) -> CommandResult:
        parts = args.split(maxsplit=1)
        subcmd = parts[0].lower() if parts else ""
        target = parts[1].strip() if len(parts) > 1 else ""

        flag_fields = [f.name for f in dataclasses.fields(config.senses)]

        # No args → try the modal picker first, fall back to plain list.
        if subcmd == "":
            if _open_senses_modal(flag_fields):
                return CommandResult("")
            subcmd = "list"

        if subcmd == "list":
            active = {s.sense_name for s in senses}
            rows = []
            for name in flag_fields:
                enabled = getattr(config.senses, name)
                mark = "on " if enabled else "off"
                loaded = " (loaded)" if name in active else ""
                rows.append(f"  {mark}  {name}{loaded}")
            return CommandResult("Senses:\n" + "\n".join(rows))

        if subcmd in ("enable", "disable"):
            if not target:
                return CommandResult(f"Usage: /senses {subcmd} <sense_name>")
            if target not in flag_fields:
                return CommandResult(
                    f"Unknown sense '{target}'. Try /senses list."
                )
            try:
                path = set_sense_enabled(target, subcmd == "enable")
            except OSError as e:
                return CommandResult(f"/senses: could not write config: {e}")
            verb = "enabled" if subcmd == "enable" else "disabled"
            return CommandResult(
                f"{target} {verb} in {path.name}. "
                "Restart TokenPal for the change to take effect."
            )

        return CommandResult("Usage: /senses [list|enable <name>|disable <name>]")

    def _cmd_idle_tools(args: str) -> CommandResult:
        from datetime import datetime

        from tokenpal.brain.idle_rules import M1_RULES, rule_by_name
        from tokenpal.brain.idle_tools import build_context
        from tokenpal.config.consent import Category, has_consent

        parts = args.split(maxsplit=1)
        subcmd = parts[0].lower() if parts else "list"
        target = parts[1].strip() if len(parts) > 1 else ""

        if subcmd == "list":
            rows = [
                f"  global:  {'on ' if config.idle_tools.enabled else 'off'}  "
                f"(cooldown {int(config.idle_tools.global_cooldown_s)}s, "
                f"cap {config.idle_tools.max_per_hour}/h)"
            ]
            for rule in M1_RULES:
                on = config.idle_tools.rules.get(rule.name, rule.enabled_default)
                mark = "on " if on else "off"
                rows.append(f"  {mark}  {rule.name} — {rule.description}")
            return CommandResult("Idle tools:\n" + "\n".join(rows))

        if subcmd in ("on", "off"):
            try:
                path = set_idle_tools_enabled(subcmd == "on")
            except OSError as e:
                return CommandResult(f"/idle_tools: could not write config: {e}")
            return CommandResult(
                f"idle_tools turned {subcmd} in {path.name}. "
                "Restart TokenPal for the change to take effect."
            )

        if subcmd in ("enable", "disable"):
            if not target:
                return CommandResult(f"Usage: /idle_tools {subcmd} <rule_name>")
            if rule_by_name(target) is None:
                return CommandResult(
                    f"Unknown idle rule '{target}'. Try /idle_tools list."
                )
            try:
                path = set_idle_rule_enabled(target, subcmd == "enable")
            except OSError as e:
                return CommandResult(f"/idle_tools: could not write config: {e}")
            verb = "enabled" if subcmd == "enable" else "disabled"
            return CommandResult(
                f"{target} {verb} in {path.name}. "
                "Restart TokenPal for the change to take effect."
            )

        if subcmd == "roll":
            if not target:
                return CommandResult(
                    "Usage: /idle_tools roll <rule_name> (forces a fire "
                    "bypassing predicates + cooldowns)"
                )
            rule = rule_by_name(target)
            if rule is None:
                return CommandResult(
                    f"Unknown idle rule '{target}'. Try /idle_tools list."
                )

            def _run_roll() -> None:
                async def _go() -> None:
                    ctx = build_context(
                        now=datetime.now(),
                        session_minutes=1,
                        first_session_of_day=True,
                        active_readings={},
                        mood=str(personality.mood),
                        time_since_last_comment_s=9999.0,
                        consent_web_fetches=has_consent(Category.WEB_FETCHES),
                    )
                    result = await brain._idle_tools.force_fire(rule.name, ctx)
                    if result is None:
                        overlay.log_buddy_message(
                            f"/idle_tools: {rule.name} fired but tool returned nothing."
                        )
                        return
                    await brain._generate_tool_riff(
                        brain._context.snapshot(), result,
                    )

                try:
                    asyncio.run_coroutine_threadsafe(
                        _go(), brain._loop,
                    ).result(timeout=30)
                except Exception as e:
                    overlay.log_buddy_message(f"/idle_tools roll failed: {e}")

            threading.Thread(target=_run_roll, daemon=True).start()
            return CommandResult(f"Rolling {target}...")

        return CommandResult(
            "Usage: /idle_tools [list|on|off|enable <rule>|disable <rule>|roll <rule>]"
        )

    def _cmd_wifi(args: str) -> CommandResult:
        parts = args.split(maxsplit=1)
        subcmd = parts[0].lower() if parts else ""
        label = parts[1].strip() if len(parts) > 1 else ""

        if subcmd != "label" or not label:
            return CommandResult("Usage: /wifi label <friendly name>")

        # read_ssid() runs a platform subprocess (up to 2s timeout) — keep it
        # off the UI thread so the input bar doesn't freeze if the shim hangs.
        def _run_wifi() -> None:
            from tokenpal.senses.network_state.sense import get_current_ssid_hash

            ssid_hash = get_current_ssid_hash()
            if not ssid_hash:
                msg = "/wifi: couldn't read current SSID (not on wifi, or platform shim missing)."
            else:
                try:
                    path = set_ssid_label(ssid_hash, label)
                except (OSError, ValueError) as e:
                    msg = f"/wifi: could not write config: {e}"
                else:
                    hint = "" if config.senses.network_state else (
                        " (also run /senses enable network_state to turn the sense on)"
                    )
                    msg = (
                        f"Labeled current wifi as '{label}' in {path.name}. "
                        f"Restart TokenPal to apply.{hint}"
                    )
            overlay.schedule_callback(lambda m=msg: overlay.log_buddy_message(m))

        threading.Thread(target=_run_wifi, daemon=True, name="wifi-cmd").start()
        return CommandResult(f"Labeling current wifi as '{label}'...")

    def _cmd_watch(args: str) -> CommandResult:
        from tokenpal.config.paths import default_watch_roots

        parts = args.split(maxsplit=1)
        subcmd = parts[0].lower() if parts else ""
        target = parts[1].strip() if len(parts) > 1 else ""

        configured = [Path(r).expanduser() for r in config.filesystem_pulse.roots]
        effective = configured if configured else default_watch_roots()

        if subcmd in ("", "list"):
            source = "config.toml" if configured else "defaults"
            rows = [f"  {p}" for p in effective] or ["  (none)"]
            return CommandResult(
                f"Watch roots ({source}):\n" + "\n".join(rows)
            )

        if subcmd in ("add", "remove"):
            if not target:
                return CommandResult(f"Usage: /watch {subcmd} <path>")
            abs_path = str(Path(target).expanduser().resolve())
            if subcmd == "add" and not Path(abs_path).is_dir():
                return CommandResult(f"/watch: not a directory: {abs_path}")
            try:
                path = add_watch_root(abs_path) if subcmd == "add" else remove_watch_root(abs_path)
            except OSError as e:
                return CommandResult(f"/watch: could not write config: {e}")
            verb = "added" if subcmd == "add" else "removed"
            hint = "" if config.senses.filesystem_pulse else (
                " (also run /senses enable filesystem_pulse to turn the sense on)"
            )
            return CommandResult(
                f"{verb} {abs_path} in {path.name}. "
                f"Restart TokenPal to apply.{hint}"
            )

        return CommandResult("Usage: /watch [list|add <path>|remove <path>]")

    def _cmd_math(args: str) -> CommandResult:
        expr = args.strip()
        if not expr:
            return CommandResult("Usage: /math <expression>")
        from tokenpal.actions.do_math import MathError, safe_eval
        try:
            result = safe_eval(expr)
        except MathError as e:
            return CommandResult(f"/math: {e}")
        except ZeroDivisionError:
            return CommandResult("/math: division by zero")
        except OverflowError:
            return CommandResult("/math: result too large")
        return CommandResult(f"{expr} = {result}")

    dispatcher.register("ask", _cmd_ask)
    dispatcher.register("gh", _cmd_gh)
    dispatcher.register("math", _cmd_math)
    dispatcher.register("senses", _cmd_senses)
    dispatcher.register("idle_tools", _cmd_idle_tools)
    dispatcher.register("wifi", _cmd_wifi)
    dispatcher.register("watch", _cmd_watch)
    dispatcher.register("help", _cmd_help)
    dispatcher.register("clear", _cmd_clear)
    dispatcher.register("chatlog", _cmd_chatlog)
    dispatcher.register("mood", _cmd_mood)
    dispatcher.register("status", _cmd_status)
    dispatcher.register("model", _cmd_model)
    dispatcher.register("voice", _cmd_voice)
    dispatcher.register("server", _cmd_server)
    dispatcher.register("zip", _cmd_zip)
    dispatcher.register("tools", _cmd_tools)
    dispatcher.register("consent", _cmd_consent)
    dispatcher.register("agent", _cmd_agent)
    dispatcher.register("research", _cmd_research)
    dispatcher.register("refine", _cmd_refine)
    dispatcher.register("intent", _cmd_intent)
    dispatcher.register("summary", _cmd_summary)
    dispatcher.register("cloud", _cmd_cloud)
    dispatcher.register("options", _cmd_options)

    # Wire input callbacks
    def _on_command(raw_input: str) -> None:
        result = dispatcher.dispatch(raw_input)
        if result.message:
            _overlay_show(overlay, result.message)

    def _on_user_input(text: str) -> None:
        nl = match_nl_command(text)
        if nl is not None:
            name, args = nl
            _on_command(f"/{name} {args}".rstrip())
            return
        brain.submit_user_input(text)

    overlay.set_command_callback(_on_command)
    overlay.set_input_callback(_on_user_input)

    def _on_buddy_reaction(kind: str) -> None:
        if kind == "poke":
            brain.on_buddy_poked()
        elif kind == "shake":
            brain.on_buddy_shaken()

    overlay.set_buddy_reaction_callback(_on_buddy_reaction)

    # Chat-log persistence: write-through on every buddy/user line, plus a
    # clear hook for Ctrl+L. MemoryStore holds the cap so the hot path
    # doesn't pay a config lookup or SELECT COUNT per insert.
    if memory is not None:
        memory.set_chat_log_max_persisted(
            config.chat_log.max_persisted if config.chat_log.persist else 0
        )
        _overlay_setter = getattr(overlay, "set_chat_persist_callback", None)
        if callable(_overlay_setter):
            def _persist_chat(
                speaker: str, text: str, url: str | None,
            ) -> None:
                memory.record_chat_entry(
                    speaker=speaker, text=text, url=url,
                )

            def _clear_chat() -> None:
                memory.clear_chat_log()

            _overlay_setter(_persist_chat, _clear_chat)

        # Hydrate the chat log before the brain thread starts emitting.
        if config.chat_log.persist and config.chat_log.hydrate_on_start > 0:
            try:
                entries = memory.get_recent_chat_entries(
                    config.chat_log.hydrate_on_start
                )
                if entries:
                    overlay.load_chat_history(entries)
            except Exception as exc:
                log.warning("chat log hydration failed: %s", exc)

    # Brain runs in a background thread with its own asyncio loop
    def _run_brain() -> None:
        try:
            asyncio.run(brain.start())
        except Exception:
            log.exception("Brain thread crashed")

    brain_thread = threading.Thread(
        target=_run_brain,
        daemon=True,
        name="tokenpal-brain",
    )
    brain_thread.start()
    log.info("Brain thread started")

    # Handle Ctrl+C gracefully
    def _shutdown(*args: object) -> None:
        log.info("Shutting down...")
        overlay.schedule_callback(overlay.teardown)

    signal.signal(signal.SIGINT, _shutdown)

    # Block on the UI event loop (main thread)
    try:
        overlay.run_loop()
    finally:
        asyncio.run(brain.stop())
        if memory:
            memory.teardown()
        _unload_model(llm.model_name)
        log.info("TokenPal shut down cleanly")


def _print_startup_summary(
    senses: list[AbstractSense],
    actions: list[AbstractAction],
    config: TokenPalConfig,
) -> None:
    """Print a brief status summary to stderr before the overlay takes over."""
    sense_names = [s.sense_name for s in senses]
    action_names = [a.action_name for a in actions]
    model = config.llm.model_name

    lines = [
        f"  model: {model}",
        f"  senses: {', '.join(sense_names) or 'none'}",
    ]
    if action_names:
        lines.append(f"  actions: {', '.join(action_names)}")

    summary = "\n".join(lines)
    print(f"\n\033[1mTokenPal\033[0m\n{summary}\n", file=sys.stderr)


def _unload_model(model_name: str) -> None:
    """Unload the model from Ollama to free RAM."""
    import subprocess

    try:
        subprocess.run(
            ["ollama", "stop", model_name],
            capture_output=True,
            timeout=5,
        )
        log.info("Unloaded model '%s' from Ollama", model_name)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


_RECOMMENDED_MODELS: list[tuple[str, str]] = [
    ("gemma4", "Google, 9B — fast, witty, tool calling (default)"),
    ("llama3.2:3b", "Meta, 3B — very fast, lightweight"),
    ("llama3.1:8b", "Meta, 8B — balanced speed/quality"),
    ("mistral:7b", "Mistral, 7B — solid all-rounder"),
    ("phi4-mini", "Microsoft, 3.8B — compact, capable"),
    ("qwen3:8b", "Alibaba, 8B — multilingual"),
]


def _handle_gh_command(subcmd: str, extra: str) -> CommandResult:
    """Handle /gh — run git/GitHub commands off the main thread."""
    import shutil
    import subprocess

    if subcmd == "log":
        count = extra.strip() if extra else "5"
        if not count.isdigit():
            return CommandResult("", error="Usage: /gh log [count]")
        try:
            out = subprocess.run(
                ["git", "log", f"-{count}", "--oneline", "--no-color"],
                capture_output=True, text=True, timeout=5,
            )
            if out.returncode != 0:
                return CommandResult("", error=out.stderr.strip() or "git log failed")
            return CommandResult(out.stdout.strip() or "", error="No commits found." if not out.stdout.strip() else None)
        except Exception as e:
            return CommandResult("", error=f"git log failed: {e}")

    # prs / issues
    if not shutil.which("gh"):
        return CommandResult("", error="gh CLI not found — install from https://cli.github.com")
    gh_cmd = "pr" if subcmd == "prs" else "issue"
    try:
        out = subprocess.run(
            ["gh", gh_cmd, "list", "--limit", "5"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0:
            return CommandResult("", error=out.stderr.strip() or f"gh {gh_cmd} list failed")
        return CommandResult(out.stdout.strip() or "", error=f"No open {subcmd}." if not out.stdout.strip() else None)
    except Exception as e:
        return CommandResult("", error=f"gh failed: {e}")


def _apply_cloud_modal_result(result: Any, config: TokenPalConfig) -> None:
    """Persist the CloudModal's output: new key (if any), toggles, model.

    Runs the same writers as the /cloud subcommands so the on-disk state
    stays consistent regardless of which path the user took.
    """
    from tokenpal.ui.cloud_modal import CloudModalResult

    assert isinstance(result, CloudModalResult)
    cfg = config.cloud_llm

    if result.new_api_key:
        try:
            set_cloud_key(result.new_api_key)
        except ValueError as e:
            log.warning("cloud modal: bad key shape: %s", e)
            # Don't flip enabled on a bad key paste. The modal already
            # dismissed - surface nothing noisier than a log line.
            return

    try:
        set_cloud_enabled(result.enabled)
    except OSError as e:
        log.warning("cloud modal: could not persist enabled flag: %s", e)
    cfg.enabled = result.enabled

    # research_synth + research_plan + research_deep + research_search:
    # upsert via the writer.
    try:
        from tokenpal.config.toml_writer import update_config

        def _mutate(data: dict[str, Any]) -> None:
            section = data.setdefault("cloud_llm", {})
            section["research_synth"] = result.research_synth
            section["research_plan"] = result.research_plan
            section["research_deep"] = result.research_deep
            section["research_search"] = result.research_search

        update_config(_mutate)
    except OSError as e:
        log.warning("cloud modal: could not persist site flags: %s", e)
    cfg.research_synth = result.research_synth
    cfg.research_plan = result.research_plan
    cfg.research_deep = result.research_deep
    cfg.research_search = result.research_search

    if result.model in ALLOWED_MODELS and result.model != cfg.model:
        try:
            set_cloud_model(result.model)
        except OSError as e:
            log.warning("cloud modal: could not persist model: %s", e)
        cfg.model = result.model

    # ----------------------------------------------------------------
    # Tavily (cloud_search layer)
    # ----------------------------------------------------------------
    cs = config.cloud_search
    tavily_key_ok = True
    if result.tavily_new_api_key:
        try:
            set_tavily_key(result.tavily_new_api_key)
        except ValueError as e:
            log.warning("cloud modal: bad Tavily key shape: %s", e)
            tavily_key_ok = False

    if tavily_key_ok:
        try:
            set_cloud_search_layer_enabled(result.tavily_enabled)
        except OSError as e:
            log.warning(
                "cloud modal: could not persist cloud_search enabled: %s", e,
            )
        cs.enabled = result.tavily_enabled

        depth = result.tavily_search_depth
        if depth in ("basic", "advanced") and depth != cs.search_depth:
            try:
                from tokenpal.config.toml_writer import update_config

                def _mutate_depth(data: dict[str, Any]) -> None:
                    data.setdefault("cloud_search", {})["search_depth"] = depth

                update_config(_mutate_depth)
            except OSError as e:
                log.warning(
                    "cloud modal: could not persist search_depth: %s", e,
                )
            cs.search_depth = depth  # type: ignore[assignment]

    # ----------------------------------------------------------------
    # Brave (key-only)
    # ----------------------------------------------------------------
    if result.brave_new_api_key:
        try:
            set_brave_key(result.brave_new_api_key)
        except ValueError as e:
            log.warning("cloud modal: bad Brave key shape: %s", e)

    # ----------------------------------------------------------------
    # /refine supplemental cap
    # ----------------------------------------------------------------
    refine_max = max(0, int(result.refine_max_supplemental))
    if refine_max != cs.refine_max_supplemental:
        try:
            from tokenpal.config.toml_writer import update_config

            def _mutate_refine(data: dict[str, Any]) -> None:
                data.setdefault("cloud_search", {})[
                    "refine_max_supplemental"
                ] = refine_max

            update_config(_mutate_refine)
        except OSError as e:
            log.warning(
                "cloud modal: could not persist refine_max_supplemental: %s", e,
            )
        cs.refine_max_supplemental = refine_max

    log.info(
        "cloud modal: enabled=%s synth=%s plan=%s deep=%s search=%s "
        "model=%s tavily_enabled=%s tavily_depth=%s tavily_key=%s "
        "brave_key=%s",
        cfg.enabled, cfg.research_synth, cfg.research_plan,
        cfg.research_deep, cfg.research_search, cfg.model,
        cs.enabled, cs.search_depth,
        "set" if get_tavily_key() else "unset",
        "set" if get_brave_key() else "unset",
    )


_CLOUD_BACKENDS: tuple[str, ...] = ("anthropic", "tavily", "brave")


def _handle_cloud_command(args: str, config: TokenPalConfig) -> CommandResult:
    """Handle /cloud — manage opt-in commercial backends.

    Two-level dispatch:
        /cloud [status]                           aggregate status for all backends
        /cloud <backend> [action] [args...]       per-backend subcommands

    Known backends: anthropic (synth), tavily (search+extract), brave (search).

    Legacy flat subcommands still work as sugar for `/cloud anthropic ...`:
        /cloud enable <key>          → /cloud anthropic enable <key>
        /cloud disable               → /cloud anthropic disable
        /cloud forget                → /cloud anthropic forget
        /cloud model <id>            → /cloud anthropic model <id>
        /cloud plan|deep|search ...  → /cloud anthropic plan|deep|search ...
    """
    parts = args.split(maxsplit=2)
    first = parts[0].lower() if parts else ""

    # Aggregate status (bare /cloud or /cloud status)
    if first == "" or first == "status":
        return CommandResult(_cloud_aggregate_status(config))

    # Two-level: /cloud <backend> <action> [rest]
    if first in _CLOUD_BACKENDS:
        backend = first
        sub = parts[1].lower() if len(parts) > 1 else ""
        rest = parts[2].strip() if len(parts) > 2 else ""
        if backend == "anthropic":
            return _handle_cloud_anthropic(sub, rest, config)
        if backend == "tavily":
            return _handle_cloud_tavily(sub, rest, config)
        if backend == "brave":
            return _handle_cloud_brave(sub, rest, config)

    # Legacy flat subcommand — route to anthropic handler with a single
    # deprecation log line (don't spam the user on each call).
    log.info("/cloud: legacy flat subcommand '%s' — routed to /cloud anthropic", first)
    sub = first
    rest = parts[1].strip() if len(parts) > 1 else ""
    # Re-glue for the multi-token legacy case (e.g. `/cloud model claude-...`)
    if len(parts) > 2:
        rest = f"{rest} {parts[2].strip()}".strip()
    return _handle_cloud_anthropic(sub, rest, config)


def _cloud_aggregate_status(config: TokenPalConfig) -> str:
    """Render one line per configured backend. Quiet for backends not enabled."""
    lines: list[str] = []
    lines.append(_anthropic_status_line(config))
    lines.append(_tavily_status_line(config))
    lines.append(_brave_status_line())
    return "\n".join(lines)


def _refine_gate_error(config: TokenPalConfig) -> str | None:
    """Return a specific reason string if /refine's cloud prerequisites
    aren't satisfied, or None if ready. Mirrors _build_cloud_backend's
    None-return cases so the user sees the actual gate that failed
    instead of a blanket 'requires cloud'."""
    cfg = config.cloud_llm
    if not cfg.enabled:
        return (
            "/refine: cloud_llm.enabled is false. "
            "Run /cloud anthropic enable <key> (or flip the toggle in the /cloud modal)."
        )
    if not cfg.research_synth:
        return (
            "/refine: cloud_llm.research_synth is false. "
            "Enable 'Use for /research synth' in the /cloud modal, "
            "or set research_synth = true under [cloud_llm] in config.toml."
        )
    if not get_cloud_key():
        return (
            "/refine: no Anthropic API key stored. "
            "Run /cloud anthropic enable <sk-ant-...>."
        )
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return (
            "/refine: anthropic SDK not installed on this machine. "
            "Run: python -m pip install anthropic"
        )
    return None


def _anthropic_status_line(config: TokenPalConfig) -> str:
    cfg = config.cloud_llm
    if not cfg.enabled:
        stored = get_cloud_key()
        suffix = " (key stored, run /cloud anthropic enable to resume)" if stored else ""
        return f"Anthropic: disabled{suffix}"
    key = get_cloud_key()
    if not key:
        return "Anthropic: enabled but no key — run /cloud anthropic enable <key>"
    from tokenpal.llm.cloud_backend import DEEP_MODE_MODELS
    flags: list[str] = []
    if cfg.research_synth:
        flags.append("synth")
    else:
        flags.append("synth=OFF")
    if cfg.research_plan:
        flags.append("plan")
    if cfg.research_deep:
        if cfg.model in DEEP_MODE_MODELS:
            flags.append("deep")
        else:
            flags.append("deep=set but needs Sonnet+")
    elif cfg.research_search:
        if cfg.model in DEEP_MODE_MODELS:
            flags.append("search")
        else:
            flags.append("search=set but needs Sonnet+")
    flag_str = f", {'+'.join(flags)}" if flags else ""
    return f"Anthropic: enabled, {cfg.model}{flag_str}, key {fingerprint(key)}"


def _tavily_status_line(config: TokenPalConfig) -> str:
    cfg = config.cloud_search
    key = get_tavily_key()
    if not cfg.enabled:
        suffix = " (key stored, run /cloud tavily enable to resume)" if key else ""
        return f"Tavily: disabled{suffix}"
    if not key:
        return "Tavily: enabled but no key — run /cloud tavily enable <key>"
    return (
        f"Tavily: enabled, {cfg.search_depth}, max_results={cfg.max_results}, "
        f"key {fingerprint(key)}"
    )


def _handle_cloud_tavily(sub: str, target: str, config: TokenPalConfig) -> CommandResult:
    """Manage the Tavily-backed search+extract layer for /research."""
    cfg = config.cloud_search

    if sub == "" or sub == "status":
        return CommandResult(_tavily_status_line(config))

    if sub == "enable":
        if target:
            try:
                set_tavily_key(target)
            except ValueError as e:
                return CommandResult(f"/cloud tavily enable rejected: {e}")
            stored = target
        else:
            stored = get_tavily_key() or ""
            if not stored:
                return CommandResult(
                    "Usage: /cloud tavily enable <api-key>\n"
                    "Get a key at https://app.tavily.com (1,000 credits/month "
                    "free — about 100-200 /research runs)."
                )
        try:
            set_cloud_search_layer_enabled(True)
        except OSError as e:
            return CommandResult(f"/cloud tavily: could not persist flag: {e}")
        cfg.enabled = True  # live runtime flip, no restart required
        fp = fingerprint(stored)
        return CommandResult(
            f"Tavily search layer enabled — {cfg.search_depth}, "
            f"key {fp}. Next /research will route search+extract through "
            "Tavily (synth path unchanged).\n\n"
            "Privacy note: every /research query and the URLs it visits go "
            "to Tavily. Use /cloud tavily disable to pause without forgetting "
            "the key."
        )

    if sub == "disable":
        try:
            set_cloud_search_layer_enabled(False)
        except OSError as e:
            return CommandResult(f"/cloud tavily: could not persist flag: {e}")
        cfg.enabled = False
        had_key = get_tavily_key() is not None
        suffix = " (key retained)" if had_key else ""
        return CommandResult(f"Tavily search layer disabled{suffix}.")

    if sub == "forget":
        clear_tavily_key()
        try:
            set_cloud_search_layer_enabled(False)
        except OSError:
            pass
        cfg.enabled = False
        return CommandResult("Tavily search layer disabled and key wiped.")

    return CommandResult(
        "Usage: /cloud tavily [status|enable <key>|disable|forget]"
    )


def _brave_status_line() -> str:
    """Brave has no runtime-enabled flag (Phase 4 planner routes to it on
    demand when a key is present). Status is purely a key-presence check."""
    key = get_brave_key()
    if not key:
        return "Brave: disabled (no key — run /cloud brave enable <key>)"
    return f"Brave: key on disk, {fingerprint(key)} (routed per-query by planner)"


def _handle_cloud_brave(sub: str, target: str, config: TokenPalConfig) -> CommandResult:
    """Manage the Brave Search API key. Unlike Tavily/Anthropic there is no
    enabled flag — the smart planner routes queries to Brave when a key is
    present, otherwise falls back to DDG."""
    if sub == "" or sub == "status":
        return CommandResult(_brave_status_line())

    if sub == "enable":
        if not target:
            return CommandResult(
                "Usage: /cloud brave enable <api-key>\n"
                "Get a key at https://api.search.brave.com "
                "(2,000 queries/month free)."
            )
        try:
            set_brave_key(target)
        except ValueError as e:
            return CommandResult(f"/cloud brave enable rejected: {e}")
        fp = fingerprint(target)
        return CommandResult(
            f"Brave key stored — {fp}. The smart planner will route "
            "queries to Brave when it's a better fit than DDG (Phase 4).\n\n"
            "Privacy note: Brave receives every query it's routed."
        )

    if sub == "forget":
        clear_brave_key()
        return CommandResult("Brave key wiped.")

    return CommandResult("Usage: /cloud brave [status|enable <key>|forget]")


def _handle_cloud_anthropic(subcmd: str, target: str, config: TokenPalConfig) -> CommandResult:
    """Manage /research synth routing through Anthropic."""
    cfg = config.cloud_llm

    if subcmd == "" or subcmd == "status":
        return CommandResult(_anthropic_status_line(config))

    if subcmd == "enable":
        if target:
            try:
                set_cloud_key(target)
            except ValueError as e:
                # Scrub raw key from any echo - handler input may be in logs.
                return CommandResult(f"/cloud enable rejected: {e}")
            stored = target
        else:
            # Bare /cloud enable - re-enable using the already-stored key.
            stored = get_cloud_key() or ""
            if not stored:
                return CommandResult(
                    "Usage: /cloud enable <api-key>\n"
                    "Get a key at https://console.anthropic.com/settings/keys "
                    "(workspace needs at least $5 credit)."
                )
        try:
            set_cloud_enabled(True)
        except OSError as e:
            return CommandResult(f"/cloud: could not persist enabled flag: {e}")
        cfg.enabled = True  # live runtime flip, no restart required
        fp = fingerprint(stored)
        return CommandResult(
            f"Cloud LLM enabled - {cfg.model}, key {fp}. "
            "Next /research will route synth through Anthropic."
        )

    if subcmd == "disable":
        try:
            set_cloud_enabled(False)
        except OSError as e:
            return CommandResult(f"/cloud: could not persist flag: {e}")
        cfg.enabled = False
        had_key = get_cloud_key() is not None
        suffix = " (key retained)" if had_key else ""
        return CommandResult(f"Cloud LLM disabled{suffix}.")

    if subcmd == "forget":
        clear_cloud_key()
        try:
            set_cloud_enabled(False)
        except OSError:
            pass
        cfg.enabled = False
        return CommandResult("Cloud LLM disabled and key wiped.")

    if subcmd == "model":
        if not target:
            return CommandResult(
                f"Usage: /cloud model <id>. Choose from: "
                f"{', '.join(ALLOWED_MODELS)}"
            )
        if target not in ALLOWED_MODELS:
            return CommandResult(
                f"Unknown model '{target}'. Allowed: {', '.join(ALLOWED_MODELS)}"
            )
        try:
            set_cloud_model(target)
        except OSError as e:
            return CommandResult(f"/cloud: could not persist model: {e}")
        cfg.model = target
        return CommandResult(f"Cloud LLM model set to {target}.")

    if subcmd == "plan":
        if target.lower() in ("on", "true", "enable"):
            new_val = True
        elif target.lower() in ("off", "false", "disable"):
            new_val = False
        else:
            state = "on" if cfg.research_plan else "off"
            return CommandResult(
                f"Cloud planner stage: {state}. "
                "Usage: /cloud plan [on|off]. "
                "Off by default - opt-in for ambiguous / multi-constraint "
                "questions where Haiku plans better than local Qwen3."
            )
        try:
            set_cloud_plan(new_val)
        except OSError as e:
            return CommandResult(f"/cloud: could not persist plan flag: {e}")
        cfg.research_plan = new_val
        verb = "on" if new_val else "off"
        return CommandResult(f"Cloud planner stage turned {verb}.")

    if subcmd == "deep":
        from tokenpal.llm.cloud_backend import DEEP_MODE_MODELS
        if target.lower() in ("on", "true", "enable"):
            new_val = True
        elif target.lower() in ("off", "false", "disable"):
            new_val = False
        else:
            state = "on" if cfg.research_deep else "off"
            needs = (
                "" if cfg.model in DEEP_MODE_MODELS
                else f"\nNote: deep mode requires Sonnet 4.6+ "
                     f"(current model: {cfg.model})."
            )
            return CommandResult(
                f"Cloud deep mode: {state}. "
                "Usage: /cloud deep [on|off]. "
                "Replaces local search+fetch with Anthropic's server-side "
                "web_search + web_fetch tools.\n\n"
                "WARNING: deep mode can cost $1-3/run on review-heavy "
                "queries. Every web_fetch loads full page content into "
                "the tool-loop context, and each subsequent step re-bills "
                "the accumulated input. For fresh-web Sonnet synthesis "
                "without the snowball, use /cloud search on instead."
                f"{needs}"
            )
        if new_val and cfg.model not in DEEP_MODE_MODELS:
            allowed = ", ".join(sorted(DEEP_MODE_MODELS))
            return CommandResult(
                f"Deep mode requires one of: {allowed}. "
                f"Current model is {cfg.model}. "
                f"Run /cloud model <id> first."
            )
        try:
            set_cloud_deep(new_val)
        except OSError as e:
            return CommandResult(f"/cloud: could not persist deep flag: {e}")
        cfg.research_deep = new_val
        if new_val:
            return CommandResult(
                "Cloud deep mode turned on.\n"
                "WARNING: expect $1-3 per /research run. Each web_fetch "
                "loads full page content; the tool-loop re-bills it on "
                "every step. Prefer /cloud search for cheaper Sonnet-on-web."
            )
        return CommandResult("Cloud deep mode turned off.")

    if subcmd == "search":
        from tokenpal.llm.cloud_backend import DEEP_MODE_MODELS
        if target.lower() in ("on", "true", "enable"):
            new_val = True
        elif target.lower() in ("off", "false", "disable"):
            new_val = False
        else:
            state = "on" if cfg.research_search else "off"
            needs = (
                "" if cfg.model in DEEP_MODE_MODELS
                else f"\nNote: search mode requires Sonnet 4.6+ "
                     f"(current model: {cfg.model})."
            )
            return CommandResult(
                f"Cloud search mode: {state}. "
                "Usage: /cloud search [on|off]. "
                "Sonnet drives web_search only (no web_fetch). Costs a "
                "fraction of deep mode because search results are "
                "filtered server-side instead of loaded as full page "
                f"dumps. Good middle tier for fresh-web awareness.{needs}"
            )
        if new_val and cfg.model not in DEEP_MODE_MODELS:
            allowed = ", ".join(sorted(DEEP_MODE_MODELS))
            return CommandResult(
                f"Search mode requires one of: {allowed}. "
                f"Current model is {cfg.model}."
            )
        try:
            set_cloud_search(new_val)
        except OSError as e:
            return CommandResult(f"/cloud: could not persist search flag: {e}")
        cfg.research_search = new_val
        verb = "on" if new_val else "off"
        override = (
            "\nNote: /cloud deep is also on — deep takes precedence. "
            "Run /cloud deep off to use search mode."
            if new_val and cfg.research_deep else ""
        )
        return CommandResult(f"Cloud search mode turned {verb}.{override}")

    return CommandResult(
        "Usage: /cloud anthropic [status|enable <key>|disable|forget|"
        "model <id>|plan on|off|deep on|off|search on|off]"
    )


def _handle_zip_command(args: str) -> CommandResult:
    """Handle /zip — geocode a zip code and write weather location to config."""
    from tokenpal.config.weather import geocode_zip, write_weather_config

    zipcode = args.strip()
    if not zipcode:
        return CommandResult("Usage: /zip 90210")
    if not re.match(r"^\d{5}$", zipcode):
        return CommandResult("Enter a 5-digit US zip code, e.g. /zip 90210")

    try:
        geo = geocode_zip(zipcode)
    except Exception as e:
        return CommandResult(f"Geocoding failed: {e}")

    if not geo:
        return CommandResult(f"No location found for zip code {zipcode}")

    write_weather_config(geo.lat, geo.lon, geo.label)
    return CommandResult(f"Weather set to {geo.label} ({zipcode}). Restart TokenPal to activate.")


def _handle_model_command(
    args: str,
    llm: AbstractLLMBackend,
    overlay: AbstractOverlay,
    brain: Brain | None = None,
    config: TokenPalConfig | None = None,
) -> CommandResult:
    """Handle /model subcommands."""
    import json
    import urllib.request

    parts = args.strip().split(maxsplit=1)
    subcmd = parts[0].lower() if parts else ""
    inference_engine = (
        config.llm.inference_engine if config is not None else "ollama"
    )
    subargs = parts[1].strip() if len(parts) > 1 else ""

    # No args → show current model
    if not subcmd:
        return CommandResult(f"Current model: {llm.model_name}")

    if subcmd in ("list", "pull", "browse") and inference_engine == "llamacpp":
        return CommandResult(
            "llama-server manages GGUFs manually — see docs/amd-dgpu-setup.md"
        )

    if subcmd == "list":
        try:
            req = urllib.request.Request("http://localhost:11434/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
            models = data.get("models", [])
            if not models:
                return CommandResult("No models installed.")
            names = []
            for m in models:
                name = m.get("name", "?")
                size_gb = m.get("size", 0) / 1e9
                marker = " *" if name == llm.model_name else ""
                names.append(f"{name} ({size_gb:.1f}GB){marker}")
            return CommandResult("Models: " + ", ".join(names))
        except Exception:
            return CommandResult("Can't reach Ollama.")

    if subcmd == "pull":
        if not subargs:
            return CommandResult("Usage: /model pull <name>")
        model = subargs

        def _pull() -> None:
            import subprocess

            bubble = SpeechBubble(
                text=f"Downloading {model}...", persistent=True,
            )
            overlay.schedule_callback(lambda: overlay.show_speech(bubble))
            overlay.schedule_callback(
                lambda: overlay.update_status(f"Pulling {model}...")
            )
            try:
                result = subprocess.run(
                    ["ollama", "pull", model],
                    capture_output=True, text=True, timeout=600,
                )
                if result.returncode == 0:
                    _overlay_finalize(
                        overlay, f"Got {model}! /model {model} to use it."
                    )
                else:
                    err = (result.stderr or "unknown error").strip()[:60]
                    _overlay_finalize(overlay, f"Pull failed: {err}")
            except Exception:
                _overlay_finalize(overlay, "Pull failed. Check logs.")
            finally:
                overlay.schedule_callback(
                    lambda: overlay.update_status("")
                )

        threading.Thread(
            target=_pull, daemon=True, name="model-pull",
        ).start()
        return CommandResult("")

    if subcmd == "browse":
        lines = [f"{n} — {d}" for n, d in _RECOMMENDED_MODELS]
        return CommandResult(
            "Recommended: " + " | ".join(lines)
            + " — /model pull <name> to download"
        )

    # Bare name → switch model for the CURRENT server and remember it there.
    # Does NOT touch the global [llm] model_name fallback.
    llm.set_model(subcmd)
    if (
        hasattr(llm, "refresh_capability")
        and brain is not None
        and brain._loop is not None
    ):
        try:
            asyncio.run_coroutine_threadsafe(llm.refresh_capability(), brain._loop)
        except Exception:
            log.exception("Failed to schedule capability refresh for %s", subcmd)
    if (
        hasattr(llm, "warmup")
        and brain is not None
        and brain._loop is not None
    ):
        try:
            asyncio.run_coroutine_threadsafe(llm.warmup(), brain._loop)
        except Exception:
            log.exception("Failed to schedule warmup for %s", subcmd)
    try:
        from tokenpal.config.toml_writer import remember_server_model

        remember_server_model(llm.api_url, subcmd)
    except Exception:
        log.exception("Failed to persist /model selection for %s", llm.api_url)
        return CommandResult(f"Switched to {subcmd} (not persisted, see logs)")
    return CommandResult(f"Switched to {subcmd} (remembered for {llm.api_url})")


_VOICE_USAGE = (
    "Usage: /voice list | switch <name> | off | info"
    " | train <wiki> <character> | finetune <name>"
    " | finetune-setup | import <gguf_path>"
    " | regenerate [name|--all] | ascii [name|--all]"
)


def _voice_list(voices_dir: Path) -> CommandResult:
    from tokenpal.tools.voice_profile import list_profiles
    profiles = list_profiles(voices_dir)
    if not profiles:
        return CommandResult("No voices saved yet.")
    items = [f"{name} ({count} lines)" for _, name, count in profiles]
    return CommandResult("Voices: " + ", ".join(items))


def _voice_info(personality: PersonalityEngine) -> CommandResult:
    name = personality.voice_name
    if not name:
        return CommandResult("Using default TokenPal voice.")
    ft = " (fine-tuned)" if personality.is_finetuned else ""
    return CommandResult(f"Voice: {name}{ft}")


def _voice_off(
    personality: PersonalityEngine,
    llm: AbstractLLMBackend | None = None,
    config: TokenPalConfig | None = None,
) -> CommandResult:
    from tokenpal.tools.train_voice import activate_voice
    was_finetuned = personality.is_finetuned
    personality.set_voice(None)
    if was_finetuned and llm and config:
        llm.set_model(config.llm.model_name)
    activate_voice("")
    return CommandResult("Back to default TokenPal.")


def _voice_switch(
    args: str,
    personality: PersonalityEngine,
    voices_dir: Path,
    llm: AbstractLLMBackend | None = None,
    config: TokenPalConfig | None = None,
    on_voice_loaded: Callable[[], None] | None = None,
) -> CommandResult:
    from tokenpal.tools.train_voice import activate_voice
    from tokenpal.tools.voice_profile import load_profile, slugify
    if not args:
        return CommandResult("Usage: /voice switch <name>")
    try:
        slug = slugify(args)
        profile = load_profile(slug, voices_dir)
        was_finetuned = personality.is_finetuned
        personality.set_voice(profile)
        if on_voice_loaded:
            on_voice_loaded()
        if profile.finetuned_model and llm:
            llm.set_model(profile.finetuned_model)
        elif was_finetuned and llm and config:
            llm.set_model(config.llm.model_name)
        activate_voice(slug)
        return CommandResult(f"Switched to {profile.character}.")
    except FileNotFoundError:
        return CommandResult(f"Voice '{args}' not found.")


def _handle_voice_command(
    args: str,
    personality: PersonalityEngine,
    voices_dir: Path,
    overlay: AbstractOverlay,
    brain: Brain | None = None,
    llm: AbstractLLMBackend | None = None,
    config: TokenPalConfig | None = None,
    on_voice_loaded: Callable[[], None] | None = None,
) -> CommandResult:
    """Dispatch /voice subcommands to per-action helpers.

    The VoiceModal result handler calls the same helpers directly, so
    modal and slash command share one implementation — never forking.
    """
    parts = args.strip().split(maxsplit=1)
    subcmd = parts[0].lower() if parts else ""
    subargs = parts[1].strip() if len(parts) > 1 else ""

    if subcmd == "list":
        return _voice_list(voices_dir)
    if subcmd == "info":
        return _voice_info(personality)
    if subcmd == "off":
        return _voice_off(personality, llm, config)
    if subcmd == "switch":
        return _voice_switch(
            subargs, personality, voices_dir, llm, config, on_voice_loaded,
        )
    if subcmd == "train":
        return _start_voice_training(
            subargs, personality, voices_dir, overlay, brain,
            on_voice_loaded=on_voice_loaded,
        )
    if subcmd == "finetune":
        return _start_voice_finetune(
            subargs, personality, voices_dir, overlay, brain, llm, config,
        )
    if subcmd == "finetune-setup":
        return _start_finetune_setup(overlay, config)
    if subcmd == "import":
        return _import_gguf(subargs, personality, voices_dir, overlay, llm)
    if subcmd == "regenerate":
        return _start_voice_regenerate(
            subargs, personality, voices_dir, overlay,
            on_voice_loaded=on_voice_loaded,
        )
    if subcmd == "ascii":
        return _start_voice_regenerate_ascii(
            subargs, personality, voices_dir, overlay,
            on_voice_loaded=on_voice_loaded,
        )
    return CommandResult(_VOICE_USAGE)


def _overlay_show(overlay: AbstractOverlay, msg: str, persistent: bool = False) -> None:
    """Show a speech bubble via the overlay (thread-safe)."""
    bubble = SpeechBubble(text=msg, persistent=persistent)
    overlay.schedule_callback(lambda: overlay.show_speech(bubble))


def _overlay_finalize(overlay: AbstractOverlay, msg: str) -> None:
    """Clear any persistent progress bubble, then show a final message."""
    overlay.schedule_callback(overlay.hide_speech)
    bubble = SpeechBubble(text=msg, persistent=False)
    overlay.schedule_callback(lambda: overlay.show_speech(bubble))


def _overlay_status(overlay: AbstractOverlay, msg: str) -> None:
    """Update the status bar via the overlay (thread-safe)."""
    overlay.schedule_callback(lambda: overlay.update_status(msg))


def _start_voice_training(
    args: str,
    personality: PersonalityEngine,
    voices_dir: Path,
    overlay: AbstractOverlay,
    brain: Brain | None = None,
    on_voice_loaded: Callable[[], None] | None = None,
) -> CommandResult:
    """Kick off wiki voice training in a background thread."""
    parts = args.split(maxsplit=1)
    if len(parts) < 2:
        return CommandResult(
            'Usage: /voice train <wiki> "<character>"'
        )

    wiki = parts[0]
    character = parts[1].strip("\"'")

    def _train() -> None:
        try:
            from tokenpal.tools.train_voice import activate_voice, train_from_wiki
            from tokenpal.tools.voice_profile import slugify

            def _on_progress(step: str) -> None:
                _overlay_show(overlay, step, persistent=True)
                _overlay_status(overlay, f"Training: {step}")

            profile = train_from_wiki(
                wiki, character, voices_dir=voices_dir,
                progress_callback=_on_progress,
            )
            if profile is None:
                _overlay_finalize(overlay, f"Not enough lines for {character}.")
                return

            personality.set_voice(profile)
            activate_voice(slugify(profile.character))
            if on_voice_loaded:
                overlay.schedule_callback(on_voice_loaded)
            _overlay_finalize(
                overlay, f"I'm {character} now! ({len(profile.lines)} lines)"
            )
            log.info(
                "Voice trained: %s from %s (%d lines)",
                character, wiki, len(profile.lines),
            )

        except Exception:
            log.exception("Voice training failed")
            _overlay_finalize(overlay, "Training failed. Check logs.")
        finally:
            if brain:
                brain.paused = False
            _overlay_status(overlay, "")

    _overlay_show(overlay, f"Learning to be {character}...", persistent=True)
    _overlay_status(overlay, f"Training {character}...")
    if brain:
        brain.paused = True

    train_thread = threading.Thread(target=_train, daemon=True, name="voice-train")
    train_thread.start()
    return CommandResult("")


def _start_voice_regenerate(
    args: str,
    personality: PersonalityEngine,
    voices_dir: Path,
    overlay: AbstractOverlay,
    on_voice_loaded: Callable[[], None] | None = None,
) -> CommandResult:
    """Regenerate persona for existing voice profiles."""
    from tokenpal.tools.voice_profile import list_profiles, load_profile, slugify

    do_all = args.strip().lower() == "--all"
    if not args.strip():
        # Regenerate current voice
        if not personality.voice_name:
            return CommandResult(
                "No active voice. Usage: /voice regenerate <name> or --all"
            )
        slugs = [slugify(personality.voice_name)]
    elif do_all:
        profiles = list_profiles(voices_dir)
        slugs = [slug for slug, _, _ in profiles]
        if not slugs:
            return CommandResult("No voice profiles found.")
    else:
        slugs = [slugify(args.strip())]

    def _regen() -> None:
        try:
            from tokenpal.tools.train_voice import regenerate_voice_assets

            for slug in slugs:
                try:
                    profile = load_profile(slug, voices_dir)
                except FileNotFoundError:
                    _overlay_show(overlay, f"Voice '{slug}' not found.")
                    continue

                def _on_progress(step: str) -> None:
                    _overlay_show(overlay, step, persistent=True)

                regenerate_voice_assets(
                    profile, voices_dir, progress_callback=_on_progress,
                )

            count = len(slugs)
            msg = f"Regenerated {count} voice{'s' if count != 1 else ''}."
            _overlay_finalize(overlay, msg)
            log.info("Voice regeneration complete: %s", slugs)

            # Hot-swap if current voice was regenerated
            current = slugify(personality.voice_name) if personality.voice_name else ""
            if current in slugs:
                profile = load_profile(current, voices_dir)
                personality.set_voice(profile)
                if on_voice_loaded:
                    overlay.schedule_callback(on_voice_loaded)
        except Exception:
            log.exception("Voice regeneration failed")
            _overlay_finalize(overlay, "Regeneration failed. Check logs.")

    label = f"{len(slugs)} voices" if do_all else slugs[0]
    _overlay_show(overlay, f"Regenerating {label}...", persistent=True)

    regen_thread = threading.Thread(
        target=_regen, daemon=True, name="voice-regen",
    )
    regen_thread.start()
    return CommandResult("")


def _start_voice_regenerate_ascii(
    args: str,
    personality: PersonalityEngine,
    voices_dir: Path,
    overlay: AbstractOverlay,
    on_voice_loaded: Callable[[], None] | None = None,
) -> CommandResult:
    """Regenerate only the ASCII art frames for existing voice profiles."""
    from tokenpal.tools.voice_profile import list_profiles, load_profile, slugify

    do_all = args.strip().lower() == "--all"
    if not args.strip():
        if not personality.voice_name:
            return CommandResult(
                "No active voice. Usage: /voice ascii <name> or --all"
            )
        slugs = [slugify(personality.voice_name)]
    elif do_all:
        profiles = list_profiles(voices_dir)
        slugs = [slug for slug, _, _ in profiles]
        if not slugs:
            return CommandResult("No voice profiles found.")
    else:
        slugs = [slugify(args.strip())]

    def _regen() -> None:
        try:
            from tokenpal.tools.train_voice import regenerate_ascii_art

            for slug in slugs:
                try:
                    profile = load_profile(slug, voices_dir)
                except FileNotFoundError:
                    _overlay_show(overlay, f"Voice '{slug}' not found.")
                    continue

                def _on_progress(step: str) -> None:
                    _overlay_show(overlay, step, persistent=True)

                regenerate_ascii_art(
                    profile, voices_dir, progress_callback=_on_progress,
                )

            count = len(slugs)
            msg = f"Regenerated ASCII for {count} voice{'s' if count != 1 else ''}."
            _overlay_finalize(overlay, msg)
            log.info("Voice ASCII regeneration complete: %s", slugs)

            current = slugify(personality.voice_name) if personality.voice_name else ""
            if current in slugs:
                profile = load_profile(current, voices_dir)
                personality.set_voice(profile)
                if on_voice_loaded:
                    overlay.schedule_callback(on_voice_loaded)
        except Exception:
            log.exception("Voice ASCII regeneration failed")
            _overlay_finalize(overlay, "ASCII regeneration failed. Check logs.")

    label = f"{len(slugs)} voices" if do_all else slugs[0]
    _overlay_show(overlay, f"Regenerating ASCII for {label}...", persistent=True)

    regen_thread = threading.Thread(
        target=_regen, daemon=True, name="voice-ascii-regen",
    )
    regen_thread.start()
    return CommandResult("")


def _start_voice_finetune(
    args: str,
    personality: PersonalityEngine,
    voices_dir: Path,
    overlay: AbstractOverlay,
    brain: Brain | None = None,
    llm: AbstractLLMBackend | None = None,
    config: TokenPalConfig | None = None,
) -> CommandResult:
    """Kick off remote LoRA fine-tuning in a background thread."""
    from tokenpal.tools.voice_profile import load_profile, save_profile, slugify

    if not args:
        return CommandResult("Usage: /voice finetune <voice_name>")

    slug = slugify(args)
    try:
        profile = load_profile(slug, voices_dir)
    except FileNotFoundError:
        return CommandResult(f"Voice '{args}' not found. Train it first with /voice train.")

    if not config or not config.finetune.remote.host:
        return CommandResult(
            "No remote GPU configured. Set [finetune.remote] host in config.toml"
        )

    def _finetune() -> None:
        try:
            from tokenpal.tools.remote_train import remote_finetune

            def _on_progress(step: str) -> None:
                _overlay_show(overlay, step, persistent=True)
                _overlay_status(overlay, f"Fine-tuning: {step}")

            loop = asyncio.new_event_loop()
            loop.run_until_complete(
                remote_finetune(profile, config.finetune, _on_progress)
            )
            loop.close()

            from datetime import datetime
            model_name = f"tokenpal-{slug}"
            profile.finetuned_model = model_name
            profile.finetuned_base = config.finetune.base_model
            profile.finetuned_date = datetime.now().isoformat(timespec="seconds")
            save_profile(profile, voices_dir)

            personality.set_voice(profile)
            if llm:
                llm.set_model(model_name)

            _overlay_finalize(overlay, f"{profile.character} fine-tuned! Model: {model_name}")
            log.info("Fine-tuning complete: %s → %s", slug, model_name)

        except Exception:
            log.exception("Fine-tuning failed")
            _overlay_finalize(overlay, "Fine-tuning failed. Check logs.")
        finally:
            if brain:
                brain.paused = False
            _overlay_status(overlay, "")

    _overlay_show(overlay, f"Fine-tuning {profile.character}...", persistent=True)
    _overlay_status(overlay, f"Fine-tuning {profile.character}...")
    if brain:
        brain.paused = True

    ft_thread = threading.Thread(target=_finetune, daemon=True, name="voice-finetune")
    ft_thread.start()
    return CommandResult("")


def _start_finetune_setup(
    overlay: AbstractOverlay,
    config: TokenPalConfig | None = None,
) -> CommandResult:
    """Run one-time remote training environment setup."""
    if not config or not config.finetune.remote.host:
        return CommandResult(
            "No remote GPU configured. Set [finetune.remote] host in config.toml"
        )

    def _setup() -> None:
        try:
            from tokenpal.tools.remote_train import remote_setup

            def _on_progress(step: str) -> None:
                _overlay_show(overlay, step, persistent=True)
                _overlay_status(overlay, f"Setup: {step}")

            loop = asyncio.new_event_loop()
            ok = loop.run_until_complete(
                remote_setup(config.finetune.remote, _on_progress)
            )
            loop.close()

            if ok:
                _overlay_finalize(overlay, "Remote training environment ready!")
            else:
                _overlay_finalize(overlay, "Setup failed. Check tokenpal.log for details.")
        except Exception:
            log.exception("Finetune setup failed")
            _overlay_finalize(overlay, "Setup failed. Check logs.")
        finally:
            _overlay_status(overlay, "")

    _overlay_show(overlay, "Setting up remote training environment...", persistent=True)
    _overlay_status(overlay, "Setting up remote training...")

    setup_thread = threading.Thread(target=_setup, daemon=True, name="finetune-setup")
    setup_thread.start()
    return CommandResult("")


def _import_gguf(
    args: str,
    personality: PersonalityEngine,
    voices_dir: Path,
    overlay: AbstractOverlay,
    llm: AbstractLLMBackend | None = None,
) -> CommandResult:
    """Import a GGUF file trained on another machine."""
    from tokenpal.tools.voice_profile import load_profile, save_profile

    if not args:
        return CommandResult("Usage: /voice import <gguf_path>")

    gguf_path = Path(args).expanduser().resolve()
    if not gguf_path.exists():
        return CommandResult(f"File not found: {gguf_path}")
    if not gguf_path.suffix == ".gguf":
        return CommandResult("Expected a .gguf file.")

    # Derive voice name from GGUF filename (e.g., mordecai.gguf → mordecai)
    slug = gguf_path.stem
    model_name = f"tokenpal-{slug}"

    # Try to find matching voice profile
    try:
        profile = load_profile(slug, voices_dir)
    except FileNotFoundError:
        return CommandResult(
            f"No voice profile for '{slug}'. "
            f"Train the voice first with /voice train."
        )

    # Register with Ollama
    from tokenpal.tools.dataset_prep import build_system_prompt
    from tokenpal.tools.finetune_voice import register_ollama

    system_prompt = build_system_prompt(profile)
    if not register_ollama(gguf_path, model_name, system_prompt):
        return CommandResult("Failed to register with Ollama. Is it running?")

    # Update profile
    from datetime import datetime
    profile.finetuned_model = model_name
    profile.finetuned_base = "imported"
    profile.finetuned_date = datetime.now().isoformat(timespec="seconds")
    save_profile(profile, voices_dir)

    # Activate
    personality.set_voice(profile)
    if llm:
        llm.set_model(model_name)

    return CommandResult(f"Imported and activated: {model_name}")
