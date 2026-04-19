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
| `fastembed` | `0.8.0` | `[semantic]` | ONNX embeddings for semantic search (loads the Nomic v1.5 model — see §10) |
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
| **Embedding model download (if `[semantic]` installed).** First use of `pdf_search` with `mode="semantic"` downloads the Nomic v1.5 ONNX model from Hugging Face. | On hardened workstations, use the airgap side-load procedure documented in §9. Full per-file SHA-256 vetting in §10. Opt-in via the `[semantic]` extra — not installed by default. |
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

## 9. Hugging Face as a supply-chain source (for `[semantic]`)

The `[semantic]` extra pulls `fastembed`, which downloads an embedding model from Hugging Face Hub on first use. This section documents the trust model for that specific network event.

### HF as a company

- NYC-incorporated, US jurisdiction.
- SOC 2 Type 2 certified.
- Major enterprise adopters include AWS, Microsoft, Google, Meta, IBM, NVIDIA.
- No publicly-disclosed platform breach history.
- Runs automated scanning on uploads (malicious-pickle detection, typosquat warnings, usage-pattern anomaly flags).

### What HF is NOT

- **Not a curated archive.** Any user can upload a model. The platform delivers bytes — it does not vet the publisher's intent.
- **Not signed-by-default.** Model files are hashed (SHA-256 on the HF UI), but not cryptographically signed by the publisher in the Sigstore / OpenSSF sense. Verification depends on the publisher's operational security.

### Trust decomposition for an HF-hosted model on a hardened workstation

The trust you're extending when downloading a model from HF has two independent axes:

1. **Trust in HF the platform** (did the bytes get delivered intact, unaltered) — SOC 2 and the CDN integrity model cover this. Treat HF itself as equivalent to downloading from GitHub Releases or PyPI in risk terms.

2. **Trust in the specific publisher and file** (is the weight itself safe and non-malicious) — this is a per-file decision. Verify SHA-256 against the publisher's model card. Optionally cross-reference against commit history + paper citations.

### Required workflow on a hardened workstation

1. Download on a trusted machine with internet access:
   ```bash
   python -c "from fastembed import TextEmbedding; TextEmbedding('nomic-ai/nomic-embed-text-v1.5')"
   ```
   Files land in `~/.cache/fastembed/models--nomic-ai--nomic-embed-text-v1.5/`.

2. Verify each file's SHA-256 against the value published on the HF model card (`https://huggingface.co/nomic-ai/nomic-embed-text-v1.5` → *Files and versions* → individual file → *Copy download link* or click the file and scroll to metadata).

3. Transfer the verified cache directory to the target workstation (USB, internal file-transfer, however your org approves). The target never touches HF Hub.

4. Pin `fastembed==0.8.0` in `pyproject.toml` so the cache directory layout is stable across rebuilds.

### Why ONNX-format files are safer than PyTorch `.pt`/`.bin`

ONNX files are Protocol Buffer serializations of a computation graph + numeric weights. They do not contain executable code. Loading an ONNX file cannot RCE you the way a PyTorch pickle file can. The residual risks — see §10 below — are poisoned outputs, watermarking, parser 0-days in `onnxruntime`, and custom-operator loading. `fastembed` does not enable custom operators; onnxruntime is patched via our standard dep-pinning discipline.

---

## 10. Nomic v1.5 model — per-file provenance

The specific model this fork uses when the `[semantic]` extra is installed. Every fact here is independently verifiable via the HF model card and the Nomic GitHub repository.

### Identity

- **Repository:** `nomic-ai/nomic-embed-text-v1.5` on Hugging Face Hub.
- **Publisher:** Nomic AI Inc., NYC, US-incorporated private company.
- **License:** Apache License 2.0.
- **Paper:** *"Nomic Embed: Training a Reproducible Long Context Text Embedder"* (Nussbaum et al., 2024). Published at https://arxiv.org/abs/2402.01613.
- **Training code + data:** publicly released. This is unusual — most model publishers release only the weights. Nomic v1.5's Contrastor training framework and the 235 M-pair training corpus are on GitHub at `nomic-ai/contrastors`.

