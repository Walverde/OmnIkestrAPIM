import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, List
from uuid import uuid4

import yaml
from pydantic import ValidationError
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from .database import Base
from .manifest_migrations import ManifestMigrationError, migrate_manifest
from .models import (
    DEFAULT_WORKSPACE_ID,
    ProductEvent,
    ProductFile,
    ProductManifest,
    ProductRecord,
    ProductSummary,
    WorkspaceRecord,
)


IMAGE_EXTENSIONS = {"bmp", "gif", "heic", "jpeg", "jpg", "png", "tif", "tiff", "webp"}
DIRECT_MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | {
    "avi",
    "m4a",
    "m4v",
    "mkv",
    "mov",
    "mp3",
    "mp4",
    "mpeg",
    "mpg",
    "wav",
    "webm",
}
LEGACY_GROUP_MANIFEST_KEYS = {"schema_version", "id", "title"}
SKU_PATTERN = re.compile(
    r"(?:^|[^a-z0-9])(?:sku|ref|referencia|codigo)[-_ ]*([a-z0-9][a-z0-9._-]*)",
    re.IGNORECASE,
)


def _catalog_path(path: str | Path) -> str:
    return str(Path(str(path)).expanduser())


def _display_path(path: str | Path) -> str:
    return Path(str(path)).expanduser().as_posix()


def _storage_path(path: str | Path) -> str:
    return Path(str(path)).expanduser().as_posix()


def _ensure_workspace(db: Session, workspace_id: str = DEFAULT_WORKSPACE_ID) -> None:
    if not inspect(db.bind).has_table("workspaces"):
        Base.metadata.create_all(bind=db.bind)
    if db.get(WorkspaceRecord, workspace_id) is None:
        db.add(WorkspaceRecord(id=workspace_id, name=workspace_id))
        db.flush()


def _write_manifest_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            yaml.safe_dump(data, temporary_file, allow_unicode=True, sort_keys=False)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _manifest_metadata(
    files: List[ProductFile], product_path: str, preferred_id: str | None = None
) -> tuple[dict[str, Any] | None, str | None]:
    manifest_file = next(
        (
            file
            for file in files
            if file.name.lower() in {"product.yaml", "product.yml"}
            and Path(file.full_path).parent.resolve() == Path(product_path).resolve()
        ),
        None,
    )
    manifest_path = (
        Path(manifest_file.full_path)
        if manifest_file is not None
        else Path(product_path) / "product.yaml"
    )

    try:
        manifest_exists = manifest_path.exists()
        if manifest_exists:
            with manifest_path.open("r", encoding="utf-8") as manifest_stream:
                raw_metadata = yaml.safe_load(manifest_stream)
        else:
            raw_metadata = {}
        if raw_metadata is None:
            raw_metadata = {}
        if not isinstance(raw_metadata, dict):
            return None, "O manifesto precisa conter um objeto YAML."
    except (OSError, yaml.YAMLError) as error:
        return None, str(error)

    try:
        raw_metadata, migrated = migrate_manifest(raw_metadata)
    except ManifestMigrationError as error:
        try:
            return json.loads(json.dumps(raw_metadata, default=str)), str(error)
        except (TypeError, ValueError):
            return None, str(error)

    needs_write = not manifest_exists or migrated
    if "id" not in raw_metadata:
        raw_metadata["id"] = preferred_id or str(uuid4())
        needs_write = True
    if "title" not in raw_metadata:
        raw_metadata["title"] = raw_metadata.get("name") or Path(product_path).name
        needs_write = True

    try:
        manifest = ProductManifest.model_validate(raw_metadata)
        if needs_write:
            _write_manifest_atomic(manifest_path, raw_metadata)
    except (OSError, ValidationError) as error:
        if isinstance(error, ValidationError):
            details = "; ".join(
                f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
                for item in error.errors()
            )
            if any(item["loc"] == ("id",) for item in error.errors()):
                raw_metadata["id"] = str(preferred_id or uuid4())
                try:
                    _write_manifest_atomic(manifest_path, raw_metadata)
                except OSError:
                    pass
            try:
                return json.loads(json.dumps(raw_metadata, default=str)), details
            except (TypeError, ValueError):
                return None, f"{details}; o manifesto contém dados que não podem ser indexados."
        return raw_metadata, str(error)

    return manifest.model_dump(mode="json"), None


def _metadata_value(metadata: dict[str, Any] | None, *keys: str) -> str | None:
    if not metadata:
        return None
    normalized = {str(key).casefold(): value for key, value in metadata.items()}
    for key in keys:
        value = normalized.get(key.casefold())
        if isinstance(value, (str, int, float)) and str(value).strip():
            return str(value).strip()
    return None


