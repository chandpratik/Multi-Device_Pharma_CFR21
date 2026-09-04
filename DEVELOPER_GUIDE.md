# Pharma Code Verification — Developer Guide

## 1. Purpose and scope

This repository contains a two-device desktop application for validating pharmaceutical codes read by Cognex DataMan cameras. Each read is compared with a taught master code, classified as `PASS` or `FAIL`, written to a durable CSV write-ahead log (WAL), displayed in the GUI, and optionally used to pulse a shared PLC over Modbus/TCP.

The application also contains controls intended to support a 21 CFR Part 11 deployment: named users, role-based permissions, password policies, session locking, a tamper-evident audit trail, file checksums, report export, and database backups.

> Important: software features alone do not establish regulatory compliance. A validated deployment also requires approved procedures, qualification, access controls, backup/restore testing, training, and change control.

Current application metadata is defined in `version.py`:

- Application: Pharma Code Verification
- Company: Sun Pharma
- Version: 1.0.0

## 2. Technology stack

| Area | Implementation |
|---|---|
| Desktop UI | Python and PyQt6 |
| Live charts | pyqtgraph |
| Camera protocol | TCP socket, Cognex DataMan/DMCC on port 23 |
| PLC protocol | Modbus/TCP through pymodbus |
| Durable scan log | CSV WAL |
| Batch output | Excel workbook through openpyxl |
| Compliance data | SQLite (`compliance.db`) |
| Password hashing | bcrypt |
| PDF reports | ReportLab |
| Tests | pytest |

The supported dependency versions are pinned in `requirements.txt` for reproducibility.

## 3. Repository map

```text
main.py                 Application entry point and startup sequence
version.py              Application name, company, and version
license_check.py        Camera serial-number allow-list verification
crash_handler.py        Unhandled-exception capture and crash auditing
requirements.txt        Pinned runtime dependencies

config/
  settings.py           Typed configuration, defaults, JSON load/save

core/
  models.py             Shared ReadRecord and SessionInfo dataclasses
  app_controller.py     GUI-to-hardware orchestration layer
  datalogger.py         Per-camera scan loop and PASS/FAIL logic

comms/
  camera.py             Buffered TCP camera client
  plc_modbus.py          Shared, thread-safe Modbus/TCP client
  excel_wal.py           Per-device CSV WAL and Excel generation

cfr21/
  db.py                 SQLite connection, schema, migrations, first user
  user_manager.py       Authentication, password policy, and RBAC
  session_manager.py    Login session, timeout, lock, and reauthentication
  audit_trail.py        Append-only application audit records and hash chain
  record_integrity.py   SHA-256 sealing and verification of batch files
  db_backup.py          Database backup and backup checksum recording
  report_export.py      Audit-trail and batch-record PDF exports

gui/
  main_window.py        Main UI and application workflow coordination
  device_panel.py       Per-device status, counters, and recent reads
  dialogs.py            Session setup and advanced settings
  cfr_dialogs.py        Login, password, reason, and reauth dialogs
  cfr_tab.py            Audit, users, integrity, and report pages
  styles.py             Global Qt stylesheet
  widgets.py            Reusable UI helpers and bundled-font loading
  ui_constants.py       Shared dimensions and UI constants

tests/                  Compliance-focused automated tests
fonts/                  Bundled IBM Plex font files
```

## 4. Local setup

The project is primarily designed for Windows, although most non-hardware logic is platform-independent.

### Prerequisites

- Python 3.10 is known to be used by the existing test artifacts. Python 3.9 artifacts are also present.
- A virtual environment is strongly recommended.
- Camera and PLC access require the workstation to reach their configured IP addresses.

