# FF2 PS2: TRI2, paletas y colores preset

Investigación y reparación: 2026-10-02. Base de implementación:
`PZViewer_FF1_iluminacion_vertex.zip`; muestras recibidas en `pk2.zip`.

## 1. Evidencias de las dos muestras

`rch00.pk2` y `rch00Mono.pk2` miden **1.428.720 bytes** cada uno. Cada PK2
contiene un SGD 0x1050 en el offset absoluto 0x20, de longitud 0x15CCD0.
Los offsets siguientes son **relativos al inicio del SGD**, salvo indicación
contraria. Los hashes de entrada, geometría y recursos están fijados en
`tests/ff2/sample_baseline.json`.

Cabecera little-endian `<IBBHIIII>`:

| Campo | Offset | Valor observado |
|---|---:|---:|
| Versión | +0x00 | 0x1050 |
| mapflag / kind | +0x04 / +0x05 | 0 / 1 |
| Número de materiales | +0x06 | 29 |
| Tabla de coordenadas | +0x08 | 0x2C0 |
| Tabla de materiales | +0x0C | 0x95C0 |
| phead | +0x10 | 0xA9B0 |
| Número de bloques | +0x14 | 169 |
| Tabla de heads | +0x18 | 169 uint32 |

El resultado tiene 168 coordenadas/huesos, 307 mallas, 12.251 vértices y
8.669 triángulos. Las pruebas comparan posiciones, normales, UV, índices,
pesos, articulaciones y huesos con la salida anterior. Se registran dos
referencias de geometría: parser directo y servidor. El servidor ya aplicaba
dos correcciones de UV por material; esta reparación no las añade ni elimina.

### Diferencia normal/Mono

Hay **30.166 bytes distintos**, y todos pertenecen a los payloads RGB VIF de
los vértices. No hay diferencias fuera de esos payloads: materiales, índices,
coordenadas, texturas de color, paletas monocromas y UV son iguales.

`tests/ff2/rgb_locations.json` conserva las posiciones de 1.791 UNPACK obtenidas
en la inspección anterior a la implementación. Las pruebas leen directamente
los float32 de esos offsets y los comparan, vértice a vértice, con la salida.
El RGB de Mono tiene R=G=B; el del normal no. Ningún nombre de archivo se usa
para elegir o intercambiar sus datos.

## 2. TRI2 normal frente a MonotoneTRI2

Las unidades tienen cabecera de 16 bytes y un enlace relativo a la siguiente.
Se recorren las cadenas, no se buscan firmas dentro de los píxeles.

| Unidad | Offset | Categoría | Registros |
|---|---:|---:|---:|
| TRI2 de color | 0xA9F0 | 10 | 2 |
| MonotoneTRI2 | 0xC2080 | 13 | 27 |

Los dos registros normales cargan bloques PSMCT32 de VRAM de **64x2016** y
**64x896**, incluyendo tanto los índices de textura como las paletas normales.
Las 27 cargas posteriores contienen las alternativas monocromas. El lector
anterior procesaba ambas categorías indiscriminadamente: el segundo grupo
pisaba los CLUT de color.

La selección normal procesa únicamente categoría 10. `monochrome=True`
aplica explícitamente 10 y después 13. Se conserva el modo alternativo como
dato legítimo; no se borra del archivo. El lector valida count, padding,
DIRECT y sus límites, y reutiliza sin alterar el lector de transferencias GIF
hardware de `pz_gs_ff1` y las funciones de direccionamiento de `pz_gs_vram`.
La selección de unidades, identidad de recurso y diagnóstico son de FF2.

Obscura distingue estas categorías y deja MonotoneTRI2 desactivado en su
recorrido habitual; esto apoya la separación, no demuestra por sí solo todos
los estados gráficos del juego [1]. Las dos muestras y las pruebas de modo
explícito permiten reproducir y contrastar el efecto de cada carga.

## 3. Identidad de textura y paleta

Cada material ocupa 176 bytes. Su TEX0 de 64 bits está en material+112.
Se preserva el registro completo, tanto al parsear como al fusionar entradas.
Una coincidencia de TBP0 no basta: los mismos índices pueden usar otro CLUT.

