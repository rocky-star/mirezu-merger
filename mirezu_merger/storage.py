import logging
import tomllib
import urllib.parse
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from .constants import (
    GENERATED_CONFIG_FILE_NAME,
    GENERATED_PROFILES_DIR_NAME,
    GENERATED_TEMPLATE_FILE_NAME,
)
from .errors import (
    ConfigParseError,
    ConfigReadError,
    GeneratedAssetWriteError,
    OutputWriteError,
    PathOperationError,
    ProfileReadError,
    ProvidersWriteError,
    SessionParseError,
    SessionReadError,
    SessionWriteError,
    TemplateReadError,
)
from .models import (
    ClashRoot,
    Config,
    LoadedBuildConfig,
    LoadedProfile,
    Patchable,
    StandardProxyConfig,
    URLProxyConfig,
    WizardAIConfig,
    WizardAIAnalysisMode,
    WizardAnalysisConfig,
    WizardNetworkOptions,
    WizardSession,
    WizardStrategy,
    WizardTarget,
    YAMLMapping,
)
from .wizard_presets import STRATEGY_ORDER, TARGET_ORDER
from .yaml_support import yaml

logger = logging.getLogger(__name__)
SENSITIVE_QUERY_KEYS = frozenset({
    'token',
    'access_token',
    'api_key',
    'apikey',
    'key',
    'secret',
    'password',
    'passwd',
    'pwd',
    'sig',
    'signature',
})
REDACTED_VALUE = '<redacted>'


def load_config(config_path: Path) -> Config:
    try:
        with open(config_path, 'rb') as config_file:
            return cast(Config, cast(object, tomllib.load(config_file)))
    except OSError as error:
        raise ConfigReadError(config_path) from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigParseError(config_path) from error


def resolve_template_path(config_path: Path, config: Config) -> Path:
    return config_path.parent / config['template_file']


def load_template(template_path: Path) -> ClashRoot:
    try:
        return cast(ClashRoot, cast(object, yaml.load(template_path)))
    except OSError as error:
        raise TemplateReadError(template_path) from error


def load_build_config(config_path: Path) -> LoadedBuildConfig:
    logger.debug(_('Loading the configuration from %s'), config_path)
    config = load_config(config_path)
    logger.info(_('Successfully loaded the configuration from %s'), config_path)

    template_path = resolve_template_path(config_path, config)
    logger.debug(_('Loading the template from %s'), template_path)
    template = load_template(template_path)
    logger.info(_('Successfully loaded the template from %s'), template_path)

    return LoadedBuildConfig(
        config_path=config_path,
        config=config,
        template_path=template_path,
        template=template,
    )


def load_profiles(profiles_dir: Path) -> list[LoadedProfile]:
    profiles: list[LoadedProfile] = []
    for profile_path in profiles_dir.glob('*.yaml'):
        try:
            patch = cast(YAMLMapping, cast(object, yaml.load(profile_path) or {}))
        except OSError as error:
            raise ProfileReadError(profile_path) from error
        profiles.append(
            LoadedProfile(
                path=profile_path,
                patch=cast(Patchable, patch),
            ))
    return profiles


