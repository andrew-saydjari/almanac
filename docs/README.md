# almanac Documentation

This directory holds the documentation of the `andrew-saydjari/almanac` fork (Sphinx,
with Markdown pages through MyST).

## Documentation Structure

- **[Installation Guide](installation.md)**: installing the fork
- **[User Guide](user-guide.md)**: how to build and use almanac files
- **[CLI Reference](cli-reference.md)**: every command and option
- **[Configuration Guide](configuration.md)**: every setting
- **[Data Formats](data-formats.md)**: every HDF5 group, dataset and attribute, and what
  downstream code reads
- **[Exposure Overrides](exposure-overrides.md)**: the override and hand-placed targets
  files
- **[FAQ](faq.md)**: questions and troubleshooting
- **[API Reference](api-reference.md)**: the main Python entry points
- **[Development Guide](development.md)**: tests, CI, the documentation build and audit

## Building Documentation

```bash
uv sync --frozen --extra dev --extra docs
cd docs
uv run sphinx-build -b html . _build/html -W --keep-going   # what CI runs
make html                                                    # the same build through the Makefile (warnings not fatal)
make livehtml                                                # live rebuild (needs sphinx-autobuild)
```

`docs/requirements.txt` lists the build requirements for ReadTheDocs
(`.readthedocs.yaml`). `audit_docs.py` checks the pages against the code (see the
[Development Guide](development.md#documentation-audit)); `test_docs.py` is an older
build smoke test.
