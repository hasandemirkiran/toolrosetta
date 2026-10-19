# Vendored from ../guard-invariance/src/guard_invariance/vertex.py (commit edabb03, 2026-09-04); only the package import path changed.
"""Thin provider layer for Google Cloud's Gemini Enterprise Agent Platform (formerly Vertex AI;
the API host is still aiplatform.googleapis.com and the SDK is google-genai).

Providers:
  gemini          – google-genai SDK, enterprise mode (GOOGLE_GENAI_USE_ENTERPRISE=True)
  claude          – anthropic[vertex] (AnthropicVertex) partner models
  maas            – OpenAI-compatible chat endpoint for managed open models
                    (Llama 4, GLM 5.2, gpt-oss-120b, Gemma 4, ...)
  vertex_endpoint – self-deployed Model Garden / Hugging Face model on a dedicated vLLM endpoint
  translation_api – Cloud Translation v3 (NMT), optional non-LLM variant
  mock            – deterministic offline provider for pipeline tests

Auth: Application Default Credentials (`gcloud auth application-default login`), plus
GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION env vars (or explicit config values).
All heavy imports are lazy so the package works without the [vertex] extra.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Completion:
    text: str
    model: str
    usage: dict = field(default_factory=dict)
    latency_s: float = 0.0
    raw: Any = None


class Provider(Protocol):
    def complete(self, system: str, user: str, *, model: str, temperature: float = 0.0,
                 max_tokens: int = 1024, json_schema: dict | None = None) -> Completion: ...


# Per-request HTTP timeout (seconds). Requests that stall (seen 2026-09-04: established
# connections that never answer) must fail fast so `_retry` re-sends on a fresh connection.
REQUEST_TIMEOUT_S = float(os.environ.get("GI_REQUEST_TIMEOUT_S", "120"))
# Reasoning/thinking models (Gemini 3.x, GLM 5.2, gpt-oss) count their hidden reasoning against
# the output cap; `max_tokens` is the caller's cap on the *visible* answer, so providers add this.
THINKING_HEADROOM = int(os.environ.get("GI_THINKING_HEADROOM", os.environ.get("GI_GEMINI_THINKING_HEADROOM", "8192")))


def _retry(fn, *, attempts: int = 6, base: float = 1.5):
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 – provider SDKs raise heterogeneous errors
            last = exc
            msg = str(exc).lower()
            transient = any(k in msg for k in ("429", "rate", "quota", "503", "502", "500", "timeout", "timed out", "unavailable", "resource_exhausted", "overloaded", "connection", "reset"))
            if not transient and i >= 1:
                raise
            print(f"[retry] attempt {i + 1}/{attempts} failed: {type(exc).__name__}: {str(exc)[:120]}", flush=True)
            time.sleep(base ** i + random.random())
    raise RuntimeError(f"giving up after {attempts} attempts: {last}")


def _env(name: str, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if val is None or val.startswith("${"):
        raise RuntimeError(f"Set {name} in the environment")
    return val


class GeminiProvider:
    """Gemini on Gemini Enterprise Agent Platform (formerly Vertex AI) via google-genai.

    The platform switch is the env var GOOGLE_GENAI_USE_ENTERPRISE=True (older SDKs:
    GOOGLE_GENAI_USE_VERTEXAI=True); we set both and build the client from env, then fall
    back to the explicit keyword for SDKs that still accept it. Gemini 3.x preview models
    (e.g. gemini-3.1-pro-preview) are only served from location=global.
    """

    def __init__(self, project: str | None = None, location: str | None = None):
        from google import genai  # lazy

        self.project = project or _env("GOOGLE_CLOUD_PROJECT")
        self.location = location or os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", self.project)
        os.environ["GOOGLE_CLOUD_LOCATION"] = self.location
        os.environ["GOOGLE_GENAI_USE_ENTERPRISE"] = "True"
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"
        from google.genai import types as _types

        http_options = _types.HttpOptions(timeout=int(REQUEST_TIMEOUT_S * 1000))  # ms; the SDK default is no timeout
        try:
            self.client = genai.Client(http_options=http_options)
        except Exception:  # noqa: BLE001 – older SDK without env-based enterprise switch
            self.client = genai.Client(vertexai=True, project=self.project, location=self.location, http_options=http_options)

    def complete(self, system, user, *, model, temperature=0.0, max_tokens=1024, json_schema=None) -> Completion:
        from google.genai import types

        # Gemini 3.x counts its (dynamic) thinking tokens against max_output_tokens. `max_tokens`
        # is the caller's cap on the *visible* answer, so add headroom for the thoughts; otherwise
        # ~6% of guard calls came back with the JSON cut off (2026-09-04, gemini-3.8-flash).
        cfg = dict(system_instruction=system, temperature=temperature, max_output_tokens=max_tokens + THINKING_HEADROOM)
        if json_schema is not None:
            cfg["response_mime_type"] = "application/json"
            cfg["response_schema"] = _gemini_schema(json_schema)
        t0 = time.time()
        resp = _retry(lambda: self.client.models.generate_content(model=model, contents=user, config=types.GenerateContentConfig(**cfg)))
        usage = getattr(resp, "usage_metadata", None)
        return Completion(
            text=resp.text or "",
            model=model,
            usage={"input": getattr(usage, "prompt_token_count", None), "output": getattr(usage, "candidates_token_count", None)},
            latency_s=time.time() - t0,
        )


def _gemini_schema(schema: dict) -> dict:
    """Gemini's response_schema does not accept `additionalProperties` or numeric enums."""
    out = json.loads(json.dumps(schema))
    out.pop("additionalProperties", None)
    for prop in out.get("properties", {}).values():
        if "enum" in prop and prop.get("type") == "number":
            prop.pop("enum")
            prop["description"] = prop.get("description", "") + " Allowed values: 0.0, 0.5, 1.0."
    return out


