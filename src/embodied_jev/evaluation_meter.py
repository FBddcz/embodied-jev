"""Benchmark-only HTTP metering; never changes workbench policy decisions."""
from __future__ import annotations

import time


def token_summary(records, attempts):
    """Missing provider usage is unknown, including failed or unreported calls."""
    result = {"source": "provider_reported", "attempts": attempts,
              "http_requests": len(records)}
    for side in ("input", "output"):
        counts = [r[f"{side}_tokens"] for r in records if r.get(f"{side}_tokens") is not None]
        result[f"{side}_reported_calls"] = len(counts)
        result[f"{side}_reported_tokens"] = sum(counts)
        result[f"{side}_complete"] = len(counts) == attempts
    result["complete"] = result["input_complete"] and result["output_complete"]
    return result


def make_policy(provider, connection=None):
    # Resolve at call time so existing tests/adapters can inject the policy.
    from .policies import DecisionPolicy

    class MeteredPolicy(DecisionPolicy):
        def _post(self, url, **kwargs):
            started = time.perf_counter()
            record = {"index": len(self.api_calls), "http_status": None, "model": None,
                      "input_tokens": None, "output_tokens": None}
            try:
                response = super()._post(url, **kwargs)
                record["http_status"] = response.status_code
                # Capture usage even when downstream status/decision validation fails.
                try:
                    body = response.json()
                except (ValueError, UnicodeError):
                    body = None
                if isinstance(body, dict):
                    model = body.get("model")
                    if isinstance(model, str) and model.strip() and len(model) <= 256:
                        key = self.connection.get("key", "")
                        record["model"] = model.replace(key, "[hidden]") if key else model
                    usage = body.get("usage")
                    names = ("prompt_tokens", "completion_tokens") if self.provider == "chat" else ("input_tokens", "output_tokens")
                    if isinstance(usage, dict):
                        for side, name in zip(("input", "output"), names):
                            value = usage.get(name)
                            if type(value) is int and value >= 0:
                                record[f"{side}_tokens"] = value
                return response
            except Exception as exc:
                record["error_type"] = type(exc).__name__
                raise
            finally:
                record["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
                self.api_calls.append(record)

    policy = MeteredPolicy(provider, connection=connection) if connection else MeteredPolicy(provider)
    policy.api_calls = []
    return policy
