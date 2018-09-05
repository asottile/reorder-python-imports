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
usage: reorder-python-imports [-h] [--diff-only] [--add-import ADD_IMPORT]
                              [--remove-import REMOVE_IMPORT]
                              [--application-directories APPLICATION_DIRECTORIES]
                              [filenames [filenames ...]]

positional arguments:
  filenames

optional arguments:
  -h, --help            show this help message and exit
  --diff-only           Show unified diff instead of applying reordering.
  --add-import ADD_IMPORT
                        Import to add to each file. Can be specified multiple
                        times.
  --remove-import REMOVE_IMPORT
                        Import to remove from each file. Can be specified
                        multiple times.
  --application-directories APPLICATION_DIRECTORIES
                        Colon separated directories that are considered top-
                        level application directories. Defaults to `.`
```

## As a pre-commit hook

See [pre-commit](https://github.com/pre-commit/pre-commit) for instructions

Sample `.pre-commit-config.yaml`

```yaml
-   repo: https://github.com/asottile/reorder_python_imports
    rev: v1.1.1
    hooks:
    -   id: reorder-python-imports
```

## What does it do?

### Separates imports into three sections

```python
import sys
import pyramid
import reorder_python_imports
```

becomes

```python
import sys

import pyramid

import reorder_python_imports
```

### `import` imports before `from` imports

```python
from os import path
import sys
```

becomes

```python
import sys
from os import path
```

### Splits `from` imports (may be configurable in the future!)

```python
from os.path import abspath, exists
```

becomes

```python
from os.path import abspath
from os.path import exists
```

### Using `# noreorder`

Lines containing and after lines which contain a `# noreorder` comment will
be ignored.  Additionally any imports that appear after non-whitespace
non-comment lines will be ignored.

For instance, these will not be changed:

```python
import sys

try:  # not import, not whitespace
    import foo
except ImportError:
    pass
```


```python
import sys

import reorder_python_imports

import matplotlib  # noreorder
matplotlib.use('Agg')
import matplotlib.pyplot as plt
```

```python
# noreorder
import sys
import pyramid
import reorder_python_imports
```

## why this style?

The style chosen by `reorder-python-imports` has a single aim: reduce merge
conflicts.

By having a single import per line, multiple contributors can
add / remove imports from a single module without resulting in a conflict.

Consider the following example which causes a merge conflict:

```diff
# developer 1
-from typing import Dict, List
+from typing import Any, Dict, List
```

```diff
# developer 2
-from typing import Dict, List
+from typing import Dict, List, Tuple
```

no conflict with the style enforced by `reorder-python-imports`:

```diff
+from typing import Any
 from typing import Dict
 from typing import List
+from typing import Tuple
```

## Adding / Removing Imports

Let's say I want to enforce `absolute_import` across my codebase.  I can use: `--add-import 'from __future__ import absolute_import'`.

```console
$ cat test.py
print('Hello world')
$ reorder-python-imports --add-import 'from __future__ import absolute_import' test.py
Reordering imports in test.py
$ cat test.py
from __future__ import absolute_import
print('Hello world')
```

Let's say I no longer care about supporting Python 2.5, I can remove `from __future__ import with_statement` with `--remove-import 'from __future__ import with_statement'`

```console
$ cat test.py
from __future__ import with_statement
with open('foo.txt', 'w') as foo_f:
    foo_f.write('hello world')
$ reorder-python-imports --remove-import 'from __future__ import with_statement' test.py
Reordering imports in test.py
$ cat test.py
with open('foo.txt', 'w') as foo_f:
    foo_f.write('hello world')
```

## Removing obsolete `__future__` imports

The cli provides a few options to help "burn the bridges" with old python
versions by removing `__future__` imports automatically.  Each option implies
all older versions.

- `--py22-plus`: `nested_scopes`
- `--py23-plus`: `generators`
- `--py26-plus`: `with_statement`
- `--py3-plus`: `division`, `absolute_import`, `print_function`,
  `unicode_literals`
- `--py37-plus`: `generator_stop`
