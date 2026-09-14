# BUG-ZCODE-WINERROR5: Concurrency failure during os.replace on Windows

## Description
When multiple threads attempt to run `configure_zcode` concurrently on Windows, `os.replace` raises `[WinError 5] Acesso negado` (Access denied). While `os.replace` is atomic and prevents file corruption, it fails abruptly under contention on Windows environments, which could prevent the MCP configuration from being successfully applied if run simultaneously by multiple processes.

## Steps to Reproduce
1. Execute multiple concurrent calls to `configure_zcode(zcode_config_path, squad_runtime_path)` in a Windows environment.
2. Observe `PermissionError: [WinError 5] Acesso negado: '<temp_path>' -> '<target_path>'`.

## Expected Behavior
The script should either handle the contention gracefully (e.g., using a file lock mechanism like `filelock`, or retrying with backoff) or ignore the error if the file already contains the correct configuration.

## Observed Behavior
Exceptions are raised during `os.replace`, causing the `configure_zcode` function to fail for concurrent callers. The JSON file itself does not get corrupted, fulfilling the data integrity requirement, but the failure is ungraceful.

## Impact
Low to Medium. The configuration file remains intact, but an unhandled exception may interrupt automation workflows on Windows.

## Recommendations
Implement a retry mechanism with a short delay around `os.replace` specifically for Windows environments, or use a cross-platform file locking library to synchronize access to `.zcode/config.json`.