Las 29 entradas de material contienen **27 TEX0 distintos** sobre **24 TBP0**.
Tres buffers están referenciados con paletas diferentes. El diccionario ofrece
un acceso TBP0 de compatibilidad, pero el visor, CLI y las fusiones utilizan la
identidad completa mediante `for_material`/`bind_textures`.

No se deduplican imágenes por igualdad de píxeles. Cada TEX0 se decodifica una
vez; referencias al mismo TEX0 sí comparten imagen. En estas muestras hay
24 imágenes con al menos un píxel R/G/B diferente y tres negras auténticas,
dos con alfa parcial. Los PNG conservan alfa cero y parcial.

Los formatos probados son PSMT8/PSMT4 con CLUT PSMCT32 y CSM1. Las variantes
no soportadas se notifican; no se afirman verificadas por el hecho de compartir
cabecera. Las funciones GS heredadas mantienen límites de dimensiones/memoria.

## 4. Colores por vértice

El lector FF2 mantiene la geometría del parser compartido intacta y recupera
los colores preset de mallas 0x12/0x32. El offset a primitivas/colores es un
int16 en unidad MESH+22, relativo a la unidad y expresado en palabras de
4 bytes. Los atributos se leen de UNPACK V3-32; en las muestras aparece 0x78
(V3-32 con máscara). NUM indica el número de vectores; cero significa 256.

Los controles VIF se avanzan con sus tamaños, no escaneando hasta encontrar
un entero que parezca un opcode. Los datos tienen que permanecer dentro de la
unidad y el número de colores debe coincidir con el de vértices producidos.
Valores no finitos, negativos, mayores de 255, UNPACK truncados o referencias
incorrectas producen un error con offset. Un patrón entero 1 en el primer
componente identifica la repetición i-2, no el float 1.0; hay pruebas sintéticas
para este marcador, pero ninguna repetición en las dos muestras FF2.

Para MODULATE, la operación GS usa un divisor 128 [2]. Los float del buffer
están en esas unidades, no unos normalizados y otros no:

`ganancia_RGB = RGB_almacenado / 128`

El parser previo elegía escala por el máximo de cada vértice, saturaba las
componentes en 1 y sustituía tiras totalmente negras por 0,85. Los tres
comportamientos se eliminan en la ruta preset FF2. El visor interpola y
multiplica las texturas por las ganancias sin una segunda luz genérica.
No se normaliza la longitud del vector RGB: eso destruiría la intensidad.

| Comprobación | rch00 | rch00Mono |
|---|---:|---:|
| Mallas / tiras | 307 / 1.791 | 307 / 1.791 |
| Vértices leídos | 12.251 | 12.251 |
| RGB completamente negro | 3.726 | 3.726 |
| 0 < máximo RGB <= 1, antes de dividir | 245 | 251 |
| Máximo RGB > 128 | 815 | 767 |
| Máximo de ganancia | 255/128 | 255/128 |

Hay 576 tiras negras completas en cada muestra. Son parte de una iluminación
almacenada que sí tiene datos en otras zonas, por lo que no se rellenan con gris.
Un modelo cuyo buffer entero sea cero se informa como `empty_preset_cache` y
no se activa automáticamente como iluminación válida. No se inventa una
iluminación alternativa. Otros tipos de malla conservan su comportamiento
previo y no reciben una falsa marca de prelight verificado.

## 5. Integración, exportación y diferencias con el motor

`viewer/server.py` combina las entradas SGD de un PK2 sobre una VRAM compartida,
conserva TEX0/CLUT al fusionar materiales y transmite `ff2_prelit` por modelo y
malla. Un SGD independiente con cargas GS propias tiene prioridad sobre los
archivos vecinos; sin cargas propias conserva la búsqueda externa previa.
`pz_export_cli.py` carga ahora geometría y texturas de todas las entradas FF2,
no únicamente geometría de la primera.

