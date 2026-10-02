# Actualización MPX/XPR de Xbox

> **Nota de estado.** Este documento describe un análisis que se hizo sobre cuatro
> archivos de `examples/`. Esa carpeta se ha eliminado del proyecto, y con ella
> `tests/` y `docs/validation/`: el visor sigue funcionando y los parsers siguen
> leyendo `.mpx` y `.xpr` de cualquier carpeta de juego, pero **los comandos de
> este documento ya no se pueden ejecutar tal cual** porque sus ficheros de
> entrada no están. Las especificaciones de formato de `ff1x-sgd1060.md` y
> `xbox-xpr.md` siguen siendo válidas: describen la estructura de los archivos,
> no las muestras concretas.

Se ha realizado el análisis directamente sobre los cuatro archivos de `examples`.
El proyecto incluye los nuevos parsers, integración con el visor y la CLI,
documentación del formato, pruebas y renders de comprobación.

## Abrir el modelo

Conserva `m000_miku4.mpx` y `m000_miku4.xpr` juntos, con el mismo nombre base.
Inicia el visor como antes y abre el **MPX**. Para miku5, utiliza su pareja
homónima. Abrir un XPR por separado muestra sus texturas, no busca un modelo
sustituto. Se conservan los scripts originales de arranque y las preferencias.

```console
python pz_viewer.py --browser
```

La aplicación mantiene sus dependencias originales. Para el lector y las
herramientas de análisis se necesitan Python, NumPy y Pillow. Para las pruebas,
además, pytest. No se incluye un nuevo ejecutable Windows compilado.

## Resultados comprobados

| Archivo | Mallas | Vértices | Triángulos | Texturas base | Mipmaps totales |
|---|---:|---:|---:|---:|---:|
| m000_miku4.mpx + .xpr | 69 | 4396 | 5829 | 32 | 96 |
| m000_miku5.mpx + .xpr | 64 | 4291 | 5584 | 30 | 90 |

Ahora se leen las UV reales, los triangle strips completos y la geometría
ponderada del último bloque. Los materiales se enlazan por el índice de recurso,
incluido el cero. Los XPR se decodifican desde la base correcta, con dimensiones
rectangulares y alfa BC3. Las 186 imágenes mip coinciden byte a byte con un
segundo decodificador DDS.

## Reproducir las comprobaciones

```console
python -m pytest -q tests
python tools/verify_xbox_http.py -o xbox_http_check
python tools/inspect_xbox.py examples/m000_miku4.mpx -o xbox_check/miku4 --pixels --preview --export
python tools/inspect_xbox.py examples/m000_miku5.mpx -o xbox_check/miku5 --pixels --preview --export
python pz_export_cli.py examples/m000_miku4.mpx -o xbox_export -f glb,obj
```

`--pixels` exporta todos los mipmaps y un atlas. `--preview` genera vistas con
un rasterizador independiente; no son capturas del juego. `--export` verifica
la ruta de exportación GLB/OBJ. Los informes detallan offsets, contadores,
materiales, tablas, cobertura de buffers y errores de correspondencia.

## Archivos relevantes

El nuevo lector MPX está en `pz_core/pz_mpx_ff1x.py`. El lector XPR reescrito está
en `pz_core/pz_xpr0.py`. `pz_core/pz_export_xbox.py` adapta únicamente la
convención OBJ y la presentación de alfa de GLB para esta ruta. Se ha conectado
el lector en `viewer/server.py` y `pz_export_cli.py`.

La especificación está en `docs/ff1x-sgd1060.md` y `docs/xbox-xpr.md`. Los informes,
renders y el manifiesto de hashes `protected_sha256.json` estaban en
`docs/validation/`; esa carpeta se eliminó y ya no están. Lo que documenta el
formato sigue vigente, y los análisis se vuelven a generar con `tools/`.

Los parsers PS2, los lectores Xbox legacy de MDL/SGD y el exportador compartido
no se han modificado. No hay muestras PS2 en el ZIP: se verifica su integridad
de código, no se afirma haber ejecutado esos juegos ni probado todos sus assets.

## Limitaciones concretas

La interpretación está validada para las dos parejas incluidas; los diseños
no soportados se rechazan en lugar de inventar resultados. XPR admite recursos
2D BC1/BC2/BC3, con muestras reales BC3 y pruebas sintéticas BC1/BC2. Siguen sin
reconstruirse shaders, animaciones, orden de pasadas y ciertos campos/banderas.
GLB usa una política genérica de cutout/blend para mostrar el alfa conservado.
DAE/FBX no se han validado para esta ruta.

El código del frontend WebGL no se ha modificado. Se han probado los datos
serializados del visor y se aportan renders independientes. La revisión visual
no sustituye una comparación con una captura del motor original de Xbox.
