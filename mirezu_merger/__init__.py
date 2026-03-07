"""A script for merging YAML configuration files provided by subscription
services.
"""

__all__ = ['main']
__version__ = '0.1'
__author__ = ('Rocky\N{WHITE STAR}Star <rocky-star22 at outlook dot com>'
              .replace(' at ', '@').replace(' dot ', '.'))

import argparse
import copy
import gettext
import importlib.resources
import io
import locale
import logging
import re
import string
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Collection, Container, Sequence
from pathlib import Path
from typing import Any, Final, Literal, TypedDict, cast, final

import ruamel.yaml
from typing_extensions import NotRequired, assert_never, override

EXIT_FAILURE: Final = 1
USER_AGENT: Final = 'clash-verge/1.19.4'
BUILTIN_PROXY_NAMES: Final = frozenset([
    'DIRECT', 'REJECT', 'REJECT-DROP', 'PASS', 'COMPATIBLE'])

logger = logging.getLogger()
yaml = ruamel.yaml.YAML(typ='safe')


@final
class SubscriptionMappingConfig(TypedDict):
    source: str
    targets: str | list[str]
    allowed_keywords: NotRequired[list[str]]


@final
class SubscriptionConfig(TypedDict):
    url: str
    prefix: NotRequired[str]
    suffix: NotRequired[str]
    proxy_required: NotRequired[bool]
    node_override: NotRequired[dict[str, dict[str, Any]]]
    ignore: NotRequired[bool]
    mappings: list[SubscriptionMappingConfig]


@final
class StandardProxyConfig(TypedDict):
    type: Literal['standard']
    host: str
    protocol: NotRequired[str]


@final
class URLProxyConfig(TypedDict):
    type: Literal['url']
    url: str


@final
class Config(TypedDict):
    template_file: str
    providers_file: NotRequired[str]
    denied_keywords: NotRequired[list[str]]
    user_agent: NotRequired[str]
    proxy: NotRequired[StandardProxyConfig | URLProxyConfig]
    subscriptions: dict[str, SubscriptionConfig]


ClashProxyGroup = TypedDict(
    'ClashProxyGroup',
    {
        'name': str,
        'proxies': list[str],
    })
ClashProxy = TypedDict(
    'ClashProxy',
    {
        'name': str,
        'type': str,
    })
ClashRoot = TypedDict(
    'ClashRoot',
    {
        'proxies': list[ClashProxy],
        'proxy-groups': list[ClashProxyGroup],
    })


@final
class URLQuotingFormatter(string.Formatter):
    @override
    def convert_field(self, value: Any, conversion: str | None) -> Any:
        if conversion == 'q':
            return urllib.parse.quote_plus(str(value))
        else:
            return super().convert_field(value, conversion)


def make_keyword_pattern(keywords: Collection[str]) -> re.Pattern[str]:
    return re.compile('|'.join(map(re.escape, keywords)))


def merge_subscription_proxies(
        config: Config, template: ClashRoot,
        sub_name: str, sub_config: SubscriptionConfig,
        sub_root: ClashRoot) -> set[str]:
    denied_pattern = (
        make_keyword_pattern(config['denied_keywords'])
        if 'denied_keywords' in config
        else None)
    prefix = sub_config.get('prefix', '')
    suffix = sub_config.get('suffix', '')
    node_override = sub_config.get('node_override', {})
    proxy_names: set[str] = set()
    for i, clash_proxy in enumerate(sub_root['proxies']):
        if (denied_pattern is not None
            and denied_pattern.search(clash_proxy['name']) is not None):
            logger.warning(
                _('Ignoring node %(node)s from subscription %(sub)s'),
                {'node': clash_proxy['name'], 'sub': sub_name})
            continue

        logger.debug(_('Merging node #%(n)d (%(node)s)'),
                     {'n': i + 1, 'node': clash_proxy['name']})
        proxy_names.add(clash_proxy['name'])
        clash_proxy['name'] = prefix + clash_proxy['name'] + suffix
        override = node_override.get(
            clash_proxy['type'], node_override.get('', None))
        if override is not None:
            logger.debug(_('Applying the appropriate overriding'))
            cast(dict[str, Any], cast(object, clash_proxy)).update(override)
        template['proxies'].append(clash_proxy)
    logger.info(ngettext('Merged %d node from the subscription',
                         'Merged %d nodes from the subscription',
                         len(proxy_names)),
                len(proxy_names))
    return proxy_names


