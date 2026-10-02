# Entrega: ingeniería inversa MPX/XPR de Xbox

> **Nota de estado.** Informe de una entrega concreta. Los ficheros de `examples/`
> sobre los que se trabajó se han eliminado después, junto con `tests/` y
> `docs/validation/`. Los resultados numéricos que se citan aquí se midieron sobre
> esos cuatro ficheros y ya no son verificables desde el proyecto, aunque el
> análisis del formato sigue siendo correcto y está descrito en
> `ff1x-sgd1060.md` y `xbox-xpr.md`.

Se ha trabajado sobre el ZIP recibido, sin restablecer el proyecto a HEAD ni
sustituir archivos por una versión anterior. Los ejemplos originales se conservan.

## Resultado

| Pareja | Mallas | Vértices | Triángulos | Materiales | Texturas | Mips |
|---|---:|---:|---:|---:|---:|---:|
| m000_miku4 | 69 | 4396 | 5829 | 37 | 32 | 96 |
| m000_miku5 | 64 | 4291 | 5584 | 36 | 30 | 90 |

Se han corregido la base de offsets XPR, la interpretación del bitfield Format,
las dimensiones rectangulares y la decodificación BC3/alfa. El recurso cero se
conserva. El nuevo parser MPX lee las UV reales del buffer GPU, los strips
completos, los índices independientes a fuentes de posición y normal, las
tablas de grupos y la geometría ponderada del último bloque. La asociación
material-textura es directa por índice, sin búsquedas aproximadas de nombres.

## Pruebas ejecutadas

**56 pruebas pasan, 0 fallan.** Se han comparado las 186 imágenes mip con el
decodificador DDS de Pillow: todos los bytes RGBA coinciden, sin tolerancia.
Los buffers GPU quedan completamente cubiertos en las veinte entradas y las
copias GPU/fuente de posiciones y normales coinciden exactamente. El máximo
residuo de las dos copias ponderadas transformadas es inferior a 0,0015 unidades.

También se han validado índices, pesos, jerarquías, winding frente a normales,
archivos truncados, referencias inválidas, declaraciones no soportadas y análisis
paralelos sin estado global compartido.

Se han ejercitado por HTTP real el HTML del visor, la carga MPX de ambas variantes,
la secuencia miku4 → miku5 → miku4, la exportación GLB con PNG integrados, OBJ ZIP
con referencias MTL válidas, la carga de un XPR independiente y la exportación
de sus 30 PNG base. Todas esas solicitudes devolvieron HTTP 200. Esos resultados
se guardaban en `docs/validation/http_results.json`, carpeta ya eliminada; el
procedimiento se reproduce con
`python tools/verify_xbox_http.py -o xbox_http_check`.

Las pruebas GLB verifican geometría, índices, pesos, matrices inverse bind,
ancestro común del skin, imágenes integradas y presentación de alfa. Las OBJ
verifican geometría, inversión V específica de OBJ y rutas de las texturas.

## Integridad y cambios

Se han contrastado por SHA-256 **35 archivos originales protegidos**, incluidos
los parsers PS2 existentes, los lectores legacy Xbox de MDL/SGD, el exportador
compartido, los archivos del frontend y los cuatro ejemplos. Todos coinciden
con los bytes recibidos. No se afirma una regresión funcional PS2: no había
muestras PS2 en el ZIP.

Solo se modifican cinco archivos originales: `pz_core/pz_xpr0.py`,
`viewer/server.py`, `pz_export_cli.py`, `docs/ff1x-sgd1060.md` y `README.md`.
Se añaden el lector MPX independiente, adaptadores Xbox de exportación,
documentación, herramientas, pruebas y evidencias. Los hashes antes/después
figuraban en `docs/validation/verification_summary.json`, ya eliminada junto con
el resto de esa carpeta.

## Qué no se presenta como resuelto

No se han reconstruido animaciones, shaders, pasadas de materiales, culling ni
la semántica de todos los campos desconocidos. El soporte de archivos se limita
a estructuras reconocidas: ante otras variantes, hay errores explícitos.
Los XPR reales aportados son BC3; BC1/BC2 tienen pruebas sintéticas. No se han
validado DAE/FBX para Xbox ni generado un nuevo ejecutable Windows.

No se ha completado la verificación interactiva WebGL en este entorno. El
frontend original depende de bibliotecas CDN y su descarga no estuvo disponible
en el intento realizado. **Las imágenes incluidas son renders de un rasterizador
independiente**, no capturas del visor ni del juego de Xbox. La lectura, la API
HTTP y las exportaciones sí están probadas como se detalla arriba.

## Uso

Descomprime el proyecto en una carpeta nueva, o aplica el ZIP de solo cambios
sobre una copia del proyecto recibido. No es necesario borrar tu carpeta `.git`.
Mantén cada MPX junto a su XPR homónimo y abre el MPX desde el visor habitual.
Consulta `LEEME_XBOX.md` y los dos documentos de formato para reproducir el análisis.
