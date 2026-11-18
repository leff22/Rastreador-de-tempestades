# Especificação Curta - Tracking com GeoTIFF

## Objetivo

Construir um pipeline operacional para rastrear núcleos de chuva a partir de uma sequência cronológica de arquivos GeoTIFF, sem depender de dados brutos NEXRAD nem de modelo treinado.

Entrada:

- 1 arquivo GeoTIFF por instante
- intervalo de 10 minutos
- mesma grade e mesma projeção
- `-1 = nodata`
- `0..55 = intensidade`

## Decisões Fechadas

- Regra de detecção: limiar duplo
- Calibração inicial: equilibrada
- Filtro de ruído: área mínima + intensidade média
- Forma da detecção: polígono para análise + bounding box para tracker
- Fusão: manter múltiplos IDs por alguns frames antes de consolidar
- Divisão: fragmento principal herda o ID, demais recebem novos IDs
- Persistência sem detecção: 2 frames
- Associação temporal: IoU + distância entre centróides + tendência de movimento
- Estratégia de desenvolvimento: protótipo primeiro, endurecimento depois
- Ordem de desenvolvimento: segmentação -> tracking -> visualização
- Escopo V1: tracking + regras simples de fusão/divisão

## Arquitetura V1

1. Leitura do GeoTIFF

- abrir array e metadados geográficos
- preservar `-1` no raster original
- usar máscara lógica interna `valid_mask = array != -1` apenas para processamento

2. Segmentação

- aplicar limiar de entrada do núcleo
- aplicar limiar de manutenção do núcleo
- usar morfologia para reduzir ruído e unir fragmentos relevantes

3. Extração de objetos

- calcular polígono/contorno
- gerar `bbox`, centroide, área, pico e intensidade média

4. Tracking

- alimentar o `SortTracker` com as `bbox`
- complementar a associação com distância e direção
- manter trilha por até 2 frames sem detecção

5. Regras de evento

- fusão: manter IDs candidatos por janela curta antes de consolidar
- divisão: maior fragmento herda o ID anterior

6. Exportação

- salvar trilhas e estados por frame
- gerar visualização para validação operacional

## Estrutura de Desenvolvimento

### Fase 1 - Base

- leitor de GeoTIFF
- máscara lógica de validade sem alterar o valor `-1` original
- inspeção estatística por frame

### Fase 2 - Segmentação

- limiar duplo configurável
- área mínima configurável
- intensidade média mínima configurável
- limpeza morfológica

### Fase 3 - Tracking

- adaptação do pipeline para usar detecções vindas do TIFF
- integração com `src/tracker.py`
- persistência de trilhas

### Fase 4 - Fusão e Divisão

- regra simples de herança de ID
- janela curta para consolidação de fusão

### Fase 5 - Saída e Validação

- exportação estruturada
- imagem de inspeção com overlays
- validação com sequência real

## Dúvida do Item 9 - Saída e Aplicação no Sistema

O fato de a entrada ser TIFF não obriga a saída a ser TIFF.

Para o seu sistema, a saída mais útil será tripla:

### 1. Saída de dados

- `JSON` por frame com os núcleos detectados
- `JSON` consolidado com histórico por `track_id`
- `GeoJSON` por frame para desenhar no mapa

Campos esperados:

- `timestamp`
- `track_id`
- `bbox_px`
- `centroid_px`
- `centroid_lonlat`
- `area_px`
- `intensity_max`
- `intensity_mean`
- `status` (`new`, `active`, `merged`, `split`, `lost`)

### 2. Saída visual

- `PNG` por frame com overlay das trilhas
- `GIF` ou `MP4` apenas para inspeção e validação humana

### 3. Saída geográfica

- `GeoJSON` com polígonos ou centróides dos núcleos rastreados
- `GeoJSON` com trilhas por `track_id`
- pronto para consumo por frontend de mapa

## Recomendação para o V1

No V1, a melhor escolha é:

- saída principal: `JSON + GeoJSON`
- saída auxiliar: `PNG`

Assim:

- o sistema consome dado estruturado
- o mapa consegue desenhar camadas geográficas nativamente
- você consegue validar visualmente se o tracking está coerente
- o `GIF` fica opcional, só para auditoria rápida

## Valores Iniciais Recomendados

As sugestões abaixo foram derivadas da lógica do projeto original:

- limiar principal em `extract_labels.py`: `45.0`
- área mínima em `extract_labels.py`: `16 px`
- resolução aproximada descrita no projeto original: `~1.17 km/px`
- metadado original salvo por célula: `bbox_pixel`, `max_dbz`, `area_pixels`, `geo`

### 1. Limiar inicial de entrada

- `45`

Justificativa:

- é o valor já usado no projeto original para detectar núcleo convectivo severo
- mantém aderência direta à heurística já validada no repositório

### 2. Limiar inicial de manutenção

- `40`

Justificativa:

- o projeto original não tinha histerese, só um limiar único de `45`
- para tracking, um valor um pouco menor ajuda a manter continuidade temporal entre frames sem abrir demais o núcleo

### 3. Área mínima operacional

- `20 km²` como regra operacional inicial

