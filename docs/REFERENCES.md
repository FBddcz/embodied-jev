# Reference Mapping

Reviewed on 2026-09-20. These are architectural references, not a merged distribution or a claim of equivalent performance. Upstream repositories can change after this date.

| Open-source project | Idea used in EmbodiedJev | Scope of this implementation |
| --- | --- | --- |
| [openroboto-ai/jev-robot-control](https://github.com/openroboto-ai/jev-robot-control) | Phase followed by motor decision, contact-based success, recorded trajectories | Panda bounded motion candidates; no upstream XYZ-channel controller or comparison recordings |
| [FazalAAli/jev-robotics-demo](https://github.com/FazalAAli/jev-robotics-demo) | Simulate candidate actions in a scratch MuJoCo world | Copies MjData, checks contacts/grasp retention; two-finger Panda rather than Allegro hand |
| [BrendanH18/jev_fsd](https://github.com/BrendanH18/jev_fsd) | Code proposes physical alternatives; a typed model selects | Applies that separation to robot manipulation, not autonomous driving |
| [TarunTomar122/jev-askable-arm](https://github.com/TarunTomar122/jev-askable-arm) | Bounded robot primitives and explicit state | Closed candidate menu and IK targets |
| [RomanSlack/jev-drone](https://github.com/RomanSlack/jev-drone) | Separate model decisions from fast control | Python policy versus MuJoCo actuator loop |
| [amazedsaint/jevduck](https://github.com/amazedsaint/jevduck) | Measured outcomes, explicit control ownership | Pause/stop, measured postconditions, episode logs |
| [tripathiarpan20/openarm-jev-lab](https://github.com/tripathiarpan20/openarm-jev-lab) | Simulator/worker separation and inspectable experiments | Local worker plus browser workbench; LIBERO is not integrated |
| [TheoLeeCJ/SemIf](https://github.com/TheoLeeCJ/SemIf) | Direct finite-option probability readout; MiniCPM5-2B support | Independent single-prompt adapter with candidate boundary checks; no shared-prefix reuse, calibration or training |
| [MuJoCo Menagerie](https://github.com/google-deepmind/mujoco_menagerie) | Tested articulated robot model and assets | Vendored Panda, revision and license in third-party notices |

SemIf includes MiniCPM5-2B in its public model support/evaluations. It is therefore inaccurate to say nobody has used MiniCPM for Jev-style decisions. SemIf is a separate open-source project, not TypeSafe's official Jev implementation. Its classification benchmarks do not establish embodied manipulation results.

The TypeSafe adapter follows the [official HTTP schema](https://docs.typesafe.ai/api). A selected-option probability and the API's `confidence` are different quantities; both are retained separately when returned, and only selected probability gates this prototype.

## User-supplied MuJoCo demonstration

[Dmytro Hrybov's September 18, 2026 post](https://x.com/dimentary/status/2101018760371171420) describes splitting each update into intent selection and motor selection after a single-stage approach struggled. His [follow-up](https://x.com/dimentary/status/2101018934095003720) says the first call selects grasp/carry/release intent, the second chooses XYZ movement and finger commands, and the bars display Jev's output probabilities. Observations are simplified geometry and contacts supplied as text, not images. The accessible post does not itself link a source repository; no claim is made that another listed repository is this exact demo.

EmbodiedJev follows the same two-stage idea and displays phase and action probabilities separately. Its execution layer selects bounded, prewritten motion candidates rather than independent XYZ channels. Singleton menus skip inference and are marked as such. Local MiniCPM probabilities come from candidate-token logits; they are not TypeSafe Jev measurements or calibrated physical success probabilities.

## Jev and GPT-6 in the Public Comparison

The [reviewed controller source](https://github.com/openroboto-ai/jev-robot-control/blob/7a4ed8b72c3c17d7aa790678ed9660df67c10dd3/incremental_policy.py) uses `OPENROUTER_API_KEY` for both branches:

- Jev: `https://openrouter.ai/api/alpha/decisions`, model `typesafe/jev-1.13`, body `{model, state, questions}`.
- GPT-6: `https://openrouter.ai/api/v1/chat/completions`, model `openai/gpt-6-astra`, `messages` and a strict JSON schema, `reasoning_effort=low`, `max_completion_tokens=4096`, no temperature parameter.

The same environment asks the two controllers for intent and then motor decisions. GPT-6 is a separate comparison controller, not an internal component of Jev. The public adapter labels its GPT probabilities as self-reported, while Jev returns native distributions. EmbodiedJev's chat adapter requests a choice only and does not preserve such self-reported probabilities. Model names in source do not establish access for a particular API account or compatibility of every gateway.
