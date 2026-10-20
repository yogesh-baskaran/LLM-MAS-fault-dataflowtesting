"""Export a portable dashboard; viewing results does not execute tests locally."""
import json
from pathlib import Path

from .common import redact


def export_html(report, destination):
    template = (Path(__file__).resolve().parent.parent / 'dashboard' / 'index.html').read_text(encoding='utf-8')
    encoded = json.dumps(redact(report), default=str).replace('<', '\\u003c').replace('&', '\\u0026')
    destination.write_text(template.replace('/*REPORT_DATA*/ null', encoded), encoding='utf-8')

