#!/usr/bin/env python3
"""Confira os artefatos de distribuição (wheel e sdist) e exercite a instalação fora da árvore de fontes."""
from __future__ import annotations

import argparse
import base64
import csv
from email.parser import BytesParser
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NAME = "folha_ponto_local"
DIST_NAME = "folha-ponto-local"
VERSION = "1.0.0"
DISTINFO = f"{NAME}-{VERSION}.dist-info"
ROOT_FILES = {
    ".gitattributes",
    ".gitignore",
    "LICENSE",
    "README.md",
    "MANIFEST.in",
    "pyproject.toml",
    "requirements-build.txt",
}
SUFFIXES = {
    "src": {".py", ".json"},
    "tests": {".py"},
    "scripts": {".py"},
    "tools": {".py"},
    "docs": {".md", ".json", ".png", ".svg"},
    ".github": {".yml"},
}
FORBIDDEN = {".git", "local", "dist", "build", "__pycache__", ".openai", ".impeccable", "node_modules"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def safe_name(name: str) -> PurePosixPath:
    p = PurePosixPath(name)
    require(
        bool(name) and not p.is_absolute() and str(p) == name and "\\" not in name,
        "Nome de arquivo não canônico no artefato.",
    )
    require(
        all(x not in FORBIDDEN and x not in {".", ".."} and not x.startswith((".venv", ".env")) for x in p.parts),
        "Contexto local não pode integrar a distribuição.",
    )
    return p


def source_files(root: Path = ROOT) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if any(x in FORBIDDEN or x.startswith((".venv", ".env")) for x in rel.parts):
            continue
        if not p.is_file():
            continue
        name = rel.as_posix()
        eligible = name in ROOT_FILES or (rel.parts[0] in SUFFIXES and p.suffix in SUFFIXES[rel.parts[0]])
        if eligible:
            require(not p.is_symlink(), "Fonte pública não pode ser link simbólico.")
            result[name] = p.read_bytes()
    return result


def runtime_files(root: Path = ROOT) -> dict[str, bytes]:
    package = root / "src" / NAME
    return {
        p.relative_to(root / "src").as_posix(): p.read_bytes()
        for p in package.rglob("*")
        if p.is_file() and p.suffix in {".py", ".json"}
    }


def check_wheel(path: Path, root: Path = ROOT) -> dict[str, object]:
    with zipfile.ZipFile(path) as archive:
        require(archive.testzip() is None, "CRC do wheel inválido.")
        names = archive.namelist()
        require(len(names) == len(set(names)), "Membro repetido no wheel.")
        for name in names:
            safe_name(name)
            require(not name.endswith("/"), "Diretório inesperado no wheel.")
        files = {name: archive.read(name) for name in names}
    runtime = runtime_files(root)
    metadata_names = {
        f"{DISTINFO}/{name}"
        for name in ["METADATA", "WHEEL", "entry_points.txt", "top_level.txt", "RECORD"]
    }
    metadata_names.add(f"{DISTINFO}/licenses/LICENSE")
    require(set(files) == set(runtime) | metadata_names, "Lista fechada do wheel diverge.")
    for name, expected in runtime.items():
        require(files[name] == expected, "O wheel alterou bytes de execução.")
    require(
        files[f"{DISTINFO}/licenses/LICENSE"] == (root / "LICENSE").read_bytes(),
        "Licença do wheel divergente.",
    )
    metadata = BytesParser().parsebytes(files[f"{DISTINFO}/METADATA"])
    require(metadata["Name"] == DIST_NAME and metadata["Version"] == VERSION, "Identidade do wheel divergente.")
    require(
        metadata["Requires-Python"] == ">=3.11" and metadata["License-Expression"] == "MIT",
        "Metadados de plataforma/licença divergentes.",
    )
    require(not metadata.get_all("Requires-Dist"), "O runtime não deve ter dependências externas.")
    require("Root-Is-Purelib: true" in files[f"{DISTINFO}/WHEEL"].decode(), "Wheel deve ser Python puro.")
    require(
        f"{DIST_NAME} = {NAME}.cli:main" in files[f"{DISTINFO}/entry_points.txt"].decode(),
        "Entry point ausente.",
    )
    rows = list(csv.reader(io.StringIO(files[f"{DISTINFO}/RECORD"].decode())))
    require(len(rows) == len(files) and {r[0] for r in rows} == set(files), "Cobertura RECORD divergente.")
    for name, digest, size in rows:
        if name.endswith("/RECORD"):
            require(digest == size == "", "RECORD não deve assinar a si próprio.")
        else:
            expected = base64.urlsafe_b64encode(hashlib.sha256(files[name]).digest()).rstrip(b"=").decode()
            require(
                digest == "sha256=" + expected and size == str(len(files[name])),
                "Hash/tamanho RECORD inválido.",
            )
    return {"kind": "wheel", "members": len(files), "runtimeFiles": len(runtime), "recordChecked": True}


def check_sdist(path: Path, root: Path = ROOT) -> dict[str, object]:
    prefix = f"{NAME}-{VERSION}/"
    files: dict[str, bytes] = {}
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            if member.isdir():
                continue
            require(member.isfile() and member.name.startswith(prefix), "Tipo/caminho inesperado no sdist.")
            name = member.name[len(prefix) :]
            safe_name(name)
            require(name not in files, "Membro repetido no sdist.")
            extracted = archive.extractfile(member)
            require(extracted is not None, "Membro ilegível no sdist.")
            files[name] = extracted.read()
    source = source_files(root)
    metadata = {"PKG-INFO", "setup.cfg"} | {
        f"src/{NAME}.egg-info/{n}"
        for n in ["PKG-INFO", "SOURCES.txt", "dependency_links.txt", "entry_points.txt", "top_level.txt"]
    }
    require(set(files) == set(source) | metadata, "Lista fechada do sdist diverge da fonte pública.")
    for name, expected in source.items():
        require(files[name] == expected, "O sdist alterou os bytes da fonte.")
    info = BytesParser().parsebytes(files["PKG-INFO"])
    require(info["Name"] == DIST_NAME and info["Version"] == VERSION, "Identidade do sdist divergente.")
    return {"kind": "sdist", "members": len(files), "sourceFiles": len(source), "sourceBytesChecked": True}


def run(args: list[str], *, cwd: Path, env: dict[str, str]) -> str:
    p = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    require(
        p.returncode == 0,
        "Um comando de instalação/conferência falhou: " + (p.stderr[-1400:] or p.stdout[-1400:]),
    )
    return p.stdout


def installed_origin_matches(location: str, environment: Path) -> bool:
    return Path(location).resolve().is_relative_to(Path(environment).resolve())


def check_install(path: Path) -> dict[str, object]:
    artifact = path.resolve()
    with tempfile.TemporaryDirectory(prefix="folha-ponto-install-") as temporary:
        tmp_path = Path(temporary)
        env_dir = tmp_path / "env"
        venv.EnvBuilder(with_pip=True).create(env_dir)
        bindir = env_dir / ("Scripts" if os.name == "nt" else "bin")
        python = bindir / ("python.exe" if os.name == "nt" else "python")
        cli = bindir / (f"{DIST_NAME}.exe" if os.name == "nt" else DIST_NAME)
        environment = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
        environment.update(PYTHONUTF8="1", PYTHONNOUSERSITE="1")
        if artifact.name.endswith(".tar.gz"):
            run(
                [str(python), "-m", "pip", "install", "--disable-pip-version-check", "setuptools==84.2.1"],
                cwd=tmp_path,
                env=environment,
            )
        run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-deps",
                "--no-build-isolation",
                str(artifact),
            ],
            cwd=tmp_path,
            env=environment,
        )
        location = run(
            [str(python), "-c", f"import {NAME}; print({NAME}.__file__)"],
            cwd=tmp_path,
            env=environment,
        ).strip()
        require(installed_origin_matches(location, env_dir), "A importação não veio da instalação isolada.")
        require(
            run([str(cli), "--version"], cwd=tmp_path, env=environment).strip().endswith(VERSION),
            "Versão instalada divergente.",
        )
        bundle = tmp_path / "exemplo-folha-ponto.zip"
        run([str(cli), "exemplo", "--saida", str(bundle)], cwd=tmp_path, env=environment)
        first = bundle.read_bytes()
        require(zipfile.is_zipfile(io.BytesIO(first)), "CLI instalada não gerou ZIP.")
        validation = run(
            [str(python), str(ROOT / "tools/verify_independent.py"), str(bundle), "--demo"],
            cwd=tmp_path,
            env=environment,
        )
        refused = subprocess.run(
            [str(cli), "exemplo", "--saida", str(bundle)],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
        )
        require(refused.returncode == 2 and bundle.read_bytes() == first, "Repetição não preservou destino existente.")
        return {
            "kind": "installed-" + ("wheel" if artifact.suffix == ".whl" else "sdist"),
            "outsideSource": True,
            "demoBytes": len(first),
            "existingDestinationPreserved": True,
            "independentReader": json.loads(validation),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dist", type=Path)
    group.add_argument("--install", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.install:
            result: object = check_install(args.install)
        else:
            wheel = args.dist / f"{NAME}-{VERSION}-py3-none-any.whl"
            sdist = args.dist / f"{NAME}-{VERSION}.tar.gz"
            result = [check_wheel(wheel), check_sdist(sdist)]
    except (ValueError, OSError, zipfile.BadZipFile, tarfile.TarError, KeyError, json.JSONDecodeError) as error:
        print(f"Distribuição recusada: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
