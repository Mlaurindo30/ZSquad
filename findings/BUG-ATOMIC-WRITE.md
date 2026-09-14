# BUG: Non-Atomic Write in Claude Config Adapter

## Description
The `merge_claude_config` function writes directly to the user's `claude_desktop_config.json` file. This operation is not atomic. In the event of a crash or interruption during writing, the JSON file will be corrupted (truncated).

## Location
- `integrations/claude_adapter.py`
- Function: `merge_claude_config`

## Steps to Reproduce (Theoretical)
1. Initiate the agent-squad Claude setup.
2. Force-kill the process exactly during the execution of `json.dump(data, f)`.
3. Check `%APPDATA%/Claude/claude_desktop_config.json`. The file will be truncated and invalid JSON.

## Expected Behavior
The writing operation should use an atomic write approach (e.g., writing to a `.tmp` file and executing `os.replace()`) to ensure the configuration file is never left in a corrupted state.

## Severity
**High**. Corrupting the Claude Desktop configuration file could break the user's existing setup.
