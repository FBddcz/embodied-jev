# Validation Scope

## Deterministic Physics Baseline

Recorded on 2026-09-20, macOS arm64, Python 3.11.15, MuJoCo 3.13.0. Reproduce with:

```bash
embodied-jev benchmark --seeds 0 1 2 --tasks transfer stack barrier --output runs/benchmark.json
```

`benchmark-baseline.json` records all nine episodes, including failures if any. This run: 9/9 passed, eight executed actions per episode, zero monitored forbidden-contact steps. Seeds perturb the source position by at most 25 mm per horizontal axis. Cross-platform floating-point behavior may differ.

Tests also check preview isolation, out-of-bounds target rejection, grasp/support evidence, JSON exports, pose replay, provider contract validation and cancellation during delayed inference. The UI suite captures desktop/mobile screenshots, checks canvas pixels and scene movement, and exercises controls.

## Models

The baseline makes zero model calls and emits no model probabilities. TypeSafe and local HTTP adapters are checked with mocked responses. MiniCPM5-2B uses a pinned standard Llama model through Transformers. Full model weights and a paid TypeSafe key were not used in this validation; no model task success or latency measurement is claimed.

The short phase menus, fixed orientation, supplied destination and prewritten motion primitives simplify planning significantly. Passing this baseline does not demonstrate general robot intelligence or sim-to-real transfer. Probability gating has not been calibrated on manipulation episodes. Disabling previews disables candidate rollout checks but keeps workspace checks and the live contact stop.

Additional local verification: 28 Python tests passed, including connection-key redaction, no network call on saving credentials, mocked chat requests, and sparse vocabulary projection against full logits on a tiny randomly initialized Llama. This checks the calculation, not MiniCPM model quality. The pinned real MiniCPM5-2B tokenizer passed complete-prefix checks for empty history, nested empty lists and numeric object coordinates: A/B/C mapped to token IDs 54/55/56 in all three cases. Reproduce with `python scripts/check_minicpm_tokenizer.py` after installing `.[minicpm]`; tokenizer files require a small download, weights are not loaded.

## Replay

The timeline re-renders recorded joint/object configurations. It is geometric replay of an episode, not re-execution from saved solver state and not an independent dynamics verification. The export contains observations and decisions for analysis; it does not contain weights, credentials or raw camera frames.
