# 15th Korean Bar Examination — LLM Solving Records

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

**한국어: [readme.md](readme.md)**

<div align="center">

### [**Interactive Dashboard**](https://nicetry3675.github.io/korean-bar-exam-llm/)

[![Dashboard](https://img.shields.io/badge/Dashboard-Live-brightgreen?style=for-the-badge&logo=github)](https://nicetry3675.github.io/korean-bar-exam-llm/)

**Per-model scores, per-subject accuracy, and cost analysis**

</div>

---

## Overview

Results of various LLMs solving the **multiple-choice section of the 15th Korean Bar
Examination** (Public Law 40 questions, Civil Law 70, Criminal Law 40 — 150 questions,
375 points total, 2.5 points per question).

Seven model families were run at **different reasoning efforts, for 26 combinations
in total**. The goal is to show how much the same model's score moves with its
reasoning budget, and what that costs.

> This benchmark covers **only the multiple-choice section**, so it cannot be used to
> determine whether a candidate would pass the examination.

---

## Overall Results

| High reasoning (max·high) | Low reasoning (none·low) |
|---|---|
| ![Overall score comparison - high reasoning](https://nicetry3675.github.io/korean-bar-exam-llm/images/전체_고추론.png) | ![Overall score comparison - low reasoning](https://nicetry3675.github.io/korean-bar-exam-llm/images/전체_저추론.png) |

| Model | Public Law | Civil Law | Criminal Law | Total | Answered | API-equivalent cost | Output tokens |
|---|---|---|---|---|---|---|---|
| Claude Fable 5 (max) | 97.5 | 167.5 | 97.5 | **362.5** | 150/150 | $71.13 | 1.39M |
| Claude Opus 5 (max) | 97.5 | 167.5 | 92.5 | **357.5** | 150/150 | $24.23 | 941K |
| Claude Fable 5 (high) | 95 | 167.5 | 92.5 | **355** | 150/150 | $15.31 | 278K |
| Claude Opus 5 (high) | 95 | 167.5 | 87.5 | **350** | 149/150 | $7.99 | 291K |
| Gemini 3.1 Pro Preview (high) | 87.5 | 155 | 95 | **337.5** | 150/150 | $7.91 | 646K |
| Gemini 3.1 Pro Preview (low) | 87.5 | 152.5 | 90 | **330** | 150/150 | $2.30 | 179K |
| Gemini 3.6 Flash (high) | 92.5 | 140 | 85 | **317.5** | 150/150 | $5.88 | 768K |
| Gemini 3.6 Flash (low) | 87.5 | 147.5 | 77.5 | **312.5** | 150/150 | $1.03 | 121K |
| Claude Opus 5 (none) | 82.5 | 152.5 | 72.5 | **307.5** | 137/150 | $3.64 | 118K |
| GPT-5.6 Sol (high) | 90 | 137.5 | 80 | **307.5** | 150/150 | $17.19 | 559K |
| GPT-5.6 Sol (max) | 85 | 145 | 77.5 | **307.5** | 150/150 | $45.69 | 1.51M |
| Claude Opus 4.8 (max) | 85 | 142.5 | 72.5 | **300** | 143/150 | $5.55 | 194K |
| Claude Opus 4.8 (none) | 80 | 147.5 | 70 | **297.5** | 149/150 | $3.16 | 98K |
| Claude Opus 4.8 (high) | 82.5 | 132.5 | 77.5 | **292.5** | 147/150 | $3.17 | 99K |
| Claude Sonnet 5 (max) | 80 | 120 | 67.5 | **267.5** | 132/150 | $123.91 | 8.23M |
| GPT-5.6 Sol (none) | 67.5 | 115 | 80 | **262.5** | 150/150 | $1.61 | 40K |
| GPT-5.6 Luna (high) | 77.5 | 100 | 65 | **242.5** | 150/150 | $12.37 | 2.05M |
| Claude Sonnet 5 (none) | 60 | 117.5 | 60 | **237.5** | 146/150 | $2.39 | 131K |
| GPT-5.6 Terra (max) | 75 | 97.5 | 65 | **237.5** | 147/150 | $41.85 | 2.78M |
| Claude Sonnet 5 (high) | 70 | 105 | 57.5 | **232.5** | 119/150 | $10.99 | 705K |
| GPT-5.6 Terra (high) | 70 | 92.5 | 67.5 | **230** | 150/150 | $8.50 | 553K |
| GPT-5.6 Terra (none) | 67.5 | 90 | 72.5 | **230** | 150/150 | $0.76 | 37K |
| GPT-5.6 Luna (max) | 75 | 90 | 57.5 | **222.5** | 125/150 | $18.91 | 3.14M |
| Gemini 3.5 Flash-Lite (low) | 55 | 95 | 60 | **210** | 150/150 | $0.38 | 142K |
| Gemini 3.5 Flash-Lite (high) | 60 | 85 | 55 | **200** | 150/150 | $2.18 | 864K |
| GPT-5.6 Luna (none) | 47.5 | 82.5 | 42.5 | **172.5** | 145/150 | $0.31 | 37K |

> **Answered** counts questions where an answer could be extracted. The rest score zero;
> causes are broken down in [When no answer was obtained](#when-no-answer-was-obtained).
>
> **API-equivalent cost** is not a subscription bill. It applies public API list prices
> to the measured token usage, so it differs from what was actually charged.

### By subject

| | Public Law | Civil Law | Criminal Law |
|---|---|---|---|
| High reasoning | ![Public Law, high reasoning](https://nicetry3675.github.io/korean-bar-exam-llm/images/공법_고추론.png) | ![Civil Law, high reasoning](https://nicetry3675.github.io/korean-bar-exam-llm/images/민사법_고추론.png) | ![Criminal Law, high reasoning](https://nicetry3675.github.io/korean-bar-exam-llm/images/형사법_고추론.png) |
| Low reasoning | ![Public Law, low reasoning](https://nicetry3675.github.io/korean-bar-exam-llm/images/공법_저추론.png) | ![Civil Law, low reasoning](https://nicetry3675.github.io/korean-bar-exam-llm/images/민사법_저추론.png) | ![Criminal Law, low reasoning](https://nicetry3675.github.io/korean-bar-exam-llm/images/형사법_저추론.png) |

---

## Key Findings

1. **Fable 5 wins under every condition.** 362.5 at max (96.7%), and still 355 at high
   for one-fifth the cost ($15.31).
2. **Opus 5 is a generational jump.** At the same max effort it beats Opus 4.8 (300) by
   **+57.5 points**, at one-third the cost of Fable max.
3. **Opus 4.8 is nearly effort-insensitive.** none 297.5 / high 292.5 / max 300 — a
   spread of just 7.5 points, all for $3–6. **Non-thinking beats high.**
4. **Sonnet 5 at max is pathological overthinking.** 8.23M output tokens (55K per
   question average) and $123.91 for 267.5 points. 18 questions burned the full 128K
   output limit without producing an answer. It is the only one of the three Anthropic
   models where more thinking backfired.
5. **Sol is effort-insensitive.** max and high tie at 307.5 while costing 2.7× more
   ($45.69 vs $17.19).
6. **Luna depends on reasoning the most:** none 172.5 → high 242.5, a +70 swing.
7. **Flash-Lite scores higher at low than at high** (210 vs 200) — a miniature of the
   Sonnet max pattern, where more thinking hurts a smaller model.
8. **Best value: Gemini 3.6 Flash (low)** — 312.5 points for $1.03, or $0.0033 per point.
   It outscores Sol max (307.5, $45.69) at 1/44 the cost.

---

## Scoring

### Official score (v1) and parallel notation (v2)

The prompt instructs each model to end its response with `정답: N`.
**Official scores come from a strict parser (v1) that accepts only that format**,
because following the required format is treated as part of the skill being measured.

Some responses reached the right answer but broke the format, so results from a lenient
parser (v2) that also recognizes prose phrasing are recorded **alongside** in the
workbook. v2 is reference-only: it does not feed the official scores, the dashboard, or
the table above.

| Model | v1 (official) | v2 (reference) | Delta |
|---|---|---|---|
| Claude Sonnet 5 (high) | 232.5 | 287.5 | +55.0 |
| Claude Opus 5 (none) | 307.5 | 340.0 | +32.5 |
| Claude Opus 4.8 (max) | 300.0 | 315.0 | +15.0 |
| Claude Sonnet 5 (none) | 237.5 | 245.0 | +7.5 |
| Claude Opus 4.8 (high) | 292.5 | 297.5 | +5.0 |
| Claude Opus 5 (high) | 350.0 | 352.5 | +2.5 |

The other 20 combinations score identically under v1 and v2. Format violations occurred
**only in the Anthropic models**; the OpenAI and Google models had zero.

### When no answer was obtained

| Type | Affected | Cause |
|---|---|---|
| `parse_failed` | Sonnet 5 (high) 31, Opus 5 (none) 13, Opus 4.8 (max) 7, Luna (none) 5, Sonnet 5 (none) 4, Opus 4.8 (high) 3, Opus 4.8 (none) 1, Opus 5 (high) 1 | Answered, but not in the required format (partly recovered under v2) |
| `no_answer` | Sonnet 5 (max) 18 | Exhausted the 128K output limit without producing an answer |
| `no_answer` | Luna (max) 25, Terra (max) 3 | No response received — ChatGPT Codex backend stream lifetime limit |

All are scored as zero. The Luna/Terra stream truncations did not recover on retry;
neither `service_tier: "priority"` (accepted but ineffective) nor background mode
(rejected with 400 `Store must be set to false`) worked around them.

---

## Test Environment and Caveats

**Important: this experiment mostly ran over subscription OAuth, not public API keys.**

- **Anthropic / OpenAI models**: OAuth credentials from Claude and ChatGPT subscriptions
- **Gemini models**: Google API key
- **Reasoning setting**: explicitly set per run to none / high / max (low / high for Gemini)
- **System prompt**: none beyond the benchmark instruction
- **External tools**: **not provided** (no search, no calculator)
- **Question delivery**: text extracted from the Ministry of Justice's public HWP files,
  sent as a single user message per question
- **Retries**: only when a request errored without a response. Malformed responses were
  scored as incorrect rather than retried.

Subscription OAuth paths use consumer accounts and private transports. Automated
benchmarking over them may violate terms of service and lead to rate limiting or account
suspension. **These results may therefore differ from calling the same models over the
public API.**

Generation parameters such as temperature were left at their defaults, so repeated runs
will vary somewhat.

---

## Running It

Question text and the source HWP files are not included in this repository for copyright
reasons. For the answer key, scoring, provenance, local conversion, and the dry-run
procedure, see the [benchmark guide](benchmarks/bar-exam-15/README.md).

```bash
# 1. Convert the public HWP files into the local, git-ignored problems/ tree
python3 scripts/prepare_bar_exam.py

# 2. Prepare model config (records env-var names or oauth_profile, never secret values)
cp benchmark_models.example.json benchmark_models.json

# 3. Preview — the default is a dry run with no network access
python3 benchmark_runner.py --benchmark bar-exam-15 \
  --config benchmark_models.json --run-mode question

# 4. Real execution requires an explicit flag and a cap
python3 benchmark_runner.py --benchmark bar-exam-15 \
  --config benchmark_models.json --run-mode question \
  --execute --max-requests 150

# 5. After review, sync the workbook and dashboard data
python3 sync_data.py --benchmark bar-exam-15 import --all
python3 sync_data.py --benchmark bar-exam-15 export --all-sheets --all-models
```

---

## License and Copyright

### Project license

Distributed under the MIT license — see [LICENSE.md](LICENSE.md). This repository builds
on the benchmark dashboard and synchronization code from
[hehee9/2026-CSAT](https://github.com/hehee9/2026-CSAT), and retains its copyright notice.

### Exam content

- The 15th Korean Bar Examination questions and answer key are published by the
  **Ministry of Justice** of the Republic of Korea.
- That content is distributed under the
  [Korea Open Government License Type 1](https://www.kogl.or.kr/info/userGuide.do), which
  requires attribution. This content license is separate from the MIT license covering
  this repository's source code.
- This repository contains no original question text — only the answer key, scoring,
  provenance, and model results.
- Sources: [questions](https://www.moj.go.kr/bbs/moj/150/602397/artclView.do) ·
  [final answer key](https://www.moj.go.kr/bbs/moj/151/603464/artclView.do)
