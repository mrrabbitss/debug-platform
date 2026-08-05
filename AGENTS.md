# Repository Agent Guide

This file applies to the whole repository. Keep it short: it is a map and a set
of guardrails, not a duplicate architecture document.

## Source of truth

- `CAPABILITIES.md`: implemented, planned and explicitly unavailable features.
- `HARNESS_ENGINEERING.md`: engineering-harness status and ordered backlog.
- `docs/README.md`: architecture and operating-document index.
- `VALIDATION.md`: most recent verified regression record.
- `workflow/skill.yaml`: allowlisted agent-facing API operations and safety rules.

When behavior changes, update the relevant source-of-truth file in the same
commit. Do not describe an unverified feature as available.

## Repository map

- `backend/app/api`: FastAPI HTTP boundaries.
- `backend/app/services`: domain logic, parsers, retrieval, graphs and jobs.
- `backend/tests`: backend, migration, security and repository-contract tests.
- `frontend/src`: Vue 3 administrator and diagnosis workbench.
- `vscode-extension`: internal VS Code client.
- `scripts`: Win11 bootstrap, diagnostics, model installation and validation.
- `workflow`: deliberately allowlisted API contract for agent integrations.
- `sample_data`: synthetic regression fixtures only; never add company data.

## First actions

1. Read `CAPABILITIES.md`, `HARNESS_ENGINEERING.md` and the relevant document
   from `docs/README.md`.
2. Run `git status --short --branch` and preserve unrelated user changes.
3. On Win11, run `scripts\doctor_local.bat` when environment health is unclear.
4. Use the smallest validation mode that provides useful feedback, then run the
   full mode before publishing a material change.

## Canonical commands

```bat
scripts\start_local.bat
scripts\validate_all.bat Fast
scripts\validate_all.bat Full
scripts\refresh_python_lock.bat -Check
```

`External` validation additionally requires Docker:

```bat
scripts\validate_all.bat External
```

GitHub Actions remains authoritative for Ubuntu, PostgreSQL, Qdrant and Docker
when those services are unavailable on the development computer.

## Dependency rules

- Declare compatible Python ranges in `backend/pyproject.toml`.
- Regenerate `backend/uv.lock` and `backend/constraints.lock` with
  `scripts\refresh_python_lock.bat` after changing Python dependencies.
- Install Python dependencies through `backend/constraints.lock`.
- Use `npm ci`; never hand-edit npm lockfiles.
- Do not commit `.venv`, `node_modules`, `models`, runtime databases or reports.

## Architecture boundaries

- API routes validate transport concerns and delegate domain work to services.
- Services do not import frontend code or depend on request-global state.
- Long imports, parsing and index builds use the persistent job layer.
- Published graph/vector generations remain atomic; a failed rebuild must leave
  the previous active generation readable.
- Model output is untrusted data. Validate schemas, evidence IDs and state
  transitions before persistence.

## Security and data handling

- Never commit real company logs, credentials, internal endpoints or model keys.
- Treat uploaded logs, repositories and documents as untrusted input.
- Do not send content to a model endpoint without explicit consent and an
  approved endpoint policy.
- Keep model-egress audit content-free and redact secrets from validation logs.
- Knowledge produced by a model remains `DRAFT` until human review and publish.
- Mutating or destructive tools require the narrowest target and explicit scope.

## Definition of done

- Tests cover the changed behavior and a relevant regression path.
- `scripts\validate_all.bat Full` passes, or an unavailable external dependency
  is stated and then verified by GitHub Actions.
- `scripts\check_repo_harness.py` passes.
- Documentation, capability inventory and workflow contract are synchronized.
- The diff contains no generated runtime data, credentials or unrelated edits.
- GitHub CI is green before the change is considered ready to merge.
