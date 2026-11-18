# QA toolkit: Smart Coding Assistant

Owner: Muhd Nurasyraf (QA & Test Engineer)

## First time

```
venv\bin\python -m pip install flask pytest bandit pip-audit matplotlib
```

## Run everything (recommended)

Window 1, the server:
```
$env:GEMINI_API_KEY="your_key"
venv\bin\python app.py
```

Window 2, the QA suite (about 10–12 minutes):
```
venv\bin\python tests\run_all_qa.py
```

Reports and charts go to `qa_reports\<date_time>\`. Open `QA_REPORT.txt` first.

| Option | Meaning |
| --- | --- |
| `--skip-live` | No server needed: tests, Bandit, pip-audit and charts only (~10 s) |
| `--runs 1` | Benchmark each sample once (52 requests, ~5 min) |
| `--delay 3` | Wait longer between Gemini requests if many reviews fail with 502 (quota) |

## The files

| File | What it does | Needs server? |
| --- | --- | --- |
| `test_app.py` | Team's core feature tests | No |
| `test_qa_edge_cases.py` | Bad input, error handling, fallback, all 10 languages; `test_DEFECT_` = known bugs | No |
| `test_security.py` | SQLi, XSS, secrets, prompt-injection output; `test_VULN_` = security gaps | No |
| `conftest.py` | Saves every pytest run to `test_results.csv` + `test_summary.txt` | — |
| `qa_benchmark.py` | 52 planted-bug samples (≈5 per language) sent to the real AI | Yes |
| `qa_dos_poc.py` | Live size-limit and rate-limit attack (uses no Gemini quota) | Yes |
| `make_qa_charts.py` | Builds 6 slide charts from the latest reports | No |
| `run_all_qa.py` | Runs all of the above in order | For steps 4–5 |

## Reading the results

- `test_DEFECT_` and `test_VULN_` tests **fail on purpose**: each proves a known issue.
  In `test_summary.txt` they show as "Confirmed (known issue)". Once fixed, they show as **FIXED**.
- **UNEXPECTED FAILURE** in the summary means something new broke. Investigate it.
- In the benchmark, "detected" is a keyword check. Before calling a red cell a miss, read
  what the AI actually said in `benchmark_results.csv` or in the app.
