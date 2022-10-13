from __future__ import annotations

import io
import os
import sys
from unittest import mock

import pytest
from classify_imports import Settings

from reorder_python_imports import apply_import_sorting
from reorder_python_imports import fix_file_contents
from reorder_python_imports import main
from reorder_python_imports import parse_imports
from reorder_python_imports import partition_source
from reorder_python_imports import remove_duplicated_imports
from reorder_python_imports import Replacements
from reorder_python_imports import Tok
from reorder_python_imports import TOKENIZE


@pytest.fixture
def in_tmpdir(tmpdir):
    with tmpdir.as_cwd():
        yield tmpdir


@pytest.mark.parametrize(
    's',
    (
        "''",
        '""',
        'r"hi"',
        'u"hello"',
        '"""hello\nworld"""',
        "'''\n'''",
        "'''\\\n'''",
        "'''\\\r\n'''",
    ),
)
def test_tokenize_can_match_strings(s):
    tp, pat = TOKENIZE[-1]
    assert tp == Tok.STRING
    assert pat.fullmatch(s)


@pytest.mark.parametrize(
    's',
    (
        pytest.param('', id='trivial'),
        pytest.param('#!/usr/bin/env python\n', id='shebang'),
        pytest.param('# -*- coding: UTF-8 -*-\n', id='source encoding'),
        pytest.param('  # coding: UTF-8\n', id='source encoding indented'),
        pytest.param(
            '#!/usr/bin/env python\n'
            '# -*- coding: UTF-8 -*-\n',
            id='shebang and source encoding',
        ),
        pytest.param('"""foo"""\n', id='docstring'),
        pytest.param(
            '"""foo"""\n'
            '"""bar"""\n',
            id='multiple docstrings',
        ),
        pytest.param(
            '# -*- coding UTF-8 -*-\n'
            'u"""☃☃☃"""\n',
            id='unicode docstring',
        ),
    ),
)
def test_partition_source_before_code_only(s):
    before, imports, after, nl = partition_source(s)
    assert before == s
    assert imports == []
    assert after == ''
    assert nl == '\n'


@pytest.mark.parametrize(
    's',
    (
        pytest.param('#!/usr/bin/env python', id='shebang no nl'),
        pytest.param('# -*- coding: UTF-8 -*-', id='source encoding no nl'),
        pytest.param('"""foo"""', id='docstring no nl'),
    ),
)
def test_adds_newline_no_nl(s):
    before, imports, after, nl = partition_source(s)
    assert before == f'{s}\n'
    assert imports == []
    assert after == ''
    assert nl == '\n'


@pytest.mark.parametrize(
    's',
    (
        pytest.param('x = 1\n', id='code only'),
        pytest.param(
            'x = 1\n'
            'import os\n',
            id='found code before imports',
        ),
        pytest.param(
            '# noreorder\n'
            'import os\n',
            id='noreorder before imports',
        ),
        pytest.param(
            '"""docstring"""  # noreorder\n'
            'import os\n',
            id='noreorder on docstring\n',
        ),
        pytest.param(
            '"""docstring""" + "nope"\n',
            id='docstring with expression',
        ),
    ),
)
def test_partition_source_code_only(s):
    before, imports, after, nl = partition_source(s)
    assert before == ''
    assert imports == []
    assert after == s
    assert nl == '\n'


def test_partition_source_code_only_adds_newline():
    before, imports, after, nl = partition_source('x = 1')
    assert before == ''
    assert imports == []
    assert after == 'x = 1\n'
    assert nl == '\n'


@pytest.mark.parametrize(
    ('s', 'expected'),
    (
        pytest.param('import os\n', ['import os\n'], id='simple import'),
        pytest.param('import os', ['import os\n'], id='simple import no nl'),
        pytest.param(
            'from foo import *  # noqa\n',
            ['from foo import *  # noqa\n'],
            id='preserves comments on imports',
        ),
        pytest.param(
            'import sys\nimport os\n',
            ['import sys\n', 'import os\n'],
            id='multiple imports',
        ),
        pytest.param(
            'import sys\n\n\nimport os\n',
            ['import sys\n', 'import os\n'],
            id='discards whitespace within imports',
        ),
        pytest.param(
            'import os\n'
            '\n'
            '    \n'
            'import sys\n',
            ['import os\n', 'import sys\n'],
            id='discards trailing whitespace within imports',
        ),
        pytest.param(
            'from foo import (\n'
            '    bar,\n'
            ')\n',
            ['from foo import (\n    bar,\n)\n'],
            id='multiline imports',
        ),
        pytest.param(
            'from foo import (  # c1 )\n'
            '    bar,  # c2 )\n'
            ')  # c3\n',
            ['from foo import (  # c1 )\n    bar,  # c2 )\n)  # c3\n'],
            id='multiline imports with comments',
        ),
        pytest.param(
            'from foo import *\n',
            ['from foo import *\n'],
            id='star imports',
        ),
    ),
)
def test_partition_source_imports_only(s, expected):
    before, imports, after, nl = partition_source(s)
    assert imports == expected
    assert before == after == ''
    assert nl == '\n'