Justificativa:

- o projeto original usa `16 px` com comentário de referência `< ~20 km²`
- como agora a grade do TIFF é diferente da grade `256x256` original, a recomendação correta é guardar essa regra em unidade física e converter para pixel na grade do TIFF em tempo de execução

### 4. Intensidade média mínima operacional

- `35`

Justificativa:

- não existe esse campo no projeto original, então esta é uma derivação operacional
- como o pico de entrada começa em `45`, usar média mínima em `35` tende a filtrar núcleos com pico isolado, mas ainda manter áreas organizadas

### 5. Formato inicial do JSON esperado pelo sistema

Sugestão baseada no `dataset_metadata.json` original, expandido para uso operacional:

```json
{
  "timestamp": "2026-10-01T16:30:00Z",
  "frame_name": "rainviewer_20261001_1630",
  "source": "rainviewer",
  "summary": {
    "total_cells": 3,
    "active_tracks": 3
  },
  "cells": [
    {
      "track_id": 12,
      "status": "active",
      "bbox_px": [120, 220, 260, 340],
      "centroid_px": [190, 280],
      "centroid_lonlat": {
        "lon": -46.63,
        "lat": -23.55
      },
      "area_px": 1840,
      "area_km2": 22.40,
      "intensity_max": 48,
      "intensity_mean": 35,
      "motion": {
        "vx_px_frame": 8.2,
        "vy_px_frame": -3.4
      }
    }
  ]
}
```

Justificativa:

- reaproveita a estrutura conceitual do metadata original: caixa, área, intensidade e geo
- adiciona `track_id`, `status` e movimento para atender o uso operacional
- mantém o payload simples para backend e frontend

## Tabela de Configuração V1

| Parâmetro                     | Valor inicial        | Unidade     | Uso no código                                   | Observação                                   |
| ------------------------------ | -------------------- | ----------- | ------------------------------------------------ | ---------------------------------------------- |
| `source_name`                | `rainviewer`       | texto       | Identificação da fonte nos arquivos de saída  | Vai em `JSON`, `GeoJSON` e nome de produto |
| `nodata_value`               | `-1`               | inteiro     | Comparação para gerar `valid_mask`           | Preservar no raster original                   |
| `input_interval_minutes`     | `10`               | minutos     | Ordenação temporal e cálculo de persistência | Base do ciclo operacional                      |
| `detection_threshold`        | `45`               | intensidade | Detectar nascimento de núcleo                   | Limiar na literatura                           |
| `maintenance_threshold`      | `40`               | intensidade | Manter núcleo ativo entre frames                | Limiar para continuidade                       |
| `min_area_km2`               | `20.0`             | km²        | Filtrar núcleos pequenos                        | Converter para pixel em runtime                |
| `min_mean_intensity`         | `35`               | intensidade | Filtrar núcleo fraco ou ruidoso                 | Apoia a área mínima                          |
| `morphology_closing_kernel`  | `3`                | pixels      | Fechamento morfológico                          | Herdado do projeto original                    |
| `tracker_max_age`            | `2`                | frames      | Persistência sem detecção                     | Equivale a até 20 min                         |
| `tracker_min_hits`           | `1`                | frames      | Confirmar trilha no início                      | V1 mais responsivo                             |
| `tracker_iou_threshold`      | `0.10`             | razão      | Associação geométrica no tracker              | Já usado em `inference_and_track.py`        |
| `centroid_distance_weight`   | `0.35`             | razão      | Peso da distância na associação               | Complementa o IoU                              |
| `motion_vector_weight`       | `0.25`             | razão      | Peso da tendência de movimento                  | Complementa o IoU                              |
| `merge_hold_frames`          | `2`                | frames      | Janela curta antes de consolidar fusão          | Implementa a regra escolhida                   |
| `split_inheritance_strategy` | `largest_fragment` | texto       | Regra de herança na divisão                    | Fragmento maior herda o ID                     |
| `export_png`                 | `true`             | booleano    | Gerar imagem final com overlay                   | Publicação visual                            |
| `export_json`                | `true`             | booleano    | Gerar saída estruturada                         | Integração do sistema                        |
| `export_geojson`             | `true`             | booleano    | Gerar camada geográfica                         | Uso no mapa interativo                         |
| `export_gif`                 | `false`            | booleano    | Gerar animação de auditoria                    | Opcional no V1                                 |
| `output_crs`                 | `EPSG:4326`        | CRS         | Padronizar saídas geográficas                  | Compatível com web map                        |

## Convenção Inicial de Nomes no Código

- `source_name`
- `nodata_value`
- `valid_mask`
- `input_interval_minutes`
- `detection_threshold`
- `maintenance_threshold`
- `min_area_km2`
- `min_mean_intensity`
- `morphology_closing_kernel`
- `tracker_max_age`
- `tracker_min_hits`
- `tracker_iou_threshold`
- `centroid_distance_weight`
- `motion_vector_weight`
- `merge_hold_frames`
- `split_inheritance_strategy`
- `export_png`
- `export_json`
- `export_geojson`
- `export_gif`
- `output_crs`
