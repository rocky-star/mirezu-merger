import gettext
import logging
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from mirezu_merger.cli import run
from mirezu_merger.models import (
    GeneratedPlan,
    PresetBundle,
    ResolvedSubscription,
    WizardNetworkOptions,
    WizardSession,
)
from mirezu_merger.storage import (
    build_private_companion_path,
    load_wizard_session,
    write_generated_plan_assets,
    write_wizard_session,
)
from mirezu_merger.wizard_presets import (
    STRATEGY_ORDER,
    TARGET_ORDER,
    build_default_template,
    build_preset_bundle,
)
from mirezu_merger.wizard import build_generated_plan, run_wizard


def setUpModule() -> None:
    gettext.NullTranslations().install(names={'ngettext'})
    logging.disable(logging.CRITICAL)


def tearDownModule() -> None:
    logging.disable(logging.NOTSET)


class WizardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write_subscription(self, name: str, content: str) -> str:
        path = self.root / f'{name}.yaml'
        path.write_text(content, encoding='utf-8')
        return path.as_uri()

    def test_build_default_template_loads_packaged_yaml(self) -> None:
        template = build_default_template()

        self.assertEqual(template['mixed-port'], 7890)
        self.assertEqual(template['proxies'], [])
        self.assertEqual(
            [group['name'] for group in template['proxy-groups']],
            ['Proxy', 'Auto', 'Fallback', 'All Nodes'],
        )

    def test_build_preset_bundle_loads_packaged_yaml(self) -> None:
        bundle = build_preset_bundle('ai')

        self.assertEqual(TARGET_ORDER, ('desktop', 'mobile', 'router'))
        self.assertEqual(STRATEGY_ORDER, ('general', 'streaming', 'ai', 'minimal'))
        self.assertEqual(bundle.strategy_patch['rules'][-1], 'MATCH,Proxy')
        self.assertEqual(bundle.platform_patches['router']['tun']['stack'], 'system')

    def test_build_generated_plan_uses_documented_models(self) -> None:
        session = WizardSession(
            subscription_urls=[
                self.write_subscription(
                    'solo',
                    'proxies:\n'
                    '  - name: NodeA\n'
                    '    type: ss\n',
                ),
            ],
            targets=['desktop', 'router'],
            strategy='general',
            network=WizardNetworkOptions(),
            outdir=self.root / 'output',
        )

        plan = build_generated_plan(session)

        self.assertIsInstance(plan, GeneratedPlan)
        self.assertIsInstance(plan.preset_bundle, PresetBundle)
        self.assertIsInstance(plan.resolved_subscriptions[0], ResolvedSubscription)
        self.assertEqual(plan.generated_profiles[0].path.name, 'desktop.yaml')
        self.assertEqual(plan.generated_profiles[1].path.name, 'router.yaml')
        self.assertEqual(plan.subscription_report.successful_count, 1)
        self.assertIn('solo', plan.generated_config['subscriptions'])
        self.assertEqual(plan.analysis_report.stats.original_node_count, 1)
        self.assertIn(
            'All Nodes',
            [group['name'] for group in plan.preview_template['proxy-groups']],
        )

    def test_run_wizard_generates_outputs(self) -> None:
        subscription_a = self.write_subscription(
            'a',
            'proxies:\n'
            '  - name: OpenAI Tokyo\n'
            '    type: ss\n',
        )
        subscription_b = self.write_subscription(
            'b',
            'proxies:\n'
            '  - name: OpenAI Tokyo\n'
            '    type: ss\n',
        )
        outdir = self.root / 'output'
        answers = iter(['1,2', '3', 'n', '', 'y', 'y'])
        output = StringIO()

        exit_code = run_wizard(
            [
                'mirezu-merger wizard',
                '--subscription', subscription_a,
                '--subscription', subscription_b,
                '-o', str(outdir),
            ],
            input_func=lambda prompt='': next(answers),
            output=output,
        )

        self.assertEqual(exit_code, 0)
        self.assertTrue((outdir / 'desktop.yaml').is_file())
        self.assertTrue((outdir / 'mobile.yaml').is_file())
        self.assertTrue((outdir / 'wizard.session.toml').is_file())
        self.assertTrue((outdir / 'generated' / 'generated-config.toml').is_file())
        self.assertTrue((outdir / 'generated' / 'generated-template.yaml').is_file())
        self.assertTrue(
            (outdir / 'generated' / 'generated-profiles' / 'desktop.yaml').is_file())
        self.assertTrue(
            (outdir / 'generated' / 'generated-profiles' / 'mobile.yaml').is_file())
        desktop_output = (outdir / 'desktop.yaml').read_text(encoding='utf-8')
        self.assertIn('AI', desktop_output)
        self.assertIn('Japan', desktop_output)
        self.assertIn('OpenAI Tokyo', desktop_output)
        self.assertNotIn('OpenAI Tokyo [b]', desktop_output)
        self.assertIn('Step 5/5: Review', output.getvalue())
        self.assertIn('Removed as true duplicates: 1', output.getvalue())
        self.assertIn('Generated usage groups: AI', output.getvalue())
        self.assertIn('Generated files:', output.getvalue())
        self.assertIn('Saved wizard session:', output.getvalue())
        self.assertIn('Exported generated assets:', output.getvalue())

    def test_streaming_group_requires_explicit_signal(self) -> None:
        session = WizardSession(
            subscription_urls=[
                self.write_subscription(
                    'plain',
                    'proxies:\n'
                    '  - name: Plain WS Node\n'
                    '    type: vmess\n'
                    '    network: ws\n'
                    '    udp: true\n',
                ),
            ],
            targets=['desktop'],
            strategy='general',
            network=WizardNetworkOptions(),
            outdir=self.root / 'output',
        )

        plan = build_generated_plan(session)

        self.assertNotIn(
            'Streaming',
            [group['name'] for group in plan.preview_template['proxy-groups']],
        )

    def test_overly_broad_streaming_group_is_skipped(self) -> None:
        session = WizardSession(
            subscription_urls=[
                self.write_subscription(
                    'broad',
                    'proxies:\n'
                    '  - name: Node1\n'
                    '    type: ss\n'
                    '    server: s1.example.com\n'
                    '    port: 1001\n'
                    '  - name: Node2\n'
                    '    type: ss\n'
                    '    server: s2.example.com\n'
                    '    port: 1002\n'
                    '  - name: Node3\n'
                    '    type: ss\n'
                    '    server: s3.example.com\n'
                    '    port: 1003\n'
                    '  - name: Node4\n'
                    '    type: ss\n'
                    '    server: s4.example.com\n'
                    '    port: 1004\n'
                    '  - name: Node5\n'
                    '    type: ss\n'
                    '    server: s5.example.com\n'
                    '    port: 1005\n'
                    'proxy-groups:\n'
                    '  - name: Netflix\n'
                    '    proxies: [Node1, Node2, Node3, Node4, Node5]\n',
                ),
            ],
            targets=['desktop'],
            strategy='general',
            network=WizardNetworkOptions(),
            outdir=self.root / 'output',
        )

        plan = build_generated_plan(session)

        self.assertNotIn(
            'Streaming',
            [group['name'] for group in plan.preview_template['proxy-groups']],
        )

    def test_run_wizard_shows_readable_failure_summary(self) -> None:
        subscription = self.write_subscription(
            'good',
            'proxies:\n'
            '  - name: Good Node\n'
            '    type: ss\n',
        )
        missing_subscription = (self.root / 'missing.yaml').as_uri()
        output = StringIO()
        answers = iter(['1', '1', 'n', '', 'y', 'y'])

        exit_code = run_wizard(
            [
                'mirezu-merger wizard',
                '--subscription', subscription,
                '--subscription', missing_subscription,
                '-o', str(self.root / 'output'),
            ],
            input_func=lambda prompt='': next(answers),
            output=output,
        )

        self.assertEqual(exit_code, 0)
        self.assertIn('failed to fetch after 1 attempt', output.getvalue())
        self.assertIn('default action: skipped', output.getvalue())
        self.assertNotIn('fetch_failed', output.getvalue())
        self.assertIn('Subscription fetch policy:', output.getvalue())

    def test_run_wizard_can_resume_from_saved_session(self) -> None:
        subscription = self.write_subscription(
            'solo',
            'proxies:\n'
            '  - name: NodeA\n'
            '    type: ss\n',
        )
        first_outdir = self.root / 'first-output'
        first_answers = iter(['1,2', '3', 'n', '', 'y', 'y'])
        first_output = StringIO()

        first_exit_code = run_wizard(
            [
                'mirezu-merger wizard',
                '--subscription', subscription,
                '-o', str(first_outdir),
            ],
            input_func=lambda prompt='': next(first_answers),
            output=first_output,
        )

        self.assertEqual(first_exit_code, 0)
        session_file = first_outdir / 'wizard.session.toml'
        resumed_outdir = self.root / 'resumed-output'
        resumed_output = StringIO()
        resumed_answers = iter(['y'])

        resumed_exit_code = run_wizard(
            [
                'mirezu-merger wizard',
                '--resume',
                '--session-file', str(session_file),
                '-o', str(resumed_outdir),
            ],
            input_func=lambda prompt='': next(resumed_answers),
            output=resumed_output,
        )

        self.assertEqual(resumed_exit_code, 0)
        self.assertTrue((resumed_outdir / 'desktop.yaml').is_file())
        self.assertTrue((resumed_outdir / 'mobile.yaml').is_file())
        self.assertIn('Loaded wizard session:', resumed_output.getvalue())
        self.assertNotIn('Step 2/5: Select output targets.', resumed_output.getvalue())
        self.assertIn('Preset: ai', resumed_output.getvalue())

    def test_run_wizard_uses_optional_ai_analysis_when_configured(self) -> None:
        subscription = self.write_subscription(
            'ambiguous',
            'proxies:\n'
            '  - name: Edge Node\n'
            '    type: ss\n',
        )
        config_path = self.root / 'wizard.config.toml'
        config_path.write_text(
            '[analysis.ai]\n'
            'base_url = "https://api.example.com/v1"\n'
            'api_key = "sk-test"\n'
            'model = "gpt-4.1-mini"\n',
            encoding='utf-8',
        )
        outdir = self.root / 'output'
        answers = iter(['1', '1', 'y', '', 'n', '', 'y', 'y'])
        output = StringIO()

        with patch(
                'mirezu_merger.node_analysis.request_ai_suggestions',
                return_value={
                    'Edge Node': {
                        'region': {
                            'value': 'Japan',
                            'confidence': 'high',
                        },
                        'usages': [
                            {
                                'value': 'AI',
                                'confidence': 'medium',
                            },
                        ],
                    },
                }):
            exit_code = run_wizard(
                [
                    'mirezu-merger wizard',
                    '--subscription', subscription,
                    '--wizard-config-file', str(config_path),
                    '-o', str(outdir),
                ],
                input_func=lambda prompt='': next(answers),
                output=output,
            )

        self.assertEqual(exit_code, 0)
        desktop_output = (outdir / 'desktop.yaml').read_text(encoding='utf-8')
        session_text = (outdir / 'wizard.session.toml').read_text(encoding='utf-8')
        self.assertIn('AI node analysis is available.', output.getvalue())
        self.assertIn('Select AI analysis mode.', output.getvalue())
        self.assertIn('AI analysis mode: assisted', output.getvalue())
        self.assertIn('AI analysis: enabled', output.getvalue())
        self.assertIn('AI-analyzed nodes: 1', output.getvalue())
        self.assertIn('Generated region groups: Japan', output.getvalue())
        self.assertIn('Generated usage groups: AI', output.getvalue())
        self.assertIn('Japan', desktop_output)
        self.assertIn('AI', desktop_output)
        self.assertIn('ai_analysis_mode = "assisted"', session_text)

    def test_run_wizard_full_ai_analysis_requests_all_kept_nodes(self) -> None:
        subscription = self.write_subscription(
            'mixed',
            'proxies:\n'
            '  - name: Edge Node\n'
            '    type: ss\n'
            '    server: edge.example.com\n'
            '  - name: Japan AI Node\n'
            '    type: ss\n'
            '    server: jp.example.com\n',
        )
        config_path = self.root / 'wizard.config.toml'
        config_path.write_text(
            '[analysis.ai]\n'
            'base_url = "https://api.example.com/v1"\n'
            'api_key = "sk-test"\n'
            'model = "gpt-4.1-mini"\n',
            encoding='utf-8',
        )
        outdir = self.root / 'output'
        answers = iter(['1', '1', 'y', '2', 'n', '', 'y', 'y'])
        output = StringIO()
        requested_names: list[str] = []

        def capture_request(nodes: list[object], __: object) -> dict[str, object]:
            requested_names.extend(
                getattr(node, 'final_name')
                for node in nodes
            )
            return {}

        with patch(
                'mirezu_merger.node_analysis.request_ai_suggestions',
                side_effect=capture_request):
            exit_code = run_wizard(
                [
                    'mirezu-merger wizard',
                    '--subscription', subscription,
                    '--wizard-config-file', str(config_path),
                    '-o', str(outdir),
                ],
                input_func=lambda prompt='': next(answers),
                output=output,
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(requested_names, ['Edge Node', 'Japan AI Node'])
        self.assertIn('AI analysis mode: full', output.getvalue())

    def test_load_wizard_session_accepts_legacy_ai_assisted_analysis_flag(self) -> None:
        session_path = self.root / 'wizard.session.toml'
        session_path.write_text(
            f'outdir = "{self.root.as_posix()}"\n'
            'subscription_urls = ["https://example.com/sub.yaml"]\n'
            'targets = ["desktop"]\n'
            'strategy = "general"\n'
            'ai_assisted_analysis = true\n'
            '\n'
            '[network]\n'
            'allow_subscription_failures = true\n',
            encoding='utf-8',
        )

        session = load_wizard_session(session_path)

        self.assertEqual(session.ai_analysis_mode, 'assisted')

    def test_sensitive_urls_are_redacted_in_public_exports(self) -> None:
        session = WizardSession(
            subscription_urls=[
                'https://example.com/sub?token=secret-token&label=demo',
            ],
            targets=['desktop'],
            strategy='general',
            network=WizardNetworkOptions(),
            outdir=self.root / 'output',
        )
        session_path = self.root / 'wizard.session.toml'

        written_session_paths = write_wizard_session(session_path, session)

        public_session_text = session_path.read_text(encoding='utf-8')
        private_session_path = build_private_companion_path(session_path)
        private_session_text = private_session_path.read_text(encoding='utf-8')
        loaded_session = load_wizard_session(session_path)
        self.assertEqual(written_session_paths, [session_path, private_session_path])
        self.assertIn('token=%3Credacted%3E', public_session_text)
        self.assertIn('secret-token', private_session_text)
        self.assertEqual(loaded_session.subscription_urls, session.subscription_urls)

        generated_paths = write_generated_plan_assets(
            self.root / 'generated',
            {
                'template_file': 'generated-template.yaml',
                'providers_file': 'proxy-providers.yaml',
                'subscriptions': {
                    'demo': {
                        'url': session.subscription_urls[0],
                        'ignore': True,
                    },
                },
            },
            build_default_template(),
            [],
        )
        public_generated_config = self.root / 'generated' / 'generated-config.toml'
        private_generated_config = build_private_companion_path(public_generated_config)
        self.assertIn(public_generated_config, generated_paths)
        self.assertIn(private_generated_config, generated_paths)
        self.assertIn(
            'token=%3Credacted%3E',
            public_generated_config.read_text(encoding='utf-8'),
        )
        self.assertIn(
            'secret-token',
            private_generated_config.read_text(encoding='utf-8'),
        )

    def test_run_wizard_reports_missing_session_file(self) -> None:
        output = StringIO()

        exit_code = run_wizard(
            [
                'mirezu-merger wizard',
                '--resume',
                '--session-file', str(self.root / 'missing.session.toml'),
            ],
            input_func=lambda prompt='': 'y',
            output=output,
        )

        self.assertEqual(exit_code, 1)
        self.assertIn('Failed to read wizard session', output.getvalue())

    def test_cli_dispatches_wizard_command(self) -> None:
        subscription = self.write_subscription(
            'solo',
            'proxies:\n'
            '  - name: NodeA\n'
            '    type: ss\n',
        )
        outdir = self.root / 'output'
        answers = iter(['1', '1', 'n', '', 'y', 'y'])
        with patch('builtins.input', lambda prompt='': next(answers)):
            exit_code = run(
                [
                    'mirezu-merger',
                    'wizard',
                    '--subscription', subscription,
                    '-o', str(outdir),
                ],
            )

        self.assertEqual(exit_code, 0)
        self.assertTrue((outdir / 'desktop.yaml').is_file())

    def test_run_wizard_blocks_when_no_usable_subscriptions_remain(self) -> None:
        missing_subscription = (self.root / 'missing.yaml').as_uri()
        output = StringIO()
        answers = iter(['1', '1', 'n', '', 'y', 'y'])

        exit_code = run_wizard(
            [
                'mirezu-merger wizard',
                '--subscription', missing_subscription,
                '-o', str(self.root / 'output'),
            ],
            input_func=lambda prompt='': next(answers),
            output=output,
        )

        self.assertEqual(exit_code, 1)
        self.assertIn('No usable subscriptions remain.', output.getvalue())


if __name__ == '__main__':
    unittest.main()
