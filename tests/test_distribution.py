from __future__ import annotations

import ast
from pathlib import Path
import unittest

from scripts.check_distribution import ROOT, installed_origin_matches, runtime_files, safe_name, source_files


class TestDistributionAndCodeDiscipline(unittest.TestCase):
    def test_runtime_and_source_inventory_are_non_empty(self) -> None:
        rt = runtime_files(ROOT)
        src = source_files(ROOT)
        self.assertIn("folha_ponto_local/core.py", rt)
        self.assertIn("folha_ponto_local/data/exemplo.json", rt)
        self.assertIn("pyproject.toml", src)
        self.assertIn("docs/exemplo.png", src)

    def test_safe_name_rejects_local_and_traversal_paths(self) -> None:
        self.assertEqual(str(safe_name("folha_ponto_local/core.py")), "folha_ponto_local/core.py")
        for invalid in ("../secret.py", "/abs/path.py", "local/out.json", ".venv/bin/python", "a\\b.py"):
            with self.assertRaises(ValueError):
                safe_name(invalid)

    def test_installed_origin_matches_checks_subpath(self) -> None:
        self.assertTrue(installed_origin_matches(str(ROOT / "src" / "folha_ponto_local"), ROOT))
        self.assertFalse(installed_origin_matches("/tmp", ROOT))

    def test_zero_float_usage_across_package_source(self) -> None:
        pkg_dir = ROOT / "src" / "folha_ponto_local"
        for py_file in sorted(pkg_dir.rglob("*.py")):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, float):
                    self.fail(f"Literal float encontrado em {py_file}: {node.value}")
                if isinstance(node, ast.Name) and node.id == "float":
                    self.fail(f"Uso de float() encontrado em {py_file}")

    def test_zero_float_usage_in_independent_verifier(self) -> None:
        verifier = ROOT / "tools" / "verify_independent.py"
        tree = ast.parse(verifier.read_text(encoding="utf-8"), filename=str(verifier))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                self.fail(f"Literal float encontrado em {verifier}: {node.value}")


if __name__ == "__main__":
    unittest.main()
