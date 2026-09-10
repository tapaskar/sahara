"""A text-mode Sahara for the call simulator: the real persona, the real tools, the real
memory and escalation — driven by typed lines instead of audio. Lets a tester hold an
actual dialogue with the agent to try personas and see what it remembers and escalates,
without a phone, a mic, or the Live audio API.

Stateless per request: the whole conversation is rebuilt from the stored turns each time,
so it survives across HTTP calls and server restarts.
"""
from __future__ import annotations

import asyncio
import logging

from google.genai import types

from .. import config
from ..gemini import text_client

log = logging.getLogger("sahara.engine.text_chat")


class ModelBusy(Exception):
    """The provider is rate-limited or overloaded — a retriable, user-facing condition,
    not a bug. Kept distinct so the caller can say 'the line is busy, try again'."""


def _tools(decls: list[dict]) -> list[types.Tool]:
    return [types.Tool(function_declarations=[types.FunctionDeclaration(**t) for t in decls])]


async def respond(system_prompt: str, tool_decls: list[dict], history: list[dict],
                  on_tool_call) -> str:
    """One assistant turn. `history` is the conversation so far as {who, text} dicts
    (who in {sahara, parent}); the newest parent line is already the last entry. Runs a
    manual function-calling loop — our tools have side effects, so we execute them via
    on_tool_call and feed the results back until the model produces speech.
    """
    contents: list[types.Content] = []
    for t in history:
        role = "model" if t["who"] == "sahara" else "user"
        contents.append(types.Content(role=role, parts=[types.Part(text=t["text"])]))

    cfg = types.GenerateContentConfig(system_instruction=system_prompt, tools=_tools(tool_decls))
    client = text_client()

    for _ in range(6):                       # bounded: a turn may fire several tools first
        r = await _generate(client, contents, cfg)
        cand = (r.candidates or [None])[0]
        if cand is None or not cand.content:
            return ""
        parts = cand.content.parts or []
        calls = [p.function_call for p in parts if getattr(p, "function_call", None)]
        if calls:
            contents.append(cand.content)
            responses = []
            for fc in calls:
                result = await on_tool_call({"id": fc.id or fc.name, "name": fc.name,
                                             "args": dict(fc.args or {})})
                responses.append(types.Part.from_function_response(
                    name=fc.name, response=result or {"ok": True}))
            contents.append(types.Content(role="user", parts=responses))
            if any(fc.name == "end_call" for fc in calls):
                text = "".join(p.text for p in parts if getattr(p, "text", None))
                return text or ""
            continue
        return "".join(p.text for p in parts if getattr(p, "text", None))
    return ""


async def _generate(client, contents, cfg, tries: int = 2):
    """One model call, retrying transient overload once. Quota (429) and overload (503)
    surface as ModelBusy so the UI can distinguish 'try again' from a real failure."""
    last = None
    for attempt in range(tries):
        try:
            return await client.aio.models.generate_content(
                model=config.GEMINI_TEXT_MODEL, contents=contents, config=cfg)
        except Exception as e:
            last = e
            msg = str(e)
            transient = "503" in msg or "UNAVAILABLE" in msg or "overloaded" in msg
            quota = "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower()
            if quota:
                raise ModelBusy("The AI service quota is exhausted — check billing or wait a little.") from e
            if transient and attempt + 1 < tries:
                await asyncio.sleep(1.2)
                continue
            if transient:
                raise ModelBusy("The AI service is briefly overloaded — please try again.") from e
            raise
    raise last
