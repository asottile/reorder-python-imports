from __future__ import absolute_import
from __future__ import print_function
from __future__ import unicode_literals

import argparse
import ast
import collections
import difflib
import functools
import io
import sys
import tokenize

import six
from aspy.refactor_imports.import_obj import import_obj_from_str
from aspy.refactor_imports.sort import sort


class CodeType(object):
    PRE_IMPORT_CODE = 'pre_import_code'
    IMPORT = 'import'
    NON_CODE = 'non_code'
    CODE = 'code'


CodePartition = collections.namedtuple('CodePartition', ('code_type', 'src'))


TERMINATES_COMMENT = frozenset((tokenize.NL, tokenize.ENDMARKER))
TERMINATES_DOCSTRING = frozenset((tokenize.NEWLINE, tokenize.ENDMARKER))
TERMINATES_IMPORT = frozenset((tokenize.NEWLINE, tokenize.ENDMARKER))


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


def _partitions_to_src(partitions):
    return ''.join(part.src for part in partitions)


def partition_source(src):
    """Partitions source into a list of `CodePartition`s for import
    refactoring.
    """
    # pylint:disable=too-many-branches,too-many-locals,too-many-statements
    if type(src) is not six.text_type:
        raise TypeError('Expected text but got `{}`'.format(type(src)))

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
    seen_import = False
    for (
            token_type, token_text, (srow, scol), (erow, ecol), _,
    ) in tokenize.generate_tokens(io.StringIO(src).readline):
        # Searching for a start of a chunk
        if pending_chunk_type is None:
            if not seen_import and token_type == tokenize.COMMENT:
                if 'noreorder' in token_text:
                    chunks.append(CodePartition(CodeType.CODE, src[startpos:]))
                    break
                else:
                    pending_chunk_type = CodeType.PRE_IMPORT_CODE
                    possible_ending_tokens = TERMINATES_COMMENT
            elif not seen_import and token_type == tokenize.STRING:
                pending_chunk_type = CodeType.PRE_IMPORT_CODE
                possible_ending_tokens = TERMINATES_DOCSTRING
            elif scol == 0 and srow in visitor.top_level_import_line_numbers:
                seen_import = True
                pending_chunk_type = CodeType.IMPORT
                possible_ending_tokens = TERMINATES_IMPORT
            elif token_type == tokenize.NL:
                # A NL token is a non-important newline, we'll immediately
                # append a NON_CODE partition
                endpos = line_offsets[erow] + ecol
                srctext = src[startpos:endpos]
                startpos = endpos
                chunks.append(CodePartition(CodeType.NON_CODE, srctext))
            elif token_type == tokenize.COMMENT:
                if 'noreorder' in token_text:
                    chunks.append(CodePartition(CodeType.CODE, src[startpos:]))
                    break
                else:
                    pending_chunk_type = CodeType.CODE
                    possible_ending_tokens = TERMINATES_COMMENT
            elif token_type == tokenize.ENDMARKER:
                # Token ended right before end of file or file was empty
                pass
            else:
                # We've reached a `CODE` block, which spans the rest of the
                # file (intentionally timid).  Let's append that block and be
                # done
                chunks.append(CodePartition(CodeType.CODE, src[startpos:]))
                break
        # Attempt to find ending of token
        elif token_type in possible_ending_tokens:
            endpos = line_offsets[erow] + ecol
            srctext = src[startpos:endpos]
            startpos = endpos
            chunks.append(CodePartition(pending_chunk_type, srctext))
            pending_chunk_type = None
            possible_ending_tokens = None
        elif token_type == tokenize.COMMENT and 'noreorder' in token_text:
            chunks.append(CodePartition(CodeType.CODE, src[startpos:]))
            break

    # Make sure we're not removing any code
    assert _partitions_to_src(chunks) == src
    return chunks


