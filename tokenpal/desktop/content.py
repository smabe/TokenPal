"""The privacy contract every desktop-content tool follows.

Text read out of another app is prompt-only: it goes to the LLM and nowhere
else. A tool that reads it must, in this order:

1. call ``require_consent()`` first, before any argument validation, and
   return its ``ActionResult`` when it is not ``None``;
2. call ``refuse_if_sensitive(source_app, window_title)`` as soon as the app
   name is known — before the OS read when the window list gives it, after
   otherwise — and return its result when it is not ``None``;
3. read from the OS;
4. wrap the text with ``DesktopContent.to_prompt_block()`` and put only that
   into the prompt.

Never log ``DesktopContent.text`` and never assign it (or anything derived
from it) to ``ActionResult.display_text``: that field is persisted to the
chat log. ``ActionResult.output`` carries the envelope to the model, and the
conversation path DEBUG-logs 200 chars of a tool result — which is why marked
tools are kept off that path entirely rather than relying on the log level.

Both halves of that are enforced as of the marked-action work: the
conversation tool specs exclude actions declaring ``reads_desktop_content``
and ``_execute_tool_call`` refuses one by name anyway, so neither desktop
content nor a reply derived from it can enter ``ConversationSession.history``,
which feeds the summarizer. See ``docs/claude/actions.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tokenpal.actions.base import ActionResult, consent_error
from tokenpal.brain.personality import (
    contains_sensitive_content_term,
    contains_sensitive_term,
)
from tokenpal.config.consent import Category, has_consent
from tokenpal.util.text_guards import neutralize_envelope_tags
from tokenpal.util.untrusted_text import scrub_content_body

ContentKind = Literal["selection", "document", "ocr"]

_ENVELOPE_TAG = "desktop_content"
# source_app is an OS-supplied window-owner name and kind may reach us from a
# caller the Literal does not constrain at runtime, so both are untrusted
# attribute values: drop what would terminate the attribute or the tag, and
# flatten every line separator so the opening tag stays one line.
_UNSAFE_ATTR_RE = re.compile(r'["<>]')
_ATTR_SPACE_RE = re.compile(r"\s+")

_CONSENT_LABEL = "desktop content"


def _attr_value(raw: str) -> str:
    """Make *raw* safe to interpolate into a double-quoted tag attribute."""
    return _ATTR_SPACE_RE.sub(" ", _UNSAFE_ATTR_RE.sub("", raw)).strip()


@dataclass(frozen=True, repr=False)
class DesktopContent:
    """Text read from another app, carried to a prompt without leaking.

    ``repr`` and ``str`` deliberately omit ``text`` so an accidental
    ``log.debug("%s", content)`` cannot spill it into a log file.
    """

    text: str
    source_app: str
    kind: ContentKind

    def __repr__(self) -> str:
        return (
            f"DesktopContent(kind={self.kind}, "
            f"app={self.source_app!r}, chars={len(self.text)})"
        )

    __str__ = __repr__

    def to_prompt_block(self) -> str:
        """Scrubbed ``<desktop_content>`` envelope for the LLM prompt."""
        app = _attr_value(self.source_app)
        kind = _attr_value(self.kind)
        body = neutralize_envelope_tags(scrub_content_body(self.text), _ENVELOPE_TAG)
        return (
            f'<{_ENVELOPE_TAG} kind="{kind}" app="{app}">\n'
            f"{body}\n</{_ENVELOPE_TAG}>"
        )


def refuse_if_sensitive(source_app: str, window_title: str = "") -> ActionResult | None:
    """Error result when *source_app* matches SENSITIVE_APPS, or
    *window_title* names an identity-critical service, else None.

    The title check exists for browsers: "Safari" is never a sensitive app,
    but a banking or password-manager page in it is, and the title is the only
    signal. It uses the narrower content-term list because titles are prose.

    The message never names the app: the result is returned to the model as
    the tool result, and the repo substitutes a generic label wherever a
    sensitive app name could reach a sink (``list_processes``,
    ``senses/process_heat``). For a marked tool the trace line is already
    unpersisted, so this is defence in depth.
    """
    if not (
        contains_sensitive_term(source_app) or contains_sensitive_content_term(window_title)
    ):
        return None
    return ActionResult(
        output="Won't read from that app: it's on the sensitive-app list.",
        success=False,
    )


def require_consent(path: Path | None = None) -> ActionResult | None:
    """Consent error unless ``Category.DESKTOP_CONTENT`` is granted, else None.

    *path* mirrors ``has_consent``'s test hook.
    """
    if has_consent(Category.DESKTOP_CONTENT, path):
        return None
    return consent_error(_CONSENT_LABEL)
