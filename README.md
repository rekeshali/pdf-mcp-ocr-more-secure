# pdf-mcp-ocr-more-secure

A security-hardened fork of [jztan/pdf-mcp](https://github.com/jztan/pdf-mcp) with an added OCR fallback for scanned PDFs. Packaged as a Claude Code plugin for internal distribution on a locked-down workstation behind a trusted internal model proxy. The upstream README is preserved as [`README.OG.md`](./README.OG.md).

---

## What this is

An **MCP server** (Model Context Protocol — the stdio-based plugin interface Claude Code uses to expose local tools to the agent) that lets Claude read PDFs. Once installed, seven tools become available in every Claude Code session:

- `pdf_info` — document metadata, page count, table of contents (inline for small TOCs).
- `pdf_read_pages` — text, images, and tables from specific page ranges (`"1-5,10,15-20"`). OCR fallback for scanned pages.
- `pdf_read_all` — full document text up to a page cap. OCR fallback for scanned pages.
- `pdf_search` — hybrid keyword (BM25/FTS5) + semantic search via Reciprocal Rank Fusion. Semantic requires the `[semantic]` extra.
- `pdf_get_toc` — extracts the document's table of contents.
- `pdf_cache_stats` / `pdf_cache_clear` — inspect / trim the on-disk extraction cache.

You don't invoke these directly. You tell Claude something like *"summarize `~/Downloads/paper.pdf`"* or *"find the section about cryo cooling in `/reports/manual.pdf`"* — Claude picks the tool, runs it, and works from the returned text.

**OCR fallback:** when a page's embedded text layer is empty or sparse (<50 non-whitespace chars), the server renders the page at 300 DPI via PyMuPDF and runs it through Tesseract automatically. Scanned documents return readable text without the agent having to know the PDF is image-based. Callers can override with `force_ocr=True` (always OCR) or `skip_ocr=True` (never OCR).

---

## Why this fork

Three layered concerns, in order:

1. **Can this third-party package leak file contents to external actors?** The upstream has SSRF protections, but they use Python's `ipaddress.is_private/is_loopback/...` properties and allow `http://` as well as `https://`. This fork tightens the URL floor to `https://`-only with an explicit, documentable CIDR block list.
2. **Can I audit what's actually in the dep tree?** Upstream has no pinned lockfile discipline ("`>=`" bounds everywhere). This fork exact-pins everything and enforces `uv sync --frozen` on install and in CI.
3. **How do I make users without the `[ocr]` extra still see useful errors?** The upstream doesn't ship OCR at all; this fork adds it as an optional extra that degrades gracefully when missing.

This is **not** a hardened-for-shared-deployment package. It is **not** a mitigation for AI-agent prompt injection.

---

## Threat model

### In scope

- The package code and its direct/transitive dependencies as a potential data-exfil vector.
- Accidental network egress (URL-source fetches to unintended hosts).
- Supply-chain install-time code execution.

### Out of scope

- **AI-agent risks** (prompt injection, agent misuse). Mitigate at the agent/policy layer, not here.
- **Multi-user / shared-server deployments.** One user, one machine.
- **Adversaries with physical or kernel access.**

### Operating conditions under which this fork is considered secure

1. Installed from your organization's internal mirror, not public PyPI.
2. Claude Code CLI uses only a trusted internal model proxy.
3. Single-user workstation on a secured internal network.
4. Rebuilds use `uv sync --frozen` (blocks lockfile drift and install-time code in transitive deps).

If any of these is not met, treat this fork as equivalent to the upstream package.

---

## What we changed vs upstream

Hardening stacks as **hardcoded floor** (always on, not configurable) + **user layer** (optional additional restrictions on top). The user layer can narrow access further but cannot loosen the floor.

| Hardening | Details |
|---|---|
| Hardcoded URL floor | Two checks that can't be disabled. **(1) Scheme must be `https:`** — `http:`, `file:`, `data:`, `blob:`, malformed URLs rejected at load time. **(2) SSRF block list** — every DNS-resolved IP is checked against `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16` (link-local + cloud metadata `169.254.169.254`), `0.0.0.0/8`, `::1/128`, `fc00::/7` (IPv6 ULA), `fe80::/10` (IPv6 link-local). |
| User-configurable layer (optional) | JSON config at `~/.claude/plugin-settings/pdf-mcp-ocr.json` lets you add allow/deny rules on top of the floor: hostname patterns for `url:` sources and path patterns for `path:` sources. Shell-glob syntax, deny wins on conflict. Cannot override the floor — if your allow list permits a host that resolves to a blocked IP, the request is still rejected. See **User config** section below. |
| Exact-pinned deps | `pyproject.toml` uses `==` for every direct dep (with explained range exceptions for `numpy` and `pillow` due to cross-Python-version constraints). `uv.lock` pins the full resolved tree with SHA-256 hashes. `uv sync --frozen` blocks drift at install and in CI. |
| OCR fallback added | `pytesseract` integration in `extractor.py`. Not a new tool — transparent fallback inside existing read tools when the text layer is sparse. Graceful degradation when the `[ocr]` extra isn't installed. |
| Plugin install flow | Packaged as a Claude Code plugin with `.claude-plugin/plugin.json` and a `${CLAUDE_PLUGIN_ROOT}`-based `.mcp.json`. Install/uninstall/enable/disable scripts provided. |

---

## Installation

**Prerequisites:**

- `uv` ([install](https://docs.astral.sh/uv/)) or `pip install uv`
- `claude` CLI logged in
- `tesseract` binary (only if you want OCR):
  - macOS: `brew install tesseract`
  - Debian/Ubuntu: `sudo apt install tesseract-ocr`

```bash
git clone https://github.com/rekeshali/pdf-mcp-ocr-more-secure
cd pdf-mcp-ocr-more-secure
./install.sh
```

`install.sh` runs `uv sync --frozen --extra ocr` to install Python deps from the locked set, drops a template config at `~/.claude/plugin-settings/pdf-mcp-ocr.json` if missing, then `claude plugin install . --scope user`. Idempotent on re-run.

**Verify:**

```bash
claude plugin list     # should list pdf-mcp-ocr
```

Inside a Claude session: `/mcp` shows `pdf-mcp-ocr` as a connected server.

---

## User config

Optional. File location: **`~/.claude/plugin-settings/pdf-mcp-ocr.json`**. All fields optional; missing fields = permissive within the floor.

```json
{
  "path": {
    "allow": ["~/Documents/claude-pdfs/**"],
    "deny":  ["~/.ssh/**", "~/.aws/**"]
  },
  "url": {
    "allow": ["*.internal.example.com", "docs.corp.example.com"],
    "deny":  ["evil.example.com"]
  }
}
```

**Semantics:**

- `path` rules apply to the `path:` source; `url` rules apply to the `url:` source (host match only).
- Path patterns are shell globs (`fnmatch`) with leading `~` expansion.
- URL host patterns: `*` matches any characters including dots (wildcard-cert style), case-insensitive.
- If `allow` is non-empty, the input **must** match one entry. Empty allow = permissive within the floor.
- `deny` is checked too. **Deny wins** when both match (fail-closed).
- **Always-on floor:** scheme floor (https-only) and SSRF block list are enforced regardless of this config. The user `url.allow` cannot permit a host that resolves to a blocked IP.
- **No hot reload.** Config is cached on first read; edits require restarting the MCP server (`./disable.sh && ./enable.sh`).

**Known caveat — DNS rebinding:** we resolve the URL host at validation time, then `httpx` resolves it again at fetch time. An attacker controlling DNS for an allow-listed host could return a public IP for our lookup and a private IP for httpx's. Closing this would require a custom httpx transport that pins the validated IP. Accepted residual for this fork.

---

## Usage

Once installed, the seven tools are available in any Claude Code session. Ask naturally:

> Read `/path/to/report.pdf` and summarize section 3.
>
> What's in `~/Downloads/scanned-manual.pdf`? It's an image-based scan.
>
> Summarize https://internal.example.com/manuals/foo.pdf — just the table of contents.
>
> Search for "cryogenic cooling" in `/path/to/paper.pdf`.

For scanned PDFs, OCR runs automatically when the text layer is empty. You'll see `ocr_used: true` and a confidence indicator (`"high"` >10 tokens, `"low"` otherwise) in the returned data.

See [`README.OG.md`](./README.OG.md) for the full tool schema with all parameters.

---

## Update, disable, uninstall

```bash
./disable.sh      # stop Claude from loading it (plugin stays installed)
./enable.sh       # turn it back on
./uninstall.sh    # fully remove the plugin
```

To update: `git pull` in your clone, then re-run `./install.sh`. The plugin is copied into Claude's plugin cache at install time, so pulling alone does nothing — the re-install step is required.

---

## Gotchas

1. **Plugin cache is not a live link.** The plugin directory Claude loads is a *copy* made at install time. Editing files in your clone has no effect until `./install.sh`.
2. **Opening this repo in Claude Code shows a cosmetic `pdf-mcp-ocr` failure.** The tracked `.mcp.json` uses `${CLAUDE_PLUGIN_ROOT}`, which only resolves inside the plugin context. Project-scope load fails; the installed plugin is unaffected.
3. **`tesseract` is a system binary, not a Python package.** `install.sh` warns if missing. OCR fallback degrades gracefully (returns original sparse text + error info) when pytesseract can't import. Set `skip_ocr=True` to suppress the attempt.
4. **Re-running `./install.sh` fully replaces the prior install.** Idempotent by design.
5. **DNS rebinding TOCTOU is not closed.** Documented as an accepted residual in the User config section.
6. **Known pre-existing flaky test:** `tests/test_cache.py::TestPageEmbeddingsLifecycle::test_clear_expired_removes_stale_embeddings` is timing-sensitive on fast machines (compares `accessed_at < now() - 0h`, fails on microsecond-resolution gaps). Inherited from upstream. 338/339 pass; unrelated to hardening.

---

## Rebuilding from source

Only needed if you're modifying code:

```bash
uv sync --frozen --extra dev --extra ocr
uv run pytest
uv run pip-audit
```

- `--frozen` refuses to modify `uv.lock`; if any resolution would change, install fails loudly instead of silently drifting.
- `--extra ocr` brings in `pytesseract` + `pillow` (still needs the `tesseract` system binary).
- `--extra dev` brings in pytest, mypy, pip-audit, etc.

---

## Supply-chain and CI

Two workflows at `.github/workflows/`:

- **`pdf-mcp-ocr-ci.yml`** — runs on push to `develop`/`main` and on PRs. Installs with `uv sync --frozen`, runs `pip-audit` (CVE scan against pinned versions), the test suite, and a smoke import. Workflow fails on issues; merge-blocking requires branch protection / rulesets configured at the repo level.
- **`pdf-mcp-ocr-audit.yml`** — runs daily at 13:00 UTC + `workflow_dispatch`. Just `uv sync --frozen` + `pip-audit`. Catches newly-disclosed CVEs against already-pinned versions (push-based CI can't see these).

Both files expose a single `TOOL_PATH` env var at the top so they can drop into a monorepo with a one-line edit. Grep for `CHANGE-AFTER-MOVE` when relocating.

**Dep-update discipline:** changes must go through a PR that modifies both `pyproject.toml` and `uv.lock`, with human review of the lockfile diff. Do not run `uv lock` or `uv sync` without `--frozen` on release branches.

---

## Dev-time notes

- Upstream's dev scripts (`scripts/benchmark_rrf.py`, `scripts/compare_search.py`) are preserved for perf comparisons. They're dev-only; never auto-invoked. Documented in `SECURITY-AUDIT.md` as dev-only code surface.
- Full audit receipts live in [`SECURITY-AUDIT.md`](./SECURITY-AUDIT.md).
- Test suite: `uv run pytest` (338 pass / 1 pre-existing jztan flake).

---

## License

MIT, inherited from upstream. See [`LICENSE`](./LICENSE).
