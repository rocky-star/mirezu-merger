import gettext
import importlib.resources
import io
import builtins


def ensure_translation() -> None:
    if ('_' not in builtins.__dict__
        or 'ngettext' not in builtins.__dict__):
        gettext.NullTranslations().install(names={'ngettext'})


def install_translation(locale_name: str) -> None:
    mo_file_name = (
        importlib.resources.files('mirezu_merger')
        / 'locale'
        / (locale_name + '.mo'))
    translations = (
        gettext.GNUTranslations(io.BytesIO(mo_file_name.read_bytes()))
        if mo_file_name.is_file()
        else gettext.NullTranslations())
    translations.install(names={'ngettext'})
