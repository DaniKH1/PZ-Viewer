# MPX de Xbox: estructura 0x1060 verificada

## Alcance y estado

> **Nota de estado.** Los archivos `examples/m000_miku4.mpx` y
> `examples/m000_miku5.mpx` que se analizaron aquí se han eliminado del proyecto.
> Este documento sigue siendo válido como **especificación de formato**: describe
> la disposición de bytes, no el contenido de unas muestras concretas, y el lector
> que lo implementa sigue activo. Lo que ya no puede reproducirse son los comandos
> de la sección final, porque apuntan a ficheros que no están.

Esta documentación sustituye la interpretación anterior. Describe los archivos
`examples/m000_miku4.mpx` y `examples/m000_miku5.mpx`, contrastados con sus XPR
homónimos. El lector activo para estos archivos es `pz_core/ff1x/pz_mpx_ff1x.py`.
No utiliza los parsers de MDL, PK2, PK3 ni SGD de PS2. De `pz_sgd_ff3` importa
únicamente las clases de datos del visor; no llama a sus funciones de lectura,
transformación o reconstrucción de geometría.

Se han reconstruido contenedor, comandos, buffers GPU, UV, índices, materiales,
grupos de fuentes, coordenadas y pesos. Se han contrastado las dos muestras y
los mipmaps contra un decodificador independiente. Esto NO significa que se
conozcan todos los campos del formato, sus variantes ni los estados gráficos
del ejecutable de Xbox. Las limitaciones se detallan al final.

## Correcciones respecto a la documentación anterior

La cabecera tiene 40 bytes antes de la tabla de bloques. Los campos `+0x10` y
`+0x14` señalan un buffer GPU y su longitud; allí están las UV que anteriormente
se daban por ausentes. `+0x1C` señala directamente la tabla de materiales. Las
entradas de la tabla de bloques son offsets completos de 32 bits, no offsets
inferiores a 64 KiB con supuestas banderas en su mitad alta.

La topología procede de los índices reales de triangle strips. No necesita
separación heurística por huesos, generación de quads ni triangulación por
proximidad. El último bloque no tiene por qué contener un comando de bounding
box: en estas muestras contiene geometría ponderada y debe recorrerse.

La documentación de XPR se ha separado en `docs/ff1x/xpr-format.md`. El recurso cero no es
una referencia a la cabecera, las superficies rectangulares no son texturas
cuadradas de otro formato y los selectores BC3 no son alfa de relleno.

## Convenciones

Todos los enteros y floats son little-endian. Salvo indicación contraria, los
offsets de esta página son relativos al inicio del payload 0x1060 de una entrada,
no al archivo MPX. Los informes JSON incluyen también `file_offset`, para
convertirlos a offsets absolutos sin ambigüedad.

## Contenedor MPX

| Offset | Tipo | Significado comprobado |
|---|---|---|
| `0x00` | u32 | Número de entradas; 10 en ambas muestras. |
| `0x04` | u32[3] | Ceros en las muestras. |
| `0x10` | descriptor + payload | Primera entrada. |

Cada descriptor ocupa 16 bytes: longitud del payload, tipo y dos campos cero.
Los tipos de todas las entradas examinadas son cero. El siguiente descriptor
está en `descriptor + 16 + longitud`. Las longitudes son múltiplos de 16.
Después de las diez entradas hay **cuatro palabras `0xFFFFFFFF`**. El lector
acepta también un contenedor que termine exactamente después del último
payload; no acepta basura silenciosa después de las entradas.

## Cabecera de entrada

| Offset | Tipo | Significado |
|---|---|---|
| `0x00` | u32 | Versión `0x1060`. |
| `0x04`, `0x08` | u32 | Campos desconocidos, cero en estas muestras. |
| `0x0C` | u32 | Cantidad de materiales. |
| `0x10` | u32 | Offset del buffer de vértices GPU. |
| `0x14` | u32 | Longitud en bytes de ese buffer. |
| `0x18` | u32 | Offset de la tabla de coordenadas. |
| `0x1C` | u32 | Offset de la tabla de materiales. |
| `0x20` | u32 | Offset del descriptor de pools fuente. |
| `0x24` | u32 | Cantidad de entradas de la tabla de bloques; 29. |
| `0x28` | u32[n] | Offsets de los bloques; cero indica bloque ausente. |