def test_partition_source_before_removes_newlines():
    before, imports, after, nl = partition_source(
        '# comment here\n'
        '\n'
        '# another comment here\n',
    )
    assert before == (
        '# comment here\n'
        '# another comment here\n'
    )
    assert imports == []
    assert after == ''
    assert nl == '\n'


def test_partition_source_before_and_code_only():
    before, imports, after, nl = partition_source(
        '# before\n'
        'x = 1\n',
    )
    assert before == '# before\n'
    assert imports == []
    assert after == 'x = 1\n'
    assert nl == '\n'


def test_partition_source_all_three_types():
    before, imports, after, nl = partition_source(
        '# comment before\n'
        'import os\n'
        'import sys\n'
        'print("code after")\n',
    )
    assert before == '# comment before\n'
    assert imports == ['import os\n', 'import sys\n']
    assert after == 'print("code after")\n'
    assert nl == '\n'


def test_partition_source_preserves_whitespace_after():
    before, imports, after, nl = partition_source(
        'import os\n'
        '\n'
        'print(1)\n',
    )
    assert before == ''
    assert imports == ['import os\n']
    assert after == '\nprint(1)\n'
    assert nl == '\n'


def test_partition_source_preserves_comments_after():
    before, imports, after, nl = partition_source(
        'import os\n'
        '\n'
        '# comment here!\n'
        '\n'
        'print(1)\n',
    )
    assert before == ''
    assert imports == ['import os\n']
    assert after == (
        '\n'
        '# comment here!\n'
        '\n'
        'print(1)\n'
    )
    assert nl == '\n'


def test_partition_source_interspersed_comments_become_code():
    before, imports, after, nl = partition_source(
        'import os\n'
        '# hello world\n'
        'import sys\n'
        '\n'
        'print(1)\n',
    )
    assert before == ''
    assert imports == ['import os\n', 'import sys\n']
    assert after == (
        '# hello world\n'
        '\n'
        'print(1)\n'
    )
    assert nl == '\n'


def test_partition_source_noreorder_becomes_code():
    before, imports, after, nl = partition_source(
        'import os\n'
        'import sys  # noreorder\n'
        'import re\n',
    )
    assert before == ''
    assert imports == ['import os\n']
    assert after == (
        'import sys  # noreorder\n'
        'import re\n'
    )
    assert nl == '\n'


def test_partition_source_noreorder_becomes_code_on_first_import():
    before, imports, after, nl = partition_source(
        '# before\n'
        'import os  # noreorder\n'
        'import sys\n',
    )
    assert before == '# before\n'
    assert imports == []
    assert after == (
        'import os  # noreorder\n'
        'import sys\n'
    )
    assert nl == '\n'


def test_partition_source_noreorder_import_whitespace_above_preserved():
    before, imports, after, nl = partition_source(
        'import os\n'
        '\n'
        'import sys  # noreorder\n'
        'import re\n',
    )
    assert before == ''
    assert imports == ['import os\n']
    assert after == (
        '\n'
        'import sys  # noreorder\n'
        'import re\n'
    )
    assert nl == '\n'


def test_partition_source_noreorder_starts_code():
    before, imports, after, nl = partition_source(
        'import os\n'
        '# noreorder\n'
        'import sys\n',
    )
    assert before == ''
    assert imports == ['import os\n']
    assert after == (
        '# noreorder\n'
        'import sys\n'
    )
    assert nl == '\n'


def test_partition_source_noreorder_keeps_whitespace_above():
    before, imports, after, nl = partition_source(
        'import os\n'
        '\n'
        '# noreorder\n'
        'import sys\n',
    )
    assert before == ''
    assert imports == ['import os\n']
    assert after == (
        '\n'
        '# noreorder\n'
        'import sys\n'
    )
    assert nl == '\n'


