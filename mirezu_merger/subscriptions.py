import logging
import socket
import string
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, cast, final

import ruamel.yaml
from typing_extensions import assert_never, override

from .constants import (
    SUBSCRIPTION_FETCH_RETRIES,
    SUBSCRIPTION_FETCH_TIMEOUT,
    USER_AGENT,
)
from .i18n import ensure_translation
from .merge import (
    merge_subscription_proxies,
    merge_subscription_proxy_groups,
    make_subscription_issue,
)
from .models import (
    ClashRoot,
    Config,
    SubscriptionConfig,
    SubscriptionProbeResult,
    SubscriptionReport,
)
from .yaml_support import yaml

logger = logging.getLogger(__name__)
RETRYABLE_HTTP_STATUS_CODES = frozenset([408, 429, 500, 502, 503, 504])


@final
class URLQuotingFormatter(string.Formatter):
    @override
    def convert_field(self, value: Any, conversion: str | None) -> Any:
        if conversion == 'q':
            return urllib.parse.quote_plus(str(value))
        else:
            return super().convert_field(value, conversion)


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


def should_retry_fetch(url: str, error: urllib.error.URLError | OSError) -> bool:
    parsed_url = urllib.parse.urlparse(url)
    if parsed_url.scheme not in {'http', 'https'}:
        return False
    if isinstance(error, urllib.error.HTTPError):
        return error.code in RETRYABLE_HTTP_STATUS_CODES

    reason = error
    if isinstance(error, urllib.error.URLError):
        reason = cast(object, getattr(error, 'reason', error))

    if isinstance(reason, (TimeoutError, socket.timeout)):
        return True
    return (
        isinstance(reason, OSError)
        and not isinstance(reason, FileNotFoundError)
    )


def format_fetch_error_reason(error: urllib.error.URLError | OSError) -> str:
    reason = getattr(error, 'reason', error)
    return str(reason)


def mark_probe_failed(
        result: SubscriptionProbeResult,
        code: str,
        *,
        ignore_failures: bool,
        **details: object) -> None:
    result.status = 'ignored' if ignore_failures else 'failed'
    result.issues.append(
        make_subscription_issue(
            code,
            'warning' if ignore_failures else 'error',
            **details,
        ))


def normalize_subscription_root(
        sub_name: str,
        sub_root: object,
        result: SubscriptionProbeResult) -> ClashRoot | None:
    if not isinstance(sub_root, dict):
        logger.error(
            _('The configuration retrieved from subscription %s is empty or invalid'),
            sub_name,
        )
        mark_probe_failed(
            result,
            'invalid_subscription_root',
            ignore_failures=result.ignore_failures,
        )
        return None

    proxies = cast(object, sub_root.get('proxies'))
    if not isinstance(proxies, list):
        logger.error(
            _('The configuration retrieved from subscription %s does not contain proxies'),
            sub_name,
        )
        mark_probe_failed(
            result,
            'missing_proxies',
            ignore_failures=result.ignore_failures,
        )
        return None

    proxy_groups = cast(object, sub_root.get('proxy-groups'))
    if proxy_groups is None:
        logger.warning(
            _('The configuration retrieved from subscription %s does not contain proxy-groups'),
            sub_name,
        )
        result.issues.append(
            make_subscription_issue('missing_proxy_groups', 'warning'))
        proxy_groups = []
    elif not isinstance(proxy_groups, list):
        logger.error(
            _('The proxy-groups in subscription %s is invalid'),
            sub_name,
        )
        mark_probe_failed(
            result,
            'invalid_proxy_groups',
            ignore_failures=result.ignore_failures,
        )
        return None

    result.detected_proxy_count = len(proxies)
    result.detected_proxy_group_count = len(proxy_groups)
    return cast(
        ClashRoot,
        {
            'proxies': proxies,
            'proxy-groups': proxy_groups,
        },
    )


def probe_subscription(
        config: Config,
        sub_name: str,
        sub_config: SubscriptionConfig) -> SubscriptionProbeResult:
    result = SubscriptionProbeResult(
        name=sub_name,
        url=sub_config['url'],
        ignore_failures=sub_config.get('ignore', False),
        status='ready',
    )
    logger.info(_('Retrieving the configuration from subscription %s'),
                sub_name)

    request = make_request(
        config, sub_config['url'],
        proxy_required=sub_config.get('proxy_required', False))
    sub_root = None
    max_attempts = SUBSCRIPTION_FETCH_RETRIES + 1
    attempt_count = 0
    while attempt_count < max_attempts:
        attempt_count += 1
        try:
            with urllib.request.urlopen(
                    request,
                    timeout=SUBSCRIPTION_FETCH_TIMEOUT) as response:
                sub_root = yaml.load(response)
            break
        except ruamel.yaml.YAMLError as error:
            logger.error(
                _('Failed to parse the configuration from subscription %s'),
                sub_name,
            )
            mark_probe_failed(
                result,
                'invalid_yaml',
                ignore_failures=result.ignore_failures,
                reason=str(error),
            )
            return result
        except (urllib.error.URLError, OSError) as error:
            if (attempt_count < max_attempts
                and should_retry_fetch(sub_config['url'], error)):
                logger.warning(
                    _('Retrying subscription %(sub)s after transient fetch failure '
                      + '(%(attempt)d/%(max_attempts)d)'),
                    {
                        'sub': sub_name,
                        'attempt': attempt_count,
                        'max_attempts': max_attempts,
                    },
                )
                continue
            msg = _('Failed to retrieve the configuration from subscription %s')
            if result.ignore_failures:
                logger.error(msg, sub_name)
                logger.error(_('Ignoring subscription %s due to ignore flag'),
                             sub_name)
            else:
                logger.error(msg, sub_name)
            mark_probe_failed(
                result,
                'fetch_failed',
                ignore_failures=result.ignore_failures,
                reason=format_fetch_error_reason(error),
                attempts=attempt_count,
            )
            return result

    if sub_root is None:
        if result.ignore_failures:
            logger.error(
                _('The configuration retrieved from subscription %s is empty or invalid'),
                sub_name)
            logger.error(_('Ignoring subscription %s due to ignore flag'),
                         sub_name)
        else:
            logger.critical(
                _('The configuration retrieved from subscription %s is empty or invalid'),
                sub_name)
        mark_probe_failed(
            result,
            'empty_subscription',
            ignore_failures=result.ignore_failures,
        )
        return result

    result.root = normalize_subscription_root(sub_name, sub_root, result)
    return result


def probe_subscriptions(config: Config) -> SubscriptionReport:
    ensure_translation()
    return SubscriptionReport(
        results=[
            probe_subscription(config, sub_name, sub_config)
            for sub_name, sub_config in config['subscriptions'].items()
        ])


def apply_subscription_report(
        config: Config,
        template: ClashRoot,
        report: SubscriptionReport) -> None:
    results_by_name = {result.name: result for result in report.results}
    for sub_name, sub_config in config['subscriptions'].items():
        result = results_by_name[sub_name]
        if result.root is None or result.status != 'ready':
            continue
        merge_result = merge_subscription_proxies(
            config,
            template,
            sub_name,
            sub_config,
            result.root,
            result.issues,
        )
        result.merged_proxy_count = merge_result.merged_proxy_count
        result.renamed_proxy_count = merge_result.renamed_proxy_count
        result.merged_proxies = merge_result.merged_proxies
        merge_subscription_proxy_groups(
            config,
            template,
            sub_name,
            sub_config,
            result.root,
            merge_result.name_map,
            result.issues,
        )
