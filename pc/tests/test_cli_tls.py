"""Tests for `openmuscle web` TLS cert auto-discovery (cli._resolve_tls).

So plain `openmuscle web` serves HTTPS for the Quest when a mkcert pair is
configured, instead of requiring --ssl-* on every launch.
"""

import os

from openmuscle.cli import _resolve_tls


def _touch(p):
    p.write_text("x")
    return str(p)


def _no_home(monkeypatch, tmp_path):
    # Neutralize ~/.openmuscle so only the case under test can match.
    monkeypatch.setattr(os.path, "expanduser", lambda _p: str(tmp_path / "nohome"))


def _clear_env(monkeypatch):
    monkeypatch.delenv("OPENMUSCLE_SSL_CERTFILE", raising=False)
    monkeypatch.delenv("OPENMUSCLE_SSL_KEYFILE", raising=False)


def test_explicit_flags_win():
    cert, key, src = _resolve_tls("/x/cert.pem", "/x/key.pem")
    assert (cert, key) == ("/x/cert.pem", "/x/key.pem")
    assert src == "explicit flags"


def test_env_vars(tmp_path, monkeypatch):
    cert = _touch(tmp_path / "c.pem")
    key = _touch(tmp_path / "k.pem")
    _no_home(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENMUSCLE_SSL_CERTFILE", cert)
    monkeypatch.setenv("OPENMUSCLE_SSL_KEYFILE", key)
    monkeypatch.chdir(tmp_path)
    cert_r, key_r, src = _resolve_tls(None, None)
    assert (cert_r, key_r) == (cert, key)
    assert "env" in src


def test_current_directory(tmp_path, monkeypatch):
    _touch(tmp_path / "vr-cert.pem")
    _touch(tmp_path / "vr-key.pem")
    _clear_env(monkeypatch)
    _no_home(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    cert, key, src = _resolve_tls(None, None)
    assert (cert, key) == ("vr-cert.pem", "vr-key.pem")
    assert src == "current directory"


def test_home_dir(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".openmuscle").mkdir(parents=True)
    _touch(home / ".openmuscle" / "vr-cert.pem")
    _touch(home / ".openmuscle" / "vr-key.pem")
    _clear_env(monkeypatch)
    monkeypatch.setattr(os.path, "expanduser", lambda _p: str(home))
    monkeypatch.chdir(tmp_path)              # cwd has no certs
    cert, key, src = _resolve_tls(None, None)
    assert os.path.isfile(cert) and os.path.isfile(key)
    assert src == "~/.openmuscle/"


def test_none_found_returns_http(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    _no_home(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)              # empty dir, no certs anywhere
    assert _resolve_tls(None, None) == (None, None, None)


def test_partial_pair_ignored(tmp_path, monkeypatch):
    # Only the cert present (no key) -> not a usable pair -> fall through to HTTP.
    _touch(tmp_path / "vr-cert.pem")
    _clear_env(monkeypatch)
    _no_home(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    assert _resolve_tls(None, None) == (None, None, None)
