"""Research actions — fetch_url, research. Exposed via /tools picker for
both /agent use and the /research pipeline. Network-touching, consent-gated
on web_fetches; the research action also requires research_mode consent.

The single-shot `search_web` tool was removed: the LLM reached for it on
deep questions where it should have used `research`. `search()` is still
alive as an internal primitive for /ask (human-typed) and the app enricher.
"""
