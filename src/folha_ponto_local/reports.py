"""Geração determinística de relatórios (JSON, CSV, Markdown, SVG e SHA256SUMS) em pacote ZIP."""
from __future__ import annotations

import csv
import hashlib
from html import escape
import io
import json
import os
from pathlib import Path
import tempfile
import zipfile

from .core import TimesheetResult, apurar
from .errors import TimesheetError
from .jsoncodec import dumps_output, loads

MAX_BUNDLE_BYTES = 268_435_456

REPORT_FILES = (
    "LEIA-ME.md",
    "RELATORIO.md",
    "SHA256SUMS",
    "apuracao.json",
    "entrada.json",
    "espelho-diario.csv",
    "mapa-jornada.svg",
    "resumo-colaboradores.csv",
)

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def apurar_json(data: bytes | str) -> TimesheetResult:
    """Decodifica JSON estrito e executa a apuração."""
    return apurar(loads(data))


def _safe_cell(value: str) -> str:
    stripped = value.lstrip(" ")
    if stripped.startswith(_FORMULA_PREFIXES) or value.startswith(("\t", "\r")):
        return "'" + value
    return value


def _xml_text(value: str) -> str:
    if any(ord(ch) in (0xFFFE, 0xFFFF) for ch in value):
        return escape(json.dumps(value, ensure_ascii=True), quote=True)
    return escape(value, quote=True)


def _format_hm(minutes: int) -> str:
    sign = "-" if minutes < 0 else "+" if minutes > 0 else ""
    abs_min = abs(minutes)
    h, m = divmod(abs_min, 60)
    return f"{sign}{h:02d}h{m:02d}m"


def _format_hm_unsigned(minutes: int) -> str:
    abs_min = abs(minutes)
    h, m = divmod(abs_min, 60)
    prefix = "-" if minutes < 0 else ""
    return f"{prefix}{h:02d}h{m:02d}m"


