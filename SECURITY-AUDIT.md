# Security Audit — pdf-mcp-ocr-more-secure

> Fork of `jztan/pdf-mcp@41560349` · Audit commit: current `develop` HEAD
>
> Auditable record behind the security claims in `README.md`. Covers what was audited, how, what was found, what was pruned, and what residual risks remain.

---

## 1. Threat model

**In scope.** Whether this package — its own code, its Python dependency tree, or the installed plugin — can leak PDF content or other host data to external actors without the user's knowledge, on a single-user workstation on a secured internal network behind a trusted internal model proxy.

**Out of scope.** AI-agent misuse (prompt injection, agent goes rogue), multi-user/shared-server deployment, adversaries with physical or kernel access, side-channel attacks.

---

## 2. First-party code audit (this repo's `src/`)

Grep targets across `src/pdf_mcp/`:

```
fetch(  requests  urllib.request  urllib3  socket  dns  dgram
httpx  aiohttp  websockets  XMLHttpRequest
exec(  spawn  subprocess  child_process  eval(  compile(
os.system  os.exec  __import__
```

| Target | Result | Notes |
|---|---|---|
| Outbound network primitives | Only `httpx` in `url_fetcher.py` (for user-requested URL sources) and `socket.getaddrinfo` in `url_fetcher.py` + `config.py` (for SSRF floor DNS resolution). `dns` imported only as a namespace via `ipaddress` in `url_fetcher.py`. | The DNS lookup is part of validating a user-supplied URL; not an unrelated egress path. |
| Dynamic code execution | None (no `eval`, `exec`, `compile`, `__import__`, `subprocess`, `os.system`). | |
| Telemetry / analytics | None. | |
| Env-var-gated features | None in `src/pdf_mcp/*.py`. `os.getenv` / `os.environ[` returns zero hits. | |
| pdfjs-style script execution in parsed PDFs | N/A — PyMuPDF is the PDF engine; PyMuPDF does not execute PDF JavaScript. | |
| Tesseract invocation | Via `pytesseract.image_to_string()`, lazy-imported so the `[ocr]` extra is truly optional. Tesseract spawns its own binary; that's `pytesseract`'s job, not ours. | |

---

## 3. Runtime dependency audit (pinned set)

