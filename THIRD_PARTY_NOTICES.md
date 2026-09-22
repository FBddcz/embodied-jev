# Third-Party Notices

## Franka Emika Panda

- Source: https://github.com/google-deepmind/mujoco_menagerie/tree/822c2d8f877dd166c5b7d3c9f7e3c3b6589473b7/franka_emika_panda
- Commit: `822c2d8f877dd166c5b7d3c9f7e3c3b6589473b7`
- Included: `panda.xml`, `assets/`, and `LICENSE`, under `src/embodied_jev/assets/panda/`.
- License: Apache-2.0, original text preserved in that directory.
- Vendored files are unmodified. The runtime reads the XML and constructs a separate scene containing table, task objects and a TCP site; it changes the contact friction/timestep and omits the upstream keyframe in memory.

## Runtime and Frontend

MuJoCo is Apache-2.0. Three.js, Lucide, Vite, FastAPI, HTTPX and the other installed dependencies retain their respective licenses. npm and Python install their package notices; no dependency license is superseded by this project's MIT license.

MiniCPM5-2B weights are downloaded only on explicit provider enablement and use. They are not included in the repository. See https://huggingface.co/openbmb/MiniCPM5-2B for the model card and applicable license.

The repositories in `docs/REFERENCES.md` informed the architecture. Their source code, model weights and recorded performance results have not been copied into this implementation.

## LIBERO experiment and gallery

The optional simulator uses the external official LIBERO source/assets at commit `8f1084e3132a39270c3a13ebe37270a43ece2a01`, robosuite and MuJoCo. The downloader preserves LIBERO's LICENSE; simulator assets and Python environments are not vendored into this repository.

[Dimweaker/jev-libero](https://github.com/Dimweaker/jev-libero/tree/3bdad985b225aeccc39fbe5863c6eea2e81c515a) informed the task selection, layered-control presentation and synchronized replay design. Its recordings, results and implementation are not redistributed here. This project's gallery uses its own recorded experiments.
