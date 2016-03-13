# -*- coding: UTF-8 -*-
from __future__ import absolute_import
from __future__ import unicode_literals

import ast
import io
import os.path
import subprocess
import sys

import pytest
from reorder_python_imports.main import apply_import_sorting
from reorder_python_imports.main import CodePartition
from reorder_python_imports.main import CodeType
from reorder_python_imports.main import fix_file_contents
from reorder_python_imports.main import get_line_offsets_by_line_no
from reorder_python_imports.main import main
from reorder_python_imports.main import partition_source
from reorder_python_imports.main import remove_duplicated_imports
from reorder_python_imports.main import separate_comma_imports
from reorder_python_imports.main import TopLevelImportVisitor


def test_partition_source_trivial():
    assert partition_source('') == []


def test_partition_source_errors_with_bytes():
    with pytest.raises(TypeError):
        partition_source(b'')


def test_partition_source_shebang():
    assert partition_source('#!/usr/bin/env python\n') == [
        CodePartition(CodeType.PRE_IMPORT_CODE, '#!/usr/bin/env python\n'),
    ]


def test_partition_source_shebang_no_nl():
    assert partition_source('#!/usr/bin/env python') == [
        CodePartition(CodeType.PRE_IMPORT_CODE, '#!/usr/bin/env python'),
    ]


def test_partition_source_encoding():
    assert partition_source('# -*- coding: UTF-8 -*-\n') == [
        CodePartition(CodeType.PRE_IMPORT_CODE, '# -*- coding: UTF-8 -*-\n'),
    ]


def test_partition_source_encoding_no_nl():
    assert partition_source('# -*- coding: UTF-8 -*-') == [
        CodePartition(CodeType.PRE_IMPORT_CODE, '# -*- coding: UTF-8 -*-'),
    ]


def test_partition_source_indented_encoding():
    assert partition_source('   # -*- coding: UTF-8 -*-\n') == [
        CodePartition(
            CodeType.PRE_IMPORT_CODE,
            '   # -*- coding: UTF-8 -*-\n',
        ),
    ]


def test_partition_source_encoding_and_shebang():
    assert partition_source(
        '#!/usr/bin/env python\n'
        '# -*- coding: UTF-8 -*-\n'
    ) == [
        CodePartition(CodeType.PRE_IMPORT_CODE, '#!/usr/bin/env python\n'),
        CodePartition(CodeType.PRE_IMPORT_CODE, '# -*- coding: UTF-8 -*-\n'),
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
        CodePartition(CodeType.PRE_IMPORT_CODE, '"""foo"""\n'),
    ]


def test_partition_source_docstring_no_nl():
    assert partition_source('"""foo"""') == [
        CodePartition(CodeType.PRE_IMPORT_CODE, '"""foo"""'),
    ]


def test_partition_source_multiple_docstrings():
    assert partition_source(
        '"""foo"""\n'
        '"""bar"""\n'
    ) == [
        # only the first docstring should count as a docstring
        CodePartition(CodeType.PRE_IMPORT_CODE, '"""foo"""\n'),
        CodePartition(CodeType.PRE_IMPORT_CODE, '"""bar"""\n'),
    ]


