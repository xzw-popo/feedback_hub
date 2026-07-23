# Topic Excel Hyperlink Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the backend workbook mandatory in every delivery and ensure every presented Excel workbook retains clickable feedback links after arbitrary Agent processing.

**Architecture:** Add one deterministic, fail-closed workbook gate to the distributed Skill. It treats the official backend workbook as the ID-to-URL authority, repairs derived workbooks into a distinct output, and validates both official and derived artifacts before the Skill permits delivery.

**Tech Stack:** Python 3, `openpyxl==3.1.5`, `argparse`, `pytest`, Markdown Skill instructions.

## Global Constraints

- The backend-exported `feedback_list.xlsx` remains an unmodified, separately presented primary artifact.
- Every presented `.xlsx` must contain clickable feedback links; plain-text URLs are invalid.
- Derived workbooks may filter, reorder, relabel, add columns, and add sheets, but item-level rows must retain `Feedback ID` and a feedback-link column.
- Link targets come only from the official workbook and are restored as `HYPERLINK` formula, external hyperlink relationship, and Hyperlink style.
- Missing or duplicate IDs, unknown IDs, missing columns, and missing link targets fail closed.
- Summary-only sheets are allowed only when the official workbook or another validated item-level sheet is also presented.

---

### Task 1: Deterministic Workbook Hyperlink Gate

**Files:**
- Create: `codex-skills/mining-feedback-topics/scripts/ensure_feedback_hyperlinks.py`
- Test: `feedback_hub/tests/test_mining_feedback_topics_skill_package.py`

**Interfaces:**
- Consumes: official backend `.xlsx`, final input `.xlsx`, and either `--check` or `--output OUTPUT_XLSX`.
- Produces: exit code `0` plus a JSON summary on success; exit code `2` plus a concise stderr error on invalid input.
- Produces: `repair_and_validate(official_path: Path, input_path: Path, output_path: Path | None) -> dict[str, int]`.

- [ ] **Step 1: Write failing tests for repair, preservation, and fail-closed behavior**

Add helpers that create a small official workbook with `Feedback ID`, `对应链接`, formulas, hyperlink targets, styles, and two feedback records. Add tests covering:

```python
def test_hyperlink_gate_repairs_filtered_reordered_and_labeled_workbook(tmp_path):
    official = _write_official_workbook(tmp_path)
    derived = _write_derived_workbook(
        tmp_path,
        rows=[("feedback-2", "纯文本链接", "类别 B"), ("feedback-1", None, "类别 A")],
    )
    output = tmp_path / "verified.xlsx"

    code, stdout, stderr = _run_hyperlink_gate(
        ["--official", str(official), "--input", str(derived), "--output", str(output)]
    )

    assert code == 0, stderr
    workbook = load_workbook(output, data_only=False)
    assert workbook["反馈清单"]["C2"].data_type == "f"
    assert workbook["反馈清单"]["C2"].hyperlink.target == "https://example.test/2"
    assert workbook["反馈清单"]["D2"].value == "类别 B"
```

Also add independent tests for:

- multiple item-level sheets are repaired while a summary sheet is unchanged;
- `--check` accepts the untouched official workbook;
- unknown, duplicate, and blank feedback IDs fail;
- a sheet with feedback headers but no ID or link column fails;
- missing official link targets fail;
- input and output must differ;
- repaired output is saved atomically and input bytes remain unchanged.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_mining_feedback_topics_skill_package.py -k hyperlink_gate -q
```

Expected: FAIL because `ensure_feedback_hyperlinks.py` does not exist.

- [ ] **Step 3: Implement the minimum gate**

Create a focused script with:

```python
ID_HEADERS = {"feedback id", "feedback_id", "反馈id"}
LINK_HEADERS = {"对应链接", "反馈链接", "source_url", "链接"}
FEEDBACK_HEADERS = {"反馈原文", "feedback_text"}


