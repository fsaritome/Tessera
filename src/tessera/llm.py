"""Minimal client for an OpenAI-compatible chat endpoint on local infrastructure.

Refuses non-local endpoints unless allow_cloud=True is passed for that run.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import urllib.request
from urllib.parse import urlparse

from .config import Config


class LLMError(RuntimeError):
    pass


def _is_local(host: str, local_hosts: list[str]) -> bool:
    if host.lower() in {h.lower() for h in local_hosts}:
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not (ip.is_private or ip.is_loopback or ip.is_link_local):
            return False
    return bool(infos)


class LocalLLM:
    def __init__(self, cfg: Config, allow_cloud: bool = False):
        self.base_url = os.environ.get("TESSERA_LLM_URL") or cfg.get("llm.base_url")
        self.model = os.environ.get("TESSERA_LLM_MODEL") or cfg.get("llm.model")
        self.timeout = cfg.get("llm.timeout_s", 180)
        self.temperature = cfg.get("llm.temperature", 0.2)
        if not self.base_url or not self.model:
            raise LLMError("LLM not configured: set TESSERA_LLM_URL and TESSERA_LLM_MODEL.")
        host = urlparse(self.base_url).hostname or ""
        if not allow_cloud and not _is_local(host, cfg.get("llm.local_hosts") or []):
            raise LLMError(
                f"Endpoint host '{host}' is not local. Refusing to send data. "
                "Use --allow-cloud for this run if you really mean it."
            )

    def chat_json(self, system: str, user: str) -> dict:
        body = {
            "model": self.model,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        headers = {"Content-Type": "application/json"}
        if key := os.environ.get("TESSERA_LLM_API_KEY"):
            headers["Authorization"] = f"Bearer {key}"
        req = urllib.request.Request(
            self.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(body).encode(),
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.load(resp)
        except OSError as e:
            raise LLMError(f"LLM request failed: {e}") from e
        text = data["choices"][0]["message"]["content"]
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < start:
            raise LLMError("LLM did not return JSON.")
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as e:
            raise LLMError(f"LLM returned invalid JSON: {e}") from e