def write_yaml_document(
        path: Path, content: YAMLMapping,
        error_type: type[PathOperationError]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf_8') as output_file:
            yaml.dump(content, output_file)
    except OSError as error:
        raise error_type(path) from error


def write_providers_document(path: Path, content: YAMLMapping) -> None:
    write_yaml_document(path, content, ProvidersWriteError)


def write_output_document(path: Path, content: YAMLMapping) -> None:
    write_yaml_document(path, content, OutputWriteError)


def write_text_document(
        path: Path, content: str,
        error_type: type[PathOperationError]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf_8', newline='\n') as output_file:
            output_file.write(content)
    except OSError as error:
        raise error_type(path) from error


def quote_toml_string(value: str) -> str:
    escaped = (
        value
        .replace('\\', '\\\\')
        .replace('"', '\\"')
        .replace('\b', '\\b')
        .replace('\t', '\\t')
        .replace('\n', '\\n')
        .replace('\f', '\\f')
        .replace('\r', '\\r')
    )
    return f'"{escaped}"'


def format_toml_string_array(values: Sequence[str]) -> str:
    return '[' + ', '.join(quote_toml_string(value) for value in values) + ']'


def build_private_companion_path(path: Path) -> Path:
    return path.with_name(path.stem + '.private' + path.suffix)


def is_sensitive_subscription_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.username is not None or parsed.password is not None:
        return True
    query_items = urllib.parse.parse_qsl(
        parsed.query,
        keep_blank_values=True,
    )
    return any(
        key.lower() in SENSITIVE_QUERY_KEYS
        for key, __ in query_items
    )


def redact_sensitive_subscription_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    netloc = parsed.netloc
    if parsed.username is not None or parsed.password is not None:
        host = parsed.hostname or ''
        if parsed.port is not None:
            host = f'{host}:{parsed.port}'
        netloc = f'{REDACTED_VALUE}@{host}'

    query_items = urllib.parse.parse_qsl(
        parsed.query,
        keep_blank_values=True,
    )
    redacted_items = [
        (
            key,
            REDACTED_VALUE if key.lower() in SENSITIVE_QUERY_KEYS else value,
        )
        for key, value in query_items
    ]
    return urllib.parse.urlunparse(
        parsed._replace(
            netloc=netloc,
            query=urllib.parse.urlencode(redacted_items),
        ),
    )


def redact_subscription_urls(urls: Sequence[str]) -> tuple[list[str], bool]:
    redacted_urls: list[str] = []
    changed = False
    for url in urls:
        if is_sensitive_subscription_url(url):
            redacted_urls.append(redact_sensitive_subscription_url(url))
            changed = True
        else:
            redacted_urls.append(url)
    return redacted_urls, changed


def has_redacted_subscription_url(urls: Sequence[str]) -> bool:
    for url in urls:
        if REDACTED_VALUE in url:
            return True
        parsed = urllib.parse.urlparse(url)
        if (
            parsed.username == REDACTED_VALUE
            or parsed.password == REDACTED_VALUE
        ):
            return True
        query_items = urllib.parse.parse_qsl(
            parsed.query,
            keep_blank_values=True,
        )
        if any(value == REDACTED_VALUE for __, value in query_items):
            return True
    return False


def merge_subscription_urls(
        public_urls: Sequence[str],
        private_urls: Sequence[str]) -> list[str]:
    if len(public_urls) != len(private_urls):
        raise ValueError('subscription URL count mismatch')
    merged_urls: list[str] = []
    for public_url, private_url in zip(public_urls, private_urls, strict=True):
        merged_urls.append(
            private_url if has_redacted_subscription_url([public_url]) else public_url,
        )
    return merged_urls


def serialize_wizard_session(
        session: WizardSession,
        *,
        redact_sensitive_urls: bool = False) -> str:
    subscription_urls = list(session.subscription_urls)
    urls_were_redacted = False
    if redact_sensitive_urls:
        subscription_urls, urls_were_redacted = redact_subscription_urls(
            subscription_urls,
        )
    lines = [
        f'outdir = {quote_toml_string(str(session.outdir.resolve()))}',
        ('subscription_urls = ' + format_toml_string_array(subscription_urls)),
        f'targets = {format_toml_string_array(session.targets)}',
        f'strategy = {quote_toml_string(session.strategy)}',
        f'ai_analysis_mode = {quote_toml_string(session.ai_analysis_mode)}',
        '',
        '[network]',
        ('allow_subscription_failures = '
         + str(session.network.allow_subscription_failures).lower()),
    ]
    if urls_were_redacted:
        lines.insert(
            0,
            '# Sensitive subscription URLs were redacted. '
            + 'Use the companion *.private.toml file for resume or rebuild.',
        )
    if session.network.user_agent is not None:
        lines.append(
            f'user_agent = {quote_toml_string(session.network.user_agent)}')
    if session.network.proxy is not None:
        lines.extend([
            '',
            '[network.proxy]',
            f'type = {quote_toml_string(session.network.proxy["type"])}',
        ])
        match session.network.proxy['type']:
            case 'standard':
                lines.append(
                    f'host = {quote_toml_string(session.network.proxy["host"])}')
                if 'protocol' in session.network.proxy:
                    lines.append(
                        'protocol = '
                        + quote_toml_string(
                            session.network.proxy['protocol']))
            case 'url':
                lines.append(
                    f'url = {quote_toml_string(session.network.proxy["url"])}')
    return '\n'.join(lines) + '\n'


def serialize_config(
        config: Config,
        *,
        redact_sensitive_urls: bool = False) -> str:
    lines = [
        f'template_file = {quote_toml_string(config["template_file"])}',
    ]
    urls_were_redacted = False
    if 'providers_file' in config:
        lines.append(
            f'providers_file = {quote_toml_string(config["providers_file"])}')
    if 'denied_keywords' in config:
        lines.append(
            'denied_keywords = '
            + format_toml_string_array(config['denied_keywords']))
    if 'user_agent' in config:
        lines.append(
            f'user_agent = {quote_toml_string(config["user_agent"])}')
    if 'proxy' in config:
        lines.extend([
            '',
            '[proxy]',
            f'type = {quote_toml_string(config["proxy"]["type"])}',
        ])
        match config['proxy']['type']:
            case 'standard':
                lines.append(
                    f'host = {quote_toml_string(config["proxy"]["host"])}')
                if 'protocol' in config['proxy']:
                    lines.append(
                        'protocol = '
                        + quote_toml_string(config['proxy']['protocol']))
            case 'url':
                lines.append(
                    f'url = {quote_toml_string(config["proxy"]["url"])}')

    for sub_name, sub_config in config['subscriptions'].items():
        subscription_url = sub_config['url']
        if redact_sensitive_urls and is_sensitive_subscription_url(subscription_url):
            subscription_url = redact_sensitive_subscription_url(subscription_url)
            urls_were_redacted = True
        lines.extend([
            '',
            f'[subscriptions.{sub_name}]',
            f'url = {quote_toml_string(subscription_url)}',
        ])
        optional_keys = ('prefix', 'suffix', 'proxy_required', 'ignore')
        for key in optional_keys:
            if key in sub_config:
                value = cast(object, sub_config[key])
                if isinstance(value, bool):
                    lines.append(f'{key} = {str(value).lower()}')
                elif isinstance(value, str):
                    lines.append(f'{key} = {quote_toml_string(value)}')
    if urls_were_redacted:
        lines.insert(
            0,
            '# Sensitive subscription URLs were redacted. '
            + 'Use the companion *.private.toml file for rebuild.',
        )
    return '\n'.join(lines) + '\n'


def parse_string_list(
        raw_value: object,
        error_path: Path) -> list[str]:
    if not isinstance(raw_value, list) or not all(
            isinstance(item, str) for item in raw_value):
        raise SessionParseError(error_path)
    return cast(list[str], raw_value)


def parse_wizard_targets(
        raw_value: object,
        error_path: Path) -> list[WizardTarget]:
    targets = parse_string_list(raw_value, error_path)
    if not targets:
        raise SessionParseError(error_path)
    selected_targets: list[WizardTarget] = []
    for target in targets:
        if target not in TARGET_ORDER or target in selected_targets:
            raise SessionParseError(error_path)
        selected_targets.append(cast(WizardTarget, target))
    return selected_targets


def parse_wizard_strategy(
        raw_value: object,
        error_path: Path) -> WizardStrategy:
    if not isinstance(raw_value, str) or raw_value not in STRATEGY_ORDER:
        raise SessionParseError(error_path)
    return cast(WizardStrategy, raw_value)


def parse_wizard_ai_analysis_mode(
        raw_value: object,
        error_path: Path) -> WizardAIAnalysisMode:
    if raw_value not in {'disabled', 'assisted', 'full'}:
        raise SessionParseError(error_path)
    return cast(WizardAIAnalysisMode, raw_value)


def parse_wizard_network_options(
        raw_value: object,
        error_path: Path) -> WizardNetworkOptions:
    if raw_value is None:
        return WizardNetworkOptions()
    if not isinstance(raw_value, dict):
        raise SessionParseError(error_path)

    allow_failures = raw_value.get('allow_subscription_failures', True)
    if not isinstance(allow_failures, bool):
        raise SessionParseError(error_path)

    user_agent = raw_value.get('user_agent')
    if user_agent is not None and not isinstance(user_agent, str):
        raise SessionParseError(error_path)

    proxy = raw_value.get('proxy')
    proxy_config: StandardProxyConfig | URLProxyConfig | None = None
    if proxy is not None:
        if not isinstance(proxy, dict):
            raise SessionParseError(error_path)
        proxy_type = proxy.get('type')
        if proxy_type == 'standard':
            host = proxy.get('host')
            protocol = proxy.get('protocol', 'http')
            if not isinstance(host, str) or not isinstance(protocol, str):
                raise SessionParseError(error_path)
            proxy_config = cast(
                StandardProxyConfig,
                {
                    'type': 'standard',
                    'host': host,
                    'protocol': protocol,
                },
            )
        elif proxy_type == 'url':
            proxy_url = proxy.get('url')
            if not isinstance(proxy_url, str):
                raise SessionParseError(error_path)
            proxy_config = cast(
                URLProxyConfig,
                {
                    'type': 'url',
                    'url': proxy_url,
                },
            )
        else:
            raise SessionParseError(error_path)

    return WizardNetworkOptions(
        proxy=proxy_config,
        user_agent=cast(str | None, user_agent),
        allow_subscription_failures=allow_failures,
    )


def load_wizard_analysis_config(
        path: Path) -> WizardAnalysisConfig:
    try:
        with open(path, 'rb') as config_file:
            loaded = cast(dict[str, object], cast(object, tomllib.load(config_file)))
    except FileNotFoundError:
        return WizardAnalysisConfig()
    except (OSError, tomllib.TOMLDecodeError) as error:
        logger.warning(_('Failed to read wizard analysis config from %s: %s'),
                       path, error)
        return WizardAnalysisConfig()

    analysis = loaded.get('analysis')
    if not isinstance(analysis, dict):
        return WizardAnalysisConfig()
    raw_ai = analysis.get('ai')
    if not isinstance(raw_ai, dict):
        return WizardAnalysisConfig()

    base_url = raw_ai.get('base_url')
    api_key = raw_ai.get('api_key')
    model = raw_ai.get('model', 'gpt-4.1-mini')
    if (
        not isinstance(base_url, str)
        or not isinstance(api_key, str)
        or not isinstance(model, str)
        or not base_url.strip()
        or not api_key.strip()
    ):
        return WizardAnalysisConfig()
    return WizardAnalysisConfig(
        ai=WizardAIConfig(
            base_url=base_url.strip(),
            api_key=api_key.strip(),
            model=model.strip(),
        ),
    )


def load_wizard_session(path: Path) -> WizardSession:
    try:
        with open(path, 'rb') as session_file:
            loaded = cast(dict[str, object], cast(object, tomllib.load(session_file)))
    except OSError as error:
        raise SessionReadError(path) from error
    except tomllib.TOMLDecodeError as error:
        raise SessionParseError(path) from error

    try:
        outdir = loaded.get('outdir')
        if not isinstance(outdir, str):
            raise SessionParseError(path)
        raw_ai_analysis_mode = loaded.get('ai_analysis_mode')
        if raw_ai_analysis_mode is not None:
            ai_analysis_mode = parse_wizard_ai_analysis_mode(
                raw_ai_analysis_mode,
                path,
            )
        else:
            ai_assisted_analysis = loaded.get('ai_assisted_analysis', False)
            if not isinstance(ai_assisted_analysis, bool):
                raise SessionParseError(path)
            ai_analysis_mode = (
                'assisted' if ai_assisted_analysis else 'disabled'
            )
        session = WizardSession(
            subscription_urls=parse_string_list(
                loaded.get('subscription_urls'),
                path,
            ),
            targets=parse_wizard_targets(loaded.get('targets'), path),
            strategy=parse_wizard_strategy(loaded.get('strategy'), path),
            network=parse_wizard_network_options(loaded.get('network'), path),
            outdir=Path(outdir),
            ai_analysis_mode=ai_analysis_mode,
        )
        if has_redacted_subscription_url(session.subscription_urls):
            private_path = build_private_companion_path(path)
            if not private_path.is_file():
                raise SessionParseError(path)
            private_session = load_wizard_session(private_path)
            return WizardSession(
                subscription_urls=merge_subscription_urls(
                    session.subscription_urls,
                    private_session.subscription_urls,
                ),
                targets=session.targets,
                strategy=session.strategy,
                network=session.network,
                outdir=session.outdir,
                ai_analysis_mode=session.ai_analysis_mode,
            )
        return session
    except KeyError as error:
        raise SessionParseError(path) from error
    except ValueError as error:
        raise SessionParseError(path) from error


def write_wizard_session(path: Path, session: WizardSession) -> list[Path]:
    written_paths = [path]
    write_text_document(
        path,
        serialize_wizard_session(session, redact_sensitive_urls=True),
        SessionWriteError,
    )
    if any(is_sensitive_subscription_url(url) for url in session.subscription_urls):
        private_path = build_private_companion_path(path)
        write_text_document(
            private_path,
            serialize_wizard_session(session),
            SessionWriteError,
        )
        written_paths.append(private_path)
    return written_paths


def write_generated_plan_assets(
        root: Path,
        generated_config: Config,
        generated_template: ClashRoot,
        generated_profiles: list[LoadedProfile]) -> list[Path]:
    config_path = root / GENERATED_CONFIG_FILE_NAME
    write_text_document(
        config_path,
        serialize_config(generated_config, redact_sensitive_urls=True),
        GeneratedAssetWriteError,
    )

    generated_paths = [config_path]
    if any(
            is_sensitive_subscription_url(subscription['url'])
            for subscription in generated_config['subscriptions'].values()):
        private_config_path = build_private_companion_path(config_path)
        write_text_document(
            private_config_path,
            serialize_config(generated_config),
            GeneratedAssetWriteError,
        )
        generated_paths.append(private_config_path)

    template_path = root / GENERATED_TEMPLATE_FILE_NAME
    write_yaml_document(
        template_path,
        cast(YAMLMapping, cast(object, generated_template)),
        GeneratedAssetWriteError,
    )

    generated_paths.append(template_path)
    profiles_root = root / GENERATED_PROFILES_DIR_NAME
    for profile in generated_profiles:
        profile_path = profiles_root / profile.path.name
        write_yaml_document(
            profile_path,
            cast(YAMLMapping, cast(object, profile.patch)),
            GeneratedAssetWriteError,
        )
        generated_paths.append(profile_path)
    return generated_paths
