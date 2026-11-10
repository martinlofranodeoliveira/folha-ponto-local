# Formato de Entrada e Pacote de Saída

O **Folha de Ponto Local** (`1.0.0`) processa um documento JSON (`schema_version: 1`) e publica um pacote ZIP determinístico com oito arquivos.

## 1. Regras do Codec JSON Estrito

- Codificação **UTF-8 estrita sem BOM** (`\ufeff` é recusado com `JSON_BOM`).
- **Proibição de `float` e constantes não finitas**: qualquer número com ponto decimal, notação exponencial, `NaN`, `Infinity` ou `-Infinity` é recusado imediatamente (`JSON_FLOAT` / `JSON_INVALID`).
- **Proibição de chaves duplicadas**: objetos com chaves repetidas são recusados com `JSON_DUPLICATE_KEY`.
- **Proibição de `bool` como inteiro**: `true` e `false` não são aceitos em campos numéricos.
- **Esquema fechado**: qualquer chave desconhecida ou ausente em qualquer nível recusa o documento com `INPUT_SCHEMA`.
- **Limites de leitura**: até `8.388.608` bytes (8 MiB), profundidade máxima `16` e até `200.000` nós JSON na entrada (`64 MiB` e `1.000.000` nós nos documentos derivados).

## 2. Esquema de Entrada (`schema_version: 1`)

```json
{
  "schema_version": 1,
  "periodo": {
    "id": "quinzena-demo-2026-11",
    "titulo": "Quinzena Sintetica de Demonstracao (02/11/2026 a 15/11/2026)",
    "data_inicio": "2026-11-02",
    "data_fim": "2026-11-15"
  },
  "regras": {
    "tolerancia_diaria_minutos": 10,
    "interjornada_minima_minutos": 660,
    "intrajornada_minima_acima_6h_minutos": 60,
    "intrajornada_minima_4h_a_6h_minutos": 15,
    "janela_noturna_inicio": "22:00",
    "janela_noturna_fim": "05:00",
    "saldo_anterior_minutos": 30
  },
  "colaboradores": [
    {
      "id": "colab-demo-01",
      "apelido": "Equipe Operacao Diurna",
      "funcao": "Analista de Operacoes",
      "jornada_prevista_por_dia_semana": {
        "1": 480,
        "2": 480,
        "3": 480,
        "4": 480,
        "5": 480,
        "6": 0,
        "7": 0
      },
      "dias": [
        {
          "data": "2026-11-03",
          "tipo_dia": "normal",
          "batidas": [
            {
              "entrada": "2026-11-03T08:03",
              "saida": "2026-11-03T12:00"
            },
            {
              "entrada": "2026-11-03T13:00",
              "saida": "2026-11-03T16:57"
            }
          ],
          "observacao": "Variacao de -6m absorvida pela tolerancia diaria"
        }
      ]
    }
  ]
}
```

### Limites e domínios dos campos

| Caminho | Tipo | Restrições |
| --- | --- | --- |
| `schema_version` | `int` | Exatamente `1` |
| `periodo.id` | `str` | Identificador ASCII `[A-Za-z0-9][A-Za-z0-9_.-]{0,79}` |
| `periodo.titulo` | `str` | 1 a 160 caracteres, sem controles ou espaços extremos |
| `periodo.data_inicio`, `data_fim` | `str` | Data ISO `YYYY-MM-DD` válida; `data_inicio <= data_fim`, amplitude máxima de 366 dias |
| `regras.tolerancia_diaria_minutos` | `int` | `0..60` |
| `regras.interjornada_minima_minutos` | `int` | `0..1440` |
| `regras.intrajornada_minima_acima_6h_minutos` | `int` | `0..240` |
| `regras.intrajornada_minima_4h_a_6h_minutos` | `int` | `0..120` (menor ou igual ao limite acima de 6h) |
| `regras.janela_noturna_inicio`, `fim` | `str` | `HH:MM` (`00:00..23:59`), valores distintos |
| `regras.saldo_anterior_minutos` | `int` | `-100000..100000` |
| `colaboradores` | `list` | 1 a 200 colaboradores com `id` ASCII único (sem dados pessoais reais) |
| `colaboradores[].jornada_prevista_por_dia_semana` | `dict` | Chaves `"1"` (segunda) a `"7"` (domingo), valores `0..1440` |
| `colaboradores[].saldo_anterior_minutos` | `int` (opcional) | `-100000..100000`; quando omitido, herda `regras.saldo_anterior_minutos` |
| `colaboradores[].dias` | `list` | 1 a 367 registros em ordem cronológica estrita dentro do período |
| `dias[].tipo_dia` | `str` | `"normal"`, `"feriado"`, `"folga_compensada"` ou `"ausencia_justificada"` |
| `dias[].batidas` | `list` | 0 a 16 pares `{entrada, saida}` no formato `YYYY-MM-DDTHH:MM` |
| `dias[].observacao` | `str \| null` | Até 240 caracteres sem controles, ou `null` |

### Regras de batidas e virada de meia-noite

- A primeira `entrada` de um dia deve ocorrer na própria `data` do registro (`00:00` a `23:59` de `data`).
- As marcações subsequentes e a `saida` podem avançar até `23:59` de `data + 1 dia`, permitindo turnos noturnos que cruzam a meia-noite.
- Em cada par, `entrada < saida`. Entre pares consecutivos do mesmo dia, exige-se `saida_anterior < entrada_seguinte` (intervalo intrajornada estritamente positivo).
- Entre dias consecutivos trabalhados pelo mesmo colaborador, exige-se `ultima_saida_dia_anterior <= primeira_entrada_dia_atual` (sobreposições entre jornadas são recusadas com `INPUT_BATIDA_OVERLAP`).

## 3. Conteúdo do Pacote ZIP

Os oito arquivos do pacote têm timestamp fixo `1980-01-01 00:00:00`, permissão `0644` e compressão `ZIP_STORED`.

1. `LEIA-ME.md`: instruções do pacote e escopo técnico.
2. `RELATORIO.md`: relatório em Markdown com tabelas de resumo e memória diária.
3. `SHA256SUMS`: lista `<sha256>  <arquivo>` dos outros sete membros do ZIP.
4. `apuracao.json`: documento estruturado com `periodo`, `regras`, `resumo_geral` e `colaboradores`.
5. `entrada.json`: cópia byte a byte do arquivo de entrada.
6. `espelho-diario.csv`: uma linha por dia registrado de cada colaborador.
7. `mapa-jornada.svg`: painel visual `1440×900` com barras diárias, selos de turno noturno/alerta e balanço do banco de horas.
8. `resumo-colaboradores.csv`: uma linha de consolidação por colaborador.

Nos arquivos CSV, qualquer célula textual cujo conteúdo (após espaços iniciais) comece com `=`, `+`, `-`, `@`, `\t` ou `\r` recebe um apóstrofo inicial (`'`) para evitar execução de fórmulas ao abrir em editores de planilhas. Campos numéricos inteiros permanecem sem apóstrofo.
