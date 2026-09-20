# Contributing

Install `.[test]` in a virtual environment, run `npm ci` and `npm run build`. Before a pull request run `pytest -q` and `npm run test:ui` (install Playwright Chromium first). Frontend formatting uses `npx prettier --write frontend/ vite.config.js`.

Keep model results separate from the deterministic baseline. A new result should include provider, resolved model/revision, task, seed, scene hash, decision settings and all failures. Do not overwrite reference evidence to hide a regression. Do not call candidate probabilities calibrated physical success probabilities.

Maintain real finger/object contact in the default tasks. New simulators should preserve explicit reset, observation, bounded action, feedback, stop and replay contracts. Add evidence before claiming simulator or model compatibility.

For GitHub publication, publish only this project directory. Exclude `.env`, model caches, virtual environments, `node_modules`, build output and private episode records. Include this license, third-party notices, robot asset licenses and the lockfile. Suggested repository name: `embodied-jev`.