def merge_subscription_proxy_groups(
        config: Config, template: ClashRoot,
        sub_name: str, sub_config: SubscriptionConfig, sub_root: ClashRoot,
        sub_proxy_names: Container[str]) -> None:
    denied_pattern = (
        make_keyword_pattern(config['denied_keywords'])
        if 'denied_keywords' in config
        else None)
    prefix = sub_config.get('prefix', '')
    suffix = sub_config.get('suffix', '')

    template_proxy_groups = {g['name']: g for g in template['proxy-groups']}
    sub_proxy_groups = {g['name']: g for g in sub_root['proxy-groups']}
    for i, mapping in enumerate(sub_config['mappings']):
        logger.debug(
            _('Processing mapping #%(n)d (%(from)r to %(to)r)'),
            {'n': i + 1, 'from': mapping['source'], 'to': mapping['targets']})
        allowed_pattern = (
            make_keyword_pattern(mapping['allowed_keywords'])
            if 'allowed_keywords' in mapping
            else None)

        # Collect proxy names to be appended to target proxy groups
        applied_proxy_names: list[str] = []
        for proxy_name in sub_proxy_groups[mapping['source']]['proxies']:
            if (proxy_name not in sub_proxy_names
                and (denied_pattern is not None
                     and denied_pattern.search(proxy_name) is None)
                and proxy_name not in sub_proxy_groups
                and proxy_name not in BUILTIN_PROXY_NAMES):
                msg = _('Node %(node)s specified in node group %(group)s is '
                        + 'not in the configuration from the subscription %(sub)s')
                logger.warning(msg,
                               {
                                   'node': proxy_name,
                                   'group': mapping['source'],
                                   'sub': sub_name,
                               })
                logger.warning(_('The configuration might be corrupted'))
                continue
            if ((allowed_pattern is not None
                 and allowed_pattern.search(proxy_name) is None)
                or (denied_pattern is not None
                    and denied_pattern.search(proxy_name) is not None)
                or proxy_name in sub_proxy_groups
                or proxy_name in BUILTIN_PROXY_NAMES):
                logger.debug(
                    _('Ignoring node %(node)s specified in '
                      + 'node group %(group)s'),
                    {'node': proxy_name, 'group': mapping['source']})
                continue
            logger.debug(_('Node %s will be appended'), proxy_name)
            applied_proxy_names.append(prefix + proxy_name + suffix)

        if isinstance(mapping['targets'], str):
            mapping['targets'] = [mapping['targets']]
        for group_name in (gn or mapping['source'] for gn in mapping['targets']):
            logger.debug(_('Modifying node group %s'), group_name)
            group_proxy_names = (
                template_proxy_groups[group_name].setdefault('proxies', []))
            group_proxy_names.extend(
                n for n in applied_proxy_names if n not in group_proxy_names)
    logger.info(ngettext('Processed %d mapping for the subscription',
                         'Processed %d mappings for the subscription',
                         len(sub_config['mappings'])),
                len(sub_config['mappings']))

    forgotten_group_names = (
        set(sub_proxy_groups) - {m['source'] for m in sub_config['mappings']})
    if forgotten_group_names:
        logger.warning(_('These node groups in subscription %(sub)s has '
                         + 'not been processed: %(groups)r'),
                       {'sub': sub_name, 'groups': forgotten_group_names})
        logger.warning(_('Please consider update your configuration'))


def make_request(
        config: Config, url: str,
        *,
        proxy_required: bool = False) -> urllib.request.Request:
    user_agent = config.get('user_agent', USER_AGENT)
    request = urllib.request.Request(url)
    request.add_header('User-Agent', user_agent)
    if proxy_required and (proxy_config := config.get('proxy')) is not None:
        match proxy_config['type']:
            case 'standard':
                request.set_proxy(
                    proxy_config['host'], proxy_config.get('protocol', 'http'))
                logger.info(_('Applied standard proxy %s'), proxy_config['host'])
            case 'url':
                request.full_url = URLQuotingFormatter().format(
                    proxy_config['url'], url=url, ua=user_agent)
                logger.info(_('Rewrote the URL to %s'), request.full_url)
            case proxy_type:
                assert_never(proxy_type)
        logger.info(_('Applied the specified HTTP proxy'))
    return request