@pytest.mark.parametrize(
    ('imports', 'expected'),
    (
        pytest.param([], [], id='trivial'),
        pytest.param(
            ['import os\n', 'import sys\n'],
            ['import os\n', 'import sys\n'],
            id='nothing to separate',
        ),
        pytest.param(
            ['import os, sys\n'],
            ['import os\n', 'import sys\n'],
            id='separates imports',
        ),
        pytest.param(
            # can't know what the comment points to, so we just remove it
            ['import os, sys  # derp\n'],
            ['import os\n', 'import sys\n'],
            id='separating removes comments',
        ),
        pytest.param(
            ['import sys  # noqa\n'],
            ['import sys  # noqa\n'],
            id='comments preserved when not separating',
        ),
    ),
)
def test_parse_imports(imports, expected):
    parsed = parse_imports(imports, to_add=())
    unparsed = [s for s, _ in parsed]
    assert unparsed == expected


@pytest.mark.parametrize(
    ('imports', 'expected'),
    (
        pytest.param([], [], id='trivial'),
        pytest.param(
            ['import sys\n', 'from six import text_type\n'],
            ['import sys\n', 'from six import text_type\n'],
            id='no duplicates removed',
        ),
        pytest.param(
            ['import sys\n', 'import sys\n'],
            ['import sys\n'],
            id='removes exact duplicate',
        ),
        pytest.param(
            ['import os\n', 'import os.path\n'],
            ['import os.path\n'],
            id='removes redundant import, keeps last',
        ),
        pytest.param(
            ['import os.path\n', 'import os\n'],
            ['import os.path\n'],
            id='removes redundant import, keeps first',
        ),
        pytest.param(
            ['import os\n', 'import os.path as os_path\n'],
            ['import os\n', 'import os.path as os_path\n'],
            id='aliased is not redundant, last aliased',
        ),
        pytest.param(
            ['import os as osmod\n', 'import os.path\n'],
            ['import os as osmod\n', 'import os.path\n'],
            id='aliased is not redundant, first aliased',
        ),
    ),
)
def test_remove_duplicated_imports(imports, expected):
    parsed = parse_imports(imports, to_add=())
    ret = remove_duplicated_imports(parsed, to_remove=set())
    unparsed = [s for s, _ in ret]
    assert unparsed == expected


def test_apply_import_sorting_trivial():
    assert apply_import_sorting(parse_imports([], to_add=())) == []


def test_apply_import_sorting_all_types():
    imports = ['import os\n']
    assert apply_import_sorting(parse_imports(imports, to_add=())) == imports


def test_apply_import_sorting_sorts_imports():
    imports = [
        # local imports
        'from reorder_python_imports import main\n',
        'import reorder_python_imports\n',
        # site-package imports
        'from six import text_type\n',
        'import aspy\n',
        # System imports (out of order)
        'from os import path\n',
        'import os\n',
    ]
    expected = [
        'import os\n',
        'from os import path\n',
        '\n',
        'import aspy\n',
        'from six import text_type\n',
        '\n',
        'import reorder_python_imports\n',
        'from reorder_python_imports import main\n',
    ]
    assert apply_import_sorting(parse_imports(imports, to_add=())) == expected


def test_apply_import_sorting_sorts_imports_with_application_module():
    imports = [
        'import _c_module\n',
        'import reorder_python_imports\n',
        'import third_party\n',
    ]
    expected = [
        'import third_party\n',
        '\n',
        'import _c_module\n',
        'import reorder_python_imports\n',
    ]
    ret = apply_import_sorting(
        parse_imports(imports, to_add=()),
        settings=Settings(unclassifiable_application_modules=('_c_module',)),
    )
    assert ret == expected


def test_apply_import_sorting_maintains_comments():
    imports = ['import foo  # noqa\n']
    assert apply_import_sorting(parse_imports(imports)) == imports


@pytest.mark.parametrize('s', ('', '\n', '\n\n\n'))
def test_add_imports_empty_file(s):
    assert fix_file_contents(
        s,
        to_add=('from __future__ import absolute_import\n',),
        to_remove=set(),
        to_replace=Replacements.make([]),
    ) == ''


def test_add_import_import_already_there():
    assert fix_file_contents(
        'from __future__ import absolute_import\n',
        to_add=('from __future__ import absolute_import\n',),
        to_remove=set(),
        to_replace=Replacements.make([]),
    ) == 'from __future__ import absolute_import\n'


def test_add_import_not_there():
    assert fix_file_contents(
        'import os',
        to_add=('from __future__ import absolute_import\n',),
        to_remove=set(),
        to_replace=Replacements.make([]),
    ) == (
        'from __future__ import absolute_import\n'
        '\n'
        'import os\n'
    )


