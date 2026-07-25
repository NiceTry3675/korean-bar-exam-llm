# Benchmark registry

`registry.json` is the tracked source of truth for benchmark discovery. It lets
benchmarks be added without introducing hard-coded subject lists in Python or in
the web application.

Each entry defines:

- localized title and navigation visibility;
- scoring scale and evaluated question count;
- `default` and `hard` result, token-usage, and workbook assets;
- stable section IDs, sheet names, local problem directories, and limits.

`metadataPath` may point to a tracked answer-only metadata file. This permits
score synchronization and empty-state builds when copyright-sensitive local
question text is absent. A real model run must still use the ignored local
`problemDir/questions.json` and text files prepared from the official source.
