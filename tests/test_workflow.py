import gettext
import io
import logging
import socket
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import urllib.error

from mirezu_merger.constants import SUBSCRIPTION_FETCH_TIMEOUT
from mirezu_merger.errors import SubscriptionProbeError
from mirezu_merger.merge import apply_patches
from mirezu_merger.models import LoadedProfile
from mirezu_merger.subscriptions import probe_subscriptions
from mirezu_merger.workflow import render_build_artifacts


def setUpModule() -> None:
    gettext.NullTranslations().install(names={'ngettext'})
    logging.disable(logging.CRITICAL)


def tearDownModule() -> None:
    logging.disable(logging.NOTSET)


class FakeResponse(io.BytesIO):
    def __enter__(self) -> 'FakeResponse':
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        self.close()
        return False


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write_subscription(self, name: str, content: str) -> str:
        path = self.root / f'{name}.yaml'
        path.write_text(content, encoding='utf-8')
        return path.as_uri()

    def make_config(self, subscriptions: dict[str, dict[str, object]]) -> dict[str, object]:
        return {
            'template_file': 'template.yaml',
            'providers_file': 'proxy-providers.yaml',
            'subscriptions': subscriptions,
        }

    def make_template(self) -> dict[str, object]:
        return {
            'proxies': [],
            'proxy-groups': [],
        }

    def test_proxies_only_subscription_is_reported_and_merged(self) -> None:
        config = self.make_config({
            'solo': {
                'url': self.write_subscription(
                    'solo',
                    'proxies:\n'
                    '  - name: Alpha\n'
                    '    type: ss\n',
                ),
            },
        })

        report = probe_subscriptions(config)  # type: ignore[arg-type]
        self.assertEqual(report.successful_count, 1)
        self.assertEqual(report.failed_count, 0)
        self.assertEqual(report.results[0].detected_proxy_group_count, 0)
        self.assertIn(
            'missing_proxy_groups',
            [issue.code for issue in report.results[0].issues],
        )

        artifacts = render_build_artifacts(
            config,  # type: ignore[arg-type]
            self.make_template(),
            [],
        )
        self.assertEqual(
            [proxy['name'] for proxy in artifacts.providers_content['proxies']],  # type: ignore[index]
            ['Alpha'],
        )

    def test_partial_subscription_failure_can_be_summarized_and_allowed(self) -> None:
        config = self.make_config({
            'good': {
                'url': self.write_subscription(
                    'good',
                    'proxies:\n'
                    '  - name: Good Node\n'
                    '    type: ss\n',
                ),
            },
            'bad': {
                'url': (self.root / 'missing.yaml').as_uri(),
            },
        })

        with self.assertRaises(SubscriptionProbeError) as cm:
            render_build_artifacts(
                config,  # type: ignore[arg-type]
                self.make_template(),
                [],
            )
        self.assertEqual(cm.exception.report.failed_count, 1)
        self.assertEqual(cm.exception.report.successful_count, 1)
        self.assertEqual(cm.exception.report.results[1].status, 'failed')
        self.assertIn(
            'fetch_failed',
            [issue.code for issue in cm.exception.report.results[1].issues],
        )

        artifacts = render_build_artifacts(
            config,  # type: ignore[arg-type]
            self.make_template(),
            [],
            allow_subscription_failures=True,
        )
        self.assertEqual(artifacts.subscription_report.failed_count, 1)
        self.assertEqual(
            [proxy['name'] for proxy in artifacts.providers_content['proxies']],  # type: ignore[index]
            ['Good Node'],
        )

    def test_transient_fetch_failure_is_retried_before_succeeding(self) -> None:
        config = self.make_config({
            'retrying': {
                'url': 'https://example.invalid/sub.yaml',
            },
        })
        response_body = (
            b'proxies:\n'
            b'  - name: Retry Node\n'
            b'    type: ss\n'
        )

        def fake_urlopen(request: object, *, timeout: float) -> FakeResponse:
            del request
            self.assertEqual(timeout, SUBSCRIPTION_FETCH_TIMEOUT)
            fake_urlopen.calls += 1
            if fake_urlopen.calls == 1:
                raise urllib.error.URLError(socket.timeout('timed out'))
            return FakeResponse(response_body)

        fake_urlopen.calls = 0  # type: ignore[attr-defined]
        with patch('urllib.request.urlopen', side_effect=fake_urlopen) as mock_urlopen:
            report = probe_subscriptions(config)  # type: ignore[arg-type]

        self.assertEqual(mock_urlopen.call_count, 2)
        self.assertEqual(report.successful_count, 1)
        self.assertEqual(report.failed_count, 0)
        self.assertEqual(report.results[0].detected_proxy_count, 1)

    def test_duplicate_proxy_names_are_renamed_and_reported(self) -> None:
        config = self.make_config({
            'alpha': {
                'url': self.write_subscription(
                    'alpha',
                    'proxies:\n'
                    '  - name: Tokyo\n'
                    '    type: ss\n',
                ),
            },
            'beta': {
                'url': self.write_subscription(
                    'beta',
                    'proxies:\n'
                    '  - name: Tokyo\n'
                    '    type: ss\n',
                ),
            },
        })

        artifacts = render_build_artifacts(
            config,  # type: ignore[arg-type]
            self.make_template(),
            [],
        )

        self.assertEqual(
            [proxy['name'] for proxy in artifacts.providers_content['proxies']],  # type: ignore[index]
            ['Tokyo', 'Tokyo [beta]'],
        )
        beta_result = artifacts.subscription_report.results[1]
        self.assertEqual(beta_result.renamed_proxy_count, 1)
        self.assertIn(
            'duplicate_proxy_name',
            [issue.code for issue in beta_result.issues],
        )

    def test_layered_patches_apply_in_order(self) -> None:
        base_template = {
            'rules': ['MATCH,Proxy'],
            'dns': {
                'enabled': False,
            },
            'tun': {
                'stack': 'system',
            },
        }
        platform_patch = {
            'rules': ['DOMAIN-SUFFIX,example.com,Proxy'],
            'dns': {
                'enabled': True,
            },
        }
        strategy_patch = {
            'rules': ['DOMAIN-SUFFIX,openai.com,AI'],
            'tun': {
                'stack': 'mixed',
            },
        }

        output = apply_patches(base_template, [platform_patch, strategy_patch])

        self.assertEqual(
            output['rules'],
            [
                'MATCH,Proxy',
                'DOMAIN-SUFFIX,example.com,Proxy',
                'DOMAIN-SUFFIX,openai.com,AI',
            ],
        )
        self.assertEqual(output['dns']['enabled'], True)
        self.assertEqual(output['tun']['stack'], 'mixed')

    def test_profiles_still_render_through_build_workflow(self) -> None:
        config = self.make_config({
            'solo': {
                'url': self.write_subscription(
                    'solo',
                    'proxies:\n'
                    '  - name: NodeA\n'
                    '    type: ss\n',
                ),
            },
        })
        profiles = [
            LoadedProfile(
                path=Path('desktop.yaml'),
                patch={
                    'rules': ['MATCH,Proxy'],
                },
            ),
        ]

        artifacts = render_build_artifacts(
            config,  # type: ignore[arg-type]
            self.make_template(),
            profiles,
        )

        self.assertEqual(len(artifacts.outputs), 1)
        self.assertEqual(artifacts.outputs[0].name, 'desktop.yaml')
        self.assertEqual(artifacts.outputs[0].content['rules'], ['MATCH,Proxy'])


if __name__ == '__main__':
    unittest.main()
