# Topic XLSX Runtime Dependency Design

## Problem

The main API serves XLSX exports, but its startup path installs only `requirements.txt`. The XLSX dependency `openpyxl` is declared only in `requirements-topic-mining.txt`, so a normal DevCloud deployment produces HTTP 500 for Excel exports.

## Design

- Declare `openpyxl==3.1.5` in the base runtime requirements used by the main API.
- Keep `requirements-topic-mining.txt` as a compatibility entry point that includes the base requirements without duplicating the version.
- Add a static packaging regression test so future deployments cannot omit the XLSX runtime dependency.
- Do not change export data, workbook format, or Run state behavior.

## Verification

- The packaging regression test fails before the dependency move and passes afterward.
- Topic export and API tests remain green.
- After deployment, the original verified Run exports an XLSX that can be downloaded and opened by `openpyxl`.
