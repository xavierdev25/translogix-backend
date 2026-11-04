## COMPETENCIA KODO TAKAI 2026 • ESPECIFICACIÓN OFICIAL DE RETO

## Dosier de Negocio, Operaciones y Requerimientos de IA

Caso de Estudio: TransLogix Colombia S.A.S. (Sector Transporte, Logística y Cadena de Suministro)

## 1. CONTEXTO DEL SECTOR Y FICHA CORPORATIVA

El sector logístico y de transporte de carga en Colombia opera bajo una geografía compleja (Cordillera de los Andes, corredores viales interurbanos Bogotá-Buenaventura, Bogotá-Costa Caribe) y un marco regulatorio estricto supervisado por el Ministerio de Transporte y la Superintendencia de Puertos y Transporte (Supertransporte). Las empresas de este rubro deben gestionar constantemente la variación de costos operacionales (peajes, combustibles, pólizas de seguro), así como cumplir con los tiempos de entrega pactados (SLA).

TransLogix Colombia S.A.S. es un operador logístico integral con más de 12 años en el mercado nacional. Estructura su operación bajo una arquitectura híbrida B2B (suministro a grandes cadenas y distribuidores) y B2C (paquetería de última milla para comercio electrónico).

|   | Razón Social / Comercial NIT / Registro Mercantil | TransLogix Colombia S.A.S. / TransLogix Express 901.684.329-8 / Matrícula 00385921 (Cámara de Comercio de Bogotá) |
| --- | --- | --- |
|   | Código CIIU Principal | 4923 (Transporte de carga por carretera) | 5210 (Almacenamiento y depósito) Calle 26 # 96J-15, Zona Franca Fontibón, Bogotá. Centros logísticos en Rionegro (Medellín), |
|   | Sede Principal & Hubs Flota Operativa (480 unidades) | Yumbo (Cali) y Malambo (Barranquilla). 120 Tractocamiones S3 (Carga Masiva), 180 Camiones C2/C3 (Intermunicipal), 130 Turbos/Vans (Última Milla) y 50 Furgones Termo con Cadena de Frío. |
|   | Métricas Clave de Operación | 45.000 despachos mensuales | 92.4% OTIF (On-Time In-Full) | Cobertura en 32 departamentos (+650 municipios). |

## 2. PORTAFOLIO DETALLADO DE SERVICIOS DE NEGOCIO

## A. Transporte de Carga Masiva y Semimasiva (FTL / LTL)

Movilización de contenedores (20' y 40') y carga suelta intermunicipal en tractocamiones y camiones de gran tonelaje. Incluye precintado de seguridad, póliza de cobertura por trayecto y monitoreo GPS 24/7 integrado al centro de control.

## B. Paquetería Empresarial y Mensajería Exprés (Última Milla)

Envíos de documentos, muestras comerciales y paquetes de hasta 50 kg con tiempos de entrega de 24 a 48 horas a nivel nacional, e integración con servicios Same Day (mismo día) en Bogotá, Medellín y Cali.

## C. Almacenamiento, WMS y Fulfillment e-Commerce

Gestión de inventarios bajo sistema WMS corporativo en bodegas de ambiente controlado y zonas francas. Servicios de recibo, Pick & Pack, etiquetado e integración con plataformas e-commerce (Shopify, WooCommerce, VTEX).

## D. Transporte con Cadena de Frío Certificada

Flota equipada con sistemas de refrigeración multitemperatura (-20°C a 8°C) y registrométros digitales para el sector farmacéutico, hospitalario, biológico y de alimentos perecederos.

## E. Logística Inversa y Gestión de Devoluciones

Recolección de mercancía devuelta en puntos de venta o domicilio del consumidor final, inspección de estado físico, re-empaque y consolidación para retorno al centro de distribución.


## 3. DIAGNÓSTICO PROFUNDO Y PROBLEMÁTICA DEL CANAL DE ATENCIÓN

Debido al crecimiento acelerado del comercio electrónico y de los contratos corporativos B2B, el canal de atención de TransLogix procesa actualmente más de 18,000 interacciones diarias a través de tres canales principales: WhatsApp Business API (60%), Web Chat (25%) y PBX Telefónico (15%).

## Puntos de Dolor Operativos y Financieros:

