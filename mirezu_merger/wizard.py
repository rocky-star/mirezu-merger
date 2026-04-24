import argparse
import copy
from dataclasses import replace
import logging
import sys
import urllib.parse
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO, cast

from .constants import (
    EXIT_FAILURE,
    GENERATED_ASSETS_DIR_NAME,
    SUBSCRIPTION_FETCH_RETRIES,
    SUBSCRIPTION_FETCH_TIMEOUT,
    WIZARD_CONFIG_FILE_NAME,
    WIZARD_SESSION_FILE_NAME,
)
from .errors import (
    GeneratedAssetWriteError,
    SessionParseError,
    SessionReadError,
    SessionWriteError,
)
from .i18n import ensure_translation, install_translation
from .merge import apply_patches
from .models import (
    ClashRoot,
    Config,
    GeneratedPlan,
    LoadedProfile,
    NodeAnalysisReport,
    PresetBundle,
    ResolvedSubscription,
    StandardProxyConfig,
    SubscriptionIssue,
    SubscriptionReport,
    WizardAnalysisConfig,
    WizardAIAnalysisMode,
    WizardNetworkOptions,
    WizardSession,
    WizardStrategy,
    WizardTarget,
    WrittenArtifacts,
)
from .node_analysis import analyze_template_nodes
from .subscriptions import probe_subscriptions
from .storage import (
    load_wizard_analysis_config,
    load_wizard_session,
    write_generated_plan_assets,
    write_wizard_session,
)
from .wizard_presets import (
    STRATEGY_ORDER,
    TARGET_ORDER,
    build_preset_bundle,
)
from .workflow import (
    build_artifacts_from_template,
    render_build_artifacts,
    write_build_artifacts,
)

logger = logging.getLogger(__name__)

PromptFunc = Callable[[str], str]


def build_argument_parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description=_('Run the interactive wizard mode.'),
    )
    parser.add_argument(
        '-o', '--outdir',
        default=None, type=Path,
        help=_('the output directory'),
    )
    parser.add_argument(
        '--subscription',
        action='append',
        default=[],
        help=_('append a subscription URL'),
    )
    parser.add_argument(
        '--subscription-file',
        action='append',
        default=[],
        type=Path,
        help=_('read subscription URLs from a text file'),
    )
    parser.add_argument(
        '--resume',
        action='store_true',
        help=_('resume from a saved wizard session'),
    )
    parser.add_argument(
        '--session-file',
        type=Path,
        help=_('the wizard session file to load or save'),
    )
    parser.add_argument(
        '--wizard-config-file',
        type=Path,
        help=_('the wizard analysis config file to load'),
    )
    parser.add_argument(
        '--locale', help=_('the locale name of the translation to use'))
    parser.add_argument(
        '-v', '--verbose',
        action='count', default=0,
        help=_('increase the logging level, useful for debugging'),
    )
    return parser


def configure_logging(verbosity: int) -> None:
    logging_level = (
        {0: logging.WARNING, 1: logging.INFO}.get(verbosity, logging.DEBUG))
    logging.basicConfig(level=logging_level)


def write_line(output: TextIO, text: str = '') -> None:
    output.write(text + '\n')
    output.flush()


def read_lines_from_file(path: Path) -> list[str]:
    with open(path, encoding='utf_8') as handle:
        return [line.strip() for line in handle]


def normalize_subscription_urls(
        raw_entries: Sequence[str]) -> tuple[list[str], list[str], int]:
    unique_urls: list[str] = []
    invalid_entries: list[str] = []
    seen_urls: set[str] = set()
    raw_count = 0
    for entry in raw_entries:
        url = entry.strip()
        if not url:
            continue
        raw_count += 1
        parsed = urllib.parse.urlparse(url)
        if ((parsed.scheme in {'http', 'https'} and parsed.netloc)
            or (parsed.scheme == 'file' and parsed.path)):
            if url not in seen_urls:
                seen_urls.add(url)
                unique_urls.append(url)
        else:
            invalid_entries.append(url)
    return unique_urls, invalid_entries, raw_count


