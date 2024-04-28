from textwrap import dedent

from reorder_python_imports import fix_file_contents
from reorder_python_imports import Replacements


def test_leaves_newline_between_docstring_and_imports():
    contents = dedent('''\
        """module docstring"""

        import foo
    ''')
    expected = contents
    actual = fix_file_contents(contents, to_remove=set(), to_replace=Replacements.make([]))
    assert actual == expected
