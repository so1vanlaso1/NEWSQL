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

import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

from backend import config


@dataclass
class LlmResult:
    content: str = ""
    model: str = ""
    usage: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)
    error: Optional[str] = None
    used_json_object: bool = False
    latency_ms: int = 0


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
        temperature: float = 0.0,
        max_tokens: int = 1200,
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
        print(f"[llm] resolved model id: {model!r} (base={self.base_url})")
        return model

    # ---- chat ----
    def _post(self, payload: dict) -> httpx.Response:
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as c:
            return c.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            )

    def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LlmResult:
        model = self.resolve_model()
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }
        used_json = False
        if self.try_json_object:
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
            )
        except Exception as exc:  # noqa: BLE001 - client must never raise
            latency = int((time.time() - started) * 1000)
            return LlmResult(model=model, used_json_object=used_json, latency_ms=latency,
                             error=f"{exc.__class__.__name__}: {exc}")


# ---- lazy singleton (never touches the network at import time) ---------------
_client: Optional[LlmClient] = None


def get_client() -> LlmClient:
    global _client
    if _client is None:
        _client = LlmClient(
            base_url=config.LLM_BASE_URL,
            model=config.LLM_MODEL,
            api_key=config.LLM_API_KEY,
            timeout=config.LLM_TIMEOUT,
            temperature=config.LLM_TEMPERATURE,
            max_tokens=config.LLM_MAX_TOKENS,
            ngrok_skip=config.LLM_NGROK_SKIP_WARNING,
            try_json_object=config.LLM_TRY_JSON_OBJECT,
            model_fallback=config.LLM_MODEL_FALLBACK,
        )
    return _client
