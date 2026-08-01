# Pi kg CLI Extension Content Assertions

Coverage: Task 2 — plan requires extension-string verification before commit.

Run test expecting RED failure (missing `_EXTENSION_SOURCE`):

```bash
uv run pytest tests/test_install_pi.py::test_pi_extension_has_only_cli_tools_and_capture_controls -q
```

Expected: FAIL (attribute error or empty string).