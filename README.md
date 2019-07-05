[![Build Status](https://travis-ci.org/asottile/reorder_python_imports.svg?branch=master)](https://travis-ci.org/asottile/reorder_python_imports)
[![Coverage Status](https://img.shields.io/coveralls/asottile/reorder_python_imports.svg?branch=master)](https://coveralls.io/r/asottile/reorder_python_imports)

reorder_python_imports
==========

Tool for automatically reordering python imports.  Similar to `isort` but
uses static analysis more.


## Installation

`pip install reorder-python-imports`


## Console scripts

Consult `reorder-python-imports --help` for the full set of options.

`reorder-python-imports` takes filenames as positional arguments

Common options:

- `--py##-plus`: [see below](#removing-obsolete-__future__-imports).
- `--add-import` / `--remove-import`: [see below](#adding--removing-imports).
- `--replace-import`: [see below](#replacing-imports).
- `--application-directories`: by default, `reorder-python-imports` assumes
  your project is rooted at `.`.  If this isn't true, tell it where your
  import roots live.  For example, when using the popular `./src` layout you'd
  use `--application-directories=.:src` (note: multiple paths are separated
  using a `:`).

## As a pre-commit hook

See [pre-commit](https://github.com/pre-commit/pre-commit) for instructions

Sample `.pre-commit-config.yaml`

```yaml
-   repo: https://github.com/asottile/reorder_python_imports
    rev: v1.6.0
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

### Splits `from` imports

```python
from os.path import abspath, exists
```

becomes

```python
from os.path import abspath
from os.path import exists
```

### Removes duplicate imports

```python
import os
import os.path
import sys
import sys
```

becomes

```python
import os.path
import sys
```

## Using `# noreorder`

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

Let's say I want to enforce `absolute_import` across my codebase.  I can use:
`--add-import 'from __future__ import absolute_import'`.

```console
$ cat test.py
print('Hello world')
$ reorder-python-imports --add-import 'from __future__ import absolute_import' test.py
Reordering imports in test.py
$ cat test.py
from __future__ import absolute_import
print('Hello world')
```

Let's say I no longer care about supporting Python 2.5, I can remove
`from __future__ import with_statement` with
`--remove-import 'from __future__ import with_statement'`

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

## Replacing imports

Imports can be replaced with others automatically (if they provide the same
names).  This can be useful for factoring out compatibility libraries such
as `six` (see below for automated `six` rewriting).

This rewrite avoids `NameError`s as such it only occurs when:

- the imported symbol is the same before and after
- the import is a `from` import

The argument is specified as `orig.mod=new.mod` or with an optional
checked attribute `orig.mod=new.mod:attr`.  The checked attribute is useful
for renaming some imports from a module instead of a full module.

For example:

```bash
# full module move
--replace-import six.moves.queue=queue
# specific attribute move
--replace-import six.moves=io:StringIO
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

## Removing / rewriting obsolete `six` imports

With `--py3-plus`, `reorder-python-imports` will also remove / rewrite imports
from `six`.  Rewrites follow the same rules as
[replacing imports](#replacing-imports) above.

For example:

```diff
+import queue
+from io import StringIO
+from urllib.parse import quote_plus
+
 import six.moves.urllib.parse
-from six.moves import queue
-from six.moves import range
-from six.moves import StringIO
-from six.moves.urllib.parse import quote_plus
```