def combine_trailing_code_chunks(partitions):
    chunks = list(partitions)

    NON_COMBINABLE = (CodeType.IMPORT, CodeType.PRE_IMPORT_CODE)
    if chunks and chunks[-1].code_type not in NON_COMBINABLE:
        src = chunks.pop().src
        while chunks and chunks[-1].code_type not in NON_COMBINABLE:
            src = chunks.pop().src + src

        chunks.append(CodePartition(CodeType.CODE, src))
    return chunks


def separate_comma_imports(partitions):
    """Turns `import a, b` into `import a` and `import b`"""
    def _inner():
        for partition in partitions:
            if partition.code_type is CodeType.IMPORT:
                import_obj = import_obj_from_str(partition.src)
                if import_obj.has_multiple_imports:
                    for new_import_obj in import_obj.split_imports():
                        yield CodePartition(
                            CodeType.IMPORT, new_import_obj.to_text()
                        )
                else:
                    yield partition
            else:
                yield partition

    return list(_inner())


def add_imports(partitions, to_add=()):
    partitions = list(partitions)
    if not _partitions_to_src(partitions).strip():
        return partitions

    # If we don't have a trailing newline, this refactor is wrong
    if not partitions[-1].src.endswith('\n'):
        partitions[-1] = CodePartition(
            partitions[-1].code_type,
            partitions[-1].src + '\n',
        )

    return partitions + [
        CodePartition(CodeType.IMPORT, imp_statement.strip() + '\n')
        for imp_statement in to_add
    ]


def remove_imports(partitions, to_remove=()):
    to_remove_imports = set(
        import_obj_from_str(imp_statement) for imp_statement in to_remove
    )

    def _inner():
        for partition in partitions:
            if (
                    partition.code_type is not CodeType.IMPORT or
                    import_obj_from_str(partition.src) not in to_remove_imports
            ):
                yield partition

    return list(_inner())


def remove_duplicated_imports(partitions):
    def _inner():
        seen = set()
        for partition in partitions:
            if partition.code_type is CodeType.IMPORT:
                import_obj = import_obj_from_str(partition.src)
                if import_obj not in seen:
                    seen.add(import_obj)
                    yield partition
            else:
                yield partition
    return list(_inner())


def apply_import_sorting(
        partitions,
        separate_relative=False,
        separate_from_import=False,
        **sort_kwargs
):
    pre_import_code = []
    imports = []
    trash = []
    rest = []
    for partition in partitions:
        {
            CodeType.PRE_IMPORT_CODE: pre_import_code,
            CodeType.IMPORT: imports,
            CodeType.NON_CODE: trash,
            CodeType.CODE: rest,
        }[partition.code_type].append(partition)

    # Need to give an import a newline if it doesn't have one (needed for no
    # EOL)
    imports = [
        partition if partition.src.endswith('\n') else
        CodePartition(CodeType.IMPORT, partition.src + '\n')
        for partition in imports
    ]

    import_obj_to_partition = dict(
        (import_obj_from_str(partition.src), partition)
        for partition in imports
    )

    new_imports = []
    relative_imports = []

    def _import_type_switches(last_import_obj, import_obj):
        """Returns True if separate_from_import is True and  `import_obj` is
        :class:`aspy.refactor_imports.import_obj.FromImport`
        and ``last_import_obj`` is
        :class:`aspy.refactor_imports.import_obj.ImportImport`
        """
        return (
            separate_from_import and
            last_import_obj is not None and
            type(last_import_obj) is not type(import_obj)
        )

    sorted_blocks = sort(import_obj_to_partition.keys(), **sort_kwargs)
    for block in sorted_blocks:
        last_import_obj = None

        for import_obj in block:
            if separate_relative and import_obj.is_explicit_relative:
                relative_imports.append(import_obj_to_partition[import_obj])
            else:
                if _import_type_switches(last_import_obj, import_obj):
                    new_imports.append(CodePartition(CodeType.NON_CODE, '\n'))

                last_import_obj = import_obj
                new_imports.append(import_obj_to_partition[import_obj])

        new_imports.append(CodePartition(CodeType.NON_CODE, '\n'))

        if relative_imports:
            relative_imports.insert(0, CodePartition(CodeType.NON_CODE, '\n'))
    # XXX: I want something like [x].join(...) (like str join) but for now
    # this works
    if new_imports:
        new_imports.pop()

    # There's the potential that we moved a bunch of whitespace onto the
    # beginning of the rest of the code.  To fix this, we're going to combine
    # all of that code, and then make sure there are two linebreaks to start
    restsrc = _partitions_to_src(rest)
    restsrc = restsrc.rstrip()
    if restsrc:
        rest = [
            CodePartition(CodeType.CODE, restsrc + '\n'),
        ]
    else:
        rest = []

    return pre_import_code + new_imports + relative_imports + rest