### Install and run (PowerShell)

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python main.py
```

On the first run, `cfr21.db.initialise()` creates `compliance.db` and a one-time `admin` account. Its random password is written to `first_login.txt` in the project root (or beside the executable in a frozen build) and printed to the console. Log in, change the password immediately, and securely remove `first_login.txt` afterward.

Do not use a production database or production log directory for development and automated testing.

### Run tests

Pytest is documented but commented out in `requirements.txt`. Install the pinned version indicated there, or install it explicitly in the development environment, then run:

```powershell
pip install pytest==8.3.2
pytest tests -v
```

Tests monkeypatch the database path to a temporary SQLite file. They cover authentication, account lockout, password policy/history, permissions, sessions, audit-chain tamper detection, and batch-file integrity.

## 5. Startup sequence

`main.py` owns the application lifecycle:

1. Install the global crash handler.
2. Configure logging and create the `QApplication`.
3. Apply a fixed light palette, stylesheet, and bundled fonts.
4. Initialize and migrate `compliance.db`; run SQLite integrity checks.
5. Load `settings.json`, falling back to typed defaults for missing/invalid values.
6. Check for suspicious database recreation when backups exist but audit records do not.
7. Construct one `SessionManager` from the configured security policy.
8. Block on `LoginDialog` until authentication succeeds.
9. Write `APP_STARTED` to the audit trail.
10. Construct and display `MainWindow`.
11. Back up the compliance database immediately and every four hours.
12. On normal shutdown, audit `APP_CLOSED` and log out the active session.

## 6. Runtime architecture

```text
PyQt GUI (main_window.py)
          |
          v
AppController (one instance)
     |                    |
     |                    +--> PLCClient (one shared Modbus connection)
     |
     +--> Datalogger 1 --> CameraClient 1 --> WALExcelLogger 1
     |
     +--> Datalogger 2 --> CameraClient 2 --> WALExcelLogger 2

SessionManager --> user_manager --> compliance.db
GUI/actions ----> audit_trail ----> compliance.db
Batch close ----> record_integrity -> compliance.db
```

`MainWindow` coordinates user actions and translates worker callbacks into Qt signals. `AppController` is the boundary between GUI code and device/logging objects. Hardware internals should not be accessed directly from widgets.

There is one `Datalogger` per camera and one shared `PLCClient`. Both dataloggers receive the same `SessionInfo`, but maintain independent master codes, read counters, WAL files, and Excel outputs.

## 7. Scan and batch data flow

### Before logging

The operator configures a session with at least a batch ID, connects the required cameras/PLC, and teaches a master code for each active camera. Camera connection also runs `license_check.py`, which queries `DEVICE.SERIAL-NUMBER` and accepts only serials in `_AUTHORISED`.

### During logging

For each device, `Datalogger.start()` opens or creates a timestamped WAL and starts its scan thread. The scan loop:

1. Receives a newline-delimited camera response.
2. Normalizes the response into a code.
3. Compares it with the current master code.
4. Creates a `ReadRecord` containing read ID, timestamp, scan data, master data, status, batch/operator/product, and device ID.
5. Appends the record to CSV and flushes it immediately.
6. Queues a PLC fail pulse when applicable.
7. Emits callbacks for the GUI, counters, alarms, and error handling.

PLC work is decoupled from camera scanning so a Modbus write does not block scan acquisition. GUI updates are dispatched using Qt signals; worker callbacks must never modify widgets directly.

### PASS/FAIL behavior

The parser in `core/datalogger.py` treats an exact master-code match as `PASS`. `NO_READ` and validation-failure responses are failures. Consecutive failures are counted and trigger an alarm at the configured threshold.

### Stopping a batch

Stopping first terminates the scan threads and closes the live WAL handles. `MainWindow` then runs `AppController.close_batch()` in a background Qt thread. Each device:

1. Reads the complete WAL.
2. Builds the Excel workbook once, avoiding Excel work in the scan loop.
3. Computes SHA-256 hashes for the WAL and Excel files.
4. Records the seals in `file_integrity`.

The unique database index on `(batch_id, device_id, file_type)` prevents a file type for the same batch/device from being resealed as a second record.

## 8. Files and persistence

### Development layout

```text
settings.json                         Runtime configuration
compliance.db                         Users, audit, policies, file seals
first_login.txt                       One-time first-install credential
crash_log.txt                         Crash diagnostics
config/logs/                          Default log root in source mode
  Device1/
    ProductionLog_<batch>_<time>.xlsx
    wal/WAL_<batch>_<time>.csv
  Device2/
    ProductionLog_<batch>_<time>.xlsx
    wal/WAL_<batch>_<time>.csv
  db_backups/compliance_backup_<time>.db
