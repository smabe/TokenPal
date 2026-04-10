"""Fetch character transcripts from Fandom wikis via the MediaWiki API."""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterator

# Fandom wiki API endpoint pattern
_API_URL = "https://{wiki}.fandom.com/api.php"

# Pre-compiled regexes for wiki markup stripping
_RE_WIKI_LINK = re.compile(r"\[\[([^\]|]*\|)?([^\]]+)\]\]")
_RE_BOLD = re.compile(r"'''?")
_RE_ITALIC = re.compile(r"''")
_RE_TEMPLATE = re.compile(r"\{\{[^}]*\}\}")
_RE_BRACKET = re.compile(r"\[.*?\]")
_RE_L_TEMPLATE = re.compile(r"\{\{L\|([^|]+)\|(.+)\}\}")
_RE_DIALOGUE = re.compile(r"^([A-Za-z][A-Za-z .'\-&,]{0,40}):\s+(.+)")


def _api_get(wiki: str, params: dict) -> dict:
    """Make a GET request to the Fandom MediaWiki API."""
    params["format"] = "json"
    query = urllib.parse.urlencode(params)
    url = f"{_API_URL.format(wiki=wiki)}?{query}"
    req = urllib.request.Request(url, headers={"User-Agent": "TokenPal/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def list_transcript_pages(wiki: str, limit: int = 500) -> list[str]:
    """List all transcript page titles from a Fandom wiki."""
    pages: list[str] = []
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": "Category:Transcripts",
        "cmlimit": str(min(limit, 500)),
    }

    while True:
        data = _api_get(wiki, params)
        members = data.get("query", {}).get("categorymembers", [])
        pages.extend(m["title"] for m in members)

        # Handle pagination
        cont = data.get("continue", {}).get("cmcontinue")
        if cont and len(pages) < limit:
            params["cmcontinue"] = cont
        else:
            break

    return pages


def fetch_transcript_wikitext(wiki: str, page_title: str) -> str | None:
    """Fetch raw wikitext for a single transcript page."""
    try:
        data = _api_get(wiki, {
            "action": "parse",
            "page": page_title,
            "prop": "wikitext",
        })
        return data.get("parse", {}).get("wikitext", {}).get("*")
    except (urllib.error.URLError, KeyError):
        return None


def _strip_wiki_markup(text: str) -> str:
    """Strip all wiki markup to plain text."""
    text = _RE_WIKI_LINK.sub(r"\2", text)
    text = _RE_BOLD.sub("", text)
    text = _RE_ITALIC.sub("", text)
    text = _RE_TEMPLATE.sub("", text)
    text = _RE_BRACKET.sub("", text)
    return text.strip()


def _wikitext_to_dialogue(wikitext: str) -> str:
    """Convert Fandom wikitext to plain-text 'Name: dialogue' format."""
    lines: list[str] = []

    for raw_line in wikitext.splitlines():
        raw_line = raw_line.strip()

        # Try {{L|Character|dialogue}} template first (Adventure Time, etc.)
        m = _RE_L_TEMPLATE.match(raw_line)
        if m:
            name = _strip_wiki_markup(m.group(1))
            dialogue = _strip_wiki_markup(m.group(2))
        else:
            # Strip all markup, then look for "Name: dialogue"
            cleaned = _strip_wiki_markup(raw_line)
            m = _RE_DIALOGUE.match(cleaned)
            if not m:
                continue
            name = m.group(1).strip()
            dialogue = m.group(2).strip()

        if not name or not dialogue:
            continue

        # Skip stage directions
        if name.startswith("(") or name.startswith("["):
            continue

        lines.append(f"{name}: {dialogue}")

    return "\n".join(lines)


def fetch_all_transcripts(
    wiki: str,
    max_pages: int = 500,
    progress: bool = True,
) -> str:
    """Fetch all transcripts from a wiki and return as plain-text dialogue.

    Returns a single string with all episodes concatenated in
    'Name: dialogue' format, one line per speech.
    """
    pages = list_transcript_pages(wiki, limit=max_pages)
    if not pages:
        return ""

    if progress:
        print(f"  Found {len(pages)} transcript pages on {wiki}.fandom.com")

    all_dialogue: list[str] = []
    for i, page in enumerate(pages):
        if progress:
            print(f"\r  Fetching [{i + 1}/{len(pages)}] {page[:50]}...", end="", flush=True)

        wikitext = fetch_transcript_wikitext(wiki, page)
        if wikitext:
            dialogue = _wikitext_to_dialogue(wikitext)
            if dialogue:
                all_dialogue.append(dialogue)

    if progress:
        print(f"\r  Fetched {len(all_dialogue)}/{len(pages)} transcripts successfully" + " " * 30)

    return "\n".join(all_dialogue)
