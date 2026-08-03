# Contributing

Two things live here: the Home Assistant integration (`custom_components/gc3/`)
and the client library it depends on (`pygc3/`). They are tested and released
separately.

## The integration

Home Assistant 2026.7+ requires Python ≥3.14.2, so the integration is developed
against that.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-test.txt
pip install ./pygc3

pytest --cov=custom_components/gc3   # 69 tests, ≥95% enforced
mypy custom_components/gc3           # strict
ruff check custom_components tests
ruff format --check custom_components tests
```

The panel is mocked at the `pygc3` boundary (see `tests/conftest.py`), not at
HTTP — `pygc3` owns its own transport tests, and everything the integration does
is a function of the parsed models. No test touches a real panel.

## The library

```bash
cd pygc3
pip install -e ".[test,dev]"
pytest --cov=pygc3
mypy
ruff check src tests
```

`pygc3` supports Python ≥3.11 (wider than the integration needs, since anyone
talking to a GC3 can use it) and CI runs the matrix.

## Testing against a real panel

You need the panel's `X-Api-Key` and `X-Pairing-Key`. On current firmware these
can only be captured from an already-paired controller — see
[`INTEGRATION_PLAN.md`](INTEGRATION_PLAN.md) §2 for the full investigation and
the MITM procedure, and only ever against your own equipment.

**Never commit those credentials.** `.env` and packet captures are gitignored;
diagnostics downloads redact them. If a key does reach a commit, rotate it by
re-pairing rather than trying to rewrite history.

Bear in mind that this drives a live alarm. Arming and disarming during testing
will sound sirens and, on a monitored panel, can dispatch responders.

## Quality scale

`custom_components/gc3/quality_scale.yaml` records the status of every
[Integration Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/)
rule. Changes that satisfy a `todo` rule should flip it in the same PR; changes
that add a feature should not silently break a `done` one.

## Releasing

- **Integration** — bump `version` in `custom_components/gc3/manifest.json` and
  tag `v<version>`. HACS reads the tag.
- **pygc3** — bump `pyproject.toml` and `pygc3/__init__.__version__`, update
  `pygc3/CHANGELOG.md`, tag `pygc3-v<version>`. The tag triggers the PyPI
  publish workflow (trusted publishing; no token in the repo). If the library
  gained a release the integration needs, bump the pin in `manifest.json`
  `requirements` too.