def _image_priority(file: ProductFile) -> tuple[int, str]:
    name = file.name.casefold()
    extension = (file.extension or "").casefold()
    if any(marker in name for marker in ("cover", "primary", "principal", "capa")):
        priority = 0
    elif "removebg-preview" in name:
        priority = 1
    elif any(marker in name for marker in ("front", "frente")):
        priority = 2
    else:
        priority = 3
    extension_priority = {"png": 0, "webp": 1, "jpg": 2, "jpeg": 2, "heic": 3}
    return priority * 10 + extension_priority.get(extension, 4), file.relative_path.casefold()


def _analyze_product(
    name: str,
    path: str,
    files: List[ProductFile],
    preferred_id: str | None = None,
    folder_group: str = "",
    ambiguous_structure: bool = False,
) -> ProductSummary:
    manifest, manifest_error = _manifest_metadata(files, path, preferred_id)
    images = sorted(
        (
            file
            for file in files
            if file.extension and file.extension.casefold() in IMAGE_EXTENSIONS
        ),
        key=_image_priority,
    )
    sku = _metadata_value(manifest, "sku", "ref", "referencia", "codigo", "código")
    if sku is None:
        candidates = [(name, False), *((file.name, True) for file in files)]
        for candidate, is_filename in candidates:
            searchable_name = Path(candidate).stem if is_filename else candidate
            match = SKU_PATTERN.search(searchable_name)
            if match:
                sku = match.group(1).rstrip("._-")
                break

    category = _metadata_value(manifest, "category", "categoria")
    category_suggestion = folder_group.rsplit("/", 1)[-1] if folder_group else None

    product_path = _catalog_path(path)
    return ProductSummary(
        id=str(manifest["id"]) if manifest and manifest.get("id") else None,
        name=_metadata_value(manifest, "title", "name", "nome", "titulo", "título") or name,
        path=product_path,
        folder_group=folder_group,
        files=files,
        has_manifest=manifest is not None or manifest_error is not None,
        sku=sku,
        title=_metadata_value(manifest, "title", "name", "nome", "titulo", "título") or name,
        category=category,
        description=_metadata_value(manifest, "description", "descricao", "descrição"),
        price=manifest.get("price") if manifest and not manifest_error else None,
        cost=manifest.get("cost") if manifest and not manifest_error else None,
        condition=manifest.get("condition") if manifest and not manifest_error else None,
        status=manifest.get("status") if manifest and not manifest_error else None,
        marketplaces=(
            manifest.get("marketplaces", [])
            if manifest
            and not manifest_error
            and isinstance(manifest.get("marketplaces", []), list)
            else []
        ),
        images=[file.relative_path for file in images],
        primary_image=images[0].relative_path if images else None,
        category_suggestion=category_suggestion,
        category_mismatch=bool(
            category
            and category_suggestion
            and category.strip().casefold() != category_suggestion.strip().casefold()
        ),
        ambiguous_structure=ambiguous_structure,
        manifest=manifest,
        manifest_error=manifest_error,
    )


def _scan_product(
    root: Path,
    product_path: Path,
    preferred_id: str | None = None,
    folder_group: str | None = None,
    ambiguous_structure: bool = False,
) -> ProductSummary:
    if folder_group is None:
        try:
            parent_group = product_path.resolve().parent.relative_to(root.resolve())
            folder_group = "" if str(parent_group) == "." else parent_group.as_posix()
        except ValueError:
            folder_group = ""
    files: List[ProductFile] = []
    for file in sorted(product_path.rglob("*"), key=lambda item: str(item).lower()):
        if file.is_dir():
            continue
        files.append(
            ProductFile(
                name=file.name,
                full_path=_display_path(file),
                relative_path=_display_path(file.relative_to(root)),
                extension=file.suffix.lower().lstrip(".") or None,
            )
        )

    product = _analyze_product(
        product_path.name,
        str(product_path),
        files,
        preferred_id=preferred_id,
        folder_group=folder_group,
        ambiguous_structure=ambiguous_structure,
    )
    has_root_manifest = any(
        file.name.lower() in {"product.yaml", "product.yml"}
        and Path(file.full_path).parent.resolve() == product_path.resolve()
        for file in files
    )
    if product.has_manifest and not has_root_manifest:
        manifest_path = product_path / "product.yaml"
        files.append(
            ProductFile(
                name=manifest_path.name,
                full_path=_display_path(manifest_path),
                relative_path=_display_path(manifest_path.relative_to(root)),
                extension="yaml",
            )
        )
        product.files = files
    return product