`app.js` activa los colores únicamente con datos validados de FF1 o FF2.
Las mallas FF2 preiluminadas utilizan material básico, evitando multiplicarlas
además por las luces de inspección de personajes. V sigue alternando la
modulación; F reencuadra. Se incrementa la versión del script para evitar una
copia anterior en caché. No se cambian gamma ni exposición globales.

El adaptador nuevo `pz_export_ff2.py` delega los demás juegos al adaptador FF1
anterior. Para FF2 utiliza una copia: base de material blanca, KHR_materials_unlit
en GLB y una selección genérica MASK/BLEND según alfa, como presentación
portable. La política de alfa **no es una reconstrucción de registros GS ALPHA**.

La especificación glTF exige que COLOR_0 esté entre 0 y 1 [3]. Solo al exportar
GLB/OBJ se acotan las ganancias; la fuente y el visor no se modifican. GLB
registra el número de vértices afectados y el límite de representación en
`extras.ff2_vertex_colors`. La textura PNG conserva sus bytes RGBA originales.
El exportador compartido agrupa mallas por recurso de textura; las pruebas
comparan los colores concatenados de cada grupo, no suponen una malla GLB por
cada proceso SGD. DAE/FBX mantienen la implementación previa.

Ni el material moderno ni el rasterizador CPU reproducen exactamente el
framebuffer, redondeos, estados de textura, niebla o luces dinámicas de PS2.
Estos ejemplos no aportan un `.lit` y no ha sido necesario generar luces para
recuperar sus colores almacenados. No se afirma una reproducción idéntica de
un fotograma original.

## 6. Pruebas y preservación

Se añaden pruebas de las dos muestras, buffers RGB directos, todos los datos
no RGB de normal/Mono, identidades de paleta, alfa negro legítimo, corrupción,
renombrado, fusiones, CLI, endpoints HTTP reales y GLB/OBJ/PNG. Los métodos
reales de app.js se ejecutan con dobles mínimos de Three/DOM; esas pruebas
no son un render GPU.

La ejecución de Chromium intentó abrir el servidor local y devolvió
`net::ERR_BLOCKED_BY_ADMINISTRATOR`. El informe de esa limitación está en
`docs/ff2/validation/browser-check.json`. No se completó una prueba interactiva WebGL.
Los renders CPU de `tools/inspect_ff2.py` sí proceden de los datos decodificados,
con cámara y exposición registradas.

Los 32 archivos listados en `tests/ff2/protected_sha256.json` permanecen idénticos
a la base: incluyen los parsers FF1, Xbox, Wii, FF3, el exportador compartido y
los ejemplos FF1. No se modifican binarios originales de ninguna muestra.

**Migración explícita de pruebas históricas:** los dos manifests SHA de FF1
se actualizan únicamente para `pz_pk2_ff2.py` y `pz_sgd_ff2.py`, archivos que
ahora se solicita modificar. Todos sus demás hashes se conservan. La antigua
prueba que exigía el comportamiento monocromo incondicional de FF2 se convierte
en una prueba de reproducción de ese mismo resultado con `monochrome=True`.
Las pruebas funcionales de color, huesos e iluminación de FF1 siguen pasando.
No se eliminan pruebas para esconder cambios de geometría.

## Fuentes primarias

[1] Obscura, `Model.cpp`, `SgSortUnitPrim` y `HandleTri2DataBlock`:
https://raw.githubusercontent.com/Mikompilation/Obscura/master/ModelConverter/game/Model.cpp

[2] PCSX2, `tfx_fs.glsl`, función `tfx`: producto C*T/128 y saturación final:
https://raw.githubusercontent.com/PCSX2/pcsx2/master/bin/resources/shaders/opengl/tfx_fs.glsl

[3] Khronos, glTF 2.0, Meshes, COLOR_n (rango de componentes [0,1]):
https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#meshes

Consultadas el 2026-10-02. Se contrastaron estructuras/semántica; no se copiaron
algoritmos de normalización RGB de Obscura ni shaders GPL de PCSX2 al proyecto.
La evidencia de las muestras y los comandos de reproducción tienen prioridad
sobre generalizaciones a variantes no proporcionadas.
