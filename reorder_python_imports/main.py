from __future__ import absolute_import
from __future__ import print_function
from __future__ import unicode_literals

import ast
import collections
import io
import re
import tokenize

import six


ENCODING_RE = re.compile(r"^\s*#.*coding[:=]\s*([-\w.]+)")


class CodeType(object):
    SHEBANG = 'shebang'
    ENCODING = 'encoding'
    DOCSTRING = 'docstring'
    IMPORT = 'import'
    NON_CODE = 'non_code'
    CODE = 'code'


CodePartition = collections.namedtuple('CodePartition', ('code_type', 'src'))


TERMINATES_COMMENT = frozenset((tokenize.NL, tokenize.ENDMARKER))
TERMINATES_DOCSTRING = frozenset((tokenize.NEWLINE, tokenize.ENDMARKER))
TERMINATES_IMPORT = frozenset((tokenize.NEWLINE, tokenize.ENDMARKER))
TERMINATES_CODE = frozenset((tokenize.ENDMARKER,))


class TopLevelImportVisitor(ast.NodeVisitor):
    def __init__(self):
        self.top_level_import_line_numbers = []

    def _visit_import(self, node):
        # If it's indented, we don't really care about the import.
        if node.col_offset == 0:
            self.top_level_import_line_numbers.append(node.lineno)

    visit_Import = visit_ImportFrom = _visit_import


def get_line_offsets_by_line_no(src):
    # Padded so we can index with line number
    offsets = [None, 0]
    for line in src.splitlines():
        offsets.append(offsets[-1] + len(line) + 1)
    return offsets


def partition_source(src):
    """Partitions source into a list of `CodePartition`s for import
    refactoring.
    """
    # pylint:disable=too-many-locals
    if type(src) is not six.text_type:
        raise TypeError('Expected text but got `{0}`'.format(type(src)))

    # In python2, ast.parse(text_string_with_encoding_pragma) raises
    # SyntaxError: encoding declaration in Unicode string
    # We'll encode arbitrarily to UTF-8, though it's incorrect in some cases
    # for things like strings and comments, we're really only looking for the
    # start token for imports, which are ascii.
    ast_obj = ast.parse(src.encode('UTF-8'))
    visitor = TopLevelImportVisitor()
    visitor.visit(ast_obj)

    line_offsets = get_line_offsets_by_line_no(src)

    chunks = []
    startpos = 0
    pending_chunk_type = None
    possible_ending_tokens = None
    for (
            token_type, token_s, (srow, scol), (erow, ecol), _,
    ) in tokenize.generate_tokens(io.StringIO(src).readline):
        # Searching for a start of a chunk
        if pending_chunk_type is None:
            if (
                    token_type == tokenize.COMMENT and
                    srow == 1 and
                    token_s.startswith('#!')
            ):
                pending_chunk_type = CodeType.SHEBANG
                possible_ending_tokens = TERMINATES_COMMENT
            elif (
                    token_type == tokenize.COMMENT and
                    srow in (1, 2) and
                    ENCODING_RE.match(token_s)
            ):
                pending_chunk_type = CodeType.ENCODING
                possible_ending_tokens = TERMINATES_COMMENT
            elif scol == 0 and srow in visitor.top_level_import_line_numbers:
                pending_chunk_type = CodeType.IMPORT
                possible_ending_tokens = TERMINATES_IMPORT
            elif scol == 0 and token_type == tokenize.STRING:
                pending_chunk_type = CodeType.DOCSTRING
                possible_ending_tokens = TERMINATES_DOCSTRING
            elif token_type == tokenize.NL:
                # A NL token is a non-important newline, we'll immediately
                # append a MOVABLE_CODE partition
                endpos = line_offsets[erow] + ecol
                srctext = src[startpos:endpos]
                startpos = endpos
                chunks.append(CodePartition(CodeType.NON_CODE, srctext))
            elif token_type == tokenize.COMMENT:
                pending_chunk_type = CodeType.NON_CODE
                possible_ending_tokens = TERMINATES_COMMENT
            elif token_type == tokenize.ENDMARKER:
                # Token ended right before end of file or file was empty
                pass
            else:
                pending_chunk_type = CodeType.CODE
                possible_ending_tokens = TERMINATES_CODE
        # Attempt to find ending of token
        elif token_type in possible_ending_tokens:
            endpos = line_offsets[erow] + ecol
            srctext = src[startpos:endpos]
            startpos = endpos
            chunks.append(CodePartition(pending_chunk_type, srctext))
            pending_chunk_type = None
            possible_ending_tokens = None

    # Make sure we're not removing any code
    assert ''.join(partition.src for partition in chunks) == src
    return chunks


if __name__ == '__main__':
    import sys
    assert len(sys.argv) == 2
    contents = io.open(sys.argv[1]).read()
    print('<document>', end='')
    for partition in partition_source(contents):
        print(
            '<{0}>{1}</{0}>'.format(
                partition.code_type, partition.src,
            ),
            end='',
        )
    print('</document>')
