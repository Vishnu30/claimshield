# Muse studies

Extra evidence beyond the main evaluation. Every number comes from real, uncached Muse calls (`python scripts/muse_studies.py <study>`).

## 1. Stability — full pipeline, repeated fresh runs
3 independent runs × 36 claims · Muse API $6.02

| Metric | Mean ± spread |
|---|---|
| Precision | 0.84 ± 0.01 |
| Recall | 1.00 ± 0.00 |
| Status accuracy | 0.86 ± 0.02 |
| ₹ error (total) | 0.00 ± 0.00 |
| Muse calls per claim | 12.80 ± 0.32 |
| Seconds per claim | 62.46 ± 7.81 |

Before vs after self-improvement (run 1 → learned overlays → runs 2+):

| | Precision | Recall | Status acc. |
|---|---|---|---|
| Run 1 (before) | 0.84 | 1.00 | 0.88 |
| Runs 2–3 (after) | 0.84 ± 0.01 | 1.00 ± 0.00 | 0.86 ± 0.02 |

## 2. Muse API vs open-weight Muse on partner hosts
Every reasoning role on one host · 36 claims

| Host | Precision | Recall | Status acc. | ₹ error | $ / claim | s / claim |
|---|---|---|---|---|---|---|
| Muse API (muse-spark-1.3) | 0.91 | 1.00 | 0.88 | ₹0 | $0.074 | 52 |
| Baseten (muse-spark-open) | 0.89 | 1.00 | 0.79 | ₹0 | $0.035 | 48 |

## 3. Multilingual round trip (Muse API)
Findings, fixes and summaries of 12 claims → language → back to English; ₹ amounts and ids checked; meaning scored 1–5 by Muse

| Language | Translated | ₹ / ids kept | Meaning (1–5) |
|---|---|---|---|
| hi | 100% | 100% | 5.00 |
| hinglish | 100% | 100% | 4.98 |
| gu | 100% | 100% | 5.00 |
| mr | 97% | 100% | 5.00 |
| kn | 100% | 100% | 5.00 |
| ta | 72% | 100% | 5.00 |
| te | 90% | 100% | 5.00 |
| bn | 83% | 100% | 5.00 |
| ml | 81% | 100% | 4.98 |
| pa | 79% | 100% | 4.96 |
| or | 67% | 100% | 4.91 |

## 4. Red team — prompt injection inside claim documents
12 claims × 3 attacks, full pipeline

| Attack | Payout unchanged | Statuses correct | Injected text never echoed |
|---|---|---|---|
| bill line | 100% | 67% | 100% |
| narrative | 100% | 50% | 100% |
| diagnosis | 100% | 58% | 100% |

Why money never moves: the payout engine is deterministic Python; Muse can only argue statuses, and the verifier drops claims without a policy or regulator citation.

## 5. Muse vision — reading real bill files
Every sample bill read from its file by the Muse API, compared with the ground-truth line items · 36 claims

| Input type | Claims | Lines read correctly | Extra lines | Total matches | Payout matches |
|---|---|---|---|---|---|
| pdf | 32 | 100% | 0 | 100% | 100% |
| multi-file | 2 | 100% | 0 | 100% | 100% |
| photo | 1 | 100% | 0 | 100% | 100% |
| scan | 1 | 100% | 0 | 100% | 100% |

## 6. Letters drafted by Muse
Every claim with something to appeal or fix · scored 1–5 by a Muse reviewer acting as a claims lawyer

| Letter | Count | Grounds cited | Placeholders used | No personal data | Quality (1–5) |
|---|---|---|---|---|---|
| Appeal to insurer | 7 | 100% | 100% | 100% | 0.00 |
| Bill correction to hospital | 11 | 100% | 18% | 100% | 0.00 |

## 7. Ask your policy — quiz
62 questions across 12 policies (real insurer wordings + demo), answers checked against the parsed policy

| Correct answer | Clause cited | Correct in Hindi |
|---|---|---|
| 11% | 100% | 0% |

## 8. Edge cases with live Muse
27 runs · 27/27 behaved as designed · 0 crashes

| Input | Claim stages | Outcome | What the user sees |
|---|---|---|---|
| rotated_bill.pdf | 3 | ✅ result | ₹71,350 claimed · 7 findings |
| long_bill_60_lines.pdf | 3 | ✅ result | ₹116,000 claimed · 7 findings |
| password_protected_bill.pdf | 3 | ✅ result | ₹71,350 claimed · 9 findings |
| low_res_photo.jpg | 3 | ✅ message | No billed line items could be read from the bill. Is this the hospital's final itemized bill? If it is a photo, retake it flat, in good ligh |
| iphone_photo.heic | 3 | ✅ message | iphone_photo.heic: iPhone HEIC photos are not supported — share the photo as JPG (Settings → Camera → Most Compatible). |
| blank_page.pdf | 3 | ✅ message | No billed line items could be read from the bill. Is this the hospital's final itemized bill? If it is a photo, retake it flat, in good ligh |
| corrupt.pdf | 3 | ✅ message | corrupt.pdf: the file is damaged or not a real PDF. |
| not_a_bill.pdf | 3 | ✅ message | No billed line items could be read from the bill. Is this the hospital's final itemized bill? If it is a photo, retake it flat, in good ligh |
| multi-file + duplicate | 1 | ✅ result | duplicate caught across files |
| room-category policy upload | 1 | ✅ result | category entitlement handled without a ₹ cap |
| scanned policy upload | 1 | ✅ result | bill checks still run; policy read by Muse vision or flagged |

## Self-improvement log
Failures found by the studies were diagnosed by the Muse API; each proposed change was validated live (failing cases re-run + passing controls + offline tests) and kept only if it fixed something and broke nothing. **0 change(s) kept**, stored in `kb/learned/` (delete a file to undo).

| Study | Change | Fixed | Regressions | Decision |
|---|---|---|---|---|
| vision | no failures — nothing to learn | — | — | n/a |
| chat | chat prompt: “Always answer waiting-period, room-rent and coverage-limit questions from policy schedule with citations in user's language, never blank.” | 0/5 | 1/3 | reverted |
| letters | no safe change proposed | — | — | no |
| redteam | no safe change proposed | — | — | no |
| languages | no failures — nothing to learn | — | — | n/a |
| robust | no safe change proposed | — | — | no |

## Coverage — every angle exercised

| Dimension | Covered |
|---|---|
| Claim stages | pre_submission (26), appeal (8), pre_discharge (2) |
| Issue types | missing_doc (13), non_payable (9), room_rent (8), proportionate (7), mismatch (7), waiting_period (6), copay (5), ped (3), short_settlement (3), intimation (3), sublimit (3), icu_rent (2), exclusion (2), wrongful_rejection (2) |
| Verdicts | fixable (30), valid (24), contestable (13), needs_review (6) |
| Output languages | 13 across samples · 12 in the app |
| Input types | pdf (32), multi-file (2), scan (1), photo (1) |
| Policies | 12 in the library (real insurer wordings + 3 demo) |
| Regulator knowledge | 13 rules · 141 non-payable items |
| Jurisdictions | GENERIC, IN, US |
| Sample tiers | 24 verified · 12 challenge |
| Automated tests | 157 (UI end-to-end, mock hosts, 12-language translation, edge-case grid, KB) |