def test_partition_source_unicode_docstring():
    assert partition_source(
        '# -*- coding: UTF-8 -*-\n'
        'u"""☃☃☃"""\n'
    ) == [
        CodePartition(CodeType.PRE_IMPORT_CODE, '# -*- coding: UTF-8 -*-\n'),
        CodePartition(CodeType.PRE_IMPORT_CODE, 'u"""☃☃☃"""\n'),
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
        CodePartition(CodeType.PRE_IMPORT_CODE, '# hello world\n'),
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


def test_separate_comma_imports_trivial():
    assert separate_comma_imports([]) == []


def test_separate_comma_imports_none_to_separate():
    input_partitions = [
        CodePartition(CodeType.IMPORT, 'import os\n'),
        CodePartition(CodeType.NON_CODE, '\n'),
        CodePartition(CodeType.IMPORT, 'import six\n'),
    ]
    assert separate_comma_imports(input_partitions) == input_partitions


def test_separate_comma_imports_separates_some():
    assert separate_comma_imports([
        CodePartition(CodeType.IMPORT, 'import os, sys\n'),
    ]) == [
        CodePartition(CodeType.IMPORT, 'import os\n'),
        CodePartition(CodeType.IMPORT, 'import sys\n'),
    ]


def test_separate_comma_imports_removes_comments():
    # Since it's not really possible to know what the comma points to, we just
    # remove it
    assert separate_comma_imports([
        CodePartition(CodeType.IMPORT, 'import os, sys  # derp\n'),
    ]) == [
        CodePartition(CodeType.IMPORT, 'import os\n'),
        CodePartition(CodeType.IMPORT, 'import sys\n'),
    ]


def test_separate_comma_imports_does_not_remove_comments_when_not_splitting():
    input_partitions = [CodePartition(CodeType.IMPORT, 'import sys  # noqa\n')]
    assert separate_comma_imports(input_partitions) == input_partitions


def test_remove_duplicated_imports_trivial():
    assert remove_duplicated_imports([]) == []


def test_remove_duplicated_imports_no_dupes_no_removals():
    input_partitions = [
        CodePartition(CodeType.IMPORT, 'import sys\n'),
        CodePartition(CodeType.NON_CODE, '\n'),
        CodePartition(CodeType.IMPORT, 'from six import text_type\n'),
    ]
    assert remove_duplicated_imports(input_partitions) == input_partitions


def test_remove_duplicated_imports_removes_duplicated():
    assert remove_duplicated_imports([
        CodePartition(CodeType.IMPORT, 'import sys\n'),
        CodePartition(CodeType.IMPORT, 'import sys\n'),
    ]) == [
        CodePartition(CodeType.IMPORT, 'import sys\n'),
    ]


def test_apply_import_sorting_trivial():
    assert apply_import_sorting([]) == []


def test_apply_import_sorting_all_types():
    input_partitions = [
        CodePartition(CodeType.PRE_IMPORT_CODE, '#!/usr/bin/env python\n'),
        CodePartition(CodeType.PRE_IMPORT_CODE, '# -*- coding: UTF-8 -*-\n'),
        CodePartition(CodeType.PRE_IMPORT_CODE, '"""foo"""\n'),
        CodePartition(CodeType.IMPORT, 'import os\n'),
        CodePartition(CodeType.CODE, '\n\nx = 5\n'),
    ]
    assert apply_import_sorting(input_partitions) == input_partitions


def test_apply_import_sorting_sorts_imports():
    assert apply_import_sorting([
        # local imports
        CodePartition(
            CodeType.IMPORT, 'from reorder_python_imports import main\n',
        ),
        CodePartition(CodeType.IMPORT, 'import reorder_python_imports\n'),
        # site-package imports
        CodePartition(CodeType.IMPORT, 'from six import text_type\n'),
        CodePartition(CodeType.IMPORT, 'import aspy\n'),
        # System imports (out of order)
        CodePartition(CodeType.IMPORT, 'from os import path\n'),
        CodePartition(CodeType.IMPORT, 'import os\n'),
    ]) == [
        CodePartition(CodeType.IMPORT, 'import os\n'),
        CodePartition(CodeType.IMPORT, 'from os import path\n'),
        CodePartition(CodeType.NON_CODE, '\n'),
        CodePartition(CodeType.IMPORT, 'import aspy\n'),
        CodePartition(CodeType.IMPORT, 'from six import text_type\n'),
        CodePartition(CodeType.NON_CODE, '\n'),
        CodePartition(CodeType.IMPORT, 'import reorder_python_imports\n'),
        CodePartition(
            CodeType.IMPORT, 'from reorder_python_imports import main\n',
        ),
    ]


def test_apply_import_sorting_maintains_comments():
    input_partitions = [
        CodePartition(CodeType.IMPORT, 'import foo  # noqa\n'),
    ]
    assert apply_import_sorting(input_partitions) == input_partitions


def test_apply_import_sorting_removes_padding_if_only_imports():
    assert apply_import_sorting([
        CodePartition(CodeType.IMPORT, 'import foo\n'),
        CodePartition(CodeType.NON_CODE, '\n\n'),
    ]) == [
        CodePartition(CodeType.IMPORT, 'import foo\n'),
    ]


def test_add_import_trivial():
    assert fix_file_contents(
        '',
        imports_to_add=('from __future__ import absolute_import',),
    ) == ''


def test_add_import_import_already_there():
    assert fix_file_contents(
        'from __future__ import absolute_import\n',
        imports_to_add=('from __future__ import absolute_import',),
    ) == 'from __future__ import absolute_import\n'


def test_add_import_not_there():
    assert fix_file_contents(
        'import os',
        imports_to_add=('from __future__ import absolute_import',),
    ) == (
        'from __future__ import absolute_import\n'
        '\n'
        'import os\n'
    )


def test_does_not_put_before_leading_comment():
    assert fix_file_contents(
        '# -*- coding: UTF-8 -*-',
        imports_to_add=('from __future__ import absolute_import',),
    ) == (
        '# -*- coding: UTF-8 -*-\n'
        'from __future__ import absolute_import\n'
    )


def test_remove_import_trivial():
    assert fix_file_contents(
        '',
        imports_to_remove=('from __future__ import with_statement',),
    ) == ''


def test_remove_import_import_not_there():
    assert fix_file_contents(
        'import os\n',
        imports_to_remove=('from __future__ import with_statement',),
    ) == 'import os\n'


def test_remove_imports_actually_removes():
    assert fix_file_contents(
        'from __future__ import with_statement\n\n'
        'import os\n',
        imports_to_remove=('from  __future__ import with_statement',),
    ) == 'import os\n'


tfiles = pytest.mark.parametrize('filename', os.listdir('test_data/inputs'))


@tfiles
def test_fix_file_contents(filename):
    input_contents = io.open(os.path.join('test_data/inputs', filename)).read()
    expected = io.open(os.path.join('test_data/outputs', filename)).read()
    assert fix_file_contents(input_contents) == expected


@tfiles
def test_integration_main(filename, tmpdir):
    input_contents = io.open(os.path.join('test_data/inputs', filename)).read()
    expected = io.open(os.path.join('test_data/outputs', filename)).read()

    test_file_path = tmpdir.join('test.py').strpath
    with io.open(test_file_path, 'w') as f:
        f.write(input_contents)

    # Check return value with --diff-only
    retv_diff = main((test_file_path, '--diff-only'))
    assert retv_diff == int(input_contents != expected)

    retv = main((test_file_path,))
    # Check return value
    assert retv == int(input_contents != expected)

    # Check the contents rewritten
    assert io.open(test_file_path).read() == expected


def test_integration_main_stdout(capsys):
    ret = main(('--print-only', 'test_data/inputs/needs_reordering.py'))
    assert ret == 1
    out, err = capsys.readouterr()
    assert out == 'import os\n\nimport six\n\nimport reorder_python_imports\n'
    assert err == '==> test_data/inputs/needs_reordering.py <==\n'


def _apply_patch(patch):
    patch_proc = subprocess.Popen(('patch',), stdin=subprocess.PIPE)
    patch_proc.communicate(patch.encode('UTF-8'))
    assert patch_proc.returncode == 0


def test_does_not_reorder_with_diff_only(in_tmpdir, capsys):
    test_file_path = in_tmpdir.join('test.py').strpath
    original_contents = 'import sys\nimport os\n'
    with io.open(test_file_path, 'w') as test_file:
        test_file.write(original_contents)

    retv = main((test_file_path, '--diff-only'))
    assert retv == 1
    assert io.open(test_file_path).read() == original_contents
    patch, _ = capsys.readouterr()
    _apply_patch(patch)
    assert io.open(test_file_path).read() == 'import os\nimport sys\n'


def test_patch_multiple_files_no_eol(in_tmpdir, capsys):
    test1filename = in_tmpdir.join('test1.py').strpath
    test2filename = in_tmpdir.join('test2.py').strpath
    with io.open(test1filename, 'w') as test1:
        # Intentionally no EOL
        test1.write('import sys\nimport os')

    with io.open(test2filename, 'w') as test2:
        test2.write('import sys\nimport os\n')

    ret = main((test1filename, test2filename, '--diff-only'))
    assert ret == 1
    patch, _ = capsys.readouterr()
    _apply_patch(patch)
    assert io.open(test1filename).read() == 'import os\nimport sys\n'
    assert io.open(test2filename).read() == 'import os\nimport sys\n'


@pytest.yield_fixture
def restore_sys_path():
    before = sys.path[:]
    yield
    sys.path[:] = before


@pytest.mark.usefixtures('in_tmpdir', 'restore_sys_path')
def test_additional_directories_integration():
    if '' in sys.path:  # pragma: no cover (depends on run environment)
        sys.path.remove('')

    # Intentionally avoiding 'tests' and 'testing' because those would clash
    # with the names of this project
    os.makedirs('nottests/nottesting')
    io.open('nottests/nottesting/__init__.py', 'w').close()

    with io.open('foo.py', 'w') as foo_file:
        foo_file.write(
            'import thirdparty\n'
            'import nottests\n'
            'import nottesting\n'
        )

    # Without the new option
    main(('foo.py',))
    assert io.open('foo.py').read() == (
        'import nottesting\n'
        'import thirdparty\n'
        '\n'
        'import nottests\n'
    )

    # With the new option
    main(('foo.py', '--application-directories', '.:nottests'))
    assert io.open('foo.py').read() == (
        'import thirdparty\n'
        '\n'
        'import nottesting\n'
        'import nottests\n'
    )