def test_does_not_put_before_leading_comment():
    assert fix_file_contents(
        '# -*- coding: UTF-8 -*-',
        to_add=('from __future__ import absolute_import\n',),
        to_remove=set(),
        to_replace=Replacements.make([]),
    ) == (
        '# -*- coding: UTF-8 -*-\n'
        'from __future__ import absolute_import\n'
    )


def test_remove_import_trivial():
    assert fix_file_contents(
        '',
        to_add=(),
        to_remove={('__future__', 'with_statement', '')},
        to_replace=Replacements.make([]),
    ) == ''


def test_remove_import_import_not_there():
    assert fix_file_contents(
        'import os\n',
        to_add=(),
        to_remove={('__future__', 'with_statement', '')},
        to_replace=Replacements.make([]),
    ) == 'import os\n'


def test_remove_imports_actually_removes():
    assert fix_file_contents(
        'from __future__ import with_statement\n\n'
        'import os\n',
        to_add=(),
        to_remove={('__future__', 'with_statement', '')},
        to_replace=Replacements.make([]),
    ) == 'import os\n'


def test_replace_imports_noop():
    ret = fix_file_contents(
        'import os\n'
        'import sys\n',
        to_add=(),
        to_remove=set(),
        # import imports are not rewritten
        to_replace=Replacements.make([('os', 'fail', '')]),
    )
    assert ret == 'import os\nimport sys\n'


def test_replace_imports_basic_from():
    ret = fix_file_contents(
        'from foo import bar\n',
        to_add=(),
        to_remove=set(),
        to_replace=Replacements.make([('foo', 'baz', '')]),
    )
    assert ret == 'from baz import bar\n'


def test_replace_imports_relative_module():
    ret = fix_file_contents(
        'from .foo import bar\n',
        to_add=(),
        to_remove=set(),
        to_replace=Replacements.make([('.foo', 'baz', '')]),
    )
    assert ret == 'from baz import bar\n'


def test_replace_imports_from_does_not_replace_name():
    ret = fix_file_contents(
        'from foo import bar\n',
        to_add=(),
        to_remove=set(),
        to_replace=Replacements.make([('foo.bar', 'baz.hi', '')]),
    )
    assert ret == 'from foo import bar\n'


def test_replace_imports_from_asname():
    ret = fix_file_contents(
        'from foo import bar as baz\n',
        to_add=(),
        to_remove=set(),
        to_replace=Replacements.make([('foo', 'baz', '')]),
    )
    assert ret == 'from baz import bar as baz\n'


def test_replace_imports_specific_attribute_name():
    ret = fix_file_contents(
        'from foo import bar\n'
        'from foo import baz\n',
        to_add=(),
        to_remove=set(),
        to_replace=Replacements.make([('foo', 'aaa', 'bar')]),
    )
    assert ret == (
        'from aaa import bar\n'
        'from foo import baz\n'
    )


def test_replace_imports_specific_attribute_name_relative():
    ret = fix_file_contents(
        'from .foo import bar\n',
        to_add=(),
        to_remove=set(),
        to_replace=Replacements.make([('.foo', 'aaa', 'bar')]),
    )
    assert ret == 'from aaa import bar\n'


def test_replace_module_imported():
    ret = fix_file_contents(
        'from six.moves import queue\n',
        to_add=(),
        to_remove=set(),
        to_replace=Replacements.make([('six.moves.queue', 'queue', '')]),
    )
    assert ret == 'import queue\n'


def test_replace_module_imported_asname():
    ret = fix_file_contents(
        'from six.moves import queue as Queue\n',
        to_add=(),
        to_remove=set(),
        to_replace=Replacements.make([('six.moves.queue', 'queue', '')]),
    )
    assert ret == 'import queue as Queue\n'


def test_replace_module_imported_relative():
    ret = fix_file_contents(
        'from .foo import bar as thing\n',
        to_add=(),
        to_remove=set(),
        to_replace=Replacements.make([('.foo.bar', 'womp.baz', '')]),
    )
    assert ret == 'from womp import baz as thing\n'


def test_replace_module_imported_becomes_relative():
    ret = fix_file_contents(
        'from a.b import c as d\n',
        to_add=(),
        to_remove=set(),
        to_replace=Replacements.make([('a.b.c', '.e', '')]),
    )
    assert ret == 'from a.b import c as d\n'


def test_replace_module_imported_with_nested_replacement():
    ret = fix_file_contents(
        'from six.moves.urllib import parse\n',
        to_add=(),
        to_remove=set(),
        to_replace=Replacements.make([
            ('six.moves.urllib.parse', 'urllib.parse', ''),
        ]),
    )
    assert ret == 'from urllib import parse\n'


