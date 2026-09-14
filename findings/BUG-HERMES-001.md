# BUG-HERMES-001: YAML Comment Preservation Failure

## Metadata
**Component:** `integrations/hermes_adapter.py`
**Severity:** Low (Usability/DX)
**Reporter:** James Bach & Michael Bolton (`12-qa-engineer`)

## Description
When `install_hermes_mcp_server` updates an existing `config.yaml`, it uses `yaml.dump()` from the `PyYAML` library. PyYAML natively strips all comments and formatting when parsing and re-serializing the file.

## Reproduction Steps
1. Create a `config.yaml` with comments:
   ```yaml
   # This is a user comment
   baseline: true
   ```
2. Run `install_hermes_mcp_server(config_path, '/mock/runtime')`.
3. Inspect the updated `config.yaml`.

## Expected Behavior
The file should be updated with the new MCP server configuration while retaining the `# This is a user comment`.

## Observed Behavior
The comments are completely removed from the file.

## Technical Recommendation
Replace `PyYAML` (`import yaml`) with `ruamel.yaml` in `hermes_adapter.py`, which is designed for round-trip YAML parsing and perfectly preserves comments.
