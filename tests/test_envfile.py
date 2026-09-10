"""Catalogue .env groupé + découverte auto."""

from pathlib import Path

from tutosvideo.envfile import (
    all_env_fields,
    infer_group,
    infer_secret,
    iter_env_groups,
    write_env_map,
)


def test_infer_groups():
    assert infer_group("MISTRAL_API_KEY") == "ia"
    assert infer_group("TUTO_DOLIBARR_LOGIN") == "dolibarr"
    assert infer_group("TUTO_EMAIL") == "compte"
    assert infer_group("IMAP_HOST") == "imap"
    assert infer_group("CUSTOM_FOO") == "autre"


def test_infer_secret():
    assert infer_secret("MISTRAL_API_KEY")
    assert infer_secret("TUTO_PASSWORD")
    assert not infer_secret("TUTO_EMAIL")


def test_discover_extra_key(tmp_path: Path, monkeypatch):
    from tutosvideo import envfile

    monkeypatch.setattr(envfile, "ROOT", tmp_path)
    example = tmp_path / ".env.example"
    example.write_text(
        "MISTRAL_API_KEY=\nWP_API_TOKEN=abc\n",
        encoding="utf-8",
    )
    fields = {f.key: f for f in all_env_fields(tmp_path / ".env")}
    assert "WP_API_TOKEN" in fields
    assert fields["WP_API_TOKEN"].group == "autre"
    assert fields["WP_API_TOKEN"].secret is True


def test_groups_order_and_write(tmp_path: Path, monkeypatch):
    from tutosvideo import envfile

    monkeypatch.setattr(envfile, "ROOT", tmp_path)
    (tmp_path / ".env.example").write_text("MISTRAL_CHAT_MODEL=x\n", encoding="utf-8")
    groups = iter_env_groups(tmp_path / ".env")
    titles = [t for _, t, _ in groups]
    assert titles[0].startswith("IA")
    assert "Dolibarr" in titles
    path = write_env_map({"MISTRAL_CHAT_MODEL": "mistral-small-latest"}, tmp_path / ".env")
    text = path.read_text(encoding="utf-8")
    assert "# --- IA (Mistral) ---" in text
    assert "# --- Dolibarr ---" in text
