import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import replace
from typing import Any, cast

from .models import (
    AnalyzedNode,
    ClashRoot,
    ConflictSummary,
    GeneratedGroupPlan,
    MergedProxyRecord,
    NodeAnalysisReport,
    NodeAnalysisStats,
    NodeLabel,
    NodeLabelConfidence,
    SubscriptionReport,
    WizardAIConfig,
    WizardAIAnalysisMode,
    WizardStrategy,
)

logger = logging.getLogger(__name__)

REGION_ORDER = (
    'Hong Kong',
    'Taiwan',
    'Japan',
    'Singapore',
    'United States',
)
USAGE_ORDER = (
    'AI',
    'Streaming',
)
CORE_GROUPS = (
    'Proxy',
    'Auto',
    'Fallback',
    'All Nodes',
)
GROUP_SCORE_WEIGHT = 3
NAME_SCORE_WEIGHT = 2
SERVER_SCORE_WEIGHT = 1
FEATURE_SCORE_WEIGHT = 1
USAGE_GROUP_BROADNESS_THRESHOLD = 0.8
USAGE_GROUP_MIN_TOTAL_NODES_FOR_BROADNESS = 5

REGION_RULES: dict[str, dict[str, tuple[str, ...]]] = {
    'Hong Kong': {
        'group': ('hong kong', 'hongkong', 'hk', '香港', '港'),
        'name': ('hong kong', 'hongkong', 'hk', 'hkg', '香港'),
        'server': ('.hk', 'hongkong', 'hkbn', 'hkt'),
    },
    'Taiwan': {
        'group': ('taiwan', 'tw', 'taipei', '台湾', '台灣'),
        'name': ('taiwan', 'tw', 'taipei', '台湾', '台灣'),
        'server': ('.tw', 'taiwan', 'taipei'),
    },
    'Japan': {
        'group': ('japan', 'jp', 'tokyo', 'osaka', '日本'),
        'name': ('japan', 'jp', 'tokyo', 'osaka', '日本'),
        'server': ('.jp', 'japan', 'tokyo', 'osaka'),
    },
    'Singapore': {
        'group': ('singapore', 'sg', '新加坡'),
        'name': ('singapore', 'sg', '新加坡'),
        'server': ('.sg', 'singapore'),
    },
    'United States': {
        'group': (
            'united states', 'usa', 'us', 'america', 'los angeles',
            'san jose', 'new york', 'seattle', '美国',
        ),
        'name': (
            'united states', 'usa', 'us', 'america', 'los angeles',
            'san jose', 'new york', 'seattle', '美国',
        ),
        'server': ('.us', 'unitedstates', 'america', 'us-west', 'us-east'),
    },
}
USAGE_RULES: dict[str, dict[str, tuple[str, ...]]] = {
    'AI': {
        'group': ('ai', 'openai', 'chatgpt', 'claude', 'anthropic', 'gemini'),
        'name': ('ai', 'openai', 'chatgpt', 'claude', 'anthropic', 'gemini'),
        'server': ('openai', 'anthropic', 'claude', 'gemini'),
        'feature': ('reality', 'grpc'),
    },
    'Streaming': {
        'group': (
            'streaming', 'netflix', 'disney', 'youtube', 'hbo',
            'unlock', 'tiktok', '抖音', '哔哩哔哩', '流媒体', '奈飞', '解锁',
        ),
        'name': (
            'streaming', 'netflix', 'disney', 'youtube', 'hbo',
            'unlock', 'tiktok', '抖音', '哔哩哔哩', '流媒体', '奈飞', '解锁',
        ),
    },
}


def normalize_text(value: str) -> str:
    lowered = value.lower()
    normalized = re.sub(r'[\[\](){}/\\|_\-+#:]+', ' ', lowered)
    normalized = re.sub(r'\s+', ' ', normalized)
    return normalized.strip()


