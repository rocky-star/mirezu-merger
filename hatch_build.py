import os.path
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, final

from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po
from hatchling.builders.hooks.plugin.interface import BuildHookInterface
from typing_extensions import override


@final
class CustomBuildHook(BuildHookInterface):
    @override
    def clean(self, versions: list[str]) -> None:
        for mo in Path(self.root, 'mirezu_merger', 'locale').glob('*.mo'):
            mo.unlink()

    @override
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        for po in Path(self.root, 'mirezu_merger', 'locale').glob('*.po'):
            with open(po, encoding='utf_8') as po_file:
                catalog = read_po(po_file)
            with open(po.with_suffix('.mo'), 'wb') as mo_file:
                write_mo(mo_file, catalog)