def test_replace_module_imported_with_nested_replacement_asname():
    ret = fix_file_contents(
        'from six.moves.urllib import parse as urllib_parse\n',
        to_add=(),
        to_remove=set(),
        to_replace=Replacements.make([
            ('six.moves.urllib.parse', 'urllib.parse', ''),
        ]),
    )
    assert ret == 'from urllib import parse as urllib_parse\n'


def test_replace_module_imported_with_nested_renamed_replacement_asname():
    ret = fix_file_contents(
        'from six.moves.urllib import parse as urllib_parse\n',
        to_add=(),
        to_remove=set(),
        to_replace=Replacements.make([
            ('six.moves.urllib.parse', 'urllib.parse2', ''),
        ]),
    )
    assert ret == 'from urllib import parse2 as urllib_parse\n'


def test_replace_module_prefix():
    ret = fix_file_contents(
        'from a.b.c import d\n',
        to_add=(),
        to_remove=set(),
        to_replace=Replacements.make([('a.b', 'e.f', '')]),
    )
    assert ret == 'from e.f.c import d\n'


def test_replace_module_prefix_relative():
    ret = fix_file_contents(
        'from .a.b.c import d\n',
        to_add=(),
        to_remove=set(),
        to_replace=Replacements.make([('.a.b', 'e.f', '')]),
    )
    assert ret == 'from e.f.c import d\n'


def test_replace_module_skips_attr_specific_rules():
    ret = fix_file_contents(
        'from libone import util\n',
        to_add=(),
        to_remove=set(),
        to_replace=Replacements.make([
            ('libone.util', 'libtwo.util', 'is_valid'),
        ]),
    )
    assert ret == 'from libone import util\n'


def test_replace_module_would_make_incorrect_new_import():
    ret = fix_file_contents(
        'from foo import bar\n',
        to_add=(),
        to_remove=set(),
        to_replace=Replacements.make([('foo.bar', 'baz', '')]),
    )
    assert ret == 'from foo import bar\n'


def test_replace_module_skips_nonmatching_rules():
    ret = fix_file_contents(
        'from libthree import util\n',
        to_add=(),
        to_remove=set(),
        to_replace=Replacements.make([('libone.util', 'libtwo.util', '')]),
    )
    assert ret == 'from libthree import util\n'


def test_replace_import_import_with_asname():
    ret = fix_file_contents(
        'import a.b as c\n',
        to_add=(),
        to_remove=set(),
        to_replace=Replacements.make([('a.b', 'd.e', '')]),
    )
    assert ret == 'import d.e as c\n'


def test_replace_import_import_prefix_with_asname():
    ret = fix_file_contents(
        'import a.b as c\n',
        to_add=(),
        to_remove=set(),
        to_replace=Replacements.make([('a', 'd', '')]),
    )
    assert ret == 'import d.b as c\n'


def test_replace_import_unrelated_import_with_asname():
    ret = fix_file_contents(
        'import something.unrelated as c\n',
        to_add=(),
        to_remove=set(),
        to_replace=Replacements.make([('a', 'd', '')]),
    )
    assert ret == 'import something.unrelated as c\n'


cases = pytest.mark.parametrize(
    ('s', 'expected'),
    (
        pytest.param('', '', id='trivial'),
        pytest.param(
            'import os\n'
            "# I'm right after imports\n"
            'x = os.path\n',

            'import os\n'
            "# I'm right after imports\n"
            'x = os.path\n',

            id='code right after imports',
        ),
        pytest.param(
            '# Mostly to demonstrate the (potentially non-ideal) behaviour\n'
            'import os\n\n'
            '# Hello from nomansland\n'
            'import six\n',

            '# Mostly to demonstrate the (potentially non-ideal) behaviour\n'
            'import os\n\n'
            'import six\n'
            '# Hello from nomansland\n',

            id='comment in imports',
        ),
        pytest.param(
            'import os\n'
            '"""misplaced docstring"""\n'
            'import sys\n',

            'import os\n'
            'import sys\n'
            '"""misplaced docstring"""\n',

            id='docstring in imports',
        ),
        pytest.param(
            "# I'm a license comment\n"
            'import os\n',

            "# I'm a license comment\n"
            'import os\n',

            id='license comment',
        ),
        pytest.param(
            'import reorder_python_imports\n'
            'import os\n'
            'import six\n',

            'import os\n\n'
            'import six\n\n'
            'import reorder_python_imports\n',

            id='needs reordering',
        ),
        pytest.param(
            'import sys\n'
            'import os',

            'import os\n'
            'import sys\n',

            id='no eol',
        ),
        pytest.param(
            '# noreorder\n'
            'import reorder_python_imports\n'
            'import os\n'
            'import six\n',

            '# noreorder\n'
            'import reorder_python_imports\n'
            'import os\n'
            'import six\n',

            id='noreorder all',
        ),
        pytest.param(
            'import sys\n'
            'import reorder_python_imports\n\n'
            'import matplotlib # noreorder\n'
            "matplotlib.use('Agg')\n",

            'import sys\n\n'
            'import reorder_python_imports\n\n'
            'import matplotlib # noreorder\n'
            "matplotlib.use('Agg')\n",

            id='noreorder inline',
        ),
        pytest.param(
            'import sys\n'
            'import reorder_python_imports\n\n'
            '# noreorder\n'
            'import matplotlib\n'
            "matplotlib.use('Agg')\n",

            'import sys\n\n'
            'import reorder_python_imports\n\n'
            '# noreorder\n'
            'import matplotlib\n'
            "matplotlib.use('Agg')\n",

            id='noreorder not at beginning',
        ),
        pytest.param(
            'from __future__ import annotations\n'
            '\n'
            'import __future__\n',
            'from __future__ import annotations\n'
            '\n'
            'import __future__\n',
            id='__future__ from and import',
        ),
    ),
)


