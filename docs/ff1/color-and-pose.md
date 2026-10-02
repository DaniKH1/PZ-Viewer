# FF1 PS2 — reparación de color e investigación de poses

Fecha de trabajo: 2 de octubre de 2026. Base: `PZ Viewer(1).zip` proporcionado por el usuario. Los resultados siguientes proceden de esos binarios; no se han comparado con una captura del juego ejecutándose.

## 1. Causa del blanco y negro

El lector anterior `pz_gs_vram.reconstruct_sgd_textures` buscaba cabeceras por pasos de 16 bytes y aceptaba tanto categoría 10 como 13. La categoría 10 carga la imagen de VRAM normal, incluidos los índices y las paletas de color. La categoría 13 carga **paletas monocromas alternativas** en las mismas direcciones. Al ejecutarlas después, el lector destruía el color que ya había recuperado.

No faltaban los canales RGB ni era necesario pintar las texturas. La evidencia reproducible es cargar solo la categoría 10, sin modificar los ejemplos: los mismos índices de textura recuperan colores. Con la opción explícita `monochrome=True`, el nuevo lector vuelve a producir, byte a byte en RGBA, la salida monocroma del antiguo.

La rama `ff1` de Obscura respalda la distinción: `Model::SgSortUnitPrim` procesa `TRI2`; la línea `case MonotoneTRI2` está comentada. `HandleTri2DataBlock` usa el contador de texturas, el padding y el tamaño de VIF DIRECT para avanzar, no una búsqueda dentro de los píxeles.

### Ubicaciones verificadas

Los offsets de la habitación son relativos a su primer SGD, no al inicio del PK2 exterior.

| Muestra | Unidad color (cat. 10) | Unidad monocroma (cat. 13) | Transferencias color / monocromas |
|---|---:|---:|---:|
| i000_play_camera1.sgd | 0x1670 | 0x9810 | 1 / 2 |
| f012_savepnt.sgd | 0x0930 | 0xAB10 | 1 / 3 |
| r000_genkan.pk2, SGD 0 | 0x93B0 | 0x91590 | 2 / 31 |

Las transferencias color son cargas PSMCT32 de regiones de VRAM; las superficies se consultan después con el TEX0 completo del material. El número de transferencias no es el número de texturas.

## 2. Nuevo lector FF1, sin reglas por archivo

`pz_core/ff1/pz_gs_ff1.py` sigue los punteros relativos de las unidades de proceso desde la tabla de bloques, incluido el bloque 0. Cada TRI2 contiene una cabecera VIF de 16 bytes y un paquete DIRECT cuyo tamaño delimita sus GIF tags. El lector interpreta registros A+D e IMAGE/IMAGE2, comprueba límites y admite una IMAGE dividida en varios tags.

Una transferencia de índices puede ir seguida de un `sceGsLoadImage` de paleta **sin otra cabecera VIF**. Se prueba con paquetes sintéticos PSMT8 y PSMT4. Los desplazamientos de destino usan DSAX en bits 32–42 y DSAY en 48–58; SSAX no se confunde con DSAX.

La reconstrucción normal omite la categoría 13. El modo monocromo existe únicamente como opción explícita de la API, para verificación y para quien necesite la variante. No se ha añadido un selector nuevo al frontend.

```python
from pz_core.ff1.pz_gs_ff1 import reconstruct_sgd_textures, bind_textures
images, uploads = reconstruct_sgd_textures(data, model.materials)
textures = bind_textures(images, model.materials)
# Variante deliberada, no predeterminada:
gray, _ = reconstruct_sgd_textures(data, model.materials, monochrome=True)
```

`FF1TextureMap` conserva el acceso legacy por TBP0 y añade `for_material`, basado en el TEX0 de 64 bits. Dos materiales que reutilicen el mismo buffer de índices con distinta CLUT no se confunden. El merge FF1 conserva el TEX0 alto, que el merger compartido perdía al clonar materiales posteriores.

La habitación se reconstruye con todos sus streams enlazados sobre una VRAM compartida. Se incluyen las superficies de los paneles tardíos y se elimina, solo en FF1, el filtro arbitrario de altura mínima de 16 píxeles. Una unidad sin cargas no fabrica imágenes negras a partir de VRAM vacía.

El backend respeta la selección explícita `game=ff1`, también para nombres desconocidos. Un SGD con unidades color y monocroma ofrece además una pista estructural para el modo automático; no es un identificador universal de todas las familias SGD. La selección explícita FF2/FF3 tiene prioridad y no se sustituye por esa pista.

## 3. Resultado de las muestras

