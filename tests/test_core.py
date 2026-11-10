from __future__ import annotations

from fractions import Fraction
import unittest

from folha_ponto_local import TimesheetError, apurar


def _base_doc() -> dict[str, object]:
    return {
        "schema_version": 1,
        "periodo": {
            "id": "p-2026-11",
            "titulo": "Periodo de Teste",
            "data_inicio": "2026-11-02",
            "data_fim": "2026-11-08",
        },
        "regras": {
            "tolerancia_diaria_minutos": 10,
            "interjornada_minima_minutos": 660,
            "intrajornada_minima_acima_6h_minutos": 60,
            "intrajornada_minima_4h_a_6h_minutos": 15,
            "janela_noturna_inicio": "22:00",
            "janela_noturna_fim": "05:00",
            "saldo_anterior_minutos": 50,
        },
        "colaboradores": [
            {
                "id": "colab-01",
                "apelido": "Operador Base",
                "funcao": "Tecnico",
                "jornada_prevista_por_dia_semana": {
                    "1": 480,
                    "2": 480,
                    "3": 480,
                    "4": 480,
                    "5": 480,
                    "6": 0,
                    "7": 0,
                },
                "dias": [
                    {
                        "data": "2026-11-03",
                        "tipo_dia": "normal",
                        "batidas": [
                            {"entrada": "2026-11-03T08:00", "saida": "2026-11-03T12:00"},
                            {"entrada": "2026-11-03T13:00", "saida": "2026-11-03T17:00"},
                        ],
                        "observacao": None,
                    }
                ],
            }
        ],
    }


