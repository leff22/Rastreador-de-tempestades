# Resultado: identidade do núcleo e direção com três observações

Este relatório registra a rodada inicial com direção pelos centros observados. A calibração posterior do apoio e os parâmetros operacionais atuais estão em [calibração da área de apoio](resultado_calibracao_apoio.md). As contagens de trapézios e erros de direção abaixo pertencem à referência inicial.

Rodada concluída em 18/11/2026, sobre os 37 TIFFs de 15/11/2026, 11:00–23:00, a cada 20 minutos. Os números abaixo abrangem toda a cobertura dos TIFFs; o mapa mostra apenas seu recorte.

O ID e o polígono acompanham o **núcleo ≥40 dBZ**, com área mínima de 40 km². A área **≥32 dBZ** serve somente como apoio para estimar deslocamento. Núcleos válidos permanecem detectados mesmo sem apoio aceito de 60 km². Essa independência recuperou **60 observações de núcleos** antes descartadas pela área mínima do apoio; a sequência soma agora **4.376 observações de núcleos**.

## IDs

Os números acima de 1.000 vinham do contador acumulado de nascimentos da sequência; não indicavam essa quantidade de núcleos simultâneos.

Agora `core_id` usa o menor número livre, reservado enquanto a trajetória estiver ativa, inclusive durante ausências. Só é reutilizado após encerramento. `track_uid` permanece único e crescente na sequência, permitindo distinguir trajetórias que reutilizem o mesmo número visível. A seleção temporal e a linhagem usam esse UID.

O maior ID visível nesta rodada foi **348**. Houve **1.879 trajetórias inferidas**, com UIDs distintos. Esses UIDs podem ultrapassar 1.000 e não são exibidos como rótulos do mapa.

## Associação e calibração

A sobreposição é a interseção dos **núcleos** dividida pela menor área, em EPSG:6933. Sem associação direta, a área de apoio fornece uma translação por correlação de máscaras; essa translação precisa fazer o núcleo anterior sobrepor o atual. Também é testado o movimento causal do próprio núcleo. Compartilhar uma área de apoio não une IDs.

As translações respeitam velocidade máxima de centróide de 180 km/h, ausência máxima de 40 minutos, razão de áreas dos núcleos ≥25%, diferença de pico ≤15 dBZ e vantagem ≥10 pontos percentuais sobre concorrentes. O apoio exige sobreposição registrada ≥50% e razão de áreas ≥25%. A grade inicia em 2 km e é limitada a 256 pixels por dimensão. Divisões e fusões vêm apenas da sobreposição direta dos núcleos e reiniciam o movimento.

As cinco configurações usam as mesmas detecções independentes, cache versão 3, e coerência mínima 0,85.

| Configuração | Trajetórias distintas | Com só uma observação | Com ≥3 observações | Diretas | Pelo apoio | Pelo histórico |
|---|---:|---:|---:|---:|---:|---:|
| Sobreposição direta, 10% | 2.520 | 1.669 | 397 | 1.856 | 0 | 0 |
| Direta + histórico, 10% | 2.489 | 1.647 | 391 | 1.855 | 0 | 32 |
| Direta + apoio + histórico, 10% | **1.879** | **961** | **550** | 1.845 | 628 | 24 |
| Direta + apoio + histórico, 20% | 2.034 | 1.113 | 527 | 1.633 | 682 | 27 |
| Direta + apoio + histórico, 30% | 2.180 | 1.277 | 481 | 1.425 | 734 | 37 |

Mantido **10%** como parâmetro operacional inicial. Frente à sobreposição direta sobre os mesmos núcleos, a combinação com apoio reduziu as trajetórias de uma observação em **42,4%**, e os nascimentos acumulados em **25,4%**. São sinais de continuidade; a identidade meteorológica ainda precisa de exemplos rotulados.

A configuração adotada realizou **2.497 associações** com uma observação anterior: 1.845 diretas, 628 pelo apoio e 24 pelo histórico. Rejeitou **2.394 candidatos de translação ambíguos**, distribuídos por várias rodadas; esse número conta candidatos, não núcleos distintos. Registrou 264 eventos de fusão e 216 de divisão.

Nas 652 associações por translação, o resíduo entre o deslocamento aplicado e o centro observado foi de **2,85 km na mediana** e **6,84 km no percentil 90**, medidos na projeção equivalente. Esse resíduo verifica compatibilidade geométrica; não confirma que cada par representa o mesmo núcleo meteorológico.

