from __future__ import absolute_import
from __future__ import unicode_literals

import contextlib
import os

import pytest


@contextlib.contextmanager
def cwd(path):
    pwd = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(pwd)


@pytest.yield_fixture
def in_tmpdir(tmpdir):
    with cwd(tmpdir.strpath):
        yield tmpdir
