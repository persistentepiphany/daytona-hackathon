"""env_key: process environment first, then .env as backup."""

from repro import env


def _isolate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(env, "_REPO_ROOT", tmp_path)


def test_process_env_wins_over_dotenv(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAYTONA_API_KEY", "from-env")
    (tmp_path / ".env").write_text("DAYTONA_API_KEY=from-file\n")
    assert env.env_key("DAYTONA_API_KEY") == "from-env"


def test_dotenv_used_when_env_missing(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.delenv("DAYTONA_API_KEY", raising=False)
    monkeypatch.delenv("DAYTONA_API", raising=False)
    (tmp_path / ".env").write_text("DAYTONA_API_KEY=from-file\n")
    assert env.env_key("DAYTONA_API_KEY", "DAYTONA_API") == "from-file"


def test_empty_env_falls_through_to_dotenv(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAYTONA_API_KEY", "")
    (tmp_path / ".env").write_text("DAYTONA_API_KEY=from-file\n")
    assert env.env_key("DAYTONA_API_KEY") == "from-file"


def test_alias_order(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.delenv("DAYTONA_API_KEY", raising=False)
    monkeypatch.delenv("DAYTONA_API", raising=False)
    (tmp_path / ".env").write_text("DAYTONA_API=alias-file\n")
    assert env.env_key("DAYTONA_API_KEY", "DAYTONA_API") == "alias-file"


def test_parses_comments_export_and_quotes(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.delenv("A", raising=False)
    monkeypatch.delenv("B", raising=False)
    monkeypatch.delenv("C", raising=False)
    (tmp_path / ".env").write_text(
        "# comment\n"
        "export A=plain\n"
        "B=\"quoted\"\n"
        "C='also'\n"
    )
    assert env.env_key("A") == "plain"
    assert env.env_key("B") == "quoted"
    assert env.env_key("C") == "also"


def test_missing_returns_none(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.delenv("NO_SUCH_KEY", raising=False)
    assert env.env_key("NO_SUCH_KEY") is None


def test_finds_dotenv_at_repo_root_when_cwd_has_none(tmp_path, monkeypatch):
    monkeypatch.setattr(env, "_REPO_ROOT", tmp_path)
    cwd = tmp_path / "subdir"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.delenv("DAYTONA_API_KEY", raising=False)
    (tmp_path / ".env").write_text("DAYTONA_API_KEY=root-file\n")
    assert env.env_key("DAYTONA_API_KEY") == "root-file"
