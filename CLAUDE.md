# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Streamlit dashboard that uses mathematical optimization (Pyomo + Ipopt NLP
solver) to simulate optimal credit-limit allocation across a credit card
portfolio, maximizing expected net profit (or minimizing expected loss)
subject to a risk/profit constraint. It's a personal proof-of-concept
project (not production-bound) — see `docs/ROADMAP.md` for the phased plan
(currently Phase 1 complete, Phase 2 in progress) and `docs/devlog/` for
dated working notes.

## Commands

```bash
# Setup (requires Conda for the ipopt system solver)
conda env create -f environment.yml
conda activate credit_card_demo

# Run the app
streamlit run app.py    # http://localhost:8501

# Run tests
pytest tests/ -v
pytest tests/test_rules.py -v          # single file
pytest tests/test_rules.py::TestApplyBusinessRules -v   # single class
```

There is no separate lint/typecheck command configured. The test suite
(`tests/test_rules.py`) intentionally targets only `domain/rules.py` and
depends on pandas/numpy/pytest only — it does not require Ipopt/Pyomo, so it
runs fast in CI (`.github/workflows/tests.yml` installs a minimal dependency
set, not the full `requirements.txt`, for this reason).

## Architecture

Three-layer separation, driven by the goal of eventually exposing the
optimization engine behind an API (Phase 2) without rewriting it:

- **`app.py`** — Streamlit UI only: page layout, widgets, charts,
  bilingual (EN/JP) label translation via a local `t(en, jp)` helper keyed
  off a sidebar language selector. Calls into `domain/` for all computation
  and holds no business logic itself.
- **`domain/solver.py`** — Pyomo/Ipopt model construction and solving
  (`solve_optimization`, `get_efficient_frontiers`,
  `run_sensitivity_analysis`, `calculate_portfolio_profit`). Knows nothing
  about Streamlit or UI language state — functions that need
  presentation-layer input (e.g. translated chart labels in
  `run_sensitivity_analysis`) take it as a parameter (`parameter_labels`)
  rather than importing `app.py`. Also owns `get_ipopt_path()`, which probes
  several fallback locations to find the Ipopt binary because it differs
  per hosting environment (Streamlit Cloud's Conda path, Azure's
  amplpy-installed path, etc.) — this cross-environment drift is exactly
  what the Phase 2a-pre Dockerization work aims to eliminate.
- **`domain/rules.py`** — Pure post-processing functions
  (`round_to_menu`, `apply_business_rules`, `classify_action`) that convert
  the solver's continuous-value theoretical solution into a practically
  applicable discrete credit limit. Has no dependency on Ipopt, which is
  what makes it unit-testable in isolation (`tests/test_rules.py`). Rule
  application order in `apply_business_rules` is significant and documented
  in its docstring: menu rounding → Shopping segment no-decrease rule →
  housewife/student cap applied last so it always wins.
- **`domain/config.py`** — Loads `config/business_rules.yaml` once at
  import time into module-level constants (`LIMIT_OPTIONS`,
  `HOUSEWIFE_STUDENT_CAP`, `SCENARIO_PD_MULTIPLIERS`). Only thresholds whose
  *approval path* would actually change by moving them to config were
  externalized here (e.g. values a non-engineer might plausibly need to
  tweak); values that always require an engineer's PR regardless of storage
  format (e.g. the frontier sampling range) were deliberately left as
  in-code constants.

Data flow: `app.py` generates a deterministic synthetic customer dataset
(`generate_customer_data()`, seeded) and passes the DataFrame explicitly
into `domain/solver.py` functions — the domain layer never reaches back
into `app.py` to regenerate or refetch data itself, to keep it free of
circular UI dependencies.

## Deployment

Two live deployments built from the same `main` branch (no feature
branching — shared `domain/` logic would otherwise need duplicate fixes
across branches):

- **Streamlit Community Cloud** — entry point `app.py`, environment defined
  by `environment.yml` (auto-detected by Streamlit Cloud to provision the
  Ipopt system solver via Conda).
- **Azure App Service** (Linux) — deployed via GitHub Actions
  (`.github/workflows/main_credit-portfolio-optimizer.yml`, code-deploy/Oryx
  mode using OIDC federated login), installs Ipopt via the `amplpy` pip
  wheel instead of Conda. Phase 2 (FastAPI-fication under a new `api/`
  directory, containerization) targets Azure as primary; see the Phase 2
  "Deployment Strategy" section of `docs/ROADMAP.md` for the
  directory-separation plan.

`docs/azure_deployment_notes.md` and the README's "Key Engineering
Challenges Solved" section document environment-specific gotchas already
hit (Oryx's randomized `/tmp` build path, port mismatch defaults, a
previously swallowed exception that hid a missing-solver error).
