from collections.abc import Callable
from typing import Any


CURRENT_SCHEMA_VERSION = 1


class ManifestMigrationError(ValueError):
    pass


def _migrate_unversioned_to_v1(manifest: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(manifest)
    migrated["schema_version"] = 1
    return migrated


MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {
    0: _migrate_unversioned_to_v1,
}


def migrate_manifest(
    manifest: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    migrated = dict(manifest)
    raw_version = migrated.get("schema_version", 0)
    if type(raw_version) is not int or raw_version < 0:
        raise ManifestMigrationError(
            "schema_version precisa ser um inteiro não negativo."
        )
    if raw_version > CURRENT_SCHEMA_VERSION:
        raise ManifestMigrationError(
            f"Versão de schema {raw_version} não é suportada; "
            f"versão máxima suportada: {CURRENT_SCHEMA_VERSION}."
        )

    version = raw_version
    changed = "schema_version" not in migrated
    while version < CURRENT_SCHEMA_VERSION:
        migration = MIGRATIONS.get(version)
        if migration is None:
            raise ManifestMigrationError(
                f"Não existe migração do schema {version} para {version + 1}."
            )
        migrated = migration(migrated)
        next_version = migrated.get("schema_version")
        if type(next_version) is not int or next_version != version + 1:
            raise ManifestMigrationError(
                f"A migração do schema {version} não definiu a versão {version + 1}."
            )
        version = next_version
        changed = True

    return migrated, changed