```

In a frozen executable, `settings.json`, `compliance.db`, and first-login credentials are stored beside the executable. The default log-root helper is based on the `config` directory in source mode and the executable directory in a frozen build. Always confirm the effective log path in Advanced Settings before production use.

WAL rows contain:

```text
read_id, timestamp, batch_id, operator_id, product_name,
raw_data, master_data, status
```

The WAL is the durable source used to generate Excel and recover counters after an interrupted run. On startup the UI detects unsealed/orphaned WALs and offers the integrity recovery path.

## 9. Configuration reference

`AppConfig` is composed of typed dataclasses and persists atomically to `settings.json`. Unknown keys are ignored; missing values retain defaults; known values are type-coerced when possible.

| Section | Key defaults | Purpose |
|---|---|---|
| `device1` | `192.168.10.10:23`, poll `2.0s` | Camera 1 connection |
| `device2` | `192.168.10.11:23`, poll `2.0s` | Camera 2 connection |
| `plc` | `192.168.10.20:502` | Shared Modbus endpoint |
| `plc` | registers `0`, `1`; pass `0`; fail `1` | Per-device fail pulse configuration |
| `plc` | optional registers default to `-1` | Reject/trigger delay, timing, camera status, spares; `-1` disables a register |
| `general` | failure limit `3` | Alarm threshold and storage paths |
| `policy` | timeout `30m`, expiry `90d` | Session/password policy |
| `policy` | attempts `3`, lockout `30m`, history `5` | Login lockout and password reuse |
| `company` | name `Sun Pharma` | PDF report identity |

Configuration changes are saved by an administrator and applied to dataloggers on the next Start Logging operation. Connection-related changes should be made while logging is stopped.

## 10. Compliance subsystem

### Database schema

The current schema version is 3. `cfr21/db.py` owns all database location and connection behavior. Connections enable SQLite WAL journal mode and foreign keys, commit on a clean context-manager exit, and roll back on exceptions.

| Table | Responsibility |
|---|---|
| `schema_version` | Migration level |
| `users` | Accounts, bcrypt hashes, roles, status, lockout, password age |
| `password_history` | Previous bcrypt hashes used to prevent reuse |
| `audit_trail` | Application events with identity, reason, session, workstation, and hash chain |
| `file_integrity` | SHA-256 seals for Excel and WAL batch files |
| `backup_integrity` | SHA-256 seals for database backups |

The audit trail is append-only by application design. Each new row includes the previous record hash and its own hash. `verify_chain()` detects edited, deleted, inserted, or reordered records. This is tamper-evident behavior, not database-level prevention against a privileged filesystem/database administrator.

### Roles

| Capability | Administrator | Supervisor | Operator | QA |
|---|:---:|:---:|:---:|:---:|
| Start/stop logging | Yes | Yes | Yes | No |
| Set/clear master | Yes | Yes | Yes | No |
| Connect cameras/PLC | Yes | Yes | Yes | No |
| View live data | Yes | Yes | Yes | Yes |
| View/export reports and audit | Yes | Yes | No | Yes |
| Manage users | Yes | No | No | No |
| Change settings | Yes | No | No | No |
| Change own password | Yes | Yes | Yes | Yes |

Sensitive settings access requires administrator permission plus reauthentication. Passwords require at least eight characters and uppercase, lowercase, digit, and special-character classes. Account creation, deactivation/reactivation, password reset, login failures, lockouts, batch lifecycle, hardware events, settings changes, exports, integrity checks, crashes, and backups are among the audited actions.

### Backup behavior

The app performs a database backup at startup and every four hours. If `general.backup_destination` is empty, backups go to a `db_backups` directory below the configured log root. Backups are checksum-sealed in `backup_integrity`.

A startup heuristic warns when an older, empty audit database is found while backup files exist. Treat this as an investigation signal rather than proof of tampering. Restore procedures should be validated outside the production application before they are needed.

## 11. Threading model

| Thread/context | Responsibilities |
|---|---|
| Qt GUI thread | Widgets, dialogs, controller commands, configuration changes |
| One scan thread per device | Socket reads, parsing, record creation, WAL append, callbacks |
| Datalogger PLC worker | Queued fail-register writes |
| PLC refresh thread | Periodic static/status register writes |
| Batch-close QThread | Excel generation and file sealing |

Development rules:

- Never update Qt widgets directly from a scan or PLC callback.
- Keep WAL writes synchronous and short; durability precedes display.
- Do not add Excel generation or slow I/O to the scan loop.
- Treat the shared PLC client as a single resource; preserve its internal locking.
- Stop logging before changing connections or replacing device objects.

## 12. Common development tasks

### Change camera response parsing

Update `_extract_code()` / `_parse_line()` in `core/datalogger.py`, then add focused unit tests for normal reads, `NO_READ`, validation failures, malformed input, and exact master matching.

### Add a configurable setting

1. Add the typed field to the appropriate dataclass in `config/settings.py`.
2. Add it to the schema passed to `_apply()` in `AppConfig.load()`.
3. Add it to the Advanced Settings UI and extraction/population methods in `gui/dialogs.py`.
4. Push it into live components in `AppController._apply_config()` if needed.
5. Audit a meaningful before/after description for regulated settings.

### Add a PLC register

Add the configuration field, expose it in settings, pass it through `AppController`, and implement writes in `comms/plc_modbus.py`. Follow the existing `-1 = disabled` convention and keep network writes off the GUI and scan-critical paths.

### Add a regulated user action

Define an `ACTION_*` constant in `cfr21/audit_trail.py`, use `audit.log()` at the point the action succeeds, attach the current user and session ID, and require a reason/reauthentication where the action changes critical state.

### Change the database schema

Never edit only the initial `CREATE TABLE` statements. Increment `_SCHEMA_VERSION`, add an idempotent migration in `_migrate()`, preserve existing records and hashes, and add migration tests against both a fresh and an older schema.

### Authorize another camera

Add the exact Cognex serial number to `_AUTHORISED` in `license_check.py`. Because this is a source-code allow-list, make the change through the validated release/change-control process.

## 13. Testing strategy and current boundaries

The existing suite focuses on the compliance package. It does not currently provide broad automated coverage for:

- PyQt screen workflows and role-dependent visibility
- Real Cognex DMCC responses and connection recovery
- Real Modbus register behavior and PLC timing
- Long-running, high-volume scan performance
- Power-loss behavior during WAL and SQLite writes
- Backup restoration and frozen-executable paths
- End-to-end report contents and print fidelity

For hardware-independent development, prefer unit tests with fake camera and PLC objects rather than connecting to production devices. Before release, perform integration tests on an isolated qualification network and retain evidence under the site's validation/change-control process.

## 14. Troubleshooting

### Application exits before the main window

- Closing the login dialog intentionally exits with code 0.
- Check the console and `crash_log.txt` for startup failures.
- Verify `compliance.db` is writable and passes SQLite integrity checks.

### First login credentials are unavailable

- Look for `first_login.txt` beside `main.py` in source mode or beside the executable in a frozen build.
- The file is created only when the `users` table is empty.
- Do not delete or recreate `compliance.db` merely to obtain a new password; use the controlled recovery/restore process.

### Camera connection succeeds but authorization fails

- Confirm the device is a supported Cognex DataMan camera and responds to DMCC.
- Confirm port 23 is reachable and no other client monopolizes the connection.
- Compare the returned serial with `_AUTHORISED` in `license_check.py`.

### No PLC fail pulse

- Verify the PLC is connected separately from the cameras.
- Check per-device holding-register addresses and Modbus reachability.
- Confirm the configured fail value and the 100 ms pulse expectation.
- Review audit events and runtime logs for `PLC_WRITE_FAILED`.

### Excel file is missing after Stop

- The CSV WAL is the primary recovery source; verify it exists and is readable.
- Excel is generated asynchronously after scan threads stop, so wait for batch-close completion.
- Check disk permissions/free space and runtime logs for rebuild errors.

### Integrity verification fails

- Do not overwrite or “repair” the file in place.
- Preserve the file, database, backups, audit export, and relevant crash logs.
- Compare the current checksum with the stored seal and investigate through the approved deviation process.

## 15. Release checklist

- Update `version.py` under change control.
- Keep runtime dependency versions pinned and document upgrades.
- Run `pytest tests -v` in a clean environment.
- Complete camera and PLC integration tests on qualification hardware.
- Verify first-login, password-change, lockout, timeout, and role workflows.
- Verify audit-chain and batch-file integrity checks.
- Verify database backup and restore, including the selected destination.
- Confirm effective paths and permissions in the packaged executable.
- Remove `first_login.txt` after credential handover.
- Archive validation evidence, configuration, dependency versions, and release artifact hashes.

