import copy
import logging
import re
from collections.abc import Collection
from typing import Any, cast

from .constants import BUILTIN_PROXY_NAMES
from .models import (
    ClashRoot,
    Config,
    IssueSeverity,
    MergedProxyRecord,
    Patchable,
    SubscriptionConfig,
    SubscriptionIssue,
    SubscriptionMergeResult,
    YAMLMapping,
)

logger = logging.getLogger(__name__)


def make_keyword_pattern(keywords: Collection[str]) -> re.Pattern[str]:
    return re.compile('|'.join(map(re.escape, keywords)))


def make_subscription_issue(
        code: str,
        severity: IssueSeverity,
        **details: object) -> SubscriptionIssue:
    return SubscriptionIssue(
        code=code,
        severity=severity,
        details=cast(dict[str, Any], details),
    )


def make_unique_proxy_name(
        base_name: str,
        sub_name: str,
        existing_names: Collection[str]) -> str:
    if base_name not in existing_names:
        return base_name

    candidate = f'{base_name} [{sub_name}]'
    suffix = 2
    while candidate in existing_names:
        candidate = f'{base_name} [{sub_name} #{suffix}]'
        suffix += 1
    return candidate


def build_source_group_membership(sub_root: ClashRoot) -> dict[str, list[str]]:
    source_groups = {group['name'] for group in sub_root['proxy-groups']}
    membership: dict[str, list[str]] = {}
    for group in sub_root['proxy-groups']:
        for proxy_name in group.get('proxies', []):
            if proxy_name in source_groups or proxy_name in BUILTIN_PROXY_NAMES:
                continue
            membership.setdefault(proxy_name, []).append(group['name'])
    return membership


def merge_subscription_proxies(
        config: Config, template: ClashRoot,
        sub_name: str, sub_config: SubscriptionConfig,
        sub_root: ClashRoot,
        issues: list[SubscriptionIssue] | None = None) -> SubscriptionMergeResult:
    denied_pattern = (
        make_keyword_pattern(config['denied_keywords'])
        if 'denied_keywords' in config
        else None)
    prefix = sub_config.get('prefix', '')
    suffix = sub_config.get('suffix', '')
    node_override = sub_config.get('node_override', {})
    existing_names = {proxy['name'] for proxy in template['proxies']}
    merged_proxy_count = 0
    renamed_proxy_count = 0
    proxy_name_map: dict[str, list[str]] = {}
    merged_proxies: list[MergedProxyRecord] = []
    source_group_membership = build_source_group_membership(sub_root)
    for i, clash_proxy in enumerate(sub_root['proxies']):
        original_name = clash_proxy['name']
        if (denied_pattern is not None
            and denied_pattern.search(original_name) is not None):
            logger.warning(
                _('Ignoring node %(node)s from subscription %(sub)s'),
                {'node': original_name, 'sub': sub_name})
            continue

        logger.debug(_('Merging node #%(n)d (%(node)s)'),
                     {'n': i + 1, 'node': original_name})
        desired_name = prefix + original_name + suffix
        resolved_name = make_unique_proxy_name(
            desired_name, sub_name, existing_names)
        if resolved_name != desired_name:
            logger.warning(
                _('Renamed duplicated node %(old)s to %(new)s'),
                {'old': desired_name, 'new': resolved_name},
            )
            renamed_proxy_count += 1
            if issues is not None:
                issues.append(
                    make_subscription_issue(
                        'duplicate_proxy_name',
                        'warning',
                        original_name=desired_name,
                        resolved_name=resolved_name,
                    ))
        clash_proxy['name'] = resolved_name
        override = node_override.get(
            clash_proxy['type'], node_override.get('', None))
        if override is not None:
            logger.debug(_('Applying the appropriate overriding'))
            cast(dict[str, Any], cast(object, clash_proxy)).update(override)
        template['proxies'].append(clash_proxy)
        existing_names.add(resolved_name)
        proxy_name_map.setdefault(original_name, []).append(resolved_name)
        merged_proxies.append(
            MergedProxyRecord(
                source_name=sub_name,
                source_url=sub_config['url'],
                original_name=original_name,
                final_name=resolved_name,
                proxy=copy.deepcopy(cast(dict[str, Any], cast(object, clash_proxy))),
                source_groups=list(source_group_membership.get(original_name, [])),
                was_renamed=resolved_name != desired_name,
            ),
        )
        merged_proxy_count += 1
    logger.info(ngettext('Merged %d node from the subscription',
                         'Merged %d nodes from the subscription',
                         merged_proxy_count),
                merged_proxy_count)
    return SubscriptionMergeResult(
        name_map=proxy_name_map,
        merged_proxy_count=merged_proxy_count,
        renamed_proxy_count=renamed_proxy_count,
        merged_proxies=merged_proxies,
    )