def keyword_matches(text: str, keywords: tuple[str, ...]) -> list[str]:
    matched: list[str] = []
    for keyword in keywords:
        normalized_keyword = normalize_text(keyword)
        if not normalized_keyword:
            continue
        if len(normalized_keyword) <= 2 and normalized_keyword.isascii():
            pattern = (
                r'(?<![a-z0-9])'
                + re.escape(normalized_keyword)
                + r'(?![a-z0-9])'
            )
            if re.search(pattern, text) is not None:
                matched.append(keyword)
        elif normalized_keyword.startswith('.'):
            if normalized_keyword in text:
                matched.append(keyword)
        elif normalized_keyword in text:
            matched.append(keyword)
    return matched


def collect_signal_texts(record: MergedProxyRecord) -> tuple[list[str], list[str], list[str], list[str]]:
    name_texts = [
        normalize_text(record.original_name),
        normalize_text(record.final_name),
    ]
    group_texts = [normalize_text(group_name) for group_name in record.source_groups]
    server_texts = [
        normalize_text(value)
        for value in collect_proxy_signal_values(record.proxy)
    ]
    feature_texts = [normalize_text(record.proxy.get('type', ''))]
    if record.proxy.get('udp'):
        feature_texts.append('udp')
    for key in ('network', 'tls', 'client-fingerprint'):
        value = record.proxy.get(key)
        if isinstance(value, str):
            feature_texts.append(normalize_text(value))
    return name_texts, group_texts, server_texts, feature_texts


def collect_proxy_signal_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        values: list[str] = []
        for item in value:
            values.extend(collect_proxy_signal_values(item))
        return values
    if isinstance(value, dict):
        values: list[str] = []
        for key, item in value.items():
            if key in {
                'server', 'servername', 'sni', 'host', 'network',
                'grpc-opts', 'ws-opts', 'reality-opts', 'http-opts',
            }:
                values.extend(collect_proxy_signal_values(item))
        return values
    return []


def evaluate_labels(
        label_rules: dict[str, dict[str, tuple[str, ...]]],
        record: MergedProxyRecord,
        *,
        strategy: WizardStrategy | None = None) -> tuple[dict[str, int], dict[str, list[str]]]:
    name_texts, group_texts, server_texts, feature_texts = collect_signal_texts(record)
    scores: dict[str, int] = {}
    reasons: dict[str, list[str]] = {}
    for label, rule in label_rules.items():
        label_score = 0
        label_reasons: list[str] = []
        group_matches = find_matches(group_texts, rule.get('group', ()))
        if group_matches:
            label_score += GROUP_SCORE_WEIGHT
            label_reasons.append('source group')
        name_matches = find_matches(name_texts, rule.get('name', ()))
        if name_matches:
            label_score += NAME_SCORE_WEIGHT
            label_reasons.append('node name')
        server_matches = find_matches(server_texts, rule.get('server', ()))
        if server_matches:
            label_score += SERVER_SCORE_WEIGHT
            label_reasons.append('server hint')
        feature_matches = find_matches(feature_texts, rule.get('feature', ()))
        if feature_matches:
            label_score += FEATURE_SCORE_WEIGHT
            label_reasons.append('feature')
        if strategy is not None and label.lower() == strategy:
            label_score += 1
            label_reasons.append('strategy preference')
        if label_score > 0:
            scores[label] = label_score
            reasons[label] = label_reasons
    return scores, reasons


def find_matches(texts: list[str], keywords: tuple[str, ...]) -> list[str]:
    matches: list[str] = []
    for text in texts:
        matches.extend(keyword_matches(text, keywords))
    return list(dict.fromkeys(matches))


def score_to_confidence(score: int, gap: int) -> NodeLabelConfidence:
    if score >= 5 and gap >= 2:
        return 'high'
    if score >= 3 and gap >= 1:
        return 'medium'
    if score >= 2 and gap >= 2:
        return 'medium'
    return 'low'


