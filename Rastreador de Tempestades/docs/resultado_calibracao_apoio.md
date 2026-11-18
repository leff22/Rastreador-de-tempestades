# Calibração da área de apoio e direção

Sequência de 37 TIFFs de 15/11/2026, 11:00–23:00, em intervalos de 20 minutos. As 4.376 observações de núcleos ≥40 dBZ são preservadas em todas as variantes. A área de apoio permanece sem identidade temporal e não define o polígono do núcleo.

## Comparação

Foram comparados apoio em 30, 32 e 35 dBZ, cada um com limite de velocidade de centróide de 120 ou 180 km/h. Área mínima do núcleo: 40 km²; apoio: 60 km²; sobreposição dos núcleos: 10%; ausência máxima: 40 minutos.

Dentro de cada configuração, os dois métodos de direção usam exatamente as mesmas associações. A referência calcula velocidades dos centros observados. O método conferido substitui cada vetor pela translação da máscara de apoio quando há qualidade suficiente e compatibilidade com o núcleo. O avaliador também identifica sequências comuns às seis configurações pelas mesmas quatro observações, com horários e coordenadas exatos.

Os dois métodos usam somente t−2, t−1 e t, pesos 1:2, deslocamento médio mínimo de 1 km e coerência mínima de 0,85. O horário seguinte entra somente no avaliador. Divisão e fusão reiniciam movimento e histórico.

## Conferência do deslocamento

A correlação binária registra a translação do apoio em EPSG:6933. Para direção, exige sobreposição ≥65%, razão de áreas ≥25% e margem normalizada ≥2% entre o pico escolhido e a melhor alternativa fora de três células. A grade começa em 2 km e é limitada a 256 pixels por dimensão. Esses filtros são heurísticos.

A translação é convertida para longitude/latitude e então para vetor geodésico no centro anterior do núcleo. Precisa respeitar a velocidade permitida e fazer o núcleo transladado sobrepor o atual em ≥10%. Apoio ausente ou incerto conserva o vetor do centro observado. Apoio com registro aceito, mas incompatível com o núcleo, oculta o trapézio enquanto o conflito estiver entre os dois intervalos recentes.

`history_lonlat` continua contendo somente centros observados. `motion_steps` documenta os dois vetores recentes, fontes e avaliações. O movimento pelo histórico usado para associação conserva a referência dos centros, permitindo a comparação pareada dos métodos.

## Revisão visual e limites

[Recortes de radar](../outputs/support_review.png) e [índice de casos](../outputs/support_review.csv) mostram melhora e piora da direção, divisão e desaparecimento. Os contornos brancos são núcleos observados; a seta laranja representa o centroide e a ciano, o apoio conferido. O comprimento das setas é apenas uma referência visual de 20 km.

O erro angular e o erro de posição são relativos ao próximo centro associado, não a uma medida independente de transporte atmosférico. Crescimento e mudança de forma podem mover centros sem deslocamento equivalente. A amostra não tem identidades rotuladas e a mesma sequência serve para ajuste e avaliação. Menos nascimentos ou menor erro nessa amostra não comprovam precisão meteorológica.

Os relatórios informam quantas direções eram elegíveis e quantas foram publicadas; ocultar casos incertos altera a população avaliada. Contagens das avaliações do apoio representam aparições dos intervalos nas janelas de avaliação, não quantidade de tempestades distintas.

[Resultados completos](../outputs/support_calibration.json), [validação dos produtos](../outputs/validation_report.json) e [contrato](contrato_mapa.md).
