# Benchmark dashboard

React/Vite dashboard for the repository's benchmark registry. Data files are copied from the repository root before local development and production builds.

## Local commands

```bash
npm install
npm test
npm run lint
npm run build
npm run dev
```

The copy step reads `../benchmarks/registry.json`; result, token-usage, and optional question-metadata paths come from each benchmark mode in that registry.

## URLs

- Question-by-question (default): `/korean-bar-exam-llm/`
- Whole-subject mode: `/korean-bar-exam-llm/?mode=hard`
- All models in one chart (used by the README image export): `/korean-bar-exam-llm/?models=all`

A run mode whose result file is still an empty array renders a results-pending state instead of treating the empty array as a load failure. The whole-subject mode has not been run yet, so it currently shows that state.

The deployed base path is set once, in `vite.config.js`. `index.html`, `manifest.json`, and `sw.js` all derive their paths from it, so renaming the repository only requires changing that one line.
