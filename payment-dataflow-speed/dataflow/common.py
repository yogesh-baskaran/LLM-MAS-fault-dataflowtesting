from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact(value: Any) -> Any:
    """No full PAN/CVV in exported results or model prompts."""
    if isinstance(value, dict):
        return {k: ('[REDACTED]' if k.lower() in {'credit_card_number', 'credit_card_cvv', 'card_number', 'card_cvv', 'creditcardnumber', 'creditcardcvv', 'cvv', 'pan'}
                    else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        value=re.sub(r'(mongodb(?:\+srv)?://)[^\s"/]*@',r'\1[REDACTED]@',value)
        value=re.sub(r'("(?:card_cvv|credit_card_cvv)"\s*:\s*)\d+',r'\1"[REDACTED]"',value)
        return re.sub(r'(?<![A-Za-z0-9])\d{12,19}(?![A-Za-z0-9])', '[REDACTED_PAN]', value)
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(redact(value), indent=2, default=str), encoding='utf-8')
    temporary.replace(path)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

