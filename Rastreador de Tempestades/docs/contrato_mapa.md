# Contrato dos produtos de núcleos

`outputs/sequence_summary.json` lista observações ordenadas, configuração e caminhos relativos. Pastas: `YYYYMMDD_HHMM`. `processing_mode: cores`.

| Arquivo | Conteúdo |
|---|---|
| `radar.png` | Reflectividade transparente, sem anotações |
| `cells.geojson` | Polígonos observados ≥40 dBZ, `core_id`, `track_uid`, associação, linhagem e movimento |
| `support.geojson` | Áreas ≥32 dBZ com `detection_id` local; somente apoio de deslocamento |
| `trends.geojson` | Trapézios fixos, dimensões em km, eixo, `core_id` e `track_uid` |
| `tracks.geojson` | Trajetórias de centróides observados; reiniciadas em divisões/fusões |
| `metadata.json` | `cores`, `support_areas`, limiares, eventos, UIDs ausentes/encerrados |
| `manifest.json` | Nomes locais, camadas, CRS e limites exatos do PNG |

Geometrias em longitude/latitude EPSG:4326; polígonos podem ser Polygon ou MultiPolygon, com buracos. `manifest.bounds: [[sul,oeste],[norte,leste]]`. O raster deve ocupar esses limites.

`core_id` é um número visível reservado enquanto a trajetória estiver ativa, mesmo ausente. Pode ser reutilizado após encerramento. `track_uid` é único na sequência e nunca reutilizado; usar como chave de banco, linhagem e seleção ao avançar o tempo. `cell_id` e `support_detection_id` são locais à observação. A área de apoio não possui `system_id` nem trajetória própria.

`support_detection_id` pode ser nulo: o núcleo permanece válido sem área de apoio aceita. Cache versão 3 contém a detecção independente dos núcleos; versões antigas exigem novo processamento dos TIFFs.

`association.method`: `birth`, `overlap`, `support_translation` ou `history_translation`. A sobreposição é sempre dos **núcleos**, dividida pela menor área, em EPSG:6933. `previous_timestamp` aponta para a última observação associada. `translation` documenta deslocamento em metros na projeção equivalente e, quando usada, a qualidade da correlação do apoio. Ausências não produzem polígonos sintéticos.

`lineage.parent_track_uids`, `split` e `merge` vêm da sobreposição direta dos núcleos. Os eventos identificam pais e filhos por UID. Fusões encerram parentes sem continuação herdada.

`history_lonlat` registra somente observações já recebidas. `motion` usa os últimos três pontos: graus a partir do norte, km/h, deslocamento médio em km, durações reais, coerência, confiança, `observed_intervals: 2`, `source_timestamps` e `uses_future_data: false`.

`motion_steps` guarda os dois intervalos mais recentes, com `before_timestamp`, `timestamp`, `east_m`, `north_m`, `source`, `assessment` e `registration`. Os centros em `history_lonlat` nunca são substituídos. O modo `centroid` usa esses centros; `support_checked` usa a translação do apoio quando sua qualidade e a sobreposição do núcleo permitem. Sem apoio ou com correlação incerta, usa o centro observado. As associações pelo histórico usam sempre a direção dos centros, permitindo comparar os modos com as mesmas identidades.

`registration` registra offsets em EPSG:6933, sobreposição, razão de áreas, resolução e `peak_margin`: diferença normalizada entre o pico e a melhor alternativa fora de um raio de três células. Para direção, exige sobreposição ≥65%, margem ≥2%, razão de áreas ≥25%, velocidade geodésica permitida e sobreposição do núcleo transladado ≥10%. Esses controles são heurísticos, não probabilidades.

`motion.interval_sources` e `support_assessments` explicam os dois vetores. `support_conflict: true` indica translação do apoio incompatível com o núcleo. `trend` é nulo quando não estimável, inconsistente ou em conflito no modo conferido. Contém `geometry`, `axis`, `length_km`, `near_width_km`, `far_width_km`, `anchor: intense_core_forward_edge` e `interpretation: fixed_direction_corridor`. As três dimensões seguem a configuração e permanecem constantes. Não há tempos futuros publicados.

O mapa seleciona núcleos pela chave `track_uid` e rotula pelo `core_id`. A camada de apoio fica desligada inicialmente. Contagens e filtros são do recorte. Sem fuso presumido e sem API externa.
