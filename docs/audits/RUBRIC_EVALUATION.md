# Evaluación con la rúbrica — auditoría del 2026-11-04

**Escala y alcance:**
- Escala de 0 a 3 por indicador.
- La auditoría solo observa el backend, el contrato y los datos.
- **N/O** = no observable desde aquí: no se asigna puntuación (no se inventa).
- Los indicadores visuales, de uso y de exposición requieren inspeccionar la PWA y preparar al equipo.
- Evidencia en [`TEST_EVIDENCE.md`](TEST_EVIDENCE.md); acciones en
  [`REMEDIATION_PLAN.md`](REMEDIATION_PLAN.md).

| Indicador | Puntuación estimada | Evidencia | Brecha | Acción para llegar a 3 | Responsable |
|---|---|---|---|---|---|
| **1.1** Funcionamiento general | **2** (backend) | 536 pruebas en verde. Los 4 módulos del caso funcionan de punta a punta. Si Ollama cae, no hay errores; tampoco bajo saturación tras REL-01 | Falta el canal asesor→cliente (CR-007). Botones interactivos solo como texto (R2). El `:8000` del túnel corre código anterior. La PWA no se inspeccionó | Reiniciar `:8000` con 1.6.0. Ensayar la demo completa en la PWA. CR-007 P0a | backend, frontend |
| **1.2** Apariencia visual | **N/O** | — | No es observable desde el backend | Revisar la PWA (consistencia, estados vacíos y de error, versión móvil) | frontend |
| **1.3** Facilidad de uso | **N/O** | El backend da respuestas rápidas, oferta de asesor en cada degradación y mensajes en español. No basta para puntuar | Sin prueba con usuarios | Prueba de pasillo con 3–5 personas sobre los 4 flujos | frontend, negocio |
| **1.4** Velocidad de IA | **2** | Rutas deterministas en ms (rastreo en chat, p50 4,7 ms). RAG p50 ~1,9 s en serie y 7,4 s con 4 en paralelo. Bajo ráfaga, la degradación es inmediata (p50 1,6 s con 24 simultáneas) | Una sola GPU/CPU local: ~0,5 respuestas IA/s. Sin *streaming* | Para la demo, precargar el modelo (primera llamada en frío) y no lanzar ráfagas. Para 3, *streaming* o más capacidad de inferencia (Q10) | IA, backend |
| **1.5** Precisión de IA | **2** | Golden set 18/18, 0 alucinaciones, 5/5 abstenciones (antes, 15/18 con el juez revisado). Controles deterministas de números, PII y URL. Escalamiento CRIT-001 a CRIT-005 verificado | Conjunto pequeño (18). El juez usa palabras clave (2 ampliadas tras revisión manual). La confianza es heurística, no calibrada | Ampliar a más de 50 preguntas reales de clientes, con revisión humana del juez. Medir la tasa de escalada falsa | IA, negocio |
| **2.1** Idea de negocio | **N/O** | El backend responde a los 4 dolores del caso: 55 % de consultas de rastreo, 12 % de error en cotizaciones, PQRS incompletas, HITL | Se evalúa en la exposición | Contar el problema con las cifras del caso (18.000 interacciones/día, 12 % de errores, 40 % de PQRS incompletas) | negocio, exposición |
| **2.2** Reglas lógicas del negocio | **2** | Fórmula oficial exacta (190.975). Seguro mínimo 2.500. TEST_ONLY separado. Plazos de PQRS. CRIT-001 a CRIT-005 desde el JSON oficial | Mercancía peligrosa aceptada en lotes sin política (Q1). CRIT-004 no demostrable (Q7). Inactividad de 5 min sin usar (Q8). Modos de CRIT-003/005 sin aprobación escrita (Q6) | Responder Q1, Q6, Q7 y Q8 e implementar | negocio, backend |
| **2.3** Viabilidad económica | **N/O** | Dato técnico: el LLM es local (sin costo por token) y la capacidad está medida (TEST_EVIDENCE §5) | Sin modelo de costos | Estimar el costo por interacción (infraestructura de inferencia ÷ volumen) frente al costo del asesor humano | negocio |
| **2.4** Utilidad real | **2** (evidencia técnica) | El 55 % del tráfico (rastreo) se resuelve sin humano ni LLM. Cotizaciones sin error aritmético. Escalamiento con un *payload* completo para el asesor | El asesor no puede responder al cliente (CR-007). La validación con usuarios es N/O | CR-007 P0a; demo con un caso de punta a punta, del cliente al asesor | backend, frontend, negocio |
| **2.5** Potencial de crecimiento | **2** | Contrato primero con versionado y CR. Puertos sustituibles (LLM, TMS). API B2B de lotes. Perfiles tarifarios versionados | Limitador en memoria por proceso. Un solo Ollama. PII sin cifrar, que bloquea producción | Cifrado (Q5), limitador compartido y plan de inferencia (Q10) | backend, IA |
| **3.1** Claridad al explicar el problema | **N/O** | — | Exposición | Guion de 1 minuto con el dolor, la cifra y la solución | exposición |
| **3.2** Seguridad al hablar | **N/O** | — | Exposición | Ensayo cronometrado y preguntas difíciles: ¿y si la IA se equivoca? (HITL y 0 alucinaciones medidas) | exposición |
| **3.3** Manejo del tiempo | **N/O** | — | Exposición | Ensayo con cronómetro; demo de 3 flujos como máximo | exposición |
| **3.4** Apoyo visual | **N/O** | Material disponible: diagrama de arquitectura (INTEGRAL_AUDIT §3), tablas de antes/después y golden set | Exposición | Llevar 1 diapositiva de métricas medidas: 18/18 y 0 alucinaciones, 0 errores bajo ráfaga, fórmula exacta | exposición |
| **3.5** Presentación visual y vestimenta | **N/O** | — | Exposición | Preparación del equipo | exposición |

**Resumen:**
- De los 6 indicadores observables desde el backend (1.1, 1.4, 1.5, 2.2, 2.4 y 2.5), todos quedan en
  **2**. La brecha común hacia 3 está en las decisiones Q1–Q10 y en CR-007.
- Los 9 restantes requieren la PWA o la exposición.
