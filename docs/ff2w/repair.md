# Reparación de MDLB/PPDB reciclados de Xbox en Project Zero 2 Wii

## Entrega y alcance

Trabajo sobre el archivo **PZ Viewer.zip** aportado en esta conversación,
no sobre el proyecto Xbox anterior. Se han reparado y probado las parejas
`ch000_bontage`, `ch000_doa` y `ch000_goth` de `examples`.

Los binarios originales no se han modificado. El arreglo de ejecución está
en `pz_core/ff2w/pz_mdlb_ff2w.py`; la CLI añade exclusivamente el enrutamiento
MDLB/PK2B. No se han editado los parsers PS2/Xbox, el exportador compartido,
el servidor, la interfaz WebGL ni el lanzador.

## 1. Causa de la geometría amontonada y del esqueleto roto

En estos archivos **ENOB+0x50 es una matriz inversa de enlace**, almacenada
para vectores fila. El lector anterior la trataba como transformación
global de reposo. Su cálculo de geometría buscaba las traslaciones en
[3,7,11], que son cero en esta representación; y el adaptador del esqueleto
utilizaba valores de la matriz inversa como si fueran posiciones globales.
Por eso las piezas aparecían cerca del origen y los huesos se alejaban del
cuerpo. Transponer únicamente no resuelve esa diferencia semántica.

La corrección no depende del nombre del archivo ni de recolocar las piezas
«hasta que se vea bien». Se reconstruye, por separado, la jerarquía desde
el cuaternión, escala y traslación locales almacenados en ENOB. En los 78
registros se verifica:

```text
World_desde_TRS · transpose(matriz_almacenada) ≈ Identidad
World_bind = inverse(transpose(matriz_almacenada))
```

Las matrices resultantes alimentan tanto TREV rígido como GIEW ponderado y
el esqueleto. Se conservan los padres originales, incluidos los que aparecen
después de sus hijos. Los IDs se resuelven como IDs, no como posiciones de
lista. La convención se decide para el rig completo, incluidos los huesos
sin traslación, y no se ejecuta el ajuste anatómico legacy sobre estos rigs.

En GIEW las posiciones **ya están ponderadas**. La transformación correcta
suma `R * posición_preponderada + peso * traslación`; volver a multiplicar
la posición por el peso deformaría las uniones. Se han contrastado todas
las posiciones de los tres archivos con un cálculo independiente.

También se evita introducir una escala 0.05 en la rotación de los huesos
verificados. El exportador Wii solo deshace ese factor en la ruta legacy
que realmente lo había aplicado. Geometría y joints comparten unidades y
centrado en cada vía de carga/exportación.

## 2. Causa de las franjas y colores incorrectos

El lector localizaba correctamente la cabecera de texturas en 0x80, pero
seguía sumando 0x40 a los offsets de píxeles. Además ignoraba el segundo
puntero de cada entrada: **sí conduce a una paleta dentro del PPDB**.

Se ha corregido la base común de los offsets, leído los descriptores de
paleta y decodificado RGB5A3 con su alfa. Los índices CI8 se reordenan
según tiles 8×4 en vez de leerse como filas lineales. Ahora quedan resueltas
las 27 imágenes base, incluidas las 12 CI8 que antes eran rampas grises.

Se elimina asimismo la asociación especulativa de las imágenes 0→9 y 7→10
como máscaras: solo se utilizan relaciones declaradas por ETAM. No se
sustituyen datos por texturas o geometría de otras plataformas.

## 3. Resultados medidos

| Modelo | Shapes | Grupos/mallas | Vértices del pool | Triángulos | Huesos | Imágenes base |
|---|---:|---:|---:|---:|---:|---:|
| ch000_bontage | 19 | 46 | 6411 | 8582 | 26 | 9 |
| ch000_doa | 22 | 71 | 6624 | 8450 | 26 | 10 |
| ch000_goth | 19 | 54 | 6508 | 8454 | 26 | 8 |

El pool cuenta las posiciones antes de duplicarlas en costuras/materiales;
no es el recuento de vértices de un GLB agrupado o expandido. La topología,
UV, joints y pesos se comparan con la salida original: no se han quitado
caras para disimular la deformación.

Error máximo de la prueba independiente TRS×inverse-bind:

| Modelo | Residuo máximo absoluto |
|---|---:|
| ch000_bontage | 0.0001999550 |
| ch000_doa | 0.0001856328 |
| ch000_goth | 0.0001853970 |

La tolerancia es 0.0005, para la discrepancia de redondeo de los campos
originales. La identidad al multiplicar la matriz inversa directamente
por su inversión numérica es mucho más precisa, pero no sustituye a esta
comprobación independiente.

## 4. Verificación incluida

**71 tests, sin fallos** en el entorno de entrega. Registro completo en
`validation_wii/test_run.txt`. Se vuelven a ejecutar desde una extracción
nueva del ZIP antes de entregarlo.

