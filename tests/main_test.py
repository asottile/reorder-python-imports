# -*- coding: UTF-8 -*-
from __future__ import absolute_import
from __future__ import unicode_literals

import ast

import pytest

from reorder_python_imports.main import CodePartition
from reorder_python_imports.main import CodeType
from reorder_python_imports.main import get_line_offsets_by_line_no
from reorder_python_imports.main import partition_source
from reorder_python_imports.main import TopLevelImportVisitor


def test_partition_source_trivial():
    assert partition_source('') == []


def test_partition_source_errors_with_bytes():
    with pytest.raises(TypeError):
        partition_source(b'')


def test_partition_source_shebang():
    assert partition_source('#!/usr/bin/env python\n') == [
        CodePartition(CodeType.SHEBANG, '#!/usr/bin/env python\n'),
    ]


def test_partiiton_source_shebang_no_nl():
    assert partition_source('#!/usr/bin/env python') == [
        CodePartition(CodeType.SHEBANG, '#!/usr/bin/env python'),
    ]


def test_partition_source_encoding():
    assert partition_source('# -*- coding: UTF-8 -*-\n') == [
        CodePartition(CodeType.ENCODING, '# -*- coding: UTF-8 -*-\n'),
    ]


def test_partition_source_encoding_no_nl():
    assert partition_source('# -*- coding: UTF-8 -*-') == [
        CodePartition(CodeType.ENCODING, '# -*- coding: UTF-8 -*-'),
    ]


def test_partition_source_indented_encoding():
    assert partition_source('   # -*- coding: UTF-8 -*-\n') == [
        CodePartition(CodeType.ENCODING, '   # -*- coding: UTF-8 -*-\n'),
    ]


def test_partition_source_encoding_and_shebang():
    assert partition_source(
        '#!/usr/bin/env python\n'
        '# -*- coding: UTF-8 -*-\n'
    ) == [
        CodePartition(CodeType.SHEBANG, '#!/usr/bin/env python\n'),
        CodePartition(CodeType.ENCODING, '# -*- coding: UTF-8 -*-\n'),
    ]


def test_partition_source_import():
    assert partition_source('import os\n') == [
        CodePartition(CodeType.IMPORT, 'import os\n'),
    ]


def test_partion_source_import_no_nl():
    assert partition_source('import os') == [
        CodePartition(CodeType.IMPORT, 'import os'),
    ]


def test_partition_source_import_contains_comment():
    # We want to maintain comments with imports
    assert partition_source('from foo import *  # noqa\n') == [
        CodePartition(CodeType.IMPORT, 'from foo import *  # noqa\n'),
    ]


def test_partition_source_import_inside_code_not_an_import():
    assert partition_source('x = 1\nimport os\n') == [
        CodePartition(CodeType.CODE, 'x = 1\nimport os\n'),
    ]


def test_partition_source_docstring():
    assert partition_source('"""foo"""\n') == [
        CodePartition(CodeType.DOCSTRING, '"""foo"""\n'),
    ]


def test_partition_source_docstring_no_nl():
    assert partition_source('"""foo"""') == [
        CodePartition(CodeType.DOCSTRING, '"""foo"""'),
    ]


def test_partition_source_unicode_docstring():
    assert partition_source(
        '# -*- coding: UTF-8 -*-\n'
        'u"""☃☃☃"""\n'
    ) == [
        CodePartition(CodeType.ENCODING, '# -*- coding: UTF-8 -*-\n'),
        CodePartition(CodeType.DOCSTRING, 'u"""☃☃☃"""\n'),
    ]


def test_partition_source_blank_lines_with_whitespace():
    assert partition_source(
        'import os\n'
        '\n'
        '    \n'
        'import sys\n'
    ) == [
        CodePartition(CodeType.IMPORT, 'import os\n'),
        CodePartition(CodeType.NON_CODE, '\n'),
        CodePartition(CodeType.NON_CODE, '    \n'),
        CodePartition(CodeType.IMPORT, 'import sys\n'),
    ]


def test_partition_source_code():
    assert partition_source('x = 1\n') == [
        CodePartition(CodeType.CODE, 'x = 1\n'),
    ]


def test_partition_source_code_no_nl():
    assert partition_source('x = 1') == [
        CodePartition(CodeType.CODE, 'x = 1'),
    ]


def test_partition_source_comment_lines():
    assert partition_source(
        '# hello world\n'
        'import os\n'
    ) == [
        CodePartition(CodeType.NON_CODE, '# hello world\n'),
        CodePartition(CodeType.IMPORT, 'import os\n'),
    ]


def _src_to_import_lines(src):
    ast_obj = ast.parse(src)
    visitor = TopLevelImportVisitor()
    visitor.visit(ast_obj)
    return visitor.top_level_import_line_numbers


def test_import_visitor_trivial():
    assert _src_to_import_lines(b'') == []


def test_import_visitor_simple_import():
    ret = _src_to_import_lines(
        b'import foo\n'
        b'#something else\n'
    )
    assert ret == [1]


def test_import_visitor_simple_import_2():
    ret = _src_to_import_lines(
        b'# -*- coding: utf-8 -*-\n'
        b'import os\n'
    )
    assert ret == [2]


def test_import_visitor_multiple_imports():
    ret = _src_to_import_lines(
        b'import os\n'
        b'import sys\n'
    )
    assert ret == [1, 2]


def test_import_visitor_ignores_indented_imports():
    ret = _src_to_import_lines(
        b'if True:\n'
        b'    import os\n'
    )
    assert ret == []


def test_line_offsets_trivial():
    assert get_line_offsets_by_line_no('') == [None, 0]


def test_line_offsets_no_eof_newline():
    assert get_line_offsets_by_line_no('hello') == [None, 0, 6]


def test_line_offsets_eof_newline():
    assert get_line_offsets_by_line_no('hello\n') == [None, 0, 6]


def test_line_offsets_multiple_lines():
    src = 'hello\nworld\n'
    ret = get_line_offsets_by_line_no(src)
    assert ret == [None, 0, 6, 12]
    # To demonstrate how it is used: acquire everything from line 2 onwards
    assert src[ret[2]:] == 'world\n'