def _scan_products(
    root: Path, preferred_ids: dict[str, str] | None = None
) -> List[ProductSummary]:
    if not root.exists():
        return []
    preferred_ids = preferred_ids or {}
    products: List[ProductSummary] = []

    def walk(group_path: Path, group_parts: list[str]) -> None:
        for folder in sorted(group_path.iterdir(), key=lambda path: path.name.casefold()):
            if not folder.is_dir():
                continue

            direct_files = [entry for entry in folder.iterdir() if entry.is_file()]
            child_folders = [entry for entry in folder.iterdir() if entry.is_dir()]
            has_manifest = any(
                entry.name.casefold() in {"product.yaml", "product.yml"}
                for entry in direct_files
            )
            manifest_path = next(
                (
                    entry
                    for entry in direct_files
                    if entry.name.casefold() in {"product.yaml", "product.yml"}
                ),
                None,
            )
            legacy_group_manifest = bool(
                manifest_path
                and child_folders
                and _is_legacy_group_manifest(manifest_path)
            )
            has_direct_media = any(
                entry.suffix.lower().lstrip(".") in DIRECT_MEDIA_EXTENSIONS
                for entry in direct_files
            )

            if (has_manifest and not (legacy_group_manifest and not has_direct_media)) or has_direct_media:
                group = "/".join(group_parts)
                products.append(
                    _scan_product(
                        root,
                        folder,
                        preferred_ids.get(str(folder)),
                        folder_group=group,
                        ambiguous_structure=(
                            (not has_manifest or legacy_group_manifest)
                            and has_direct_media
                            and bool(child_folders)
                        ),
                    )
                )
                continue

            walk(folder, [*group_parts, folder.name])

    walk(root, [])
    return products


def _is_legacy_group_manifest(manifest_path: Path) -> bool:
    try:
        metadata = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return False
    return isinstance(metadata, dict) and set(metadata) == LEGACY_GROUP_MANIFEST_KEYS


def _upsert_product_record(
    db: Session, product: ProductSummary, workspace_id: str
) -> ProductRecord:
    storage_path = _storage_path(product.path)
    record = db.scalar(
        select(ProductRecord).where(
            ProductRecord.workspace_id == workspace_id,
            ProductRecord.product_uuid == product.id,
        )
    )
    if record is None:
        record = db.scalar(
            select(ProductRecord).where(
                ProductRecord.workspace_id == workspace_id,
                ProductRecord.path == storage_path,
            )
        )
    if record is None:
        record = ProductRecord(
            workspace_id=workspace_id,
            product_uuid=product.id or str(uuid4()),
            name=product.name,
            title=product.title or product.name,
            path=storage_path,
        )
        db.add(record)

    record.product_uuid = product.id or record.product_uuid
    record.name = product.name
    record.title = product.title or product.name
    record.folder_group = product.folder_group
    record.path = storage_path
    record.sku = product.sku
    record.category = product.category
    record.description = product.description
    record.price = product.price
    record.cost = product.cost
    record.condition = product.condition
    record.status = product.status or "pending"
    record.marketplaces = product.marketplaces
    record.manifest = product.manifest or {}
    record.manifest_error = product.manifest_error
    record.ambiguous_structure = product.ambiguous_structure
    record.files = [file.model_dump() for file in product.files]
    record.has_manifest = product.has_manifest
    return record


def _root_for_path(path: str | Path | None, roots: tuple[Path, ...]) -> Path | None:
    if path is None:
        return None
    candidate = Path(path).expanduser().resolve()
    for root in roots:
        try:
            candidate.relative_to(root)
            return root
        except ValueError:
            continue
    return None


