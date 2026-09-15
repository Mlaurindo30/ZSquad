# Governed work-item reclassification

Research date: 2026-09-15.

## Decision

The runtime uses a dedicated `reclassify-work-item` command instead of extending
`init-work-item --force`. Creation and migration have different safety and audit
semantics; keeping them separate makes dry-run the default and prevents an
existing directory from being treated as a creation target.

The first allowlisted transition is `evolution -> epic`. It exists to preserve
an established initiative and enable the canonical Epic -> Feature -> Story ->
Task hierarchy without violating Query Before Create.

## Safety contract

- Resolve one exact work-item ID inside the canonical project namespace.
- Reject all transitions not explicitly allowlisted.
- Require the current type to match `--from-type`.
- Reject a future Epic with `parent_id`.
- Reject existing non-Feature children that reference the target as parent.
- Validate the resulting status against `work-item.schema.json` before writing.
- Default to dry-run; require `--apply` for the atomic status update.
- Preserve the directory and every existing artifact.
- Be idempotent when the target already has the requested type.
- Record before/after SHA-256 digests in the target evidence directory.

## Recovery

If independent review rejects the migration, restore the prior `status.yaml`
from version control or another verified copy whose digest matches
`status_sha256_before` in the audit evidence. Do not delete or move the work-item
directory. Re-run `validate-work-item` and the hierarchy tests after recovery.

## Official references

- Python argparse: https://docs.python.org/3/library/argparse.html
- Python pathlib: https://docs.python.org/3/library/pathlib.html
- Azure Boards backlog hierarchy: https://learn.microsoft.com/en-us/azure/devops/boards/backlogs/backlogs-overview

