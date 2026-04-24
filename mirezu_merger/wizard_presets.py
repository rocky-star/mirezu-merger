import copy
import importlib.resources
from functools import cache
from typing import cast

from .models import ClashRoot, Patchable, PresetBundle, WizardStrategy, WizardTarget
from .yaml_support import yaml


def load_packaged_yaml_resource(file_name: str) -> object:
    resource_file = importlib.resources.files('mirezu_merger') / file_name
    return yaml.load(resource_file.read_text(encoding='utf-8'))


@cache
def load_preset_definitions() -> dict[str, object]:
    return cast(
        dict[str, object],
        cast(object, load_packaged_yaml_resource('wizard-presets.yaml')),
    )


_PRESET_DEFINITIONS = load_preset_definitions()
TARGET_ORDER: tuple[WizardTarget, ...] = cast(
    tuple[WizardTarget, ...],
    tuple(cast(list[str], _PRESET_DEFINITIONS['target_order'])),
)
STRATEGY_ORDER: tuple[WizardStrategy, ...] = cast(
    tuple[WizardStrategy, ...],
    tuple(cast(list[str], _PRESET_DEFINITIONS['strategy_order'])),
)


def build_default_template() -> ClashRoot:
    return cast(
        ClashRoot,
        cast(object, load_packaged_yaml_resource('wizard-template.yaml')),
    )


def build_strategy_patch(strategy: WizardStrategy) -> Patchable:
    strategies = cast(
        dict[WizardStrategy, Patchable],
        _PRESET_DEFINITIONS['strategies'],
    )
    return cast(Patchable, copy.deepcopy(strategies[strategy]))


def build_platform_patches() -> dict[WizardTarget, Patchable]:
    platforms = cast(
        dict[WizardTarget, Patchable],
        _PRESET_DEFINITIONS['platforms'],
    )
    return cast(dict[WizardTarget, Patchable], copy.deepcopy(platforms))


def build_preset_bundle(strategy: WizardStrategy) -> PresetBundle:
    return PresetBundle(
        name=strategy,
        base_template=build_default_template(),
        strategy_patch=build_strategy_patch(strategy),
        platform_patches=build_platform_patches(),
    )


def populate_standard_proxy_groups(template: ClashRoot) -> None:
    proxy_names = [proxy['name'] for proxy in template['proxies']]
    leaf_choices = proxy_names or ['DIRECT']
    root_choices = (
        ['Auto', 'Fallback', 'All Nodes', 'DIRECT']
        if proxy_names
        else ['DIRECT']
    )

    proxy_groups = {group['name']: group for group in template['proxy-groups']}
    for group_name in ('All Nodes', 'Auto', 'Fallback'):
        if group_name in proxy_groups:
            proxy_groups[group_name]['proxies'] = leaf_choices.copy()
    if 'Proxy' in proxy_groups:
        proxy_groups['Proxy']['proxies'] = root_choices.copy()
    for group_name in ('Streaming', 'AI'):
        if group_name in proxy_groups:
            proxy_groups[group_name]['proxies'] = root_choices.copy()