def collect_subscription_entries(
        args: argparse.Namespace,
        input_func: PromptFunc,
        output: TextIO) -> list[str]:
    collected_entries: list[str] = list(args.subscription)
    for file_name in args.subscription_file:
        collected_entries.extend(read_lines_from_file(file_name))
    if collected_entries:
        return collected_entries

    write_line(output, 'Step 1/5: Paste subscription URLs, one per line.')
    write_line(output, 'You may also paste a local text file path.')
    write_line(output, 'Finish with an empty line.')
    while True:
        line = input_func('> ').strip()
        if not line:
            break
        if '://' not in line and Path(line).is_file():
            collected_entries.extend(read_lines_from_file(Path(line)))
            continue
        collected_entries.append(line)
    return collected_entries


def summarize_initial_subscriptions(
        raw_count: int,
        valid_urls: Sequence[str],
        invalid_entries: Sequence[str],
        output: TextIO) -> None:
    write_line(output)
    write_line(output, f'Detected {raw_count} entries, {len(valid_urls)} unique usable URLs.')
    if invalid_entries:
        write_line(
            output,
            f'Ignored {len(invalid_entries)} invalid entries before probing.',
        )
    write_line(output)


def resolve_wizard_outdir(raw_outdir: Path | None) -> Path:
    return raw_outdir if raw_outdir is not None else Path('output')


def resolve_session_file(
        configured_session_file: Path | None,
        outdir: Path) -> Path:
    return (
        configured_session_file
        if configured_session_file is not None
        else outdir / WIZARD_SESSION_FILE_NAME
    )


def resolve_wizard_config_file(
        configured_wizard_config_file: Path | None) -> Path:
    return (
        configured_wizard_config_file
        if configured_wizard_config_file is not None
        else Path(WIZARD_CONFIG_FILE_NAME)
    )


def parse_multi_choice(
        raw_value: str,
        options: Sequence[str]) -> list[str]:
    selected_values: list[str] = []
    for token in [item.strip() for item in raw_value.split(',') if item.strip()]:
        if token.isdigit():
            index = int(token) - 1
            if 0 <= index < len(options):
                selected = options[index]
            else:
                raise ValueError(token)
        else:
            if token not in options:
                raise ValueError(token)
            selected = token
        if selected not in selected_values:
            selected_values.append(selected)
    if not selected_values:
        raise ValueError(raw_value)
    return selected_values


def sanitize_subscription_name(value: str) -> str:
    sanitized = ''.join(
        character
        for character in value.lower()
        if character.isalnum() or character in {'-', '_'}
    )
    return sanitized.strip('-_')


def build_subscription_name(
        subscription_url: str,
        fallback_index: int,
        existing_names: set[str]) -> str:
    parsed = urllib.parse.urlparse(subscription_url)
    candidate = ''
    if parsed.hostname is not None:
        host_parts = [part for part in parsed.hostname.split('.') if part]
        if host_parts:
            if len(host_parts) >= 3 and host_parts[-2] in {'co', 'com', 'net', 'org'}:
                candidate = host_parts[-3]
            elif len(host_parts) >= 2:
                candidate = host_parts[-2]
            else:
                candidate = host_parts[0]
    elif parsed.scheme == 'file' and parsed.path:
        candidate = Path(parsed.path).stem
    candidate = sanitize_subscription_name(candidate) or f'sub{fallback_index}'

    resolved_name = candidate
    suffix = 2
    while resolved_name in existing_names:
        resolved_name = f'{candidate}-{suffix}'
        suffix += 1
    existing_names.add(resolved_name)
    return resolved_name


