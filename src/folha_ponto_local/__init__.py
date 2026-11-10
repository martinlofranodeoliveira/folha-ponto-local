"""Apuração local e determinística de espelho de ponto e banco de horas em minutos exatos."""

__version__ = "1.0.0"

from .core import TimesheetResult, apurar
from .errors import TimesheetError
from .reports import apurar_json, build_report, write_report

__all__ = [
    "TimesheetError",
    "TimesheetResult",
    "apurar",
    "apurar_json",
    "build_report",
    "write_report",
    "__version__",
]