- Dispersión y Desactualización de la Información Operativa: La información sobre tarifas complejas, recargos por zonas de difícil acceso, restricciones de carga peligrosa (clases UN/IMO) y normativas del Ministerio de Transporte se encuentra fragmentada en manuales PDF de más de 120 páginas y tablas dinámicas en Excel. Los agentes humanos tardan un promedio de 4.5 minutos buscando una condición específica.

- Saturación del Canal por Consultas Repetitivas de Rastreo: El 55% de las interacciones diarias corresponden a clientes preguntando *"¿Dónde está mi guía?"*. A pesar de que existe un sistema TMS interno, la falta de una interfaz conversacional amigable obliga al cliente a recurrir a un agente humano para traducir los códigos técnicos de estado.

- Errores Recurrentes en la Cotización de Fletes: El cálculo tarifario requiere aplicar la fórmula de peso volumétrico vs. peso báscula real, más la sobretasa de seguro de mercancía (1% sobre valor declarado, min \$2.500 COP) y recargos por reexpedición. Los agentes cometen equivocaciones en el 12% de las cotizaciones manuales, generando pérdida de margen o reclamos del cliente.

- Ineficiencia en la Radicación de PQRS e Indemnizaciones: El Estatuto del Consumidor en Colombia (Ley 1480 de 2011) y el Código de Comercio (Art. 1028) establecen plazos estrictos para presentar reclamaciones por mercancía perdida o averiada (10 días hábiles). El 40% de las solicitudes se radican incompletas por falta de orientación inicial al usuario sobre los documentos obligatorios (Guía emitida, Factura comercial con IVA, Fotos del embalaje y Acta con salvedad).

## 4. REQUERIMIENTOS TÉCNICOS PARA LA COMPETENCIA KODO TAKAI (BACKEND IA + RAG + PWA)

El desafío principal para las universidades participantes en KODO TAKAI 2026 consiste en construir el backend de un agente de inteligencia artificial entrenado localmente mediante la técnica RAG (Retrieval-Augmented Generation) y su integración con una PWA (Progressive Web App) ligera y omnicanal.


## Especificación de Módulos a Desarrollar:

- 1. Motor de Ingesta Vectorial (RAG Pipeline): Indexar la documentación no estructurada (Manual de Operaciones, Normas de Empaque, Tarifarios y Códigos de Cobertura). Estrategia recomendada: Chunks de 512 tokens con solapamiento de 64 tokens, priorizando metadatos de categoría (ej: tipo: tarifa, zona: costa_caribe).

- 2. CalculadoraConversacional de Cotizaciones: La IA debe solicitar iterativamente Origen, Destino, Dimensiones (Alto, Ancho, Largo en cm), Peso real (kg) y Valor Declarado (\$ COP).

Peso Volumétrico (kg) = (Alto cm × Ancho cm × Largo cm) / 4000

Peso a Facturar = Máximo (Peso Real, Peso Volumétrico)

Flete Total = Flete Base (Origen, Destino, Peso Facturable) + Máximo (0.01 × Valor Declarado, \$2.500 COP)

- 3. Módulo de Rastreo Inteligente de Guías (TMS Integration): Al recibir una consulta con formato de guía (ej. TLX- 984021-CO), la IA debe consultar el endpoint de tracking del TMS y transformar estados técnicos como RELECTURA_HUB_FONTIBON a explicaciones en lenguaje natural: "Tu paquete ya ingresó a nuestro centro principal de clasificación en Bogotá y se encuentra programado para salir en la ruta nocturna hacia tu ciudad."

- 4. Asistente para Normativa de Cargas Especiales: Consultar la base RAG para informar si un producto requiere Hoja de Seguridad (MSDS), rotulado IMO de riesgo químico, o si pertenece a la lista de mercancías prohibidas (dinero en efectivo, joyas, explosivos).

- 5. Protocolo de Escalamiento Humano (Human-in-the-Loop): El agente de IA debe calcular un puntaje de confianza (Confidence Score entre 0 y 1). La PWA debe transferir la conversación inmediatamente a un asesor humano cuando se cumpla cualquiera de las siguientes condiciones:

- Confianza de la respuesta RAG inferior a 0.75.

- Reclamaciones o PQRS con valor declarado superior a \$5.000.000 COP.

- Detección de sentimiento fuertemente negativo o amenazas de demanda legal ante Supertransporte.

- Identificación de una cuenta corporativa clasificada como Key Account / VIP.
