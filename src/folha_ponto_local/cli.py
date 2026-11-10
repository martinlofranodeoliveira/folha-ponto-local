"""Interface de linha de comando local, sem rede e sem sobrescrita de arquivos."""
from __future__ import annotations

import argparse
from importlib import resources
import os
import stat
import sys

from . import __version__
from .errors import TimesheetError
from .jsoncodec import MAX_BYTES
from .reports import apurar_json, write_report


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise TimesheetError("CLI_ARGUMENT", "Argumentos inválidos; consulte --help.")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="folha-ponto-local",
        description="Apura espelho de ponto e banco de horas em minutos inteiros exatos; execução local.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)

    apurar_cmd = commands.add_parser("apurar", help="Apurar um arquivo JSON de folha de ponto.")
    apurar_cmd.add_argument("entrada", help="Arquivo regular UTF-8 de entrada JSON (sem symlink).")
    apurar_cmd.add_argument("--saida", required=True, help="Novo pacote ZIP; nunca sobrescreve destino existente.")

    exemplo_cmd = commands.add_parser("exemplo", help="Gerar o pacote completo do cenário sintético autoral.")
    exemplo_cmd.add_argument("--saida", required=True, help="Novo pacote ZIP; nunca sobrescreve destino existente.")
    return parser


def _read_input(path: str) -> bytes:
    descriptor: int | None = None
    try:
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode):
            raise TimesheetError("INPUT_FILE", "A entrada deve ser arquivo regular explícito, sem symlink.")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        after = os.lstat(path)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(after.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise TimesheetError("INPUT_FILE", "A entrada foi alterada ou não é arquivo regular explícito.")
        if opened.st_size > MAX_BYTES:
            raise TimesheetError("JSON_LIMIT", "Documento excede o limite de bytes.")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(65_536, MAX_BYTES + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_BYTES:
                raise TimesheetError("JSON_LIMIT", "Documento excede o limite de bytes.")
            chunks.append(chunk)
        return b"".join(chunks)
    except TimesheetError:
        raise
    except (OSError, ValueError):
        raise TimesheetError("INPUT_IO", "Não foi possível ler a entrada como arquivo regular.") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def main(argv: list[str] | None = None) -> int:
    """Retorna 0 no sucesso e 2 em recusa controlada; erros não repetem valores da entrada."""
    try:
        args = _parser().parse_args(argv)
        if args.command == "exemplo":
            package = resources.files("folha_ponto_local").joinpath("data")
            input_bytes = package.joinpath("exemplo.json").read_bytes()
        else:
            input_bytes = _read_input(args.entrada)
        result = apurar_json(input_bytes)
        write_report(args.saida, result, input_bytes)
        print("Relatório de apuração criado.")
        return 0
    except TimesheetError as error:
        print(f"[{error.code}] {error.message}", file=sys.stderr)
        return 2
    except Exception:
        print("[CLI_IO] Não foi possível concluir a operação local.", file=sys.stderr)
        return 2
