"""Tests for pdf_mcp.config — load_config + path/URL allow/deny helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from pdf_mcp import config as cfg


@pytest.fixture(autouse=True)
def _reset_config_cache() -> Any:
    """Reset the module-level cache between tests so each test starts fresh."""
    cfg._reset_cache_for_tests()
    yield
    cfg._reset_cache_for_tests()


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point config.CONFIG_PATH at a tmp file for this test."""
    fake = tmp_path / "pdf-mcp-ocr.json"
    monkeypatch.setattr(cfg, "CONFIG_PATH", fake)
    return fake


# ── load_config ──────────────────────────────────────────────────────────


class TestLoadConfigDefaults:
    def test_missing_file_yields_empty_defaults(self, config_path: Path) -> None:
        assert not config_path.exists()
        loaded = cfg.load_config()
        assert loaded.path.allow == [] and loaded.path.deny == []
        assert loaded.url.allow == [] and loaded.url.deny == []

    def test_malformed_json_falls_back_to_defaults(self, config_path: Path) -> None:
        config_path.write_text("{not json", encoding="utf-8")
        loaded = cfg.load_config()
        assert loaded.path.allow == [] and loaded.path.deny == []

    def test_non_object_root_falls_back_to_defaults(self, config_path: Path) -> None:
        config_path.write_text('["an","array","not","object"]', encoding="utf-8")
        loaded = cfg.load_config()
        assert loaded.path.allow == []

    def test_well_formed_config_parses(self, config_path: Path) -> None:
        config_path.write_text(
            json.dumps(
                {
                    "path": {"allow": ["~/Documents/**"], "deny": ["~/.ssh/**"]},
                    "url": {
                        "allow": ["*.internal.example.com"],
                        "deny": ["evil.example.com"],
                    },
                }
            ),
            encoding="utf-8",
        )
        loaded = cfg.load_config()
        assert loaded.path.allow == ["~/Documents/**"]
        assert loaded.path.deny == ["~/.ssh/**"]
        assert loaded.url.allow == ["*.internal.example.com"]
        assert loaded.url.deny == ["evil.example.com"]

    def test_missing_section_yields_empty_ruleset(self, config_path: Path) -> None:
        config_path.write_text(
            json.dumps({"path": {"allow": ["/tmp"]}}), encoding="utf-8"
        )
        loaded = cfg.load_config()
        assert loaded.path.allow == ["/tmp"]
        assert loaded.path.deny == []
        assert loaded.url.allow == []  # url section missing entirely

    def test_non_array_allow_treated_as_empty(self, config_path: Path) -> None:
        config_path.write_text(
            json.dumps({"path": {"allow": "not-an-array", "deny": [123, "valid"]}}),
            encoding="utf-8",
        )
        loaded = cfg.load_config()
        # deny list had a non-string; whole field rejected (strict).
        assert loaded.path.allow == []
        assert loaded.path.deny == []

    def test_caches_after_first_load(
        self, config_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path.write_text(json.dumps({"path": {"allow": ["a"]}}), encoding="utf-8")
        call_count = {"n": 0}
        real_read = Path.read_text

        def counting_read(self: Path, *args: Any, **kwargs: Any) -> str:
            if self == config_path:
                call_count["n"] += 1
            return real_read(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", counting_read)
        cfg.load_config()
        cfg.load_config()
        cfg.load_config()
        assert call_count["n"] == 1


# ── check_path_allowed ────────────────────────────────────────────────────


class TestCheckPathAllowed:
    def test_permissive_when_both_lists_empty(self, config_path: Path) -> None:
        config_path.write_text(
            json.dumps({"path": {"allow": [], "deny": []}}), encoding="utf-8"
        )
        cfg.check_path_allowed("/any/random/path.pdf")  # must not raise

    def test_deny_hit_raises(self, config_path: Path) -> None:
        home = os.path.expanduser("~")
        config_path.write_text(
            json.dumps({"path": {"deny": [f"{home}/.ssh/**"]}}), encoding="utf-8"
        )
        with pytest.raises(ValueError, match="deny list"):
            cfg.check_path_allowed(f"{home}/.ssh/id_rsa")

    def test_tilde_expansion_in_deny_patterns(self, config_path: Path) -> None:
        config_path.write_text(
            json.dumps({"path": {"deny": ["~/.ssh/**"]}}), encoding="utf-8"
        )
        with pytest.raises(ValueError, match="deny list"):
            cfg.check_path_allowed(os.path.expanduser("~/.ssh/id_rsa"))

    def test_allow_list_enforces_whitelist(self, config_path: Path) -> None:
        home = os.path.expanduser("~")
        config_path.write_text(
            json.dumps({"path": {"allow": [f"{home}/Documents/**"]}}), encoding="utf-8"
        )
        cfg.check_path_allowed(f"{home}/Documents/x.pdf")  # permitted
        with pytest.raises(ValueError, match="allow list"):
            cfg.check_path_allowed("/tmp/x.pdf")  # not in allow list

    def test_deny_wins_when_both_match(self, config_path: Path) -> None:
        config_path.write_text(
            json.dumps(
                {
                    "path": {
                        "allow": ["/tmp/**"],
                        "deny": ["/tmp/secrets/**"],
                    }
                }
            ),
            encoding="utf-8",
        )
        cfg.check_path_allowed("/tmp/ok.pdf")  # permitted by allow
        with pytest.raises(ValueError, match="deny list"):
            cfg.check_path_allowed("/tmp/secrets/creds.pdf")


# ── check_url_host_allowed ──────────────────────────────────────────────


class TestCheckUrlHostAllowed:
    def test_permissive_when_no_rules(self, config_path: Path) -> None:
        config_path.write_text(json.dumps({"url": {}}), encoding="utf-8")
        cfg.check_url_host_allowed("anything.example.com")  # no raise

    def test_deny_hit_raises(self, config_path: Path) -> None:
        config_path.write_text(
            json.dumps(
                {"url": {"deny": ["evil.example.com", "*.malware.net"]}}
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="deny list"):
            cfg.check_url_host_allowed("evil.example.com")
        with pytest.raises(ValueError, match="deny list"):
            cfg.check_url_host_allowed("x.malware.net")

    def test_allow_list_enforces_whitelist(self, config_path: Path) -> None:
        config_path.write_text(
            json.dumps({"url": {"allow": ["*.internal.example.com"]}}),
            encoding="utf-8",
        )
        cfg.check_url_host_allowed("docs.internal.example.com")  # permitted
        with pytest.raises(ValueError, match="allow list"):
            cfg.check_url_host_allowed("example.com")

    def test_deny_wins_over_allow(self, config_path: Path) -> None:
        config_path.write_text(
            json.dumps(
                {
                    "url": {
                        "allow": ["*.example.com"],
                        "deny": ["evil.example.com"],
                    }
                }
            ),
            encoding="utf-8",
        )
        cfg.check_url_host_allowed("ok.example.com")  # allowed
        with pytest.raises(ValueError, match="deny list"):
            cfg.check_url_host_allowed("evil.example.com")

    def test_host_glob_matches_across_dots(self, config_path: Path) -> None:
        """`*` in host patterns is wildcard-cert style (matches any chars incl. dots)."""
        config_path.write_text(
            json.dumps({"url": {"allow": ["*.corp.example.com"]}}),
            encoding="utf-8",
        )
        cfg.check_url_host_allowed("a.b.corp.example.com")

    def test_host_match_is_case_insensitive(self, config_path: Path) -> None:
        config_path.write_text(
            json.dumps({"url": {"deny": ["EVIL.example.com"]}}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="deny list"):
            cfg.check_url_host_allowed("evil.EXAMPLE.com")


# ── CONFIG_PATH sanity ──────────────────────────────────────────────────


class TestConfigPathLocation:
    def test_resolves_under_plugin_settings(self) -> None:
        # The real module-level CONFIG_PATH must live under ~/.claude/plugin-settings/.
        # (The `config_path` fixture monkey-patches this in other tests.)
        import importlib

        importlib.reload(cfg)
        expected_tail = Path(".claude/plugin-settings/pdf-mcp-ocr.json")
        assert str(cfg.CONFIG_PATH).endswith(str(expected_tail))