Direct runtime deps (from `pyproject.toml`'s `[project].dependencies`):

| Package | Version | Publisher | Role |
|---|---|---|---|
| `fastmcp` | `3.2.0` | jlowin | MCP server framework |
| `pymupdf` | `1.27.2.2` | Artifex Software | PDF parsing |
| `pydantic` | `2.12.5` | Pydantic / Samuel Colvin | Validation (FastMCP dep, exposed) |
| `httpx` | `0.28.1` | Encode | URL fetching |

Direct optional extras:

| Package | Version | Extra | Role |
|---|---|---|---|
| `pytesseract` | `0.3.13` | `[ocr]` | Tesseract Python wrapper |
| `pillow` | `>=12.2.0,<13.0` (range due to `[semantic]` interaction) | `[ocr]` + `[dev]` | Image processing for OCR |
| `fastembed` | `0.8.0` | `[semantic]` | ONNX embeddings for semantic search |
| `numpy` | `>=2.2.6,<3.0.0` (range due to cross-Python constraints) | `[semantic]` + `[dev]` | Transitive of fastembed |

**Transitive-dep constraints** pinned via `[tool.uv].constraint-dependencies` in `pyproject.toml` to pin away from known CVEs:

| Constraint | Reason |
|---|---|
| `authlib>=1.6.11` | GHSA-jj8c-mmj3-mmgv (via fastmcp chain) |
| `cryptography>=46.0.7` | CVE-2026-39892 (via authlib / httpx chain) |
| `pygments>=2.20.0` | CVE-2026-4539 (via pip-audit / other tooling) |
| `python-multipart>=0.0.26` | CVE-2026-40347 (via fastmcp chain) |

---

## 4. CVE status

`uv run pip-audit` against the locked dependency set: **0 vulnerabilities**.

History:
- Pre-hardening (pinned at current-as-of-install versions): **9** findings (2 in `fastmcp`, 1 each in `cryptography`/`authlib`/`pygments`/`pytest`/`python-multipart`, 2 in `pillow`).
- After bumping direct deps to latest patched (`fastmcp 3.2.0`, `pytest 9.0.3`, `pillow` range bumped, `fastembed 0.8.0`) and adding transitive constraints: **0**.

Re-run:

```bash
uv sync --frozen --extra dev
uv run pip-audit --timeout 60
```

---

## 5. Code pruned (per "prune code, not prose" policy)

Removed from upstream because they're executable code we don't use:

| Path | Why |
|---|---|
| `scripts/release.py` | Upstream's PyPI publishing script; we don't republish |
| `.github/workflows/ci.yml` | Upstream CI targeting jztan's infra; replaced with our own |
| `.github/workflows/publish-pypi.yml` | Upstream release pipeline |
| `.github/workflows/dependency-review.yml` | Upstream Dependabot-adjacent config |

Preserved (prose / configs / data — no execution surface):

- `README.md` → renamed to `README.OG.md`; this fork-specific README at `README.md`.
- `CHANGELOG.md`, `ROADMAP.md` — upstream prose; reference material.
- `LICENSE` — mandatory for compliance.
- `codecov.yml`, `server.json` — declarative; don't execute.
- `benchmark_data/ground_truth.json` — test fixture data.

Preserved with a dev-only note:

- `scripts/benchmark_rrf.py`, `scripts/compare_search.py` — exercise runtime for perf benchmarking; never auto-invoked.

---

## 6. Hardening changes (one row per commit)

| Layer | Commit | What it closes |
|---|---|---|
| OCR fallback added | `e51d153` | New feature; no security closure. Transparent scanned-doc support. |
| URL floor: https-only + explicit CIDR SSRF | `3be5f2a` | `http:`, `file:`, `data:`, `blob:`, loopback, link-local, RFC 1918, IPv6 ULA — all rejected at validation time. Cloud metadata `169.254.169.254` in-scope via the link-local range. |
| User allow/deny config | `3a6fa91` | Optional layer on top of floor. Can narrow access (allow list whitelist, deny list blacklist). Cannot loosen the floor. |
| Exact-pin deps + transitive constraints | `f3c496f` | Blocks future-malicious-version auto-install and registry tarball swaps. Closed 9 pip-audit findings. |
| Prune upstream CI / release scripts | `1598fa4` | Removes executable code with no runtime function in our deployment. |
| Plugin install flow + project rename | `3689214` | Ships as a Claude Code plugin; `install.sh` drops config template, verifies prereqs. |
| CI workflows | `8c14e46` | Daily audit + push/PR validation catches CVEs against already-pinned versions. |

---

## 7. Residual risks (accepted and documented)

| Risk | Why accepted |
|---|---|
| **DNS rebinding TOCTOU.** Our SSRF floor DNS-resolves at validation; `httpx` re-resolves at fetch. A malicious DNS server could return different IPs. | Closing requires pinning the validated IP via a custom `httpx.Transport`. ~30 LOC. Single-user threat model + no firewall in deployment plan = accepted. |
| **Dep internals not vendored.** We pin the set with hashes, but dep source isn't in this repo. A malicious version slipped in *before* we pinned would be live. | Static grep + pip-audit + Socket.dev manual cross-check is the practical mitigation. Vendoring loses the auto-update story. |
| **No path sandbox by default.** Resolved paths are unrestricted unless user adds `path.allow` to their config. | OS file-permission boundary is the floor. User opt-in for tighter sandbox. |
| **Tesseract binary trust.** OCR mode shells out to an externally-installed `tesseract` binary that we don't manage. | Tesseract is a well-known OSS tool installed via OS package manager. In the threat model, trust in OS packages is assumed. |
| **fastembed model download (if `[semantic]` installed).** First use of `pdf_search` with `mode="semantic"` downloads ~67 MB ONNX model from Hugging Face. | Runtime, user-triggered network event to a specific allow-listable domain. Opt-in via the `[semantic]` extra — not installed by default. |
| **One pre-existing jztan test is flaky.** `test_clear_expired_removes_stale_embeddings` compares `accessed_at < now() - 0h`; fails on microsecond-resolution gaps. | Inherited from upstream; not a hardening concern. Documented in README gotchas. |

---

## 8. External verification (recommended manual pass)

Per-dep Socket.dev pages (free public risk analysis):

- <https://socket.dev/pypi/package/fastmcp>
- <https://socket.dev/pypi/package/pymupdf>
- <https://socket.dev/pypi/package/pydantic>
- <https://socket.dev/pypi/package/httpx>
- <https://socket.dev/pypi/package/pytesseract>
- <https://socket.dev/pypi/package/pillow>
- <https://socket.dev/pypi/package/fastembed>
- <https://socket.dev/pypi/package/numpy>

Look for: install scripts, network access, filesystem access, shell spawning, obfuscated code, suspicious imports, recent maintainer changes.

Other free public scanners:

- **OSV.dev** — `https://osv.dev/list?ecosystem=PyPI&q=<package>` — broader cross-ecosystem vuln coverage than `pip-audit`.
- **deps.dev** — `https://deps.dev/pypi/<package>` — dep graph, license, Scorecard signals.
- **OpenSSF Scorecard** — `https://scorecard.dev/viewer/?uri=github.com/<org>/<repo>` — maintenance/security-practice score.

---

## 9. Re-audit recipe

Run after any dep change:

1. `uv sync --frozen --extra dev --extra ocr` — reproducible install, no resolution.
2. `uv run pip-audit --timeout 60` — known-CVE check.
3. Grep loop from Section 2 across `src/pdf_mcp/`.
4. Spot-check Socket.dev pages for any new dep.
5. `uv run pytest` — all non-flaky tests pass.
6. Update this document with the new audit head SHA.

For ongoing automation:

- `.github/workflows/pdf-mcp-ocr-ci.yml` runs `pip-audit` on every push and PR.
- `.github/workflows/pdf-mcp-ocr-audit.yml` runs `pip-audit` daily.

---

## 10. Entity-name compliance

Per the hardening skill's non-negotiable entity-name policy: no company, agency, product, customer, or internal-project name appears in this repo's code, tests, commit messages, config examples, or docs. Verification:

```bash
grep -rniE '<entity-name>' . \
    --include="*.py" --include="*.md" --include="*.toml" \
    --include="*.sh" --include="*.json" --include="*.yml" \
  | grep -v '\.venv' | grep -v 'README\.OG\.md' | grep -v '\.git/'

git log --all --format="%B" | grep -iE '<entity-name>'
```

`README.OG.md` is excluded by design (upstream's own README; if upstream names an entity, that's their call).