| Archivo | Mallas | Vértices | Triángulos | Huesos | Texturas |
|---|---:|---:|---:|---:|---:|
| i000_play_camera1.sgd | 11 | 275 | 149 | 5 | 2 |
| f012_savepnt.sgd | 13 | 436 | 228 | 6 | 3 |
| r000_genkan.pk2 | 262 | 9.667 | 5.665 | 143 | 26 |
| m001_mafuyu.mdl | 128 | 5.153 | 4.155 | 28 | 23 |

Las **31 texturas de entorno** contienen canales RGB no idénticos tras la corrección. Antes todos sus píxeles eran grises. Las paletas originales pueden ser oscuras y de baja saturación: un píxel no gris es una medición, no una afirmación de que todas las imágenes sean visualmente muy coloridas.

No se ha recoloreado ni aumentado la saturación. La geometría, normales, UV, colores de vértice, topología, índices de huesos y pesos de las tres muestras de entorno conservan el mismo hash de datos que la lectura FF1 original. Los materiales sin TEX0 continúan sin textura; no se rellenan con imágenes aleatorias.

Los 23 PNG de Mafuyu conservan exactamente sus bytes RGBA originales. La geometría del personaje también es idéntica. La corrección de color no altera la ruta TIM2 que ya funcionaba en sus personajes.

## 4. Mafuyu: pose del archivo frente a fallo de matrices

Hay un solo personaje distinto en los ejemplos: Mafuyu, tanto en MDL como en MPK. No hay base para afirmar que se haya validado todo el elenco de FF1.

### Lo que ya está en el binario

El primer SGD contiene 28 registros SGDCOORDINATE de **224 bytes**. Su `matCoord` de 4×4 ocupa los primeros 64 bytes. Las dos matrices posteriores están a cero en esta muestra, por lo que no son una T-pose alternativa recuperable.

Las posiciones globales almacenadas del hombro y codo ya descienden:

| Hueso medido | Posición aproximada en el archivo |
|---|---|
| 1, hombro | (-2,868; 25,871; -1,210) |
| 3, codo | (-7,068; 23,365; -1,264) |
| 2, hombro opuesto | (3,203; 25,840; 1,233) |
| 4, codo opuesto | (7,359; 23,279; 0,737) |

La inclinación hombro-codo es aproximadamente 31 grados bajo la horizontal, calculada antes de convertir el sistema de coordenadas. Es una pose de reposo con brazos bajos, no una T horizontal que el parser haya deformado. Los índices anteriores son mediciones del informe y de una prueba: **no son una tabla aplicada por el parser**.

Obscura FF1 elimina la escala de `matCoord` y usa esas matrices como coordenadas globales; se ha conservado ese criterio ya presente en el parser. No se acumulan otra vez como si cada matriz fuera local ni se ponen todas sus rotaciones a cero.

`m001_mafuyu.acs` contiene accesorios, no se ha tratado como un archivo de animaciones. Los `.bwc` y `.clt` son paquetes de cambios de paleta. Esta reparación no implementa un reproductor de movimientos ni genera una postura horizontal nueva.

### Error real corregido

El helper compartido que orientaba el personaje giraba bien los vértices, pero negaba solo algunos coeficientes de la matriz ósea. Eso no equivale a una rotación: en la salida anterior 27 bases no eran ortogonales y 5 determinantes eran negativos.

Se ha añadido un helper **exclusivo de FF1**, sin modificar el helper compartido que usan otros formatos. Con matrices column-major y `C = diag(-1, 1, -1, 1)`, cada matriz global pasa a ser `C @ M`. Se aplica a todos sus coeficientes necesarios, igual que a los puntos y normales.

Los 28 huesos resultantes tienen determinante aproximadamente +1 y error máximo de ortogonalidad 4,37e-7. Sus posiciones globales y la forma visible del personaje no cambian respecto al archivo; lo reparado son las bases y su coherencia para rig/exportación. El mismo resultado se comprueba en MDL y MPK.

Los huesos sin nombre reciben `FF1_Bone_XX`, evitando la tabla anatómica de nombres FF3 del exportador compartido. No se inventan equivalencias anatómicas para un rig FF1 diferente.

El parámetro legado `export_t_pose` del exportador no sintetizaba una T-pose. Su ayuda CLI se ha aclarado; no se anuncia una funcionalidad de reposing que no existe. Para obtener una T estricta habría que aplicar una pose objetivo al esqueleto y al skin, como una operación aparte.

## 5. Integración y pruebas

La ruta del visor, la exportación HTTP y la CLI usan la reconstrucción FF1 corregida. La CLI también carga las partes renderizables posteriores del PK2, no solo su primer SGD. Las UV compactas 0x00/0x02 mantienen la convención que ya aplica el parser; no dependen de que el archivo renombrado siga llamándose `i000...`.

Se evita reutilizar la caché JSON sola al reseleccionar un activo FF1: la secuencia A → B → A restaura también el modelo de exportación, no deja B dentro de CURRENT_STATE.

