# API Python e Modelo Matemático

```python
from folha_ponto_local import apurar, apurar_json, build_report, write_report
from folha_ponto_local.jsoncodec import loads

documento = loads(json_bytes)
resultado = apurar(documento)
relatorio_dict = resultado.to_dict()
frac_noturna = resultado.night_equivalent_fraction("colab-demo-02")
```

## Funções públicas

- `apurar(document: object) -> TimesheetResult`: valida integralmente o dicionário de entrada (inclusive quando construído em memória) e retorna um `TimesheetResult` imutável.
- `apurar_json(data: bytes | str) -> TimesheetResult`: decodifica bytes ou texto UTF-8 pelo codec JSON estrito e invoca `apurar`.
- `build_report(result: TimesheetResult, input_bytes: bytes) -> bytes`: gera e verifica em memória os bytes do pacote ZIP determinístico, garantindo que `input_bytes` corresponda estruturalmente ao documento apurado.
- `write_report(path: str | os.PathLike, result: TimesheetResult, input_bytes: bytes) -> Path`: publica o pacote ZIP de forma atômica e exclusiva via `os.link`, recusando sobrescrever destinos existentes.

## Imutabilidade de `TimesheetResult`

- `result.to_dict()` devolve uma nova árvore de dicionários e listas a cada chamada.
- `result.source_document()` devolve uma nova cópia do documento de entrada validado.
- `result.night_equivalent_fraction(colaboradora_id=None)` devolve um `fractions.Fraction` exato representando os minutos noturnos multiplicados pelo fator de hora noturna reduzida `Fraction(8, 7)`.

## Fórmulas exatas por registro diário

Para cada colaborador e cada dia em `dias`:

1. **Minutos trabalhados (`minutos_trabalhados`)**:
   Soma das durações `(saida - entrada)` de cada par em `batidas`.
2. **Intervalo intrajornada (`minutos_intervalo_intrajornada`)**:
   Soma das pausas `(entrada_{j+1} - saida_j)` entre batidas consecutivas do mesmo dia (`0` quando há zero ou um par).
3. **Minutos noturnos reais (`minutos_noturnos_reais`)**:
   Soma da interseção exata de cada intervalo `[entrada, saida]` com as janelas noturnas `[janela_noturna_inicio, janela_noturna_fim]` que cobrem `data` e `data + 1`.
4. **Minutos previstos (`minutos_previstos`)**:
   `0` quando `tipo_dia` é `"feriado"` ou `"ausencia_justificada"`; caso contrário, `jornada_prevista_por_dia_semana[str(dia_semana_iso)]`.
5. **Tolerância e variação líquida (`variacao_liquida_minutos`)**:
   Seja `diferenca_bruta = minutos_trabalhados - minutos_previstos`.
   Se `minutos_previstos > 0` e `0 < abs(diferenca_bruta) <= tolerancia_diaria_minutos`, então `tolerancia_aplicada = True` e `variacao_liquida_minutos = 0`. Caso contrário, `tolerancia_aplicada = False` e `variacao_liquida_minutos = diferenca_bruta`.
6. **Crédito, débito e saldo acumulado**:
   - `credito_minutos = max(0, variacao_liquida_minutos)`
   - `debito_minutos = max(0, -variacao_liquida_minutos)`
   - `saldo_banco_acumulado_minutos = saldo_anterior_minutos + sum(variacao_liquida_minutos)` até o dia corrente.

## Códigos de erro (`TimesheetError`)

`TimesheetError` herda de `ValueError` e expõe `code`, `message` e `path`.

| Código | Condição |
| --- | --- |
| `JSON_INVALID` | Sintaxe JSON inválida, UTF-8 inválido ou constante não finita |
| `JSON_BOM` | Presença de UTF-8 BOM (`\ufeff`) |
| `JSON_FLOAT` | Número com ponto decimal ou expoente |
| `JSON_DUPLICATE_KEY` | Chave repetida no mesmo objeto JSON |
| `JSON_LIMIT` | Excesso de bytes, nós, profundidade ou dígitos inteiros |
| `INPUT_SCHEMA` | Campo obrigatório ausente, chave desconhecida ou tipo inválido |
| `INPUT_ID` | Identificador fora do padrão ASCII ou colaborador duplicado |
| `INPUT_LIMIT` | Quantidade ou valor inteiro fora da faixa permitida |
| `INPUT_DATE` | Data inválida ou fora do período |
| `INPUT_DATE_ORDER` | Datas diárias fora de ordem cronológica estrita ou repetidas |
| `INPUT_TIME` | Horário `HH:MM` inválido ou janela noturna degenerada |
| `INPUT_TIMESTAMP` | Carimbo `YYYY-MM-DDTHH:MM` inválido |
| `INPUT_BATIDA_DATE` | Batida fora da janela permitida (`data` e `data + 1`) |
| `INPUT_BATIDA_INTERVAL` | `saida <= entrada` |
| `INPUT_BATIDA_OVERLAP` | Batidas sobrepostas no mesmo dia ou entre dias consecutivos |
| `OUTPUT_EXISTS` | Arquivo ou symlink de destino já existe |
| `OUTPUT_PARENT` | Diretório pai da saída não existe |