def prompt_targets(input_func: PromptFunc, output: TextIO) -> list[WizardTarget]:
    write_line(output, 'Step 2/5: Select output targets.')
    for index, target in enumerate(TARGET_ORDER, start=1):
        write_line(output, f'[{index}] {target}')
    while True:
        raw_value = input_func('Selection [1,2 by default]: ').strip() or '1,2'
        try:
            selected = parse_multi_choice(raw_value, TARGET_ORDER)
        except ValueError:
            write_line(output, 'Invalid selection. Use comma-separated numbers or names.')
            continue
        write_line(output)
        return cast(list[WizardTarget], selected)


def prompt_strategy(input_func: PromptFunc, output: TextIO) -> WizardStrategy:
    write_line(output, 'Step 3/5: Select a strategy preset.')
    for index, strategy in enumerate(STRATEGY_ORDER, start=1):
        write_line(output, f'[{index}] {strategy}')
    while True:
        raw_value = input_func('Selection [1 by default]: ').strip() or '1'
        try:
            selected = parse_multi_choice(raw_value, STRATEGY_ORDER)
        except ValueError:
            write_line(output, 'Invalid selection. Use a number or preset name.')
            continue
        write_line(output)
        return cast(WizardStrategy, selected[0])


def prompt_ai_analysis_mode(
        input_func: PromptFunc,
        output: TextIO) -> WizardAIAnalysisMode:
    write_line(output, 'AI node analysis is available.')
    while True:
        try:
            enabled = prompt_yes_no(
                input_func,
                'Use AI analysis?',
                default=False,
            )
            break
        except ValueError:
            write_line(output, 'Please answer with y or n.')
    if not enabled:
        write_line(output)
        return 'disabled'

    write_line(output, 'Select AI analysis mode.')
    write_line(output, '[1] assisted')
    write_line(output, '[2] full')
    while True:
        raw_value = input_func('Selection [1 by default]: ').strip() or '1'
        try:
            selected = parse_multi_choice(raw_value, ('assisted', 'full'))
        except ValueError:
            write_line(output, 'Invalid selection. Use a number or mode name.')
            continue
        write_line(output)
        return cast(WizardAIAnalysisMode, selected[0])


def prompt_yes_no(
        input_func: PromptFunc,
        prompt: str,
        *,
        default: bool) -> bool:
    suffix = '[Y/n]' if default else '[y/N]'
    raw_value = input_func(f'{prompt} {suffix} ').strip().lower()
    if not raw_value:
        return default
    if raw_value in {'y', 'yes'}:
        return True
    if raw_value in {'n', 'no'}:
        return False
    raise ValueError(raw_value)


def prompt_network_options(
        input_func: PromptFunc,
        output: TextIO) -> WizardNetworkOptions:
    write_line(output, 'Step 4/5: Configure network options.')
    while True:
        try:
            use_proxy = prompt_yes_no(
                input_func,
                'Use HTTP proxy for subscription fetching?',
                default=False,
            )
            break
        except ValueError:
            write_line(output, 'Please answer with y or n.')

    proxy_config = None
    if use_proxy:
        proxy_host = input_func('Proxy host (host:port): ').strip()
        proxy_protocol = input_func('Proxy protocol [http]: ').strip() or 'http'
        proxy_config = cast(
            StandardProxyConfig,
            {
                'type': 'standard',
                'host': proxy_host,
                'protocol': proxy_protocol,
            },
        )

    user_agent = input_func(
        'Custom User-Agent? [leave empty for default] ').strip() or None

    while True:
        try:
            allow_subscription_failures = prompt_yes_no(
                input_func,
                'Continue when some subscriptions fail?',
                default=True,
            )
            break
        except ValueError:
            write_line(output, 'Please answer with y or n.')

    write_line(output)
    return WizardNetworkOptions(
        proxy=proxy_config,
        user_agent=user_agent,
        allow_subscription_failures=allow_subscription_failures,
    )


