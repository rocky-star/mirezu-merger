from typing import Final

EXIT_FAILURE: Final = 1
USER_AGENT: Final = 'clash-verge/1.19.4'
SUBSCRIPTION_FETCH_TIMEOUT: Final = 10.0
SUBSCRIPTION_FETCH_RETRIES: Final = 2
WIZARD_SESSION_FILE_NAME: Final = 'wizard.session.toml'
WIZARD_CONFIG_FILE_NAME: Final = 'wizard.config.toml'
GENERATED_ASSETS_DIR_NAME: Final = 'generated'
GENERATED_CONFIG_FILE_NAME: Final = 'generated-config.toml'
GENERATED_TEMPLATE_FILE_NAME: Final = 'generated-template.yaml'
GENERATED_PROFILES_DIR_NAME: Final = 'generated-profiles'
BUILTIN_PROXY_NAMES: Final = frozenset([
    'DIRECT', 'REJECT', 'REJECT-DROP', 'PASS', 'COMPATIBLE'])
