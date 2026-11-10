"""Codec JSON estrito e limitado com aritmética exclusivamente inteira."""
from __future__ import annotations

import json
import re

from .errors import TimesheetError

MAX_BYTES = 8_388_608
MAX_DEPTH = 16
MAX_NODES = 200_000
MAX_NUMBER_CHARS = 32
MAX_DIGITS = 20
MAX_OUTPUT_BYTES = 67_108_864
MAX_OUTPUT_NODES = 1_000_000
_INTEGER = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")


def _error(message: str, code: str = "JSON_INVALID") -> TimesheetError:
    return TimesheetError(code, message)


def _parse_int(token: str) -> int:
    if _INTEGER.fullmatch(token) is None:
        raise _error("Inteiro JSON inválido.")
    digits = token[1:] if token.startswith("-") else token
    if len(token) > MAX_NUMBER_CHARS or len(digits) > MAX_DIGITS:
        raise _error("Número inteiro excede o limite de dígitos.", "JSON_LIMIT")
    return int(token)


def _reject_float(_token: str) -> None:
    raise _error("Números em ponto flutuante ou decimais não são permitidos.", "JSON_FLOAT")


def _reject_constant(_value: str) -> None:
    raise _error("Constantes não finitas não são JSON válido.")


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise _error("Objeto JSON contém chave duplicada.", "JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _string(value: str) -> str:
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise _error("Texto contém Unicode inválido.")
    return json.dumps(value, ensure_ascii=False)


def int_text(value: int) -> str:
    """Representação textual exata de um int estrito (rejeita bool)."""
    if type(value) is not int:
        raise _error("Tipo numérico não suportado; exige int estrito.")
    if abs(value) >= 10**MAX_DIGITS:
        raise _error("Número inteiro excede o limite de dígitos.", "JSON_LIMIT")
    return str(value)


def _dumps(value: object, *, max_nodes: int, max_bytes: int) -> str:
    nodes = 0

    def emit(item: object, depth: int) -> str:
        nonlocal nodes
        nodes += 1
        if nodes > max_nodes:
            raise _error("Documento excede o limite de nós.", "JSON_LIMIT")
        kind = type(item)
        if kind is dict or kind is list:
            if depth > MAX_DEPTH:
                raise _error("Documento excede o limite de profundidade.", "JSON_LIMIT")
            if not item:
                return "{}" if kind is dict else "[]"
            indentation = "  " * depth
            if kind is dict:
                rows = []
                for key, child in item.items():
                    if type(key) is not str:
                        raise _error("Chaves JSON devem ser texto.")
                    rows.append(indentation + _string(key) + ": " + emit(child, depth + 1))
                return "{\n" + ",\n".join(rows) + "\n" + "  " * (depth - 1) + "}"
            rows = [indentation + emit(child, depth + 1) for child in item]
            return "[\n" + ",\n".join(rows) + "\n" + "  " * (depth - 1) + "]"
        if kind is str:
            return _string(item)
        if item is None:
            return "null"
        if kind is bool:
            return "true" if item else "false"
        if kind is int:
            return int_text(item)
        raise _error("Documento contém tipo não suportado.")

    result = emit(value, 1) + "\n"
    if len(result.encode("utf-8")) > max_bytes:
        raise _error("Documento excede o limite de bytes canônicos.", "JSON_LIMIT")
    return result


def dumps(value: object) -> str:
    """Serializa JSON de entrada (pretty 2 espaços + LF) até 8 MiB / 200000 nós."""
    return _dumps(value, max_nodes=MAX_NODES, max_bytes=MAX_BYTES)


def dumps_output(value: object) -> str:
    """Serializa JSON derivado (pretty 2 espaços + LF) até 64 MiB / 1000000 nós."""
    return _dumps(value, max_nodes=MAX_OUTPUT_NODES, max_bytes=MAX_OUTPUT_BYTES)


def loads(data: bytes | str) -> object:
    """Lê UTF-8 sem BOM; recusa duplicatas, floats, constantes não finitas e limites excedidos."""
    if type(data) is bytes:
        if len(data) > MAX_BYTES:
            raise _error("Documento excede o limite de bytes.", "JSON_LIMIT")
        try:
            source = data.decode("utf-8", "strict")
        except UnicodeError:
            raise _error("Documento não é UTF-8 válido.") from None
    elif type(data) is str:
        source = data
        try:
            size = len(source.encode("utf-8", "strict"))
        except UnicodeError:
            raise _error("Texto contém Unicode inválido.") from None
        if size > MAX_BYTES:
            raise _error("Documento excede o limite de bytes.", "JSON_LIMIT")
    else:
        raise _error("A entrada deve ser bytes ou texto.")
    if source.startswith("\ufeff"):
        raise _error("BOM não é permitido.", "JSON_BOM")
    depth = 0
    quoted = escaped = False
    for char in source:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > MAX_DEPTH:
                raise _error("Documento excede o limite de profundidade.", "JSON_LIMIT")
        elif char in "]}":
            depth -= 1
    try:
        value = json.loads(
            source,
            parse_int=_parse_int,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
            object_pairs_hook=_pairs,
        )
    except TimesheetError:
        raise
    except (json.JSONDecodeError, RecursionError, OverflowError):
        raise _error("Sintaxe JSON inválida.") from None
    dumps(value)
    return value
