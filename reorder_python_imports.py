from __future__ import annotations

import argparse
import ast
import collections
import enum
import io
import os
import sys
import tokenize
from typing import Generator
from typing import Iterable
from typing import NamedTuple
from typing import Sequence

from classify_imports import Import
from classify_imports import import_obj_from_str
from classify_imports import ImportFrom
from classify_imports import Settings
from classify_imports import sort

# this is a performance hack.  see https://bugs.python.org/issue43014
if (  # pragma: no branch
        sys.version_info < (3, 10) and
        callable(getattr(tokenize, '_compile', None))
):  # pragma: <3.10 cover
    from functools import lru_cache
    tokenize._compile = lru_cache()(tokenize._compile)  # type: ignore

CodeType = enum.Enum('CodeType', 'PRE_IMPORT_CODE IMPORT NON_CODE CODE')


class CodePartition(NamedTuple):
    code_type: CodeType
    src: str


TERMINATES_COMMENT = frozenset((tokenize.NL, tokenize.ENDMARKER))
TERMINATES_DOCSTRING = frozenset((tokenize.NEWLINE, tokenize.ENDMARKER))
TERMINATES_IMPORT = frozenset((tokenize.NEWLINE, tokenize.ENDMARKER))


def get_line_offsets_by_line_no(src: str) -> list[int]:
    # Padded so we can index with line number
    offsets = [0, 0]
    for line in src.splitlines(True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _partitions_to_src(partitions: Iterable[CodePartition]) -> str:
    return ''.join(part.src for part in partitions)


def partition_source(src: str) -> list[CodePartition]:
    """Partitions source into a list of `CodePartition`s for import
    refactoring.
    """
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
            elif (
                    scol == 0 and
                    token_type == tokenize.NAME and
                    token_text in {'from', 'import'}
            ):
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


def combine_trailing_code_chunks(
        partitions: Iterable[CodePartition],
) -> list[CodePartition]:
    chunks = list(partitions)

    NON_COMBINABLE = (CodeType.IMPORT, CodeType.PRE_IMPORT_CODE)
    if chunks and chunks[-1].code_type not in NON_COMBINABLE:
        src = chunks.pop().src
        while chunks and chunks[-1].code_type not in NON_COMBINABLE:
            src = chunks.pop().src + src

        chunks.append(CodePartition(CodeType.CODE, src))
    return chunks


def separate_comma_imports(
        partitions: Iterable[CodePartition],
) -> list[CodePartition]:
    """Turns `import a, b` into `import a` and `import b`"""
    def _inner() -> Generator[CodePartition, None, None]:
        for partition in partitions:
            if partition.code_type is CodeType.IMPORT:
                obj = import_obj_from_str(partition.src)
                if obj.is_multiple:
                    for new_obj in obj.split():
                        yield CodePartition(CodeType.IMPORT, str(new_obj))
                else:
                    yield partition
            else:
                yield partition

    return list(_inner())


def add_imports(
        partitions: Iterable[CodePartition],
        to_add: tuple[str, ...] = (),
) -> list[CodePartition]:
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


def remove_imports(
        partitions: Iterable[CodePartition],
        to_remove: set[tuple[str, str | None] | tuple[str, str, str | None]],
) -> list[CodePartition]:
    return [
        partition for partition in partitions
        if (
            partition.code_type is not CodeType.IMPORT or
            import_obj_from_str(partition.src).key not in to_remove
        )
    ]


class Replacements(NamedTuple):
    # (orig_mod, attr) => new_mod
    exact: dict[tuple[str, str], str]
    # orig_mod => new_mod (no attr)
    mods: dict[str, str]

    @classmethod
    def make(cls, args: list[tuple[str, str, str]]) -> Replacements:
        exact = {}
        mods = {}

        for mod_from, mod_to, attr in args:
            if attr:
                exact[mod_from, attr] = mod_to
            else:
                mod_from_base, _, mod_from_attr = mod_from.rpartition('.')
                mod_to_base, _, mod_to_attr = mod_to.rpartition('.')

                # for example `six.moves.urllib.request=urllib.request`
                if (
                        mod_from_attr and
                        mod_to_base and
                        mod_from_attr == mod_to_attr
                ):
                    exact[mod_from_base, mod_from_attr] = mod_to_base

                mods[mod_from] = mod_to

        return cls(exact=exact, mods=mods)


def replace_imports(
        partitions: Iterable[CodePartition],
        to_replace: Replacements,
) -> list[CodePartition]:
    def _inner() -> Generator[CodePartition, None, None]:
        for partition in partitions:
            if partition.code_type is CodeType.IMPORT:
                import_obj = import_obj_from_str(partition.src)

                # cannot rewrite import-imports: makes undefined names
                if isinstance(import_obj, Import):
                    yield partition
                    continue

                mod, symbol, asname = import_obj.key
                mod_symbol = f'{mod}.{symbol}'

                # from a.b.c import d => from e.f.g import d
                if (mod, symbol) in to_replace.exact:
                    node = ast.ImportFrom(
                        module=to_replace.exact[mod, symbol],
                        names=import_obj.node.names,
                        level=0,
                    )
                    yield partition._replace(src=str(ImportFrom(node)))
                # from a.b.c import d as e => from f import g as e
                # from a.b.c import d as e => import f as e
                # from a.b import c => import c
                elif (
                        mod_symbol in to_replace.mods and
                        (asname or to_replace.mods[mod_symbol] == symbol)
                ):
                    new_mod = to_replace.mods[mod_symbol]
                    new_mod, dot, new_sym = new_mod.rpartition('.')
                    if new_mod:
                        node = ast.ImportFrom(
                            module=new_mod,
                            names=[ast.alias(new_sym, asname)],
                            level=0,
                        )
                        yield partition._replace(src=str(ImportFrom(node)))
                    elif not dot:
                        node_i = ast.Import(names=[ast.alias(new_sym, asname)])
                        yield partition._replace(src=str(Import(node_i)))
                    else:
                        yield partition
                # from a.b.c import d => from e import d
                elif mod in to_replace.mods:
                    node = ast.ImportFrom(
                        module=to_replace.mods[mod],
                        names=import_obj.node.names,
                        level=0,
                    )
                    yield partition._replace(src=str(ImportFrom(node)))
                else:
                    for mod_name in _module_to_base_modules(mod):
                        if mod_name in to_replace.mods:
                            new_mod = to_replace.mods[mod_name]
                            node = ast.ImportFrom(
                                module=f'{new_mod}{mod[len(mod_name):]}',
                                names=import_obj.node.names,
                                level=0,
                            )
                            yield partition._replace(src=str(ImportFrom(node)))
                            break
                    else:
                        yield partition
            else:
                yield partition
    return list(_inner())


def _module_to_base_modules(s: str) -> Generator[str, None, None]:
    """return all module names that would be imported due to this
    import-import
    """
    parts = s.split('.')
    for i in range(1, len(parts)):
        yield '.'.join(parts[:i])


def remove_duplicated_imports(
        partitions: Iterable[CodePartition],
) -> list[CodePartition]:
    seen: set[Import | ImportFrom] = set()
    seen_module_names: set[str] = set()
    without_exact_duplicates = []

    for partition in partitions:
        if partition.code_type is CodeType.IMPORT:
            import_obj = import_obj_from_str(partition.src)
            if import_obj not in seen:
                seen.add(import_obj)
                if (
                        isinstance(import_obj, Import) and
                        not import_obj.key.asname
                ):
                    seen_module_names.update(
                        _module_to_base_modules(import_obj.module),
                    )
                without_exact_duplicates.append(partition)
        else:
            without_exact_duplicates.append(partition)

    out_partitions = []
    for partition in without_exact_duplicates:
        if partition.code_type is CodeType.IMPORT:
            import_obj = import_obj_from_str(partition.src)
            if (
                    isinstance(import_obj, Import) and
                    not import_obj.key.asname and
                    import_obj.key.module in seen_module_names
            ):
                continue
        out_partitions.append(partition)

    return out_partitions


def apply_import_sorting(
        partitions: Iterable[CodePartition],
        settings: Settings = Settings(),
) -> list[CodePartition]:
    pre_import_code: list[CodePartition] = []
    imports: list[CodePartition] = []
    trash: list[CodePartition] = []
    rest: list[CodePartition] = []
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

    sorted_blocks = sort(import_obj_to_partition.keys(), settings=settings)
    for block in sorted_blocks:
        for import_obj in block:
            new_imports.append(import_obj_to_partition[import_obj])

        new_imports.append(CodePartition(CodeType.NON_CODE, '\n'))

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

    return pre_import_code + new_imports + rest


def _most_common_line_ending(s: str) -> str:
    # initialize in case there's no line endings at all
    counts = collections.Counter({'\n': 0})
    for line in s.splitlines(True):
        for ending in ('\r\n', '\r', '\n'):
            if line.endswith(ending):
                counts[ending] += 1
                break
    return counts.most_common(1)[0][0]


def fix_file_contents(
        contents: str,
        *,
        to_add: tuple[str, ...] = (),
        to_remove: set[tuple[str, str | None] | tuple[str, str, str | None]],
        to_replace: Replacements,
        settings: Settings = Settings(),
) -> str:
    # internally use `'\n` as the newline and normalize at the very end
    nl = _most_common_line_ending(contents)
    contents = contents.replace('\r\n', '\n').replace('\r', '\n')

    partitioned = partition_source(contents)
    partitioned = combine_trailing_code_chunks(partitioned)
    partitioned = add_imports(partitioned, to_add=to_add)
    partitioned = separate_comma_imports(partitioned)
    partitioned = remove_imports(partitioned, to_remove=to_remove)
    partitioned = replace_imports(partitioned, to_replace=to_replace)
    partitioned = remove_duplicated_imports(partitioned)
    partitioned = apply_import_sorting(partitioned, settings=settings)

    return _partitions_to_src(partitioned).replace('\n', nl)


def _fix_file(
        filename: str,
        args: argparse.Namespace,
        *,
        to_remove: set[tuple[str, str | None] | tuple[str, str, str | None]],
        to_replace: Replacements,
        settings: Settings = Settings(),
) -> int:
    if filename == '-':
        contents_bytes = sys.stdin.buffer.read()
    else:
        with open(filename, 'rb') as f:
            contents_bytes = f.read()
    try:
        contents = contents_bytes.decode()
    except UnicodeDecodeError:
        print(
            f'{filename} is non-utf-8 (not supported)',
            file=sys.stderr,
        )
        return 1

    new_contents = fix_file_contents(
        contents,
        to_add=args.add_import,
        to_remove=to_remove,
        to_replace=to_replace,
        settings=settings,
    )
    if filename == '-':
        print(new_contents, end='')
    elif contents != new_contents:
        print(f'Reordering imports in {filename}', file=sys.stderr)
        with open(filename, 'wb') as f:
            f.write(new_contents.encode())

    if args.exit_zero_even_if_changed:
        return 0
    else:
        return contents != new_contents


REMOVALS: dict[tuple[int, ...], set[str]] = collections.defaultdict(set)
REPLACES: dict[tuple[int, ...], set[str]] = collections.defaultdict(set)

REMOVALS[(3,)].add('from io import open')

# GENERATED VIA generate-future-info
REMOVALS[(2, 2)].add('from __future__ import nested_scopes')
REMOVALS[(2, 3)].add('from __future__ import generators')
REMOVALS[(2, 6)].add('from __future__ import with_statement')
REMOVALS[(3,)].add('from __future__ import absolute_import, division, print_function, unicode_literals')  # noqa: E501
REMOVALS[(3, 7)].add('from __future__ import generator_stop')
# END GENERATED

# GENERATED VIA generate-typing-rewrite-info
# Using:
#     flake8-typing-imports==1.12.0
#     mypy_extensions==0.4.3
#     typing_extensions==4.0.1
REPLACES[(3, 7)].update((
    'mypy_extensions=typing:NoReturn',
))
REPLACES[(3, 8)].update((
    'mypy_extensions=typing:TypedDict',
))
REPLACES[(3, 6)].update((
    'typing_extensions=typing:AsyncIterable',
    'typing_extensions=typing:AsyncIterator',
    'typing_extensions=typing:Awaitable',
    'typing_extensions=typing:ClassVar',
    'typing_extensions=typing:ContextManager',
    'typing_extensions=typing:Coroutine',
    'typing_extensions=typing:DefaultDict',
    'typing_extensions=typing:NewType',
    'typing_extensions=typing:TYPE_CHECKING',
    'typing_extensions=typing:Text',
    'typing_extensions=typing:Type',
    'typing_extensions=typing:get_type_hints',
    'typing_extensions=typing:overload',
))
REPLACES[(3, 7)].update((
    'typing_extensions=typing:AsyncContextManager',
    'typing_extensions=typing:AsyncGenerator',
    'typing_extensions=typing:ChainMap',
    'typing_extensions=typing:Counter',
    'typing_extensions=typing:Deque',
))
REPLACES[(3, 8)].update((
    'typing_extensions=typing:Final',
    'typing_extensions=typing:Literal',
    'typing_extensions=typing:OrderedDict',
    'typing_extensions=typing:Protocol',
    'typing_extensions=typing:SupportsIndex',
    'typing_extensions=typing:TypedDict',
    'typing_extensions=typing:final',
    'typing_extensions=typing:get_args',
    'typing_extensions=typing:get_origin',
    'typing_extensions=typing:runtime_checkable',
))
REPLACES[(3, 9)].update((
    'typing_extensions=typing:Annotated',
))
REPLACES[(3, 10)].update((
    'typing_extensions=typing:Concatenate',
    'typing_extensions=typing:ParamSpec',
    'typing_extensions=typing:TypeAlias',
    'typing_extensions=typing:TypeGuard',
))
# END GENERATED

# GENERATED VIA generate-typing-pep585-rewrites
REPLACES[(3, 9)].update((
    'typing=collections.abc:AsyncGenerator',
    'typing=collections.abc:AsyncIterable',
    'typing=collections.abc:AsyncIterator',
    'typing=collections.abc:Awaitable',
    'typing=collections.abc:ByteString',
    'typing=collections.abc:Callable',
    'typing=collections.abc:Collection',
    'typing=collections.abc:Container',
    'typing=collections.abc:Coroutine',
    'typing=collections.abc:Generator',
    'typing=collections.abc:Hashable',
    'typing=collections.abc:ItemsView',
    'typing=collections.abc:Iterable',
    'typing=collections.abc:Iterator',
    'typing=collections.abc:KeysView',
    'typing=collections.abc:Mapping',
    'typing=collections.abc:MappingView',
    'typing=collections.abc:MutableMapping',
    'typing=collections.abc:MutableSequence',
    'typing=collections.abc:MutableSet',
    'typing=collections.abc:Reversible',
    'typing=collections.abc:Sequence',
    'typing=collections.abc:Sized',
    'typing=collections.abc:ValuesView',
    'typing=collections:ChainMap',
    'typing=collections:Counter',
    'typing=collections:OrderedDict',
    'typing=re:Match',
    'typing=re:Pattern',
    'typing.re=re:Match',
    'typing.re=re:Pattern',
))
# END GENERATED

# GENERATED VIA generate-python-future-info
# Using future==0.18.2
REMOVALS[(3,)].update((
    'from builtins import *',
    'from builtins import ascii',
    'from builtins import bytes',
    'from builtins import chr',
    'from builtins import dict',
    'from builtins import filter',
    'from builtins import hex',
    'from builtins import input',
    'from builtins import int',
    'from builtins import isinstance',
    'from builtins import list',
    'from builtins import map',
    'from builtins import max',
    'from builtins import min',
    'from builtins import next',
    'from builtins import object',
    'from builtins import oct',
    'from builtins import open',
    'from builtins import pow',
    'from builtins import range',
    'from builtins import round',
    'from builtins import str',
    'from builtins import super',
    'from builtins import zip',
))
# END GENERATED

# GENERATED VIA generate-six-info
# Using six==1.15.0
REMOVALS[(3,)].update((
    'from six import callable',
    'from six import next',
    'from six.moves import filter',
    'from six.moves import input',
    'from six.moves import map',
    'from six.moves import range',
    'from six.moves import zip',
))
REPLACES[(3,)].update((
    'six.moves.BaseHTTPServer=http.server',
    'six.moves.CGIHTTPServer=http.server',
    'six.moves.SimpleHTTPServer=http.server',
    'six.moves._dummy_thread=_dummy_thread',
    'six.moves._thread=_thread',
    'six.moves.builtins=builtins',
    'six.moves.cPickle=pickle',
    'six.moves.collections_abc=collections.abc',
    'six.moves.configparser=configparser',
    'six.moves.copyreg=copyreg',
    'six.moves.dbm_gnu=dbm.gnu',
    'six.moves.dbm_ndbm=dbm.ndbm',
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
))
# END GENERATED

# GENERATED VIA generate-mock-info
# Using mock==4.0.3
REPLACES[(3,)].update((
    'mock.mock=unittest.mock:ANY',
    'mock.mock=unittest.mock:DEFAULT',
    'mock.mock=unittest.mock:FILTER_DIR',
    'mock.mock=unittest.mock:MagicMock',
    'mock.mock=unittest.mock:Mock',
    'mock.mock=unittest.mock:NonCallableMagicMock',
    'mock.mock=unittest.mock:NonCallableMock',
    'mock.mock=unittest.mock:PropertyMock',
    'mock.mock=unittest.mock:call',
    'mock.mock=unittest.mock:create_autospec',
    'mock.mock=unittest.mock:mock_open',
    'mock.mock=unittest.mock:patch',
    'mock.mock=unittest.mock:sentinel',
    'mock=unittest.mock:ANY',
    'mock=unittest.mock:DEFAULT',
    'mock=unittest.mock:FILTER_DIR',
    'mock=unittest.mock:MagicMock',
    'mock=unittest.mock:Mock',
    'mock=unittest.mock:NonCallableMagicMock',
    'mock=unittest.mock:NonCallableMock',
    'mock=unittest.mock:PropertyMock',
    'mock=unittest.mock:call',
    'mock=unittest.mock:create_autospec',
    'mock=unittest.mock:mock_open',
    'mock=unittest.mock:patch',
    'mock=unittest.mock:sentinel',
))
# END GENERATED

# GENERATED VIA generate-collections-info
REPLACES[(3,)].update((
    'collections=collections.abc:AsyncGenerator',
    'collections=collections.abc:AsyncIterable',
    'collections=collections.abc:AsyncIterator',
    'collections=collections.abc:Awaitable',
    'collections=collections.abc:ByteString',
    'collections=collections.abc:Callable',
    'collections=collections.abc:Collection',
    'collections=collections.abc:Container',
    'collections=collections.abc:Coroutine',
    'collections=collections.abc:Generator',
    'collections=collections.abc:Hashable',
    'collections=collections.abc:ItemsView',
    'collections=collections.abc:Iterable',
    'collections=collections.abc:Iterator',
    'collections=collections.abc:KeysView',
    'collections=collections.abc:Mapping',
    'collections=collections.abc:MappingView',
    'collections=collections.abc:MutableMapping',
    'collections=collections.abc:MutableSequence',
    'collections=collections.abc:MutableSet',
    'collections=collections.abc:Reversible',
    'collections=collections.abc:Sequence',
    'collections=collections.abc:Set',
    'collections=collections.abc:Sized',
    'collections=collections.abc:ValuesView',
))
# END GENERATED


def _add_version_options(parser: argparse.ArgumentParser) -> None:
    versions = sorted(REMOVALS.keys() | REPLACES.keys())

    msg = 'Removes/updates obsolete imports; implies all older versions.'
    parser.add_argument(
        f'--py{"".join(str(n) for n in versions[0])}-plus', help=msg,
        action='store_const', dest='min_version', const=versions[0],
        default=(0,),
    )
    for version in versions[1:]:
        parser.add_argument(
            f'--py{"".join(str(n) for n in version)}-plus', help=msg,
            action='store_const', dest='min_version', const=version,
        )


def _validate_import(s: str) -> str:
    try:
        import_obj_from_str(s)
    except (SyntaxError, KeyError):
        raise argparse.ArgumentTypeError(f'expected import: {s!r}')
    else:
        return s


def _validate_replace_import(s: str) -> tuple[str, str, str]:
    mods, _, attr = s.partition(':')
    try:
        orig_mod, new_mod = mods.split('=')
    except ValueError:
        raise argparse.ArgumentTypeError(
            f'expected `orig.mod=new.mod` or `orig.mod=new.mod:attr`: {s!r}',
        )
    else:
        return orig_mod, new_mod, attr


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        'filenames', nargs='*',
        help='If `-` is given, reads from stdin and writes to stdout.',
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
        '--unclassifiable-application-module', action='append', default=[],
        dest='unclassifiable',
        help=(
            '(may be specified multiple times) module names that are '
            'considered application modules.  this setting is intended to be '
            'used for things like C modules which may not always appear on '
            'the filesystem'
        ),
    )

    _add_version_options(parser)

    args = parser.parse_args(argv)

    to_remove = {
        obj.key
        for s in args.remove_import
        for obj in import_obj_from_str(s).split()
    } | {
        obj.key
        for k, v in REMOVALS.items()
        if args.min_version >= k
        for s in v
        for obj in import_obj_from_str(s).split()
    }

    for k, v in REPLACES.items():
        if args.min_version >= k:
            args.replace_import.extend(
                _validate_replace_import(replace_s) for replace_s in v
            )

    to_replace = Replacements.make(args.replace_import)

    if os.environ.get('PYTHONPATH'):
        sys.stderr.write('$PYTHONPATH set, import order may be unexpected\n')
        sys.stderr.flush()

    settings = Settings(
        application_directories=tuple(args.application_directories.split(':')),
        unclassifiable_application_modules=frozenset(args.unclassifiable),
    )

    retv = 0
    for filename in args.filenames:
        retv |= _fix_file(
            filename,
            args,
            to_remove=to_remove,
            to_replace=to_replace,
            settings=settings,
        )
    return retv


if __name__ == '__main__':
    raise SystemExit(main())
