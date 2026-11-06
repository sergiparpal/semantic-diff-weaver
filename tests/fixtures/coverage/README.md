# Coverage data files recorded on Python 3.14

`python314-sysmon.coverage` and `python314-ctrace.coverage` are the `.coverage` data files of the
same pytest run of the project below. They were recorded on CPython 3.14.6 with coverage.py 7.17.2
and pytest-cov 7.1.0, the first with coverage.py's default core and the second with its C tracer:

```bash
python -m pytest --cov=shop --cov-context=test --cov-report= tests
COVERAGE_CORE=ctrace python -m pytest --cov=shop --cov-context=test --cov-report= tests
```

Both tests run lines 2 and 4 of `parse_amount`, since `total` calls it:

- **Default core, `sys.monitoring`.** Lines 2 and 4 name `test_parse_amount` alone, the first test
  that ran them. coverage.py printed a `no-sysmon-context` warning, and nothing in the file records
  it.
- **C tracer.** Lines 2 and 4 name both tests.

`relative_files` keeps the paths relative, so the files hold no machine's paths. The project is the
head of the change the regression tests in `tests/integration/test_coverage_contexts.py` analyze,
where line 2 was `if not text:`.

`pyproject.toml`:

```toml
[tool.pytest.ini_options]
pythonpath = ["src"]

[tool.coverage.run]
relative_files = true
```

`src/shop/money.py`, beside an empty `src/shop/__init__.py`:

```python
def parse_amount(text):
    if not text or text == "-":
        return 0
    return int(text)


def total(amounts):
    return sum(parse_amount(text) for text in amounts)
```

`tests/test_money.py`:

```python
from shop.money import parse_amount, total


def test_parse_amount():
    assert parse_amount("") == 0
    assert parse_amount("3") == 3


def test_total():
    assert total(["1", "2"]) == 3
```
