# Publishing to GitHub

## One-time
| # | Command / action |
|---|---|
| 1 | Install Git for Windows (`git --version` to check) |
| 2 | `git config --global user.name "Your Name"` and `git config --global user.email "you@example.com"` |
| 3 | github.com → **New repository** → name `claimshield` → **Public** → do **not** add a README, .gitignore or license |

## Push
```
cd C:\path\to\claimshield
git init
git add .
git status
```
**Stop if `git status` lists `.env`** — it must never be committed. Then:
```
git commit -m "ClaimShield: patient-side health-claim auditor on Meta Muse Spark"
git branch -M main
git remote add origin https://github.com/<you>/claimshield.git
git push -u origin main
```
GitHub asks you to sign in in the browser the first time.

## What gets committed
| Committed | Not committed (`.gitignore`) |
|---|---|
| Code, tests, README, docs, screenshots | `.env` (your keys) |
| `kb/` including built `kb/policies/*.json` and `kb/manifest.json` | `data/raw/**/*.pdf` (re-fetchable; URLs and SHA-256 hashes are in `data/sources_lock.csv`) |
| `data/scenarios/`, `data/samples/`, demo policy PDFs | `logs/` cache and usage, `venv/` |
| `eval/results.json` (after your eval run) | `kb/policies/upl_*.json` (your own uploads) |

## Updating later
```
git add .
git commit -m "Add eval results and screenshots"
git push
```

## Final checks before submitting
| # | Check |
|---|---|
| 1 | Open the repo in an **incognito** window — it loads without signing in |
| 2 | The README renders: diagrams visible, screenshots load, video link works |
| 3 | The **tests** badge turns green (Actions tab) |
| 4 | `git log --all -p \| findstr /I "API_KEY="` shows only empty values |
| 5 | Every teammate is added on the hackathon portal |