def classify_region(
        record: MergedProxyRecord,
        strategy: WizardStrategy) -> tuple[NodeLabel | None, list[str]]:
    scores, reasons = evaluate_labels(REGION_RULES, record)
    if not scores:
        return None, []
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    top_label, top_score = ordered[0]
    second_score = ordered[1][1] if len(ordered) > 1 else 0
    if len(ordered) > 1 and top_score == second_score:
        conflicts = [
            label
            for label, score in ordered
            if score == top_score
        ]
        return None, conflicts
    confidence = score_to_confidence(top_score, top_score - second_score)
    return NodeLabel(
        value=top_label,
        confidence=confidence,
        score=top_score,
        reasons=reasons[top_label],
    ), []


def classify_usages(
        record: MergedProxyRecord,
        strategy: WizardStrategy) -> tuple[list[NodeLabel], list[str]]:
    scores, reasons = evaluate_labels(
        USAGE_RULES,
        record,
        strategy=strategy,
    )
    if not scores:
        return [], []
    labels = [
        NodeLabel(
            value=label,
            confidence=score_to_confidence(score, score),
            score=score,
            reasons=reasons[label],
        )
        for label, score in scores.items()
        if should_keep_usage_label(label, score, reasons[label])
    ]
    if len(labels) > 1 and all(label.confidence == 'low' for label in labels):
        return [], [label.value for label in labels]
    labels.sort(key=lambda item: (-item.score, item.value))
    return labels, []


def should_keep_usage_label(
        label: str,
        score: int,
        reasons: list[str]) -> bool:
    if score < 2:
        return False
    if label == 'Streaming':
        return (
            score >= 3
            and any(reason in {'source group', 'node name'} for reason in reasons)
        )
    return True


def canonicalize_for_fingerprint(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: canonicalize_for_fingerprint(item)
            for key, item in sorted(value.items())
            if key != 'name'
        }
    if isinstance(value, list):
        return [canonicalize_for_fingerprint(item) for item in value]
    return value


def fingerprint_proxy(proxy: dict[str, Any]) -> str:
    return json.dumps(
        canonicalize_for_fingerprint(proxy),
        ensure_ascii=True,
        separators=(',', ':'),
        sort_keys=True,
    )


def deduplicate_records(
        records: list[MergedProxyRecord]) -> tuple[list[MergedProxyRecord], dict[str, str]]:
    survivors: list[MergedProxyRecord] = []
    duplicates: dict[str, str] = {}
    fingerprints: dict[str, str] = {}
    for record in records:
        fingerprint = fingerprint_proxy(record.proxy)
        if fingerprint in fingerprints:
            duplicates[record.final_name] = fingerprints[fingerprint]
            continue
        fingerprints[fingerprint] = record.final_name
        survivors.append(record)
    return survivors, duplicates


def prune_duplicate_nodes(template: ClashRoot, duplicates: dict[str, str]) -> None:
    if not duplicates:
        return
    duplicate_names = set(duplicates)
    template['proxies'] = [
        proxy for proxy in template['proxies']
        if proxy['name'] not in duplicate_names
    ]
    for group in template['proxy-groups']:
        group['proxies'] = [
            proxy_name for proxy_name in group.get('proxies', [])
            if proxy_name not in duplicate_names
        ]


def build_rule_based_nodes(
        records: list[MergedProxyRecord],
        duplicates: dict[str, str],
        strategy: WizardStrategy) -> list[AnalyzedNode]:
    nodes: list[AnalyzedNode] = []
    for record in records:
        region_label, region_conflicts = classify_region(record, strategy)
        usage_labels, usage_conflicts = classify_usages(record, strategy)
        nodes.append(
            AnalyzedNode(
                source_name=record.source_name,
                source_url=record.source_url,
                original_name=record.original_name,
                final_name=record.final_name,
                proxy_type=str(record.proxy.get('type', '')),
                source_groups=list(record.source_groups),
                region_label=region_label,
                usage_labels=usage_labels,
                region_conflicts=region_conflicts,
                usage_conflicts=usage_conflicts,
                duplicate_of=duplicates.get(record.final_name),
                was_renamed=record.was_renamed,
                included_in_output=record.final_name not in duplicates,
            ),
        )
    return nodes


