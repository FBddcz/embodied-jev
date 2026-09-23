"""Run nine real API trials through an already running local EmbodiedJev server.

Start the server from this checkout, save an OpenAI-compatible connection in
the UI, then run this script. It reuses that connection without reading its Key,
accessing the system keyring, or changing the model/endpoint configuration.
Real API calls may incur charges: three tasks x seeds 0,1,2, at most 12 actions
and 600 seconds per episode (at most 216 model calls in the complete batch).

The first transfer trial is the pilot and counts toward the nine trials. API
errors, timeouts, or external intervention stop the batch; ordinary physical
task failures are recorded and the next trial runs. Existing output directories
are refused, so an interrupted batch is never silently retried or overwritten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import socket
import statistics
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8090"
SETUP = {"provider": "chat", "preview": True, "threshold": 0, "max_cycles": 12, "speed": 4}
TRIALS = [(task, seed) for task in ("transfer", "stack", "barrier") for seed in range(3)]
MAX_SECONDS = 600
MAX_CALLS = 216
ACTIVE = {"running", "paused", "uncertain"}
TERMINAL = {"completed", "stopped", "error", "exhausted", "stalled", "uncertain", "paused", "timeout"}


class BenchmarkError(Exception):
    """A diagnostic safe to print without upstream response bodies."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def repository_commit():
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO,
                                check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError):
        raise BenchmarkError("Run this script from a Git checkout with Git installed.") from None
    return result.stdout.strip()