def build_generated_config(
        subscription_urls: Sequence[str],
        network_options: WizardNetworkOptions) -> Config:
    config = cast(
        Config,
        {
            'template_file': 'generated-template.yaml',
            'providers_file': 'proxy-providers.yaml',
            'subscriptions': {},
        },
    )
    if network_options.proxy is not None:
        config['proxy'] = network_options.proxy
    if network_options.user_agent is not None:
        config['user_agent'] = network_options.user_agent

    subscriptions: dict[str, dict[str, object]] = {}
    used_names: set[str] = set()
    for index, subscription_url in enumerate(subscription_urls, start=1):
        subscription_name = build_subscription_name(
            subscription_url,
            index,
            used_names,
        )
        subscriptions[subscription_name] = {
            'url': subscription_url,
            'ignore': network_options.allow_subscription_failures,
        }
    config['subscriptions'] = cast(dict[str, object], subscriptions)
    return config


def build_resolved_subscriptions(
        report: SubscriptionReport) -> list[ResolvedSubscription]:
    return [
        ResolvedSubscription(
            name=result.name,
            url=result.url,
            status=result.status,
            issues=list(result.issues),
            detected_proxy_count=result.detected_proxy_count,
            detected_proxy_group_count=result.detected_proxy_group_count,
            merged_proxy_count=result.merged_proxy_count,
            renamed_proxy_count=result.renamed_proxy_count,
        )
        for result in report.results
    ]


def build_generated_profiles(
        targets: Sequence[WizardTarget],
        preset_bundle: PresetBundle) -> list[LoadedProfile]:
    return [
        LoadedProfile(
            path=Path(f'{target}.yaml'),
            patch=preset_bundle.platform_patches[target],
        )
        for target in targets
    ]


def build_wizard_post_processor(
        session: WizardSession,
        analysis_config: WizardAnalysisConfig) -> Callable[[ClashRoot, SubscriptionReport], NodeAnalysisReport]:
    def post_process(
            template: ClashRoot,
            report: SubscriptionReport) -> NodeAnalysisReport:
        return analyze_template_nodes(
            template,
            report,
            session.strategy,
            ai_config=analysis_config.ai,
            ai_analysis_mode=session.ai_analysis_mode,
        )
    return post_process


def build_generated_plan(
        session: WizardSession,
        *,
        analysis_config: WizardAnalysisConfig | None = None) -> GeneratedPlan:
    effective_analysis_config = (
        analysis_config
        if analysis_config is not None
        else WizardAnalysisConfig()
    )
    generated_config = build_generated_config(
        session.subscription_urls,
        session.network,
    )
    subscription_report = probe_subscriptions(generated_config)
    preset_bundle = build_preset_bundle(session.strategy)
    generated_template = cast(
        ClashRoot,
        cast(
            object,
            apply_patches(
                preset_bundle.base_template,
                [preset_bundle.strategy_patch],
            ),
        ),
    )
    generated_profiles = build_generated_profiles(
        session.targets,
        preset_bundle,
    )
    preview_artifacts = render_build_artifacts(
        generated_config,
        generated_template,
        [],
        allow_subscription_failures=True,
        subscription_report=subscription_report,
        template_post_processor=build_wizard_post_processor(
            session,
            effective_analysis_config,
        ),
    )
    analysis_report = preview_artifacts.analysis_report
    if analysis_report is None:
        raise RuntimeError('wizard analysis report was not generated')
    return GeneratedPlan(
        session=session,
        resolved_subscriptions=build_resolved_subscriptions(subscription_report),
        preset_bundle=preset_bundle,
        generated_config=generated_config,
        generated_template=generated_template,
        preview_template=cast(
            ClashRoot,
            cast(object, preview_artifacts.merged_template),
        ),
        generated_profiles=generated_profiles,
        subscription_report=subscription_report,
        analysis_report=analysis_report,
    )


def can_generate(plan: GeneratedPlan) -> bool:
    return (
        plan.subscription_report.successful_count > 0
        and not plan.subscription_report.has_blocking_failures
    )


