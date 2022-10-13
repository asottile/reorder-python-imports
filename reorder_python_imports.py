from __future__ import annotations

import argparse
import ast
import collections
import enum
import io
import itertools
import os
import re
import sys
from typing import Generator
from typing import NamedTuple
from typing import Sequence

from classify_imports import Import
from classify_imports import import_obj_from_str
from classify_imports import ImportFrom
from classify_imports import Settings
from classify_imports import sort

CodeType = enum.Enum('CodeType', 'PRE_IMPORT_CODE IMPORT NON_CODE CODE')

Tok = enum.Enum('Tok', 'IMPORT STRING NEWLINE ERROR')

# GENERATED VIA generate-tokenize
COMMENT = r'#[^\r\n]*'
NAME = r'\w+'
PREFIX = r'[RrUu]?'
DOUBLE_3 = r'"""[^"\\]*(?:(?:\\.|\\\n|"(?!""))[^"\\]*)*"""'
SINGLE_3 = r"'''[^'\\]*(?:(?:\\.|\\\n|'(?!''))[^'\\]*)*'''"
DOUBLE_1 = r'"[^"\\]*(?:\\.[^"\\]*)*"'
SINGLE_1 = r"'[^'\\]*(?:\\.[^'\\]*)*'"
# END GENERATED

WS = r'[ \f\t]+'
IMPORT = fr'(?:from|import)(?={WS})'
EMPTY = fr'[ \f\t]*(?=\n|{COMMENT})'
OP = '[,.*]'
ESCAPED_NL = r'\\\n'
NAMES = fr'\((?:\s+|,|{NAME}|{ESCAPED_NL}|{COMMENT})*\)'
STRING = fr'{PREFIX}(?:{DOUBLE_3}|{SINGLE_3}|{DOUBLE_1}|{SINGLE_1})'


