from __future__ import annotations

import csv
import io
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

from folha_ponto_local import TimesheetError, apurar, apurar_json, build_report, write_report
from folha_ponto_local.jsoncodec import dumps, dumps_output, int_text, loads
from folha_ponto_local.reports import _safe_cell
from tests.test_core import _base_doc


class TestJsonCodec(unittest.TestCase):
    def test_reject_bom(self) -> None:
        with self.assertRaises(TimesheetError) as ctx:
            loads('\ufeff{"a": 1}')
        self.assertEqual(ctx.exception.code, "JSON_BOM")

    def test_reject_floats_and_exponents(self) -> None:
        for payload in ('{"a": 1.0}', '{"a": 0.5}', '{"a": 1e3}', '{"a": -2.5}'):
            with self.assertRaises(TimesheetError) as ctx:
                loads(payload)
            self.assertEqual(ctx.exception.code, "JSON_FLOAT")

    def test_reject_non_finite_constants(self) -> None:
        for payload in ('{"a": NaN}', '{"a": Infinity}', '{"a": -Infinity}'):
            with self.assertRaises(TimesheetError) as ctx:
                loads(payload)
            self.assertEqual(ctx.exception.code, "JSON_INVALID")

    def test_reject_duplicate_keys(self) -> None:
        with self.assertRaises(TimesheetError) as ctx:
            loads('{"a": 1, "a": 2}')
        self.assertEqual(ctx.exception.code, "JSON_DUPLICATE_KEY")

    def test_reject_excessive_depth(self) -> None:
        deep = "[" * 17 + "]" * 17
        with self.assertRaises(TimesheetError) as ctx:
            loads(deep)
        self.assertEqual(ctx.exception.code, "JSON_LIMIT")

    def test_reject_oversized_integer_token(self) -> None:
        huge = '{"a": ' + ("9" * 25) + "}"
        with self.assertRaises(TimesheetError) as ctx:
            loads(huge)
        self.assertEqual(ctx.exception.code, "JSON_LIMIT")

    def test_reject_invalid_utf8_and_surrogates(self) -> None:
        with self.assertRaises(TimesheetError) as ctx:
            loads(b"\xff\xfe")
        self.assertEqual(ctx.exception.code, "JSON_INVALID")
        with self.assertRaises(TimesheetError) as ctx2:
            dumps({"a": "\ud800"})
        self.assertEqual(ctx2.exception.code, "JSON_INVALID")

    def test_reject_float_and_bool_in_int_text_and_dumps(self) -> None:
        with self.assertRaises(TimesheetError) as ctx:
            dumps({"a": 1.5})
        self.assertEqual(ctx.exception.code, "JSON_INVALID")
        with self.assertRaises(TimesheetError) as ctx2:
            int_text(True)  # type: ignore[arg-type]
        self.assertEqual(ctx2.exception.code, "JSON_INVALID")

    def test_reject_non_bytes_or_str_in_loads(self) -> None:
        with self.assertRaises(TimesheetError) as ctx:
            loads(123)  # type: ignore[arg-type]
        self.assertEqual(ctx.exception.code, "JSON_INVALID")


