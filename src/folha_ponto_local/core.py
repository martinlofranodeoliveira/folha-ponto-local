"""Motor determinístico de espelho de ponto e banco de horas em minutos inteiros exatos."""
from __future__ import annotations

from datetime import date
from fractions import Fraction
import re

from .errors import TimesheetError
from .jsoncodec import dumps

SCHEMA_VERSION = 1
FATOR_HORA_NOTURNA = Fraction(8, 7)  # 60 min / 52,5 min (hora noturna reduzida)

MAX_COLABORADORES = 200
MAX_DIAS_POR_COLABORADOR = 367
MAX_BATIDAS_POR_DIA = 16
MAX_PERIODO_DIAS = 366
MAX_SALDO_ANTERIOR = 100_000

TIPOS_DIA = ("normal", "feriado", "folga_compensada", "ausencia_justificada")
DIAS_SEMANA_KEYS = ("1", "2", "3", "4", "5", "6", "7")
NOMES_DIA_SEMANA = {
    1: "segunda-feira",
    2: "terca-feira",
    3: "quarta-feira",
    4: "quinta-feira",
    5: "sexta-feira",
    6: "sabado",
    7: "domingo",
}

ALERTA_INTRAJORNADA = "INTRAJORNADA_INSUFICIENTE"
ALERTA_INTERJORNADA = "INTERJORNADA_INSUFICIENTE"

_ASCII_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")
_DATE_RE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})\Z")
_HHMM_RE = re.compile(r"([0-9]{2}):([0-9]{2})\Z")
_TIMESTAMP_RE = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2})T([0-9]{2}):([0-9]{2})\Z")


def _err(code: str, message: str, path: str = "") -> TimesheetError:
    return TimesheetError(code, message, path)


def _clone(value: object) -> object:
    if type(value) is dict:
        return {k: _clone(v) for k, v in value.items()}
    if type(value) is list:
        return [_clone(v) for v in value]
    return value


def _require_dict(value: object, expected_keys: set[str], path: str, optional_keys: set[str] | None = None) -> dict[str, object]:
    if type(value) is not dict:
        raise _err("INPUT_SCHEMA", "Esperado objeto JSON na estrutura.", path)
    actual = set(value.keys())
    allowed = expected_keys | (optional_keys or set())
    if not expected_keys.issubset(actual) or not actual.issubset(allowed):
        raise _err("INPUT_SCHEMA", "Campos obrigatórios ausentes ou chaves desconhecidas no objeto.", path)
    return value


def _require_int(value: object, minimum: int, maximum: int, path: str, code: str = "INPUT_LIMIT") -> int:
    if type(value) is not int:
        raise _err("INPUT_SCHEMA", "Campo numérico exige inteiro estrito (sem bool ou float).", path)
    if not (minimum <= value <= maximum):
        raise _err(code, "Valor inteiro fora do intervalo permitido.", path)
    return value


def _require_clean_text(value: object, min_len: int, max_len: int, path: str, *, trim_check: bool = True) -> str:
    if type(value) is not str:
        raise _err("INPUT_SCHEMA", "Campo textual exige string.", path)
    if not (min_len <= len(value) <= max_len):
        raise _err("INPUT_LIMIT", "Comprimento de texto fora dos limites permitidos.", path)
    if trim_check and value and (value != value.strip()):
        raise _err("INPUT_SCHEMA", "Texto não pode iniciar ou terminar com espaços.", path)
    for ch in value:
        codepoint = ord(ch)
        if codepoint < 32 or codepoint == 127 or (0xD800 <= codepoint <= 0xDFFF) or codepoint in (0x2028, 0x2029):
            raise _err("INPUT_SCHEMA", "Texto contém caractere de controle ou separador inválido.", path)
    return value


def _require_ascii_id(value: object, path: str) -> str:
    if type(value) is not str or _ASCII_ID.fullmatch(value) is None:
        raise _err("INPUT_ID", "Identificador deve ser ASCII estrito de 1 a 80 caracteres.", path)
    return value


