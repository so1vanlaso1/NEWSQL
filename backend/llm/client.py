"""Defensive OpenAI-compatible chat client (Phase 7).

One remote call per turn against ``{LLM_BASE_URL}/chat/completions``. Design goals:

- **Never raises.** Transport/JSON/shape errors surface on ``LlmResult.error`` so the
  ``/api/chat`` handler can always return a friendly answer instead of a 500.
- **Model discovery.** ``LLM_MODEL`` blank -> ``GET {base}/models`` (falls back to
  ``LLM_MODEL_FALLBACK`` when the tunnel serves an HTML interstitial / is offline).
- **JSON mode with fallback.** Sends ``response_format={"type":"json_object"}`` when
  enabled, and retries once WITHOUT it if the server rejects the field.
- **ngrok friendly.** Sends ``ngrok-skip-browser-warning`` and follows redirects.

Uses ``httpx`` (present in both venvs; ``requests`` is not guaranteed SQLNEW-local).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Iterator, Optional, Tuple

import httpx

from backend import config
from backend.common.logging import get_logger

log = get_logger(__name__)


@dataclass
class LlmResult:
    content: str = ""
    model: str = ""
    usage: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)
    error: Optional[str] = None
    used_json_object: bool = False
    latency_ms: int = 0
    # Native tool calls the model emitted (Phase 17). Each: {"id","name","arguments": dict}.
    # ``arguments`` is defensively parsed from the model's JSON string; ``_raw_arguments``
    # keeps the original string for diagnostics. Empty when the model returned no tool calls.
    tool_calls: list = field(default_factory=list)
    # Some reasoning models (e.g. Qwen3.5) split their chain-of-thought into a separate
    # ``reasoning_content`` field and leave ``content`` empty until they finish thinking.
    # Captured here for logging/diagnosis; the pipeline still reads ``content``/``tool_calls``.
    reasoning: str = ""


def _parse_tool_calls(message: dict) -> list:
    """Normalize OpenAI ``message.tool_calls`` into ``[{id,name,arguments(dict)}]``.

    Defensive: a 9B model may emit malformed ``arguments`` JSON; that call keeps
    ``arguments={}`` (+ ``_raw_arguments``) so the registry validator can reject it
    rather than the parser raising.
    """
    out: list = []
    for tc in (message.get("tool_calls") or []):
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        raw_args = fn.get("arguments")
        args: dict = {}
        if isinstance(raw_args, dict):
            args = raw_args
        elif isinstance(raw_args, str) and raw_args.strip():
            try:
                parsed = json.loads(raw_args)
                args = parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, ValueError):
                args = {}
        out.append({
            "id": tc.get("id") or "",
            "name": (fn.get("name") or "").strip(),
            "arguments": args,
            "_raw_arguments": raw_args if isinstance(raw_args, str) else "",
        })
    return out


def _looks_like_html(text: str) -> bool:
    t = (text or "").lstrip()
    return t.startswith("<") or t.startswith("<!DOCTYPE")


class LlmClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 120.0,
        temperature: float = 1.0,
        max_tokens: int = 4000,
        ngrok_skip: bool = True,
        try_json_object: bool = True,
        model_fallback: str = "default",
    ):
        self.base_url = base_url.rstrip("/")
        self._configured_model = (model or "").strip()
        self.api_key = api_key
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.ngrok_skip = ngrok_skip
        self.try_json_object = try_json_object
        self.model_fallback = model_fallback or "default"
        self._resolved_model: Optional[str] = None

    # ---- headers ----
    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        if self.ngrok_skip:
            h["ngrok-skip-browser-warning"] = "true"
        return h

    # ---- model discovery (memoized) ----
    def resolve_model(self) -> str:
        if self._resolved_model:
            return self._resolved_model
        if self._configured_model:
            self._resolved_model = self._configured_model
            return self._resolved_model
        model = self.model_fallback
        try:
            with httpx.Client(timeout=min(self.timeout, 20.0), follow_redirects=True) as c:
                r = c.get(f"{self.base_url}/models", headers=self._headers())
            if r.status_code == 200 and not _looks_like_html(r.text):
                data = r.json()
                items = data.get("data") if isinstance(data, dict) else None
                if isinstance(items, list) and items:
                    ident = items[0].get("id") if isinstance(items[0], dict) else None
                    if ident:
                        model = str(ident)
        except Exception:
            pass  # offline / interstitial / non-JSON -> keep the fallback id
        self._resolved_model = model
        log.info("resolved model id: %r (base=%s)", model, self.base_url)
        return model

    # ---- chat ----
    def _post(self, payload: dict) -> httpx.Response:
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as c:
            return c.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            )

    def _add_max_tokens(self, payload: dict, override: Optional[int]) -> None:
        value = self.max_tokens if override is None else override
        if value and value > 0:
            payload["max_tokens"] = value

    def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        json_object: Optional[bool] = None,
        tools: Optional[list] = None,
        tool_choice: Optional[str] = None,
    ) -> LlmResult:
        model = self.resolve_model()
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature if temperature is None else temperature,
        }
        self._add_max_tokens(payload, max_tokens)
        # Native tool-calling (Phase 17). ``tools`` and ``response_format=json_object`` are
        # mutually exclusive on OpenAI-compatible servers, so tool mode disables JSON mode.
        want_json = self.try_json_object if json_object is None else bool(json_object)
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
            want_json = False
        used_json = False
        if want_json:
            payload["response_format"] = {"type": "json_object"}
            used_json = True

        started = time.time()
        try:
            resp = self._post(payload)
            # If the server rejects response_format, retry once without it.
            if used_json and resp.status_code in (400, 404, 422):
                body_l = (resp.text or "").lower()
                if ("response_format" in body_l or "unsupported" in body_l
                        or "not support" in body_l or resp.status_code == 400):
                    payload.pop("response_format", None)
                    used_json = False
                    resp = self._post(payload)
            latency = int((time.time() - started) * 1000)

            if resp.status_code != 200:
                snippet = (resp.text or "")[:300]
                return LlmResult(model=model, used_json_object=used_json, latency_ms=latency,
                                 error=f"HTTP {resp.status_code}: {snippet}")
            if _looks_like_html(resp.text):
                return LlmResult(model=model, used_json_object=used_json, latency_ms=latency,
                                 error="endpoint returned HTML (tunnel offline / interstitial)")
            data = resp.json()
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            content = message.get("content")
            if content is None:  # some servers use choices[].text
                content = choice.get("text", "")
            return LlmResult(
                content=str(content or ""),
                model=str(data.get("model") or model),
                usage=data.get("usage") or {},
                raw=data if isinstance(data, dict) else {},
                used_json_object=used_json,
                latency_ms=latency,
                tool_calls=_parse_tool_calls(message),
                reasoning=str(message.get("reasoning_content") or ""),
            )
        except Exception as exc:  # noqa: BLE001 - client must never raise
            latency = int((time.time() - started) * 1000)
            return LlmResult(model=model, used_json_object=used_json, latency_ms=latency,
                             error=f"{exc.__class__.__name__}: {exc}")

    # ---- streaming chat ----
    def stream_chat(
        self,
        system: str,
        user: str,
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        json_object: Optional[bool] = None,
    ) -> Iterator[Tuple[str, object]]:
        """Yield ``("delta", str)`` for each streamed token, then ``("done", LlmResult)``.

        Never raises. If the server does not support ``stream=true`` (or streaming
        yields nothing / errors), transparently falls back to one blocking ``chat`` call
        and emits its whole content as a single delta so callers behave identically.
        """
        model = self.resolve_model()
        base = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature if temperature is None else temperature,
            "stream": True,
        }
        self._add_max_tokens(base, max_tokens)

        def _blocking_fallback() -> Iterator[Tuple[str, object]]:
            res = self.chat(system, user, temperature=temperature, max_tokens=max_tokens,
                            json_object=json_object)
            if res.content:
                yield ("delta", res.content)
            yield ("done", res)

        attempts = []
        want_json = self.try_json_object if json_object is None else bool(json_object)
        if want_json:
            p = dict(base)
            p["response_format"] = {"type": "json_object"}
            attempts.append((p, True))
        attempts.append((dict(base), False))

        started = time.time()
        for payload, used_json in attempts:
            parts: list[str] = []
            got_any = False
            try:
                with httpx.Client(timeout=self.timeout, follow_redirects=True) as c:
                    with c.stream(
                        "POST", f"{self.base_url}/chat/completions",
                        headers=self._headers(), json=payload,
                    ) as resp:
                        if resp.status_code != 200:
                            body = resp.read().decode("utf-8", "replace")
                            # response_format rejection -> retry without it (next attempt).
                            if used_json and resp.status_code in (400, 404, 422):
                                continue
                            latency = int((time.time() - started) * 1000)
                            yield ("done", LlmResult(
                                model=model, used_json_object=used_json, latency_ms=latency,
                                error=f"HTTP {resp.status_code}: {body[:300]}"))
                            return
                        for raw in resp.iter_lines():
                            line = (raw or "").strip()
                            if not line:
                                continue
                            if line.startswith("data:"):
                                line = line[5:].strip()
                            if not line:
                                continue
                            if line == "[DONE]":
                                break
                            try:
                                obj = json.loads(line)
                            except json.JSONDecodeError:
                                continue  # HTML interstitial / keep-alive comment
                            choices = obj.get("choices") or []
                            if not choices:
                                continue
                            delta = choices[0].get("delta") or {}
                            piece = delta.get("content")
                            if piece is None:
                                piece = choices[0].get("text")
                            if piece:
                                got_any = True
                                parts.append(piece)
                                yield ("delta", piece)
            except Exception:  # noqa: BLE001 - stream must never raise
                yield from _blocking_fallback()
                return

            content = "".join(parts)
            if not got_any:
                # Nothing usable streamed (e.g. non-JSON body) -> blocking fallback once.
                yield from _blocking_fallback()
                return
            latency = int((time.time() - started) * 1000)
            yield ("done", LlmResult(
                content=content, model=model, used_json_object=used_json, latency_ms=latency))
            return

        yield from _blocking_fallback()


# ---- lazy singleton (never touches the network at import time) ---------------
_client: Optional[LlmClient] = None


def get_client() -> LlmClient:
    global _client
    if _client is None:
        # Client defaults are the SQL/planner params (temperature 0, short output). The
        # analytic writer (Phase 15) passes LLM_TEMPERATURE_WRITER / LLM_MAX_TOKENS_WRITER
        # per call via chat()/stream_chat() overrides.
        _client = LlmClient(
            base_url=config.LLM_BASE_URL,
            model=config.LLM_MODEL,
            api_key=config.LLM_API_KEY,
            timeout=config.LLM_TIMEOUT,
            temperature=config.LLM_TEMPERATURE_SQL,
            max_tokens=config.LLM_MAX_TOKENS_SQL,
            ngrok_skip=config.LLM_NGROK_SKIP_WARNING,
            try_json_object=config.LLM_TRY_JSON_OBJECT,
            model_fallback=config.LLM_MODEL_FALLBACK,
        )
    return _client