**51 pruebas** comprueban las muestras, la recuperación de color, equivalencia exacta con la variante monocroma anterior, geometría y PNG intactos del personaje, packet bounds, PSMT8/4 con paleta sin VIF adicional, IMAGE partidas, offsets de destino, TEX0 con paletas distintas, ficheros renombrados, separación de parsers y archivos protegidos.

También se ejecutan cargas en el servidor HTTP real, exportaciones GLB/OBJ/ZIP de texturas y CLI para los cuatro recursos. Se leen de nuevo las imágenes embebidas en GLB. La jerarquía exportada de Mafuyu reconstruye las mismas matrices globales que el parser, y las inversas de enlace cumplen `world @ inverse_bind ≈ I`. Esto valida reposo y matrices; no es una prueba de animaciones originales del juego.

La batería se repite tras extraer el ZIP final en una carpeta nueva. Véase `docs/ff1/validation/work-report.txt`.

## 6. Archivos y aislamiento

Modificados:
- `pz_core/ff1/pz_sgd_ff1.py`: TEX0 completo en merge, pista de dispatch y convención UV compacta.
- `pz_core/ff1/pz_tim2_ff1.py`: entrada al lector nuevo de TRI2; la decodificación TIM2 existente se conserva.
- `pz_core/ff1/pz_mdl_ff1.py`: helper de orientación correcto y diagnóstico de pose almacenada.
- `pz_core/ff2/pz_pk2_ff2.py`: **solo se redirige un import** al lector GS legacy, manteniendo su lógica y su política de alfa anteriores. Esto evita que FF2 herede por accidente el cambio de modo de FF1.
- `viewer/server.py`: integración FF1, binding de materiales, selección explícita y estado al reseleccionar.
- `pz_export_cli.py`: color FF1, partes de PK2 y ayuda honesta sobre T-pose.

Nuevos: `pz_core/ff1/pz_gs_ff1.py`, `pz_core/ff1/pz_ff1_pose.py`, pruebas y referencias en `tests/ff1`, `tools/inspect_ff1.py`, esta documentación y `docs/ff1/guide.md`.

Los parsers Xbox MPX/XPR, Wii MDLB, SGD FF2/FF3, PK2 compartido, PK4, exportador compartido y fuentes del frontend no se modifican. Una prueba SHA-256 verifica 27 archivos protegidos, incluidos todos los ejemplos y los lectores compartidos relevantes. El manifiesto de entrega enumera los cambios respecto al ZIP de entrada.

## 7. Límites expresos

- No hay un archivo de puerta independiente en esta entrega; sí se recuperan los materiales de los paneles de la habitación. El arreglo de la estructura TRI2 es general, pero no se afirma haber probado cada puerta del juego.
- Solo se ha validado un personaje distinto. Otro personaje con un problema diferente requiere contrastar sus datos; no se fuerza una regla especial a todos los rigs.
- PSMCT32/PSMT8/PSMT4 y CLUT CSM1 con CPSM32 están soportados por esta ruta. Otros modos CLUT se diagnostican como no soportados; no se inventan sus colores. El lector no reproduce shaders, iluminación completa, cambios de estado GS por draw ni animaciones.
- Los renders de diagnóstico usan los datos decodificados y un rasterizador independiente. No son capturas del juego o del frontend WebGL.
- Se intentó abrir el frontend en Chromium/Playwright: la navegación local fue bloqueada por la política del entorno (`ERR_BLOCKED_BY_ADMINISTRATOR`). Por eso no se afirma una prueba visual interactiva WebGL. Los endpoints HTTP sí fueron ejecutados y verificados.
- No se compiló Obscura ni se utilizó su exportación como oráculo visual. Se consultó su código público como referencia y se contrastó la interpretación con los binarios aportados.

## Referencias primarias consultadas

- Obscura, rama `ff1`, `ModelConverter/game/Model.cpp`: `SgSortUnitPrim`, `HandleTri2DataBlock`, `BuildFF1NoScaleCoordinateWorld`, `CalculateBoneTransforms`.
  https://github.com/Mikompilation/Obscura/blob/ff1/ModelConverter/game/Model.cpp
- Obscura, `ModelConverter/game/GsTexture.cpp`: `UploadGsTexture`, campos de transferencia y paleta posterior a una imagen indexada.
  https://github.com/Mikompilation/Obscura/blob/ff1/ModelConverter/game/GsTexture.cpp
- Obscura, `ModelConverter/game/sgd_types.h`: SGDCOORDINATE, SGDTRI2FILEHEADER y descriptores de unidades de proceso.
  https://github.com/Mikompilation/Obscura/blob/ff1/ModelConverter/game/sgd_types.h

Rama consultada el 2 de octubre de 2026; puede cambiar posteriormente. El código de la corrección es una implementación Python basada en la lectura estructural y las pruebas descritas, no una copia completa del repositorio externo.