def _get_steps(imports_to_add, imports_to_remove, **sort_kwargs):
    yield combine_trailing_code_chunks
    yield separate_comma_imports
    if imports_to_add:
        yield functools.partial(add_imports, to_add=imports_to_add)
    if imports_to_remove:
        yield functools.partial(remove_imports, to_remove=imports_to_remove)
    yield remove_duplicated_imports
    yield functools.partial(apply_import_sorting, **sort_kwargs)


def fix_file_contents(
        contents, imports_to_add=(), imports_to_remove=(), **sort_kwargs
):
    partitioned = partition_source(contents)
    for step in _get_steps(imports_to_add, imports_to_remove, **sort_kwargs):
        partitioned = step(partitioned)
    return _partitions_to_src(partitioned)


def report_diff(contents, new_contents, filename):
    diff = ''.join(difflib.unified_diff(
        io.StringIO(contents).readlines(),
        io.StringIO(new_contents).readlines(),
        fromfile=filename, tofile=filename,
    ))
    if not diff.endswith('\n'):
        diff += '\n\\ No newline at end of file\n'

    print(diff, end='')


def apply_reordering(new_contents, filename):
    print('Reordering imports in {}'.format(filename))
    with io.open(filename, 'w') as f:
        f.write(new_contents)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('filenames', nargs='*')
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument(
        '--diff-only', action='store_true',
        help='Show unified diff instead of applying reordering.',
    )
    group.add_argument(
        '--print-only', action='store_true',
        help='Print the output of a single file after reordering.',
    )
    parser.add_argument(
        '--add-import', action='append',
        help='Import to add to each file.  Can be specified multiple times.',
    )
    parser.add_argument(
        '--remove-import', action='append',
        help=(
            'Import to remove from each file.  '
            'Can be specified multiple times.'
        ),
    )
    parser.add_argument(
        '--application-directories', default='.',
        help=(
            'Colon separated directories that are considered top-level '
            'application directories.  Defaults to `%(default)s`'
        ),
    )

    parser.add_argument(
        '--separate-relative', action='store_true',
        help=(
            'Put relative imports into bottom and seperate with other local '
            'imports(absolute import) with an new line .'
        ),
    )

    parser.add_argument(
        '--separate-from-import', action='store_true',
        help=(
            'Seperate `from xx import xx` imports from `import xx` imports'
            ' with an new line .'
        ),
    )

    args = parser.parse_args(argv)

    retv = 0
    for filename in args.filenames:
        contents = io.open(filename).read()
        new_contents = fix_file_contents(
            contents,
            imports_to_add=args.add_import,
            imports_to_remove=args.remove_import,
            separate_relative=args.separate_relative,
            separate_from_import=args.separate_from_import,
            application_directories=args.application_directories.split(':'),
        )
        if contents != new_contents:
            retv = 1
            if args.diff_only:
                report_diff(contents, new_contents, filename)
            elif args.print_only:
                print('==> {} <=='.format(filename), file=sys.stderr)
                print(new_contents, end='')
            else:
                apply_reordering(new_contents, filename)

    return retv


if __name__ == '__main__':
    exit(main())
