# Preparación para producción — auditoría del 2026-11-04

**Veredicto: NO LISTO para producción.**
- Listo para la **demostración**, si se tienen en cuenta los riesgos del §3.
- No se debe habilitar producción mientras no estén definidos el cifrado, el control de acceso, la
  retención y la eliminación.

## 1. Bloqueos para producción

| # | Bloqueo | Por qué bloquea | Cómo se desbloquea | Referencia |
|---|---|---|---|---|
| P1 | PII en claro: `shipment_recipients` y texto de `messages` | Datos personales sin protección en reposo | Cifrado de aplicación (propuesta aprobada) | REMEDIATION_PLAN §4, Q5 |
| P2 | Sin política de retención ni de eliminación | Ley 1581 de 2012 (Habeas Data) | Decisión legal y su implementación | Q5 |
| P3 | `POST /attachments` sin autenticación ni límite de tasa | Agotamiento del disco: 10 MB por petición, sin límite | Q3 | SEC-06 |
| P4 | Historial accesible solo con el `session_id` | El `session_id` es la única barrera para leer una conversación | Q2 | SEC-01 |
| P5 | Mercancía peligrosa aceptada en lotes B2B sin política | La spec §8 exige revisión y la aceptación automática no está documentada | Q1 / CR-006 | BIZ-01 |
| P6 | Capacidad del LLM sin demostrar | ~0,5 respuestas IA/s medidas en un portátil. No hay prueba sostenida ni mezcla real | Q10: infraestructura y prueba sostenida | CAP-01 |
| P7 | Una sola instancia: limitador en memoria por proceso; WhatsApp y PBX en `SIMULATED` | Al escalar a varias instancias, los límites dejan de aplicarse. Los canales reales no se probaron | Limitador compartido; integración real de Meta y PBX | OPS-01 |
| P8 | `/docs` público | Superficie innecesaria | Q4 | SEC-03 |

## 2. Capacidad: qué está demostrado y qué no

**Demostrado** en el equipo de la auditoría (1 proceso, pruebas de segundos):
- rastreo: ~1.100 req/s;
- turnos de chat deterministas: ~400/s;
- 0 errores hasta concurrencia 50;
- turnos con IA: ~0,5 respuestas/s;
- bajo ráfaga (24 o 40 simultáneas), 0 errores, con degradación inmediata a la oferta de asesor por
  encima de 12 en curso o en espera.

**No demostrado: las 18.000 interacciones diarias del caso.** Cálculo como hipótesis por probar, no
como afirmación:
- 18.000 al día son 0,21 interacciones/s de media.
- Según la spec, el 55 % es rastreo (determinista). Si el 45 % restante necesitara el LLM, serían
  ~8.100 al día: 0,09/s de media, por debajo de los 0,5/s medidos.
- **Falta lo que decide la respuesta:**
  - la concentración en la hora pico;
  - la proporción real de consultas que usan el LLM (la cotización es determinista; PQRS y carga
    especial usan RAG);
  - el comportamiento sostenido durante horas.
- Hasta repetir `tests/load/run_load.py` y la saturación con esa mezcla, **no se afirma la capacidad**.

## 3. Riesgos para la demostración

| Riesgo | Mitigación |
|---|---|
| El servidor `:8000` del túnel corre código **anterior** a la auditoría: sin la corrección RAG, con el texto R1 y sin límites | Reiniciarlo con 1.6.0 antes de la demo y avisar al front del texto nuevo de espera y del 413 |
| Primera pregunta con IA en frío (carga del modelo en Ollama) | Hacer una pregunta de calentamiento antes de presentar |
| Varias personas probando el chat con IA a la vez: por encima de 12, la respuesta degrada | Es lo diseñado: ofrece un asesor y no falla. Explicarlo como control de calidad (HITL) |
| CRIT-004 (Key Account) no se puede mostrar | No mencionarlo como demostrado; explicar que falta el dato del negocio (Q7) |
| `RELECTURA_HUB_FONTIBON`, el estado del ejemplo oficial, no aparece en ninguna guía de demo | Mostrar la traducción en `data/config/tms_state_map.json`, o pedir una guía real (DEMO-01). No inventar datos |
| El asesor no puede responder al cliente en el chat | El texto ya no lo promete (R1). Presentar CR-007 como la siguiente entrega |
| Una pregunta fuera del corpus | Comportamiento medido: se abstiene o escala (5/5) y no alucina |

## 4. Prioridades recomendadas

1. **Antes de la demo:**
   - reiniciar `:8000` con 1.6.0;
   - ensayar los 4 flujos en la PWA con la pregunta de calentamiento;
   - avisar al front del cambio de texto y del 413.
2. **Decisiones de negocio:**
   - Q1, mercancía peligrosa (bloquea producción y afecta la rúbrica 2.2);
   - Q6 y Q7, reglas HITL;
   - Q8, inactividad.
3. **Seguridad**, en una sola versión MAJOR coordinada con el front:
   - Q2, historial con `X-Client-Id`;
   - Q3, adjuntos atados a la sesión.
4. **PII:** aprobar la propuesta de cifrado (Q5), con su migración, y definir la retención.
5. **CR-007 P0a:** canal del asesor y CRM, que resuelve del todo R1 y R2.
6. **Capacidad:** decidir la infraestructura del LLM (Q10) y repetir las pruebas con la mezcla real y
   una duración sostenida.
7. **Calidad:** pruebas de PQRS y del RAG con un LLM falso (TST-01) y tolerancia a documentos corruptos
   (REL-02).
