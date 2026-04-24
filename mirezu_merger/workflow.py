import copy
import logging
from collections.abc import Callable
from pathlib import Path
from typing import cast

from .errors import SubscriptionProbeError
from .models import (
    BuildArtifacts,
    ClashRoot,
    Config,
    LoadedProfile,
    NodeAnalysisReport,
    RenderedDocument,
    SubscriptionReport,
    WrittenArtifacts,
    YAMLMapping,
)
from .merge import apply_patches
from .storage import (
    load_build_config,
    load_profiles,
    write_output_document,
    write_providers_document,
)
from .subscriptions import apply_subscription_report, probe_subscriptions

logger = logging.getLogger(__name__)
TemplatePostProcessor = Callable[
    [ClashRoot, SubscriptionReport],
    NodeAnalysisReport | None,
]


def build_rendered_outputs(
        merged_template: YAMLMapping,
        profiles: list[LoadedProfile]) -> list[RenderedDocument]:
    outputs: list[RenderedDocument] = []
    for profile in profiles:
        logger.info(_('Applying the patch %s'), profile.path)
        outputs.append(
            RenderedDocument(
                name=profile.path.name,
                content=apply_patches(merged_template, [profile.patch]),
            ))
    return outputs


def build_artifacts_from_template(
        subscription_report: SubscriptionReport,
        providers_file_name: str | None,
        merged_template: YAMLMapping,
        profiles: list[LoadedProfile],
        *,
        analysis_report: NodeAnalysisReport | None = None) -> BuildArtifacts:
    providers_content = (
        {'proxies': merged_template['proxies']}
        if providers_file_name is not None
        else None)
    return BuildArtifacts(
        subscription_report=subscription_report,
        providers_file_name=providers_file_name,
        providers_content=providers_content,
        outputs=build_rendered_outputs(merged_template, profiles),
        merged_template=merged_template,
        analysis_report=analysis_report,
    )


def render_build_artifacts(
        config: Config,
        template: YAMLMapping,
        profiles: list[LoadedProfile],
        *,
        allow_subscription_failures: bool = False,
        subscription_report: SubscriptionReport | None = None,
        template_post_processor: TemplatePostProcessor | None = None,
        ) -> BuildArtifacts:
    merged_template = copy.deepcopy(template)
    effective_report = (
        subscription_report
        if subscription_report is not None
        else probe_subscriptions(config)
    )
    if (effective_report.has_blocking_failures
        and not allow_subscription_failures):
        raise SubscriptionProbeError(effective_report)
    clash_template = cast(ClashRoot, cast(object, merged_template))
    apply_subscription_report(
        config,
        clash_template,
        effective_report,
    )
    analysis_report = None
    if template_post_processor is not None:
        analysis_report = template_post_processor(clash_template, effective_report)
    return build_artifacts_from_template(
        effective_report,
        config.get('providers_file'),
        merged_template,
        profiles,
        analysis_report=analysis_report,
    )


def write_build_artifacts(
        outdir: Path,
        artifacts: BuildArtifacts) -> WrittenArtifacts:
    providers_path = None
    if (artifacts.providers_file_name is not None
        and artifacts.providers_content is not None):
        providers_path = outdir / artifacts.providers_file_name
        logger.info(_('Writing the proxy-providers.yaml to %s'),
                    providers_path)
        write_providers_document(providers_path, artifacts.providers_content)

    output_paths: list[Path] = []
    for output in artifacts.outputs:
        logger.info(_('Writing the corresponding output'))
        output_path = outdir / output.name
        write_output_document(output_path, output.content)
        output_paths.append(output_path)
    return WrittenArtifacts(
        providers_path=providers_path,
        output_paths=output_paths,
    )


def build_from_paths(
        config_path: Path,
        profiles_dir: Path,
        outdir: Path,
        *,
        allow_subscription_failures: bool = False) -> WrittenArtifacts:
    loaded = load_build_config(config_path)
    profiles = load_profiles(profiles_dir)
    artifacts = render_build_artifacts(
        loaded.config,
        cast(YAMLMapping, cast(object, loaded.template)),
        profiles,
        allow_subscription_failures=allow_subscription_failures,
    )
    return write_build_artifacts(outdir, artifacts)