### Why this model (vs alternatives)

Candidate shortlist considered (all Apache-2.0 or MIT, Western-published, comparable quality):

| Option | Publisher | Provenance transparency | Chosen? |
|---|---|---|---|
| `nomic-ai/nomic-embed-text-v1.5` | Nomic AI (NYC) | Weights + code + training data public | ✅ Yes |
| `snowflake/snowflake-arctic-embed-s` | Snowflake (US, public co.) | Weights public; training data not | — |
| `mixedbread-ai/mxbai-embed-xsmall-v1` | mixedbread.ai (Berlin) | Weights public; training data not | — |
| `intfloat/e5-small-v2` | Microsoft (via MSRA Beijing researcher) | Mixed jurisdiction | — |
| `sentence-transformers/all-MiniLM-L6-v2` | UKP Lab / community | Weights public; training data not; older, weaker quality | — |
| `BAAI/bge-small-en-v1.5` | BAAI (Beijing Academy of AI, Chinese govt-funded) | — | ❌ No (upstream default; replaced) |

Nomic wins on provenance transparency (only candidate with public training code + data) and on quality (top of the list by MTEB retrieval).

### Technical behaviour in this fork

- Native output: 768-dim float32.
- We **truncate to 384-dim** via Matryoshka (trained to tolerate truncation) and L2-renormalise. Rationale: preserve binary compatibility with the existing SQLite BLOB column without schema migration, for minimal quality cost (~1-2 MTEB points at 384 vs 768).
- Prompt prefixes: `"search_document: "` for indexed text, `"search_query: "` for queries. Required for Nomic v1.5; applied inside `encode()`/`encode_query()`.
- Produced vectors are stored in the SQLite cache tagged with `MODEL_NAME = "nomic-ai/nomic-embed-text-v1.5"`; future model swaps automatically invalidate stale cached vectors.

### File-level SHA-256 (to be filled after download + verification)

Run this on a trusted machine after the first download and update this section with the actual hashes:

```bash
cd ~/.cache/fastembed/models--nomic-ai--nomic-embed-text-v1.5/snapshots/<snapshot-hash>/
for f in *.json *.onnx *.txt; do shasum -a 256 "$f"; done
```

Then cross-reference each hash against the HF model card's published SHA-256 values (click a file on the HF UI to see its hash). Record here once confirmed:

| Filename | SHA-256 | Size | Verified against HF |
|---|---|---|---|
| `config.json` | `<TBD — fill after download>` | — | — |
| `tokenizer.json` | `<TBD>` | — | — |
| `tokenizer_config.json` | `<TBD>` | — | — |
| `special_tokens_map.json` | `<TBD>` | — | — |
| `onnx/model.onnx` or `model.onnx` | `<TBD>` | — | — |
| `onnx/model_quantized.onnx` (if used) | `<TBD>` | — | — |

### Residual risks specific to Nomic

- **Poisoned rankings.** An adversarial model could return subtly wrong vectors. Mitigated by provenance (Nomic AI is a reputable OSS lab) and by the embeddings never leaving the local machine (no watermark exfiltration possible).
- **Supply-chain of future updates.** If we ever bump to Nomic v2, redo the full SHA-256 verification on the new file set.
- **ONNX parser 0-day in `onnxruntime`.** Rare, class-of-attack. Mitigated by `pip-audit` on every push + daily schedule.

---

## 11. Re-audit recipe

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

## 12. Entity-name compliance

Per the hardening skill's non-negotiable entity-name policy: no company, agency, product, customer, or internal-project name appears in this repo's code, tests, commit messages, config examples, or docs. Verification:

```bash
grep -rniE '<entity-name>' . \
    --include="*.py" --include="*.md" --include="*.toml" \
    --include="*.sh" --include="*.json" --include="*.yml" \
  | grep -v '\.venv' | grep -v 'README\.OG\.md' | grep -v '\.git/'

git log --all --format="%B" | grep -iE '<entity-name>'
```

`README.OG.md` is excluded by design (upstream's own README; if upstream names an entity, that's their call).
