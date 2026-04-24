from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import SubscriptionReport


class MirezuMergerError(Exception):
    """Base class for recoverable application errors."""


class PathOperationError(MirezuMergerError):
    def __init__(self, path: Path):
        super().__init__(str(path))
        self.path = path


class ConfigReadError(PathOperationError):
    pass


class ConfigParseError(PathOperationError):
    pass


class TemplateReadError(PathOperationError):
    pass


class ProfileReadError(PathOperationError):
    pass


class ProvidersWriteError(PathOperationError):
    pass


class OutputWriteError(PathOperationError):
    pass


class SessionReadError(PathOperationError):
    pass


class SessionParseError(PathOperationError):
    pass


class SessionWriteError(PathOperationError):
    pass


class GeneratedAssetWriteError(PathOperationError):
    pass


class SubscriptionProbeError(MirezuMergerError):
    def __init__(self, report: SubscriptionReport):
        super().__init__('subscription probe failed')
        self.report = report
