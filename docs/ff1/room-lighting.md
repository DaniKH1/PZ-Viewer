# FF1 / PS2: reparación de iluminación estática por vértice

## Base y alcance

Base exacta: `PZViewer_FF1_color_huesos.zip`, la última entrega corregida del proyecto proporcionado. Este cambio no parte de una versión antigua sin el arreglo de color. Las muestras son las diez originales de `examples`; no se editan ni se sustituyen. Fecha de verificación: 2026-10-02.

El objetivo es que la ruta FF1 interprete sus luces y genere colores localizados para los vértices de la habitación. No se modifica geometría, UV, normales, índices, pesos, huesos ni píxeles de las texturas. No hay condiciones especiales por nombre, hash, coordenadas o número de luces de la muestra.

## Diagnóstico reproducido

La ruta FF1 delegaba en un lector LIT compartido que interpretaba las categorías del proceso como tipos de luz. En `r000_genkan.lit`, los tres procesos son de categoría **11**; el subtipo está en otro campo. El lector anterior no obtenía puntos ni focos y mantenía un ambiente de sustitución `[0.18,0.18,0.18,1]`.

Además, los buffers RGB de las mallas preset de la habitación son cachés de prerender: los RGB ordinarios están a cero en disco. El lector anterior sustituía su resultado por un gris constante. Activar vertex colors no bastaba para recuperar luces que nunca se habían calculado. Desactivarlos mostraba las texturas completas, tampoco la iluminación local.

Se contrastaron estructuras y convenciones con Obscura, y el comportamiento de carga/materiales/prerender con el código FF1 recuperado en Himuro [R1–R3]. La implementación nueva es un módulo Python propio; no se ha incorporado una copia del motor ni se ha identificado a ojo dónde colocar lámparas.

## LIT: evidencia del archivo incluido

`r000_genkan.lit` tiene **336 bytes**. SHA-256: `7bea655e26f8887b4eef1f220f2acea2a5e3988184e7b8cd8fbd9121dc40796b`.

Cabecera little endian: versión `0x1050`, dos entradas en la tabla de bloques (`+0x14`), primera cadena en `0x50` (`+0x18`), segunda entrada nula. Los enlaces de las unidades son relativos a cada unidad, no direcciones de proceso utilizables en Python.

| Offset | Enlace | Categoría | Subtipo | Cantidad | Carga |
|---|---:|---:|---:|---:|---|
| `0x50` | `+0x90` | 11 | 1 | 4 | Puntos; 32 bytes por fuente |
| `0xE0` | `+0x40` | 11 | 2 | 1 | Foco; 48 bytes por fuente |
| `0x120` | `+0x20` | 11 | 3 | 1 | Ambiente; 16 bytes |
| `0x140` | 0 | 0 | 0 | 0 | Terminador |

En una unidad LIGHT: enlace `+0`, categoría `+4`, subtipo `+8`, cantidad `+12`, vectores desde `+16`. El lector admite también subtipo 0 (direccional: color y dirección) mediante pruebas sintéticas; esta muestra no contiene direccionales. Se recorre la primera cadena, conforme a `SgReadLights`, no se adivinan vectores buscando floats por todo el archivo. Los grupos repetidos sustituyen la lista del mismo subtipo; se conservan los registros de procedencia.

Valores redondeados para lectura; el parser conserva los floats originales:

| Datos en | RGB | Posición XYZ | Observación |
|---|---|---|---|
| `0x60` | 0, 0, 0 | 12.697, 52.018, −128.149 | Punto retenido, contribución nula |
| `0x80` | 0.12, 0.10, 0.07 | 122.833, 24.115, −99.326 | Punto cálido |
| `0xA0` | 0, 0, 0 | 82.843, 96.184, −32.629 | Punto retenido, contribución nula |
| `0xC0` | 0.26, 0.23, 0.16 | 86.656, 38.053, −203.405 | Punto cálido |
| `0xF0` | 0.14, 0.16, 0.23 | 83.331, 130.795, −4.696 | Foco azulado |
| `0x130` | 0.26, 0.21, 0.25 | — | Ambiente |

Destino del foco: `(83.633,62.782,−61.748)`. Su `position.w=19` proporciona el semiángulo en grados. `target.w=15` se conserva para inspección; no se usa como un supuesto cono interior. En los puntos, `diffuse.w=0` no apaga la fuente: elimina el recorte adicional por distancia. Los puntos RGB cero no se cuentan como fuentes efectivas, pero tampoco se borran del diagnóstico.

Las dos fuentes cálidas y el foco azul son datos demostrados. Asociarlos visualmente con velas y luz de ventana es una interpretación coherente con sus posiciones y colores, no una etiqueta semántica almacenada en LIT.

## Reconstrucción RGB y cálculo

Las mallas preset observadas usan RGB float VIF V3-32 y tipos `0x12` y `0x32`. El entero binario `uint32(1)` en el primer canal es un marcador de repetición del vértice dos posiciones atrás; **no** es el float `1.0`. En las tres partes renderizables se cuentan 408 marcadores. Una caché poblada conserva su término local; no se suma otra iluminación local sobre ella. Esta política se prueba con cachés sintéticas, ya que las del ejemplo están vacías.