En las dos muestras hay 28 coordenadas, es decir, `block_count - 1`. Su tabla
empieza en `0xA0` y la de materiales en `0x1920`: `(0x1920-0xA0)/224 = 28`.
El lector conserva este perfil de estructura y rechaza disposiciones que no
pueda interpretar sin conjeturas.

En la entrada 0 de miku4, el buffer GPU empieza en `0x192F0`, mide `0x10420`
bytes y acaba exactamente en el final del payload, `0x29710`. En miku5 empieza
en `0x17DE4` y mide `0xF720`; quedan 12 bytes de alineación después del buffer.

## Materiales: registros de 0x90 bytes

| Offset del material | Tipo | Interpretación |
|---|---|---|
| `0x00` | float4 | Difuso. |
| `0x10` | float4 | Ambiente. |
| `0x20` | float4 | Especular. |
| `0x30` | float4 | Emisión. |
| `0x40` | float | Exponente/potencia; 60 en las muestras. |
| `0x44` | u32 | Desconocido. |
| `0x48` | u32 | Índice del recurso XPR; `FFFFFFFF` significa sin textura. |
| `0x4C` | char[32] | Nombre de origen, normalmente terminado en `.tm2`. |
| `0x6C..0x8B` | bytes | No interpretados como parámetros gráficos. |
| `0x8C` | u32 | Banderas; significado gráfico no determinado. |

El nombre `.tm2` es una etiqueta del material, no una instrucción para leer
TIM2 de PS2. La asociación se realiza exclusivamente por el índice `+0x48`.
En miku4, material 0 de la entrada 0 → recurso XPR 0. En miku5, ese primer
material → recurso 22. No existe una correspondencia universal material 0 →
textura 0 ni se necesitan coincidencias aproximadas de nombres.

## Cadenas de comandos

Cada bloque se recorre desde el offset de la cabecera. Una palabra `u32 == 0`
es un terminador de **cuatro bytes**, no un comando completo. Para el resto,
`+0x00` contiene la longitud del comando y `+0x04` su categoría. El siguiente
comando está en `actual + longitud`.

| Categoría | Longitud | Contenido observado |
|---|---:|---|
| 3 | 16 | En `+8`, coordenada; en `+12`, modo 0 o 1. |
| 4 | 144 | Identificador de coordenada y ocho float4 de límites. |
| 2 | 12 | En `+8`, índice local de material. |
| 1 | Variable | Cabecera de geometría e índices. |
| 0 | Variable | Geometría, índices y pares de índices a los pools fuente. |

La categoría 0 no implica por sí sola dos pesos: también hay geometría fuente
única. El bloque 28 utiliza coordenada de modo 1 y geometría ponderada sin pasar
por un comando 4. Los límites se conservan como datos de diagnóstico; su
semántica exacta de culling no se implementa.

## Geometría y buffer GPU

| Offset del comando | Tipo | Contenido |
|---|---|---|
| `0x0C` | u32 | Offset **en bytes** dentro del buffer GPU. |
| `0x14` | u32 | Número de vértices. |
| `0x1C` | u32 | Offset de índices relativo a la entrada; coincide con `comando+0x38`. |
| `0x24` | u32 | Número de índices; no es el número de vértices. |
| `0x2C` | u32 | Declaración observada: 0, 1, 4 o 5. |
| `0x30` | u32 | `FFFFFFFF`, significado no atribuido. |
| `0x38` | u16[n] | Índices del triangle strip. |

Los campos intermedios desconocidos se incluyen en los informes. Declaración 0:
`float3 posición + float3 normal`, stride 24. Declaración 1: lo anterior seguido
de `float2 UV`, stride 32. Declaración 5: el mismo stride 32 y fuentes ponderadas.
Declaración 4: fuentes ponderadas con stride 24 y sin UV; aparece en
`m000_miku.mpx` y `m000_miku2.mpx`, donde cinco y cuatro mallas respectivamente
se decodifican sin pares de UV. Solo se reconocen los bits 0 (UV) y 2 (ponderado),
de modo que el parser rechaza cualquier otro bit en lugar de adivinarlo: un
registro leído con el stride equivocado todavía produce flotantes plausibles.
No se redondean offsets a múltiplos de 32: un buffer puede mezclar ambos strides.
La suma ordenada de los intervalos referenciados cubre exactamente cada buffer
GPU de las veinte entradas examinadas.

