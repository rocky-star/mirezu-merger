from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypedDict, final

from typing_extensions import NotRequired


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
    mappings: NotRequired[list[SubscriptionMappingConfig]]


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

YAMLMapping = dict[str, Any]
Patchable = dict[str, list[Any] | dict[str, Any]]
IssueSeverity = Literal['warning', 'error']
SubscriptionProbeStatus = Literal['ready', 'ignored', 'failed']
WizardTarget = Literal['desktop', 'mobile', 'router']
WizardStrategy = Literal['general', 'streaming', 'ai', 'minimal']
WizardAIAnalysisMode = Literal['disabled', 'assisted', 'full']
NodeLabelConfidence = Literal['high', 'medium', 'low']


@dataclass(frozen=True)
class LoadedBuildConfig:
    config_path: Path
    config: Config
    template_path: Path
    template: ClashRoot


@dataclass(frozen=True)
class LoadedProfile:
    path: Path
    patch: Patchable


@dataclass(frozen=True)
class RenderedDocument:
    name: str
    content: YAMLMapping


@dataclass(frozen=True)
class SubscriptionIssue:
    code: str
    severity: IssueSeverity
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MergedProxyRecord:
    source_name: str
    source_url: str
    original_name: str
    final_name: str
    proxy: dict[str, Any]
    source_groups: list[str] = field(default_factory=list)
    was_renamed: bool = False


@dataclass
class SubscriptionProbeResult:
    name: str
    url: str
    ignore_failures: bool
    status: SubscriptionProbeStatus
    issues: list[SubscriptionIssue] = field(default_factory=list)
    root: ClashRoot | None = None
    detected_proxy_count: int = 0
    detected_proxy_group_count: int = 0
    merged_proxy_count: int = 0
    renamed_proxy_count: int = 0
    merged_proxies: list[MergedProxyRecord] = field(default_factory=list)


@dataclass(frozen=True)
class SubscriptionReport:
    results: list[SubscriptionProbeResult]

    @property
    def successful_results(self) -> list[SubscriptionProbeResult]:
        return [result for result in self.results if result.status == 'ready']

    @property
    def ignored_results(self) -> list[SubscriptionProbeResult]:
        return [result for result in self.results if result.status == 'ignored']

    @property
    def failed_results(self) -> list[SubscriptionProbeResult]:
        return [result for result in self.results if result.status == 'failed']

    @property
    def total_count(self) -> int:
        return len(self.results)

    @property
    def successful_count(self) -> int:
        return len(self.successful_results)

    @property
    def ignored_count(self) -> int:
        return len(self.ignored_results)

    @property
    def failed_count(self) -> int:
        return len(self.failed_results)

    @property
    def issue_count(self) -> int:
        return sum(len(result.issues) for result in self.results)

    @property
    def has_blocking_failures(self) -> bool:
        return self.failed_count > 0


@dataclass(frozen=True)
class SubscriptionMergeResult:
    name_map: dict[str, list[str]]
    merged_proxy_count: int
    renamed_proxy_count: int
    merged_proxies: list[MergedProxyRecord]


@dataclass(frozen=True)
class NodeLabel:
    value: str
    confidence: NodeLabelConfidence
    score: int
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AnalyzedNode:
    source_name: str
    source_url: str
    original_name: str
    final_name: str
    proxy_type: str
    source_groups: list[str] = field(default_factory=list)
    region_label: NodeLabel | None = None
    usage_labels: list[NodeLabel] = field(default_factory=list)
    region_conflicts: list[str] = field(default_factory=list)
    usage_conflicts: list[str] = field(default_factory=list)
    duplicate_of: str | None = None
    was_renamed: bool = False
    included_in_output: bool = True
    generated_region_group: str | None = None
    generated_usage_groups: list[str] = field(default_factory=list)
    ai_assisted: bool = False


@dataclass(frozen=True)
class GeneratedGroupPlan:
    core_groups: list[str]
    region_groups: dict[str, list[str]]
    usage_groups: dict[str, list[str]]
    root_proxy_choices: list[str]
    fallback_rule_targets: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ConflictSummary:
    true_duplicate_count: int
    renamed_count: int
    region_conflict_nodes: list[str] = field(default_factory=list)
    usage_conflict_nodes: list[str] = field(default_factory=list)
    downgraded_nodes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NodeAnalysisStats:
    original_node_count: int
    merged_node_count: int
    deduplicated_node_count: int
    renamed_node_count: int
    region_grouped_node_count: int
    usage_grouped_node_count: int
    unclassified_node_count: int
    ai_assisted_node_count: int


@dataclass(frozen=True)
class NodeAnalysisReport:
    nodes: list[AnalyzedNode]
    group_plan: GeneratedGroupPlan
    conflict_summary: ConflictSummary
    stats: NodeAnalysisStats
    generated_region_groups: list[str]
    generated_usage_groups: list[str]
    ai_available: bool = False
    ai_analysis_mode: WizardAIAnalysisMode = 'disabled'
    ai_error: str | None = None

    @property
    def ai_enabled(self) -> bool:
        return self.ai_analysis_mode != 'disabled'


@dataclass(frozen=True)
class WizardAIConfig:
    base_url: str
    api_key: str
    model: str = 'gpt-4.1-mini'


@dataclass(frozen=True)
class WizardAnalysisConfig:
    ai: WizardAIConfig | None = None


@dataclass(frozen=True)
class WizardNetworkOptions:
    proxy: StandardProxyConfig | URLProxyConfig | None = None
    user_agent: str | None = None
    allow_subscription_failures: bool = True


@dataclass(frozen=True)
class WizardSession:
    subscription_urls: list[str]
    targets: list[WizardTarget]
    strategy: WizardStrategy
    network: WizardNetworkOptions
    outdir: Path
    ai_analysis_mode: WizardAIAnalysisMode = 'disabled'

    @property
    def ai_assisted_analysis(self) -> bool:
        return self.ai_analysis_mode != 'disabled'


@dataclass(frozen=True)
class ResolvedSubscription:
    name: str
    url: str
    status: SubscriptionProbeStatus
    issues: list[SubscriptionIssue]
    detected_proxy_count: int
    detected_proxy_group_count: int
    merged_proxy_count: int
    renamed_proxy_count: int


@dataclass(frozen=True)
class PresetBundle:
    name: str
    base_template: ClashRoot
    strategy_patch: Patchable
    platform_patches: dict[WizardTarget, Patchable]


@dataclass(frozen=True)
class GeneratedPlan:
    session: WizardSession
    resolved_subscriptions: list[ResolvedSubscription]
    preset_bundle: PresetBundle
    generated_config: Config
    generated_template: ClashRoot
    preview_template: ClashRoot
    generated_profiles: list[LoadedProfile]
    subscription_report: SubscriptionReport
    analysis_report: NodeAnalysisReport


@dataclass(frozen=True)
class BuildArtifacts:
    subscription_report: SubscriptionReport
    providers_file_name: str | None
    providers_content: YAMLMapping | None
    outputs: list[RenderedDocument]
    merged_template: YAMLMapping
    analysis_report: NodeAnalysisReport | None = None


@dataclass(frozen=True)
class WrittenArtifacts:
    providers_path: Path | None
    output_paths: list[Path]
