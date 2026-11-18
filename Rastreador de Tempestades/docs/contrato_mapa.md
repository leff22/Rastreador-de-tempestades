# Contrato dos produtos de núcleos

## Região e CRS

Todos os produtos publicados usam EPSG:4326 e o mesmo polígono trapezoidal, definido em `config/tracking_config.py`:

```json
[[-63.0, -14.6],
 [-35.5, -14.6],
 [-35.5, -34.3],
 [-63.0, -32.2],
 [-63.0, -14.6]]
```

As coordenadas seguem a ordem longitude, latitude. O `bounds` do manifesto é somente o bounding box retangular da imagem; não substitui o polígono de cobertura. `coverage_polygon` e `coverage_type: polygon` definem a área efetiva de publicação.

O recorte ocorre na exportação, depois da detecção e do rastreamento. O cache mantém geometrias e centróides originais, sem recorte. Assim, mudar a região de publicação não altera a associação temporal dos núcleos.

## Produtos por horário

Pastas: `YYYYMMDD_HHMM`. `processing_mode: cores`.

| Arquivo | Conteúdo |
|---|---|
| `radar.png` | Reflectividade em RGBA; pixels fora do polígono são transparentes |
| `cells.geojson` | Geometrias dos núcleos ≥40 dBZ após interseção com o polígono |
| `support.geojson` | Áreas ≥32 dBZ recortadas; `detection_id` local e `cell_ids` limitados aos núcleos publicados |
| `trends.geojson` | Geometrias e eixos dos trapézios recortados |
| `tracks.geojson` | Segmentos das trajetórias de centróides observados, recortados na fronteira |
| `metadata.json` | Núcleos, apoio, tendências, histórico espacial interno, eventos e metadados de cobertura |
| `manifest.json` | Arquivos, CRS, polígono, bounding box, dimensões raster e contagens das camadas |

### Regras de recorte

- Polígonos são publicados pela interseção com a região. Núcleos e áreas de apoio que cruzam a fronteira são mantidos somente na parte interna; feições sem interseção areal são omitidas.
- `area_km2` em metadata representa a área da geometria publicada, calculada em EPSG:6933. `area_px` não é publicado porque o número de pixels da detecção original não representa a área recortada.
- Se o centróide observado estiver dentro da região, ele é preservado. Se estiver fora, `centroid_lonlat` passa a ser o centróide da geometria recortada e `centroid_source` recebe `clipped_geometry`. O valor observado original permanece no cache.
- `history_lonlat` contém somente posições observadas dentro da região. Não são criadas posições observadas artificiais na fronteira.
- Trajetórias são construídas a partir do histórico integral em memória e depois intersectadas com o polígono. Os pontos de entrada e saída resultantes do recorte são vértices geométricos, não observações meteorológicas.
- Geometrias e eixos de tendências são recortados. Se o corredor não tiver interseção areal, a tendência não é publicada para aquele núcleo; o eixo pode ser nulo se não atravessar a região.
- A máscara do PNG usa o mesmo polígono. O bounding box define somente a janela retangular da imagem; a área externa ao trapézio permanece transparente.
- A metadata não publica geometrias, centróides ou pontos históricos fora da região. A geometria original e os centróides usados no rastreamento permanecem no cache.

## Resumo da sequência

`outputs/sequence_summary.json` lista observações ordenadas, configuração e caminhos relativos. Seus bounds, dimensões raster, pixels válidos e contagens representam a publicação recortada. Não devem ser interpretados como extensão integral do TIFF de entrada.

O manifesto de cada horário contém `coverage_polygon`, `coverage_type`, `bounds`, estatísticas raster e `feature_count` por camada GeoJSON. `bounds: [[sul,oeste],[norte,leste]]` descreve o bounding box da janela raster, não o limite irregular da cobertura.

## Identidade e rastreamento

`core_id` é um número visível reservado enquanto a trajetória estiver ativa, mesmo ausente. Pode ser reutilizado após encerramento. `track_uid` é único na sequência e nunca reutilizado; usar como chave de banco, linhagem e seleção ao avançar o tempo. `cell_id` e `support_detection_id` são locais à observação. A área de apoio não possui `system_id` nem trajetória própria.

`support_detection_id` pode ser nulo: o núcleo permanece válido sem área de apoio aceita. O cache de detecção mantém as geometrias originais, independentes do polígono de publicação.

`association.method`: `birth`, `overlap`, `support_translation` ou `history_translation`. A sobreposição é sempre dos **núcleos originais**, dividida pela menor área, em EPSG:6933. `previous_timestamp` aponta para a última observação associada. `translation` documenta deslocamento em metros na projeção equivalente e, quando usada, a qualidade da correlação do apoio. Ausências não produzem polígonos sintéticos.

`lineage.parent_track_uids`, `split` e `merge` vêm da sobreposição direta dos núcleos originais. Os eventos identificam pais e filhos por UID. Fusões encerram parentes sem continuação herdada.

`motion` usa os últimos três centróides observados disponíveis no rastreador: graus a partir do norte, km/h, deslocamento médio em km, durações reais, coerência, confiança, `observed_intervals: 2`, `source_timestamps` e `uses_future_data: false`. A lista espacial `history_lonlat` publicada pode conter menos pontos porque posições externas à região são omitidas; ela não substitui o histórico integral mantido no cache e usado para calcular o movimento.

`motion_steps` guarda os dois intervalos mais recentes, com `before_timestamp`, `timestamp`, `east_m`, `north_m`, `source`, `assessment` e `registration`. Os centros observados do rastreador nunca são substituídos. O modo `centroid` usa esses centros; `support_checked` usa a translação do apoio quando sua qualidade e a sobreposição do núcleo permitem. Sem apoio ou com correlação incerta, usa o centro observado. As associações pelo histórico usam sempre a direção dos centros, permitindo comparar os modos com as mesmas identidades.

`trend` é nulo quando não estimável, inconsistente ou em conflito no modo conferido. Contém `geometry`, `axis`, `length_km`, `near_width_km`, `far_width_km`, `anchor: intense_core_forward_edge` e `interpretation: fixed_direction_corridor`. As dimensões seguem a configuração e permanecem constantes. Não há tempos futuros publicados.

## Validação

`scripts/radar_tracker_cli.py validate` verifica consistência de IDs, geometrias internas ao polígono, alinhamento entre metadata e GeoJSON, contagens do manifesto, máscara alfa do raster e metadados causais. A validação não recalcula associações usando geometrias recortadas: as associações pertencem ao domínio integral do cache.
