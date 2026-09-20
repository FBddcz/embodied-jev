# Validation Scope

## Deterministic Physics Baseline

Recorded on 2026-09-20, macOS arm64, Python 3.11.15, MuJoCo 3.13.0. Reproduce with:

```bash
embodied-jev benchmark --seeds 0 1 2 --tasks transfer stack barrier --output runs/benchmark.json
```

`benchmark-baseline.json` records all nine episodes, including failures if any. This run: 9/9 passed, eight executed actions per episode, zero monitored forbidden-contact steps. Seeds perturb the source position by at most 25 mm per horizontal axis. Cross-platform floating-point behavior may differ.

Tests also check preview isolation, out-of-bounds target rejection, grasp/support evidence, JSON exports, pose replay, provider contract validation and cancellation during delayed inference. The UI suite captures desktop/mobile screenshots, checks canvas pixels and scene movement, and exercises controls.

## Models

The baseline makes zero model calls and emits no model probabilities. TypeSafe, Claude native, chat and local HTTP adapters are checked with mocked responses. No live TypeSafe or Claude call has been validated without credentials. MiniCPM5-2B **has been run with the full real weights** on an Apple M2 with 16 GB unified memory, using MPS and FP16 (Torch 2.6.0 / Transformers 4.57.6). Its measured task failures are recorded below.

### MiniCPM5-2B real inference: current limitations

The pinned revision is `12a3808a956f869c767195e9266b59c4d21d92e2`. The 5,033,557,096-byte safetensors file was verified against SHA256 `14fb8e7f0a18d53d1f239773758bf581cee7e456a4523a54622c3a245b64402c`. No rule fallback was used.

| Development run | Tasks / seed | Threshold | Result | Calls / executed actions per episode |
| --- | --- | --- | --- | --- |
| Initial full-state prompt | transfer, stack, barrier / 0 | 0.55 | 0/3; all stopped as `uncertain` before moving | 1 / 0 |
| Initial full-state prompt | transfer, stack, barrier / 0 | 0 | 0/3; repeated approach until budget exhausted | 59 / 30 |
| `phase-conditions-v2` | transfer / 0 | 0 | 0/1; still repeated approach | 59 / 30 |

Loading plus one warmup decision took 18.75–29.35 seconds across these processes. With threshold 0, initial full-state episodes took 132.18, 142.77 and 154.61 seconds; the v2 transfer episode took 202.64 seconds. Individual decisions across these threshold-0 runs took approximately 1.13–5.57 seconds, including variable background load. These are developer-machine measurements, not a controlled speed comparison.

All runs had zero measured lift and zero monitored forbidden contacts. Avoiding contact while failing to progress is **not** successful manipulation. MPS current tensor allocation was about 5.03 GB; reported driver allocation was 5.63–6.54 GB. RSS and MPS counters overlap on unified memory and must not be added as total RAM.

The [machine-readable report](results/minicpm-fp16-2026-09-20.json) includes all episode results, latency samples, warmups and memory counters, with links to compressed full episode JSON files alongside it. Initial runs predated prompt-version recording, so their exact code commit was not recorded; they are diagnostic evidence, not fully controlled benchmark submissions. The current CLI records `policy_version` for subsequent runs.

A short follow-up probe showed that simpler wording could change the post-approach choice from approach to descend. That one-state probe has not established full-task success. Work remains on the evidence representation and decision prompt; MiniCPM is integrated, but is not yet a reliable controller for these three tasks.

The short phase menus, fixed orientation, supplied destination and prewritten motion primitives simplify planning significantly. Passing this baseline does not demonstrate general robot intelligence or sim-to-real transfer. Probability gating has not been calibrated on manipulation episodes. Disabling previews disables candidate rollout checks but keeps workspace checks and the live contact stop.

Additional local verification includes connection-key redaction, no network call on saving credentials, mocked provider requests, headless uncertainty/timeout handling, and sparse vocabulary projection against full logits on a tiny randomly initialized Llama. This checks the calculation, not MiniCPM model quality. The pinned real MiniCPM5-2B tokenizer passed complete-prefix checks for empty history, nested empty lists and numeric object coordinates: A/B/C mapped to token IDs 54/55/56 in all three cases. Reproduce with `python scripts/check_minicpm_tokenizer.py` after installing `.[minicpm]`; tokenizer files require a small download, weights are not loaded.

The initial GitHub browser run timed out while checking camera pixel changes. After switching the workbench to render on scene, camera and pose changes, reducing the shadow-map size and allowing 15 seconds for visual assertions, [CI run 35488205230](https://github.com/FBddcz/embodied-jev/actions/runs/35488205230) passed. The pixel-change assertions remain enabled. This establishes the passing result, not a proven single root cause for the earlier failure.

## Replay

The timeline re-renders recorded joint/object configurations. It is geometric replay of an episode, not re-execution from saved solver state and not an independent dynamics verification. The export contains observations and decisions for analysis; it does not contain weights, credentials or raw camera frames.
