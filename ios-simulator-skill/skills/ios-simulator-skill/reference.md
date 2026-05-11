# iOS Simulator Skill — Script Reference

Full flag reference for every script. Load this file only when you need details beyond what SKILL.md or `--help` provides.

All scripts are in `${CLAUDE_SKILL_DIR}/scripts/`. All support `--help` and `--json`.

---

## Build & Development

### `build_and_test.py`
Wraps `xcodebuild` with progressive disclosure. A build returns one summary line plus an `xcresult-ID`; drill into details on demand.

| Flag | Purpose |
|------|---------|
| `--project <path>` | `.xcodeproj` path |
| `--workspace <path>` | `.xcworkspace` path (overrides `--project`) |
| `--scheme <name>` | Build scheme |
| `--destination <spec>` | `xcodebuild` destination string |
| `--clean` | Clean before build |
| `--test` | Run tests instead of build-only |
| `--get-errors <xcresult-id>` | Parse errors from a completed xcresult bundle |
| `--get-warnings <xcresult-id>` | Parse warnings |
| `--get-log <xcresult-id>` | Full build log (large — use sparingly) |
| `--verbose` / `--json` | Output controls |

### `log_monitor.py`
Stream or capture Console/`os_log` output with filtering and dedup.

| Flag | Purpose |
|------|---------|
| `--app <bundle-id>` | Filter to one app |
| `--severity error\|warning\|info\|debug` | Minimum severity |
| `--follow` | Stream until Ctrl-C |
| `--duration <seconds>` | Capture for N seconds then exit |
| `--output <path>` | Write to file |

### `spm_manager.py`
Inspect or modify a Swift Package (standalone `Package.swift` only in v1).

| Flag | Purpose |
|------|---------|
| `--package-path <dir>` | Directory containing `Package.swift` (default: cwd) |
| (default) | `swift package show-dependencies` — one-line summary |
| `--verbose` | Full indented dependency tree |
| `--describe` | `swift package describe` (targets + products) |
| `--resolve` | State-mutating: write `Package.resolved` |
| `--update` | State-mutating: update dependencies (`--package <name>` scopes) |

---

## UI Navigation

### `screen_mapper.py`
Lists interactive elements on the current screen from the a11y tree. ~10 tokens default output.

| Flag | Purpose |
|------|---------|
| `--verbose` | Full tree with frames |
| `--hints` | Include element hints |

### `navigator.py`
Find and interact with elements semantically. Preferred over pixel taps.

| Flag | Purpose |
|------|---------|
| `--find-text <str>` | Fuzzy match on label/value |
| `--find-type <Button\|TextField\|...>` | Match by element type |
| `--find-id <a11y-id>` | Exact accessibility identifier |
| `--index <n>` | Disambiguate when multiple match |
| `--tap` | Tap the matched element |
| `--enter-text <str>` | Type into a text field |
| `--list` | List all tappable elements |
| `--tap-at X,Y` | Coordinate fallback (last resort) |
| `--scroll-into-view` | Scroll until the matched element is visible before tapping (use with `--tap`) |
| `--scroll-attempts <n>` | Max swipes for `--scroll-into-view` (default 5) |

### `gesture.py`
Swipes, scrolls, pinches, long press, pull-to-refresh.

| Flag | Purpose |
|------|---------|
| `--swipe up\|down\|left\|right` | Directional swipe |
| `--scroll up\|down` | Multi-swipe scroll |
| `--pinch in\|out` | Zoom gesture |
| `--long-press X,Y` | Hold gesture |
| `--refresh` | Pull-to-refresh on scrollview |

### `keyboard.py`
Text input and hardware buttons.

| Flag | Purpose |
|------|---------|
| `--type <str>` | Type text |
| `--slow` | Slower typing for apps with input lag |
| `--key return\|delete\|tab\|space\|up\|down\|left\|right` | Special keys |
| `--button home\|lock\|volume-up\|volume-down\|screenshot` | Hardware buttons |
| `--clear` | Clear focused text field |
| `--dismiss` | Dismiss keyboard |

### `app_launcher.py`
App lifecycle.

| Flag | Purpose |
|------|---------|
| `--launch <bundle-id>` | Launch |
| `--terminate <bundle-id>` | Kill |
| `--install <path.app>` | Install bundle |
| `--uninstall <bundle-id>` | Uninstall |
| `--open-url <url>` | Deep link |
| `--list` | List installed apps |
| `--state <bundle-id>` | Running state |

