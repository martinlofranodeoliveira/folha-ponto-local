# Verificação Independente e Interoperabilidade

O verificador `tools/verify_independent.py` audita pacotes ZIP gerados pela CLI sem importar `folha_ponto_local`, confrontando os resultados com dois oráculos independentes da biblioteca padrão do Python (`set` discreto minuto a minuto e `sqlite3`).

## 1. Oráculo Discreto Minuto a Minuto

Para cada batida `{entrada, saida}` de `entrada.json`, o verificador constrói explicitamente o conjunto discreto de minutos inteiros:

```python
interval_set = set(range(entrada_min, saida_min))
```

Com esses conjuntos discretos, o verificador comprova que:
- Não há interseção entre intervalos do mesmo dia nem entre dias distintos do mesmo colaborador (`day_minutes.isdisjoint(interval_set)`).
- A cardinalidade `len(day_minutes)` coincide exatamente com `minutos_trabalhados` em `apuracao.json`.
- O subconjunto de minutos cujo minuto do dia (`m % 1440`) pertence ao conjunto discreto da janela noturna coincide exatamente com `minutos_noturnos_reais` em cada batida e no consolidado do dia.
- A equivalência racional `Fraction(noturno * 8, 7)` coincide com `minutos_noturnos_equivalentes_racional`.
- As regras de tolerância diária, intrajornada (`240` e `360` minutos) e interjornada produzem exatamente os mesmos valores de variação líquida, créditos, débitos, saldos acumulados e alertas.

## 2. Oráculo Relacional SQLite (`sqlite3`)

O verificador carrega `espelho-diario.csv` em uma tabela relacional em memória no SQLite (`:memory:`), executa `PRAGMA integrity_check` e valida os resumos por colaborador e o saldo acumulado diário usando agregações SQL (`SUM`, `COUNT`, `CASE WHEN`) e window functions (`SUM(variacao_liquida_minutos) OVER (PARTITION BY colaborador_id ORDER BY seq)`), confrontando o resultado com `resumo-colaboradores.csv` e `apuracao.json`.

## 3. Reproduzir a verificação (inclusive sem rede)

Contra um pacote ZIP existente:

```sh
python tools/verify_independent.py dist/exemplo-folha-ponto.zip --demo
```

Contra o executável CLI instalado, exercitando tanto o cenário de demonstração quanto o cenário sintético de fronteiras exatas (`240`/`241`/`360`/`361` min de intrajornada, `±10`/`±11` min de tolerância, `659`/`660` min de interjornada e neutralização de fórmulas CSV):

```sh
python tools/verify_independent.py --cli "$(command -v folha-ponto-local)" --report local/verificacao.json
```

Em Linux com suporte a namespaces de usuário e rede isolada (`unshare`):

```sh
unshare --user --map-root-user --net -- python tools/verify_independent.py \
  --require-network-isolation \
  --cli "$(command -v folha-ponto-local)" \
  --report local/verificacao-sem-rede.json
```
