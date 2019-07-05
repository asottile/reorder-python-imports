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

from aspy.refactor_imports.import_obj import import_obj_from_str
from aspy.refactor_imports.import_obj import ImportImport
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
    for line in src.splitlines(True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _partitions_to_src(partitions):
    return ''.join(part.src for part in partitions)


def partition_source(src):
    """Partitions source into a list of `CodePartition`s for import
    refactoring.
    """
    # In python2, ast.parse(text_string_with_encoding_pragma) raises
    # SyntaxError: encoding declaration in Unicode string
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

    chunks = [chunk for chunk in chunks if chunk.src]

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
                            CodeType.IMPORT, new_import_obj.to_text(),
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
        partitions[-1] = partitions[-1]._replace(src=partitions[-1].src + '\n')

    return partitions + [
        CodePartition(CodeType.IMPORT, imp_statement.strip() + '\n')
        for imp_statement in to_add
    ]


def remove_imports(partitions, to_remove=()):
    to_remove_imports = set()
    for s in to_remove:
        to_remove_imports.update(import_obj_from_str(s).split_imports())

    def _inner():
        for partition in partitions:
            if (
                    partition.code_type is not CodeType.IMPORT or
                    import_obj_from_str(partition.src) not in to_remove_imports
            ):
                yield partition

    return list(_inner())


def _mod_startswith(mod_parts, prefix_parts):
    return mod_parts[:len(prefix_parts)] == prefix_parts


def replace_imports(partitions, to_replace=()):
    def _inner():
        for partition in partitions:
            if partition.code_type is CodeType.IMPORT:
                import_obj = import_obj_from_str(partition.src)

                # cannot rewrite import-imports: makes undefined names
                if isinstance(import_obj, ImportImport):
                    yield partition
                    continue

                mod_parts = import_obj.import_statement.module.split('.')
                symbol = import_obj.import_statement.symbol
                asname = import_obj.import_statement.asname

                for orig_mod, new_mod, attr in to_replace:
                    if (
                            (attr == symbol and mod_parts == orig_mod) or
                            (not attr and _mod_startswith(mod_parts, orig_mod))
                    ):
                        mod_parts[:len(orig_mod)] = new_mod
                        import_obj.ast_obj.module = '.'.join(mod_parts)
                        new_src = import_obj.to_text()
                        yield partition._replace(src=new_src)
                        break
                    # from x.y import z => import z
                    elif (
                            not attr and
                            mod_parts + [symbol] == orig_mod and
                            len(new_mod) == 1
                    ):
                        mod_name, = new_mod
                        asname_src = ' as {}'.format(asname) if asname else ''
                        new_src = 'import {}{}\n'.format(mod_name, asname_src)
                        yield partition._replace(src=new_src)
                        break
                else:
                    yield partition
            else:
                yield partition
    return list(_inner())


def _module_to_base_modules(s):
    """return all module names that would be imported due to this
    import-import
    """
    parts = s.split('.')
    for i in range(1, len(parts)):
        yield '.'.join(parts[:i])


def remove_duplicated_imports(partitions):
    seen = set()
    seen_module_names = set()
    without_exact_duplicates = []

    for partition in partitions:
        if partition.code_type is CodeType.IMPORT:
            import_obj = import_obj_from_str(partition.src)
            if import_obj not in seen:
                seen.add(import_obj)
                if (
                        isinstance(import_obj, ImportImport) and
                        not import_obj.import_statement.asname
                ):
                    seen_module_names.update(
                        _module_to_base_modules(
                            import_obj.import_statement.module,
                        ),
                    )
                without_exact_duplicates.append(partition)
        else:
            without_exact_duplicates.append(partition)

    partitions = []
    for partition in without_exact_duplicates:
        if partition.code_type is CodeType.IMPORT:
            import_obj = import_obj_from_str(partition.src)
            if (
                    isinstance(import_obj, ImportImport) and
                    not import_obj.import_statement.asname and
                    import_obj.import_statement.module in seen_module_names
            ):
                continue
        partitions.append(partition)

    return partitions


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

    import_obj_to_partition = {
        import_obj_from_str(partition.src): partition
        for partition in imports
    }

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

        # There's an edge case if both --separate-relative and
        # --separate-from-import are passed where the first-party imports
        # will *all* be explicit relative imports and sorted into the special
        # block.  In this case, we don't want the first-party block to just
        # be a single newline.  See #23
        if last_import_obj is not None:
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
        rest = [CodePartition(CodeType.CODE, restsrc + '\n')]
    else:
        rest = []

    return pre_import_code + new_imports + relative_imports + rest


def _get_steps(
        imports_to_add,
        imports_to_remove,
        imports_to_replace,
        **sort_kwargs
):
    yield combine_trailing_code_chunks
    yield functools.partial(add_imports, to_add=imports_to_add)
    yield separate_comma_imports
    yield functools.partial(remove_imports, to_remove=imports_to_remove)
    yield functools.partial(replace_imports, to_replace=imports_to_replace)
    yield remove_duplicated_imports
    yield functools.partial(apply_import_sorting, **sort_kwargs)


def _most_common_line_ending(s):
    # initialize in case there's no line endings at all
    counts = collections.Counter({'\n': 0})
    for line in s.splitlines(True):
        for ending in ('\r\n', '\r', '\n'):
            if line.endswith(ending):
                counts[ending] += 1
                break
    return counts.most_common(1)[0][0]


def fix_file_contents(
        contents,
        imports_to_add=(),
        imports_to_remove=(),
        imports_to_replace=(),
        **sort_kwargs
):
    # internally use `'\n` as the newline and normalize at the very end
    nl = _most_common_line_ending(contents)
    contents = contents.replace('\r\n', '\n').replace('\r', '\n')

    partitioned = partition_source(contents)
    for step in _get_steps(
            imports_to_add,
            imports_to_remove,
            imports_to_replace,
            **sort_kwargs
    ):
        partitioned = step(partitioned)
    return _partitions_to_src(partitioned).replace('\n', nl)


def _fix_file(filename, args):
    if filename == '-':
        contents_bytes = getattr(sys.stdin, 'buffer', sys.stdin).read()
    else:
        with open(filename, 'rb') as f:
            contents_bytes = f.read()
    try:
        contents = contents_bytes.decode('UTF-8')
    except UnicodeDecodeError:
        print(
            '{} is non-utf-8 (not supported)'.format(filename),
            file=sys.stderr,
        )
        return 1

    new_contents = fix_file_contents(
        contents,
        imports_to_add=args.add_import,
        imports_to_remove=args.remove_import,
        imports_to_replace=args.replace_import,
        separate_relative=args.separate_relative,
        separate_from_import=args.separate_from_import,
        application_directories=args.application_directories.split(':'),
    )
    if filename == '-':
        print(new_contents, end='')
    elif contents != new_contents:
        if args.diff_only:
            _report_diff(contents, new_contents, filename)
        elif args.print_only:
            print('!!! --print-only is deprecated', file=sys.stderr)
            print('!!! maybe use `-` instead?', file=sys.stderr)
            print('==> {} <=='.format(filename), file=sys.stderr)
            print(new_contents, end='')
        else:
            print('Reordering imports in {}'.format(filename))
            with open(filename, 'wb') as f:
                f.write(new_contents.encode('UTF-8'))

    if args.exit_zero_even_if_changed:
        return 0
    else:
        return contents != new_contents


def _report_diff(contents, new_contents, filename):
    diff = ''.join(
        difflib.unified_diff(
            io.StringIO(contents).readlines(),
            io.StringIO(new_contents).readlines(),
            fromfile=filename, tofile=filename,
        ),
    )
    if not diff.endswith('\n'):
        diff += '\n\\ No newline at end of file\n'

    print(diff, end='')


FUTURE_IMPORTS = (
    ('py22', ('nested_scopes',)),
    ('py23', ('generators',)),
    ('py26', ('with_statement',)),
    (
        'py3',
        ('division', 'absolute_import', 'print_function', 'unicode_literals'),
    ),
    ('py37', ('generator_stop',)),
)


def _add_future_options(parser):
    prev = []
    for py, removals in FUTURE_IMPORTS:
        opt = '--{}-plus'.format(py)
        futures = ', '.join(removals)
        implies = '. implies: {}'.format(', '.join(prev)) if prev else ''
        parser.add_argument(
            opt, action='store_true',
            help='Remove obsolete future imports ({}){}'.format(
                futures, implies,
            ),
        )
        prev.append(opt)


def _future_removals(args):
    implied = False
    to_remove = []
    for py, removals in reversed(FUTURE_IMPORTS):
        implied |= getattr(args, '{}_plus'.format(py))
        if implied:
            to_remove.extend(removals)
    if to_remove:
        yield 'from __future__ import {}'.format(', '.join(to_remove))


# GENERATED VIA generate-six-info
# Using six==1.11.0
SIX_REMOVALS = [
    'from six.moves import filter',
    'from six.moves import input',
    'from six.moves import map',
    'from six.moves import range',
    'from six.moves import zip',
]
SIX_RENAMES = [
    'six.moves.BaseHTTPServer=http.server',
    'six.moves.CGIHTTPServer=http.server',
    'six.moves.SimpleHTTPServer=http.server',
    'six.moves._dummy_thread=_dummy_thread',
    'six.moves._thread=_thread',
    'six.moves.builtins=builtins',
    'six.moves.cPickle=pickle',
    'six.moves.configparser=configparser',
    'six.moves.copyreg=copyreg',
    'six.moves.dbm_gnu=dbm.gnu',
    'six.moves.email_mime_base=email.mime.base',
    'six.moves.email_mime_image=email.mime.image',
    'six.moves.email_mime_multipart=email.mime.multipart',
    'six.moves.email_mime_nonmultipart=email.mime.nonmultipart',
    'six.moves.email_mime_text=email.mime.text',
    'six.moves.html_entities=html.entities',
    'six.moves.html_parser=html.parser',
    'six.moves.http_client=http.client',
    'six.moves.http_cookiejar=http.cookiejar',
    'six.moves.http_cookies=http.cookies',
    'six.moves.queue=queue',
    'six.moves.reprlib=reprlib',
    'six.moves.socketserver=socketserver',
    'six.moves.tkinter=tkinter',
    'six.moves.tkinter_colorchooser=tkinter.colorchooser',
    'six.moves.tkinter_commondialog=tkinter.commondialog',
    'six.moves.tkinter_constants=tkinter.constants',
    'six.moves.tkinter_dialog=tkinter.dialog',
    'six.moves.tkinter_dnd=tkinter.dnd',
    'six.moves.tkinter_filedialog=tkinter.filedialog',
    'six.moves.tkinter_font=tkinter.font',
    'six.moves.tkinter_messagebox=tkinter.messagebox',
    'six.moves.tkinter_scrolledtext=tkinter.scrolledtext',
    'six.moves.tkinter_simpledialog=tkinter.simpledialog',
    'six.moves.tkinter_tix=tkinter.tix',
    'six.moves.tkinter_tkfiledialog=tkinter.filedialog',
    'six.moves.tkinter_tksimpledialog=tkinter.simpledialog',
    'six.moves.tkinter_ttk=tkinter.ttk',
    'six.moves.urllib.error=urllib.error',
    'six.moves.urllib.parse=urllib.parse',
    'six.moves.urllib.request=urllib.request',
    'six.moves.urllib.response=urllib.response',
    'six.moves.urllib.robotparser=urllib.robotparser',
    'six.moves.urllib_error=urllib.error',
    'six.moves.urllib_parse=urllib.parse',
    'six.moves.urllib_robotparser=urllib.robotparser',
    'six.moves.xmlrpc_client=xmlrpc.client',
    'six.moves.xmlrpc_server=xmlrpc.server',
    'six.moves=collections:UserDict',
    'six.moves=collections:UserList',
    'six.moves=collections:UserString',
    'six.moves=functools:reduce',
    'six.moves=io:StringIO',
    'six.moves=itertools:filterfalse',
    'six.moves=itertools:zip_longest',
    'six.moves=os:getcwd',
    'six.moves=os:getcwdb',
    'six.moves=subprocess:getoutput',
    'six.moves=sys:intern',
    'six=functools:wraps',
    'six=io:BytesIO',
    'six=io:StringIO',
]
# END GENERATED


def _is_py3(args):
    for py, _ in FUTURE_IMPORTS:
        if py.startswith('py3') and getattr(args, '{}_plus'.format(py)):
            return True
    else:
        return False


def _six_removals(args):
    if _is_py3(args):
        return SIX_REMOVALS
    else:
        return []


def _six_replaces(args):
    if _is_py3(args):
        return [_validate_replace_import(s) for s in SIX_RENAMES]
    else:
        return []


def _validate_import(s):
    try:
        import_obj_from_str(s)
    except (SyntaxError, KeyError):
        raise argparse.ArgumentTypeError('expected import: {!r}'.format(s))
    else:
        return s


def _validate_replace_import(s):
    mods, _, attr = s.partition(':')
    try:
        orig_mod, new_mod = mods.split('=')
    except ValueError:
        raise argparse.ArgumentTypeError(
            'expected `orig.mod=new.mod` or '
            '`orig.mod=new.mod:attr`: {!r}'.format(s),
        )
    else:
        return orig_mod.split('.'), new_mod.split('.'), attr


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
        help=(
            '(Deprecated) '
            'Print the output of a single file after reordering.'
        ),
    )
    parser.add_argument('--exit-zero-even-if-changed', action='store_true')
    parser.add_argument(
        '--add-import', action='append', default=[], type=_validate_import,
        help='Import to add to each file.  Can be specified multiple times.',
    )
    parser.add_argument(
        '--remove-import', action='append', default=[], type=_validate_import,
        help=(
            'Import to remove from each file.  '
            'Can be specified multiple times.'
        ),
    )
    parser.add_argument(
        '--replace-import', action='append', default=[],
        type=_validate_replace_import,
        help=(
            'Module pairs to replace imports. '
            'For example: `--replace-import orig.mod=new.mod`.  '
            'For renames of a specific imported attribute, use the form '
            '`--replace-import orig.mod=new.mod:attr`.  '
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
            'Separate explicit relative (`from . import ...`) imports into a '
            'separate block.'
        ),
    )

    parser.add_argument(
        '--separate-from-import', action='store_true',
        help=(
            'Seperate `from xx import xx` imports from `import xx` imports'
            ' with a new line .'
        ),
    )

    _add_future_options(parser)

    args = parser.parse_args(argv)
    args.remove_import.extend(_future_removals(args))
    args.remove_import.extend(_six_removals(args))
    args.replace_import.extend(_six_replaces(args))

    retv = 0
    for filename in args.filenames:
        retv |= _fix_file(filename, args)
    return retv


if __name__ == '__main__':
    exit(main())
