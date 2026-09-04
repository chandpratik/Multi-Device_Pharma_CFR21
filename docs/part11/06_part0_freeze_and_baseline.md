# Part 0 — Freeze and Baseline

## Objective

Establish a controlled, reproducible baseline before further compliance changes.
This phase prevents uncertainty about which source, database schema,
configuration, and executable were assessed or changed.

## Implementation sequence

1. Assign a formal application name, system owner, quality owner, intended use,
   regulated process, deployment topology, and supported operating systems.
2. Identify every regulated record and its authoritative source, owner,
   retention period, review requirement, and signature requirement.
3. Inventory source files, dependencies, database schemas, settings, hardware
   interfaces, output files, backup locations, and deployment scripts.
4. Create a source baseline tag and calculate hashes for source, dependency
   lockfiles, database schema, executable, installer, and controlled documents.
5. Archive representative `compliance.db`, WAL, Excel, PDF, JSON, log, and backup
   artifacts without changing their content.
6. Establish requirement IDs and map audit findings F-01 through F-23 to
   requirements, risks, remediation tasks, tests, and approval records.
7. Define severity, change classification, review, approval, release, rollback,
   deviation, and emergency-change procedures.
8. Record known limitations and prohibit regulated use until release criteria
   are met.

## Required documents

- Intended-use statement and system boundary.
- User and functional requirements specifications.
- Regulated-record inventory and data-flow diagram.
- Initial risk assessment and audit-finding register.
- Configuration-item inventory and baseline manifest.
- Change-control and validation plan.

## Verification

- Independently reproduce the baseline from the recorded source revision.
- Verify every manifest hash.
- Confirm every regulated record has an identified source and retention owner.
- Confirm every audit finding maps to at least one requirement and test.

## Definition of done

Quality and system owners approve the baseline, intended use, risk assessment,
requirements, validation plan, and restriction on production use.
