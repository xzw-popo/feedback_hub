# Topic JSONL Unicode Separator Design

## Problem

Topic runs write feedback as JSON Lines with `ensure_ascii=False`. A feedback body may therefore contain the valid JSON character U+2028 or U+2029. Python's `str.splitlines()` treats those characters as line boundaries, so the reader splits one JSON object inside a string and reports `invalid_artifact`.

## Design

- Keep feedback text unchanged; U+2028 and U+2029 remain valid content.
- Treat only LF (`\n`) as the JSONL record delimiter.
- Apply the same rule to the ordinary artifact reader and the manifest-verified artifact reader.
- Preserve the existing validation and `invalid_artifact` error contract for genuinely malformed JSONL.

## Verification

- Regression tests cover U+2028 and U+2029 in both reader paths.
- The existing malformed-JSON test continues to fail closed.
- The topic-mining test suite remains green.
