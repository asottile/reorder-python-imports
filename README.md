[![Build Status](https://travis-ci.org/asottile/reorder_python_imports.svg?branch=master)](https://travis-ci.org/asottile/reorder_python_imports)
[![Coverage Status](https://img.shields.io/coveralls/asottile/reorder_python_imports.svg?branch=master)](https://coveralls.io/r/asottile/reorder_python_imports)

reorder_python_imports
==========

Tool for automatically reordering python imports.  Similar to `isort` but
uses static analysis more.


## Installation

`pip install reorder-python-imports`


## Console scripts

```
reorder-python-imports --help
usage: reorder-python-imports [-h] [filenames [filenames ...]]

positional arguments:
  filenames

optional arguments:
  -h, --help  show this help message and exit
```

## As a pre-commit hook

See [pre-commit](https://github.com/pre-commit/pre-commit) for instructions

Hooks available:
- `reorder-python-imports` - This hook reorders imports in python files.
