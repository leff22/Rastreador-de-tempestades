# Rastreador de Tempestades

Detecta e acompanha **núcleos intensos ≥40 dBZ**. A área da tempestade **≥32 dBZ** serve como apoio para estimar deslocamento; não recebe identidade temporal e não define a caixa do núcleo.

## Executar

Python 3.11 ou superior, com `pip install -r requirements.txt`.

**Processar Radar.cmd** processa, valida, calibra e gera o GIF. **Abrir Preview Radar.cmd** abre o mapa local com núcleos, trapézios, filtros e controle temporal.

```powershell
python "scripts/radar_tracker_cli.py" process --preview-gif
python "scripts/radar_tracker_cli.py" serve-preview
python "scripts/radar_tracker_cli.py" validate
python "scripts/radar_tracker_cli.py" compare
python "scripts/radar_tracker_cli.py" gif
python "scripts/replay_systems.py"
python "scripts/test_systems.py"
python "scripts/calibrate_support.py"
```

O nome `replay_systems.py` foi mantido para os comandos existentes; agora reassocia núcleos. Ele usa o cache, verifica a assinatura dos TIFFs e preserva os PNGs. `process` aceita `--input-dir`, `--output-root`, `--cache-dir` e `--overlap`; caminhos padrão partem do projeto.

`calibrate_support.py` compara apoio em **30/32/35 dBZ** e limite de velocidade em **120/180 km/h**. Mantém exatamente as detecções de núcleo de 40 dBZ, verifica o apoio em cache e lê cada TIFF uma vez para reconstruir os demais limiares. As variantes permanecem em memória. Compara direção pelos centros e pelo apoio conferido nas mesmas associações, além dos casos comuns às seis configurações.

O replay aceita `--storm-threshold`, `--max-speed` e `--direction-mode centroid|support_checked`. Alterar apenas o limiar do apoio reconstrói essa máscara e sua assinatura em cache, preservando núcleos e PNGs. A configuração operacional padrão está em `config/tracking_config.py`.

## Regras

- Núcleo: componente conectado ≥40 dBZ, área mínima 40 km².
- Apoio: componente conectado ≥32 dBZ, área mínima 60 km², contendo núcleo aceito.
- A ausência de apoio válido não descarta um núcleo; a máscara de 40 dBZ é independente.
- Conectividade por quatro vizinhos, sem suavização, preenchimento de buracos ou pontes artificiais. Áreas em EPSG:6933; coordenadas com precisão comum de 10⁻⁹ grau.
- `core_id`: menor número disponível, reservado durante toda a vida ativa, inclusive ausências. Só pode ser reutilizado depois do encerramento.
- `track_uid`: identificador único, crescente e nunca reutilizado na sequência; é a chave para trajetórias, seleção temporal e linhagem. `cell_id` identifica apenas a detecção daquele horário.
- Associação direta: interseção dos **núcleos** / menor área, com velocidade máxima de centróide 180 km/h e ausência máxima de 40 minutos.
- Sem associação direta: estima-se a translação da área de apoio por correlação de máscaras binárias em grade de aproximadamente 2 km, limitada a 256 pixels por dimensão e ao deslocamento plausível. Exige sobreposição do apoio ≥50% e razão de áreas ≥25%.
- Essa translação é aplicada ao núcleo anterior, que precisa sobrepor o núcleo atual. Também pode ser usada a direção causal do próprio núcleo. Não há associação só por proximidade ou por compartilhar a mesma tempestade.
- As associações por translação exigem razão de áreas dos núcleos ≥25%, diferença de pico ≤15 dBZ e vantagem de pelo menos 10 pontos percentuais sobre candidatos concorrentes.
- Divisões e fusões são inferidas apenas da sobreposição direta dos núcleos; registram UIDs e reiniciam o histórico de movimento. Parentes absorvidos são encerrados.

## Trapézio

Comprimento **20 km**, largura da base **10 km** e da frente **20 km**, constantes e configuráveis em `config/tracking_config.py`. A base nasce na borda frontal do **núcleo intenso**. Só posição e orientação mudam; o tamanho não cresce com a tempestade.

A direção usa exatamente os dois vetores de velocidade entre **t−2, t−1 e t**, respeitando a duração real dos intervalos. Os pesos são **1:2**, favorecendo o vetor mais recente. O trapézio fica oculto antes de três observações, com deslocamento médio inferior a 1 km, após divisão/fusão ou com coerência inferior a **0,85**. O comprimento é uma dimensão de apresentação; não é horizonte de previsão nem intervalo futuro de 20/40/60 minutos.

O modo `support_checked` confere também associações diretas pela translação do apoio. Exige sobreposição do apoio ≥65%, diferença normalizada do pico de correlação ≥2%, razão de áreas ≥25%, velocidade permitida e sobreposição do núcleo transladado ≥10%. Converte offsets da projeção equivalente para deslocamento geodésico. Apoio ausente ou incerto conserva o vetor do centro observado; apoio incompatível com o núcleo oculta o trapézio. `motion_steps` guarda os dois intervalos e sua avaliação sem alterar o histórico dos centros. A direção pelo histórico usada para associação continua baseada nos centros, garantindo comparações pareadas.

## Produtos e calibração

37 TIFFs originais em `inputs/rainviewer/`, de 15/11/2026, 11:00–23:00, a cada 20 minutos. Horários literais dos nomes, sem fuso presumido. O leitor exige EPSG:4326.

Cada observação exporta PNG transparente sem anotações, polígonos dos núcleos, apoio, trapézios, histórico, metadados e manifesto. [Contrato do mapa](docs/contrato_mapa.md).

- `outputs/validation_report.json`: consistência de geometria, IDs, sobreposição publicada, histórico causal e dimensões fixas.
- `outputs/calibration_report.json`: comparação de sobreposição direta, histórico e apoio com limiares 10%, 20% e 30%; trajetórias curtas; ambiguidade; erro da direção no horário seguinte; pesos da direção comparados nas mesmas associações.
- `outputs/core_tracking_preview.gif`: sequência atual, com polígonos e IDs dos núcleos.
- `outputs/support_calibration.json`: seis configurações de apoio/velocidade, direção pareada, amostras comuns e hashes de entrada.
- `outputs/support_review.png` e `outputs/support_review.csv`: recortes de melhora/piora da direção, divisão e desaparecimento no radar observado. O horário seguinte serve apenas à avaliação.
- [Calibração da área de apoio](docs/resultado_calibracao_apoio.md) e [rodada inicial de reestruturação](docs/resultado_reestruturacao.md).

A sequência não tem identidades meteorológicas rotuladas. Menos nascimentos não prova melhor rastreamento; os erros são relativos aos centróides associados. Crescimento e mudanças de forma podem deslocar esses centróides sem transporte equivalente. A mesma sequência serve para ajuste e avaliação; falta validação independente.

## Estrutura ativa

`src/storm_detection.py` extrai máscaras; `src/core_tracking.py` acompanha núcleos; `src/products.py` e `src/pipeline.py` coordenam produtos. `scripts/` opera, valida, calibra e testa; `preview/` contém o mapa. `.cache/detections/` é usada pelos replays. `outputs/` contém os produtos atuais.

`../radar-storm-tracker/` e `../Documents/` são referências preservadas. O inventário da limpeza anterior está em `docs/cleanup_manifest.json`.
