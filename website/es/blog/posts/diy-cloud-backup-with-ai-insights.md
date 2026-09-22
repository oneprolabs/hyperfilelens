---
layout: hfl-blog-post
title: "Dale un cerebro de IA a Kopia"
description: "La deduplicación y el cifrado de Kopia son sólidos, pero su CLI obliga a memorizar parámetros y KopiaUI no es mucho mejor: una vez respaldados, los datos acaban en una caja negra. HyperFileLens construye su Agent directamente sobre Kopia, reduce la configuración a unos pocos clics e integra un motor de IA (SourceLens) que lee las instantáneas directamente. Un recorrido práctico completo, desde el despliegue hasta hacerle preguntas a la IA sobre tus propias copias de seguridad."
author: "Ray Sun"
date: 2026-09-09T08:00:00+08:00
tags:
  - "IA"
  - "Copias de Seguridad"
  - "Kopia"
  - "Almacenamiento de Objetos"
  - "Código Abierto"
  - "HyperFileLens"
---
Kopia es un motor de copias de seguridad de código abierto ya maduro: unas **14.1k estrellas en GitHub** y licencia **Apache 2.0**. Ofrece almacenamiento direccionado por contenido, instantáneas incrementales, deduplicación, cifrado de extremo a extremo, y puede respaldar en S3, NAS o disco local. En pocas palabras: **hacer copias de seguridad de archivos de forma segura y eficiente es un problema que Kopia ya resolvió.**