def repair_and_validate(
    official_path: Path,
    input_path: Path,
    output_path: Path | None,
) -> dict[str, int]:
    source_links = _load_source_links(official_path)
    workbook = load_workbook(input_path, data_only=False)
    item_sheets = _find_item_sheets(workbook)
    if not item_sheets:
        raise ValueError("workbook has no item-level feedback sheet")
    seen: set[str] = set()
    repaired = 0
    for sheet, header_row, id_column, link_column in item_sheets:
        for row in range(header_row + 1, sheet.max_row + 1):
            feedback_id = _normalized_id(sheet.cell(row, id_column).value)
            if _row_is_empty(sheet, row):
                continue
            if not feedback_id or feedback_id in seen:
                raise ValueError("feedback IDs must be non-empty and unique")
            target = source_links.get(feedback_id)
            if not target:
                raise ValueError("feedback ID is unknown or has no official link")
            seen.add(feedback_id)
            cell = sheet.cell(row, link_column)
            if output_path is not None:
                escaped = target.replace('"', '""')
                cell.value = f'=HYPERLINK("{escaped}","打开反馈")'
                cell.hyperlink = target
                cell.style = "Hyperlink"
                repaired += 1
            _validate_link_cell(cell, target)
    if output_path is not None:
        _save_atomic(workbook, input_path, output_path)
        _validate_saved_output(official_path, output_path)
    return {"feedback_rows": len(seen), "repaired_links": repaired}
```

Scan the first 20 non-empty rows of each sheet for supported headers. Treat a sheet as item-level when a scanned header row contains any ID, link, or feedback-text header; require both ID and link columns. Ignore sheets with none of those headers as summaries. Build the official map from the same discovery logic, require unique IDs and HTTPS targets, and never trust a derived workbook URL as source truth.

Implement a CLI with mutually exclusive `--check` and `--output`, JSON stdout, concise errors, and exit code `2` for validation or file errors.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_mining_feedback_topics_skill_package.py -k hyperlink_gate -q
```

Expected: all hyperlink-gate tests PASS.

- [ ] **Step 5: Commit the gate**

```bash
git add codex-skills/mining-feedback-topics/scripts/ensure_feedback_hyperlinks.py feedback_hub/tests/test_mining_feedback_topics_skill_package.py
git commit -m "feat: enforce clickable links in topic workbooks"
```

### Task 2: Skill Delivery Contract

**Files:**
- Modify: `codex-skills/mining-feedback-topics/SKILL.md`
- Modify: `codex-skills/mining-feedback-topics/references/backend-contract.md`
- Test: `feedback_hub/tests/test_mining_feedback_topics_skill_package.py`

**Interfaces:**
- Consumes: `ensure_feedback_hyperlinks.py --official OFFICIAL_XLSX --input FINAL_XLSX (--check | --output VERIFIED_XLSX)`.
- Produces: a final response whose first file is the backend official Excel and whose additional Excel files are validated supplementary artifacts.

- [ ] **Step 1: Write failing static contract tests**

Add tests requiring the Skill body to state:

```python
def test_skill_requires_official_excel_as_primary_delivery():
    body = _skill_body_lower()
    assert "backend official excel" in body
    assert "first file" in body
    assert "must not be replaced" in body


def test_skill_requires_every_presented_excel_to_pass_hyperlink_gate():
    body = _skill_body_lower()
    assert "every presented `.xlsx`" in body
    assert "ensure_feedback_hyperlinks.py" in body
    assert "plain-text link" in body
    assert "must not be delivered" in body
```

Require the backend contract to contain the exact commands:

```bash
python3 scripts/ensure_feedback_hyperlinks.py --official OFFICIAL_XLSX --input OFFICIAL_XLSX --check
python3 scripts/ensure_feedback_hyperlinks.py --official OFFICIAL_XLSX --input DERIVED_XLSX --output VERIFIED_XLSX
```