def _pat(base: str, pats: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(
        fr'{base}'
        fr'(?:{"|".join(pats)})*'
        fr'(?P<comment>(?:{COMMENT})?)'
        fr'(?:\n|$)',
    )


TOKENIZE: tuple[tuple[Tok, re.Pattern[str]], ...] = (
    (Tok.IMPORT, _pat(IMPORT, (WS, NAME, OP, ESCAPED_NL, NAMES))),
    (Tok.NEWLINE, _pat(EMPTY, ())),
    (Tok.STRING, _pat(STRING, (WS, STRING, ESCAPED_NL))),
)


def _tokenize(s: str) -> Generator[tuple[Tok, str], None, None]:
    pos = 0
    while True:
        for tp, reg in TOKENIZE:
            match = reg.match(s, pos)
            if match is not None:
                if 'noreorder' in match['comment']:
                    yield (Tok.ERROR, s[pos:])
                    return
                else:
                    yield (tp, match[0])
                pos = match.end()
                break
        else:
            yield (Tok.ERROR, s[pos:])
            return


def partition_source(src: str) -> tuple[str, list[str], str, str]:
    sio = io.StringIO(src, newline=None)
    src = sio.read().rstrip() + '\n'

    if sio.newlines is None:
        nl = '\n'
    elif isinstance(sio.newlines, str):
        nl = sio.newlines
    else:
        nl = sio.newlines[0]

    chunks = []
    pre_import = True
    for token_type, s in _tokenize(src):
        if token_type is Tok.IMPORT:
            pre_import = False
            chunks.append((CodeType.IMPORT, s))
        elif token_type is Tok.NEWLINE:
            if s.isspace():
                tp = CodeType.NON_CODE
            elif pre_import:
                tp = CodeType.PRE_IMPORT_CODE
            else:
                tp = CodeType.CODE

            chunks.append((tp, s))
        elif pre_import and token_type is Tok.STRING:
            chunks.append((CodeType.PRE_IMPORT_CODE, s))
        else:
            chunks.append((CodeType.CODE, s))

    last_idx = 0
    for i, (tp, _) in enumerate(chunks):
        if tp in (CodeType.PRE_IMPORT_CODE, CodeType.IMPORT):
            last_idx = i

    pre = []
    imports = []
    code = []
    for i, (tp, src) in enumerate(chunks):
        if tp is CodeType.PRE_IMPORT_CODE:
            pre.append(src)
        elif tp is CodeType.IMPORT:
            imports.append(src)
        elif tp is CodeType.CODE or i > last_idx:
            code.append(src)

    return ''.join(pre), imports, ''.join(code), nl


def parse_imports(
        imports: list[str],
        *,
        to_add: tuple[str, ...] = (),
) -> list[tuple[str, Import | ImportFrom]]:
    ret = []

    for s in itertools.chain(to_add, imports):
        obj = import_obj_from_str(s)
        if not obj.is_multiple:
            ret.append((s, obj))
        else:
            ret.extend((str(new), new) for new in obj.split())

    return ret


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
        imports: list[tuple[str, Import | ImportFrom]],
        to_replace: Replacements,
) -> list[tuple[str, Import | ImportFrom]]:
    ret = []

    for s, import_obj in imports:
        if isinstance(import_obj, Import):
            mod, asname = import_obj.key
            if asname:
                if mod in to_replace.mods:
                    node_i = ast.Import(
                        names=[ast.alias(to_replace.mods[mod], asname)],
                    )
                    obj_i = Import(node_i)
                    ret.append((str(obj_i), obj_i))
                else:
                    for mod_name in _module_to_base_modules(mod):
                        if mod_name in to_replace.mods:
                            new_mod = to_replace.mods[mod_name]
                            new_mod_s = f'{new_mod}{mod[len(mod_name):]}'
                            node_i = ast.Import(
                                names=[ast.alias(new_mod_s, asname)],
                            )
                            obj_i = Import(node_i)
                            ret.append((str(obj_i), obj_i))
                            break
                    else:
                        ret.append((s, import_obj))
            else:
                ret.append((s, import_obj))
        else:
            mod, symbol, asname = import_obj.key
            mod_symbol = f'{mod}.{symbol}'

            # from a.b.c import d => from e.f.g import d
            if (mod, symbol) in to_replace.exact:
                node = ast.ImportFrom(
                    module=to_replace.exact[mod, symbol],
                    names=import_obj.node.names,
                    level=0,
                )
                obj = ImportFrom(node)
                ret.append((str(obj), obj))
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
                    obj = ImportFrom(node)
                    ret.append((str(obj), obj))
                elif not dot:
                    node_i = ast.Import(names=[ast.alias(new_sym, asname)])
                    obj_i = Import(node_i)
                    ret.append((str(obj_i), obj_i))
                else:
                    ret.append((s, import_obj))
            # from a.b.c import d => from e import d
            elif mod in to_replace.mods:
                node = ast.ImportFrom(
                    module=to_replace.mods[mod],
                    names=import_obj.node.names,
                    level=0,
                )
                obj = ImportFrom(node)
                ret.append((str(obj), obj))
            else:
                for mod_name in _module_to_base_modules(mod):
                    if mod_name in to_replace.mods:
                        new_mod = to_replace.mods[mod_name]
                        node = ast.ImportFrom(
                            module=f'{new_mod}{mod[len(mod_name):]}',
                            names=import_obj.node.names,
                            level=0,
                        )
                        obj = ImportFrom(node)
                        ret.append((str(obj), obj))
                        break
                else:
                    ret.append((s, import_obj))

    return ret


def _module_to_base_modules(s: str) -> Generator[str, None, None]:
    """return all module names that would be imported due to this
    import-import
    """
    s = s.rpartition('.')[0]
    while s:
        yield s
        s = s.rpartition('.')[0]


def remove_duplicated_imports(
        imports: list[tuple[str, Import | ImportFrom]],
        *,
        to_remove: set[tuple[str, ...]],
) -> list[tuple[str, Import | ImportFrom]]:
    seen = set(to_remove)
    seen_module_names: set[str] = set()
    without_exact_duplicates = []

    for s, import_obj in imports:
        if import_obj.key not in seen:
            seen.add(import_obj.key)
            if (
                    isinstance(import_obj, Import) and
                    not import_obj.key.asname
            ):
                seen_module_names.update(
                    _module_to_base_modules(import_obj.module),
                )
            without_exact_duplicates.append((s, import_obj))

    ret = []
    for s, import_obj in without_exact_duplicates:
        if (
                isinstance(import_obj, Import) and
                not import_obj.key.asname and
                import_obj.key.module in seen_module_names
        ):
            continue
        ret.append((s, import_obj))

    return ret


