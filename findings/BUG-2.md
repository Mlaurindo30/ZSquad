# BUG-2: WinError 5 (Access Denied) in Concurrent Executions
**Status:** Open
**Severity:** Low (Edge Case)
**Component:** Kilo Adapter

## Observation
During highly concurrent access on Windows, the atomic file swap via `os.replace(tmp_path, target_path)` fails with `[WinError 5] Acesso negado` if another thread or process is actively reading the file (`open(..., 'r')`).

## Expected Behavior
The function should either gracefully retry or fail softly without corrupting the state, or use a file lock that prevents concurrent read-writes from conflicting in Windows.

## Reproduction Steps
1. Write a base `kilo.jsonc` file.
2. Launch 10 parallel threads executing `configure_kilo_mcp`.
3. Observe tracebacks.

## Observed Result
```
Worker error: [WinError 5] Acesso negado: '...\\tmpaxe4br4k\\tmpljw7zmad' -> '...\\tmpaxe4br4k\\kilo.jsonc'
```
Windows denies the `os.replace` operation because another thread is holding a shared read lock during `json5.loads(content)`.
