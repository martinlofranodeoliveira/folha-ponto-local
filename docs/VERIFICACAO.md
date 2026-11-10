# Verificação e Distribuição

Os testes locais, o verificador independente e o pipeline de integração contínua (CI) validam o código-fonte, o wheel e o sdist de `folha-ponto-local`.

## Executar a suíte de testes e empacotamento

```sh
python -m pip install . -r requirements-build.txt
python -m unittest discover -s tests -v
python -m build
python scripts/check_distribution.py --dist dist
```

`scripts/check_distribution.py` inspeciona o wheel e o sdist contra uma lista fechada de arquivos permitidos, verifica que os bytes dos arquivos de runtime coincidem com `src/folha_ponto_local`, confere a licença MIT, o entry point `folha-ponto-local`, a ausência de dependências em `Requires-Dist` e todos os hashes SHA-256 e tamanhos do arquivo `RECORD`.

## Testar instalação isolada fora da árvore de fontes

```sh
python scripts/check_distribution.py --install dist/folha_ponto_local-1.0.0-py3-none-any.whl
python scripts/check_distribution.py --install dist/folha_ponto_local-1.0.0.tar.gz
```

Cada comando cria um ambiente virtual temporário fora do repositório, instala o artefato sem consultar a árvore original, confirma a origem do módulo importado, gera `exemplo-folha-ponto.zip` pela CLI instalada, executa `tools/verify_independent.py --demo` e comprova que uma segunda execução para o mesmo caminho recusa a sobrescrita com código de saída `2` preservando os bytes originais.

## Preparar os artefatos de release

Com a árvore Git limpa e os artefatos de build em `dist/`:

```sh
python scripts/prepare_release.py --dist dist
```

O script verifica o commit `HEAD`, confirma a igualdade entre o código importado e `src/folha_ponto_local`, valida o wheel e o sdist, gera `dist/exemplo-folha-ponto.zip`, copia `dist/exemplo.png`, executa o verificador independente no modo `--demo` e escreve `dist/SHA256SUMS`.