---

## Testing & Analysis

### `wait_for.py`
Block until a simulator condition becomes true. Replaces in-turn polling loops. Emits structured error envelopes (see "Design notes" below).

| Flag | Purpose |
|------|---------|
| `--element <query>` | Wait for an a11y element matching text/id (substring, case-insensitive) |
| `--element-gone <query>` | Wait until a previously-visible element disappears |
| `--app-state foreground\|not_running` | Wait for app state. Requires `--bundle-id` |
| `--log-match <regex>` | Wait for a log line matching the regex. Requires `--bundle-id` |
| `--timeout <seconds>` | Default 30 |
| `--interval <seconds>` | Default 0.5s (1.0s for `--log-match`) |

Exit codes: 0 matched, 1 timeout (`TIMEOUT` envelope), 2 args/env error.

### `debug_failing_test.py`
Composer: runs a test, and on failure captures xcresult errors + app logs + UI hierarchy + screenshot into one timestamped bundle.

| Flag | Purpose |
|------|---------|
| `--project` / `--workspace` / `--scheme` | Same as `build_and_test.py` |
| `--test <Target/Class/method>` | Test identifier, passed to `xcodebuild -only-testing` |
| `--bundle-id <id>` | App under test (for log + hierarchy capture) |
| `--simulator <name>` | Simulator name, e.g. `"iPhone 16 Pro"` |
| `--output <dir>` | Bundle location (default: `./debug-<timestamp>/`) |
| `--log-lines <n>` | Log tail length (default 200) |
| `--retries <n>` | Retry failed test N times before giving up (default 0) |

### `accessibility_audit.py`
WCAG-style audit on the current screen.

| Flag | Purpose |
|------|---------|
| `--verbose` | Include info-level findings |
| `--output <path>` | Write markdown report |

### `visual_diff.py`
Pixel-by-pixel screenshot comparison.

| Flag | Purpose |
|------|---------|
| `--threshold <0-1>` | Pass/fail threshold |
| `--output <path>` | Diff image location |
| `--details` | Per-region breakdown |

### `test_recorder.py`
Automates step-by-step test documentation — screenshot + a11y tree per step, markdown report out.

| Flag | Purpose |
|------|---------|
| `--test-name <str>` | Report title |
| `--output <dir>` | Output folder |

### `app_state_capture.py`
Debugging snapshot bundle: screenshot, UI hierarchy, logs, device info, markdown summary.

| Flag | Purpose |
|------|---------|
| `--app-bundle-id <id>` | App under focus |
| `--output <dir>` | Bundle location |
| `--log-lines <n>` | Log tail length (default 200) |

### `model_inspector.py`
Parse Core Data `.xcdatamodeld` and SwiftData `@Model` classes from project sources.

| Flag | Purpose |
|------|---------|
| `--project-path <dir>` | Project root |
| `--core-data-only` / `--swiftdata-only` | Scope |
| `--show-versions` | List all model versions |
| `--raw <ModelName>` | Dump raw source for one model |

### `crash_log.py`
Discover and summarise `.ips` / `.crash` reports for simulator apps, optionally symbolicated.

| Flag | Purpose |
|------|---------|
| `--app <bundle-id-or-proc>` | Filter to one app |
| `--last <Nm\|Nh\|Nd>` | Window relative to now (e.g. `1h`, `7d`) |
| `--limit N` | Cap reports summarised (default 5, most recent first) |
| `--symbolicate --dsym <path>` | Resolve frames via `atos` (`--binary` disambiguates multi-arch dSYMs) |
| `--list` | Only list discovered files, no parse |
| `--verbose` | Dump full crashed-thread frame list |

### `sim_health_check.sh`
Verifies macOS, Xcode, `simctl`, `idb`, Python. Run first if anything looks off.

---

## Environment & Permissions

### `clipboard.py`
| Flag | Purpose |
|------|---------|
| `--copy <text>` | Set simulator clipboard |
| `--test-name <str>` | Audit tag |
| `--expected <str>` | Assert current clipboard value |

### `status_bar.py`
Override status bar for screenshots and demos.

