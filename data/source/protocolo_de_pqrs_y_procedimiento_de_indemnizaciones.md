# PROTOCOLO GENERAL DE PETICIONES, QUEJAS, RECLAMOS Y PROCEDIMIENTO DE INDEMNIZACIONES

**TransLogix Colombia S.A.S. — Código de Documento: DOC-PQRS-2026-04**  
**Aplicabilidad:** Competencia KODO TAKAI 2026 / Base de Conocimiento RAG  

---

## 1. MARCO LEGAL Y REGULATORIO EN COLOMBIA

El trámite de Peticiones, Quejas, Reclamos y Solicitudes de Indemnización (PQRS) ante TransLogix Colombia S.A.S. se rige estrictamente por la normativa legal colombiana vigente:

* **Ley 1480 de 2011 (Estatuto del Consumidor):** Regula los derechos de los usuarios, garantías de prestación de servicios, protección contra publicidad engañosa y procedimientos de atención al cliente.
* **Código de Comercio de Colombia (Libro IV, Título XI - Del Contrato de Transporte):**
  * **Artículo 1028:** Presunción de entrega en buen estado salvo protesta expresa en la guía o acta de entrega.
  * **Artículo 1030:** Responsabilidad del transportador por pérdida total, parcial o avería de la mercancía desde el momento en que la recibe hasta que la entrega al destinatario.
  * **Artículo 1031:** Cálculo del valor de la indemnización basado en el valor declarado en la guía o valor comercial probado.
* **Ley 1369 de 2009:** Régimen de servicios postales y de mensajería expresa en Colombia.

---

## 2. DEFINICIONES Y TIPOLOGÍA DE PQRS

Para efectos del entrenamiento del motor RAG y clasificación automatizada en la PWA, se establecen las siguientes categorías:

1. **Petición (P):** Solicitud formal de información, copia de documentos (guías, pruebas de entrega POD) o certificaciones de servicio.
2. **Queja (Q):** Manifestación de inconformidad respecto a la atención brindada por el personal operativo, conductores, repartidores de última milla o agentes del call center.
3. **Reclamo (R):** Exigencia de cumplimiento o revisión por un servicio prestado de manera deficiente (ej. incumplimiento reiterado del tiempo de entrega SLA sin causa justificable).
4. **Solicitud de Indemnización / Siniestro (S):** Reclamación formal que busca la compensación económica por daño parcial (avería), pérdida total, saqueo o expoliación de la mercancía transportada.

---

## 3. TIEMPOS Y PLAZOS LEGALES DE RADICACIÓN

El sistema RAG debe validar los tiempos transcurridos antes de permitir la iniciación de un trámite formal de indemnización:

```
TIEMPOS MÁXIMOS DE RADICACIÓN
├── Avería Aparente (Visible):  Al momento de la entrega (en la guía/POD)
├── Avería No Aparente (Oculta): Hasta 3 días hábiles posteriores a la entrega
└── Pérdida Total / Siniestro:   Hasta 10 días hábiles tras vencer la fecha de entrega
```

* **Avería Aparente / Visible:** El destinatario debe dejar la anotación u observación escrita en la planilla del conductor o firma digital (POD) al momento de recibir el paquete (ej. *"Caja rota, cinta violada"*). De lo contrario, opera la presunción del Art. 1028 del Código de Comercio.
* **Avería No Aparente / Oculta:** Si el daño del producto no era visible externamente al momento de la entrega, el cliente dispone de un plazo máximo e improrrogable de **tres (3) días hábiles** siguientes a la fecha de entrega para radicar la novedad.
* **Pérdida Total o Extravío:** Transcurridos **cinco (5) días hábiles** posteriores a la fecha estimada de entrega sin actualización de trazabilidad en el TMS, la guía se declara automáticamente en estado `EN_INVESTIGACION_SINIESTRO`. El cliente dispone de **diez (10) días hábiles** adicionales para radicar formalmente su reclamación económica.

---

## 4. CHECKLIST DOCUMENTAL OBLIGATORIO PARA INDEMNIZACIONES

Para que el backend de IA o el asesor humano procese una solicitud de indemnización, el usuario debe aportar la totalidad de la siguiente documentación en formato digital (PDF o JPG claro):