def extract_ambiguous_nodes(nodes: list[AnalyzedNode]) -> list[AnalyzedNode]:
    ambiguous_nodes: list[AnalyzedNode] = []
    for node in nodes:
        if not node.included_in_output:
            continue
        if node.region_conflicts or node.usage_conflicts:
            ambiguous_nodes.append(node)
            continue
        if node.region_label is None or node.region_label.confidence == 'low':
            ambiguous_nodes.append(node)
            continue
        if not node.usage_labels or any(
                label.confidence == 'low' for label in node.usage_labels):
            ambiguous_nodes.append(node)
    return ambiguous_nodes


def build_ai_request_payload(nodes: list[AnalyzedNode], model: str) -> bytes:
    prompt_lines = [
        'Classify proxy nodes for a Clash configuration.',
        'Allowed regions: Hong Kong, Taiwan, Japan, Singapore, United States.',
        'Allowed usages: AI, Streaming.',
        'Return strict JSON: {"nodes":[{"name":"...","region":{"value":"...","confidence":"high|medium|low"}|null,"usages":[{"value":"...","confidence":"high|medium|low"}]}]}.',
        'Only use the provided node names and allowed labels.',
        '',
        'Nodes:',
    ]
    for node in nodes:
        prompt_lines.append(
            json.dumps(
                {
                    'name': node.final_name,
                    'original_name': node.original_name,
                    'source': node.source_name,
                    'proxy_type': node.proxy_type,
                    'source_groups': node.source_groups,
                },
                ensure_ascii=False,
            ),
        )
    payload = {
        'model': model,
        'temperature': 0,
        'messages': [
            {
                'role': 'system',
                'content': 'You classify nodes and output JSON only.',
            },
            {
                'role': 'user',
                'content': '\n'.join(prompt_lines),
            },
        ],
    }
    return json.dumps(payload).encode('utf-8')


def extract_message_content(response_payload: dict[str, object]) -> str:
    choices = response_payload.get('choices')
    if not isinstance(choices, list) or not choices:
        raise ValueError('missing choices')
    first_choice = cast(dict[str, object], choices[0])
    message = first_choice.get('message')
    if not isinstance(message, dict):
        raise ValueError('missing message')
    content = message.get('content')
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get('text')
                if isinstance(text, str):
                    parts.append(text)
        return ''.join(parts)
    raise ValueError('missing content')


