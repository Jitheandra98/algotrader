# `research/` — Research workspace

Jupyter notebooks + helper library for exploratory work outside the main
production code path.

  - `helpers.py` — utilities the notebooks import. **Tested.**
  - `notebooks/` — `.ipynb` files. **Not tested.**

## Running

```bash
poetry install --with dev
poetry run jupyter lab
```

See `notebooks/README.md` for suggested workflows.