| Flag | Purpose |
|------|---------|
| `--preset clean\|testing\|low-battery\|airplane` | Common configurations |
| `--time <HH:MM>` | Custom time |
| `--battery-level <0-100>` | Battery percent |
| `--data-network wifi\|4g\|5g\|lte` | Network type |
| `--clear` | Revert overrides |

### `push_notification.py`
| Flag | Purpose |
|------|---------|
| `--bundle-id <id>` | Target app |
| `--title <str>` / `--body <str>` / `--badge <n>` | Simple notification |
| `--payload <path.json>` | Custom APNS payload |

### `privacy_manager.py`
Grant, revoke, or reset TCC permissions (13 services: camera, microphone, photos, contacts, calendar, location, etc.).

| Flag | Purpose |
|------|---------|
| `--bundle-id <id>` | Target app |
| `--grant <services>` | Comma-separated |
| `--revoke <services>` | Comma-separated |
| `--reset` | Reset all |
| `--list` | Show supported services |

### `keychain_biometric.py`
Install certs in the simulator keychain and trigger Face ID / Touch ID events from the host.

| Flag | Purpose |
|------|---------|
| `--add-root-cert <path>` | Install as a trusted root cert |
| `--add-cert <path>` | Install as a regular (intermediate/leaf) cert |
| `--reset-keychain` | Wipe the simulator's keychain |
| `--biometric <action>` | Post `face-match` / `face-nomatch` / `touch-match` / `touch-nomatch` / `enroll-toggle` |
| `--list-biometric` | Show supported actions and their notifyutil keys |

---

## Device Lifecycle

All accept `--udid` or `--name`. Auto-detect the booted sim when both omitted.

### `simctl_boot.py`
| Flag | Purpose |
|------|---------|
| `--wait-ready` | Block until Springboard responds |
| `--timeout <s>` | Ready-wait timeout |
| `--all` / `--type iPhone\|iPad` | Batch |

### `simctl_shutdown.py`
| Flag | Purpose |
|------|---------|
| `--verify` | Confirm shutdown completed |
| `--all` / `--type ...` | Batch |

### `simctl_create.py`
| Flag | Purpose |
|------|---------|
| `--device <type>` | e.g. `"iPhone 16 Pro"` |
| `--runtime <version>` | e.g. `"iOS 17.5"` |
| `--name <str>` | Custom name |
| `--list-devices` / `--list-runtimes` | Discovery |

### `simctl_delete.py`
| Flag | Purpose |
|------|---------|
| `--yes` | Skip confirmation |
| `--all` | All simulators (destructive) |
| `--old <n>` | Keep N per device type, delete rest |

### `simctl_erase.py`
| Flag | Purpose |
|------|---------|
| `--booted` | Fast reset currently booted sim |
| `--verify` | Confirm reset |
| `--all` / `--type ...` | Batch |

---

## Design notes

- **Screenshot presets**: `full` (3-4 tiles, ~5K tokens), `half` (1 tile, ~1.6K tokens, default), `quarter` (1 tile, ~800 tokens).
- **UDID resolution order**: explicit `--udid` → device name → `$SIMCTL_UDID` env → booted simulator. Set the env var once with `export SIMCTL_UDID=...` to avoid passing `--udid` everywhere.
- **Structured error envelopes** (adopted incrementally — currently `wait_for.py`, `debug_failing_test.py`): under `--json`, failures emit `{"ok": false, "error": {"code": "<STABLE_ENUM>", "message": "...", "hint": "...", "recovery_cmd": "..."}}`. Codes include `NO_BOOTED_SIM`, `DEVICE_NOT_FOUND`, `IDB_NOT_INSTALLED`, `ELEMENT_NOT_FOUND`, `ELEMENT_AMBIGUOUS`, `APP_NOT_INSTALLED`, `APP_NOT_RUNNING`, `BUILD_FAILED`, `TEST_FAILED`, `TIMEOUT`, `INVALID_ARGS`, `ENV_MISSING`, `PERMISSION_DENIED`. The full list lives in `scripts/common/errors.py::ERROR_CODES`. Successes emit `{"ok": true, "data": {...}}`.
- **xcresult IDs** are stable within a session. They persist on disk; older bundles are candidates for cleanup.
- **Progressive disclosure principle**: default output is the minimum actionable signal; everything else is behind a flag.
