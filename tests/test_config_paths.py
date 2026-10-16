"""A copied .env must not break a read-only host (Vercel): unwritable paths fall back to the temp dir."""
import os

from app import config


def test_unwritable_bookings_db_falls_back(monkeypatch, tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    monkeypatch.setenv("BOOKINGS_DB", str(ro / "bookings.db"))
    monkeypatch.setattr(config, "WRITABLE_DIR", tmp_path / "tmpdir")
    monkeypatch.setattr(config.os, "access", lambda p, mode: os.fspath(p) != os.fspath(ro))
    assert config._writable_path("BOOKINGS_DB", "bookings.db") == tmp_path / "tmpdir" / "bookings.db"


def test_writable_configured_path_is_kept(monkeypatch, tmp_path):
    monkeypatch.setenv("BOOKINGS_DB", str(tmp_path / "mine.db"))
    assert config._writable_path("BOOKINGS_DB", "bookings.db") == tmp_path / "mine.db"


def test_default_when_not_configured(monkeypatch, tmp_path):
    monkeypatch.delenv("INDEX_DIR", raising=False)
    monkeypatch.setattr(config, "WRITABLE_DIR", tmp_path)
    assert config._writable_path("INDEX_DIR", "index") == tmp_path / "index"