## Direção e trapézios

O trapézio tem **20 km de comprimento**, **10 km na base** e **20 km na frente**. Sua base nasce na borda frontal do núcleo intenso. As dimensões permanecem constantes; mudam posição e direção.

A direção usa exatamente as velocidades entre **t−2 → t−1** e **t−1 → t**, com pesos **1:2**, respeitando a duração real dos intervalos. São necessárias três observações, deslocamento médio ≥1 km e coerência ≥0,85. Coerência mede o alinhamento dos vetores; a confiança publicada continua qualitativa. Divisão/fusão reinicia o histórico.

Os pesos e filtros abaixo foram avaliados nas mesmas associações da configuração adotada. O horário seguinte entra **somente no avaliador**, depois de o rastreador emitir a direção. O filtro de coerência altera a quantidade de casos avaliados.

| Peso do vetor recente | Coerência mínima | Casos | Erro angular mediano | Percentil 90 |
|---|---:|---:|---:|---:|
| 1 | 0,70 | 448 | 39,1° | 137,6° |
| 1 | 0,85 | 385 | 36,6° | 125,8° |
| 2 | 0,70 | 446 | 37,9° | 135,7° |
| **2** | **0,85** | **392** | **34,7°** | **126,3°** |
| 3 | 0,70 | 451 | 39,6° | 136,1° |
| 3 | 0,85 | 404 | 34,6° | 131,0° |

O peso 2 preserva a contribuição de ambos os deslocamentos e apresentou mediana próxima à do peso 3, com menor erro no percentil 90. O filtro 0,85 publica menos direções inconsistentes. **Ainda existem erros angulares grandes**, inclusive reversões: o percentil 90 de 126,3° limita fortemente qualquer interpretação como previsão confiável.

Nos 392 casos avaliados com os parâmetros adotados, extrapolar o centro pela velocidade estimada resultou em erro mediano de **8,54 km** e percentil 90 de **54,43 km**. Crescimento, mudanças de forma, associação incorreta e lacunas podem afetar esses números. A mesma sequência serviu para ajuste e avaliação, sem conjunto independente.

Entre 2.028 passos extraídos do histórico observado, sem cruzar reinícios de topologia, a mediana foi **8,15 km**. Comprimentos fixos de 10, 20 e 30 km abrangem, respectivamente, 59,9%, **79,4%** e 84,7% dessas distâncias. A escolha de 20 km controla a apresentação visual. As larguras 10/20 km são parâmetros visuais fixos; não foram validadas como cobertura probabilística de previsão.

A sequência final contém **739 trapézios**, somados entre horários. Não há horizontes futuros de 20/40/60 minutos publicados.

## Verificação e produtos

- **13 testes passaram**, incluindo continuidade, divisão/fusão, ausência/expiração, rejeição de saltos, translação ambígua, movimento pelo apoio e pelo histórico, orientação e dimensões geodésicas, três observações, núcleos separados dentro do mesmo apoio e núcleo válido sem apoio.
- **37 horários validados, zero erros**: IDs, geometria, sobreposição reconstruída, histórico causal, orientação e dimensões fixas.
- Os números do relatório de calibração conferem com os produtos efetivamente publicados.
- O GIF final tem **37 quadros, 1.000 × 1.000 pixels**.
- Seleção no mapa, filtro de pico e navegação temporal foram testados no navegador. A seleção usa UID; o apoio começa desligado. A tela testada de 683 × 522 pixels não apresentou rolagem horizontal.
- Os **37 hashes SHA-256 dos TIFFs originais conferem**, sem alterações de entrada.

[GIF](../outputs/core_tracking_preview.gif), [calibração completa](../outputs/calibration_report.json), [validação](../outputs/validation_report.json) e [contrato do mapa](contrato_mapa.md). O mapa pode ser aberto com **Abrir Preview Radar.cmd**.

Após validar as substituições, foram removidos **49 arquivos de produtos e caches antigos, 31,2 MB**: os 37 `systems.geojson`, o GIF de sistemas, a comparação antiga de áreas e os caches Python. O módulo antigo de acompanhamento de sistemas foi substituído por `src/core_tracking.py`. A limpeza anterior e os hashes de entrada continuam registrados em `cleanup_manifest.json`; o cache de detecções versão 3 permanece em uso.
