"""v6 state-path resolver. The load-bearing guarantee: a dev checkout (BABY_HOME
unset) is byte-identical to before -- everything stays cwd-relative -- and an
installed build points config.yaml / .env / baby.db at a per-user BABY_HOME."""

from __future__ import annotations

from core import paths


def test_dev_defaults_to_cwd(monkeypatch, tmp_path):
    # No BABY_HOME => cwd-relative, exactly as pre-v6.
    monkeypatch.delenv("BABY_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    assert paths.baby_home() == tmp_path
    assert paths.config_path() == tmp_path / "config.yaml"
    assert paths.env_path() == tmp_path / ".env"
    assert paths.db_path() == tmp_path / "baby.db"


def test_baby_home_env_wins(monkeypatch, tmp_path):
    home = tmp_path / "data"
    monkeypatch.setenv("BABY_HOME", str(home))
    assert paths.baby_home() == home
    assert paths.config_path() == home / "config.yaml"
    assert paths.env_path() == home / ".env"
    assert paths.db_path() == home / "baby.db"


def test_ensure_config_seeds_when_absent(monkeypatch, tmp_path):
    home = tmp_path / "data"  # does not exist yet
    monkeypatch.setenv("BABY_HOME", str(home))
    template = tmp_path / "config.default.yaml"
    template.write_text("safety:\n  mode: enforce\n", encoding="utf-8")

    out = paths.ensure_config(template)
    assert out == home / "config.yaml"
    assert out.read_text(encoding="utf-8") == "safety:\n  mode: enforce\n"


def test_ensure_config_never_clobbers(monkeypatch, tmp_path):
    home = tmp_path / "data"
    home.mkdir()
    monkeypatch.setenv("BABY_HOME", str(home))
    existing = home / "config.yaml"
    existing.write_text("owner: real-user\n", encoding="utf-8")
    template = tmp_path / "config.default.yaml"
    template.write_text("owner:\n", encoding="utf-8")

    out = paths.ensure_config(template)
    # A returning user's config is never overwritten by the template.
    assert out.read_text(encoding="utf-8") == "owner: real-user\n"


def test_ensure_config_missing_template_no_crash(monkeypatch, tmp_path):
    home = tmp_path / "data"
    monkeypatch.setenv("BABY_HOME", str(home))
    out = paths.ensure_config(tmp_path / "nope.yaml")
    assert out == home / "config.yaml"
    assert not out.exists()  # nothing to seed, and no exception