@cases
def test_fix_file_contents(s, expected):
    ret = fix_file_contents(
        s,
        to_add=(),
        to_remove=set(),
        to_replace=Replacements.make([]),
    )
    assert ret == expected


@cases
def test_integration_main(s, expected, tmpdir):
    test_file = tmpdir.join('test.py')
    test_file.write(s)

    retv = main((str(test_file),))
    # Check return value
    assert retv == int(s != expected)

    # Check the contents rewritten
    assert test_file.read() == expected


@pytest.fixture
def restore_sys_path():
    before = sys.path[:]
    yield
    sys.path[:] = before


@pytest.mark.usefixtures('restore_sys_path')
def test_additional_directories_integration(in_tmpdir):
    if '' in sys.path:  # pragma: no cover (depends on run environment)
        sys.path.remove('')

    # Intentionally avoiding 'tests' and 'testing' because those would clash
    # with the names of this project
    in_tmpdir.join('nottests/nottesting/__init__.py').ensure()

    in_tmpdir.join('foo.py').write(
        'import thirdparty\n'
        'import nottests\n'
        'import nottesting\n',
    )

    # Without the new option
    main(('foo.py',))
    assert in_tmpdir.join('foo.py').read() == (
        'import nottesting\n'
        'import thirdparty\n'
        '\n'
        'import nottests\n'
    )

    # With the new option
    main(('foo.py', '--application-directories', '.:nottests'))
    assert in_tmpdir.join('foo.py').read() == (
        'import thirdparty\n'
        '\n'
        'import nottesting\n'
        'import nottests\n'
    )


def test_fix_crlf():
    s = (
        '"""foo"""\r\n'
        'import os\r\n'
        'import sys\r\n'
        'x = 1\r\n'
    )
    ret = fix_file_contents(
        s,
        to_add=(),
        to_remove=set(),
        to_replace=Replacements.make([]),
    )
    assert ret == s


def test_fix_cr():
    s = (
        '"""foo"""\r'
        'import os\r'
        'import sys\r'
        'x = 1\r'
    )
    ret = fix_file_contents(
        s,
        to_add=(),
        to_remove=set(),
        to_replace=Replacements.make([]),
    )
    assert ret == s


def test_fix_mixed_uses_first_newline():
    s = (
        '"""foo"""\n'
        'import os\r\n'
        'import sys\r\n'
        'x = 1\r\n'
    )
    ret = fix_file_contents(
        s,
        to_add=(),
        to_remove=set(),
        to_replace=Replacements.make([]),
    )
    assert ret == (
        '"""foo"""\n'
        'import os\n'
        'import sys\n'
        'x = 1\n'
    )


@pytest.mark.parametrize(
    ('opt', 'expected'),
    (
        (
            '--py22-plus',
            'from __future__ import unicode_literals\n'
            'from __future__ import with_statement\n\n'
            'from io import open\n',
        ),
        (
            '--py26-plus',
            'from __future__ import unicode_literals\n\n'
            'from io import open\n',
        ),
        (
            '--py3-plus',
            '',
        ),
    ),
)
def test_py_options(tmpdir, opt, expected):
    f = tmpdir.join('f.py')
    f.write(
        'from __future__ import unicode_literals\n'
        'from __future__ import with_statement\n\n'
        'from io import open\n',
    )
    main((str(f), opt))
    assert f.read() == expected


