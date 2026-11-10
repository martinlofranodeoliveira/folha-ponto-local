from __future__ import annotations

import io
import os
from pathlib import Path
import tempfile
import unittest
import zipfile

from folha_ponto_local.cli import main
from folha_ponto_local.jsoncodec import dumps
from tests.test_core import _base_doc


class TestCLI(unittest.TestCase):
    def test_cli_exemplo_creates_valid_zip_and_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "exemplo.zip"
            code = main(["exemplo", "--saida", str(out)])
            self.assertEqual(code, 0)
            first = out.read_bytes()
            self.assertTrue(zipfile.is_zipfile(io.BytesIO(first)))

            code2 = main(["exemplo", "--saida", str(out)])
            self.assertEqual(code2, 2)
            self.assertEqual(out.read_bytes(), first)

    def test_cli_apurar_custom_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "entrada.json"
            out = Path(tmp) / "resultado.zip"
            raw = dumps(_base_doc()).encode("utf-8")
            inp.write_bytes(raw)
            code = main(["apurar", str(inp), "--saida", str(out)])
            self.assertEqual(code, 0)
            with zipfile.ZipFile(out) as zf:
                self.assertEqual(zf.read("entrada.json"), raw)

    def test_cli_refuses_symlink_input_and_symlink_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            real_in = tmp_path / "real.json"
            real_in.write_bytes(dumps(_base_doc()).encode("utf-8"))
            sym_in = tmp_path / "link.json"
            os.symlink(real_in, sym_in)
            out = tmp_path / "out.zip"
            self.assertEqual(main(["apurar", str(sym_in), "--saida", str(out)]), 2)
            self.assertFalse(out.exists())

            dangling_out = tmp_path / "dangling.zip"
            os.symlink(tmp_path / "nonexistent.target", dangling_out)
            self.assertEqual(main(["exemplo", "--saida", str(dangling_out)]), 2)

    def test_cli_refuses_directory_as_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            out = tmp_path / "out.zip"
            self.assertEqual(main(["apurar", str(tmp_path), "--saida", str(out)]), 2)
            self.assertFalse(out.exists())

    def test_cli_refuses_nonexistent_input_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            out = tmp_path / "out.zip"
            self.assertEqual(main(["apurar", str(tmp_path / "ausente.json"), "--saida", str(out)]), 2)
            self.assertFalse(out.exists())

    def test_cli_refuses_invalid_json_without_creating_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bad = tmp_path / "bad.json"
            bad.write_text('{"schema_version": 1.5}', encoding="utf-8")
            out = tmp_path / "out.zip"
            self.assertEqual(main(["apurar", str(bad), "--saida", str(out)]), 2)
            self.assertFalse(out.exists())

    def test_cli_refuses_missing_output_parent_and_invalid_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing_out = Path(tmp) / "nao_existe" / "saida.zip"
            self.assertEqual(main(["exemplo", "--saida", str(missing_out)]), 2)
            self.assertEqual(main(["--flag-inexistente"]), 2)
            self.assertEqual(main([]), 2)


if __name__ == "__main__":
    unittest.main()