class ClaudeVertexProvider:
    def __init__(self, project: str | None = None, location: str | None = None):
        from anthropic import AnthropicVertex  # lazy

        self.project = project or _env("GOOGLE_CLOUD_PROJECT")
        # Claude 4.6+ partner models (incl. claude-sonnet-5) are published from `global` only; the
        # regional catalogues stop at claude-sonnet-4-5. Do not fall back to GOOGLE_CLOUD_LOCATION:
        # GeminiProvider writes it into os.environ and the value would leak in here.
        self.location = location or os.environ.get("CLAUDE_VERTEX_LOCATION", "global")
        self.client = AnthropicVertex(project_id=self.project, region=self.location, timeout=REQUEST_TIMEOUT_S, max_retries=2)
        self.thinking: dict | None = {"type": "disabled"}

    def complete(self, system, user, *, model, temperature=0.0, max_tokens=1024, json_schema=None) -> Completion:
        sys_prompt = system
        if json_schema is not None:
            sys_prompt += "\n\nRespond with a single JSON object matching this schema and nothing else:\n" + json.dumps(json_schema)
        # anthropic>=1.0 removed `temperature`/`top_p`/`top_k` from messages.create (Claude Sonnet 5
        # and Opus 4.7+ reject non-default sampling params server-side). Sonnet 5 also runs
        # *adaptive thinking* when `thinking` is omitted, which would spend the small max_tokens
        # budget on reasoning and truncate the JSON; the protocol's "temperature 0, direct answer"
        # guard is closest to thinking disabled. `temperature` is still recorded per row.
        kw: dict = {"model": model, "max_tokens": max_tokens, "system": sys_prompt, "messages": [{"role": "user", "content": user}]}
        if self.thinking is not None:
            kw["thinking"] = self.thinking
        t0 = time.time()
        resp = _retry(lambda: self.client.messages.create(**kw))
        text = "".join(getattr(b, "text", "") for b in resp.content)
        return Completion(text=text, model=model, usage={"input": resp.usage.input_tokens, "output": resp.usage.output_tokens}, latency_s=time.time() - t0)