def test_py3_plus_unsixes_imports_rename_module(tmpdir):
    f = tmpdir.join('f.py')
    f.write('from six.moves.urllib.parse import quote_plus\n')
    assert main((str(f), '--py3-plus'))
    assert f.read() == 'from urllib.parse import quote_plus\n'


def test_py3_plus_unsixes_imports_removes_builtins(tmpdir):
    f = tmpdir.join('f.py')
    f.write('from six.moves import range\n')
    assert main((str(f), '--py3-plus'))
    assert f.read() == ''


def test_py3_plus_unsixes_moved_attributes(tmpdir):
    f = tmpdir.join('f.py')
    f.write('from six.moves import reduce\n')
    assert main((str(f), '--py3-plus'))
    assert f.read() == 'from functools import reduce\n'


def test_py3_plus_unsixes_wraps(tmpdir):
    f = tmpdir.join('f.py')
    f.write('from six import wraps\n')
    assert main((str(f), '--py3-plus'))
    assert f.read() == 'from functools import wraps\n'


def test_py3_plus_rewrites_mock(tmpdir):
    f = tmpdir.join('f.py')
    f.write('from mock import ANY\n')
    assert main((str(f), '--py3-plus'))
    assert f.read() == 'from unittest.mock import ANY\n'


def test_py3_plus_rewrites_mock_mock(tmpdir):
    f = tmpdir.join('f.py')
    f.write('from mock.mock import ANY\n')
    assert main((str(f), '--py3-plus'))
    assert f.read() == 'from unittest.mock import ANY\n'


@pytest.mark.xfail(reason='TODO')  # pragma: no cover (assert #2 doesn't run)
def test_py3_plus_rewrites_absolute_mock_to_relative_unittest_mock(tmpdir):
    f = tmpdir.join('f.py')
    f.write('import mock\n')
    assert main((str(f), '--py3-plus'))
    assert f.read() == 'from unittest import mock\n'


def test_py3_plus_does_not_unsix_moves_urllib(tmpdir):
    f = tmpdir.join('f.py')
    f.write('from six.moves import urllib\n')
    assert not main((str(f), '--py3-plus'))
    assert f.read() == 'from six.moves import urllib\n'


def test_py3_plus_does_not_rewrite_mock_version_info(tmpdir):
    f = tmpdir.join('f.py')
    f.write('from mock import version_info\n')
    assert not main((str(f), '--py3-plus'))
    assert f.read() == 'from mock import version_info\n'


def test_py3_plus_rewrites_collections_abc(tmpdir):
    f = tmpdir.join('f.py')
    f.write('from collections import Mapping\n')
    assert main((str(f), '--py3-plus'))
    assert f.read() == 'from collections.abc import Mapping\n'


def test_py3_plus_rewrites_cElementTree(tmpdir):
    f = tmpdir.join('f.py')
    f.write('from xml.etree.cElementTree import ElementTree\n')
    assert main((str(f), '--py3-plus'))
    assert f.read() == 'from xml.etree.ElementTree import ElementTree\n'


def test_py3_plus_rewrites_pipes_quote(tmpdir):
    f = tmpdir.join('f.py')
    f.write('from pipes import quote\n')
    assert main((str(f), '--py3-plus'))
    assert f.read() == 'from shlex import quote\n'


def test_py3_plus_removes_python_future_imports(tmpdir):
    f = tmpdir.join('f.py')
    f.write('from builtins import str\n')
    assert main((str(f), '--py3-plus'))
    assert f.read() == ''


def test_py3_plus_removes_builtins_star_import(tmpdir):
    f = tmpdir.join('f.py')
    f.write('from builtins import *')
    assert main((str(f), '--py3-plus'))
    assert f.read() == ''


def test_py37_plus_rewrites_typing_extensions_import(tmpdir):
    f = tmpdir.join('f.py')
    f.write('from typing_extensions import Deque\n')
    assert main((str(f), '--py37-plus'))
    assert f.read() == 'from typing import Deque\n'


def test_py38_plus_rewrites_mypy_extensions_import(tmpdir):
    f = tmpdir.join('f.py')
    f.write('from mypy_extensions import TypedDict\n')
    assert main((str(f), '--py38-plus'))
    assert f.read() == 'from typing import TypedDict\n'


def test_py39_plus_rewrites_pep585_imports(tmpdir):
    f = tmpdir.join('f.py')
    f.write('from typing import Sequence\n')
    assert main((str(f), '--py39-plus'))
    assert f.read() == 'from collections.abc import Sequence\n'