def _espelho_diario_csv(report: dict[str, object]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(
        (
            "colaborador_id",
            "apelido",
            "funcao",
            "data",
            "dia_semana_iso",
            "tipo_dia",
            "batidas_resumo",
            "minutos_previstos",
            "minutos_trabalhados",
            "minutos_regulares",
            "minutos_intervalo_intrajornada",
            "minutos_noturnos_reais",
            "diferenca_bruta_minutos",
            "tolerancia_aplicada",
            "variacao_liquida_minutos",
            "credito_minutos",
            "debito_minutos",
            "descanso_interjornada_minutos",
            "alertas",
            "saldo_banco_acumulado_minutos",
            "observacao",
        )
    )
    for colab in report["colaboradores"]:  # type: ignore[index]
        c_id = _safe_cell(colab["id"])
        apelido = _safe_cell(colab["apelido"])
        funcao = _safe_cell(colab["funcao"])
        for dia in colab["dias"]:
            batidas_str = " | ".join(f"{b['entrada']}..{b['saida']}" for b in dia["batidas"])
            descanso = "" if dia["descanso_interjornada_minutos"] is None else str(dia["descanso_interjornada_minutos"])
            alertas_str = ";".join(dia["alertas"])
            obs_str = _safe_cell(dia["observacao"] or "")
            writer.writerow(
                (
                    c_id,
                    apelido,
                    funcao,
                    dia["data"],
                    str(dia["dia_semana_iso"]),
                    dia["tipo_dia"],
                    _safe_cell(batidas_str),
                    str(dia["minutos_previstos"]),
                    str(dia["minutos_trabalhados"]),
                    str(dia["minutos_regulares"]),
                    str(dia["minutos_intervalo_intrajornada"]),
                    str(dia["minutos_noturnos_reais"]),
                    str(dia["diferenca_bruta_minutos"]),
                    "true" if dia["tolerancia_aplicada"] else "false",
                    str(dia["variacao_liquida_minutos"]),
                    str(dia["credito_minutos"]),
                    str(dia["debito_minutos"]),
                    descanso,
                    _safe_cell(alertas_str),
                    str(dia["saldo_banco_acumulado_minutos"]),
                    obs_str,
                )
            )
    return stream.getvalue().encode("utf-8")


def _resumo_colaboradores_csv(report: dict[str, object]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(
        (
            "colaborador_id",
            "apelido",
            "funcao",
            "dias_registrados",
            "dias_trabalhados",
            "dias_com_tolerancia",
            "dias_com_alerta",
            "minutos_previstos_total",
            "minutos_trabalhados_total",
            "minutos_regulares_total",
            "minutos_intervalo_intrajornada_total",
            "minutos_noturnos_reais_total",
            "minutos_noturnos_equivalentes_racional",
            "credito_minutos_total",
            "debito_minutos_total",
            "variacao_liquida_minutos_total",
            "saldo_anterior_minutos",
            "saldo_banco_minutos",
            "total_alertas",
            "alertas_intrajornada",
            "alertas_interjornada",
        )
    )
    for colab in report["colaboradores"]:  # type: ignore[index]
        res = colab["resumo"]
        writer.writerow(
            (
                _safe_cell(colab["id"]),
                _safe_cell(colab["apelido"]),
                _safe_cell(colab["funcao"]),
                str(res["dias_registrados"]),
                str(res["dias_trabalhados"]),
                str(res["dias_com_tolerancia"]),
                str(res["dias_com_alerta"]),
                str(res["minutos_previstos_total"]),
                str(res["minutos_trabalhados_total"]),
                str(res["minutos_regulares_total"]),
                str(res["minutos_intervalo_intrajornada_total"]),
                str(res["minutos_noturnos_reais_total"]),
                res["minutos_noturnos_equivalentes_racional"],
                str(res["credito_minutos_total"]),
                str(res["debito_minutos_total"]),
                str(res["variacao_liquida_minutos_total"]),
                str(res["saldo_anterior_minutos"]),
                str(res["saldo_banco_minutos"]),
                str(res["total_alertas"]),
                str(res["alertas_intrajornada"]),
                str(res["alertas_interjornada"]),
            )
        )
    return stream.getvalue().encode("utf-8")


def _relatorio_md(report: dict[str, object]) -> bytes:
    periodo = report["periodo"]  # type: ignore[index]
    regras = report["regras"]  # type: ignore[index]
    geral = report["resumo_geral"]  # type: ignore[index]
    colabs = report["colaboradores"]  # type: ignore[index]

    lines = [
        f"# Relatório de Apuração de Ponto — {periodo['titulo']}",
        "",
        "> **Aviso de escopo técnico:** Este relatório é produzido por um motor de cálculo aritmético",
        "> determinístico em minutos inteiros exatos sobre carimbos de tempo e parâmetros fornecidos",
        "> pelo usuário. Não constitui certificação jurídica, homologação trabalhista ou contábil,",
        "> nem substitui registrador eletrônico de ponto (REP) ou parecer especializado.",
        "",
        "## 1. Parâmetros do Período e Regras Aplicadas",
        "",
        f"- **Identificador do período:** `{periodo['id']}`",
        f"- **Intervalo:** `{periodo['data_inicio']}` a `{periodo['data_fim']}`",
        f"- **Tolerância diária:** `{regras['tolerancia_diaria_minutos']} min`",
        f"- **Descanso interjornada mínimo:** `{regras['interjornada_minima_minutos']} min` ({_format_hm_unsigned(regras['interjornada_minima_minutos'])})",
        f"- **Intrajornada mínima (> 6h):** `{regras['intrajornada_minima_acima_6h_minutos']} min`",
        f"- **Intrajornada mínima (> 4h até 6h):** `{regras['intrajornada_minima_4h_a_6h_minutos']} min`",
        f"- **Janela noturna:** `{regras['janela_noturna_inicio']}` às `{regras['janela_noturna_fim']}` (fator reduzido `{regras['fator_hora_noturna_reduzida']}`)",
        f"- **Saldo anterior padrão:** `{regras['saldo_anterior_minutos']} min` ({_format_hm(regras['saldo_anterior_minutos'])})",
        "",
        "## 2. Resumo Geral Consolidado",
        "",
        "| Indicador | Minutos Exatos | Equivalência HH:MM |",
        "| --- | ---: | ---: |",
        f"| Colaboradores avaliados | {geral['quantidade_colaboradores']} | — |",
        f"| Dias registrados | {geral['quantidade_dias_avaliados']} | — |",
        f"| Minutos previstos | {geral['minutos_previstos_total']} min | {_format_hm_unsigned(geral['minutos_previstos_total'])} |",
        f"| Minutos trabalhados | {geral['minutos_trabalhados_total']} min | {_format_hm_unsigned(geral['minutos_trabalhados_total'])} |",
        f"| Minutos regulares | {geral['minutos_regulares_total']} min | {_format_hm_unsigned(geral['minutos_regulares_total'])} |",
        f"| Minutos noturnos reais | {geral['minutos_noturnos_reais_total']} min | {_format_hm_unsigned(geral['minutos_noturnos_reais_total'])} |",
        f"| Créditos no banco de horas | {geral['credito_minutos_total']} min | {_format_hm(geral['credito_minutos_total'])} |",
        f"| Débitos no banco de horas | {geral['debito_minutos_total']} min | -{_format_hm_unsigned(geral['debito_minutos_total'])} |",
        f"| Variação líquida do período | {geral['variacao_liquida_minutos_total']} min | {_format_hm(geral['variacao_liquida_minutos_total'])} |",
        f"| Saldo anterior somado | {geral['saldo_anterior_minutos_total']} min | {_format_hm(geral['saldo_anterior_minutos_total'])} |",
        f"| **Saldo final do banco de horas** | **{geral['saldo_banco_minutos_total']} min** | **{_format_hm(geral['saldo_banco_minutos_total'])}** |",
        f"| Total de alertas de conformidade | {geral['total_alertas']} | — |",
        "",
        "## 3. Resumo por Colaborador",
        "",
        "| ID | Apelido | Função | Previsto | Trabalhado | Noturno Real | Crédito | Débito | Saldo Ant. | Saldo Final | Alertas |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    for colab in colabs:
        r = colab["resumo"]
        lines.append(
            f"| `{colab['id']}` | {colab['apelido']} | {colab['funcao']} "
            f"| {r['minutos_previstos_total']}m | {r['minutos_trabalhados_total']}m "
            f"| {r['minutos_noturnos_reais_total']}m | +{r['credito_minutos_total']}m "
            f"| -{r['debito_minutos_total']}m | {r['saldo_anterior_minutos']}m "
            f"| **{r['saldo_banco_minutos']}m** ({_format_hm(r['saldo_banco_minutos'])}) | {r['total_alertas']} |"
        )

    lines.extend(["", "## 4. Detalhamento Diário e Ocorrências", ""])
    for colab in colabs:
        r = colab["resumo"]
        lines.extend(
            [
                f"### {colab['apelido']} (`{colab['id']}`) — {colab['funcao']}",
                "",
                f"- **Saldo anterior:** `{r['saldo_anterior_minutos']} min` | "
                f"**Variação líquida:** `{r['variacao_liquida_minutos_total']} min` | "
                f"**Saldo final:** `{r['saldo_banco_minutos']} min` ({_format_hm(r['saldo_banco_minutos'])})",
                "",
                "| Data | Tipo | Previsto | Trabalhado | Intervalo | Noturno | Variação Líq. | Saldo Acum. | Status / Alertas |",
                "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
            ]
        )
        for dia in colab["dias"]:
            flags = []
            if dia["tolerancia_aplicada"]:
                flags.append("TOLERANCIA_DIARIA")
            flags.extend(dia["alertas"])
            if dia["descanso_interjornada_minutos"] is not None and "INTERJORNADA_INSUFICIENTE" in dia["alertas"]:
                flags.append(f"descanso={dia['descanso_interjornada_minutos']}m")
            status_txt = ", ".join(flags) if flags else "OK"
            lines.append(
                f"| `{dia['data']}` | `{dia['tipo_dia']}` | {dia['minutos_previstos']}m "
                f"| {dia['minutos_trabalhados']}m | {dia['minutos_intervalo_intrajornada']}m "
                f"| {dia['minutos_noturnos_reais']}m | {dia['variacao_liquida_minutos']}m "
                f"| {dia['saldo_banco_acumulado_minutos']}m | {status_txt} |"
            )
        lines.append("")

    return "\n".join(lines).encode("utf-8")


def _svg(report: dict[str, object]) -> bytes:
    """Renderiza painel visual SVG 1440x900 usando exclusivamente aritmética inteira."""
    periodo = report["periodo"]  # type: ignore[index]
    regras = report["regras"]  # type: ignore[index]
    geral = report["resumo_geral"]  # type: ignore[index]
    colabs = report["colaboradores"]  # type: ignore[index]

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="900" viewBox="0 0 1440 900" role="img" aria-labelledby="title desc">',
        f'<title id="title">Mapa de Jornada e Banco de Horas — {_xml_text(periodo["titulo"])}</title>',
        '<desc id="desc">Painel estático de apuração de espelho de ponto e banco de horas em minutos inteiros exatos, com indicação de turnos noturnos, tolerâncias e alertas de intervalo.</desc>',
        '<rect width="1440" height="900" fill="#f8fafc"/>',
        '<g font-family="DejaVu Sans, Segoe UI, Arial, sans-serif" fill="#0f172a">',
        # Top header banner
        '<rect x="0" y="0" width="1440" height="96" fill="#0f172a"/>',
        '<text x="40" y="42" font-size="24" font-weight="bold" fill="#f8fafc">Folha de Ponto Local · Espelho Diário e Banco de Horas</text>',
        f'<text x="40" y="72" font-size="12" fill="#cbd5e1">Período: {_xml_text(periodo["titulo"])} · Tolerância ±{regras["tolerancia_diaria_minutos"]}m · Interjornada mín. {regras["interjornada_minima_minutos"]}m · Janela noturna {_xml_text(regras["janela_noturna_inicio"])}–{_xml_text(regras["janela_noturna_fim"])}</text>',
        '<rect x="1150" y="24" width="250" height="48" rx="8" fill="#1e293b" stroke="#334155"/>',
        '<text x="1275" y="45" text-anchor="middle" font-size="12" fill="#94a3b8">ARITMÉTICA INTEIRA EXATA</text>',
        '<text x="1275" y="63" text-anchor="middle" font-size="13" font-weight="bold" fill="#38bdf8">LOFRANO TECNOLOGIA · v1.0.0</text>',
    ]

    # KPI Summary Cards (y=114..194)
    kpis = [
        ("COLABORADORES / DIAS", f"{geral['quantidade_colaboradores']} colab · {geral['quantidade_dias_avaliados']} dias", "Escala pseudônima local", "#0f766e"),
        ("TRABALHADO vs PREVISTO", f"{geral['minutos_trabalhados_total']}m / {geral['minutos_previstos_total']}m", f"Equiv. {_format_hm_unsigned(geral['minutos_trabalhados_total'])} trab.", "#0369a1"),
        ("MINUTOS NOTURNOS REAIS", f"{geral['minutos_noturnos_reais_total']} min", f"Equiv. reduzida (8/7): {geral['minutos_noturnos_equivalentes_racional']}m", "#4338ca"),
        ("CRÉDITOS / DÉBITOS", f"+{geral['credito_minutos_total']}m / -{geral['debito_minutos_total']}m", f"Variação líquida: {_format_hm(geral['variacao_liquida_minutos_total'])}", "#15803d"),
        ("SALDO FINAL BANCO", f"{geral['saldo_banco_minutos_total']} min ({_format_hm(geral['saldo_banco_minutos_total'])})", f"Alertas: {geral['total_alertas']} ({geral['alertas_intrajornada_total']} intra, {geral['alertas_interjornada_total']} inter)", "#b91c1c" if geral["total_alertas"] > 0 else "#0f766e"),
    ]
    for idx, (label, main_val, sub_val, accent) in enumerate(kpis):
        cx = 40 + idx * 274
        lines.extend(
            [
                f'<rect x="{cx}" y="114" width="258" height="84" rx="8" fill="#ffffff" stroke="#cbd5e1" stroke-width="1"/>',
                f'<rect x="{cx}" y="114" width="6" height="84" rx="3" fill="{accent}"/>',
                f'<text x="{cx + 20}" y="138" font-size="11" font-weight="bold" fill="#64748b">{label}</text>',
                f'<text x="{cx + 20}" y="164" font-size="18" font-weight="bold" fill="#0f172a">{_xml_text(main_val)}</text>',
                f'<text x="{cx + 20}" y="185" font-size="12" fill="#475569">{_xml_text(sub_val)}</text>',
            ]
        )

    # Main Left Panel: Daily Grid per Worker (x=40, y=214, w=920, h=590)
    lines.extend(
        [
            '<rect x="40" y="214" width="920" height="590" rx="10" fill="#ffffff" stroke="#cbd5e1"/>',
            '<text x="64" y="246" font-size="16" font-weight="bold" fill="#0f172a">Espelho Diário de Jornada, Turnos Noturnos e Conformidade</text>',
            '<text x="64" y="266" font-size="12" fill="#475569">Barras comparam minutos trabalhados com a jornada prevista; selos destacam tolerância diária, horas noturnas (22:00–05:00) e alertas.</text>',
        ]
    )

    visible_colabs = colabs[:4]
    row_height = 124
    for c_idx, colab in enumerate(visible_colabs):
        res = colab["resumo"]
        c_id_xml = _xml_text(colab["id"])
        ry = 286 + c_idx * row_height
        lines.append(
            f'<g class="colaborador" data-colaborador-id="{c_id_xml}" '
            f'data-minutos-trabalhados="{res["minutos_trabalhados_total"]}" '
            f'data-minutos-noturnos="{res["minutos_noturnos_reais_total"]}" '
            f'data-saldo-banco="{res["saldo_banco_minutos"]}" '
            f'data-alertas="{res["total_alertas"]}">'
        )
        lines.append(f'<title>{c_id_xml} — {_xml_text(colab["apelido"])}</title>')
        lines.append(
            f'<rect x="58" y="{ry}" width="884" height="112" rx="8" fill="#f8fafc" stroke="#e2e8f0"/>'
        )
        lines.append(
            f'<text x="72" y="{ry + 24}" font-size="13" font-weight="bold" fill="#0f172a">{_xml_text(colab["apelido"])}</text>'
        )
        lines.append(
            f'<text x="72" y="{ry + 43}" font-size="11" fill="#475569">{c_id_xml} · {_xml_text(colab["funcao"])}</text>'
        )
        saldo_color = "#15803d" if res["saldo_banco_minutos"] >= 0 else "#b91c1c"
        lines.append(
            f'<text x="72" y="{ry + 66}" font-size="12" font-weight="bold" fill="{saldo_color}">Saldo Final: {res["saldo_banco_minutos"]}m ({_format_hm(res["saldo_banco_minutos"])})</text>'
        )
        lines.append(
            f'<text x="72" y="{ry + 85}" font-size="11" fill="#334155">Trab: {res["minutos_trabalhados_total"]}m · Prev: {res["minutos_previstos_total"]}m</text>'
        )
        lines.append(
            f'<text x="72" y="{ry + 101}" font-size="11" fill="#4338ca">Noturno: {res["minutos_noturnos_reais_total"]}m · Alertas: {res["total_alertas"]}</text>'
        )

        dias = colab["dias"]
        shown_dias = dias[:14]
        n_cells = max(1, len(shown_dias))
        grid_x0 = 310
        cell_w = min(44, 616 // n_cells)

        for d_idx, dia in enumerate(shown_dias):
            dx = grid_x0 + d_idx * cell_w
            dy = ry + 12
            trab = dia["minutos_trabalhados"]
            prev = dia["minutos_previstos"]
            var_liq = dia["variacao_liquida_minutos"]
            noturno = dia["minutos_noturnos_reais"]
            alertas = dia["alertas"]
            tol = dia["tolerancia_aplicada"]

            if alertas:
                fill_bar = "#dc2626"
                bg_cell = "#fef2f2"
            elif tol:
                fill_bar = "#0284c7"
                bg_cell = "#f0f9ff"
            elif var_liq > 0:
                fill_bar = "#16a34a"
                bg_cell = "#f0fdf4"
            elif var_liq < 0:
                fill_bar = "#d97706"
                bg_cell = "#fffbeb"
            elif trab > 0:
                fill_bar = "#0f766e"
                bg_cell = "#f0fdfa"
            else:
                fill_bar = "#94a3b8"
                bg_cell = "#f1f5f9"

            bar_h = min(52, (trab * 52) // 600) if trab > 0 else 4
            bar_y = dy + 60 - bar_h
            prev_y = dy + 60 - min(52, (prev * 52) // 600)

            short_date = dia["data"][8:10] + "/" + dia["data"][5:7]
            lines.append(
                f'<g class="dia-jornada" data-colaborador-id="{c_id_xml}" data-data="{dia["data"]}" '
                f'data-minutos-trabalhados="{trab}" data-variacao-liquida="{var_liq}">'
            )
            lines.append(
                f'<rect x="{dx}" y="{dy}" width="{cell_w - 4}" height="88" rx="4" fill="{bg_cell}" stroke="#cbd5e1" stroke-width="1"/>'
            )
            if prev > 0:
                lines.append(
                    f'<line x1="{dx + 3}" y1="{prev_y}" x2="{dx + cell_w - 7}" y2="{prev_y}" stroke="#64748b" stroke-width="1" stroke-dasharray="2,2"/>'
                )
            lines.append(
                f'<rect x="{dx + 6}" y="{bar_y}" width="{max(8, cell_w - 16)}" height="{bar_h}" rx="2" fill="{fill_bar}"/>'
            )
            if noturno > 0:
                lines.append(
                    f'<circle cx="{dx + cell_w - 10}" cy="{dy + 10}" r="4" fill="#4338ca"/>'
                )
            if alertas:
                lines.append(
                    f'<rect x="{dx + 4}" y="{dy + 3}" width="12" height="11" rx="2" fill="#b91c1c"/>'
                )
                lines.append(
                    f'<text x="{dx + 10}" y="{dy + 12}" text-anchor="middle" font-size="9" font-weight="bold" fill="#ffffff">!</text>'
                )
            lines.append(
                f'<text x="{dx + (cell_w - 4) // 2}" y="{dy + 73}" text-anchor="middle" font-size="9" font-weight="bold" fill="#1e293b">{trab}m</text>'
            )
            lines.append(
                f'<text x="{dx + (cell_w - 4) // 2}" y="{dy + 84}" text-anchor="middle" font-size="8" fill="#475569">{short_date}</text>'
            )
            lines.append("</g>")

        lines.append("</g>")

    # For any additional collaborators beyond the 4 visual rows, still include their structured node
    for colab in colabs[4:]:
        res = colab["resumo"]
        c_id_xml = _xml_text(colab["id"])
        lines.append(
            f'<g class="colaborador" data-colaborador-id="{c_id_xml}" '
            f'data-minutos-trabalhados="{res["minutos_trabalhados_total"]}" '
            f'data-minutos-noturnos="{res["minutos_noturnos_reais_total"]}" '
            f'data-saldo-banco="{res["saldo_banco_minutos"]}" '
            f'data-alertas="{res["total_alertas"]}">'
            f'<title>{c_id_xml}</title></g>'
        )

    # Right Panel: Bank of Hours Balance & Night Equivalence (x=980, y=214, w=420, h=590)
    lines.extend(
        [
            '<rect x="980" y="214" width="420" height="590" rx="10" fill="#ffffff" stroke="#cbd5e1"/>',
            '<text x="1004" y="246" font-size="16" font-weight="bold" fill="#0f172a">Balanço do Banco de Horas</text>',
            '<text x="1004" y="266" font-size="12" fill="#475569">Composição exata: Saldo Anterior + Créditos − Débitos</text>',
        ]
    )

    max_abs_saldo = max(
        [60]
        + [
            max(
                abs(c["resumo"]["saldo_banco_minutos"]),
                abs(c["resumo"]["credito_minutos_total"]),
                abs(c["resumo"]["debito_minutos_total"]),
            )
            for c in visible_colabs
        ]
    )
    zero_x = 1190

    for c_idx, colab in enumerate(visible_colabs):
        res = colab["resumo"]
        by = 292 + c_idx * 120
        saldo = res["saldo_banco_minutos"]
        cred = res["credito_minutos_total"]
        deb = res["debito_minutos_total"]
        ant = res["saldo_anterior_minutos"]

        lines.extend(
            [
                f'<rect x="1000" y="{by}" width="380" height="106" rx="8" fill="#f8fafc" stroke="#e2e8f0"/>',
                f'<text x="1014" y="{by + 22}" font-size="13" font-weight="bold" fill="#0f172a">{_xml_text(colab["apelido"])}</text>',
                f'<text x="1366" y="{by + 22}" text-anchor="end" font-size="12" font-weight="bold" fill="{"#15803d" if saldo >= 0 else "#b91c1c"}">{saldo} min ({_format_hm(saldo)})</text>',
                f'<line x1="{zero_x}" y1="{by + 32}" x2="{zero_x}" y2="{by + 84}" stroke="#64748b" stroke-width="1"/>',
            ]
        )

        # Credit bar (right of zero_x)
        cred_w = (cred * 150) // max_abs_saldo
        if cred_w > 0:
            lines.append(
                f'<rect x="{zero_x}" y="{by + 36}" width="{max(4, cred_w)}" height="12" rx="2" fill="#16a34a"/>'
            )
        # Debit bar (left of zero_x)
        deb_w = (deb * 150) // max_abs_saldo
        if deb_w > 0:
            lines.append(
                f'<rect x="{zero_x - max(4, deb_w)}" y="{by + 52}" width="{max(4, deb_w)}" height="12" rx="2" fill="#d97706"/>'
            )
        # Final balance bar
        saldo_w = (abs(saldo) * 150) // max_abs_saldo
        if saldo >= 0:
            lines.append(
                f'<rect x="{zero_x}" y="{by + 68}" width="{max(4, saldo_w)}" height="12" rx="2" fill="#0f766e"/>'
            )
        else:
            lines.append(
                f'<rect x="{zero_x - max(4, saldo_w)}" y="{by + 68}" width="{max(4, saldo_w)}" height="12" rx="2" fill="#b91c1c"/>'
            )

        lines.append(
            f'<text x="1014" y="{by + 98}" font-size="11" fill="#334155">Ant: {ant}m · Créd: +{cred}m · Déb: -{deb}m · Líq: {res["variacao_liquida_minutos_total"]}m</text>'
        )

    # Legend & Footer (y=818..884)
    legend_items = [
        ("#0f766e", "Jornada regular"),
        ("#0284c7", "Tolerância aplicada"),
        ("#16a34a", "Crédito / Hora extra"),
        ("#d97706", "Débito / Compensação"),
        ("#dc2626", "Alerta (Intra/Interjornada)"),
        ("#4338ca", "● Turno com minutos noturnos (22h–05h)"),
    ]
    lines.append('<rect x="40" y="818" width="1360" height="62" rx="8" fill="#ffffff" stroke="#cbd5e1"/>')
    for l_idx, (l_color, l_text) in enumerate(legend_items):
        lx = 56 + l_idx * 210
        lines.append(f'<rect x="{lx}" y="833" width="14" height="14" rx="3" fill="{l_color}"/>')
        lines.append(f'<text x="{lx + 20}" y="844" font-size="12" fill="#1e293b">{l_text}</text>')
    lines.append(
        '<text x="60" y="868" font-size="11" fill="#64748b">Nota técnica: apuração determinística local sobre marcações fornecidas na entrada; todos os minutos e saldos constam integralmente em apuracao.json e nos arquivos CSV.</text>'
    )
    lines.extend(["</g>", "</svg>", ""])
    return "\n".join(lines).encode("utf-8")


_LEIAME = """# Pacote de Apuração — Folha de Ponto Local

Este arquivo ZIP contém a apuração determinística de espelho de ponto e banco
de horas calculada em minutos inteiros exatos pelo motor `folha-ponto-local`
(LOFRANO TECNOLOGIA, licença MIT).

## Arquivos incluídos

1. `entrada.json`: bytes exatos do documento de entrada submetido à apuração.
2. `apuracao.json`: estrutura analítica completa (período, regras, resumo geral,
   resumo por colaborador e memória diária com durações, intervalos, minutos
   noturnos, variações líquidas, saldos acumulados e alertas).
3. `espelho-diario.csv`: tabela diária em UTF-8 (terminador LF) pronta para
   planilhas ou importação relacional, com neutralização de fórmulas em campos
   textuais iniciados por `=`, `+`, `-` ou `@`.
4. `resumo-colaboradores.csv`: consolidação por colaborador com totais de
   minutos previstos, trabalhados, regulares, noturnos, créditos, débitos,
   saldo final do banco de horas e contagem de alertas.
5. `RELATORIO.md`: relatório textual auditável em português com tabelas de
   conferência e aviso de escopo técnico.
6. `mapa-jornada.svg`: visualização estática de alto contraste (1440×900) sem
   recursos externos, apresentando o mapa diário de jornada e o balanço do
   banco de horas.
7. `LEIA-ME.md`: este guia de leitura do pacote.
8. `SHA256SUMS`: hashes SHA-256 de todos os demais arquivos do pacote.

## Escopo e limites

O motor executa aritmética inteira exata em minutos e equivalência racional
(`8/7`) sobre carimbos `YYYY-MM-DDTHH:MM` e parâmetros declarados na entrada.
Não realiza homologação jurídica, trabalhista ou contábil, nem substitui
registrador eletrônico de ponto (REP).
"""


def _same_json(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(_same_json(v, right[k]) for k, v in left.items())
    if type(left) is list:
        return len(left) == len(right) and all(_same_json(a, b) for a, b in zip(left, right))
    return left == right


def _verified_source(result: TimesheetResult, input_bytes: bytes) -> None:
    if type(input_bytes) is not bytes:
        raise TimesheetError("REPORT_SOURCE", "A fonte do relatório deve ser fornecida em bytes originais.")
    if not _same_json(loads(input_bytes), result.source_document()):
        raise TimesheetError("REPORT_SOURCE", "Os bytes da entrada divergem do documento apurado.")


def build_report(result: TimesheetResult, input_bytes: bytes) -> bytes:
    """Constrói e verifica em memória o pacote ZIP determinístico de apuração."""
    _verified_source(result, input_bytes)
    report_data = result.to_dict()
    files: dict[str, bytes] = {
        "LEIA-ME.md": _LEIAME.encode("utf-8"),
        "RELATORIO.md": _relatorio_md(report_data),
        "apuracao.json": dumps_output(report_data).encode("utf-8"),
        "entrada.json": input_bytes,
        "espelho-diario.csv": _espelho_diario_csv(report_data),
        "mapa-jornada.svg": _svg(report_data),
        "resumo-colaboradores.csv": _resumo_colaboradores_csv(report_data),
    }
    sha_lines = [
        f"{hashlib.sha256(content).hexdigest()}  {name}\n"
        for name, content in sorted(files.items())
    ]
    files["SHA256SUMS"] = "".join(sha_lines).encode("ascii")

    if sum(map(len, files.values())) > MAX_BUNDLE_BYTES:
        raise TimesheetError("REPORT_LIMIT", "Pacote de apuração excede o limite de 256 MiB descompactados.")

    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    bundle_bytes = stream.getvalue()
    _verify_report(bundle_bytes, files)
    return bundle_bytes


def _verify_report(bundle_bytes: bytes, expected: dict[str, bytes]) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(bundle_bytes)) as archive:
            if tuple(archive.namelist()) != REPORT_FILES or archive.testzip() is not None:
                raise ValueError
            for name, content in expected.items():
                received = archive.read(name)
                if len(received) != len(content) or hashlib.sha256(received).digest() != hashlib.sha256(content).digest():
                    raise ValueError
    except (ValueError, OSError, zipfile.BadZipFile):
        raise TimesheetError("REPORT_INVALID", "Falha na verificação de integridade do pacote ZIP.") from None


def write_report(path: str | os.PathLike[str], result: TimesheetResult, input_bytes: bytes) -> Path:
    """Publica o pacote ZIP exclusivamente por hardlink de arquivo temporário irmão."""
    content = build_report(result, input_bytes)
    target = Path(path)
    staged: Path | None = None
    try:
        if not target.parent.is_dir():
            raise TimesheetError("OUTPUT_PARENT", "O diretório de saída deve existir.")
        if os.path.lexists(target):
            raise TimesheetError("OUTPUT_EXISTS", "O destino já existe; não será sobrescrito.")
        descriptor, staged_name = tempfile.mkstemp(prefix=".folha-ponto-local-", suffix=".tmp", dir=target.parent)
        staged = Path(staged_name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            if hasattr(os, "fchmod"):
                os.fchmod(handle.fileno(), 0o644)
            os.fsync(handle.fileno())
        if staged.read_bytes() != content:
            raise TimesheetError("REPORT_INVALID", "Falha na conferência do arquivo preparado.")
        try:
            os.link(staged, target)
        except FileExistsError:
            raise TimesheetError("OUTPUT_EXISTS", "O destino já existe; não será sobrescrito.") from None
        except OSError:
            raise TimesheetError(
                "OUTPUT_LINK",
                "Não foi possível publicar por hardlink exclusivo neste sistema de arquivos.",
            ) from None
        return target
    except TimesheetError:
        raise
    except (OSError, ValueError):
        raise TimesheetError("OUTPUT_IO", "Não foi possível preparar o pacote no diretório de saída.") from None
    finally:
        if staged is not None:
            try:
                staged.unlink()
            except OSError:
                pass
