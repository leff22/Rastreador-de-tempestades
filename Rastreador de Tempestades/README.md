# Rastreador de Tempestades

Detecta e acompanha **núcleos intensos ≥40 dBZ**. A área da tempestade **≥32 dBZ** serve como apoio para estimar deslocamento; não recebe identidade temporal e não define a caixa do núcleo.

## Executar

Python 3.11 ou superior, com `pip install -r requirements.txt`.

**Processar Radar.cmd** executa o processamento, valida os produtos e gera o GIF quando solicitado. **Abrir Preview Radar.cmd** abre o mapa local.

```powershell
python "scripts/radar_tracker_cli.py" process --preview-gif
python "scripts/radar_tracker_cli.py" serve-preview
python "scripts/radar_tracker_cli.py" validate
python "scripts/radar_tracker_cli.py" gif
python "scripts/radar_tracker_cli.py" compare
python "scripts/replay_systems.py"
python "scripts/test_systems.py"
python "scripts/calibrate_support.py"
```

O processamento normal não executa calibração. `compare` e `calibrate_support.py` são rotinas de diagnóstico/calibração sob demanda. `replay_systems.py` é manutenção e reprocessamento; use `--calibrate` somente quando quiser repetir a comparação experimental. `test_systems.py` executa testes sob demanda. `system_tools.py` é um módulo compartilhado de validação, calibração e geração de GIF, não um comando operacional independente.

`process` aceita `--input-dir`, `--output-root`, `--cache-dir` e `--overlap`; caminhos padrão partem do projeto. A configuração operacional, incluindo a região de publicação, está em `config/tracking_config.py`.

## Região de publicação

Todos os produtos publicados usam o mesmo polígono EPSG:4326:

```text
[[-63.0, -14.6],
 [-35.5, -14.6],
 [-35.5, -34.3],
 [-63.0, -32.2],
 [-63.0, -14.6]]
```

O polígono é trapezoidal. O raster mantém a caixa envolvente como dimensão da imagem e aplica transparência fora do polígono. Núcleos, áreas de apoio, tendências, trajetórias e geometrias da metadata são recortados pela interseção com a região. Núcleos que cruzam a fronteira são mantidos pela sua parte interna. Quando o centróide observado fica fora, a metadata publica o centróide da geometria recortada; a origem desse centróide é indicada em `centroid_source`.

As posições históricas publicadas contêm somente observações dentro da região. Trajetórias são recortadas geometricamente, inclusive nos pontos em que cruzam a fronteira. O cache preserva as detecções originais e não recebe recorte; o rastreamento continua operando sobre a geometria integral.

O manifesto registra o polígono, o bounding box, dimensões do raster e contagens das camadas. `sequence_summary.json` reporta bounds e contagens publicados, não a extensão integral do TIFF de entrada. Consulte o [contrato dos produtos](docs/contrato_mapa.md).

## Regras de detecção e rastreamento

- Núcleo: componente conectado ≥40 dBZ, área mínima original 40 km².
- Apoio: componente conectado ≥32 dBZ, área mínima original 60 km², contendo núcleo aceito.
- A ausência de apoio válido não descarta um núcleo; a máscara de 40 dBZ é independente.
- Conectividade por quatro vizinhos, sem suavização, preenchimento de buracos ou pontes artificiais. Áreas de detecção em EPSG:6933.
- `core_id`: menor número disponível, reservado durante toda a vida ativa, inclusive ausências. Só pode ser reutilizado depois do encerramento.
- `track_uid`: identificador único, crescente e nunca reutilizado na sequência; é a chave para trajetórias, seleção temporal e linhagem. `cell_id` identifica apenas a detecção daquele horário.
- Associação direta: interseção dos **núcleos** / menor área, com velocidade máxima de centróide 180 km/h e ausência máxima de 40 minutos.
- Sem associação direta: estima-se a translação da área de apoio por correlação de máscaras binárias em grade de aproximadamente 2 km. Exige sobreposição do apoio ≥50% e razão de áreas ≥25%.
- A translação é aplicada ao núcleo anterior, que precisa sobrepor o núcleo atual. Também pode ser usada a direção causal do próprio núcleo. Não há associação só por proximidade ou por compartilhar a mesma tempestade.
- Divisões e fusões são inferidas apenas da sobreposição direta dos núcleos; registram UIDs e reiniciam o histórico de movimento. Parentes absorvidos são encerrados.

## Tendências

O trapézio tem comprimento **20 km**, largura da base **10 km** e da frente **20 km**, constantes e configuráveis em `config/tracking_config.py`. A base nasce na borda frontal do núcleo intenso. Só posição e orientação mudam; o tamanho não cresce com a tempestade.

A direção usa exatamente os dois vetores de velocidade entre **t−2, t−1 e t**, respeitando a duração real dos intervalos. Os pesos são **1:2**, favorecendo o vetor mais recente. O trapézio fica oculto antes de três observações, com deslocamento médio inferior a 1 km, após divisão/fusão ou com coerência inferior a **0,85**. O comprimento é uma dimensão de apresentação; não é horizonte de previsão.

O modo `support_checked` confere também associações diretas pela translação do apoio. Exige sobreposição do apoio ≥65%, diferença normalizada do pico de correlação ≥2%, razão de áreas ≥25%, velocidade permitida e sobreposição do núcleo transladado ≥10%. Apoio ausente ou incerto conserva o vetor do centro observado; apoio incompatível com o núcleo oculta o trapézio. `motion_steps` guarda os dois intervalos e sua avaliação sem alterar o histórico dos centros.

## Estrutura do projeto

- `src/storm_detection.py`: detecção integral das máscaras de núcleo e apoio.
- `src/core_tracking.py`: associação e rastreamento sobre geometrias integrais.
- `src/products.py`: recorte espacial e exportação dos produtos.
- `src/pipeline.py`: processamento por horário, cache e resumo da sequência.
- `scripts/radar_tracker_cli.py`: entrada operacional.
- `scripts/system_tools.py`: validação, calibração e GIF.
- `scripts/replay_systems.py`: manutenção/reprocessamento.
- `scripts/calibrate_support.py`: calibração experimental.
- `scripts/test_systems.py`: testes sob demanda.
- `preview/`: mapa local.
- `.cache/detections/`: cache das detecções originais, sem recorte.
- `outputs/`: produtos publicados.

`../radar-storm-tracker/` e `../Documents/` são referências preservadas.