def strip_code_fences(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith('```'):
        stripped = re.sub(r'^```[a-zA-Z0-9_-]*\s*', '', stripped)
        stripped = re.sub(r'\s*```$', '', stripped)
    return stripped.strip()


def request_ai_suggestions(
        nodes: list[AnalyzedNode],
        ai_config: WizardAIConfig) -> dict[str, dict[str, object]]:
    endpoint = ai_config.base_url.rstrip('/') + '/chat/completions'
    request = urllib.request.Request(
        endpoint,
        data=build_ai_request_payload(nodes, ai_config.model),
        headers={
            'Authorization': f'Bearer {ai_config.api_key}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode('utf-8'))
    content = extract_message_content(cast(dict[str, object], payload))
    parsed = json.loads(strip_code_fences(content))
    if not isinstance(parsed, dict):
        raise ValueError('invalid ai response')
    raw_nodes = parsed.get('nodes')
    if not isinstance(raw_nodes, list):
        raise ValueError('invalid ai response nodes')
    suggestions: dict[str, dict[str, object]] = {}
    for item in raw_nodes:
        if not isinstance(item, dict):
            continue
        name = item.get('name')
        if isinstance(name, str):
            suggestions[name] = cast(dict[str, object], item)
    return suggestions


def build_validated_label(raw_value: object) -> NodeLabel | None:
    if not isinstance(raw_value, dict):
        return None
    value = raw_value.get('value')
    confidence = raw_value.get('confidence')
    if (
        not isinstance(value, str)
        or not isinstance(confidence, str)
        or confidence not in {'high', 'medium', 'low'}
    ):
        return None
    return NodeLabel(
        value=value,
        confidence=cast(NodeLabelConfidence, confidence),
        score=0,
        reasons=['ai-analysis'],
    )


def select_ai_analysis_nodes(
        nodes: list[AnalyzedNode],
        ai_analysis_mode: WizardAIAnalysisMode) -> list[AnalyzedNode]:
    if ai_analysis_mode == 'full':
        return [node for node in nodes if node.included_in_output]
    if ai_analysis_mode == 'assisted':
        return extract_ambiguous_nodes(nodes)
    return []


def apply_ai_assistance(
        nodes: list[AnalyzedNode],
        ai_config: WizardAIConfig,
        ai_analysis_mode: WizardAIAnalysisMode) -> tuple[list[AnalyzedNode], str | None]:
    requested_nodes = select_ai_analysis_nodes(nodes, ai_analysis_mode)
    if not requested_nodes:
        return nodes, None
    try:
        suggestions = request_ai_suggestions(requested_nodes, ai_config)
    except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError) as error:
        logger.warning(_('AI analysis failed: %s'), error)
        return nodes, str(error)

    updated_nodes: list[AnalyzedNode] = []
    for node in nodes:
        suggestion = suggestions.get(node.final_name)
        if suggestion is None:
            updated_nodes.append(node)
            continue

        next_node = node
        ai_region = build_validated_label(suggestion.get('region'))
        if (
            ai_region is not None
            and ai_region.value in REGION_ORDER
            and (
                next_node.region_label is None
                or next_node.region_label.confidence == 'low'
                or next_node.region_conflicts
            )
        ):
            next_node = replace(
                next_node,
                region_label=ai_region,
                region_conflicts=[],
                ai_assisted=True,
            )

        raw_usages = suggestion.get('usages')
        ai_usage_labels: list[NodeLabel] = []
        if isinstance(raw_usages, list):
            for raw_usage in raw_usages:
                label = build_validated_label(raw_usage)
                if label is not None and label.value in USAGE_ORDER:
                    ai_usage_labels.append(label)
        if ai_usage_labels and (
                not next_node.usage_labels
                or next_node.usage_conflicts
                or all(label.confidence == 'low'
                       for label in next_node.usage_labels)):
            ai_usage_labels = list({
                label.value: label for label in ai_usage_labels
            }.values())
            next_node = replace(
                next_node,
                usage_labels=ai_usage_labels,
                usage_conflicts=[],
                ai_assisted=True,
            )
        updated_nodes.append(next_node)
    return updated_nodes, None


def should_keep_group(
        node_names: list[str],
        strategy: WizardStrategy,
        *,
        kind: str,
        group_name: str) -> bool:
    minimum_nodes = 2 if strategy == 'minimal' else 1
    if kind == 'usage' and group_name.lower() == strategy:
        minimum_nodes = 1
    return len(node_names) >= minimum_nodes


def is_group_too_broad(
        node_names: list[str],
        total_node_count: int,
        *,
        kind: str,
        group_name: str,
        strategy: WizardStrategy) -> bool:
    if kind != 'usage' or total_node_count < USAGE_GROUP_MIN_TOTAL_NODES_FOR_BROADNESS:
        return False
    if strategy == group_name.lower():
        return False
    return (len(node_names) / total_node_count) > USAGE_GROUP_BROADNESS_THRESHOLD


def build_group_plan(
        nodes: list[AnalyzedNode],
        strategy: WizardStrategy) -> GeneratedGroupPlan:
    region_groups: dict[str, list[str]] = {}
    usage_groups: dict[str, list[str]] = {}
    included_nodes = [node for node in nodes if node.included_in_output]
    included_node_count = len(included_nodes)
    for region in REGION_ORDER:
        node_names = [
            node.final_name
            for node in included_nodes
            if (
                node.region_label is not None
                and node.region_label.value == region
                and node.region_label.confidence in {'high', 'medium'}
            )
        ]
        if should_keep_group(node_names, strategy, kind='region', group_name=region):
            region_groups[region] = node_names
    for usage in USAGE_ORDER:
        node_names = [
            node.final_name
            for node in included_nodes
            if (
                any(
                    label.value == usage and label.confidence in {'high', 'medium'}
                    for label in node.usage_labels
                )
            )
        ]
        if is_group_too_broad(
                node_names,
                included_node_count,
                kind='usage',
                group_name=usage,
                strategy=strategy):
            continue
        if should_keep_group(node_names, strategy, kind='usage', group_name=usage):
            usage_groups[usage] = node_names

    if included_node_count == 0:
        return GeneratedGroupPlan(
            core_groups=list(CORE_GROUPS),
            region_groups={},
            usage_groups={},
            root_proxy_choices=['DIRECT'],
            fallback_rule_targets={
                usage_name: 'Proxy'
                for usage_name in USAGE_ORDER
            },
        )

    root_proxy_choices = [
        'Auto',
        'Fallback',
        'All Nodes',
        *region_groups.keys(),
        *usage_groups.keys(),
        'DIRECT',
    ]
    fallback_rule_targets = {
        usage_name: 'Proxy'
        for usage_name in USAGE_ORDER
        if usage_name not in usage_groups
    }
    return GeneratedGroupPlan(
        core_groups=list(CORE_GROUPS),
        region_groups=region_groups,
        usage_groups=usage_groups,
        root_proxy_choices=root_proxy_choices,
        fallback_rule_targets=fallback_rule_targets,
    )


def ensure_group(
        template: ClashRoot,
        group_name: str,
        proxies: list[str]) -> None:
    for group in template['proxy-groups']:
        if group['name'] == group_name:
            group['proxies'] = list(proxies)
            return
    template['proxy-groups'].append({
        'name': group_name,
        'type': 'select',
        'proxies': list(proxies),
    })


def remove_group(template: ClashRoot, group_name: str) -> None:
    template['proxy-groups'] = [
        group for group in template['proxy-groups']
        if group['name'] != group_name
    ]


def rewrite_missing_group_rules(
        template: ClashRoot,
        fallback_rule_targets: dict[str, str]) -> None:
    if not fallback_rule_targets:
        return
    rules = template.get('rules', [])
    rewritten_rules: list[str] = []
    for rule in rules:
        if not isinstance(rule, str):
            continue
        tokens = rule.split(',')
        rewritten = False
        for index, token in enumerate(tokens):
            fallback = fallback_rule_targets.get(token)
            if fallback is not None:
                tokens[index] = fallback
                rewritten = True
        rewritten_rules.append(','.join(tokens) if rewritten else rule)
    template['rules'] = rewritten_rules


def apply_group_plan(
        template: ClashRoot,
        plan: GeneratedGroupPlan) -> None:
    leaf_choices = [proxy['name'] for proxy in template['proxies']]
    leaf_proxies = leaf_choices or ['DIRECT']
    ensure_group(template, 'All Nodes', leaf_proxies)
    ensure_group(template, 'Auto', leaf_proxies)
    ensure_group(template, 'Fallback', leaf_proxies)
    ensure_group(template, 'Proxy', plan.root_proxy_choices)

    for group_name in REGION_ORDER:
        if group_name in plan.region_groups:
            ensure_group(template, group_name, plan.region_groups[group_name])
        else:
            remove_group(template, group_name)
    for group_name in USAGE_ORDER:
        if group_name in plan.usage_groups:
            ensure_group(template, group_name, plan.usage_groups[group_name])
        else:
            remove_group(template, group_name)
    rewrite_missing_group_rules(template, plan.fallback_rule_targets)


def attach_generated_groups(
        nodes: list[AnalyzedNode],
        plan: GeneratedGroupPlan) -> list[AnalyzedNode]:
    updated_nodes: list[AnalyzedNode] = []
    for node in nodes:
        generated_region_group = None
        if (
            node.region_label is not None
            and node.region_label.value in plan.region_groups
            and node.final_name in plan.region_groups[node.region_label.value]
        ):
            generated_region_group = node.region_label.value
        generated_usage_groups = [
            usage_name
            for usage_name, proxy_names in plan.usage_groups.items()
            if node.final_name in proxy_names
        ]
        updated_nodes.append(
            replace(
                node,
                generated_region_group=generated_region_group,
                generated_usage_groups=generated_usage_groups,
            ),
        )
    return updated_nodes


def summarize_analysis(
        nodes: list[AnalyzedNode],
        plan: GeneratedGroupPlan,
        report: SubscriptionReport,
        *,
        ai_available: bool,
        ai_analysis_mode: WizardAIAnalysisMode,
        ai_error: str | None) -> NodeAnalysisReport:
    summarized_nodes = attach_generated_groups(nodes, plan)
    region_conflict_nodes = [
        node.final_name for node in summarized_nodes if node.region_conflicts
    ]
    usage_conflict_nodes = [
        node.final_name for node in summarized_nodes if node.usage_conflicts
    ]
    downgraded_nodes = [
        node.final_name
        for node in summarized_nodes
        if (
            node.included_in_output
            and (node.region_conflicts or node.usage_conflicts)
            and node.generated_region_group is None
            and not node.generated_usage_groups
        )
    ]
    stats = NodeAnalysisStats(
        original_node_count=sum(
            result.detected_proxy_count
            for result in report.successful_results
        ),
        merged_node_count=sum(
            1 for node in summarized_nodes if node.included_in_output
        ),
        deduplicated_node_count=sum(
            1 for node in summarized_nodes if node.duplicate_of is not None
        ),
        renamed_node_count=sum(
            1 for node in summarized_nodes if node.was_renamed
        ),
        region_grouped_node_count=sum(
            1 for node in summarized_nodes if node.generated_region_group is not None
        ),
        usage_grouped_node_count=sum(
            1 for node in summarized_nodes if node.generated_usage_groups
        ),
        unclassified_node_count=sum(
            1
            for node in summarized_nodes
            if (
                node.included_in_output
                and node.generated_region_group is None
                and not node.generated_usage_groups
            )
        ),
        ai_assisted_node_count=sum(
            1 for node in summarized_nodes if node.ai_assisted
        ),
    )
    return NodeAnalysisReport(
        nodes=summarized_nodes,
        group_plan=plan,
        conflict_summary=ConflictSummary(
            true_duplicate_count=stats.deduplicated_node_count,
            renamed_count=stats.renamed_node_count,
            region_conflict_nodes=region_conflict_nodes,
            usage_conflict_nodes=usage_conflict_nodes,
            downgraded_nodes=downgraded_nodes,
        ),
        stats=stats,
        generated_region_groups=list(plan.region_groups.keys()),
        generated_usage_groups=list(plan.usage_groups.keys()),
        ai_available=ai_available,
        ai_analysis_mode=ai_analysis_mode,
        ai_error=ai_error,
    )


def analyze_template_nodes(
        template: ClashRoot,
        report: SubscriptionReport,
        strategy: WizardStrategy,
        *,
        ai_config: WizardAIConfig | None = None,
        ai_analysis_mode: WizardAIAnalysisMode = 'disabled') -> NodeAnalysisReport:
    merged_records = [
        merged_proxy
        for result in report.successful_results
        for merged_proxy in result.merged_proxies
    ]
    survivors, duplicates = deduplicate_records(merged_records)
    prune_duplicate_nodes(template, duplicates)
    nodes = build_rule_based_nodes(merged_records, duplicates, strategy)
    ai_error = None
    if ai_analysis_mode != 'disabled' and ai_config is not None:
        nodes, ai_error = apply_ai_assistance(nodes, ai_config, ai_analysis_mode)
    elif ai_analysis_mode != 'disabled' and ai_config is None:
        ai_error = 'AI configuration is unavailable'
    plan = build_group_plan(nodes, strategy)
    apply_group_plan(template, plan)
    return summarize_analysis(
        nodes,
        plan,
        report,
        ai_available=ai_config is not None,
        ai_analysis_mode=ai_analysis_mode,
        ai_error=ai_error,
    )
