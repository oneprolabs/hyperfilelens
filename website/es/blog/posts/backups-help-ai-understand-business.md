---
layout: hfl-blog-post
title: "Ayuda a la IA a entender los datos de tu empresa, empezando por tus copias de seguridad"
description: "De recuperar datos a redescubrir su valor: el proyecto de código abierto HyperFileLens se salta por completo el Embedding, la Vector DB y la pre-indexación, dejando que un agente busque, lea y razone directamente sobre los archivos originales. Ya está en producción en el soporte de ventas global, soporte de producto e ingeniería de HyperBDR y AGIOne."
author: "Ray Sun"
date: 2026-09-07T08:00:00+08:00
tags:
  - "IA"
  - "Agentes Empresariales"
  - "Copias de Seguridad"
  - "Datos No Estructurados"
  - "Código Abierto"
---
## De recuperar datos a redescubrir su valor

Llevamos años dedicados a la protección de datos, y la pregunta central siempre ha sido la misma: ¿cómo mantener los datos a salvo y recuperarlos rápido cuando algo falla?

Pero a medida que las empresas siguen acumulando más y más datos, ha surgido una pregunta nueva:

> Una vez que estos datos están respaldados de forma segura, ¿pueden hacer algo más que quedarse ahí esperando a que ocurra un desastre?

<figure>
  <img src="/images/backups-help-ai-understand-business/backup-data-sleeping-en.webp" alt="Una gran cantidad de datos respaldados —documentos, correos, chats, imágenes, código— permanece congelada en almacenamiento frío, inactiva la mayor parte del tiempo. Solo se abre cuando algo falla, y fuera de eso casi nunca se toca." loading="lazy" />
  <figcaption>La mayoría de los datos de backup pasan toda su vida dormidos — ¿de verdad la recuperación es todo lo que valen?</figcaption>
</figure>

La industria ya intentó responder esto antes. El CDM (Copy Data Management) es un ejemplo: permite reutilizar los datos respaldados para desarrollo, pruebas y análisis.

Pero el valor del CDM viene sobre todo de los datos estructurados. Las tablas, campos y relaciones de una base de datos ya están bien definidos, así que son naturalmente fáciles de consultar y reutilizar.

La información que de verdad más le importa a una empresa, en cambio, suele vivir en datos no estructurados: documentos, correos, registros de chat, notas de reuniones, código, imágenes. Ahí es donde está el contexto y la historia reales, y es justo el tipo de información que no encaja bien en una estructura fija.

Los pipelines tradicionales de RAG suelen requerir trocear, vectorizar e indexar los datos de antemano — un proceso que tiende a perder parte del contexto original por el camino.

Agentes como Codex y Claude Code apuntan a un enfoque distinto: no necesitan que se reestructure todo el código de antemano. Simplemente buscan, entienden el contexto sobre la marcha, y resuelven tareas complejas.

Eso nos llevó a hacernos una pregunta:

> Si un agente puede entender código directamente, ¿por qué el mismo enfoque no puede servir para extraer valor de toda esa información no estructurada de la empresa?

---

## La IA no puede leer los archivos de la empresa cuando le dé la gana

Para una persona, herramientas como Codex, Claude Code y WorkBuddy ya pueden leer archivos locales y analizarlos directamente.

Pero en cuanto entras en un entorno empresarial, lo difícil no es si la IA puede entender un archivo — es esto:

> ¿A qué datos puede acceder? ¿Quién tiene permiso? ¿Cómo evitas que los datos de un área del negocio se mezclen con los de otra?

Los datos de una empresa están repartidos entre laptops de empleados, carpetas compartidas, Office 365, Feishu, DingTalk, WeCom y repositorios de código.

No puedes simplemente juntar todo eso en un solo lugar, y desde luego tampoco puedes dejarlo abierto de par en par para cualquier agente que pase por ahí.

Lo que una empresa realmente necesita es una forma de dejar que la IA entienda esta información dispersa, manteniendo intactos los límites de datos y el control de acceso.

<figure>
  <img src="/images/backups-help-ai-understand-business/data-access-boundary-en.webp" alt="Los datos empresariales, repartidos entre computadoras de empleados, carpetas compartidas, Office 365, Feishu, DingTalk, WeCom y repositorios de código, pasan por una capa de límites de permisos y control de acceso (qué datos se pueden consultar, quién puede acceder) antes de que los datos autorizados lleguen al asistente de IA empresarial, que los usa para responder preguntas de negocio y apoyar la toma de decisiones — de forma segura, conforme a las normas, sin extralimitarse ni filtrar nada." loading="lazy" />
  <figcaption>Primero se definen los permisos, luego se deja que la IA entienda los datos</figcaption>
</figure>

---

## Convertir la IA en un asistente que la gente realmente pueda usar