El cálculo nuevo lee los materiales originales de 176 bytes y utiliza ambiente, difusión, especular, emisión y `primtype`. Normaliza las normales ya decodificadas sin alterar sus buffers. Para la vista estática se implementan los términos escalares de [R2] con potencias nativas 60 y 200 de [R3]. Esquema para una fuente local y material texturado:

```text
L = posición_luz - posición_vértice; d2 = dot(L,L)
D = RGB_luz * Diffuse_material * 192
q = max(D)/255; si q=0, q=1
f = clamp(max(0,dot(N,L)) * potencia * q / d2, 0, 1)
difuso = (D/q) * f
especular = RGB_luz * Specular_material * (43*sum(Specular_material))/q * f^8
cono = clamp(((max(0,dot(eje,L)))^2/d2 - cos(ángulo)^2)/(1-cos(ángulo)^2),0,1)
RGB_final = ambiente_material + emisión + direccional + caché_local/128
```

La fuente puntual tiene cono 1. El foco multiplica sus términos por el cono. Para material no texturado se usan las escalas correspondientes 255 y 86. Se respeta la saturación de la caché y el límite de modulación GS; no se fuerza todo RGB a 1 ni se introduce un radio lineal inventado. Se evita una singularidad en un vértice coincidente con la fuente.

El cálculo se realiza en el espacio nativo de la habitación de esta muestra, cuya raíz es identidad. No se añade la transformación global del mundo del juego. Una transformación común rígida/escala uniforme conserva el cálculo si se aplica coherentemente a luces, geometría y potencia. No se ha validado con mapas que requieran una raíz no identidad, escalado no uniforme o ensamblaje de varias habitaciones. Esas variantes necesitan muestras; no se declara compatibilidad universal.

Se reinterpreta únicamente la procedencia del RGB. Los procesos se emparejan por coordenada, tipo y material, conservando orden y validando los tamaños de strips. Si un formato no cuadra, se emite un diagnóstico en vez de adivinar colores o eliminar caras. Los cambios se confirman de forma atómica por SGD. Al combinar partes se agregan advertencias y se marca un resultado parcial cuando corresponde.

## Integración

`pz_core/ff1/pz_lighting_ff1.py` contiene lectura y cálculo. `pz_sgd_ff1.py` impide que el lector de FF3 procese el LIT de FF1 y llama al módulo específico una vez obtenida la geometría. No se modifica `pz_sgd_ff3.py` ni los lectores FF2, Xbox o Wii.

Servidor y CLI buscan el acompañante del mismo nombre para PK2 y SGD. La extensión admite `.lit` y `.LIT`; dos variantes simultáneas se consideran ambiguas. También se reconocen LIGHT embebidas en la primera cadena del SGD. Esta última ruta se prueba con un archivo sintético sin geometría, no con otra habitación real.

El servidor entrega `ff1_prelit` por modelo y malla, además de `diagnostics.ff1_lighting`. El frontend activa V únicamente si hay un resultado calculado. Usa material sin iluminación adicional para esas mallas, incluso en props SGD o superficies sin textura, evitando multiplicar otra vez por la iluminación genérica de la escena. Se conserva la configuración gráfica de otras familias. El panel explica si se cargaron luces, faltan o son inválidas. Se ha cambiado la versión de la URL de `app.js` para evitar conservar el script anterior en caché.

`pz_core/export/pz_export_ff1.py` adapta GLB y OBJ sin cambiar el exportador compartido. Duplica solo los datos de exportación necesarios, deja el material blanco porque su contribución ya está calculada, y marca GLB con `KHR_materials_unlit` [R4]. En GLB/OBJ se limita RGB a [0,1] para interoperabilidad; en el visor se conserva el intervalo PS2 hasta 255/128. Las exportaciones DAE/FBX no reciben una garantía nueva de preiluminación.

## Resultado medido en r000_genkan

| Comprobación | Resultado |
|---|---:|
| Mallas renderizables | 262 |
| Vértices | 9.667 |
| Triángulos | 5.665 |
| Huesos | 143 |
| Texturas | 26 |
| Vértices de caché preset | 9.655 |
| RGB de caché previamente poblados | 0 |
| Marcadores de repetición | 408 |
| Vértices con aporte puntual > 1e−9 | 7.225 |
| Vértices con aporte de foco > 1e−9 | 2.204 |
| Tripletas RGB diferentes, a 6 decimales | 5.926 |
| Vértices con algún canal > 1 | 53 |
| Advertencias de lectura/cálculo | 0 |

Los contadores de contribución pueden incluir aportes extremadamente tenues; no significan que todos esos vértices sean perceptiblemente brillantes. El rango mínimo RGB es aproximadamente `(0.026,0.021,0.025)` y el máximo `(1.9921875,1.7623574,1.2858207)`.

