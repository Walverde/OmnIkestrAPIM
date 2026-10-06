# ADR 0002: Workspace-Scoped Catalog

- Status: Accepted
- Date: 2026-10-06

## Context

Products and their event history need a stable ownership boundary before
multiple product roots or users are introduced.

## Decision

Products and product events reference a workspace. The initial installation
creates the `default` workspace and assigns all migrated records and incoming
watcher events to it. Product UUIDs are unique within a workspace; paths are
also unique within a workspace. Event reads are workspace-scoped, paginated,
and subject to count and age retention limits.

This stage still configures one filesystem root for the application. Multiple
independent roots and workspace administration are deferred; adding the schema
boundary now avoids a later destructive migration.

## Consequences

- Existing rows migrate into `default` without deletion.
- Rename operations update a product's path while preserving its manifest UUID
  and database row.
- Future workspace-specific roots require an explicit configuration/API change;
  a workspace ID alone does not select another filesystem root today.