def merge_subscription_proxy_groups(
        config: Config, template: ClashRoot,
        sub_name: str, sub_config: SubscriptionConfig, sub_root: ClashRoot,
        sub_proxy_names: dict[str, list[str]],
        issues: list[SubscriptionIssue] | None = None) -> None:
    denied_pattern = (
        make_keyword_pattern(config['denied_keywords'])
        if 'denied_keywords' in config
        else None)

    template_proxy_groups = {g['name']: g for g in template['proxy-groups']}
    sub_proxy_groups = {g['name']: g for g in sub_root['proxy-groups']}
    mappings = sub_config.get('mappings', [])
    for i, mapping in enumerate(mappings):
        logger.debug(
            _('Processing mapping #%(n)d (%(from)r to %(to)r)'),
            {'n': i + 1, 'from': mapping['source'], 'to': mapping['targets']})
        allowed_pattern = (
            make_keyword_pattern(mapping['allowed_keywords'])
            if 'allowed_keywords' in mapping
            else None)

        source_group = sub_proxy_groups.get(mapping['source'])
        if source_group is None:
            logger.warning(
                _('Node group %(group)s specified in mappings is not in subscription %(sub)s'),
                {'group': mapping['source'], 'sub': sub_name},
            )
            if issues is not None:
                issues.append(
                    make_subscription_issue(
                        'missing_source_group',
                        'warning',
                        source_group=mapping['source'],
                    ))
            continue

        applied_proxy_names: list[str] = []
        for proxy_name in source_group['proxies']:
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
            if proxy_name not in sub_proxy_names:
                msg = _('Node %(node)s specified in node group %(group)s is '
                        + 'not in the configuration from the subscription %(sub)s')
                logger.warning(msg,
                               {
                                   'node': proxy_name,
                                   'group': mapping['source'],
                                   'sub': sub_name,
                               })
                logger.warning(_('The configuration might be corrupted'))
                if issues is not None:
                    issues.append(
                        make_subscription_issue(
                            'missing_group_node',
                            'warning',
                            node_name=proxy_name,
                            source_group=mapping['source'],
                        ))
                continue
            logger.debug(_('Node %s will be appended'), proxy_name)
            applied_proxy_names.extend(
                name for name in sub_proxy_names[proxy_name]
                if name not in applied_proxy_names)

        target_names = (
            [mapping['targets']]
            if isinstance(mapping['targets'], str)
            else mapping['targets'])
        for group_name in (gn or mapping['source'] for gn in target_names):
            logger.debug(_('Modifying node group %s'), group_name)
            target_group = template_proxy_groups.get(group_name)
            if target_group is None:
                logger.warning(
                    _('Target node group %(group)s is not in the template'),
                    {'group': group_name},
                )
                if issues is not None:
                    issues.append(
                        make_subscription_issue(
                            'missing_target_group',
                            'warning',
                            target_group=group_name,
                        ))
                continue
            group_proxy_names = target_group.setdefault('proxies', [])
            group_proxy_names.extend(
                n for n in applied_proxy_names if n not in group_proxy_names)
    logger.info(ngettext('Processed %d mapping for the subscription',
                         'Processed %d mappings for the subscription',
                         len(mappings)),
                len(mappings))

    forgotten_group_names = (
        set(sub_proxy_groups) - {m['source'] for m in mappings})
    if forgotten_group_names:
        logger.warning(_('These node groups in subscription %(sub)s has '
                         + 'not been processed: %(groups)r'),
                       {'sub': sub_name, 'groups': forgotten_group_names})
        logger.warning(_('Please consider update your configuration'))
        if issues is not None:
            issues.append(
                make_subscription_issue(
                    'unmapped_proxy_groups',
                    'warning',
                    group_names=sorted(forgotten_group_names),
                ))


def _apply_patch(working_copy: YAMLMapping, patch: Patchable) -> None:
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


def apply_patch(target: YAMLMapping, patch: Patchable) -> YAMLMapping:
    working_copy = copy.deepcopy(target)
    _apply_patch(working_copy, patch)
    return working_copy


def apply_patches(target: YAMLMapping, patches: Collection[Patchable]) -> YAMLMapping:
    working_copy = copy.deepcopy(target)
    for patch in patches:
        _apply_patch(working_copy, patch)
    return working_copy