Hoy lanzamos HyperFileLens, un nuevo proyecto de código abierto de OneProLabs.

El objetivo es simple: conectar la IA con los datos propios de tu empresa rápidamente, y entregarla a los usuarios finales como un asistente que puedan usar sin más — sin configuración, sin curva de aprendizaje.

Dos ejemplos de cómo se ve esto en la práctica:

- **Un asistente de soporte al cliente**: junta documentación de producto, presentaciones, notas de nuevas funciones, actas de reuniones, propuestas y archivos de proyecto anonimizados, y deja que responda preguntas sobre cómo funciona algo, qué cambió entre versiones, por qué se diseñó de cierta forma, o qué pasó en el pasado.
- **Un asistente para encontrar causas raíz**: combina incidentes anteriores, capturas de pantalla y código para ayudar a rastrear el origen de un problema en producción.

<figure>
  <img src="/images/backups-help-ai-understand-business/how-it-works-en.webp" alt="Los datos empresariales pasan por la plataforma de backup de código abierto HyperFileLens para crear una copia segura y aislada, que el agente de IA empresarial de código abierto SourceLens lee, busca, explora y razona — generando respuestas de soporte al cliente, una base de conocimiento empresarial y análisis de causa raíz basado en el código fuente, todo bajo control de acceso, sin que los datos salgan de su límite, totalmente auditable y bajo licencia Apache 2.0." loading="lazy" />
  <figcaption>HyperFileLens + SourceLens: el camino completo de los datos empresariales al conocimiento de IA</figcaption>
</figure>

Para un usuario común, no hay nada que aprender: nada de prompt engineering, ni necesidad de entender qué pasa por dentro. Solo abres el asistente y preguntas, como si le escribieras a un compañero de trabajo.

Construimos esta capa de "asistente" a propósito. Cuanto más capaces se vuelven los agentes de propósito general como Codex, Claude Code y WorkBuddy, más necesita el usuario común que alguien defina de antemano el alcance de los datos, los límites de la tarea y las capacidades disponibles.

El motor de IA detrás de HyperFileLens es SourceLens. Es un harness agent construido específicamente para consultar datos no estructurados — sin Embedding, sin Vector DB, sin pre-indexación. Busca, lee y razona directamente sobre los archivos originales. Cuando se topa con datos estructurados —una base de datos, una hoja de cálculo— SourceLens también puede consultarlos directamente a través de Skills, y combinar ambos tipos de resultados en una sola respuesta.

Antes construimos asistentes de conocimiento con Dify, FastGPT, Coze y RAGFlow. Lo que de verdad nos quitaba tiempo nunca fue crear el asistente en sí — era todo lo previo: trocear, indexar, preparar los datos. En nuestro caso, SourceLens ha reemplazado todo ese proceso.

Y esto no es una prueba de concepto: ya está corriendo en producción. SourceLens actualmente da soporte a ventas globales, soporte de producto e ingeniería de HyperBDR y AGIOne.

La información que antes estaba repartida entre documentación de producto, archivos de proyecto, documentación técnica y repositorios de código ahora es algo que los equipos de ventas, soporte e ingeniería pueden consultar directamente a través de un asistente de IA.

Entre el 18 de junio y el 7 de septiembre de 2026, SourceLens procesó **7.9GB** de documentos y código provenientes de **14** fuentes de datos, respondiendo **más de 1,100** preguntas a lo largo de **477** sesiones, y procesando en total más de **660 millones de tokens**.

Eso es uso real, y demuestra que los asistentes de IA empresariales ya están listos para flujos de trabajo reales, no solo demos. Al convertir el conocimiento del producto, la experiencia de los proyectos y el material técnico en algo que un asistente puede consultar, un equipo puede dar soporte a ventas globales, atención al cliente e ingeniería de forma más eficiente — sin tener que aumentar la plantilla al mismo ritmo.

En la práctica, SourceLens basa cada respuesta en los datos propios de la empresa y evidencia rastreable. Para las preguntas donde los datos de base son sólidos, el uso interno muestra alrededor de un **95%** de precisión.

SourceLens tampoco está atado a un solo modelo — elige el más adecuado según la tarea. Ahora mismo eso significa principalmente:

- **DeepSeek-V4-Flash** para comprensión y razonamiento de texto
- **Qwen3.6-Plus** para imágenes, capturas de pantalla y otro contenido multimodal

Según el uso real, las llamadas al modelo a lo largo de estos 81 días han costado en total alrededor de **140 dólares** — la prueba de que una empresa puede operar su propio asistente de datos con IA de forma continua sin que el costo se dispare.

---

## Prueba HyperFileLens tú mismo

