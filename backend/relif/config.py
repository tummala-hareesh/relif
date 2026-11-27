"""relif.yaml: one simple config for the backend, the frontend and how each is deployed.

    backend:  home, bind (host:port), public_url, max_upload_mb
    frontend: url (the deployed page; also the only CORS origin for /health)
    deploy:   frontend (vercel | none), backend (docker | docker+tailscale | local)

Found via `relif --config PATH`, then $RELIF_CONFIG, then ./relif.yaml; no file means the defaults.
Precedence: command-line flag > environment ($RELIF_HOME, $RELIF_PUBLIC_URL) > relif.yaml > defaults.
Unknown keys and bad values are errors that name the key. Never shipped to the browser.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .store import RelifError

FILE_NAME = "relif.yaml"
FRONTENDS = ("vercel", "none")
BACKENDS = ("docker", "docker+tailscale", "local")
DEFAULTS = {
    "backend": {"home": "~/.relif", "bind": "127.0.0.1:8765", "public_url": "", "max_upload_mb": 20},
    "frontend": {"url": "https://relif.vercel.app"},
    "deploy": {"frontend": "vercel", "backend": "docker"},
}
ENV = {"backend.home": "RELIF_HOME", "backend.public_url": "RELIF_PUBLIC_URL"}


class ConfigError(RelifError):
    pass


@dataclass
class Config:
    path: Path | None
    home: Path
    host: str
    port: int
    public_url: str | None
    max_upload_mb: int
    frontend_url: str
    frontend_origin: str
    deploy_frontend: str
    deploy_backend: str
    values: dict = field(default_factory=dict)   # "section.key" -> effective raw value
    source: dict = field(default_factory=dict)   # "section.key" -> default | file | env

    @property
    def root(self) -> Path | None:
        """The repo folder (where relif.yaml lives): deploy needs it to find backend/ and frontend/."""
        return self.path.parent if self.path else None


def find(explicit=None) -> Path | None:
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            raise ConfigError(f"config file not found: {p}")
        return p.resolve()
    env = os.environ.get("RELIF_CONFIG", "").strip()
    if env:
        return find(env)
    p = Path.cwd() / FILE_NAME
    return p.resolve() if p.is_file() else None


def _read(path: Path) -> dict:
    try:
        import yaml
    except ImportError:  # pragma: no cover - pyyaml is a declared dependency
        raise ConfigError(f"reading {path} needs PyYAML: pip install pyyaml") from None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: not valid YAML: {exc}") from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: must be a mapping of sections (backend, frontend, deploy)")
    for section, body in data.items():
        if section not in DEFAULTS:
            raise ConfigError(f"{path}: unknown section {section!r} (expected: {', '.join(DEFAULTS)})")
        if body is None:
            continue
        if not isinstance(body, dict):
            raise ConfigError(f"{path}: {section} must be a mapping of keys")
        for key in body:
            if key not in DEFAULTS[section]:
                raise ConfigError(f"{path}: unknown key {section}.{key} "
                                  f"(expected: {', '.join(DEFAULTS[section])})")
    return data


def _bind(value) -> tuple[str, int]:
    text = str(value).strip()
    host, sep, port = text.rpartition(":")
    if text.startswith("["):
        host = host[1:-1] if host.endswith("]") else ""
    if not sep or not host or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ConfigError(f"backend.bind must be host:port, e.g. 127.0.0.1:8765 or [::]:8765 (got {value!r})")
    return host, int(port)


def parse_frontend_url(value) -> tuple[str, str]:
    url = str(value).strip()
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        parts, port = None, None
    if not parts or parts.scheme != "https" or not parts.hostname or parts.username or parts.password \
            or parts.query or parts.fragment:
        raise ConfigError(f"frontend.url must be an https:// address with no login, query or fragment (got {value!r})")
    origin = f"https://{parts.hostname.lower()}" + (f":{port}" if port and port != 443 else "")
    return url, origin


def load(path=None) -> Config:
    found = find(path)
    data = _read(found) if found else {}
    values, source = {}, {}
    for section, keys in DEFAULTS.items():
        for key, default in keys.items():
            name = f"{section}.{key}"
            body = data.get(section) or {}
            value, src = default, "default"
            if key in body and body[key] is not None:
                value, src = body[key], "file"
            env = ENV.get(name)
            if env and os.environ.get(env, "").strip():   # empty = unset (compose passes "" when .env has none)
                value, src = os.environ[env], "env"
            values[name], source[name] = value, src

    host, port = _bind(values["backend.bind"])
    mb = values["backend.max_upload_mb"]
    if isinstance(mb, bool) or not isinstance(mb, int) or mb < 1:
        raise ConfigError(f"backend.max_upload_mb must be a whole number of MB, at least 1 (got {mb!r})")
    public = str(values["backend.public_url"] or "").strip()
    if public:
        from .web import validate_public_url
        try:
            public = validate_public_url(public)
        except ValueError as exc:
            raise ConfigError(f"backend.public_url: {str(exc).replace('--public-url ', '')}") from None
    url, origin = parse_frontend_url(values["frontend.url"])
    for name, allowed in (("deploy.frontend", FRONTENDS), ("deploy.backend", BACKENDS)):
        if values[name] not in allowed:
            raise ConfigError(f"{name} must be one of {', '.join(allowed)} (got {values[name]!r})")
    return Config(path=found, home=Path(str(values["backend.home"])).expanduser(), host=host, port=port,
                  public_url=public or None, max_upload_mb=mb, frontend_url=url, frontend_origin=origin,
                  deploy_frontend=values["deploy.frontend"], deploy_backend=values["deploy.backend"],
                  values=values, source=source)
