# ADR 0001: Product YAML Is Canonical

- Status: Accepted
- Date: 2026-10-06

## Context

Products are managed from folders and files. Keeping independent writable copies
of product metadata in YAML and PostgreSQL creates ambiguous conflict handling.

## Decision

`product.yaml` is the authoritative product record. PostgreSQL is a rebuildable
query index. A filesystem scan validates the manifest and updates indexed
columns; it never writes indexed values back into an existing manifest. Product
manifest creation and future UI edits use a temporary file in the same directory
followed by an atomic replace. Unknown YAML keys are preserved during edits.

Schema v1 requires `title`; `schema_version` defaults to `1`, and `id` is a
UUID generated once and persisted. The manifest also supports `sku`, `category`,
`price`, `cost`, `condition`, `status`, `marketplaces`, and `description`.
Invalid fields are reported on that product and do not prevent other folders
from being indexed.

## Consequences

- The database can be rebuilt from the product root.
- Reads and search filters use indexed columns and do not parse YAML on each GET.
- Filesystem edits win on the next watcher update or full reconciliation.
- UI editing must write atomically to YAML; direct database-only edits are not
  authoritative.