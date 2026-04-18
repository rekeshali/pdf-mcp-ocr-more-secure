"""Loads user config from ~/.claude/plugin-settings/pdf-mcp-ocr.json.

Mirrors the TypeScript implementation at
``pdf-reader-mcp-more-secure/src/utils/config.ts``.

Shape::

    {
      "path": {"allow": [str, ...], "deny": [str, ...]},
      "url":  {"allow": [str, ...], "deny": [str, ...]}
    }

Semantics:

* Missing file or missing section → permissive defaults (logs a debug / warn).
* Empty ``allow`` → permissive for that input type.
* Non-empty ``allow`` → whitelist mode; input must match one entry.
* ``deny`` always wins when both match (fail-closed on conflict).
* The hardcoded floor (HTTPS-only + SSRF block list for URLs) is NOT
  loosened by user config, even if a user adds ``*`` to allow.

Patterns are shell globs (``fnmatch``). Path patterns support leading ``~``
expansion. URL patterns match the HOSTNAME only; ``*`` matches any
characters including dots (wildcard-cert style), case-insensitive.

No hot reload — the config is cached on first read for the process
lifetime. Edits require restarting the MCP server.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── Types ─────────────────────────────────────────────────────────────────


class RuleSet:
    """Allow/deny pair; empty allow = permissive."""

    __slots__ = ("allow", "deny")

    def __init__(self, allow: list[str] | None = None, deny: list[str] | None = None):
        self.allow: list[str] = list(allow) if allow else []
        self.deny: list[str] = list(deny) if deny else []

    def __repr__(self) -> str:
        return f"RuleSet(allow={self.allow!r}, deny={self.deny!r})"


class PdfMcpConfig:
    """Resolved config; both RuleSets default to empty (permissive)."""

    __slots__ = ("path", "url")

    def __init__(self, path: RuleSet | None = None, url: RuleSet | None = None):
        self.path: RuleSet = path or RuleSet()
        self.url: RuleSet = url or RuleSet()

    def __repr__(self) -> str:
        return f"PdfMcpConfig(path={self.path!r}, url={self.url!r})"


# ── File location ────────────────────────────────────────────────────────

CONFIG_PATH: Path = (
    Path(os.path.expanduser("~")) / ".claude" / "plugin-settings" / "pdf-mcp-ocr.json"
)


# ── Parsing ──────────────────────────────────────────────────────────────


def _parse_rule_set(raw: Any) -> RuleSet:
    """Accept dict with optional ``allow``/``deny`` str-lists; reject others."""
    if not isinstance(raw, dict):
        return RuleSet()

    def _str_list(v: Any) -> list[str]:
        if not isinstance(v, list):
            return []
        if not all(isinstance(x, str) for x in v):
            return []
        return v

    return RuleSet(allow=_str_list(raw.get("allow")), deny=_str_list(raw.get("deny")))


# ── Cache ────────────────────────────────────────────────────────────────

_cached: PdfMcpConfig | None = None


def load_config() -> PdfMcpConfig:
    """Load (or return the cached copy of) the user's config."""
    global _cached
    if _cached is not None:
        return _cached

    try:
        raw = CONFIG_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.debug("No config file at %s; using permissive defaults.", CONFIG_PATH)
        _cached = PdfMcpConfig()
        return _cached
    except OSError as exc:
        logger.warning(
            "Could not read config file %s (%s); using defaults.", CONFIG_PATH, exc
        )
        _cached = PdfMcpConfig()
        return _cached

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning(
            "Config file %s is not valid JSON (%s); using defaults.", CONFIG_PATH, exc
        )
        _cached = PdfMcpConfig()
        return _cached

    if not isinstance(parsed, dict):
        logger.warning(
            "Config root at %s is not an object; using defaults.", CONFIG_PATH
        )
        _cached = PdfMcpConfig()
        return _cached

    _cached = PdfMcpConfig(
        path=_parse_rule_set(parsed.get("path")),
        url=_parse_rule_set(parsed.get("url")),
    )
    return _cached


def _reset_cache_for_tests() -> None:
    """Test-only. Resets the module-level cache so the next load_config() re-reads disk."""
    global _cached
    _cached = None


# ── Path matching ────────────────────────────────────────────────────────


def _expand_tilde(pattern: str) -> str:
    """Expand leading ~ or ~/ in a path pattern. ``~user/`` not supported."""
    if pattern == "~":
        return os.path.expanduser("~")
    if pattern.startswith("~/"):
        return os.path.expanduser("~") + pattern[1:]
    return pattern


def path_matches_any(resolved_path: str, patterns: list[str]) -> str | None:
    """Return the first pattern that matches `resolved_path`, or None."""
    for raw in patterns:
        expanded = _expand_tilde(raw)
        if fnmatch.fnmatch(resolved_path, expanded):
            return raw
    return None


def check_path_allowed(resolved_path: str) -> None:
    """Apply path allow/deny rules. Raises ValueError if rejected."""
    cfg = load_config()
    deny_hit = path_matches_any(resolved_path, cfg.path.deny)
    if deny_hit is not None:
        raise ValueError(
            f"Path {resolved_path!r} is in the configured deny list "
            f"(matched pattern {deny_hit!r})."
        )
    if cfg.path.allow and path_matches_any(resolved_path, cfg.path.allow) is None:
        raise ValueError(
            f"Path {resolved_path!r} is not in the configured allow list."
        )


# ── URL host matching ────────────────────────────────────────────────────


def _host_glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Convert a shell-style host glob to a compiled case-insensitive regex.

    ``*`` matches any characters including dots (wildcard-cert style).
    ``?`` matches any single character. Other regex metacharacters are escaped.
    """
    escaped = re.sub(r"[.+^${}()|\[\]\\]", lambda m: "\\" + m.group(0), pattern)
    regex_str = escaped.replace("*", ".*").replace("?", ".")
    return re.compile(f"^{regex_str}$", re.IGNORECASE)


def host_matches_any(host: str, patterns: list[str]) -> str | None:
    """Return the first host pattern that matches ``host``, or None."""
    for pat in patterns:
        if _host_glob_to_regex(pat).match(host):
            return pat
    return None


def check_url_host_allowed(host: str) -> None:
    """Apply URL-host allow/deny rules. Raises ValueError if rejected.

    This check layers on top of the SSRF floor in url_fetcher.py — it runs
    after the floor has already passed. It cannot loosen the floor.
    """
    cfg = load_config()
    deny_hit = host_matches_any(host, cfg.url.deny)
    if deny_hit is not None:
        raise ValueError(
            f"Host {host!r} is in the configured URL deny list "
            f"(matched pattern {deny_hit!r})."
        )
    if cfg.url.allow and host_matches_any(host, cfg.url.allow) is None:
        raise ValueError(
            f"Host {host!r} is not in the configured URL allow list."
        )