def summarize_subscription_report(
        report: SubscriptionReport,
        output: TextIO) -> None:
    write_line(
        output,
        (f'Usable subscriptions: {report.successful_count}; '
         + f'ignored: {report.ignored_count}; failed: {report.failed_count}.'),
    )
    total_detected_nodes = sum(
        result.detected_proxy_count
        for result in report.successful_results
    )
    write_line(output, f'Estimated nodes to merge: {total_detected_nodes}.')
    for result in report.results:
        if result.issues:
            issue_summary = '; '.join(
                describe_subscription_issue(issue)
                for issue in result.issues
            )
            write_line(
                output,
                f'- {result.name}: {result.status}; {issue_summary}',
            )


def format_summary_values(values: Sequence[str]) -> str:
    if not values:
        return 'none'
    return ', '.join(values)


def format_node_examples(values: Sequence[str], *, limit: int = 5) -> str:
    if not values:
        return 'none'
    shown_values = list(values[:limit])
    if len(values) <= limit:
        return ', '.join(shown_values)
    return ', '.join(shown_values) + f', ... ({len(values)} total)'


def format_attempt_count(attempt_count: object) -> str:
    if not isinstance(attempt_count, int) or attempt_count <= 0:
        return ''
    noun = 'attempt' if attempt_count == 1 else 'attempts'
    return f' after {attempt_count} {noun}'


def describe_subscription_issue(issue: SubscriptionIssue) -> str:
    details = issue.details
    action_text = (
        'default action: skipped'
        if issue.severity == 'warning'
        else 'default action: blocks generation'
    )
    match issue.code:
        case 'fetch_failed':
            return (
                'failed to fetch'
                + format_attempt_count(details.get('attempts'))
                + f': {details.get("reason", "unknown error")}; {action_text}'
            )
        case 'invalid_yaml':
            return f'invalid YAML: {details.get("reason", "parse error")}; {action_text}'
        case 'empty_subscription' | 'invalid_subscription_root':
            return f'empty or invalid subscription content; {action_text}'
        case 'missing_proxies':
            return f'missing proxies; {action_text}'
        case 'missing_proxy_groups':
            return 'missing proxy-groups; continuing with proxies only'
        case 'invalid_proxy_groups':
            return f'invalid proxy-groups; {action_text}'
        case 'duplicate_proxy_name':
            original_name = details.get('original_name')
            resolved_name = details.get('resolved_name')
            if original_name is not None and resolved_name is not None:
                return f'renamed duplicate node {original_name} -> {resolved_name}'
            return 'renamed duplicate node'
        case 'missing_source_group':
            return (
                'ignored mapping because source group '
                + f'{details.get("source_group", "?")} was not found'
            )
        case 'missing_group_node':
            return (
                f'group {details.get("source_group", "?")} referenced missing node '
                + f'{details.get("node_name", "?")}'
            )
        case 'missing_target_group':
            return (
                'ignored mapping because target group '
                + f'{details.get("target_group", "?")} was not found'
            )
        case 'unmapped_proxy_groups':
            group_names = details.get('group_names')
            if isinstance(group_names, list) and group_names:
                return 'ignored subscription groups: ' + ', '.join(
                    str(group_name) for group_name in group_names)
            return 'ignored unmapped subscription groups'
        case _:
            return issue.code


def render_generated_plan(plan: GeneratedPlan):
    return build_artifacts_from_template(
        plan.subscription_report,
        plan.generated_config.get('providers_file'),
        cast(
            dict[str, object],
            copy.deepcopy(cast(object, plan.preview_template)),
        ),
        plan.generated_profiles,
        analysis_report=plan.analysis_report,
    )


