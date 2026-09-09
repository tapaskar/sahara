"""One place that builds Gemini clients, Vertex or API key, global location for 3.x."""
from __future__ import annotations

import os
from functools import lru_cache

from google import genai

from . import config


def using_vertex() -> bool:
    flag = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower()
    if flag in ("true", "1", "yes"):
        return True
    if flag in ("false", "0", "no"):
        return False
    return not os.environ.get("GOOGLE_API_KEY")


@lru_cache(maxsize=1)
def text_client() -> genai.Client:
    if using_vertex():
        kw = dict(vertexai=True, location=config.GEMINI_LOCATION)
        if config.GOOGLE_CLOUD_PROJECT:
            kw["project"] = config.GOOGLE_CLOUD_PROJECT
        return genai.Client(**kw)
    return genai.Client()


def live_client() -> genai.Client:
    """The Live API is served through the same client; kept separate so it can differ later."""
    return text_client()
