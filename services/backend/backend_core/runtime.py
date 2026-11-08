import os
from pathlib import Path
from typing import Sequence


def _split_env_paths(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    return [part.strip() for part in raw_value.replace(";", ",").split(",") if part.strip()]


def _resolve_product_roots() -> tuple[Path, ...]:
    configured_roots = _split_env_paths(os.getenv("PRODUCT_ROOTS"))
    if not configured_roots:
        configured_roots = _split_env_paths(os.getenv("PRODUCTS_ROOT"))
    if not configured_roots:
        default_root = Path.cwd() / "data" / "products"
        default_root.mkdir(parents=True, exist_ok=True)
        configured_roots = [str(default_root)]

    resolved_roots: list[Path] = []
    seen: set[str] = set()
    for root in configured_roots:
        normalized = Path(root).expanduser().resolve()
        key = normalized.as_posix()
        if key not in seen:
            seen.add(key)
            resolved_roots.append(normalized)
    return tuple(resolved_roots)


PRODUCT_ROOTS = _resolve_product_roots()
PRODUCTS_ROOT = PRODUCT_ROOTS[0]
ALLOWED_PRODUCT_ROOTS = PRODUCT_ROOTS
PICKER_TOKEN = os.getenv("PICKER_TOKEN", "")
MAX_PRODUCT_EVENTS = 10000
PRODUCT_EVENT_RETENTION_DAYS = 90
DASHBOARD_PAGE = Path(__file__).parent.parent / "static" / "dashboard.html"
SETTINGS_PAGE = Path(__file__).parent.parent / "static" / "settings.html"
SETUP_PAGE = Path(__file__).parent.parent / "static" / "onboarding.html"


def apply_product_roots(roots: Sequence[str | Path]) -> tuple[Path, ...]:
    normalized_roots: list[Path] = []
    seen: set[str] = set()
    for raw_root in roots:
        if not str(raw_root).strip():
            continue
        root = Path(str(raw_root)).expanduser().resolve()
        key = root.as_posix()
        if key not in seen:
            seen.add(key)
            normalized_roots.append(root)
    if not normalized_roots:
        normalized_roots = list(_resolve_product_roots())

    global PRODUCT_ROOTS, PRODUCTS_ROOT
    PRODUCT_ROOTS = tuple(normalized_roots)
    PRODUCTS_ROOT = PRODUCT_ROOTS[0]
    return PRODUCT_ROOTS


def resolve_allowed_product_root(raw_root: str | Path) -> Path:
    candidate = Path(raw_root).expanduser().resolve(strict=True)
    if not candidate.is_dir():
        raise ValueError("A pasta selecionada não é um diretório.")

    for allowed_root in ALLOWED_PRODUCT_ROOTS:
        try:
            resolved_allowed_root = allowed_root.expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if candidate == resolved_allowed_root or resolved_allowed_root in candidate.parents:
            return candidate

    raise ValueError("A pasta precisa estar dentro de um volume de produtos montado.")