class ServerBenchmark:
    def __init__(self, output, model, commit):
        self.output, self.model, self.commit = output, model, commit
        self.episodes = []
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def api(self, path, payload=None, timeout=5):
        if not path.startswith("/api/") or ".." in path or ":" in path:
            raise BenchmarkError("Only fixed loopback API paths are allowed.")
        request = urllib.request.Request(BASE + path,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={} if payload is None else {"Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            # In particular, never follow a redirect or print a response body.
            raise BenchmarkError(f"Local server returned HTTP {exc.code}; batch stopped.") from None
        except (urllib.error.URLError, TimeoutError, socket.timeout):
            raise BenchmarkError("Local server request failed or timed out; batch stopped.") from None
        except (ValueError, UnicodeError):
            raise BenchmarkError("Local server returned invalid JSON; batch stopped.") from None

    def save(self, name, value):
        target = self.output / name
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(target)

    def emit(self, event, **fields):
        entry = {"event": event, "utc": utc_now(), **fields}
        with (self.output / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(json.dumps(entry, ensure_ascii=False), flush=True)

    def check_connection(self):
        # /api/connections exposes public metadata, never Key values. Retain
        # only the expected model and verification, not endpoint information.
        entry = self.api("/api/connections").get("chat", {})
        if entry.get("model") != self.model or not entry.get("key_configured"):
            raise BenchmarkError("Save the requested chat model and API Key in the local UI first.")
        return entry.get("verification")

    def check_inactive(self):
        state = self.api("/api/state")
        if state.get("status") in ACTIVE:
            raise BenchmarkError("An existing workbench experiment is active; it was preserved.")
        comparison = self.api("/api/comparison")
        if comparison.get("status") in {"running", "paused", "queued"}:
            raise BenchmarkError("An existing comparison is active; it was preserved.")
        return state, comparison

    def prepare(self):
        verification = self.check_connection()
        state, comparison = self.check_inactive()
        previous = self.api("/api/export")
        if previous.get("id") != state.get("id"):
            raise BenchmarkError("Workbench changed during backup; batch stopped.")
        try:
            self.output.parent.mkdir(parents=True, exist_ok=True)
            self.output.mkdir()  # Exclusive claim, including against another runner.
        except FileExistsError:
            raise BenchmarkError("Output already exists. Choose a new --output directory; nothing was overwritten.") from None
        self.save("before-experiment-export.json", previous)
        self.save("before-experiment-comparison.json", comparison)
        self.save("metadata.json", {
            "started_at": utc_now(), "repository_commit": self.commit,
            "commit_scope": "Local Git HEAD; start the server from this same checkout.",
            "provider": "chat", "configured_model": self.model, "prior_verification": verification,
            "setup": SETUP, "wall_timeout_seconds": MAX_SECONDS, "max_model_calls": MAX_CALLS,
            "trials": [{"task": task, "seed": seed} for task, seed in TRIALS],
            "api_protocol": "OpenAI-compatible Chat Completions, generated JSON choice",
            "connection_access": "Existing server-side chat connection; no credentials read or endpoint retained",
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        })
        self.save_summary()

    def save_summary(self):
        latencies = [value for row in self.episodes for value in row["model_latency_ms"]]
        def complete_total(field):
            values = [row[field] for row in self.episodes]
            return sum(values) if all(value is not None for value in values) else None

        self.save("summary.json", {
            "format": "embodied-jev-api-experiment-summary-v1", "updated_at": utc_now(),
            "commit": self.commit, "provider": "chat", "configured_model": self.model,
            "setup": SETUP, "wall_timeout_seconds": MAX_SECONDS, "planned_episodes": len(TRIALS),
            "aggregate": {
                "episodes": len(self.episodes), "successes": sum(row["success"] for row in self.episodes),
                "model_calls": sum(row["model_calls"] for row in self.episodes),
                "input_tokens": complete_total("input_tokens"),
                "output_tokens": complete_total("output_tokens"),
                "latency_mean_ms": statistics.mean(latencies) if latencies else None,
                "latency_median_ms": statistics.median(latencies) if latencies else None,
                "forbidden_contact_episodes": sum(row["forbidden_contact"] for row in self.episodes),
            },
            "episodes": self.episodes,
            "limitations": [
                "Resolved model names are reported by the configured API service.",
                "Generated JSON choice provides no token-logit probabilities.",
                "Tokens are exact episode usage totals reported by the service; per-call usage is not exported.",
                "Wall time includes network and speed=4 pacing; it is not directly comparable with speed=0 headless runs.",
                "After a stop, an already admitted upstream request may finish; no further decisions are started.",
            ],
        })

    def stop_own_episode(self, episode_id):
        try:
            state = self.api("/api/control/stop", {"episode_id": episode_id})
            return state.get("id") == episode_id and state.get("status") in TERMINAL
        except BenchmarkError:
            # A stale ID must never stop the replacement episode.
            return False

    def preserve_interrupted(self, run_id, episode_id):
        self.stop_own_episode(episode_id)
        try:
            exported = self.api("/api/export")
            if exported.get("id") == episode_id:
                self.save(f"{run_id}-interrupted-episode.json", exported)
        except BenchmarkError:
            pass

    def run_episode(self, task, seed):
        run_id = f"{task}-seed{seed}"
        if any(self.output.glob(f"{run_id}-*")):
            raise BenchmarkError("Refusing to repeat or overwrite a recorded trial.")
        self.check_connection()
        previous, _ = self.check_inactive()
        request = {**SETUP, "task": task, "seed": seed, "expected_episode_id": previous["id"]}
        state = self.api("/api/reset", request)
        episode_id = state["id"]
        self.save(f"{run_id}-request.json", {"utc": utc_now(), "episode_id": episode_id,
            "setup": request, "configured_model": self.model, "commit": self.commit})
        started_at, started = utc_now(), time.monotonic()
        deadline = started + MAX_SECONDS
        timed_out, first_response, last_heartbeat = False, False, started
        self.emit("episode_started", run_id=run_id, episode_id=episode_id)
        try:
            state = self.api("/api/control/start", {"episode_id": episode_id})
            while True:
                if state.get("id") != episode_id:
                    raise BenchmarkError("Workbench was changed externally; batch stopped.")
                answers = [state.get("last_intent"), state.get("last_decision")]
                answers += [record.get(field) for record in state.get("history", [])
                            for field in ("intent", "decision")]
                valid = next((answer for answer in answers if answer and answer.get("model_call")), None)
                if valid and not first_response:
                    first_response = True
                    self.emit("first_valid_model_response", run_id=run_id,
                              resolved_model=valid.get("model"), choice=valid["choice"],
                              latency_ms=valid["latency_ms"])
                if state.get("status") in TERMINAL:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    if not self.stop_own_episode(episode_id):
                        raise BenchmarkError("Timeout reached but stop could not be confirmed; check the local workbench.")
                    state = self.api("/api/state")
                    if state.get("id") != episode_id:
                        raise BenchmarkError("Workbench changed at the timeout boundary; batch stopped.")
                    break
                if time.monotonic() - last_heartbeat >= 30:
                    self.emit("episode_progress", run_id=run_id, status=state["status"],
                              cycles=state["cycles"], model_calls=state["model_calls"])
                    last_heartbeat = time.monotonic()
                time.sleep(min(0.2, remaining))
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    continue
                try:
                    state = self.api("/api/state", timeout=min(5, remaining))
                except BenchmarkError:
                    if time.monotonic() < deadline:
                        raise
                    # Let the deadline branch issue the ID-guarded stop.
            observed_status = state["status"]
            if observed_status in {"uncertain", "paused"}:
                self.stop_own_episode(episode_id)
            exported = self.api("/api/export")
            if exported.get("id") != episode_id:
                raise BenchmarkError("Workbench changed before export; unrelated data was not saved.")
            self.save(f"{run_id}-episode.json", exported)
        except BaseException:
            self.preserve_interrupted(run_id, episode_id)
            raise

        frames = exported.get("frames", [])
        final = frames[-1]["observation"] if frames else {}
        latencies = exported["model_latency_ms"]
        message = exported.get("message") or ""
        api_failure = observed_status == "error" and any(
            marker in message for marker in ("模型决策失败", "模型接口请求失败"))
        row = {
            "run_id": run_id, "episode_id": episode_id, "task": task, "seed": seed,
            "provider": "chat", "configured_model": self.model, "resolved_model": exported["model"],
            "commit": self.commit, "policy_version": exported["policy_version"],
            "scene_hash": exported["scene_hash"], "setup": SETUP,
            "started_at": started_at, "finished_at": utc_now(),
            "status": "timeout" if timed_out else observed_status, "export_status": exported["status"],
            "stop_reason": "hard_wall_timeout_600s" if timed_out else message or observed_status,
            "success": bool(exported["success"]), "cycles": state["cycles"],
            "model_calls": exported["model_calls"], "input_tokens": exported["input_tokens"],
            "output_tokens": exported["output_tokens"], "model_latency_ms": latencies,
            "latency_mean_ms": statistics.mean(latencies) if latencies else None,
            "latency_median_ms": statistics.median(latencies) if latencies else None,
            "wall_seconds": exported["wall_seconds"], "runner_wall_seconds": round(time.monotonic() - started, 4),
            "sim_seconds": final.get("sim_seconds"), "max_lift_m": final.get("max_lift_m"),
            "forbidden_contact": any(frame["observation"].get("forbidden_contact", False) for frame in frames),
            "final_observation": final, "systemic_api_error": api_failure,
            "episode_file": f"{run_id}-episode.json",
        }
        self.save(f"{run_id}-summary.json", row)
        self.episodes.append(row)
        self.save_summary()
        self.emit("episode_finished", **{key: row[key] for key in (
            "run_id", "status", "success", "cycles", "model_calls", "input_tokens", "output_tokens",
            "latency_mean_ms", "wall_seconds", "sim_seconds", "forbidden_contact")})
        if (api_failure or timed_out or observed_status in {"paused", "stopped", "uncertain", "timeout"}
                or (len(self.episodes) == 1 and not first_response)):
            raise BenchmarkError("API error, timeout, intervention, or no valid model response; remaining trials cancelled.")
        if row["model_calls"] > 2 * SETUP["max_cycles"]:
            raise BenchmarkError("Unexpected model-call count; remaining trials cancelled.")

    def run(self):
        self.prepare()
        for task, seed in TRIALS:
            if sum(row["model_calls"] for row in self.episodes) + 2 * SETUP["max_cycles"] > MAX_CALLS:
                raise BenchmarkError("Insufficient remaining model-call budget.")
            self.run_episode(task, seed)
        self.emit("batch_finished", episodes=len(self.episodes),
                  successes=sum(row["success"] for row in self.episodes),
                  model_calls=sum(row["model_calls"] for row in self.episodes))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=Path("runs/gpt6-server"),
                        help="New output directory, relative to the current directory; existing paths are refused.")
    parser.add_argument("--model", default="gpt-6-astra",
                        help="Expected model already saved for the chat provider; does not change the connection.")
    parser.add_argument("--expected-commit", help="Optional full Git commit SHA or prefix (at least 7 hex characters).")
    args = parser.parse_args()
    if not args.model.strip() or len(args.model) > 256:
        parser.error("--model must contain 1–256 characters")
    if args.expected_commit and (not 7 <= len(args.expected_commit) <= 40 or
                                any(ch not in "0123456789abcdefABCDEF" for ch in args.expected_commit)):
        parser.error("--expected-commit must be a 7–40 character hexadecimal SHA")
    try:
        if args.output.exists() or args.output.is_symlink():
            raise BenchmarkError("Output already exists. Choose a new --output directory; nothing was overwritten.")
        commit = repository_commit()
        if args.expected_commit and not commit.startswith(args.expected_commit.lower()):
            raise BenchmarkError("Git HEAD differs from --expected-commit; no server requests were sent.")
        ServerBenchmark(args.output, args.model.strip(), commit).run()
    except BenchmarkError as exc:
        parser.exit(1, f"Stopped: {exc}\n")
    except KeyboardInterrupt:
        parser.exit(130, "Interrupted; an ID-guarded stop was requested for this runner's active episode.\n")
    except Exception as exc:
        # Unknown errors may include sensitive upstream data. Print only type.
        parser.exit(1, f"Stopped after {type(exc).__name__}; inspect the saved local artifacts.\n")


if __name__ == "__main__":
    main()