Para el strip, el triángulo terminado en índice i usa `(i-2,i-1,i)` cuando i es
par y `(i-1,i-2,i)` cuando i es impar. Se omiten únicamente triángulos cuyos
índices se repiten; **su posición en el strip sigue contando para la paridad**.
No se ha observado reinicio mediante `FFFF`; se rechaza como índice fuera de
rango, en lugar de inventar una semántica de primitive restart.

Después de los índices, los comandos de categoría 0 contienen un par
`u16 fuente_posición, u16 fuente_normal` por vértice. Empiezan exactamente en
`index_offset + 2*index_count`, aunque esa dirección no esté alineada a cuatro
bytes. Solo se alinea el final del comando. Ejemplo miku4/entrada 0: comando
`0x1435C`, tabla de mapeo `0x14F76`.

Los índices de posiciones y normales son independientes. En todos los vértices
mapeados de ambas muestras, los primeros float3 de las fuentes coinciden
**exactamente** con posición y normal del buffer GPU. Las UV proceden siempre
del buffer GPU, nunca de ceros de relleno ni de una malla de PS2.

## Pools fuente y tablas de grupos

El descriptor tiene 64 bytes. Su primera palabra vale 3. Dentro de las palabras
observadas: `[2]` apunta a posiciones únicas, `[3]` a normales únicas, `[10]` a
posiciones ponderadas, `[11]` a normales ponderadas y `[12]` a los grupos
ponderados. `[1]`, `[5]` y `[9]` valen 4. Los otros campos observados son cero;
no se les asigna un significado no demostrado.

Las fuentes únicas ocupan 16 bytes por registro: float3 y un cuarto float. Las
ponderadas ocupan 32 bytes tanto para posiciones como para normales:

| Offset | Tipo | Contenido |
|---|---|---|
| 0 | float3 | Primera copia local. |
| 12 | float | Peso de la primera copia, de 0 a 1. |
| 16 | float3 | Segunda copia local. |
| 28 | u8 | Primer hueso. |
| 29 | u8 | Segundo hueso. |
| 30 | u16 | Cero en las muestras. |

No se dividen los pesos por 255. Tampoco se interpreta un grupo de fuentes
únicas como si necesariamente incluyese dos huesos.

Hay dos tablas consecutivas por clase de fuente, primero posiciones y después
normales. Cada tabla comienza con un u32 de cantidad de grupos. Cada grupo
ocupa ocho bytes: para fuentes únicas, `u32 hueso, u32 cantidad`; para ponderadas,
`u16 hueso0, u16 hueso1, u32 cantidad`. Las cantidades suman el número exacto de
registros de cada pool. Además, los IDs de los grupos ponderados coinciden con
los bytes de huesos de **todos** sus registros en ambas muestras.

Los grupos únicos, cuando existen, preceden inmediatamente a los ponderados,
o al primer bloque cuando no hay fuentes ponderadas. No tienen un puntero
explícito en el descriptor examinado. El lector resuelve su inicio mediante
contadores y tamaños exactos de los pools, con una búsqueda estructural acotada
al tamaño máximo admitido de tablas de grupos. Exige una única solución: no
escanea floats para escoger una geometría que parezca plausible. Por ejemplo,
en la entrada 3 hay 86 posiciones únicas y **82** normales, seguidas de 24 bytes
de tablas. Contar todos los bytes hasta el siguiente bloque como normales
incluía erróneamente metadatos.

## Coordenadas, pose y exportación del esqueleto

Cada registro de coordenada ocupa 224 bytes: matriz de 16 floats en `+0`, dos
matrices adicionales en `+64` y `+128` (cero en las muestras), cuatro floats en
`+192` y padre i32 en `+208`. Hay referencias a padres posteriores en la tabla;
no se presupone que el padre tenga un índice menor.

Los tres primeros floats de `+192` son los ángulos que permiten reconstruir la
base de bind pose. Para vectores fila se utiliza:

`R = Rx(-x) · Ry(-y) · Rz(-z)`

La traslación procede de los componentes 12, 13 y 14 de la primera matriz. Para
vectores columna, la rotación equivalente es `Rz(z) · Ry(y) · Rx(x)`. Las bases
3x3 escaladas de la primera matriz se conservan para inspección, pero ni su uso
directo ni la simple eliminación de escala reproducían las copias ponderadas.
No se atribuye todavía la función de esas matrices al código del ejecutable.

`p_mundo = peso*(p0·R0+t0) + (1-peso)*(p1·R1+t1)`

Las normales se transforman como direcciones con sus propios índices y pesos,
sin traslación, y después se normalizan. Los grupos únicos dan el hueso de sus
fuentes; la geometría sin tabla de mapeo usa la coordenada activa del comando.