def apply_import_sorting(
        imports: list[tuple[str, Import | ImportFrom]],
        settings: Settings = Settings(),
) -> list[str]:
    import_obj_to_s = {v: s for s, v in imports}

    sorted_blocks = sort(import_obj_to_s, settings=settings)

    new_imports = []
    for block in sorted_blocks:
        for import_obj in block:
            new_imports.append(import_obj_to_s[import_obj])

        new_imports.append('\n')

    # XXX: I want something like [x].join(...) (like str join) but for now
    # this works
    if new_imports:
        new_imports.pop()

    return new_imports


def fix_file_contents(
        contents: str,
        *,
        to_add: tuple[str, ...] = (),
        to_remove: set[tuple[str, ...]],
        to_replace: Replacements,
        settings: Settings = Settings(),
) -> str:
    if not contents or contents.isspace():
        return ''

    # internally use `'\n` as the newline and normalize at the very end
    before, imports, after, nl = partition_source(contents)

    parsed = parse_imports(imports, to_add=to_add)
    parsed = replace_imports(parsed, to_replace=to_replace)
    parsed = remove_duplicated_imports(parsed, to_remove=to_remove)
    imports = apply_import_sorting(parsed, settings=settings)

    return f'{before}{"".join(imports)}{after}'.replace('\n', nl)


def _fix_file(
        filename: str,
        args: argparse.Namespace,
        *,
        to_remove: set[tuple[str, ...]],
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
        to_add=tuple(f'{s.strip()}\n' for s in args.add_import),
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
REMOVALS[(3,)].update((
    'from __future__ import division',
    'from __future__ import absolute_import',
    'from __future__ import print_function',
    'from __future__ import unicode_literals',
))
REMOVALS[(3, 7)].add('from __future__ import generator_stop')
# END GENERATED

# GENERATED VIA generate-typing-rewrite-info
# Using:
#     flake8-typing-imports==1.13.0
#     mypy-extensions==0.4.3
#     typing-extensions==4.4.0
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
))
REPLACES[(3, 7)].update((
    'mypy_extensions=typing:NoReturn',
    'typing_extensions=typing:AsyncContextManager',
    'typing_extensions=typing:AsyncGenerator',
    'typing_extensions=typing:ChainMap',
    'typing_extensions=typing:Counter',
    'typing_extensions=typing:Deque',
    'typing_extensions=typing:NoReturn',
))
REPLACES[(3, 8)].update((
    'mypy_extensions=typing:TypedDict',
    'typing_extensions=typing:Final',
    'typing_extensions=typing:Literal',
    'typing_extensions=typing:OrderedDict',
    'typing_extensions=typing:Protocol',
    'typing_extensions=typing:SupportsIndex',
    'typing_extensions=typing:runtime_checkable',
))
REPLACES[(3, 9)].update((
    'typing_extensions=typing:Annotated',
    'typing_extensions=typing:get_type_hints',
))
REPLACES[(3, 10)].update((
    'typing_extensions=typing:Concatenate',
    'typing_extensions=typing:ParamSpecArgs',
    'typing_extensions=typing:ParamSpecKwargs',
    'typing_extensions=typing:TypeAlias',
    'typing_extensions=typing:TypeGuard',
    'typing_extensions=typing:get_args',
    'typing_extensions=typing:get_origin',
    'typing_extensions=typing:is_typeddict',
))
# END GENERATED

# GENERATED VIA generate-typing-pep585-rewrites
REPLACES[(3, 9)].update((
    'typing=collections.abc:AsyncGenerator',
    'typing=collections.abc:AsyncIterable',
    'typing=collections.abc:AsyncIterator',
    'typing=collections.abc:Awaitable',
    'typing=collections.abc:ByteString',
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
REPLACES[(3, 10)].add('typing=collections.abc:Callable')
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
# up until cpython 3.10.0
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
REPLACES[(3, 7)].update((
    'mock.mock=unittest.mock:seal',
    'mock=unittest.mock:seal',
))
REPLACES[(3, 8)].update((
    'mock.mock=unittest.mock:AsyncMock',
    'mock=unittest.mock:AsyncMock',
))
# END GENERATED

# GENERATED VIA generate-deprecated
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
    'pipes=shlex:quote',
    'xml.etree.cElementTree=xml.etree.ElementTree',
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
        import_obj_from_str(s).key
        for k, v in REMOVALS.items()
        if args.min_version >= k
        for s in v
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