1. **Número de Guía de Transporte:** Identificador único (`TLX-XXXXXX-CO`).
2. **Factura Comercial de Venta (con IVA):** O documento equivalente legal que certifique el costo real del producto (para personas naturales se acepta cuenta de cobro o comprobante de compra).
3. **Carta de Reclamación Formal:** Firmada por el remitente o destinatario titular del envío, indicando:
   * Valor exacto reclamado en pesos colombianos ($ COP).
   * Descripción detallada del acontecimiento.
   * Certificación bancaria (no mayor a 30 días) para el pago del reembolso.
4. **Registro Fotográfico Evidencial (En caso de avería o saqueo):**
   * Foto panorámica de la caja/empaque exterior donde se observe claramente la etiqueta de despacho.
   * Foto del daño interno y del estado del producto.
   * Foto de los materiales de amortiguación utilizados.
5. **Copia del Documento de Identidad / Cédula o RUT:** Según sea persona natural o jurídica.

---

## 5. REGLAS DE LIQUIDACIÓN DE INDEMNIZACIONES

El backend de la aplicación calculará la indemnización aplicando estrictamente la menor cifra entre el valor declarado en la guía y el valor comercial demostrado en la factura:

$$\text{Monto a Indemnizar} = \min(\text{Valor Declarado en Guía}, \text{Valor Factura Comercial Proveedor})$$

### Reglas Adicionales:
* **Flete Devuelto:** En caso de Pérdida Total o Avería del 100% causada por negligencia demostrada de TransLogix, la empresa reembolsará además el **100% del valor del flete cobrado**.
* **Deducible de Seguro:** Los envíos con valor declarado mayor a **$2,000,000 COP** están sujetos a un deducible del **5%** sobre el valor total indemnizable, asumido por la póliza de seguro de carga de TransLogix.
* **Tiempo de Respuesta Legal:** TransLogix emitirá un concepto formal de aprobación o rechazo en un plazo máximo de **quince (15) días hábiles** contados a partir del día siguiente al recibo de la documentación completa.

---

## 6. CAUSALES EXPRESAS DE EXONERACIÓN DE RESPONSABILIDAD

El motor RAG informará al cliente que TransLogix **NO ASUMIRÁ** indemnización económica bajo las siguientes circunstancias establecidas en el Art. 1031 del Código de Comercio:

1. **Vicio Propio o Naturaleza Deperidable:** Deterioro del producto por su propia fragilidad, descomposición natural o falta de estabilidad química sin fallas en el transporte.
2. **Deficiencia o Ausencia de Embalaje:** Mercancía despachada en bolsas endebles, sin amortiguación interna o en contravención al *Manual Operativo de Embalaje DOC-OPS-2026-03*.
3. **Fuerza Mayor o Caso Fortuito:** Eventos imprevisibles e irresistibles tales como derrumbes viales en carreteras nacionales, paros camioneros/bloqueos sociales, actos de terrorismo o desastres naturales.
4. **Falsedad o Inconsistencia en la Información:** Declaración fraudulenta del contenido o subdeclaración deliberada del valor comercial.

---

## 7. CASOS DE USO Y PROMPTS PARA ENTRENAMIENTO DE IA (RAG)

### Caso 1: Consulta de requisitos de reclamación por caja rota
* **Usuario:** *"Me llegó una caja aplastada y el producto adentro está roto, ¿qué debo hacer?"*
* **Respuesta RAG esperada:** *"Lamentamos lo sucedido. Para iniciar tu reclamación por avería, necesitamos que confirmes si dejaste anotación en la guía al recibir. Recuerda que tienes hasta 3 días hábiles si fue un daño no aparente. Por favor adjunta: 1) Fotos del empaque dañado y la etiqueta, 2) Fotos del producto roto, 3) Factura comercial de compra, y 4) Copia de tu guía TLX. Puedes cargar los documentos directamente en esta PWA para radicar tu ticket."*

### Caso 2: Reclamación fuera de tiempo legal
* **Usuario:** *"Hace un mes me entregaron un envío y hasta hoy lo abrí y vi que falta la mitad de las cosas. Exijo indemnización."*
* **Respuesta RAG esperada:** *"Entendemos tu inconformidad; sin embargo, de acuerdo al Art. 1028 del Código de Comercio y nuestras políticas de servicio, las novedades o saqueos no aparentes deben ser reportados dentro de los 3 días hábiles siguientes a la entrega. Al haber transcurrido 30 días, legalmente opera la presunción de entrega a satisfacción. Te invitamos a comunicarte con nuestro equipo legal si consideras presentar pruebas adicionales."*