class TestCoreCalculation(unittest.TestCase):
    def test_exact_regular_day_has_zero_variation_and_no_tolerance_flag(self) -> None:
        doc = _base_doc()
        res = apurar(doc).to_dict()
        dia = res["colaboradores"][0]["dias"][0]
        self.assertEqual(dia["minutos_previstos"], 480)
        self.assertEqual(dia["minutos_trabalhados"], 480)
        self.assertEqual(dia["minutos_regulares"], 480)
        self.assertEqual(dia["minutos_intervalo_intrajornada"], 60)
        self.assertEqual(dia["minutos_noturnos_reais"], 0)
        self.assertFalse(dia["tolerancia_aplicada"])
        self.assertEqual(dia["variacao_liquida_minutos"], 0)
        self.assertEqual(dia["saldo_banco_acumulado_minutos"], 50)
        self.assertEqual(dia["alertas"], [])

    def test_tolerance_boundary_exact_10_and_11_minutes(self) -> None:
        doc = _base_doc()
        doc["colaboradores"][0]["dias"] = [
            {
                "data": "2026-11-03",
                "tipo_dia": "normal",
                "batidas": [
                    {"entrada": "2026-11-03T08:00", "saida": "2026-11-03T12:00"},
                    {"entrada": "2026-11-03T13:00", "saida": "2026-11-03T16:50"},
                ],
                "observacao": "-10m na borda",
            },
            {
                "data": "2026-11-04",
                "tipo_dia": "normal",
                "batidas": [
                    {"entrada": "2026-11-04T08:00", "saida": "2026-11-04T12:00"},
                    {"entrada": "2026-11-04T13:00", "saida": "2026-11-04T17:10"},
                ],
                "observacao": "+10m na borda",
            },
            {
                "data": "2026-11-05",
                "tipo_dia": "normal",
                "batidas": [
                    {"entrada": "2026-11-05T08:00", "saida": "2026-11-05T12:00"},
                    {"entrada": "2026-11-05T13:00", "saida": "2026-11-05T16:49"},
                ],
                "observacao": "-11m fora da tolerancia",
            },
            {
                "data": "2026-11-06",
                "tipo_dia": "normal",
                "batidas": [
                    {"entrada": "2026-11-06T08:00", "saida": "2026-11-06T12:00"},
                    {"entrada": "2026-11-06T13:00", "saida": "2026-11-06T17:11"},
                ],
                "observacao": "+11m fora da tolerancia",
            },
        ]
        dias = apurar(doc).to_dict()["colaboradores"][0]["dias"]
        self.assertTrue(dias[0]["tolerancia_aplicada"])
        self.assertEqual(dias[0]["variacao_liquida_minutos"], 0)
        self.assertTrue(dias[1]["tolerancia_aplicada"])
        self.assertEqual(dias[1]["variacao_liquida_minutos"], 0)
        self.assertFalse(dias[2]["tolerancia_aplicada"])
        self.assertEqual(dias[2]["variacao_liquida_minutos"], -11)
        self.assertEqual(dias[2]["debito_minutos"], 11)
        self.assertFalse(dias[3]["tolerancia_aplicada"])
        self.assertEqual(dias[3]["variacao_liquida_minutos"], 11)
        self.assertEqual(dias[3]["credito_minutos"], 11)

    def test_zero_tolerance_rule_charges_1_minute_difference(self) -> None:
        doc = _base_doc()
        doc["regras"]["tolerancia_diaria_minutos"] = 0
        doc["colaboradores"][0]["dias"][0]["batidas"] = [
            {"entrada": "2026-11-03T08:00", "saida": "2026-11-03T12:00"},
            {"entrada": "2026-11-03T13:00", "saida": "2026-11-03T17:01"},
        ]
        dia = apurar(doc).to_dict()["colaboradores"][0]["dias"][0]
        self.assertFalse(dia["tolerancia_aplicada"])
        self.assertEqual(dia["variacao_liquida_minutos"], 1)

    def test_tolerance_does_not_absorb_work_when_expected_is_zero(self) -> None:
        doc = _base_doc()
        doc["colaboradores"][0]["dias"] = [
            {
                "data": "2026-11-02",
                "tipo_dia": "feriado",
                "batidas": [{"entrada": "2026-11-02T09:00", "saida": "2026-11-02T09:08"}],
                "observacao": "8 minutos em feriado devem gerar credito",
            }
        ]
        dia = apurar(doc).to_dict()["colaboradores"][0]["dias"][0]
        self.assertEqual(dia["minutos_previstos"], 0)
        self.assertFalse(dia["tolerancia_aplicada"])
        self.assertEqual(dia["variacao_liquida_minutos"], 8)
        self.assertEqual(dia["credito_minutos"], 8)

    def test_overnight_shift_crossing_midnight_and_night_window(self) -> None:
        doc = _base_doc()
        doc["colaboradores"][0]["dias"] = [
            {
                "data": "2026-11-03",
                "tipo_dia": "normal",
                "batidas": [
                    {"entrada": "2026-11-03T21:00", "saida": "2026-11-04T01:00"},
                    {"entrada": "2026-11-04T02:00", "saida": "2026-11-04T06:00"},
                ],
                "observacao": None,
            }
        ]
        result = apurar(doc)
        dia = result.to_dict()["colaboradores"][0]["dias"][0]
        self.assertEqual(dia["minutos_trabalhados"], 480)
        self.assertEqual(dia["minutos_intervalo_intrajornada"], 60)
        self.assertEqual(dia["minutos_noturnos_reais"], 360)
        self.assertEqual(dia["minutos_noturnos_equivalentes_racional"], "2880/7")
        self.assertEqual(result.night_equivalent_fraction("colab-01"), Fraction(2880, 7))
        self.assertEqual(result.night_equivalent_fraction(), Fraction(2880, 7))

    def test_early_morning_and_late_evening_night_windows_same_day(self) -> None:
        doc = _base_doc()
        doc["colaboradores"][0]["dias"] = [
            {
                "data": "2026-11-03",
                "tipo_dia": "normal",
                "batidas": [
                    {"entrada": "2026-11-03T04:00", "saida": "2026-11-03T08:00"},
                    {"entrada": "2026-11-03T20:00", "saida": "2026-11-03T23:30"},
                ],
                "observacao": "60m de manha (04-05) + 90m a noite (22-23:30)",
            }
        ]
        dia = apurar(doc).to_dict()["colaboradores"][0]["dias"][0]
        self.assertEqual(dia["minutos_noturnos_reais"], 150)
        self.assertEqual(dia["batidas"][0]["minutos_noturnos_reais"], 60)
        self.assertEqual(dia["batidas"][1]["minutos_noturnos_reais"], 90)

    def test_night_window_exact_boundary_touch_at_22_and_05(self) -> None:
        doc = _base_doc()
        doc["colaboradores"][0]["dias"] = [
            {
                "data": "2026-11-03",
                "tipo_dia": "normal",
                "batidas": [
                    {"entrada": "2026-11-03T05:00", "saida": "2026-11-03T12:00"},
                    {"entrada": "2026-11-03T13:00", "saida": "2026-11-03T22:00"},
                ],
                "observacao": "Toca 05:00 e 22:00 sem entrar na janela noturna",
            }
        ]
        dia = apurar(doc).to_dict()["colaboradores"][0]["dias"][0]
        self.assertEqual(dia["minutos_noturnos_reais"], 0)

    def test_same_day_non_midnight_night_window(self) -> None:
        doc = _base_doc()
        doc["regras"]["janela_noturna_inicio"] = "00:00"
        doc["regras"]["janela_noturna_fim"] = "05:00"
        doc["colaboradores"][0]["dias"] = [
            {
                "data": "2026-11-03",
                "tipo_dia": "normal",
                "batidas": [
                    {"entrada": "2026-11-03T22:00", "saida": "2026-11-04T03:00"}
                ],
                "observacao": None,
            }
        ]
        dia = apurar(doc).to_dict()["colaboradores"][0]["dias"][0]
        self.assertEqual(dia["minutos_noturnos_reais"], 180)

    def test_multiple_breaks_on_same_workday_sum_intrajornada(self) -> None:
        doc = _base_doc()
        doc["colaboradores"][0]["dias"][0]["batidas"] = [
            {"entrada": "2026-11-03T08:00", "saida": "2026-11-03T10:30"},
            {"entrada": "2026-11-03T10:50", "saida": "2026-11-03T13:30"},
            {"entrada": "2026-11-03T14:10", "saida": "2026-11-03T17:00"},
        ]
        dia = apurar(doc).to_dict()["colaboradores"][0]["dias"][0]
        self.assertEqual(dia["minutos_intervalo_intrajornada"], 60)
        self.assertEqual(dia["alertas"], [])

    def test_intrajornada_boundaries_240_241_360_361(self) -> None:
        doc = _base_doc()
        doc["colaboradores"][0]["dias"] = [
            {
                "data": "2026-11-02",
                "tipo_dia": "normal",
                "batidas": [{"entrada": "2026-11-02T08:00", "saida": "2026-11-02T12:00"}],
                "observacao": "240m sem pausa -> OK",
            },
            {
                "data": "2026-11-03",
                "tipo_dia": "normal",
                "batidas": [
                    {"entrada": "2026-11-03T08:00", "saida": "2026-11-03T10:00"},
                    {"entrada": "2026-11-03T10:14", "saida": "2026-11-03T12:15"},
                ],
                "observacao": "241m com 14m pausa -> ALERTA",
            },
            {
                "data": "2026-11-04",
                "tipo_dia": "normal",
                "batidas": [
                    {"entrada": "2026-11-04T08:00", "saida": "2026-11-04T11:00"},
                    {"entrada": "2026-11-04T11:15", "saida": "2026-11-04T14:15"},
                ],
                "observacao": "360m com 15m pausa -> OK",
            },
            {
                "data": "2026-11-05",
                "tipo_dia": "normal",
                "batidas": [
                    {"entrada": "2026-11-05T08:00", "saida": "2026-11-05T11:00"},
                    {"entrada": "2026-11-05T11:59", "saida": "2026-11-05T15:00"},
                ],
                "observacao": "361m com 59m pausa -> ALERTA",
            },
            {
                "data": "2026-11-06",
                "tipo_dia": "normal",
                "batidas": [
                    {"entrada": "2026-11-06T08:00", "saida": "2026-11-06T11:00"},
                    {"entrada": "2026-11-06T12:00", "saida": "2026-11-06T15:01"},
                ],
                "observacao": "361m com 60m pausa -> OK",
            },
        ]
        dias = apurar(doc).to_dict()["colaboradores"][0]["dias"]
        self.assertEqual(dias[0]["alertas"], [])
        self.assertEqual(dias[1]["alertas"], ["INTRAJORNADA_INSUFICIENTE"])
        self.assertEqual(dias[2]["alertas"], [])
        self.assertEqual(dias[3]["alertas"], ["INTRAJORNADA_INSUFICIENTE"])
        self.assertEqual(dias[4]["alertas"], [])

    def test_interjornada_boundary_659_vs_660_minutes(self) -> None:
        doc = _base_doc()
        doc["colaboradores"][0]["dias"] = [
            {
                "data": "2026-11-03",
                "tipo_dia": "normal",
                "batidas": [{"entrada": "2026-11-03T18:00", "saida": "2026-11-03T22:00"}],
                "observacao": None,
            },
            {
                "data": "2026-11-04",
                "tipo_dia": "normal",
                "batidas": [{"entrada": "2026-11-04T08:59", "saida": "2026-11-04T12:59"}],
                "observacao": "659m de descanso -> ALERTA",
            },
            {
                "data": "2026-11-05",
                "tipo_dia": "normal",
                "batidas": [{"entrada": "2026-11-05T00:00", "saida": "2026-11-05T04:00"}],
                "observacao": "661m de descanso -> OK",
            },
        ]
        dias = apurar(doc).to_dict()["colaboradores"][0]["dias"]
        self.assertIsNone(dias[0]["descanso_interjornada_minutos"])
        self.assertEqual(dias[1]["descanso_interjornada_minutos"], 659)
        self.assertEqual(dias[1]["alertas"], ["INTERJORNADA_INSUFICIENTE"])
        self.assertEqual(dias[2]["descanso_interjornada_minutos"], 661)
        self.assertEqual(dias[2]["alertas"], [])

    def test_both_intra_and_interjornada_alerts_on_same_day(self) -> None:
        doc = _base_doc()
        doc["colaboradores"][0]["dias"] = [
            {
                "data": "2026-11-03",
                "tipo_dia": "normal",
                "batidas": [{"entrada": "2026-11-03T18:00", "saida": "2026-11-03T23:00"}],
                "observacao": None,
            },
            {
                "data": "2026-11-04",
                "tipo_dia": "normal",
                "batidas": [
                    {"entrada": "2026-11-04T08:00", "saida": "2026-11-04T12:00"},
                    {"entrada": "2026-11-04T12:30", "saida": "2026-11-04T16:30"},
                ],
                "observacao": "Descanso 540m (<660) e pausa 30m (<60)",
            },
        ]
        dia = apurar(doc).to_dict()["colaboradores"][0]["dias"][1]
        self.assertEqual(dia["alertas"], ["INTRAJORNADA_INSUFICIENTE", "INTERJORNADA_INSUFICIENTE"])

    def test_folga_compensada_and_ausencia_justificada_balance(self) -> None:
        doc = _base_doc()
        doc["colaboradores"][0]["saldo_anterior_minutos"] = 120
        doc["colaboradores"][0]["dias"] = [
            {
                "data": "2026-11-03",
                "tipo_dia": "folga_compensada",
                "batidas": [],
                "observacao": "Debita 480m",
            },
            {
                "data": "2026-11-04",
                "tipo_dia": "ausencia_justificada",
                "batidas": [],
                "observacao": "Abona 480m (previsto vira 0)",
            },
        ]
        resumo = apurar(doc).to_dict()["colaboradores"][0]["resumo"]
        self.assertEqual(resumo["saldo_anterior_minutos"], 120)
        self.assertEqual(resumo["debito_minutos_total"], 480)
        self.assertEqual(resumo["credito_minutos_total"], 0)
        self.assertEqual(resumo["saldo_banco_minutos"], -360)

    def test_multi_worker_bank_conservation_invariants(self) -> None:
        doc = _base_doc()
        second = {
            "id": "colab-02",
            "apelido": "Operador 2",
            "funcao": "Analista",
            "saldo_anterior_minutos": -80,
            "jornada_prevista_por_dia_semana": {
                "1": 360,
                "2": 360,
                "3": 360,
                "4": 360,
                "5": 360,
                "6": 0,
                "7": 0,
            },
            "dias": [
                {
                    "data": "2026-11-03",
                    "tipo_dia": "normal",
                    "batidas": [
                        {"entrada": "2026-11-03T08:00", "saida": "2026-11-03T11:00"},
                        {"entrada": "2026-11-03T12:00", "saida": "2026-11-03T16:00"},
                    ],
                    "observacao": "+60m de credito",
                }
            ],
        }
        doc["colaboradores"].append(second)
        res = apurar(doc).to_dict()
        geral = res["resumo_geral"]
        self.assertEqual(geral["quantidade_colaboradores"], 2)
        self.assertEqual(geral["saldo_anterior_minutos_total"], -30)
        self.assertEqual(geral["credito_minutos_total"], 60)
        self.assertEqual(geral["debito_minutos_total"], 0)
        self.assertEqual(geral["variacao_liquida_minutos_total"], 60)
        self.assertEqual(geral["saldo_banco_minutos_total"], 30)

    def test_immutability_of_inputs_and_result(self) -> None:
        doc = _base_doc()
        result = apurar(doc)
        doc["regras"]["tolerancia_diaria_minutos"] = 999
        doc["colaboradores"][0]["id"] = "mutated"
        first = result.to_dict()
        first["colaboradores"][0]["id"] = "changed"
        first["resumo_geral"]["saldo_banco_minutos_total"] = 999999
        second = result.to_dict()
        self.assertEqual(second["colaboradores"][0]["id"], "colab-01")
        self.assertEqual(second["resumo_geral"]["saldo_banco_minutos_total"], 50)
        src_copy = result.source_document()
        src_copy["periodo"]["id"] = "tampered"
        self.assertEqual(result.source_document()["periodo"]["id"], "p-2026-11")


