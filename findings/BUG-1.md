# BUG-1: Comment Destruction on Idempotent Update
**Status:** Open
**Severity:** Medium
**Component:** Kilo Adapter

## Observation
When updating an existing `"agent-squad"` entry in `kilo.jsonc` (e.g. environment variable change), the script falls back to standard `json.dumps(parsed, indent=2)`. This action completely strips all comments (both `//` and `/* */`) from the configuration file.

## Expected Behavior
Comments and structural formatting should be preserved during an update, similar to the initial injection.

## Reproduction Steps
1. Create `kilo.jsonc` with the following content:
```jsonc
{
    "mcp": {
        // my custom comment
        "agent-squad": { "type": "old" }
    }
}
```
2. Run `configure_kilo_mcp(target_path, "/new/path")`.
3. Read the file.

## Observed Result
```json
{
  "mcp": {
    "agent-squad": {
      "type": "local",
      "command": [
        "python",
        "-m",
        "integrations.mcp_runner"
      ],
      "enabled": true,
      "environment": {
        "PYTHONPATH": "/new/path"
      }
    }
  }
}
```
The `// my custom comment` is permanently deleted.
