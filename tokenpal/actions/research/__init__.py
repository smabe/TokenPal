"""Research actions — search_web, fetch_url, research. Exposed via /tools
picker for both /agent use and the /research pipeline. Network-touching,
consent-gated on web_fetches; the research action also requires
research_mode consent.
"""