class TestReportsAndBundle(unittest.TestCase):
    def test_build_report_contains_eight_deterministic_files(self) -> None:
        doc = _base_doc()
        raw = dumps(doc).encode("utf-8")
        res = apurar_json(raw)
        first = build_report(res, raw)
        second = build_report(res, raw)
        self.assertEqual(first, second)
        with zipfile.ZipFile(io.BytesIO(first)) as zf:
            self.assertEqual(
                tuple(zf.namelist()),
                (
                    "LEIA-ME.md",
                    "RELATORIO.md",
                    "SHA256SUMS",
                    "apuracao.json",
                    "entrada.json",
                    "espelho-diario.csv",
                    "mapa-jornada.svg",
                    "resumo-colaboradores.csv",
                ),
            )
            self.assertEqual(zf.read("entrada.json"), raw)

    def test_csv_formula_injection_protection(self) -> None:
        doc = _base_doc()
        doc["colaboradores"][0]["apelido"] = "=SUM(1,2)"
        doc["colaboradores"][0]["funcao"] = "+CMD|'/C calc'!A0"
        doc["colaboradores"][0]["dias"][0]["observacao"] = "@alerta_formula"
        raw = dumps(doc).encode("utf-8")
        bundle = build_report(apurar(doc), raw)
        with zipfile.ZipFile(io.BytesIO(bundle)) as zf:
            espelho = list(csv.DictReader(io.StringIO(zf.read("espelho-diario.csv").decode("utf-8"))))
            resumo = list(csv.DictReader(io.StringIO(zf.read("resumo-colaboradores.csv").decode("utf-8"))))
        self.assertEqual(espelho[0]["apelido"], "'=SUM(1,2)")
        self.assertEqual(espelho[0]["funcao"], "'+CMD|'/C calc'!A0")
        self.assertEqual(espelho[0]["observacao"], "'@alerta_formula")
        self.assertEqual(resumo[0]["apelido"], "'=SUM(1,2)")
        self.assertEqual(_safe_cell("  -100"), "'  -100")
        self.assertEqual(_safe_cell("\t=1"), "'\t=1")

    def test_svg_escapes_special_characters_and_xml_nonchars(self) -> None:
        doc = _base_doc()
        doc["periodo"]["titulo"] = "Periodo <Teste> & \"Aspas\" \ufffe"
        doc["colaboradores"][0]["apelido"] = "Equipe <A&B> \uffff"
        raw = dumps_output(doc).encode("utf-8")
        bundle = build_report(apurar(doc), raw)
        with zipfile.ZipFile(io.BytesIO(bundle)) as zf:
            svg_bytes = zf.read("mapa-jornada.svg")
        root = ET.fromstring(svg_bytes)
        self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")

    def test_svg_handles_more_than_four_workers_preserving_all_metadata_nodes(self) -> None:
        doc = _base_doc()
        base_worker = doc["colaboradores"][0]
        doc["colaboradores"] = []
        for idx in range(1, 6):
            w = dict(base_worker)
            w["id"] = f"colab-0{idx}"
            w["apelido"] = f"Colaborador {idx}"
            doc["colaboradores"].append(w)
        raw = dumps(doc).encode("utf-8")
        bundle = build_report(apurar(doc), raw)
        with zipfile.ZipFile(io.BytesIO(bundle)) as zf:
            root = ET.fromstring(zf.read("mapa-jornada.svg"))
        ids = [n.get("data-colaborador-id") for n in root.iter() if n.get("class") == "colaborador"]
        self.assertEqual(ids, ["colab-01", "colab-02", "colab-03", "colab-04", "colab-05"])

    def test_reject_mismatched_source_bytes_in_build_report(self) -> None:
        doc = _base_doc()
        res = apurar(doc)
        other = _base_doc()
        other["regras"]["saldo_anterior_minutos"] = 0
        with self.assertRaises(TimesheetError) as ctx:
            build_report(res, dumps(other).encode("utf-8"))
        self.assertEqual(ctx.exception.code, "REPORT_SOURCE")
        with self.assertRaises(TimesheetError) as ctx2:
            build_report(res, "nao_bytes")  # type: ignore[arg-type]
        self.assertEqual(ctx2.exception.code, "REPORT_SOURCE")

    def test_write_report_atomic_and_refuses_existing_target(self) -> None:
        doc = _base_doc()
        raw = dumps(doc).encode("utf-8")
        res = apurar(doc)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "saida.zip"
            write_report(target, res, raw)
            original = target.read_bytes()
            with self.assertRaises(TimesheetError) as ctx:
                write_report(target, res, raw)
            self.assertEqual(ctx.exception.code, "OUTPUT_EXISTS")
            self.assertEqual(target.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