def review_plan(plan: GeneratedPlan, output: TextIO) -> None:
    analysis_stats = plan.analysis_report.stats
    conflict_summary = plan.analysis_report.conflict_summary
    write_line(output, 'Step 5/5: Review')
    summarize_subscription_report(plan.subscription_report, output)
    write_line(output, f'Original detected nodes: {analysis_stats.original_node_count}')
    write_line(output, f'Nodes kept after merge: {analysis_stats.merged_node_count}')
    write_line(output, f'Removed as true duplicates: {analysis_stats.deduplicated_node_count}')
    write_line(output, f'Renamed for name conflicts: {analysis_stats.renamed_node_count}')
    write_line(
        output,
        f'Nodes assigned to region groups: {analysis_stats.region_grouped_node_count}',
    )
    write_line(
        output,
        f'Nodes assigned to usage groups: {analysis_stats.usage_grouped_node_count}',
    )
    write_line(
        output,
        f'Nodes kept only in generic groups: {analysis_stats.unclassified_node_count}',
    )
    write_line(
        output,
        ('AI-analyzed nodes: '
         + f'{analysis_stats.ai_assisted_node_count}'),
    )
    write_line(
        output,
        ('Generated region groups: '
         + format_summary_values(plan.analysis_report.generated_region_groups)),
    )
    write_line(
        output,
        ('Generated usage groups: '
         + format_summary_values(plan.analysis_report.generated_usage_groups)),
    )
    if conflict_summary.downgraded_nodes:
        write_line(
            output,
            ('Conflict-downgraded nodes: '
             + format_node_examples(conflict_summary.downgraded_nodes)),
        )
    if conflict_summary.region_conflict_nodes:
        write_line(
            output,
            ('Region conflicts: '
             + format_node_examples(conflict_summary.region_conflict_nodes)),
        )
    if conflict_summary.usage_conflict_nodes:
        write_line(
            output,
            ('Usage conflicts: '
             + format_node_examples(conflict_summary.usage_conflict_nodes)),
        )
    output_files = ', '.join(profile.path.name for profile in plan.generated_profiles)
    write_line(output, f'Output files: {output_files}')
    write_line(output, f'Preset: {plan.session.strategy}')
    write_line(output, f'Output dir: {plan.session.outdir}')
    write_line(
        output,
        f'AI analysis mode: {plan.analysis_report.ai_analysis_mode}',
    )
    if plan.analysis_report.ai_available:
        ai_status = (
            'enabled'
            if plan.analysis_report.ai_enabled and plan.analysis_report.ai_error is None
            else 'requested but fell back to rules'
            if plan.analysis_report.ai_enabled
            else 'available but disabled'
        )
    else:
        ai_status = 'unavailable'
    write_line(output, f'AI analysis: {ai_status}')
    if plan.analysis_report.ai_error is not None:
        write_line(output, f'AI analysis note: {plan.analysis_report.ai_error}')
    write_line(
        output,
        ('Subscription fetch policy: '
         + f'{SUBSCRIPTION_FETCH_TIMEOUT:g}s timeout per attempt, '
         + f'up to {SUBSCRIPTION_FETCH_RETRIES + 1} attempts on transient errors'),
    )
    if plan.session.network.proxy is None:
        write_line(output, 'HTTP proxy: disabled')
    else:
        write_line(output, f'HTTP proxy: {plan.session.network.proxy["host"]}')
    write_line(
        output,
        ('User-Agent: default'
         if plan.session.network.user_agent is None
         else f'User-Agent: {plan.session.network.user_agent}'),
    )
    write_line(
        output,
        ('Continue on partial failure: yes'
         if plan.session.network.allow_subscription_failures
         else 'Continue on partial failure: no'),
    )
    if not can_generate(plan):
        write_line(output)
        if plan.subscription_report.successful_count == 0:
            write_line(output, 'No usable subscriptions remain. Generation is blocked.')
        else:
            write_line(output, 'Blocking subscription failures remain. Generation is blocked.')
    write_line(output)


def write_generation_summary(
        written: WrittenArtifacts,
        session_paths: Sequence[Path],
        generated_asset_paths: Sequence[Path],
        output: TextIO) -> None:
    write_line(output, 'Generated files:')
    if written.providers_path is not None:
        write_line(output, f'- {written.providers_path}')
    for output_path in written.output_paths:
        write_line(output, f'- {output_path}')
    write_line(output, 'Saved wizard session:')
    for session_path in session_paths:
        write_line(output, f'- {session_path}')
    write_line(output, 'Exported generated assets:')
    for generated_path in generated_asset_paths:
        write_line(output, f'- {generated_path}')