HyperFileLens es de código abierto en GitHub (<https://github.com/oneprolabs/hyperfilelens>), y hay dos formas de usarlo:

- **SaaS**: la forma más rápida de probarlo — sin necesidad de montar un servidor.
- **Edición comunitaria**: bajo licencia Apache 2.0, totalmente autoalojada.

De cualquier forma, tú mantienes el control de tus propios datos — dónde se guardan, en qué entorno corren y qué modelo usan.

<figure>
  <img src="/images/backups-help-ai-understand-business/quickstart-flow-en.webp" alt="Un flujo de cuatro pasos para probar HyperFileLens: 1) prepara tus datos (documentos, imágenes, código, chats); 2) respáldalos en almacenamiento de objetos, donde HyperFileLens crea una copia segura y aislada; 3) entrégala a SourceLens para crear un asistente; 4) empieza una conversación y obtén respuestas basadas en los datos de tu empresa. Tanto la versión SaaS como la edición comunitaria admiten el flujo completo, y HyperFileLens y SourceLens están bajo licencia Apache 2.0." loading="lazy" />
  <figcaption>Cuatro pasos para convertir los datos de la empresa en conocimiento de IA utilizable</figcaption>
</figure>

### 1. SaaS

Si solo quieres probar HyperFileLens rápidamente, entra en <https://hyperfilelens.com> y usa la versión SaaS gratuita.

No hay que desplegar ningún servidor ni configurar ningún modelo. Una vez que te registres, solo necesitas:

- Una computadora, servidor o almacenamiento compartido con los datos que quieres proteger
- Tu propio almacenamiento de objetos

Consulta la documentación del sitio para la guía completa.

### 2. Edición comunitaria

Si quieres que tus datos, el sistema y el modelo corran por completo en tu propio entorno, despliega la edición comunitaria.

Está bajo licencia Apache 2.0, y puedes configurar:

- Tu propio almacenamiento de objetos
- Tu propio modelo o API
- Tu propio entorno de ejecución

Configuración recomendada:

- Ubuntu 24.04
- CPU / RAM: 4 núcleos / 8GB para uso a pequeña escala; 8 núcleos / 16GB recomendado para equipos más grandes

China continental:

```bash
curl -fsSL https://gitee.com/oneprolabs/hyperfilelens/raw/main/deploy/online/install.sh \
  | sudo bash -s -- --mirror cn
```

Resto del mundo:

```bash
curl -fsSL https://raw.githubusercontent.com/oneprolabs/hyperfilelens/main/deploy/online/install.sh \
  | sudo bash -s -- --mirror global
```

Todo el flujo, de la instalación al uso diario, se resume en esto:

> Prepara tus datos → respáldalos en almacenamiento de objetos → crea un asistente → empieza a hablar con tus datos.

---

## La IA está cambiando la forma en que trabajamos con los datos

Antes, una empresa normalmente tenía que definir una estructura antes de poder analizar cualquier cosa.

Pero gran parte de lo que tiene valor real vive en datos no estructurados — documentos, código, correos, registros de chat. Lo que cambia con la IA es que las máquinas ahora pueden entender este contenido directamente, y sacar a la luz las relaciones y el conocimiento que hay escondidos dentro.

<figure>
  <img src="/images/backups-help-ai-understand-business/ai-data-understanding-shift-en.webp" alt="Una comparación de cómo la IA entiende los datos: antes, primero se construía la estructura — crear una tabla, ingresar los datos, luego analizarlos. Ahora, la IA lee documentos, chats, código e imágenes directamente, revelando relaciones, conocimiento y perspectivas." loading="lazy" />
  <figcaption>Antes era primero la estructura. Ahora es primero la comprensión.</figcaption>
</figure>

Por eso también le prestamos mucha atención a la ontología: la idea de que el conocimiento de una empresa quizás ya no necesite definirse a mano desde cero — podría en cambio ser descubierto y construido por la IA a medida que se acumulan los datos.

HyperFileLens todavía no es una plataforma de ontología completa, pero está probando una dirección que creemos importante:

> Una vez que la IA puede entender los datos no estructurados de una empresa, ¿pueden finalmente redescubrirse y aprovecharse los activos de datos sobre los que ha estado sentada todo este tiempo?

Las copias de seguridad existían por una sola razón: recuperar los datos después de que algo saliera mal.

En la era de la IA, los datos que hay dentro de esas copias de seguridad podrían tener un segundo tipo de valor:

Ayudar a una empresa a redescubrir el conocimiento y las ideas que han estado escondidas en sus propios datos todo este tiempo.

---

## Únete a la comunidad de código abierto de OneProLabs

Si te encuentras con algún problema al probarlo, quieres contribuir, o simplemente quieres platicar sobre el proyecto, encuéntranos aquí:

- GitHub: <https://github.com/oneprolabs/hyperfilelens>
- Twitter/X: [@oneprolabs](https://twitter.com/oneprolabs)