def _sync_product_catalog_for_roots(
    db: Session,
    roots: tuple[Path, ...] | list[Path] | None,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> List[ProductSummary]:
    normalized_roots = tuple(Path(root).expanduser().resolve() for root in (roots or ()))
    _ensure_workspace(db, workspace_id)
    existing_records = db.scalars(
        select(ProductRecord).where(ProductRecord.workspace_id == workspace_id)
    ).all()
    preferred_ids: dict[str, str] = {
        record.path: record.product_uuid for record in existing_records
    }
    products: List[ProductSummary] = []
    current_paths: set[str] = set()
    current_uuids: set[str] = set()

    for root in normalized_roots:
        discovered = _scan_products(root, preferred_ids)
        products.extend(discovered)
        current_paths.update(_storage_path(product.path) for product in discovered)
        current_uuids.update(product.id for product in discovered if product.id)

    for product in products:
        _upsert_product_record(db, product, workspace_id)

    for record in existing_records:
        if record.path not in current_paths and record.product_uuid not in current_uuids:
            db.delete(record)

    db.commit()
    return products


def _sync_product_catalog(
    db: Session, root: Path, workspace_id: str = DEFAULT_WORKSPACE_ID
) -> List[ProductSummary]:
    return _sync_product_catalog_for_roots(
        db, (Path(root).expanduser().resolve(),), workspace_id
    )


def _is_product_directory(path: Path) -> bool:
    try:
        direct_entries = list(path.iterdir())
    except OSError:
        return False
    has_manifest = any(
        entry.is_file() and entry.name.casefold() in {"product.yaml", "product.yml"}
        for entry in direct_entries
    )
    has_direct_media = any(
        entry.is_file() and entry.suffix.lower().lstrip(".") in DIRECT_MEDIA_EXTENSIONS
        for entry in direct_entries
    )
    manifest_path = next(
        (
            entry
            for entry in direct_entries
            if entry.is_file() and entry.name.casefold() in {"product.yaml", "product.yml"}
        ),
        None,
    )
    has_children = any(entry.is_dir() for entry in direct_entries)
    if (
        has_manifest
        and has_children
        and not has_direct_media
        and manifest_path is not None
        and _is_legacy_group_manifest(manifest_path)
    ):
        return False
    return has_manifest or has_direct_media


def _product_directory_for_path(
    root: Path,
    path: str,
    db: Session,
    workspace_id: str,
    is_directory: bool = False,
) -> Path | None:
    try:
        target = Path(path).expanduser().resolve()
        relative_path = target.relative_to(root.resolve())
    except ValueError:
        return None
    if not relative_path.parts:
        return None

    candidate = target if is_directory else target.parent
    while candidate != root.resolve() and root.resolve() in candidate.parents:
        indexed = db.scalar(
            select(ProductRecord.path).where(
                ProductRecord.workspace_id == workspace_id,
                ProductRecord.path == _storage_path(candidate),
            )
        )
        if indexed or (candidate.is_dir() and _is_product_directory(candidate)):
            return candidate
        candidate = candidate.parent
    return None


def _sync_product_for_event(
    db: Session, root: Path, event: ProductEvent
) -> list[ProductSummary]:
    _ensure_workspace(db, event.workspace_id)
    source_dir = _product_directory_for_path(
        root, event.path, db, event.workspace_id, event.is_directory
    )
    destination_dir = (
        _product_directory_for_path(
            root, event.destination_path, db, event.workspace_id, event.is_directory
        )
        if event.destination_path
        else source_dir
    )
    affected_dirs = list(dict.fromkeys(path for path in (source_dir, destination_dir) if path))
    if not affected_dirs:
        return []

    source_record = None
    if (
        event.event == "moved"
        and event.is_directory
        and source_dir is not None
        and source_dir != destination_dir
    ):
        source_record = db.scalar(
            select(ProductRecord).where(
                ProductRecord.workspace_id == event.workspace_id,
                ProductRecord.path == _storage_path(source_dir),
            )
        )

    indexed: list[ProductSummary] = []
    for product_dir in affected_dirs:
        if product_dir.is_dir():
            preferred_id = (
                source_record.product_uuid
                if source_record is not None and product_dir == destination_dir
                else None
            )
            product = _scan_product(root.resolve(), product_dir, preferred_id)
            _upsert_product_record(db, product, event.workspace_id)
            indexed.append(product)
        else:
            record = db.scalar(
                select(ProductRecord).where(
                    ProductRecord.workspace_id == event.workspace_id,
                    ProductRecord.path == _storage_path(product_dir),
                )
            )
            if record is not None:
                db.delete(record)
    db.commit()
    return indexed


def _serialize_product(record: ProductRecord) -> dict[str, object]:
    files = [ProductFile.model_validate(file) for file in record.files]
    images = sorted(
        (
            file
            for file in files
            if file.extension and file.extension.casefold() in IMAGE_EXTENSIONS
        ),
        key=_image_priority,
    )
    return ProductSummary(
        id=record.product_uuid,
        name=record.name,
        title=record.title,
        path=record.path,
        folder_group=record.folder_group or "",
        files=files,
        has_manifest=record.has_manifest,
        sku=record.sku,
        category=record.category,
        description=record.description,
        price=record.price,
        cost=record.cost,
        condition=record.condition,
        status=record.status,
        marketplaces=record.marketplaces or [],
        images=[file.relative_path for file in images],
        primary_image=images[0].relative_path if images else None,
        category_suggestion=record.folder_group.rsplit("/", 1)[-1]
        if record.folder_group
        else None,
        category_mismatch=bool(
            record.category
            and record.folder_group
            and record.category.strip().casefold()
            != record.folder_group.rsplit("/", 1)[-1].strip().casefold()
        ),
        ambiguous_structure=record.ambiguous_structure,
        manifest=record.manifest,
        manifest_error=record.manifest_error,
    ).model_dump()