class TestCoreValidationRejections(unittest.TestCase):
    def test_reject_same_day_overlapping_batidas(self) -> None:
        doc = _base_doc()
        doc["colaboradores"][0]["dias"][0]["batidas"] = [
            {"entrada": "2026-11-03T08:00", "saida": "2026-11-03T12:30"},
            {"entrada": "2026-11-03T12:00", "saida": "2026-11-03T17:00"},
        ]
        with self.assertRaises(TimesheetError) as ctx:
            apurar(doc)
        self.assertEqual(ctx.exception.code, "INPUT_BATIDA_OVERLAP")

    def test_reject_same_day_touching_batidas_without_break(self) -> None:
        doc = _base_doc()
        doc["colaboradores"][0]["dias"][0]["batidas"] = [
            {"entrada": "2026-11-03T08:00", "saida": "2026-11-03T12:00"},
            {"entrada": "2026-11-03T12:00", "saida": "2026-11-03T16:00"},
        ]
        with self.assertRaises(TimesheetError) as ctx:
            apurar(doc)
        self.assertEqual(ctx.exception.code, "INPUT_BATIDA_OVERLAP")

    def test_reject_cross_day_overlapping_batidas(self) -> None:
        doc = _base_doc()
        doc["colaboradores"][0]["dias"] = [
            {
                "data": "2026-11-03",
                "tipo_dia": "normal",
                "batidas": [{"entrada": "2026-11-03T22:00", "saida": "2026-11-04T06:00"}],
                "observacao": None,
            },
            {
                "data": "2026-11-04",
                "tipo_dia": "normal",
                "batidas": [{"entrada": "2026-11-04T05:30", "saida": "2026-11-04T12:00"}],
                "observacao": None,
            },
        ]
        with self.assertRaises(TimesheetError) as ctx:
            apurar(doc)
        self.assertEqual(ctx.exception.code, "INPUT_BATIDA_OVERLAP")

    def test_reject_inverted_or_zero_length_batida(self) -> None:
        for saida in ("2026-11-03T08:00", "2026-11-03T07:59"):
            doc = _base_doc()
            doc["colaboradores"][0]["dias"][0]["batidas"] = [
                {"entrada": "2026-11-03T08:00", "saida": saida}
            ]
            with self.assertRaises(TimesheetError) as ctx:
                apurar(doc)
            self.assertEqual(ctx.exception.code, "INPUT_BATIDA_INTERVAL")

    def test_reject_first_entry_on_wrong_date_or_saida_two_days_later(self) -> None:
        doc = _base_doc()
        doc["colaboradores"][0]["dias"][0]["batidas"] = [
            {"entrada": "2026-11-04T00:10", "saida": "2026-11-04T04:00"}
        ]
        with self.assertRaises(TimesheetError) as ctx:
            apurar(doc)
        self.assertEqual(ctx.exception.code, "INPUT_BATIDA_DATE")

        doc2 = _base_doc()
        doc2["colaboradores"][0]["dias"][0]["batidas"] = [
            {"entrada": "2026-11-03T22:00", "saida": "2026-11-05T01:00"}
        ]
        with self.assertRaises(TimesheetError) as ctx2:
            apurar(doc2)
        self.assertEqual(ctx2.exception.code, "INPUT_BATIDA_DATE")

    def test_reject_second_entry_before_workday_date(self) -> None:
        doc = _base_doc()
        doc["colaboradores"][0]["dias"][0]["batidas"] = [
            {"entrada": "2026-11-03T08:00", "saida": "2026-11-03T10:00"},
            {"entrada": "2026-11-02T23:00", "saida": "2026-11-03T12:00"},
        ]
        with self.assertRaises(TimesheetError) as ctx:
            apurar(doc)
        self.assertEqual(ctx.exception.code, "INPUT_BATIDA_DATE")

    def test_reject_duplicate_or_unsorted_dates(self) -> None:
        for second_date in ("2026-11-03", "2026-11-02"):
            doc = _base_doc()
            doc["colaboradores"][0]["dias"].append(
                {
                    "data": second_date,
                    "tipo_dia": "normal",
                    "batidas": [],
                    "observacao": None,
                }
            )
            with self.assertRaises(TimesheetError) as ctx:
                apurar(doc)
            self.assertEqual(ctx.exception.code, "INPUT_DATE_ORDER")

    def test_reject_date_outside_periodo(self) -> None:
        doc = _base_doc()
        doc["colaboradores"][0]["dias"][0]["data"] = "2026-11-09"
        with self.assertRaises(TimesheetError) as ctx:
            apurar(doc)
        self.assertEqual(ctx.exception.code, "INPUT_DATE")

    def test_reject_inverted_or_excessive_periodo(self) -> None:
        doc = _base_doc()
        doc["periodo"]["data_inicio"] = "2026-11-10"
        doc["periodo"]["data_fim"] = "2026-11-02"
        with self.assertRaises(TimesheetError) as ctx:
            apurar(doc)
        self.assertEqual(ctx.exception.code, "INPUT_DATE")

        doc2 = _base_doc()
        doc2["periodo"]["data_inicio"] = "2025-01-01"
        doc2["periodo"]["data_fim"] = "2026-11-08"
        with self.assertRaises(TimesheetError) as ctx2:
            apurar(doc2)
        self.assertEqual(ctx2.exception.code, "INPUT_LIMIT")

    def test_reject_duplicate_worker_id(self) -> None:
        doc = _base_doc()
        doc["colaboradores"].append(_base_doc()["colaboradores"][0])
        with self.assertRaises(TimesheetError) as ctx:
            apurar(doc)
        self.assertEqual(ctx.exception.code, "INPUT_ID")

    def test_reject_invalid_ascii_id(self) -> None:
        for bad_id in ("", "colab com espaco", "-invalido", "colab/01"):
            doc = _base_doc()
            doc["colaboradores"][0]["id"] = bad_id
            with self.assertRaises(TimesheetError) as ctx:
                apurar(doc)
            self.assertEqual(ctx.exception.code, "INPUT_ID")

    def test_reject_unknown_fields_at_any_level(self) -> None:
        for mutator in (
            lambda d: d.update(extra=1),
            lambda d: d["periodo"].update(extra=1),
            lambda d: d["regras"].update(extra=1),
            lambda d: d["colaboradores"][0].update(extra=1),
            lambda d: d["colaboradores"][0]["dias"][0].update(extra=1),
            lambda d: d["colaboradores"][0]["dias"][0]["batidas"][0].update(extra=1),
        ):
            doc = _base_doc()
            mutator(doc)
            with self.assertRaises(TimesheetError) as ctx:
                apurar(doc)
            self.assertEqual(ctx.exception.code, "INPUT_SCHEMA")

    def test_reject_boolean_as_int(self) -> None:
        doc = _base_doc()
        doc["regras"]["tolerancia_diaria_minutos"] = True
        with self.assertRaises(TimesheetError) as ctx:
            apurar(doc)
        self.assertEqual(ctx.exception.code, "INPUT_SCHEMA")

    def test_reject_invalid_timestamps_and_times(self) -> None:
        doc = _base_doc()
        doc["colaboradores"][0]["dias"][0]["batidas"][0]["entrada"] = "2026-11-03T08:00:00"
        with self.assertRaises(TimesheetError) as ctx:
            apurar(doc)
        self.assertEqual(ctx.exception.code, "INPUT_TIMESTAMP")

        doc2 = _base_doc()
        doc2["regras"]["janela_noturna_inicio"] = "24:00"
        with self.assertRaises(TimesheetError) as ctx2:
            apurar(doc2)
        self.assertEqual(ctx2.exception.code, "INPUT_TIME")

    def test_reject_identical_night_window_start_and_end(self) -> None:
        doc = _base_doc()
        doc["regras"]["janela_noturna_inicio"] = "22:00"
        doc["regras"]["janela_noturna_fim"] = "22:00"
        with self.assertRaises(TimesheetError) as ctx:
            apurar(doc)
        self.assertEqual(ctx.exception.code, "INPUT_TIME")

    def test_reject_inconsistent_intrajornada_thresholds(self) -> None:
        doc = _base_doc()
        doc["regras"]["intrajornada_minima_4h_a_6h_minutos"] = 90
        doc["regras"]["intrajornada_minima_acima_6h_minutos"] = 60
        with self.assertRaises(TimesheetError) as ctx:
            apurar(doc)
        self.assertEqual(ctx.exception.code, "INPUT_SCHEMA")

    def test_reject_invalid_tipo_dia_and_control_chars_in_text(self) -> None:
        doc = _base_doc()
        doc["colaboradores"][0]["dias"][0]["tipo_dia"] = "inexistente"
        with self.assertRaises(TimesheetError) as ctx:
            apurar(doc)
        self.assertEqual(ctx.exception.code, "INPUT_SCHEMA")

        doc2 = _base_doc()
        doc2["periodo"]["titulo"] = "Titulo com\nquebra"
        with self.assertRaises(TimesheetError) as ctx2:
            apurar(doc2)
        self.assertEqual(ctx2.exception.code, "INPUT_SCHEMA")

    def test_reject_unknown_colaboradora_in_night_fraction(self) -> None:
        res = apurar(_base_doc())
        with self.assertRaises(TimesheetError) as ctx:
            res.night_equivalent_fraction("inexistente")
        self.assertEqual(ctx.exception.code, "INPUT_ID")


if __name__ == "__main__":
    unittest.main()