def run_wizard(
        argv: Sequence[str] | None = None,
        *,
        input_func: PromptFunc | None = None,
        output: TextIO | None = None) -> int:
    if argv is None:
        argv = sys.argv
    if input_func is None:
        input_func = input
    if output is None:
        output = sys.stdout

    ensure_translation()
    parser = build_argument_parser(argv[0])
    preview_args, __ = parser.parse_known_args(argv[1:])
    if preview_args.locale is not None:
        install_translation(preview_args.locale)
    args = parser.parse_args(argv[1:])
    configure_logging(args.verbose)
    analysis_config = load_wizard_analysis_config(
        resolve_wizard_config_file(args.wizard_config_file),
    )
    try:
        if args.resume:
            initial_outdir = resolve_wizard_outdir(args.outdir)
            initial_session_file = resolve_session_file(
                args.session_file,
                initial_outdir,
            )
            session = load_wizard_session(initial_session_file)
            if args.outdir is not None:
                session = replace(session, outdir=args.outdir)
            session_file = resolve_session_file(args.session_file, session.outdir)
            write_line(output, f'Loaded wizard session: {initial_session_file}')
            write_line(output)
        else:
            raw_entries = collect_subscription_entries(args, input_func, output)
            subscription_urls, invalid_entries, raw_count = normalize_subscription_urls(
                raw_entries)
            summarize_initial_subscriptions(
                raw_count,
                subscription_urls,
                invalid_entries,
                output,
            )
            if not subscription_urls:
                write_line(output, 'No usable subscription URLs were provided.')
                return EXIT_FAILURE

            targets = prompt_targets(input_func, output)
            strategy = prompt_strategy(input_func, output)
            ai_analysis_mode: WizardAIAnalysisMode = 'disabled'
            if analysis_config.ai is not None:
                ai_analysis_mode = prompt_ai_analysis_mode(
                    input_func,
                    output,
                )
            network_options = prompt_network_options(input_func, output)
            session = WizardSession(
                subscription_urls=subscription_urls,
                targets=targets,
                strategy=strategy,
                network=network_options,
                outdir=resolve_wizard_outdir(args.outdir),
                ai_analysis_mode=ai_analysis_mode,
            )
            session_file = resolve_session_file(args.session_file, session.outdir)
    except SessionReadError as error:
        write_line(output, f'Failed to read wizard session from {error.path}.')
        return EXIT_FAILURE
    except SessionParseError as error:
        write_line(output, f'Failed to parse wizard session from {error.path}.')
        return EXIT_FAILURE

    plan = build_generated_plan(session, analysis_config=analysis_config)
    review_plan(plan, output)

    while True:
        try:
            should_generate = prompt_yes_no(
                input_func,
                'Generate now?',
                default=True,
            )
            break
        except ValueError:
            write_line(output, 'Please answer with y or n.')
    if not should_generate:
        write_line(output, 'Generation cancelled.')
        return 0
    if not can_generate(plan):
        return EXIT_FAILURE

    artifacts = render_generated_plan(plan)
    written = write_build_artifacts(plan.session.outdir, artifacts)
    try:
        saved_session_paths = write_wizard_session(session_file, plan.session)
        generated_asset_paths = write_generated_plan_assets(
            plan.session.outdir / GENERATED_ASSETS_DIR_NAME,
            plan.generated_config,
            plan.preview_template,
            plan.generated_profiles,
        )
    except SessionWriteError as error:
        write_line(output, f'Failed to write wizard session to {error.path}.')
        return EXIT_FAILURE
    except GeneratedAssetWriteError as error:
        write_line(output, f'Failed to write generated assets to {error.path}.')
        return EXIT_FAILURE
    write_generation_summary(
        written,
        saved_session_paths,
        generated_asset_paths,
        output,
    )
    return 0