Pero Kopia es un **motor de copias de seguridad**, no un producto completo de gestión de copias: no tiene el concepto de "varios hosts"; cada máquina ejecuta su propia CLI o KopiaUI, sin ver a las demás. Incluso gestionar varios repositorios desde una sola interfaz en una misma máquina es una petición abierta desde 2023 ([kopia/kopia#2976](https://github.com/kopia/kopia/issues/2976)).

**HyperFileLens no reinventa el motor de copias de seguridad: añade una capa de producto encima de Kopia.** Kopia sigue encargándose de la copia, la deduplicación, el cifrado, las instantáneas (Snapshot) y la restauración (Restore). HyperFileLens reúne varios hosts, destinos de almacenamiento, tareas e instantáneas en una sola consola web, y reduce la configuración a tres pasos: registrar un host, configurar la copia y la restauración, y empezar a respaldar. Unos diez minutos, sin tocar Repository, Policy ni parámetros de línea de comandos.

Además, HyperFileLens añade algo que Kopia nunca tuvo: IA. Extiende la cadena de **File → Snapshot** a **File → Snapshot → Search / Read / Reason → Answer**: en lugar de buscar a mano entre instantáneas y directorios, preguntas en lenguaje natural y la IA busca, lee y razona sobre los archivos originales (documentos, hojas de cálculo, presentaciones, imágenes, código), y devuelve una respuesta con su fuente. Siempre lee la copia de seguridad, nunca los datos en vivo de producción.

Así que HyperFileLens hace dos cosas: **usa Kopia para copias de seguridad fiables y usa IA para que esos datos vuelvan a ser útiles.** Este artículo recorre toda la cadena desde cero: registrar un host, configurar la copia y la restauración, generar una instantánea, conectar la IA y, al final, hacerle preguntas sobre tus propios datos respaldados.

## Qué necesitas

- Un host Linux que pueda ejecutar Docker (x86; 4 núcleos / 8GB de RAM bastan para uso a pequeña escala, 8 núcleos / 16GB de RAM recomendados para equipos más grandes; 100GB de disco), con salida a internet para llegar a la API del modelo; no necesita IP pública;
- Una cuenta de almacenamiento de objetos: cualquiera compatible con S3 sirve (Alibaba Cloud OSS, Huawei Cloud OBS, AWS S3, etc.);
- Un host del que hacer copia: Linux, Windows y macOS son compatibles;
- Credenciales de un modelo de lenguaje. DeepSeek-V4-Flash es una buena opción por defecto; para reconocer imágenes, añade el modelo multimodal más reciente de DeepSeek, **DeepSeek-V4-Flash-Vision-Exp** (ID del modelo: `deepseek-v4-flash-vision-exp`).

El flujo completo: **desplegar HyperFileLens → respaldar tus datos → obtener información de ellos.**

## Paso 1: Prepara el host e instala HyperFileLens

| Elemento | Requisito |
| --- | --- |
| Sistema operativo | Ubuntu 24.04 (x86_64) |
| CPU / RAM | 4 núcleos / 8GB para uso a pequeña escala; 8 núcleos / 16GB recomendados para equipos más grandes |
| Disco | 100GB (espacio de trabajo que usa la IA al procesar archivos en Insights; amplíalo si respaldas más datos) |
| Red | Salida a internet (para llegar a la API del modelo); no requiere IP pública |
| Puertos | 11442–11445/TCP (`11443` es la consola principal, `11444` el panel de administración de modelos); basta con abrirlos en la red interna |
| Dependencias | Docker Engine 24.0.0+, Docker Compose V2 2.20.0+ |

Conéctate por SSH, instala Docker y ejecuta el script de instalación. Si estás en China continental, el cuello de botella es el propio GitHub: el paso de `curl` tiene que apuntar a Gitee, porque ningún parámetro `--mirror` lo soluciona:

China continental:

```bash
curl -fsSL \
  https://gitee.com/oneprolabs/hyperfilelens/raw/main/deploy/online/install.sh \
  | sudo bash -s -- --mirror cn --yes
```

Resto del mundo:

```bash
curl -fsSL \
  https://raw.githubusercontent.com/oneprolabs/hyperfilelens/main/deploy/online/install.sh \
  | sudo bash -s -- --mirror global --yes
```

Al terminar, el instalador muestra dos URL y un correo/contraseña iniciales, usando la IP de la red local del host. Si la IP local es `172.30.164.250`, verás:

- `HyperFileLens · http://172.30.164.250:11443`
- `Platform Ops · http://172.30.164.250:11444`

<figure>
  <img src="/images/diy-cloud-backup-with-ai-insights/install-complete-terminal.webp" alt="Salida del terminal al terminar el script de instalación, con las URL de acceso de HyperFileLens y Platform Ops y el correo y la contraseña iniciales" loading="lazy" />
  <figcaption>Lo que muestra el instalador al terminar</figcaption>
</figure>

Abre la dirección del puerto `11443`, inicia sesión con ese correo y contraseña, y cambia la contraseña de inmediato. De paso, comprueba que la zona horaria coincide con la hora del sistema.

<figure>
  <img src="/images/diy-cloud-backup-with-ai-insights/console-overview-en.webp" alt="Página de inicio de la consola de HyperFileLens tras iniciar sesión, con la cadena de protección de datos: origen de producción, almacenamiento de destino y simulacro de recuperación" loading="lazy" />
  <figcaption>La página de inicio de la consola tras iniciar sesión</figcaption>
</figure>

## Paso 2: Configura la dirección de acceso externo (NAT) y el modelo de IA

Registrar hosts, añadir almacenamiento y generar los comandos de instalación del Agent usan más adelante la dirección de acceso externo de este host; el asistente de IA también necesita un modelo configurado para funcionar. Ninguna de las dos cosas forma parte del flujo de copia en sí, pero conviene hacerlas antes de añadir fuentes de datos: si las cambias después, tendrás que volver a registrar.

**Dirección de acceso externo**: si el host está en una nube pública, el instalador muestra la IP interna, pero la dirección accesible desde fuera es la NAT o la IP pública del proveedor; corrígelo primero. Abre `Platform Ops`, entra en **External Access** y pon la dirección pública real a la que se puede llegar. Si solo lo usas dentro de una red local, puedes saltarte este paso.

<figure>
  <img src="/images/diy-cloud-backup-with-ai-insights/platform-external-access-en.webp" alt="Página de configuración de acceso externo, con el campo de configuración, la dirección vigente y una dirección pública sugerida" loading="lazy" />
  <figcaption>En nube pública, apunta el acceso externo a tu IP pública</figcaption>
</figure>

**Modelo de IA**: sin salir de `Platform Ops`, ve a **AI Engine → AI Models** y haz clic en **Add AI Model**. DeepSeek aparece directamente como proveedor, sin pasar por otro gateway.

<figure>
  <img src="/images/diy-cloud-backup-with-ai-insights/add-ai-model-provider-en.webp" alt="Lista de proveedores al añadir un modelo de IA, con OpenAI, DeepSeek, DashScope (Qwen), Anthropic y otros proveedores principales" loading="lazy" />
  <figcaption>DeepSeek está directamente en la lista de proveedores</figcaption>
</figure>

Añade **DeepSeek-V4-Flash** (ID del modelo: `deepseek-v4-flash`) para texto; si necesitas reconocer imágenes, añade también **DeepSeek-V4-Flash-Vision-Exp** (ID del modelo: `deepseek-v4-flash-vision-exp`). La Base URL es `https://api.deepseek.com`; configura la API key y guarda cuando **Test Connection** sea correcto.

## Paso 3: Respalda tus datos: añade el directorio del host que quieres proteger

Ve a **Protection → Backup Wizard**, haz clic en **Add Source**, elige **Source Host** y selecciona Linux como sistema operativo (Windows y macOS funcionan igual).

<figure>
  <img src="/images/diy-cloud-backup-with-ai-insights/add-source-select-os-en.webp" alt="Página para añadir una fuente de copia, con Source Host seleccionado y Linux como sistema operativo de destino" loading="lazy" />
  <figcaption>Elige el sistema operativo y copia el comando de instalación</figcaption>
</figure>

Te dará un comando de instalación: cópialo en el terminal del host de destino y ejecútalo. De vuelta en el Backup Wizard, actualiza la lista de fuentes y confirma que el nuevo host aparece como **Registered** y **Online**.

<figure>
  <img src="/images/diy-cloud-backup-with-ai-insights/backup-source-registered-en.webp" alt="Lista de fuentes del Backup Wizard, con el host Linux recién registrado en estado en línea y registrado" loading="lazy" />
  <figcaption>Host registrado y en línea</figcaption>
</figure>

## Paso 4: Configura la copia de seguridad

1. Haz clic en **Create Backup Configuration**: un asistente de cinco pasos: Sources → Backup Policy → Target → Restore Plan → Review.

<figure>
  <img src="/images/diy-cloud-backup-with-ai-insights/create-backup-configuration-en.webp" alt="Asistente Create Backup Configuration, con los pasos Sources, Backup Policy, Target, Restore Plan y Review a la izquierda, y el árbol de directorios del host con la selección de rutas a la derecha" loading="lazy" />
  <figcaption>Create Backup Configuration: Sources → Backup Policy → Target → Restore Plan → Review</figcaption>
</figure>

2. El paso Target necesita un repositorio de almacenamiento de objetos: crea un Bucket y una subcuenta en la consola de tu almacenamiento (dale solo permisos de lectura, escritura y listado sobre ese Bucket) y obtén un Access Key / Secret Key. De vuelta en HyperFileLens, haz clic en **Add Object Storage Repository**, elige una plataforma (Huawei Cloud / Alibaba Cloud / AWS tienen ajustes predefinidos; para cualquier otra, elige **S3-Compatible Storage**), rellena el endpoint, la región, AK/SK, el bucket y el prefijo de objetos, y guarda y verifica. El Secret Key solo se muestra una vez: no lo dejes en ninguna captura de pantalla.

<figure>
  <img src="/images/diy-cloud-backup-with-ai-insights/add-object-storage-repository-en.webp" alt="Formulario Add Object Storage Repository, con Huawei Cloud, Alibaba Cloud, AWS y S3-Compatible Storage como plataformas predefinidas, y los campos de endpoint, región, access key y secret key" loading="lazy" />
  <figcaption>Elige cualquier plataforma de almacenamiento: los campos son casi los mismos</figcaption>
</figure>

3. Apunta Target a este repositorio, deja Backup Policy y Restore Plan con sus valores por defecto y guarda en Review.

4. Haz clic en **Backup Now** y espera a que el estado pase a **Succeeded**.

<figure>
  <img src="/images/diy-cloud-backup-with-ai-insights/backup-task-succeeded-en.webp" alt="Paso 3 del Backup Wizard, con las tareas de copia de dos hosts en estado Succeeded" loading="lazy" />
  <figcaption>Tarea de copia terminada, estado Succeeded</figcaption>
</figure>

El coste de almacenamiento es prácticamente despreciable (el almacenamiento estándar de Alibaba Cloud OSS ronda los $0.017/GB al mes). El tráfico de salida solo se produce al restaurar: completo la primera vez e incremental después, así que ya de por sí es pequeño; y las preguntas a la IA leen la copia ya restaurada, por lo que no generan otra descarga.

## Paso 5: Pregúntale a la IA y comprueba el resultado

La tarea de copia muestra **Succeeded**: no hace falta verificarla por separado. Que la IA saque la respuesta correcta de la instantánea es la prueba más directa de que funciona. Si realmente necesitas restaurar algo, haz clic en **Restore** y sigue el flujo; no lo cubrimos aquí.

Ve a **Insights → AI Copilot**: verás tu historial de chats a la izquierda. Haz clic en **New Chat**.

Elige una fuente de datos y una instantánea, y añade los archivos que quieres analizar. Admite PDF, DOCX, PPTX y XLSX, y reconoce tanto las imágenes dentro de los archivos como los archivos de imagen sueltos; trabaja sobre la instantánea de la copia, no sobre los datos en vivo de producción. (Por eso en el paso 2 añadimos un modelo multimodal: hace que el reconocimiento sea más preciso.)

Pon el tipo de análisis en **Knowledge Q&A** y la privacidad de datos en **Public Data Gateway** (el gateway propio de la plataforma, sin nada adicional que desplegar).

<figure>
  <img src="/images/diy-cloud-backup-with-ai-insights/ai-copilot-new-chat-analysis-en.webp" alt="Configuración de un nuevo chat en AI Copilot, con el tipo de análisis Knowledge Q&A (recomendado) y la privacidad de datos Public Data Gateway, junto a un panel de resumen con la fuente de copia y la instantánea elegidas" loading="lazy" />
  <figcaption>Knowledge Q&A como tipo de análisis, Public Data Gateway como privacidad de datos</figcaption>
</figure>

Haz clic en **Start Chat** y, cuando esté listo, pregunta. Supongamos que has respaldado una copia de *Viaje al Oeste*; podrías preguntar: "Algunos demonios quieren comerse la carne de Tang Sanzang y otros quieren casarse con él: ¿en qué se diferencia lo que busca cada uno?". La IA responde directamente a partir del archivo respaldado, citando la fuente.

<figure>
  <img src="/images/diy-cloud-backup-with-ai-insights/ai-copilot-answer-en.webp" alt="Interfaz de chat de AI Copilot respondiendo a una pregunta sobre un documento respaldado con una respuesta estructurada y una tabla que compara dos categorías, citando el archivo de origen" loading="lazy" />
  <figcaption>Pregunta directamente sobre tu copia de seguridad: la respuesta cita su fuente</figcaption>
</figure>

## Kopia ya tiene cerebro

Kopia en sí no se ha tocado: la deduplicación, el cifrado y la copia incremental funcionan exactamente igual que antes. Lo que cambia es la capa de encima: ya no revisas el estado host por host en la línea de comandos, la configuración son unos pocos clics en lugar de parámetros memorizados, y los datos respaldados ya no están ahí esperando un desastre: la IA puede leerlos, interrogarlos y razonar sobre ellos siempre que quieras una respuesta.

De principio a fin (desplegar, respaldar y hacerle una pregunta a la IA) lleva una o dos horas. Después, programa las copias y olvídate de ellas.

## Código abierto

HyperFileLens tiene licencia Apache 2.0:

- Proyecto principal: <https://github.com/oneprolabs/hyperfilelens>
- Motor de IA, SourceLens: <https://github.com/oneprolabs/sourcelens>
- Issues: <https://github.com/oneprolabs/hyperfilelens/issues>

¿No quieres alojarlo tú mismo? La versión SaaS gratuita en <https://hyperfilelens.com> se salta el despliegue por completo: añade una fuente de datos y listo.

## Únete a la comunidad de código abierto de OneProLabs

Si te encuentras con algún problema, quieres contribuir o simplemente quieres platicar sobre el proyecto, encuéntranos aquí:

- GitHub: <https://github.com/oneprolabs/hyperfilelens>
- Twitter/X: [@oneprolabs](https://twitter.com/oneprolabs)

Si usas WeChat, también puedes unirte a nuestro grupo de WeChat: solo escanea el código QR de abajo con WeChat.

<figure>
  <img src="/images/backups-help-ai-understand-business/wechat-group-qrcode-only.webp" alt="Código QR del grupo de WeChat de la comunidad de código abierto de OneProLabs" loading="lazy" />
  <figcaption>Escanea con WeChat para unirte al grupo de OneProLabs</figcaption>
</figure>