def retrieve_and_apply_subscriptions(config: Config, template: ClashRoot) -> None:
    for sub_name, sub_config in config['subscriptions'].items():
        logger.info(_('Retrieving the configuration from subscription %s'),
                    sub_name)

        request = make_request(
            config, sub_config['url'],
            proxy_required=sub_config.get('proxy_required', False))
        try:
            with urllib.request.urlopen(request) as response:
                # FIXME: Validation of the configuration
                sub_root = cast(ClashRoot, cast(object, yaml.load(response)))
        except urllib.error.URLError:
            msg = _('Failed to retrieve the configuration from subscription %s')
            if sub_config.get('ignore', False):
                logger.error(msg, sub_name)
                logger.error(_('Ignoring subscription %s due to ignore flag'), sub_name)
                continue
            else:
                logger.exception(msg, sub_name)
                continue

        prefix = sub_config.get('prefix', '')
        suffix = sub_config.get('suffix', '')
        sub_proxy_names = merge_subscription_proxies(
            config, template, sub_name, sub_config, sub_root)
        merge_subscription_proxy_groups(
            config, template, sub_name, sub_config, sub_root, sub_proxy_names)


Patchable = dict[str,list[Any] | dict[str, Any]]


def apply_patch(target: Patchable, patch: Patchable) -> Patchable:
    working_copy = copy.deepcopy(target)
    for key, value in patch.items():
        if isinstance(value, list):
            cast(list[Any], working_copy.setdefault(key, [])).extend(value)
        elif isinstance(value, dict):
            cast(dict[str, Any], working_copy.setdefault(key, {})).update(value)
        else:
            logger.error(_('The value type of key %(key)s in '
                           + 'the patch (%(type)s) is unacceptable'),
                         {'key': key, 'type': type(value).__qualname__})
            logger.error(_('Supported value types are: %r'),
                         {t.__qualname__ for t in {list, dict}})
    return working_copy


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


def main(argv: Sequence[str] | None = None) -> None:
    locale.setlocale(locale.LC_ALL, '')
    locale_name, __ = locale.getlocale(
        getattr(locale, 'LC_MESSAGES', locale.LC_CTYPE))
    if locale_name is not None:
        install_translation(locale_name)

    if argv is None:
        argv = sys.argv

    parser = argparse.ArgumentParser(
        prog=argv[0],
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
    args = parser.parse_args(argv[1:])

    if args.locale is not None:
        install_translation(args.locale)
    logging_level = (
        {0: logging.WARNING, 1: logging.INFO}.get(args.verbose, logging.DEBUG))
    logging.basicConfig(level=logging_level)

    logger.debug(_('Loading the configuration from %s'), args.config)
    try:
        with open(args.config, 'rb') as config_file:
            # FIXME: Validation of the configuration
            config = cast(Config, cast(object, tomllib.load(config_file)))
    except OSError:
        logger.exception(_('Failed to read the configuration from %s'),
                         args.config)
        sys.exit(EXIT_FAILURE)
    except tomllib.TOMLDecodeError:
        logger.exception(_('Failed to parse the configuration from %s'),
                         args.config)
        sys.exit(EXIT_FAILURE)
    logger.info(_('Successfully loaded the configuration from %s'), args.config)

    template_file_name = args.config.parent / config['template_file']
    logger.debug(_('Loading the template from %s'), template_file_name)
    try:
        # FIXME: Validation of the configuration
        template = cast(ClashRoot, cast(object, yaml.load(template_file_name)))
    except OSError:
        logger.exception(_('Failed to read the template from %s'),
                         template_file_name)
        sys.exit(EXIT_FAILURE)
    logger.info(_('Successfully loaded the template from %s'),
                template_file_name)

    retrieve_and_apply_subscriptions(config, template)
    if 'providers_file' in config:
        args.outdir.mkdir(exist_ok=True)
        providers_file_name = args.outdir / config['providers_file']
        logger.info(_('Writing the proxy-providers.yaml to %s'),
                    providers_file_name)
        try:
            with open(providers_file_name, 'w', encoding='utf_8') as providers_file:
                yaml.dump({'proxies': template['proxies']}, providers_file)
        except OSError:
            logger.exception(_('Failed to write the proxy-providers.yaml to %s'),
                             providers_file_name)
            sys.exit(EXIT_FAILURE)
    for profile_file_name in args.profiles.glob('*.yaml'):
        logger.info(_('Applying the patch %s'), profile_file_name)
        try:
            profile = yaml.load(profile_file_name)
        except OSError:
            logger.exception(_('Failed to read profile %s'), profile_file_name)
            sys.exit(EXIT_FAILURE)
        output = apply_patch(cast(Patchable, cast(object, template)), profile or {})

        logger.info(_('Writing the corresponding output'))
        output_file_name = args.outdir / profile_file_name.name
        try:
            args.outdir.mkdir(exist_ok=True)
            with open(output_file_name, 'w', encoding='utf_8') as output_file:
                yaml.dump(output, output_file)
        except OSError:
            logger.exception(_('Failed to write the output of patch %s'),
                             profile_file_name)
            sys.exit(EXIT_FAILURE)


if __name__ == '__main__':
    main()
