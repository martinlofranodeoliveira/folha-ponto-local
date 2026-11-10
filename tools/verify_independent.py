#!/usr/bin/env python3
"""Verificador independente de pacotes ZIP gerados por folha-ponto-local.

Usa apenas a biblioteca padrão do Python (sem importar folha_ponto_local):
1. Oráculo discreto minuto a minuto (conjuntos explícitos de minutos inteiros).
2. Oráculo relacional SQLite (agregação SQL sobre espelho-diario.csv e resumo-colaboradores.csv).
3. Verificação de SHA256SUMS, CSV (neutralização de fórmulas), SVG (XML seguro) e ouro manual (--demo).
"""
from __future__ import annotations

import argparse
import csv
from datetime import date
from fractions import Fraction
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

NAMES = (
    "LEIA-ME.md",
    "RELATORIO.md",
    "SHA256SUMS",
    "apuracao.json",
    "entrada.json",
    "espelho-diario.csv",
    "mapa-jornada.svg",
    "resumo-colaboradores.csv",
)

DEMO_SUMMARY = {
    "quantidade_colaboradores": 3,
    "quantidade_dias_avaliados": 42,
    "minutos_previstos_total": 12000,
    "minutos_trabalhados_total": 11892,
    "minutos_regulares_total": 11474,
    "minutos_intervalo_intrajornada_total": 1210,
    "minutos_noturnos_reais_total": 3030,
    "minutos_noturnos_equivalentes_racional": "24240/7",
    "credito_minutos_total": 405,
    "debito_minutos_total": 510,
    "variacao_liquida_minutos_total": -105,
    "saldo_anterior_minutos_total": 90,
    "saldo_banco_minutos_total": -15,
    "total_alertas": 3,
    "alertas_intrajornada_total": 2,
    "alertas_interjornada_total": 1,
}

DEMO_WORKERS_GOLD = {
    "colab-demo-01": {
        "minutos_previstos_total": 4320,
        "minutos_trabalhados_total": 3932,
        "minutos_noturnos_reais_total": 0,
        "credito_minutos_total": 90,
        "debito_minutos_total": 480,
        "variacao_liquida_minutos_total": -390,
        "saldo_banco_minutos": -360,
        "dias_com_tolerancia": 2,
        "alertas_intrajornada": 1,
        "alertas_interjornada": 0,
    },
    "colab-demo-02": {
        "minutos_previstos_total": 4320,
        "minutos_trabalhados_total": 4605,
        "minutos_noturnos_reais_total": 3030,
        "credito_minutos_total": 285,
        "debito_minutos_total": 0,
        "variacao_liquida_minutos_total": 285,
        "saldo_banco_minutos": 315,
        "dias_com_tolerancia": 0,
        "alertas_intrajornada": 0,
        "alertas_interjornada": 1,
    },
    "colab-demo-03": {
        "minutos_previstos_total": 3360,
        "minutos_trabalhados_total": 3355,
        "minutos_noturnos_reais_total": 0,
        "credito_minutos_total": 30,
        "debito_minutos_total": 30,
        "variacao_liquida_minutos_total": 0,
        "saldo_banco_minutos": 30,
        "dias_com_tolerancia": 2,
        "alertas_intrajornada": 1,
        "alertas_interjornada": 0,
    },
}