def _parse_date(value: object, path: str) -> tuple[str, date]:
    if type(value) is not str or _DATE_RE.fullmatch(value) is None:
        raise _err("INPUT_DATE", "Data deve seguir estritamente o formato YYYY-MM-DD.", path)
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise _err("INPUT_DATE", "Data de calendário inexistente.", path) from None
    return value, parsed


def _parse_hhmm(value: object, path: str) -> tuple[str, int]:
    if type(value) is not str:
        raise _err("INPUT_TIME", "Horário deve ser texto no formato HH:MM.", path)
    match = _HHMM_RE.fullmatch(value)
    if match is None:
        raise _err("INPUT_TIME", "Horário deve seguir estritamente o formato HH:MM.", path)
    hour, minute = int(match[1]), int(match[2])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise _err("INPUT_TIME", "Hora ou minuto fora do intervalo 00:00..23:59.", path)
    return value, hour * 60 + minute


def _parse_timestamp(value: object, path: str) -> tuple[str, date, int]:
    if type(value) is not str:
        raise _err("INPUT_TIMESTAMP", "Carimbo de batida deve ser texto ISO YYYY-MM-DDTHH:MM.", path)
    match = _TIMESTAMP_RE.fullmatch(value)
    if match is None:
        raise _err("INPUT_TIMESTAMP", "Carimbo de batida deve seguir YYYY-MM-DDTHH:MM sem segundos.", path)
    _, d_obj = _parse_date(match[1], path)
    hour, minute = int(match[2]), int(match[3])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise _err("INPUT_TIMESTAMP", "Hora ou minuto da batida fora do intervalo 00:00..23:59.", path)
    abs_minute = d_obj.toordinal() * 1440 + hour * 60 + minute
    return value, d_obj, abs_minute


def _night_minutes_for_interval(start_min: int, end_min: int, day_ordinal: int, win_start: int, win_end: int) -> int:
    """Calcula a interseção exata em minutos de [start_min, end_min] com as janelas noturnas."""
    total = 0
    if win_start > win_end:
        for u in (day_ordinal - 1, day_ordinal, day_ordinal + 1):
            w_a = u * 1440 + win_start
            w_b = (u + 1) * 1440 + win_end
            overlap = min(end_min, w_b) - max(start_min, w_a)
            if overlap > 0:
                total += overlap
    else:
        for u in (day_ordinal, day_ordinal + 1):
            w_a = u * 1440 + win_start
            w_b = u * 1440 + win_end
            overlap = min(end_min, w_b) - max(start_min, w_a)
            if overlap > 0:
                total += overlap
    return total


def _rational_str(minutes: int) -> str:
    equiv = Fraction(minutes) * FATOR_HORA_NOTURNA
    return f"{equiv.numerator}/{equiv.denominator}"


class TimesheetResult:
    """Resultado imutável da apuração de espelho de ponto e banco de horas."""

    def __init__(self, source_doc: dict[str, object], report_doc: dict[str, object]) -> None:
        self._source = _clone(source_doc)
        self._report = _clone(report_doc)

    def to_dict(self) -> dict[str, object]:
        """Retorna um novo dicionário com o resultado completo da apuração."""
        return _clone(self._report)  # type: ignore[return-value]

    def source_document(self) -> dict[str, object]:
        """Retorna um novo dicionário com o documento de entrada validado."""
        return _clone(self._source)  # type: ignore[return-value]

    def night_equivalent_fraction(self, colaboradora_id: str | None = None) -> Fraction:
        """Retorna a equivalência exata de hora noturna reduzida (fator 8/7) como Fraction."""
        if colaboradora_id is None:
            resumo = self._report["resumo_geral"]  # type: ignore[index]
            return Fraction(resumo["minutos_noturnos_reais_total"]) * FATOR_HORA_NOTURNA  # type: ignore[index]
        for colab in self._report["colaboradores"]:  # type: ignore[index]
            if colab["id"] == colaboradora_id:
                return Fraction(colab["resumo"]["minutos_noturnos_reais_total"]) * FATOR_HORA_NOTURNA
        raise _err("INPUT_ID", "Colaborador solicitado não consta na apuração.", "colaboradores")