La diferencia máxima entre las dos copias ponderadas transformadas es
0,00149913 unidades en miku4 y 0,00147383 en miku5. No se desplazan vértices
para forzar esa coincidencia. Los dos ejemplos se validan con la misma fórmula.

Miku4 contiene dos poses de coordenadas distintas entre sus entradas; miku5,
una. Solo se comparten esqueletos exactamente iguales después de decodificarlos.
Se añade una raíz común para que el skin exportado tenga un ancestro común:
57 nodos de hueso en miku4 y 29 en miku5, incluyendo esa raíz auxiliar.

## Integración y garantías de aislamiento

El visor despacha `.mpx` y `.xpr` directamente a `parse_xbox_asset`. La ruta MDL
antigua quedó fuera de este cambio desde el principio: `pz_mdl_ff1x.py` ya se
había borrado y `pz_sgd_ff1x.py` se eliminó después por quedar huérfano — el
formato 0x1060 que describía este documento lo lee hoy `pz_mpx_ff1x.py`.
La CLI incorpora `.mpx`. No se buscan MPK, modelos PS2 ni variantes numéricas
como sustitutos de geometría. Si las copias ponderadas discrepan de su bind pose
por más de 0,02 unidades, el lector puede consultar el MPX base sin sufijo
numérico en la misma carpeta (por ejemplo, `m000_miku2.mpx` consulta
`m000_miku.mpx`). Solo usa su esqueleto si todas sus entradas son coherentes y
coinciden el número de entradas, los huesos y la jerarquía; la geometría y los
materiales siguen viniendo del archivo seleccionado. En caso contrario se
conserva la lectura original y su advertencia. Solo se autoselecciona un XPR del
mismo nombre base; también se puede proporcionar un XPR explícito al API.

La caché JSON antigua no se reutiliza para MPX/XPR: no conservaba el estado de
exportación ni incluía el sidecar en la invalidación. Al abrir un XPR independiente
se limpia la selección de modelo anterior para evitar exportar otro personaje.

`pz_export_xbox.py` adapta únicamente los modelos marcados como Xbox: GLB conserva
las UV de origen superior izquierdo y OBJ invierte V al convenio de ese formato,
sin modificar el modelo en memoria. El exportador compartido y los parsers PS2
conservan sus bytes originales. Los materiales GLB reciben una política genérica
MASK/BLEND para no descartar el alfa; esta decisión de presentación no se confunde
con ingeniería inversa de los estados gráficos originales.

## Resultados y reproducción

| Muestra | Entradas | Mallas | Vértices | Triángulos | Materiales | Vértices ponderados | Texturas |
|---|---:|---:|---:|---:|---:|---:|---:|
| miku4 | 10 | 69 | 4396 | 5829 | 37 | 1386 | 32 |
| miku5 | 10 | 64 | 4291 | 5584 | 36 | 1326 | 30 |

Desde la raíz del proyecto:

```console
python -m pytest -q tests
python tools/inspect_xbox.py examples/m000_miku4.mpx -o xbox_check/miku4 --pixels --preview --export
python tools/inspect_xbox.py examples/m000_miku5.mpx -o xbox_check/miku5 --pixels --preview --export
python pz_export_cli.py examples/m000_miku4.mpx -o xbox_export -f glb,obj
```

Los informes de referencia y vistas de diagnóstico que vivían en
`docs/validation/` se eliminaron con esa carpeta; lo que queda es reproducible con
los dos comandos de arriba, que vuelven a generar el HTML y los PNG.
Los renders son un rasterizador independiente con UV y píxeles extraídos: no
son capturas del juego ni una emulación de sus shaders.

## Límites todavía abiertos

No se han reconstruido animaciones, shaders, culling, orden de pasadas, la
semántica de las banderas de material ni los campos desconocidos de cabeceras
y comandos. Tampoco se afirma compatibilidad con toda variante de MPX/XPR:
los diseños no admitidos producen errores con sección y offset.

GLB y OBJ se han probado estructuralmente. DAE/FBX no se han validado para esta
ruta Xbox. No hay muestras PS2 en el ZIP, por lo que la garantía sobre PS2 es de
integridad de código y aislamiento de rutas, no una batería funcional con juegos
PS2. El documento anterior contenía conclusiones incorrectas; las sustituidas
no deben trasladarse a los lectores legacy que se han dejado intactos.
