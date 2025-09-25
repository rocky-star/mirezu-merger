#!/usr/bin/env python3

import subprocess
import os.path
import glob
import shutil
from typing import Final

XGETTEXT: Final = shutil.which('xgettext')
assert XGETTEXT is not None
subprocess.run(
    [XGETTEXT,
     '-o', 'mirezu-merger.pot', '-p', os.path.join('mirezu_merger', 'locale'),
     '--from-code=UTF-8',
     '--copyright-holder=Rocky-Star',
     '--package-name=Mirezu Merger', '--package-version=0.1',
     '--msgid-bugs-address=https://github.com/rocky-star/mirezu-merger/issues'
     ] + glob.glob(os.path.join('mirezu_merger', '**', '*.py'), recursive=True),
    check=True)
