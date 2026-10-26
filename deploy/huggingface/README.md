---
title: AuthShield-AI
emoji: 🛡️
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 8501
short_description: Detección híbrida (RF + red neuronal) de ataques en logins
---

# AuthShield-AI

Demo del dashboard de **AuthShield-AI**: detección de ataques en logs de autenticación con un Random Forest (fuerza bruta) y una red neuronal (robo de identidad).

- Simula tráfico de logins y mira qué alertas genera cada modelo.
- Evalúa un login a mano ajustando las 4 variables.
- Consulta las métricas de los modelos sobre el conjunto de prueba.

Los datos son **sintéticos**. Código, arquitectura y API: [github.com/MurginhaSebastian/AuthShield-AI](https://github.com/MurginhaSebastian/AuthShield-AI).
