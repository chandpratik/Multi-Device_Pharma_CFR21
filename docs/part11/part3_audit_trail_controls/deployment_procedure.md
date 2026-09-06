# Part 3 Deployment Procedure

The application creates audit_signing.key and audit_anchor.json beside
compliance.db. These files are part of the audit integrity boundary and must
not be writable by operators or by an interactive administrator using SQLite
or a text editor.

Run the following from an elevated PowerShell session after the first
controlled application startup has created both files:

    .\deployment\protect_audit_artifacts.ps1 -DataDirectory "C:\ProgramData\PharmaLogger" -ServiceAccount "PHARMA\PharmaLoggerSvc"

The script changes only the two named files. It removes inherited ACLs and
grants full control to Windows SYSTEM and the local Administrators group, and
Modify access to the dedicated application service account. Operators receive
no direct access. The service account must be non-interactive and must not be a
local administrator.

QA/change control must record the resolved data directory, service-account
identity, ACL output, backup destination, and restore test result. Re-run the
procedure after changing the service account or moving the data directory.

SQLite has no native database users or GRANT ownership model. Part 3 therefore
enforces runtime audit protection with the SQLite authorizer and append-only
triggers; operating-system ACLs provide the separate deployment boundary for
the signing key and external anchor.
