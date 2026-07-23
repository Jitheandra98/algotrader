"""Research workspace — Jupyter notebooks + helper library for exploratory work.

The actual ``.ipynb`` files live in ``research/notebooks/``. The :mod:`research.helpers`
module provides common utilities (fixture loaders, plotting wrappers) that the
notebooks import.

Notebooks are NOT part of the lint/type-check/test gate — they're scratchpads.
The helper library *is* tested.
"""
