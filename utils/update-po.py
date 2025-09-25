#!/usr/bin/env python3

import subprocess
import os.path
import glob
import shutil
from typing import Final

POT_FILE_NAME: Final = os.path.join(
    'mirezu_merger', 'locale', 'mirezu-merger.pot')
MSGMERGE: Final = shutil.which('msgmerge')
assert MSGMERGE is not None
for po_file_name in glob.glob(os.path.join('mirezu_merger', 'locale', '*.po')):
    subprocess.run([MSGMERGE, '-U', po_file_name, os.path.join(POT_FILE_NAME)],
                   check=True)