def apurar(document: object) -> TimesheetResult:
    """Valida integralmente a entrada e calcula o espelho de ponto e banco de horas em minutos exatos."""
    dumps(document)
    source = _clone(document)
    root = _require_dict(source, {"schema_version", "periodo", "regras", "colaboradores"}, "$")

    version = _require_int(root["schema_version"], SCHEMA_VERSION, SCHEMA_VERSION, "$.schema_version", "INPUT_SCHEMA")

    periodo_raw = _require_dict(root["periodo"], {"id", "titulo", "data_inicio", "data_fim"}, "$.periodo")
    periodo_id = _require_ascii_id(periodo_raw["id"], "$.periodo.id")
    periodo_titulo = _require_clean_text(periodo_raw["titulo"], 1, 160, "$.periodo.titulo")
    inicio_str, inicio_date = _parse_date(periodo_raw["data_inicio"], "$.periodo.data_inicio")
    fim_str, fim_date = _parse_date(periodo_raw["data_fim"], "$.periodo.data_fim")
    if inicio_date > fim_date:
        raise _err("INPUT_DATE", "Data inicial do período não pode ser posterior à data final.", "$.periodo")
    if (fim_date - inicio_date).days > MAX_PERIODO_DIAS:
        raise _err("INPUT_LIMIT", "Período excede o limite máximo de 366 dias.", "$.periodo")

    regras_raw = _require_dict(
        root["regras"],
        {
            "tolerancia_diaria_minutos",
            "interjornada_minima_minutos",
            "intrajornada_minima_acima_6h_minutos",
            "intrajornada_minima_4h_a_6h_minutos",
            "janela_noturna_inicio",
            "janela_noturna_fim",
            "saldo_anterior_minutos",
        },
        "$.regras",
    )
    tol_diaria = _require_int(regras_raw["tolerancia_diaria_minutos"], 0, 60, "$.regras.tolerancia_diaria_minutos")
    interjornada_min = _require_int(
        regras_raw["interjornada_minima_minutos"], 0, 1440, "$.regras.interjornada_minima_minutos"
    )
    intra_6h = _require_int(
        regras_raw["intrajornada_minima_acima_6h_minutos"],
        0,
        240,
        "$.regras.intrajornada_minima_acima_6h_minutos",
    )
    intra_4h = _require_int(
        regras_raw["intrajornada_minima_4h_a_6h_minutos"],
        0,
        120,
        "$.regras.intrajornada_minima_4h_a_6h_minutos",
    )
    if intra_4h > intra_6h:
        raise _err(
            "INPUT_SCHEMA",
            "Intrajornada mínima de 4h a 6h não pode superar a intrajornada acima de 6h.",
            "$.regras",
        )
    jan_ini_str, win_start = _parse_hhmm(regras_raw["janela_noturna_inicio"], "$.regras.janela_noturna_inicio")
    jan_fim_str, win_end = _parse_hhmm(regras_raw["janela_noturna_fim"], "$.regras.janela_noturna_fim")
    if win_start == win_end:
        raise _err("INPUT_TIME", "Janela noturna deve ter início e fim distintos.", "$.regras")
    saldo_anterior_padrao = _require_int(
        regras_raw["saldo_anterior_minutos"],
        -MAX_SALDO_ANTERIOR,
        MAX_SALDO_ANTERIOR,
        "$.regras.saldo_anterior_minutos",
    )

    colabs_raw = root["colaboradores"]
    if type(colabs_raw) is not list:
        raise _err("INPUT_SCHEMA", "A lista de colaboradores deve ser um array JSON.", "$.colaboradores")
    if not (1 <= len(colabs_raw) <= MAX_COLABORADORES):
        raise _err("INPUT_LIMIT", "Quantidade de colaboradores fora do limite 1..200.", "$.colaboradores")

    seen_ids: set[str] = set()
    colaboradores_out: list[dict[str, object]] = []

    tot_dias_avaliados = 0
    tot_previstos = 0
    tot_trabalhados = 0
    tot_regulares = 0
    tot_intrajornada = 0
    tot_noturnos = 0
    tot_credito = 0
    tot_debito = 0
    tot_variacao = 0
    tot_saldo_anterior = 0
    tot_saldo_final = 0
    tot_alertas = 0
    tot_alertas_intra = 0
    tot_alertas_inter = 0

    for c_idx, colab_item in enumerate(colabs_raw):
        c_path = f"$.colaboradores[{c_idx}]"
        colab = _require_dict(
            colab_item,
            {"id", "apelido", "funcao", "jornada_prevista_por_dia_semana", "dias"},
            c_path,
            optional_keys={"saldo_anterior_minutos"},
        )
        colab_id = _require_ascii_id(colab["id"], f"{c_path}.id")
        if colab_id in seen_ids:
            raise _err("INPUT_ID", "Identificador de colaborador duplicado.", f"{c_path}.id")
        seen_ids.add(colab_id)

        apelido = _require_clean_text(colab["apelido"], 1, 120, f"{c_path}.apelido")
        funcao = _require_clean_text(colab["funcao"], 1, 120, f"{c_path}.funcao")

        if "saldo_anterior_minutos" in colab:
            saldo_inicial = _require_int(
                colab["saldo_anterior_minutos"],
                -MAX_SALDO_ANTERIOR,
                MAX_SALDO_ANTERIOR,
                f"{c_path}.saldo_anterior_minutos",
            )
        else:
            saldo_inicial = saldo_anterior_padrao

        jornada_raw = _require_dict(
            colab["jornada_prevista_por_dia_semana"],
            set(DIAS_SEMANA_KEYS),
            f"{c_path}.jornada_prevista_por_dia_semana",
        )
        jornada_semana: dict[str, int] = {}
        for k in DIAS_SEMANA_KEYS:
            jornada_semana[k] = _require_int(
                jornada_raw[k], 0, 1440, f"{c_path}.jornada_prevista_por_dia_semana.{k}"
            )

        dias_raw = colab["dias"]
        if type(dias_raw) is not list:
            raise _err("INPUT_SCHEMA", "A lista de dias deve ser um array JSON.", f"{c_path}.dias")
        if not (1 <= len(dias_raw) <= MAX_DIAS_POR_COLABORADOR):
            raise _err("INPUT_LIMIT", "Quantidade de dias por colaborador fora do limite permitido.", f"{c_path}.dias")

        prev_date: date | None = None
        ultima_saida_anterior: int | None = None
        saldo_acumulado = saldo_inicial

        c_previstos = 0
        c_trabalhados = 0
        c_regulares = 0
        c_intrajornada = 0
        c_noturnos = 0
        c_credito = 0
        c_debito = 0
        c_variacao = 0
        c_dias_trab = 0
        c_dias_tol = 0
        c_dias_alerta = 0
        c_alertas_total = 0
        c_alertas_intra = 0
        c_alertas_inter = 0

        dias_out: list[dict[str, object]] = []

        for d_idx, dia_item in enumerate(dias_raw):
            d_path = f"{c_path}.dias[{d_idx}]"
            dia = _require_dict(dia_item, {"data", "tipo_dia", "batidas", "observacao"}, d_path)
            data_str, dia_date = _parse_date(dia["data"], f"{d_path}.data")
            if not (inicio_date <= dia_date <= fim_date):
                raise _err("INPUT_DATE", "Data do registro diário está fora do período informado.", f"{d_path}.data")
            if prev_date is not None and dia_date <= prev_date:
                raise _err(
                    "INPUT_DATE_ORDER",
                    "Registros diários devem estar em ordem cronológica estrita e sem datas repetidas.",
                    f"{d_path}.data",
                )
            prev_date = dia_date

            tipo_dia = dia["tipo_dia"]
            if type(tipo_dia) is not str or tipo_dia not in TIPOS_DIA:
                raise _err("INPUT_SCHEMA", "Tipo de dia inválido.", f"{d_path}.tipo_dia")

            obs_raw = dia["observacao"]
            if obs_raw is not None:
                observacao: str | None = _require_clean_text(obs_raw, 0, 240, f"{d_path}.observacao")
            else:
                observacao = None

            batidas_raw = dia["batidas"]
            if type(batidas_raw) is not list:
                raise _err("INPUT_SCHEMA", "O campo batidas deve ser uma lista de pares.", f"{d_path}.batidas")
            if len(batidas_raw) > MAX_BATIDAS_POR_DIA:
                raise _err("INPUT_LIMIT", "Quantidade de batidas no dia excede o limite.", f"{d_path}.batidas")

            day_ord = dia_date.toordinal()
            day_start_min = day_ord * 1440
            day_end_min = (day_ord + 1) * 1440
            next_day_end_min = (day_ord + 2) * 1440

            batidas_out: list[dict[str, object]] = []
            minutos_trabalhados = 0
            minutos_intrajornada = 0
            minutos_noturnos = 0
            prev_saida_no_dia: int | None = None
            primeira_entrada_dia: int | None = None
            ultima_saida_dia: int | None = None

            for b_idx, bat_item in enumerate(batidas_raw):
                b_path = f"{d_path}.batidas[{b_idx}]"
                bat = _require_dict(bat_item, {"entrada", "saida"}, b_path)
                ent_str, _, ent_min = _parse_timestamp(bat["entrada"], f"{b_path}.entrada")
                sai_str, _, sai_min = _parse_timestamp(bat["saida"], f"{b_path}.saida")

                if b_idx == 0:
                    if not (day_start_min <= ent_min < day_end_min):
                        raise _err(
                            "INPUT_BATIDA_DATE",
                            "A primeira entrada do dia deve ocorrer na própria data do registro.",
                            f"{b_path}.entrada",
                        )
                else:
                    if not (day_start_min <= ent_min < next_day_end_min):
                        raise _err(
                            "INPUT_BATIDA_DATE",
                            "Entrada fora do intervalo permitido para a jornada do dia.",
                            f"{b_path}.entrada",
                        )
                if not (day_start_min < sai_min < next_day_end_min):
                    raise _err(
                        "INPUT_BATIDA_DATE",
                        "Saída deve ocorrer na data do registro ou no dia imediatamente seguinte.",
                        f"{b_path}.saida",
                    )
                if ent_min >= sai_min:
                    raise _err(
                        "INPUT_BATIDA_INTERVAL",
                        "Horário de saída deve ser estritamente posterior ao horário de entrada.",
                        b_path,
                    )
                if prev_saida_no_dia is not None:
                    if ent_min <= prev_saida_no_dia:
                        raise _err(
                            "INPUT_BATIDA_OVERLAP",
                            "Batidas do mesmo dia devem ser estritamente cronológicas e não sobrepostas.",
                            b_path,
                        )
                    minutos_intrajornada += ent_min - prev_saida_no_dia

                if b_idx == 0:
                    primeira_entrada_dia = ent_min
                prev_saida_no_dia = sai_min
                ultima_saida_dia = sai_min

                duracao = sai_min - ent_min
                noturno_par = _night_minutes_for_interval(ent_min, sai_min, day_ord, win_start, win_end)
                minutos_trabalhados += duracao
                minutos_noturnos += noturno_par

                batidas_out.append(
                    {
                        "entrada": ent_str,
                        "saida": sai_str,
                        "duracao_minutos": duracao,
                        "minutos_noturnos_reais": noturno_par,
                    }
                )

            if minutos_trabalhados > 1440:
                raise _err("INPUT_LIMIT", "Total trabalhado no mesmo dia não pode exceder 1440 minutos.", d_path)

            if primeira_entrada_dia is not None and ultima_saida_anterior is not None:
                if primeira_entrada_dia < ultima_saida_anterior:
                    raise _err(
                        "INPUT_BATIDA_OVERLAP",
                        "Jornada do dia sobrepõe o encerramento da jornada anterior.",
                        d_path,
                    )
                descanso_interjornada: int | None = primeira_entrada_dia - ultima_saida_anterior
            else:
                descanso_interjornada = None

            if ultima_saida_dia is not None:
                ultima_saida_anterior = ultima_saida_dia

            weekday = dia_date.isoweekday()
            if tipo_dia in ("feriado", "ausencia_justificada"):
                minutos_previstos = 0
            else:
                minutos_previstos = jornada_semana[str(weekday)]

            minutos_regulares = min(minutos_trabalhados, minutos_previstos)
            diferenca_bruta = minutos_trabalhados - minutos_previstos
            if minutos_previstos > 0 and 0 < abs(diferenca_bruta) <= tol_diaria:
                tolerancia_aplicada = True
                variacao_liquida = 0
            else:
                tolerancia_aplicada = False
                variacao_liquida = diferenca_bruta

            credito_dia = max(0, variacao_liquida)
            debito_dia = max(0, -variacao_liquida)
            saldo_acumulado += variacao_liquida

            alertas_dia: list[str] = []
            if minutos_trabalhados > 360 and minutos_intrajornada < intra_6h:
                alertas_dia.append(ALERTA_INTRAJORNADA)
                c_alertas_intra += 1
            elif 240 < minutos_trabalhados <= 360 and minutos_intrajornada < intra_4h:
                alertas_dia.append(ALERTA_INTRAJORNADA)
                c_alertas_intra += 1

            if descanso_interjornada is not None and descanso_interjornada < interjornada_min:
                alertas_dia.append(ALERTA_INTERJORNADA)
                c_alertas_inter += 1

            if batidas_out:
                c_dias_trab += 1
            if tolerancia_aplicada:
                c_dias_tol += 1
            if alertas_dia:
                c_dias_alerta += 1
                c_alertas_total += len(alertas_dia)

            c_previstos += minutos_previstos
            c_trabalhados += minutos_trabalhados
            c_regulares += minutos_regulares
            c_intrajornada += minutos_intrajornada
            c_noturnos += minutos_noturnos
            c_credito += credito_dia
            c_debito += debito_dia
            c_variacao += variacao_liquida

            dias_out.append(
                {
                    "data": data_str,
                    "dia_semana_iso": weekday,
                    "dia_semana_nome": NOMES_DIA_SEMANA[weekday],
                    "tipo_dia": tipo_dia,
                    "batidas": batidas_out,
                    "minutos_previstos": minutos_previstos,
                    "minutos_trabalhados": minutos_trabalhados,
                    "minutos_regulares": minutos_regulares,
                    "minutos_intervalo_intrajornada": minutos_intrajornada,
                    "minutos_noturnos_reais": minutos_noturnos,
                    "minutos_noturnos_equivalentes_racional": _rational_str(minutos_noturnos),
                    "diferenca_bruta_minutos": diferenca_bruta,
                    "tolerancia_aplicada": tolerancia_aplicada,
                    "variacao_liquida_minutos": variacao_liquida,
                    "credito_minutos": credito_dia,
                    "debito_minutos": debito_dia,
                    "descanso_interjornada_minutos": descanso_interjornada,
                    "alertas": alertas_dia,
                    "saldo_banco_acumulado_minutos": saldo_acumulado,
                    "observacao": observacao,
                }
            )

        resumo_colab = {
            "saldo_anterior_minutos": saldo_inicial,
            "dias_registrados": len(dias_out),
            "dias_trabalhados": c_dias_trab,
            "dias_com_tolerancia": c_dias_tol,
            "dias_com_alerta": c_dias_alerta,
            "minutos_previstos_total": c_previstos,
            "minutos_trabalhados_total": c_trabalhados,
            "minutos_regulares_total": c_regulares,
            "minutos_intervalo_intrajornada_total": c_intrajornada,
            "minutos_noturnos_reais_total": c_noturnos,
            "minutos_noturnos_equivalentes_racional": _rational_str(c_noturnos),
            "credito_minutos_total": c_credito,
            "debito_minutos_total": c_debito,
            "variacao_liquida_minutos_total": c_variacao,
            "saldo_banco_minutos": saldo_acumulado,
            "total_alertas": c_alertas_total,
            "alertas_intrajornada": c_alertas_intra,
            "alertas_interjornada": c_alertas_inter,
        }

        colaboradores_out.append(
            {
                "id": colab_id,
                "apelido": apelido,
                "funcao": funcao,
                "jornada_prevista_por_dia_semana": jornada_semana,
                "resumo": resumo_colab,
                "dias": dias_out,
            }
        )

        tot_dias_avaliados += len(dias_out)
        tot_previstos += c_previstos
        tot_trabalhados += c_trabalhados
        tot_regulares += c_regulares
        tot_intrajornada += c_intrajornada
        tot_noturnos += c_noturnos
        tot_credito += c_credito
        tot_debito += c_debito
        tot_variacao += c_variacao
        tot_saldo_anterior += saldo_inicial
        tot_saldo_final += saldo_acumulado
        tot_alertas += c_alertas_total
        tot_alertas_intra += c_alertas_intra
        tot_alertas_inter += c_alertas_inter

    report_doc: dict[str, object] = {
        "schema_version": version,
        "motor": "folha-ponto-local",
        "versao_motor": "1.0.0",
        "periodo": {
            "id": periodo_id,
            "titulo": periodo_titulo,
            "data_inicio": inicio_str,
            "data_fim": fim_str,
        },
        "regras": {
            "tolerancia_diaria_minutos": tol_diaria,
            "interjornada_minima_minutos": interjornada_min,
            "intrajornada_minima_acima_6h_minutos": intra_6h,
            "intrajornada_minima_4h_a_6h_minutos": intra_4h,
            "janela_noturna_inicio": jan_ini_str,
            "janela_noturna_fim": jan_fim_str,
            "saldo_anterior_minutos": saldo_anterior_padrao,
            "fator_hora_noturna_reduzida": "8/7",
        },
        "resumo_geral": {
            "quantidade_colaboradores": len(colaboradores_out),
            "quantidade_dias_avaliados": tot_dias_avaliados,
            "minutos_previstos_total": tot_previstos,
            "minutos_trabalhados_total": tot_trabalhados,
            "minutos_regulares_total": tot_regulares,
            "minutos_intervalo_intrajornada_total": tot_intrajornada,
            "minutos_noturnos_reais_total": tot_noturnos,
            "minutos_noturnos_equivalentes_racional": _rational_str(tot_noturnos),
            "credito_minutos_total": tot_credito,
            "debito_minutos_total": tot_debito,
            "variacao_liquida_minutos_total": tot_variacao,
            "saldo_anterior_minutos_total": tot_saldo_anterior,
            "saldo_banco_minutos_total": tot_saldo_final,
            "total_alertas": tot_alertas,
            "alertas_intrajornada_total": tot_alertas_intra,
            "alertas_interjornada_total": tot_alertas_inter,
        },
        "colaboradores": colaboradores_out,
    }

    return TimesheetResult(source, report_doc)
