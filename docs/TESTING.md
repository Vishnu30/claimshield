# Testing ClaimShield

## Automated (offline, $0)
| Command | Covers |
|---|---|
| `run test` | 153 tests: payout math, every scenario through the pipeline, UI flows for all 24 samples, edge-case uploads, parsers, JSON repair, mock-provider agents/jury/vision |
| `run doctor` | `.env`, providers, routes, data, git hygiene |
| `run check` | Live probe of every provider (~$0.003) |

## Sample tiers
| Tier | Where | How to test |
|---|---|---|
| Verified (S01–S24) | Home → **Verified samples** | First open runs live and is recorded; every later open is instant (or record all with `run snapshot`); "Re-run live" re-runs with your keys |
| Challenge (C01–C12) | Home → **Challenge samples** | Runs live with your keys (rules engine if none); compare with `data/scenarios/Cxx/expected.json` |

## Files to use
| Purpose | Path |
|---|---|
| Sample claims (one click on the home screen) | `data/scenarios/<Sxx>/` — `bill*.pdf` / `bill.jpg`, `discharge.pdf`, `rejection.pdf` |
| Ground truth (don't upload) | `truth.json`, `expected.json` |
| Demo policies | `data/policies/demo_shield_policy.pdf`, `demo_classic_policy.pdf` |
| Edge cases | `data/samples/edge_cases/` |
| Real policies (after `run build-kb`) | `data/raw/policies/*.pdf` → appear in the policy library |

## Edge-case uploads (upload as the hospital bill unless noted)
| File | Expected |
|---|---|
| `password_protected_bill.pdf` | Asks for password; works with **1234**; a wrong password gives a clear message |
| `corrupt.pdf` | "The file is damaged…" |
| `blank_page.pdf` | "Scanned image…" message offline; read by Muse vision when online |
| `not_a_bill.pdf` | "No billed line items…" with photo tips |
| `rotated_bill.pdf` | Reads all 15 lines |
| `long_bill_60_lines.pdf` | Reads all 60 lines across pages |
| `low_res_photo.jpg` | Online: Muse vision attempts it; offline: clear message |
| `iphone_photo.heic` | Asks for JPG |
| `scanned_policy.pdf` (upload as **policy**) | "Policy wording could not be read"; bill checks still run |
| `policy_room_category.pdf` (upload as **policy**) | Category-based room limit → "Room category above entitlement"; zone co-pay not auto-applied |

## Knowledge-base inventory
| Check | Expect |
|---|---|
| Put a policy PDF with a random name (e.g. `download (3).pdf`) in any `data/raw/` folder, then `run build-kb` | The INVENTORY lists it as `policies` with insurer, product and UIN, plus `↔ <source id>` if it matches the registry |
| Same run | `↪ renamed … → policies/<source-id>.pdf`; unknown products get `+ registry: pol_…` |
| Run `run build-kb` again | `already present` for that source; `✓ … (already built)` for the policy; nothing re-extracted |
| `run build-kb --fast` | Every digital policy built in seconds, no model calls |
| A scanned policy without a vision model | `✗ nothing readable (scanned …)`: not saved, retried next run |

## Language
| Check | Expect |
|---|---|
| Home → language chip | Sidebar language, interface and results all switch |
| Results → language chip | Findings, fixes, summary and action plan translate in place (no re-run) |
| Download report in a non-English language | Every script renders (no black boxes) |

## Live checklist (keys in `.env`)
| # | Do | Expect |
|---|---|---|
| 1 | Home → sample **Cataract, still admitted** (Advanced → Agent depth Full, language हिन्दी) | Live trace with 🔧 tool calls and jury; ₹44,370 → ₹59,130; Hindi explanations |
| 2 | Open a finding → Show on document | Highlighted bill row / clause |
| 3 | What-if tab | Payout changes instantly |
| 4 | Letters → Draft | PDF with your details filled locally |
| 5 | Sample **Scanned bill** with "reuse ground-truth extraction" off | Lines read from the scan; highlight box on the image |
| 6 | Sample **Short-paid** / **Rejected** | Recoverable ₹26,769 / ₹2,56,000; escalation .ics |
| 7 | Ask your policy: "room rent limit?" | Cites `[3.1]` |
| 8 | Upload your own policy + bill | Policy saved to library; second run of the same policy costs 0 |
| 9 | Download report (PDF) | Patient-friendly summary + action plan |

## Demo recording
`PROFILE=prod` in `.env` → `run warm` (one live pass) → `run demo` (replay mode: the same results at $0 per take).
