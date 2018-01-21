from __future__ import absolute_import
from __future__ import unicode_literals

import pytest


@pytest.fixture
def in_tmpdir(tmpdir):
    with tmpdir.as_cwd():
        yield tmpdir