- [ ] **Step 2: Run static tests and verify RED**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_mining_feedback_topics_skill_package.py -k "official_excel or presented_excel or hyperlink_delivery" -q
```

Expected: FAIL because the delivery contract and commands are absent.

- [ ] **Step 3: Update the Skill and backend contract**

Keep `SKILL.md` below 500 words. Replace the Export/Deliver tail with a compact contract equivalent to:

```markdown
7. **Export.** Export and download `feedback_list.xlsx` as `OFFICIAL_XLSX`; preserve it byte-for-byte. Run the bundled hyperlink gate in `--check` mode. Any transformed workbook is supplementary and must retain `Feedback ID`, then be repaired to a distinct `VERIFIED_XLSX`.
8. **Deliver.** Present `OFFICIAL_XLSX` as the first file, explicitly labeled “backend official Excel”. It must not be replaced or hidden by an Agent-generated workbook. Present only gate-validated supplementary `.xlsx` files afterward. Every presented `.xlsx` must contain clickable links; a plain-text-link or failed workbook must not be delivered.
```

Add the two exact gate commands and failure semantics to `references/backend-contract.md`. State that arbitrary post-processing does not exempt a workbook from the gate and that the official artifact remains a separate download.

- [ ] **Step 4: Run package tests and Skill validation**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_mining_feedback_topics_skill_package.py -q
python3 /Users/charvel/.codex/skills/.system/skill-creator/scripts/quick_validate.py codex-skills/mining-feedback-topics
```

Expected: all package tests PASS and validator reports a valid Skill.

- [ ] **Step 5: Commit the Skill contract**

```bash
git add codex-skills/mining-feedback-topics/SKILL.md codex-skills/mining-feedback-topics/references/backend-contract.md feedback_hub/tests/test_mining_feedback_topics_skill_package.py
git commit -m "docs: require verified topic Excel delivery"
```

### Task 3: End-to-End Artifact Verification and PR Update

**Files:**
- Verify: `codex-skills/mining-feedback-topics/scripts/ensure_feedback_hyperlinks.py`
- Verify: `codex-skills/mining-feedback-topics/SKILL.md`
- Verify: `feedback_hub/tests/test_mining_feedback_topics_skill_package.py`

**Interfaces:**
- Consumes: a production-style official workbook and a deliberately transformed copy containing plain-text links.
- Produces: a repaired workbook whose feedback links are formulas, external targets, and Hyperlink style.

- [ ] **Step 1: Run the complete relevant suite**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  feedback_hub/tests/test_mining_feedback_topics_skill_package.py \
  feedback_hub/tests/test_topic_mining_export.py -q --tb=short
```

Expected: all tests PASS.

- [ ] **Step 2: Run syntax, formatting, and repository checks**

```bash
PYTHONPYCACHEPREFIX=/tmp/feedback-hub-pycache python3 -m compileall -q \
  codex-skills/mining-feedback-topics/scripts \
  feedback_hub/topic_mining
git diff --check
git status --short
```

Expected: compile and diff checks PASS; only intended files plus the existing untracked `WORKSPACE_GUIDE.md` appear.

- [ ] **Step 3: Exercise the gate against a production-style workbook**

Use the downloaded official workbook as both authority and template, create a transformed copy that replaces link formulas with text and adds an arbitrary label column, then run:

```bash
python3 codex-skills/mining-feedback-topics/scripts/ensure_feedback_hyperlinks.py \
  --official /tmp/topic-link-fixed.xlsx \
  --input /tmp/topic-link-derived.xlsx \
  --output /tmp/topic-link-verified.xlsx
```

Open the output with `openpyxl(data_only=False)` and assert every feedback link cell has `data_type == "f"`, an HTTPS `hyperlink.target`, and style `Hyperlink`.

- [ ] **Step 4: Push the existing branch**

```bash
git push origin feature/mining-feedback-topics-skill
```

Expected: the existing pull request receives the new commits.