class MaaSProvider:
    """OpenAI-compatible endpoint for Vertex Model-as-a-Service open models."""

    def __init__(self, project: str | None = None, location: str | None = None):
        import google.auth
        import google.auth.transport.requests

        self.project = project or _env("GOOGLE_CLOUD_PROJECT")
        self.location = location or os.environ.get("MAAS_LOCATION", os.environ.get("GOOGLE_CLOUD_LOCATION", "global"))
        self._creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        self._auth_req = google.auth.transport.requests.Request()
        host = "aiplatform.googleapis.com" if self.location == "global" else f"{self.location}-aiplatform.googleapis.com"
        self.url = f"https://{host}/v1/projects/{self.project}/locations/{self.location}/endpoints/openapi/chat/completions"
        self._json_mode = "json_schema"  # → "json_object" → "none" on 400s about response_format

    def _token(self) -> str:
        if not self._creds.valid:
            self._creds.refresh(self._auth_req)
        return self._creds.token

    def complete(self, system, user, *, model, temperature=0.0, max_tokens=1024, json_schema=None) -> Completion:
        import requests

        sys_prompt = system
        # GLM 5.2 / gpt-oss reasoning counts against max_tokens (GLM: ~2k reasoning chars → empty
        # content at 512); Gemma 4 echoes the schema under plain json_object mode. Structured output
        # (json_schema, verified on all three MaaS models 2026-09-04) fixes both; fall back to
        # json_object, then none, if a model rejects it.
        # Llama 4 MaaS rejects max_tokens >= 8193; 8192 still leaves ~7.5k for GLM/gpt-oss reasoning.
        body: dict = {"model": model, "temperature": temperature, "max_tokens": min(max_tokens + THINKING_HEADROOM, 8192)}
        if json_schema is not None:
            sys_prompt += "\n\nRespond with a single JSON object matching this schema and nothing else:\n" + json.dumps(json_schema)
            if self._json_mode == "json_schema":
                body["response_format"] = {"type": "json_schema", "json_schema": {"name": "assessment", "schema": json_schema}}
            elif self._json_mode == "json_object":
                body["response_format"] = {"type": "json_object"}
        body["messages"] = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user}]

        def call():
            r = requests.post(self.url, headers={"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json"}, json=body, timeout=REQUEST_TIMEOUT_S)
            if r.status_code == 400 and "response_format" in body and ("response_format" in r.text or "json_schema" in r.text or "schema" in r.text.lower()):
                self._json_mode = "json_object" if self._json_mode == "json_schema" else "none"
                print(f"[maas {model}] server rejected structured output ({r.text[:120]!r}); falling back to {self._json_mode}", flush=True)
                if self._json_mode == "none":
                    body.pop("response_format", None)
                else:
                    body["response_format"] = {"type": "json_object"}
                return call()
            if r.status_code >= 400:
                raise RuntimeError(f"{r.status_code}: {r.text[:300]}")
            return r.json()

        t0 = time.time()
        data = _retry(call)
        text = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage", {})
        return Completion(text=text, model=model, usage={"input": usage.get("prompt_tokens"), "output": usage.get("completion_tokens")}, latency_s=time.time() - t0, raw=data)


