"""Gemini: picks ONE approved tool (function calling) and explains the evidence.
The LLM never writes Cypher and never computes numbers. Any failure falls back to deterministic output."""
import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "30"))

ROUTER_PROMPT = (
    "You route questions for GraphPilot, a supply-chain investigation agent. Pick exactly ONE tool. "
    "If the question is not about supplier disruptions, purchase orders, stock risk or how entities "
    "(PO-, PRD-, SUP-, WH-, BR-, MOV-) connect, call no tool and reply with one short sentence."
)

ANSWER_PROMPT = (
    "You explain an investigation result to a business user. Use ONLY the facts provided. "
    "Do not add numbers, dates, causes or entities that are not in the facts. Never state why a supplier "
    "delay happened unless the facts give a reason; if the facts say it is not recorded, say so. "
    "Write 4 to 7 plain sentences, no markdown, no bullet points."
)


class LLMError(Exception):
    pass


def is_configured():
    return bool(API_KEY) and "paste" not in API_KEY.lower() and "your" not in API_KEY.lower()


_CLIENT = None


def _client():
    global _CLIENT
    if _CLIENT is None:
        from google import genai
        from google.genai import types

        _CLIENT = genai.Client(
            api_key=API_KEY,
            http_options=types.HttpOptions(timeout=int(TIMEOUT_S * 1000)),
        )
    return _CLIENT

def choose_tool(question, tool_decls):
    """Return (tool_name, args) or None when the model declines to call a tool."""
    if not is_configured():
        raise LLMError("GEMINI_API_KEY is not configured")
    from google.genai import types

    try:
        resp = _client().models.generate_content(
            model=MODEL,
            contents=question,
            config=types.GenerateContentConfig(
                system_instruction=ROUTER_PROMPT,
                tools=[types.Tool(function_declarations=tool_decls)],
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                temperature=0,
            ),
        )
        calls = resp.function_calls or []
    except Exception as exc:  # timeout, quota, network, bad key
        raise LLMError(str(exc)) from exc
    if not calls:
        return None
    call = calls[0]  # one tool only
    return call.name, dict(call.args or {})


def write_answer(question, facts_text):
    if not is_configured():
        raise LLMError("GEMINI_API_KEY is not configured")
    from google.genai import types

    try:
        resp = _client().models.generate_content(
            model=MODEL,
            contents=f"Question: {question}\n\nFacts:\n{facts_text}",
            config=types.GenerateContentConfig(system_instruction=ANSWER_PROMPT, temperature=0.2),
        )
        text = (resp.text or "").strip()
    except Exception as exc:
        raise LLMError(str(exc)) from exc
    if not text:
        raise LLMError("empty answer")
    return text