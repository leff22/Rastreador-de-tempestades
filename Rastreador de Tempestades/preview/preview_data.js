window.RASTREMENTO_PREVIEW_DATA = {
  productName: "Rastreamento de Tempestades",
  sourceLabel: "Rainviewer",
  regionLabel: "Brasil com foco operacional no Sul, Centro-Oeste e Sudeste",
  sequencePath: "../outputs/sequence_summary.json",
  basemapPath: "./assets/brazil-states.geojson",
  mapBounds: {
    south: -34.8,
    west: -74.5,
    north: 5.8,
    east: -32.2
  },
  mapLabels: [
    { name: "Manaus", lon: -60.02, lat: -3.10 },
    { name: "Brasilia", lon: -47.88, lat: -15.79 },
    { name: "Salvador", lon: -38.50, lat: -12.97 },
    { name: "Sao Paulo", lon: -46.63, lat: -23.55 },
    { name: "Porto Alegre", lon: -51.23, lat: -30.03 }
  ],
  initialFrameName: "rainviewer_20261115_2300",
  playbackIntervalMs: 650
};