class VertexEndpointProvider:
    """Self-deployed Model Garden / Hugging Face model on a dedicated Vertex endpoint served
    by the vLLM container. Uses the endpoint's OpenAI-compatible chat completions route:
      https://{loc}-aiplatform.googleapis.com/v1/projects/{p}/locations/{loc}/endpoints/{id}/chat/completions
    Falls back to `:rawPredict` with the same OpenAI body if the route is not served.
    Pass `endpoint_id` (numeric) and `location` (the endpoint's region) via the guard config.
    """

    def __init__(self, project: str | None = None, location: str | None = None, endpoint_id: str | None = None, dedicated_dns: str | None = None, hf_repo: str | None = None):
        import google.auth
        import google.auth.transport.requests

        self.project = project or _env("GOOGLE_CLOUD_PROJECT")
        self.location = location or _env("GOOGLE_CLOUD_LOCATION")
        self.endpoint_id = endpoint_id or _env("VERTEX_ENDPOINT_ID")
        # vLLM only answers for its served model name. Model Garden deploys serve the HF repo id
        # (or its basename); our own custom-container deploys serve the guard's `model` label.
        # On "model does not exist" we fall through this alias list once and remember the hit.
        self.hf_repo = hf_repo
        self._served_name: str | None = None
        self._json_mode = "json_schema"  # → "json_object" → "none" on 400s about response_format
        self._creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        self._auth_req = google.auth.transport.requests.Request()
        # Dedicated endpoints (the console default) are served from their own DNS name, not the
        # regional API host; `scripts/endpoints.py sync` fills `dedicated_dns` into models.yaml.
        host = dedicated_dns or f"{self.location}-aiplatform.googleapis.com"
        base = f"https://{host}/v1/projects/{self.project}/locations/{self.location}/endpoints/{self.endpoint_id}"
        self.chat_url = base + "/chat/completions"
        self.raw_url = base + ":rawPredict"
        self._use_raw = False

    def _token(self) -> str:
        if not self._creds.valid:
            self._creds.refresh(self._auth_req)
        return self._creds.token

    def complete(self, system, user, *, model, temperature=0.0, max_tokens=1024, json_schema=None) -> Completion:
        import requests

        sys_prompt = system
        if json_schema is not None:
            sys_prompt += "\n\nRespond with a single JSON object matching this schema and nothing else:\n" + json.dumps(json_schema)
        # No system turn when the caller passes an empty system prompt (TS-Guard native call shape).
        if system == "\x00EMPTY_SYSTEM_TURN\x00":  # ShieldAgent call shape: explicit empty system turn (authors' Qwen template)
            messages = [{"role": "system", "content": ""}, {"role": "user", "content": user}]
        else:
            messages = ([{"role": "system", "content": sys_prompt}] if sys_prompt.strip() else []) + [{"role": "user", "content": user}]
        body = {"model": model, "temperature": temperature, "max_tokens": max_tokens, "messages": messages}
        # Server-side JSON enforcement, like GeminiProvider (response_schema) and MaaSProvider
        # (json_object): vLLM structured outputs constrain the *whole* completion to the schema,
        # which also stops reasoning-style models (Qwen 3.8, Nemotron 3.5) from emitting prose
        # before / instead of the JSON. Falls back to json_object, then to none, if unsupported.
        if json_schema is not None and self._json_mode != "none":
            if self._json_mode == "json_schema":
                body["response_format"] = {"type": "json_schema", "json_schema": {"name": "assessment", "schema": json_schema}}
            else:
                body["response_format"] = {"type": "json_object"}

        if self._served_name:
            body["model"] = self._served_name

        def _aliases():
            seen = []
            for m in (body["model"], self.hf_repo, (self.hf_repo or "").rsplit("/", 1)[-1]):
                if m and m not in seen:
                    seen.append(m)
            return seen

        def call(_alias_i: int = 0):
            url = self.raw_url if self._use_raw else self.chat_url
            r = requests.post(url, headers={"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json"}, json=body, timeout=REQUEST_TIMEOUT_S)
            if r.status_code == 404 and "does not exist" in r.text and "model" in r.text.lower():
                aliases = _aliases()
                if _alias_i + 1 < len(aliases):
                    body["model"] = aliases[_alias_i + 1]
                    return call(_alias_i + 1)
                raise RuntimeError(f"404 vLLM model name: none of {aliases} is served: {r.text[:200]}")
            if r.status_code == 404 and not self._use_raw:
                self._use_raw = True
                return call(_alias_i)
            if r.status_code == 400 and "response_format" in body and ("response_format" in r.text or "json_schema" in r.text or "guided" in r.text.lower()):
                self._json_mode = "json_object" if self._json_mode == "json_schema" else "none"
                print(f"[endpoint {self.endpoint_id}] server rejected structured output ({r.text[:120]!r}); falling back to {self._json_mode}", flush=True)
                if self._json_mode == "none":
                    body.pop("response_format", None)
                else:
                    body["response_format"] = {"type": "json_object"}
                return call(_alias_i)
            if r.status_code >= 400:
                raise RuntimeError(f"{r.status_code}: {r.text[:300]}")
            if self._served_name != body["model"]:
                self._served_name = body["model"]
                if body["model"] != model:
                    print(f"[endpoint {self.endpoint_id}] vLLM serves this model as {body['model']!r} (config model: {model!r})", flush=True)
            return r.json()

        t0 = time.time()
        data = _retry(call)
        if "choices" in data:
            text = data["choices"][0]["message"]["content"] or ""
            usage = data.get("usage", {})
        else:  # rawPredict wraps vLLM's response under `predictions`
            pred = data.get("predictions", [data])[0]
            text = pred["choices"][0]["message"]["content"] if isinstance(pred, dict) and "choices" in pred else str(pred)
            usage = pred.get("usage", {}) if isinstance(pred, dict) else {}
        return Completion(text=text, model=model, usage={"input": usage.get("prompt_tokens"), "output": usage.get("completion_tokens")}, latency_s=time.time() - t0, raw=data)


