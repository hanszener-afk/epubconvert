# novel2epub

Converts DOCX and PDF novel manuscripts into EPUB 3, and checks that no text was lost on the way.

**Status: milestone 1 of 5.** The intermediate representation, the EPUB writer, EPUBCheck integration, and the text-fidelity comparison exist and are tested with hand-built books. There is no DOCX or PDF input yet, and no command-line tool.

## Development

Python 3.11 or later, and Java 11 or later for EPUBCheck.

```
python -m venv .venv
.venv/bin/pip install -e ".[dev]"      # Windows: .venv\Scripts\pip
.venv/bin/pytest
.venv/bin/ruff check src tests && .venv/bin/mypy
```

The `dev` extra installs the `epubcheck` package (BSD-3-Clause), which bundles EPUBCheck 5.3.0. To use a different jar, set `NOVEL2EPUB_EPUBCHECK` to its path. EPUBCheck tests are skipped if Java or the jar cannot be found.

## Documents

- `docs/PLAN.md`: architecture, dependencies and licences, decisions
- `docs/markup.md`: the XHTML and class vocabulary the writer produces
- `docs/css-decisions.md`: what the stylesheet does and avoids, and why

## Licence

MIT. See `LICENSE`.