Entradas del PK2, en orden: 224 mallas/7.511 vértices; bloque de referencia sin mallas renderizables propias; 35 mallas/2.144 vértices; 3 mallas/12 vértices. Se conserva la cobertura del lector anterior; no se omiten caras para mejorar la imagen.

Las posiciones, normales, UV, índices, asociaciones de material, joints y pesos coinciden con los hashes de la base anterior; también coinciden los huesos completos y los bytes RGBA de las 26 texturas. La cámara, el punto de guardado y Mafuyu conservan colores y huesos sin cambios. Las matrices de pose y las paletas reparadas en la entrega anterior permanecen intactas.

## Validación y reproducibilidad

La batería incluye las 51 pruebas existentes más 40 nuevas: 91 en total con Node disponible. Cubre campos/offsets, enlaces fuera de límites y ciclos, truncamientos, NaN, conos, RGB y normales, repetición, ausencia/ambigüedad de acompañante, nombres arbitrarios, cambio solo del LIT, carga de SGDs independientes, conservación de geometría/texturas, exportación y ruta de interfaz.

Las seis pruebas Node ejecutan las funciones reales de `app.js` con dobles mínimos de Three y DOM. Comprueban material, activación por defecto, alternancia V, atributos GPU preparados, geometría sin textura, otros modelos y wireframe superpuesto. **No compilan shaders ni verifican píxeles WebGL.** El intento de abrir el servidor local con Chromium fue bloqueado por `ERR_BLOCKED_BY_ADMINISTRATOR`; no se intentó eludirlo. Las pruebas HTTP y CLI sí se ejecutan.

`tests/ff1/lighting_protected_sha256.json` protege 31 archivos: 21 módulos existentes ajenos al cambio principal y los diez binarios originales. `lighting_baseline.json` guarda huellas de la salida de la versión anterior. Las pruebas anteriores conservan sus propios manifiestos y no se han relajado para hacer pasar la reparación.

Reproducción desde la raíz:

```bat
python -m pytest tests/ff1 -q
node --check viewer/static/app.js
python tools/inspect_ff1_lighting.py examples/r000_genkan.pk2 -o inspection_lighting
python pz_export_cli.py examples/r000_genkan.pk2 -o exports_lighting -f glb,obj --game ff1
```

El render CPU de inspección utiliza la geometría y las texturas de la misma ruta del servidor, interpola UV y RGB con perspectiva y utiliza z-buffer. Tiene cámara y exposición idénticas en las variantes; no hay postprocesado para dar apariencia de reparación. Es una comprobación independiente, no una captura del visor o del juego. Su muestreo simplificado no reproduce todos los estados y filtros WebGL.

## Límites pendientes

Solo se ha proporcionado una habitación FF1 con LIT real. Las pruebas de renombrado y los datos sintéticos demuestran independencia del nombre y varias reglas, no validan todos los mapas del juego. Tipos de malla fuera de los preset `0x12`/`0x32` y el compacto `0x82` no reciben un bake nuevo. Los personajes mantienen su ruta anterior.

Se reconstruyen contribuciones estáticas, no linterna, animaciones de lámparas, cambios de escena, niebla, sombras proyectadas, oclusión entre objetos ni haces volumétricos. Tampoco se reproduce la selección dinámica de hasta tres fuentes por grupo ni el especular direccional dependiente de cámara; se evalúan las fuentes codificadas. La muestra solo tiene dos puntos efectivos y un foco.

El visor conserva su pipeline r128 existente. La exportación moderna GLB, aunque use material unlit, no emula el espacio de color ni la modulación byte a byte del GS. En 53 vértices se recortan multiplicadores superiores a 1 solo en la copia de exportación. No se promete una correspondencia pixel a pixel con el juego ni con cualquier programa externo.

## Referencias primarias consultadas

[R1] Obscura, rama `ff1`, `ModelConverter/game/Model.cpp`: disposición de materiales y procesos, eliminación de escala y lectura de RGB con repetición. La carga de geometría de Obscura no sustituye la etapa de luces reconstruida aquí.
`https://raw.githubusercontent.com/Mikompilation/Obscura/ff1/ModelConverter/game/Model.cpp`

[R2] Himuro, `src/graphics/graph3d/sglight.c`: `SgReadLights`, `SetMaterialData`, `SetMaxColor255`, `_CalcPointA/B`, `CalcSpotLight`, `SetPreRenderTYPE*`, `SelectLight`, `ClearPreRenderMeshData`. Contraste de reglas; no se copia el archivo completo al proyecto.
`https://raw.githubusercontent.com/Mikompilation/Himuro/main/src/graphics/graph3d/sglight.c`

[R3] Himuro, `src/graphics/graph3d/sglib.c`, `_TransPPower` y `_TransSPower`.
`https://raw.githubusercontent.com/Mikompilation/Himuro/main/src/graphics/graph3d/sglib.c`

[R4] Khronos, extensión glTF `KHR_materials_unlit`.
`https://raw.githubusercontent.com/KhronosGroup/glTF/main/extensions/2.0/Khronos/KHR_materials_unlit/README.md`