Las pruebas comprueban estructura, límites y referencias de los campos
modificados; todos los vértices reconstruidos; padres y transformaciones
de los 78 huesos; índices y pesos; y los píxeles CI8 mediante una fórmula
escalar de direcciones distinta del reshape usado por el decoder. Además
se recorren los 65536 valores RGB5A3 posibles. Los 15 CMPR conservan el
resultado del decoder anterior alimentado desde el offset corregido.

Se abren los GLB generados, se leen sus buffers y se reconstruye su árbol
de nodos. La piel en reposo reproduce las posiciones; una traslación de
prueba en el joint de la cabeza produce el desplazamiento esperado según
los pesos, sin deformación explosiva. Se comprueba que exportar no modifica
el modelo cargado ni vuelve a escalar sus rotaciones.

Las pruebas HTTP arrancan el servidor real en un puerto local temporal,
cargan los tres archivos y descargan GLB y ZIP de OBJ+PNG. Se prueba también
la CLI en procesos separados. Los archivos estáticos se sirven correctamente;
esto **no equivale a haber ejecutado el frontend WebGL**.

La prueba de preservación verifica **36 archivos originales idénticos por
SHA-256**: los otros 19 archivos de `pz_core`, los 9 archivos del servidor y
frontend, los 6 ejemplos y los 2 lanzadores. El manifiesto está en
`tests/protected_sha256.json`.

Para la ruta nativa se crean tres fixtures sustituyendo únicamente las
matrices de los ejemplos por matrices globales en el layout legacy. Su
salida se compara con hashes de datos obtenidos ejecutando el parser
original del ZIP. Se añaden fixtures pequeños de matriz nativa y PPDB con
base 0x40. **Son regresiones sintéticas, no assets nativos extraídos del juego.**

## 5. Cómo probarlo

Cierra la herramienta, extrae el proyecto completo en una carpeta nueva y
arranca `run_viewer.bat` como antes. En **Extra → Fatal Frame 2 Wii**, apunta
a `examples` y abre `ch000_doa.mdlb`. Conserva al lado `ch000_doa.ppdb`.
La tecla **B** activa el esqueleto y **F** reencuadra. Prueba también bontage
y goth. Reiniciar evita que un proceso anterior conserve el parser en memoria.

Desde la raíz del proyecto:

```bash
python -m unittest discover -s tests -v
python tools/inspect_wii.py examples --out inspection_wii --preview --textures
python pz_export_cli.py examples/ch000_doa.mdlb -o export_doa -f glb,obj --game ff2w
```

Los tests y la inspección utilizan NumPy y Pillow, ya usados por el parser.
No requieren pytest. Las pruebas HTTP necesitan poder abrir localhost.

El paquete de solo cambios debe aplicarse sobre **esta misma versión subida**,
con copia de seguridad; no está pensado para sobrescribir a ciegas otra
revisión que contenga ediciones posteriores.

## 6. Archivos modificados y nuevos

Modificados: `pz_core/ff2w/pz_mdlb_ff2w.py`, `pz_export_cli.py`, `README.md`,
`docs/ff2w/mdlb-format.md`, `docs/ff2w/ppdb-format.md`,
`docs/architecture/gltf-export-pipeline.md`, `docs/ff2w/pk2b-format.md`.
El último documento solo recibe una nota de alcance sobre las mediciones
nativas anteriores, no una revalidación de aquellas habitaciones.

Nuevos: este informe, `docs/ff2w/guide.md`, `tests/` con sus fixtures/oráculos,
`tools/inspect_wii.py`, `tools/wii_preview.py` y `docs/validation_wii/`
con JSON, PNG de diagnóstico y registros. Las funciones de otros juegos
no se han movido ni reescrito.

## 7. Límites que siguen abiertos

No había modelos **nativos de Wii** en la muestra. No se puede afirmar una
validación funcional de toda esa colección a partir de los fixtures. La
ruta nativa existente se conserva, pero una regresión visual con sus assets
reales sigue siendo una comprobación necesaria fuera de esta entrega.

El entorno bloqueó la navegación automatizada al frontend; los PNG incluidos
son renders CPU de los arrays reales, con z-buffer, texturas y esqueleto,
no capturas del juego ni de la ventana de PZViewer. Se inspeccionaron los
tres modelos y vistas del rig, además de los datos de los endpoints HTTP.

No se han añadido animaciones ANMB ni reproducido los shaders/estados GX
originales. Hay MRON y normales empaquetadas que el adapter todavía no
aprovecha; las normales se siguen calculando desde la geometría. Puede haber
diferencias visuales en pelo, transparencias y capas respecto al juego.

La interpolación CMPR conserva la aproximación anterior a DXT1; no se
certifica exactitud bit a bit con GX. Solo se entregan niveles base PPDB.
Los layouts mixtos, rigs con otras convenciones y atributos no presentes
en estos ejemplos no se consideran automáticamente resueltos.

La escala y el centrado entre la CLI y el viewport mantienen las políticas
explicadas en `docs/architecture/gltf-export-pipeline.md`. No se ha recalibrado el tamaño
físico de todos los assets ni probado cada exportador/importador externo.