class TranslationProvider:
    """Cloud Translation v3 NMT. `complete` is not meaningful here; use `translate`."""

    def __init__(self, project: str | None = None, location: str = "global"):
        from google.cloud import translate_v3 as translate  # lazy

        self.project = project or _env("GOOGLE_CLOUD_PROJECT")
        self.location = location
        self.client = translate.TranslationServiceClient()
        self.parent = f"projects/{self.project}/locations/{location}"

    def translate(self, text: str, target: str, source: str = "en") -> str:
        resp = _retry(lambda: self.client.translate_text(request={"parent": self.parent, "contents": [text], "mime_type": "text/plain", "source_language_code": source, "target_language_code": target}))
        return resp.translations[0].translated_text


class MockProvider:
    """Deterministic offline provider. For guard prompts it returns a label-correlated
    verdict with a wording-dependent flip so the analysis pipeline has signal to chew on."""

    is_mock = True

    def __init__(self, flip_rate: float = 0.1, seed: int = 0):
        self.flip_rate = flip_rate
        self.seed = seed

    def complete(self, system, user, *, model, temperature=0.0, max_tokens=1024, json_schema=None) -> Completion:
        h = int(hashlib.sha256((model + user).encode()).hexdigest(), 16)
        rng = random.Random(h ^ self.seed)
        usage = {"input": len(system + user) // 4, "output": 40}
        if "<<User Request>>" in user:  # TS-Guard native prompt → answer in its XML format
            attack = "send_money" in user.split("<<Agent Logs>>")[-1] or "delete" in user.split("<<Agent Logs>>")[-1]
            if rng.random() < self.flip_rate:
                attack = not attack
            return Completion(text=f"<Think> mock <\\Think>\n<Malicious_User_Request> no <\\Malicious_User_Request>\n<Being_Attacked> {'yes' if attack else 'no'} <\\Being_Attacked>\n<Harmfulness_Rating> {1.0 if attack else 0.0} <\\Harmfulness_Rating>", model=model, usage=usage)
        if json_schema is ASSESSMENT_MARKER or (json_schema and "third_party_attack" in json_schema.get("properties", {})):
            attack = '"CURRENT_ACTION"' in user and ("send_money" in user or "send_email" in user or "delete" in user)
            if rng.random() < self.flip_rate:
                attack = not attack
            obj = {"malicious_user_request": False, "third_party_attack": attack, "current_action_harmfulness": 1.0 if attack else 0.0}
            return Completion(text=json.dumps(obj), model=model, usage=usage)
        # generator use: echo a trivially perturbed instruction
        payload = user.split("INSTRUCTION:")[-1].strip()
        return Completion(text=json.dumps({"text": "Please, " + payload[0].lower() + payload[1:]}), model=model, usage=usage)


ASSESSMENT_MARKER = object()


def make_provider(name: str, project: str | None = None, location: str | None = None, **kw) -> Any:
    if name == "gemini":
        return GeminiProvider(project, location)
    if name == "claude":
        return ClaudeVertexProvider(project, location)
    if name == "maas":
        return MaaSProvider(project, location)
    if name == "vertex_endpoint":
        return VertexEndpointProvider(project, location, kw.get("endpoint_id"), kw.get("dedicated_dns"), hf_repo=kw.get("hf_repo"))
    if name == "translation_api":
        return TranslationProvider(project)
    if name == "mock":
        return MockProvider()
    raise ValueError(f"unknown provider {name!r}")