class CheckError(ValueError):
    """Falha de verificação independente."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CheckError(message)


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        require(key not in result, "JSON contém chave duplicada")
        result[key] = value
    return result


def _reject_float(_token: str) -> None:
    raise CheckError("JSON contém ponto flutuante não permitido")


def _reject_constant(_token: str) -> None:
    raise CheckError("JSON contém constante não finita")


def load_json(data: bytes | str) -> object:
    if isinstance(data, bytes):
        text = data.decode("utf-8", errors="strict")
    else:
        text = data
    require(not text.startswith("\ufeff"), "JSON contém BOM")
    return json.loads(
        text,
        parse_int=int,
        parse_float=_reject_float,
        parse_constant=_reject_constant,
        object_pairs_hook=_pairs,
    )


def json_equal(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(json_equal(left[k], right[k]) for k in left)
    if type(left) is list:
        return len(left) == len(right) and all(json_equal(a, b) for a, b in zip(left, right))
    return left == right


def csv_safe(value: str) -> str:
    stripped = value.lstrip(" ")
    if stripped.startswith(("=", "+", "-", "@", "\t", "\r")) or value.startswith(("\t", "\r")):
        return "'" + value
    return value


def parse_abs_minute(ts: str) -> int:
    d_part, t_part = ts.split("T")
    d_obj = date.fromisoformat(d_part)
    hh, mm = map(int, t_part.split(":"))
    return d_obj.toordinal() * 1440 + hh * 60 + mm


def parse_hhmm(hhmm: str) -> int:
    hh, mm = map(int, hhmm.split(":"))
    return hh * 60 + mm


def discrete_oracle(entrada_doc: dict[str, object], apuracao_doc: dict[str, object]) -> int:
    """Recomputa toda a apuração por conjuntos discretos minuto a minuto e confronta apuracao.json."""
    regras = entrada_doc["regras"]  # type: ignore[index]
    win_start = parse_hhmm(regras["janela_noturna_inicio"])
    win_end = parse_hhmm(regras["janela_noturna_fim"])
    tol = regras["tolerancia_diaria_minutos"]
    inter_min = regras["interjornada_minima_minutos"]
    intra_6h = regras["intrajornada_minima_acima_6h_minutos"]
    intra_4h = regras["intrajornada_minima_4h_a_6h_minutos"]
    saldo_padrao = regras["saldo_anterior_minutos"]

    if win_start > win_end:
        night_mod_set = {m for m in range(1440) if m >= win_start or m < win_end}
    else:
        night_mod_set = {m for m in range(1440) if win_start <= m < win_end}

    total_discrete_minutes_checked = 0
    colabs_in = entrada_doc["colaboradores"]  # type: ignore[index]
    colabs_out = apuracao_doc["colaboradores"]  # type: ignore[index]
    require(len(colabs_in) == len(colabs_out), "Quantidade de colaboradores diverge")

    for c_in, c_out in zip(colabs_in, colabs_out):
        require(c_in["id"] == c_out["id"], "ID de colaborador diverge")
        jornada_map = c_in["jornada_prevista_por_dia_semana"]
        saldo_ant = c_in.get("saldo_anterior_minutos", saldo_padrao)
        saldo_acum = saldo_ant
        all_worker_minutes: set[int] = set()
        last_exit_prev_day: int | None = None

        dias_in = c_in["dias"]
        dias_out = c_out["dias"]
        require(len(dias_in) == len(dias_out), "Quantidade de dias diverge")

        for d_in, d_out in zip(dias_in, dias_out):
            require(d_in["data"] == d_out["data"], "Data do dia diverge")
            d_obj = date.fromisoformat(d_in["data"])
            weekday = d_obj.isoweekday()
            require(d_out["dia_semana_iso"] == weekday, "Dia da semana ISO diverge")

            day_minutes: set[int] = set()
            day_night_minutes: set[int] = set()
            prev_s: int | None = None
            first_e: int | None = None
            last_s: int | None = None
            intrajornada = 0

            require(len(d_in["batidas"]) == len(d_out["batidas"]), "Quantidade de batidas diverge")
            for b_in, b_out in zip(d_in["batidas"], d_out["batidas"]):
                e_min = parse_abs_minute(b_in["entrada"])
                s_min = parse_abs_minute(b_in["saida"])
                require(e_min < s_min, "Batida com intervalo vazio ou invertido")
                interval_set = set(range(e_min, s_min))
                require(day_minutes.isdisjoint(interval_set), "Sobreposição de minutos no mesmo dia")
                require(all_worker_minutes.isdisjoint(interval_set), "Sobreposição de minutos entre dias")
                day_minutes |= interval_set
                all_worker_minutes |= interval_set
                total_discrete_minutes_checked += len(interval_set)

                night_set = {m for m in interval_set if (m % 1440) in night_mod_set}
                day_night_minutes |= night_set

                require(b_out["duracao_minutos"] == len(interval_set), "Duração da batida diverge do conjunto discreto")
                require(
                    b_out["minutos_noturnos_reais"] == len(night_set),
                    "Minutos noturnos da batida divergem do oráculo discreto",
                )
                if prev_s is not None:
                    require(e_min > prev_s, "Batidas não estritamente separadas")
                    intrajornada += e_min - prev_s
                if first_e is None:
                    first_e = e_min
                prev_s = s_min
                last_s = s_min

            trab = len(day_minutes)
            noturno = len(day_night_minutes)
            previsto = 0 if d_in["tipo_dia"] in ("feriado", "ausencia_justificada") else jornada_map[str(weekday)]
            regular = min(trab, previsto)
            diff_bruta = trab - previsto
            tol_aplicada = previsto > 0 and 0 < abs(diff_bruta) <= tol
            var_liq = 0 if tol_aplicada else diff_bruta
            cred = max(0, var_liq)
            deb = max(0, -var_liq)
            saldo_acum += var_liq

            if first_e is not None and last_exit_prev_day is not None:
                descanso = first_e - last_exit_prev_day
            else:
                descanso = None
            if last_s is not None:
                last_exit_prev_day = last_s

            alertas_esp: list[str] = []
            if trab > 360 and intrajornada < intra_6h:
                alertas_esp.append("INTRAJORNADA_INSUFICIENTE")
            elif 240 < trab <= 360 and intrajornada < intra_4h:
                alertas_esp.append("INTRAJORNADA_INSUFICIENTE")
            if descanso is not None and descanso < inter_min:
                alertas_esp.append("INTERJORNADA_INSUFICIENTE")

            equiv_frac = Fraction(noturno * 8, 7)
            require(d_out["minutos_previstos"] == previsto, "minutos_previstos diverge")
            require(d_out["minutos_trabalhados"] == trab, "minutos_trabalhados diverge")
            require(d_out["minutos_regulares"] == regular, "minutos_regulares diverge")
            require(d_out["minutos_intervalo_intrajornada"] == intrajornada, "intrajornada diverge")
            require(d_out["minutos_noturnos_reais"] == noturno, "minutos_noturnos_reais diverge")
            require(
                d_out["minutos_noturnos_equivalentes_racional"] == f"{equiv_frac.numerator}/{equiv_frac.denominator}",
                "Equivalência noturna racional diverge",
            )
            require(d_out["diferenca_bruta_minutos"] == diff_bruta, "diferenca_bruta_minutos diverge")
            require(d_out["tolerancia_aplicada"] is tol_aplicada, "tolerancia_aplicada diverge")
            require(d_out["variacao_liquida_minutos"] == var_liq, "variacao_liquida_minutos diverge")
            require(d_out["credito_minutos"] == cred, "credito_minutos diverge")
            require(d_out["debito_minutos"] == deb, "debito_minutos diverge")
            require(d_out["descanso_interjornada_minutos"] == descanso, "descanso_interjornada_minutos diverge")
            require(d_out["alertas"] == alertas_esp, "alertas do dia divergem")
            require(d_out["saldo_banco_acumulado_minutos"] == saldo_acum, "saldo_banco_acumulado_minutos diverge")

    return total_discrete_minutes_checked


def sqlite_oracle(espelho_bytes: bytes, resumo_bytes: bytes, apuracao_doc: dict[str, object]) -> int:
    """Importa os dois CSVs no SQLite em memória e confere agregações e saldos acumulados via SQL."""
    espelho_text = espelho_bytes.decode("utf-8")
    resumo_text = resumo_bytes.decode("utf-8")
    require("\r" not in espelho_text and espelho_text.endswith("\n"), "espelho-diario.csv deve usar LF")
    require("\r" not in resumo_text and resumo_text.endswith("\n"), "resumo-colaboradores.csv deve usar LF")

    espelho_rows = list(csv.DictReader(io.StringIO(espelho_text, newline="")))
    resumo_rows = list(csv.DictReader(io.StringIO(resumo_text, newline="")))

    conn = sqlite3.connect(":memory:")
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE espelho (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                colaborador_id TEXT NOT NULL,
                data TEXT NOT NULL,
                tipo_dia TEXT NOT NULL,
                batidas_resumo TEXT NOT NULL,
                minutos_previstos INTEGER NOT NULL,
                minutos_trabalhados INTEGER NOT NULL,
                minutos_regulares INTEGER NOT NULL,
                minutos_intervalo_intrajornada INTEGER NOT NULL,
                minutos_noturnos_reais INTEGER NOT NULL,
                diferenca_bruta_minutos INTEGER NOT NULL,
                tolerancia_aplicada INTEGER NOT NULL,
                variacao_liquida_minutos INTEGER NOT NULL,
                credito_minutos INTEGER NOT NULL,
                debito_minutos INTEGER NOT NULL,
                descanso_interjornada_minutos INTEGER,
                alertas TEXT NOT NULL,
                saldo_banco_acumulado_minutos INTEGER NOT NULL
            )
            """
        )
        for row in espelho_rows:
            cur.execute(
                """
                INSERT INTO espelho (
                    colaborador_id, data, tipo_dia, batidas_resumo,
                    minutos_previstos, minutos_trabalhados, minutos_regulares,
                    minutos_intervalo_intrajornada, minutos_noturnos_reais,
                    diferenca_bruta_minutos, tolerancia_aplicada, variacao_liquida_minutos,
                    credito_minutos, debito_minutos, descanso_interjornada_minutos,
                    alertas, saldo_banco_acumulado_minutos
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["colaborador_id"],
                    row["data"],
                    row["tipo_dia"],
                    row["batidas_resumo"],
                    int(row["minutos_previstos"]),
                    int(row["minutos_trabalhados"]),
                    int(row["minutos_regulares"]),
                    int(row["minutos_intervalo_intrajornada"]),
                    int(row["minutos_noturnos_reais"]),
                    int(row["diferenca_bruta_minutos"]),
                    1 if row["tolerancia_aplicada"] == "true" else 0,
                    int(row["variacao_liquida_minutos"]),
                    int(row["credito_minutos"]),
                    int(row["debito_minutos"]),
                    None if row["descanso_interjornada_minutos"] == "" else int(row["descanso_interjornada_minutos"]),
                    row["alertas"],
                    int(row["saldo_banco_acumulado_minutos"]),
                ),
            )
        integrity = cur.execute("PRAGMA integrity_check").fetchone()[0]
        require(integrity == "ok", "Falha em PRAGMA integrity_check no SQLite")

        resumo_by_id = {r["colaborador_id"]: r for r in resumo_rows}
        require(len(resumo_by_id) == len(apuracao_doc["colaboradores"]), "Linhas em resumo-colaboradores.csv divergem")  # type: ignore[arg-type]

        for colab in apuracao_doc["colaboradores"]:  # type: ignore[index]
            cid_safe = csv_safe(colab["id"])
            require(cid_safe in resumo_by_id, "Colaborador ausente do CSV de resumo")
            r_csv = resumo_by_id[cid_safe]
            r_json = colab["resumo"]

            require(r_csv["apelido"] == csv_safe(colab["apelido"]), "Neutralização CSV de apelido incorreta")
            require(r_csv["funcao"] == csv_safe(colab["funcao"]), "Neutralização CSV de função incorreta")

            agg = cur.execute(
                """
                SELECT
                    COUNT(*),
                    SUM(CASE WHEN minutos_trabalhados > 0 THEN 1 ELSE 0 END),
                    SUM(tolerancia_aplicada),
                    SUM(CASE WHEN alertas != '' THEN 1 ELSE 0 END),
                    SUM(minutos_previstos),
                    SUM(minutos_trabalhados),
                    SUM(minutos_regulares),
                    SUM(minutos_intervalo_intrajornada),
                    SUM(minutos_noturnos_reais),
                    SUM(credito_minutos),
                    SUM(debito_minutos),
                    SUM(variacao_liquida_minutos)
                FROM espelho
                WHERE colaborador_id = ?
                """,
                (cid_safe,),
            ).fetchone()

            expected_tuple = (
                r_json["dias_registrados"],
                r_json["dias_trabalhados"],
                r_json["dias_com_tolerancia"],
                r_json["dias_com_alerta"],
                r_json["minutos_previstos_total"],
                r_json["minutos_trabalhados_total"],
                r_json["minutos_regulares_total"],
                r_json["minutos_intervalo_intrajornada_total"],
                r_json["minutos_noturnos_reais_total"],
                r_json["credito_minutos_total"],
                r_json["debito_minutos_total"],
                r_json["variacao_liquida_minutos_total"],
            )
            require(agg == expected_tuple, "Agregação SQL por colaborador diverge de apuracao.json")
            require(int(r_csv["saldo_banco_minutos"]) == r_json["saldo_banco_minutos"], "Saldo final CSV diverge")
            require(
                int(r_csv["saldo_anterior_minutos"]) + int(r_csv["variacao_liquida_minutos_total"])
                == int(r_csv["saldo_banco_minutos"]),
                "Conservação de saldo no CSV violada",
            )

            win_rows = cur.execute(
                """
                SELECT
                    data,
                    ? + SUM(variacao_liquida_minutos) OVER (
                        PARTITION BY colaborador_id ORDER BY seq
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ),
                    saldo_banco_acumulado_minutos
                FROM espelho
                WHERE colaborador_id = ?
                ORDER BY seq
                """,
                (r_json["saldo_anterior_minutos"], cid_safe),
            ).fetchall()
            for _, calc_saldo, csv_saldo in win_rows:
                require(calc_saldo == csv_saldo, "Window function SQL de saldo acumulado diverge")

        return len(espelho_rows)
    finally:
        conn.close()


def check_svg(svg_bytes: bytes, apuracao_doc: dict[str, object]) -> None:
    require(b"<!DOCTYPE" not in svg_bytes.upper() and b"<!ENTITY" not in svg_bytes.upper(), "SVG contém DTD/ENTITY")
    root = ET.fromstring(svg_bytes)
    require(root.tag == "{http://www.w3.org/2000/svg}svg", "Raiz SVG inesperada")
    actual_workers: dict[str, tuple[int, int, int, int]] = {}
    for node in root.iter():
        tag_name = node.tag.rsplit("}", 1)[-1]
        require(tag_name not in {"script", "foreignObject", "image", "use"}, "SVG contém elemento proibido")
        for attr_k, attr_v in node.attrib.items():
            require(
                not attr_k.lower().startswith("on")
                and "href" not in attr_k.lower()
                and "url(" not in attr_v.lower(),
                "SVG contém atributo externo ou evento",
            )
        if node.get("class") == "colaborador":
            cid = node.get("data-colaborador-id", "")
            require(cid not in actual_workers, "SVG repete colaborador")
            actual_workers[cid] = (
                int(node.get("data-minutos-trabalhados", "-1")),
                int(node.get("data-minutos-noturnos", "-1")),
                int(node.get("data-saldo-banco", "0")),
                int(node.get("data-alertas", "-1")),
            )
    expected_workers = {
        c["id"]: (
            c["resumo"]["minutos_trabalhados_total"],
            c["resumo"]["minutos_noturnos_reais_total"],
            c["resumo"]["saldo_banco_minutos"],
            c["resumo"]["total_alertas"],
        )
        for c in apuracao_doc["colaboradores"]  # type: ignore[index]
    }
    require(actual_workers == expected_workers, "Atributos analíticos do SVG divergem de apuracao.json")


def check_demo(apuracao_doc: dict[str, object]) -> None:
    require(json_equal(apuracao_doc["resumo_geral"], DEMO_SUMMARY), "Resumo geral do exemplo diverge do ouro manual")
    actual = {c["id"]: c["resumo"] for c in apuracao_doc["colaboradores"]}  # type: ignore[index]
    require(set(actual.keys()) == set(DEMO_WORKERS_GOLD.keys()), "IDs do exemplo divergem do ouro manual")
    for cid, gold in DEMO_WORKERS_GOLD.items():
        for k, v in gold.items():
            require(actual[cid][k] == v, f"Campo {k} de {cid} diverge do ouro manual")


def check_bundle(path: Path, *, demo: bool = False, input_bytes: bytes | None = None) -> dict[str, object]:
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        require(tuple(entry.filename for entry in infos) == NAMES, "Nomes ou ordem do ZIP divergem do contrato")
        for entry in infos:
            require(entry.date_time == (1980, 1, 1, 0, 0, 0), "Timestamp ZIP não determinístico")
            require(stat.S_IMODE(entry.external_attr >> 16) == 0o644, "Permissão ZIP inesperada")
            require(not (entry.flag_bits & 1), "ZIP criptografado não suportado")
        require(archive.testzip() is None, "CRC do ZIP inválido")
        files = {name: archive.read(name) for name in NAMES}

    sha_lines = files["SHA256SUMS"].decode("ascii").splitlines()
    expected_sha = [
        f"{hashlib.sha256(files[name]).hexdigest()}  {name}"
        for name in NAMES
        if name != "SHA256SUMS"
    ]
    require(sha_lines == expected_sha, "SHA256SUMS interno não confere com os arquivos do pacote")

    if input_bytes is not None:
        require(files["entrada.json"] == input_bytes, "Bytes de entrada.json divergem da fonte")

    entrada_doc = load_json(files["entrada.json"])
    apuracao_doc = load_json(files["apuracao.json"])
    require(isinstance(entrada_doc, dict) and isinstance(apuracao_doc, dict), "Raiz JSON deve ser objeto")

    discrete_minutes = discrete_oracle(entrada_doc, apuracao_doc)
    sql_rows = sqlite_oracle(files["espelho-diario.csv"], files["resumo-colaboradores.csv"], apuracao_doc)
    check_svg(files["mapa-jornada.svg"], apuracao_doc)

    relatorio_txt = files["RELATORIO.md"].decode("utf-8")
    leiame_txt = files["LEIA-ME.md"].decode("utf-8")
    require("Aviso de escopo técnico" in relatorio_txt and len(leiame_txt.strip()) > 100, "Documentação interna incompleta")

    if demo:
        check_demo(apuracao_doc)
        require(sql_rows == 42, "Exemplo exige 42 linhas no espelho diário")

    return {
        "ok": True,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "discreteMinutesVerified": discrete_minutes,
        "sqliteDailyRows": sql_rows,
        "summary": apuracao_doc["resumo_geral"],
    }


def _check_network_isolation() -> bool:
    if sys.platform.startswith("linux"):
        net_dir = Path("/sys/class/net")
        if net_dir.is_dir():
            ifaces = {p.name for p in net_dir.iterdir()}
            require(ifaces <= {"lo"}, "Interface de rede externa visível no namespace isolado")
        route_file = Path("/proc/net/route")
        if route_file.is_file():
            lines = [ln.strip() for ln in route_file.read_text(encoding="ascii").splitlines() if ln.strip()]
            require(len(lines) <= 1, "Tabela de rotas IPv4 não está vazia no namespace isolado")
    return True


def _synthetic_edge_scenario() -> dict[str, object]:
    """Cenário de borda: limites de 4h/6h intrajornada, tolerância ±10/±11, interjornada 659/660 e líderes de fórmula."""
    return {
        "schema_version": 1,
        "periodo": {
            "id": "qa-borda-2026",
            "titulo": "Cenario Sintetico de Fronteiras Exatas",
            "data_inicio": "2026-06-01",
            "data_fim": "2026-06-06",
        },
        "regras": {
            "tolerancia_diaria_minutos": 10,
            "interjornada_minima_minutos": 660,
            "intrajornada_minima_acima_6h_minutos": 60,
            "intrajornada_minima_4h_a_6h_minutos": 15,
            "janela_noturna_inicio": "22:00",
            "janela_noturna_fim": "05:00",
            "saldo_anterior_minutos": -20,
        },
        "colaboradores": [
            {
                "id": "qa-colab-01",
                "apelido": "=Equipe Fronteira",
                "funcao": "+Operador de Turno",
                "jornada_prevista_por_dia_semana": {
                    "1": 480,
                    "2": 480,
                    "3": 360,
                    "4": 240,
                    "5": 480,
                    "6": 0,
                    "7": 0,
                },
                "dias": [
                    {
                        "data": "2026-06-01",
                        "tipo_dia": "normal",
                        "batidas": [
                            {"entrada": "2026-06-01T08:00", "saida": "2026-06-01T12:00"},
                            {"entrada": "2026-06-01T13:00", "saida": "2026-06-01T17:10"},
                        ],
                        "observacao": "@Limite superior exato da tolerancia (+10m -> variacao 0)",
                    },
                    {
                        "data": "2026-06-02",
                        "tipo_dia": "normal",
                        "batidas": [
                            {"entrada": "2026-06-02T08:00", "saida": "2026-06-02T12:00"},
                            {"entrada": "2026-06-02T13:00", "saida": "2026-06-02T23:01"},
                        ],
                        "observacao": "-Acima da tolerancia e avanco noturno (601m trab, 61m noturnos)",
                    },
                    {
                        "data": "2026-06-03",
                        "tipo_dia": "normal",
                        "batidas": [
                            {"entrada": "2026-06-03T10:00", "saida": "2026-06-03T13:00"},
                            {"entrada": "2026-06-03T13:15", "saida": "2026-06-03T16:15"},
                        ],
                        "observacao": "=Descanso de 659m (<660m: alerta inter) e 360m trab com 15m pausa (sem alerta intra)",
                    },
                    {
                        "data": "2026-06-04",
                        "tipo_dia": "normal",
                        "batidas": [
                            {"entrada": "2026-06-04T08:00", "saida": "2026-06-04T12:01"}
                        ],
                        "observacao": "241m trabalhados sem pausa (alerta intra 4h-6h)",
                    },
                    {
                        "data": "2026-06-05",
                        "tipo_dia": "normal",
                        "batidas": [
                            {"entrada": "2026-06-05T21:30", "saida": "2026-06-06T01:30"},
                            {"entrada": "2026-06-06T02:30", "saida": "2026-06-06T06:19"},
                        ],
                        "observacao": "469m trab (-11m fora da tolerancia -> debito 11m)",
                    },
                    {
                        "data": "2026-06-06",
                        "tipo_dia": "feriado",
                        "batidas": [],
                        "observacao": null if False else None,
                    },
                ],
            }
        ],
    }


def run_cli_suite(cli_path: Path, *, require_net_isolation: bool = False) -> dict[str, object]:
    if require_net_isolation:
        _check_network_isolation()
    with tempfile.TemporaryDirectory(prefix="folha-ponto-qa-") as tmp:
        tmp_dir = Path(tmp)
        env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
        env.update(PYTHONUTF8="1", PYTHONNOUSERSITE="1", HOME=str(tmp_dir))

        demo_zip = tmp_dir / "demo.zip"
        proc_demo = subprocess.run([str(cli_path), "exemplo", "--saida", str(demo_zip)], cwd=tmp_dir, env=env, capture_output=True, text=True)
        require(proc_demo.returncode == 0, "Falha ao executar CLI exemplo")
        demo_report = check_bundle(demo_zip, demo=True)

        edge_doc = _synthetic_edge_scenario()
        edge_json = tmp_dir / "borda.json"
        edge_bytes = (json.dumps(edge_doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        edge_json.write_bytes(edge_bytes)
        edge_zip = tmp_dir / "borda.zip"
        proc_edge = subprocess.run([str(cli_path), "apurar", str(edge_json), "--saida", str(edge_zip)], cwd=tmp_dir, env=env, capture_output=True, text=True)
        require(proc_edge.returncode == 0, "Falha ao executar CLI apurar no cenário de borda")
        edge_report = check_bundle(edge_zip, demo=False, input_bytes=edge_bytes)

        return {
            "ok": True,
            "networkIsolationChecked": require_net_isolation,
            "demo": demo_report,
            "edgeScenario": edge_report,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verificador independente de pacotes folha-ponto-local")
    parser.add_argument("bundle", nargs="?", type=Path, help="Pacote ZIP para verificar diretamente")
    parser.add_argument("--demo", action="store_true", help="Confere o ouro manual do cenário exemplo")
    parser.add_argument("--entrada", type=Path, help="Compara bytes exatos de entrada.json")
    parser.add_argument("--cli", type=Path, help="Executa bateria independente contra o executável CLI instalado")
    parser.add_argument("--require-network-isolation", action="store_true", help="Exige namespace de rede isolado")
    parser.add_argument("--report", type=Path, help="Salva relatório JSON da verificação")
    args = parser.parse_args(argv)
    try:
        if args.cli:
            payload = run_cli_suite(args.cli, require_net_isolation=args.require_network_isolation)
        elif args.bundle:
            if args.require_network_isolation:
                _check_network_isolation()
            payload = check_bundle(
                args.bundle,
                demo=args.demo,
                input_bytes=args.entrada.read_bytes() if args.entrada else None,
            )
        else:
            raise CheckError("Informe um arquivo ZIP ou --cli")
        output = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(output + "\n", encoding="utf-8")
        print(output)
        return 0
    except Exception as exc:
        print(f"Falha na verificação independente: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
