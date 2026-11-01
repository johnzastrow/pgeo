# Handoff

Where the work stands, for the next session. Updated 2026-11-01. Real hostnames, addresses and
credentials are deliberately not here (this repository is public); the deployment record is kept
privately with the owner.

## State

| Item | State |
|---|---|
| Latest release | **v0.23.1** (pgeo 0.20.1, pelias-prep 0.2.1), tagged on `12a6794` |
| `main` | `5fcff41` plus this file. Unreleased: the bundle edge logs path only (CHANGELOG `[Unreleased]`) |
| Images | `ghcr.io/johnzastrow/pgeo-build`, `pgeo-api`, `pgeo-db` at `0.20.1` and `latest`, public, linux/amd64, with SBOMs and provenance attestations |
| CI | `.github/workflows/ci.yml`: ruff, unit tests, pip-audit, npm audit, gitleaks. Green on `main` |
| Release automation | `.github/workflows/release.yml` on any `vX.Y.Z` tag: images, bundle asset, GitHub release |
| Security alerts | Dependabot: 0 open (2 image-size alerts dismissed as tolerable). CodeQL: 0 open |
| Branch protection | `main`: no force-push or deletion; the 4 CI jobs are required for pull requests; admins bypass |
| Deployment | One public deployment of the serving bundle (Maine), running bundle 0.23.1 with the path-only log fix applied by hand |

## Done this session (2026-11-01)

- `docs/STACK_COMPARISON.md`: pgeo, Pelias, Photon and Nominatim compared on requirements and stack;
  section 9 covers how small the stack can be (four shapes, Caddy instead of nginx). The README has
  a requirements section.
- Security review, three batches: input validation, error sanitising, rate limiting behind a
  proxy, container hardening, digest pins, dump checksums, SSH drop-in, CI, Dependabot, CodeQL.
- Configurable limits, statement timeout and rate limits (`docs/CONFIGURATION.md`).
- Releases 0.23.0 and 0.23.1. 0.23.1 exists because the first real build found expired upstream
  pins (Overture, Protomaps) and an Overture schema change.
- First end-to-end test of the published artefacts, then the public deployment.

## Open, in priority order

1. **Accuracy ground truth is stale.** On current data Maine scores 92.9% against 95.7% for the
   2026-09-25 run (`data/accuracy/pgeo-bundle-0.20.0-rainbozen.json`). Most of the 48 regressions
   are addresses whose points moved in current OpenAddresses data, and the Biddeford OpenAddresses
   source has no current run. Re-derive `tests/accuracy/cases.json` from current data
   (`tests/accuracy/build_cases.py`) before comparing again.
2. **Release the path-only log fix** (`5fcff41`) as 0.23.2.
3. **Dependabot pull requests still open, deliberately:** #1 nginx 1.31, #5 Python 3.15, #7/#8
   uv 0.13, #10/#11 DuckDB 2.0 (major; DuckDB does all the data loading). Each needs a real build
   and test run, which needs Docker (not on the Windows workstation; the Linux build box has it).
4. **Untested by any automated test:** the SQL changes and the Ansible changes from the security
   review run only in deployment. The bundle, swap, role script, nginx template and container
   hardening were exercised on the 2026-11-01 deployment; the Ansible roles were not.
5. **The public deployment has no API keys** (the bundle has none). Per-client rate limits only.
6. `report/tests`: 12 line-count tests fail, before and after this session's changes (stated
   counts in the report no longer match the files).

## How to pick up

- Build and test: Docker on the Linux build box. `docker run --rm -v ./data:/data -e PGEO_BUILD=me
  ghcr.io/johnzastrow/pgeo-build:0.20.1 build`, then `dump`. Serve with the release bundle and
  `pgeo_swap.sh`, then `tests/compat/compat_test.py --sql <base>` and
  `tests/accuracy/run_accuracy.py --engine pgeo --base <base> --rps 8` (stay under the edge's
  10 r/s limit).
- Copying the repo to Linux from a Windows checkout: `git -c core.autocrlf=false archive HEAD`.
  A plain copy carries CRLF and the scripts fail with `bash\r`.
- A release is a version bump, a CHANGELOG section, and a pushed `vX.Y.Z` tag (plus component
  tags `pgeo-vX.Y.Z`, `prep-vX.Y.Z` when those change). The workflow does the rest.
