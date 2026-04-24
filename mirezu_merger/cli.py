import argparse
import locale
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from .constants import EXIT_FAILURE
from .errors import (
    ConfigParseError,
    ConfigReadError,
    OutputWriteError,
    ProfileReadError,
    SubscriptionProbeError,
    ProvidersWriteError,
    TemplateReadError,
)
from .i18n import install_translation
from .workflow import build_from_paths
from .wizard import run_wizard

logger = logging.getLogger(__name__)


def build_root_argument_parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description=_('Merge YAML configuration files provided by '
                      + 'subscription services.'),
        epilog=_('Commands: build, wizard. Legacy build usage without the '
                 + 'build subcommand is still supported.'),
    )
    parser.add_argument(
        'command',
        nargs='?',
        choices=['build', 'wizard'],
        help=_('the command to run'),
    )
    return parser


def build_argument_parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description=_('Merge YAML configuration files provided by '
                      + 'subscription services.'),
    )
    parser.add_argument('config', type=Path, help=_('the configuration file'))
    parser.add_argument('profiles', type=Path, help=_('the profile directory'))
    parser.add_argument(
        '-o', '--outdir',
        default='output', type=Path,
        help=_('the output directory'),
    )
    parser.add_argument(
        '--locale', help=_('the locale name of the translation to use'))
    parser.add_argument(
        '-v', '--verbose',
        action='count', default=0,
        help=_('increase the logging level, useful for debugging'),
    )
    return parser


def install_system_translation() -> None:
    locale.setlocale(locale.LC_ALL, '')
    locale_name, __ = locale.getlocale(
        getattr(locale, 'LC_MESSAGES', locale.LC_CTYPE))
    if locale_name is not None:
        install_translation(locale_name)


def configure_logging(verbosity: int) -> None:
    logging_level = (
        {0: logging.WARNING, 1: logging.INFO}.get(verbosity, logging.DEBUG))
    logging.basicConfig(level=logging_level)


def run_build(argv: Sequence[str]) -> int:
    parser = build_argument_parser(argv[0])
    preview_args, __ = parser.parse_known_args(argv[1:])
    if preview_args.locale is not None:
        install_translation(preview_args.locale)
    args = parser.parse_args(argv[1:])
    configure_logging(args.verbose)
    try:
        build_from_paths(args.config, args.profiles, args.outdir)
    except ConfigReadError as error:
        logger.exception(_('Failed to read the configuration from %s'),
                         error.path)
    except ConfigParseError as error:
        logger.exception(_('Failed to parse the configuration from %s'),
                         error.path)
    except TemplateReadError as error:
        logger.exception(_('Failed to read the template from %s'),
                         error.path)
    except ProfileReadError as error:
        logger.exception(_('Failed to read profile %s'), error.path)
    except ProvidersWriteError as error:
        logger.exception(_('Failed to write the proxy-providers.yaml to %s'),
                         error.path)
    except OutputWriteError as error:
        logger.exception(_('Failed to write the output of patch %s'),
                         error.path)
    except SubscriptionProbeError as error:
        logger.critical(
            ngettext(
                'Encountered %d blocking subscription failure during probing',
                'Encountered %d blocking subscription failures during probing',
                error.report.failed_count,
            ),
            error.report.failed_count,
        )
    else:
        return 0
    return EXIT_FAILURE


def run(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv

    install_system_translation()
    if len(argv) == 1:
        build_root_argument_parser(argv[0]).print_help()
        return EXIT_FAILURE
    if argv[1] in {'-h', '--help'}:
        build_root_argument_parser(argv[0]).print_help()
        return 0
    if argv[1] == 'wizard':
        return run_wizard([f'{argv[0]} wizard', *argv[2:]])
    if argv[1] == 'build':
        return run_build([f'{argv[0]} build', *argv[2:]])
    return run_build(list(argv))


def main(argv: Sequence[str] | None = None) -> None:
    raise SystemExit(run(argv))