def test_typing_callable_not_rewritten_until_3_10(tmpdir):
    f = tmpdir.join('f.py')
    f.write('from typing import Callable\n')
    assert not main((str(f), '--py39-plus'))
    assert main((str(f), '--py310-plus'))
    assert f.read() == 'from collections.abc import Callable\n'


@pytest.mark.parametrize('opt', ('--add-import', '--remove-import'))
@pytest.mark.parametrize('s', ('syntax error', '"import os"'))
def test_invalid_add_remove_syntaxes(tmpdir, capsys, opt, s):
    f = tmpdir.join('f.py')
    f.write('import os\n')
    with pytest.raises(SystemExit) as excinfo:
        main((str(f), opt, s))
    retc, = excinfo.value.args
    assert retc
    out = ''.join(capsys.readouterr())
    assert f'{opt}: expected import: {s!r}' in out


def test_can_add_multiple_imports_at_once(tmpdir):
    f = tmpdir.join('f.py')
    f.write('import argparse')
    assert main((str(f), '--add-import', 'import os, sys'))
    assert f.read() == 'import argparse\nimport os\nimport sys\n'


def test_can_remove_multiple_at_once(tmpdir):
    f = tmpdir.join('f.py')
    f.write('import argparse\nimport os\nimport sys\n')
    assert main((str(f), '--remove-import', 'import os, sys'))
    assert f.read() == 'import argparse\n'


def test_replace_module(tmpdir):
    f = tmpdir.join('f.py')
    f.write('from six.moves.urllib.parse import quote_plus\n')
    assert main((
        str(f), '--replace-import', 'six.moves.urllib.parse=urllib.parse',
    ))
    assert f.read() == 'from urllib.parse import quote_plus\n'


@pytest.mark.parametrize('s', ('invalid', 'too=many=equals'))
def test_replace_module_invalid_arg(tmpdir, capsys, s):
    f = tmpdir.join('f.py')
    f.write('import os\n')
    with pytest.raises(SystemExit) as excinfo:
        main((str(f), '--replace-import', s))
    retc, = excinfo.value.args
    assert retc
    out = ''.join(capsys.readouterr())
    expected = (
        f'--replace-import: expected `orig.mod=new.mod` or '
        f'`orig.mod=new.mod:attr`: {s!r}'
    )
    assert expected in out


def test_unreadable_files_print_filename(tmpdir, capsys):
    f = tmpdir.join('f.py')
    f.write_binary(b'\x98\xef\x12...')
    filename = str(f)
    assert main([filename])
    _, err = capsys.readouterr()
    assert filename in err


def test_main_stdin_fix_basic(capsys):
    input_b = b'import sys\nimport os\n'
    stdin = io.TextIOWrapper(io.BytesIO(input_b), 'UTF-8')
    with mock.patch.object(sys, 'stdin', stdin):
        assert main(('-',)) == 1
    out, err = capsys.readouterr()
    assert out == 'import os\nimport sys\n'


def test_main_stdin_no_fix(capsys):
    input_b = b'import os\nimport sys\n'
    stdin = io.TextIOWrapper(io.BytesIO(input_b), 'UTF-8')
    with mock.patch.object(sys, 'stdin', stdin):
        assert main(('-',)) == 0
    out, err = capsys.readouterr()
    assert out == 'import os\nimport sys\n'


def test_main_exit_code_multiple_files(tmpdir):
    f1 = tmpdir.join('t1.py')
    f1.write('import os,sys\n')
    f2 = tmpdir.join('t2.py').ensure()
    assert main((str(f1), str(f2)))


def test_exit_zero_even_if_changed(tmpdir):
    f = tmpdir.join('t.py')
    f.write('import os,sys')
    assert not main((str(f), '--exit-zero-even-if-changed'))
    assert f.read() == 'import os\nimport sys\n'
    assert not main((str(f), '--exit-zero-even-if-changed'))


def test_success_messages_are_printed_on_stderr(tmpdir, capsys):
    f = tmpdir.join('f.py')
    f.write('import os,sys')
    main((str(f),))
    out, err = capsys.readouterr()
    assert err == f'Reordering imports in {f}\n'
    assert out == ''


def test_warning_pythonpath(tmpdir, capsys):
    f = tmpdir.join('f.py').ensure()
    with mock.patch.dict(os.environ, {'PYTHONPATH': str(tmpdir)}):
        main((str(f),))
    out, err = capsys.readouterr()
    assert err == '$PYTHONPATH set, import order may be unexpected\n'
    assert out == ''
