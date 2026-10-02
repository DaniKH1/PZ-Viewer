> Este documento describe la reparación anterior de paletas/pose, que se conserva. Para la entrega actual y su parche de iluminación, empieza por `docs/ff1/lighting-guide.md`.

# Reparación FF1: color de entorno y matrices de personajes

Esta versión se aplica a **FF1 de PS2**, sobre el archivo `PZ Viewer(1).zip` facilitado. No sustituye los parsers de Xbox ni de Wii.

## Probar en el visor

1. Cierra todas las ventanas y procesos de la versión anterior. Extrae el ZIP completo en una carpeta nueva.
2. Ejecuta `run_viewer.bat`. Selecciona **Fatal Frame 1** y la carpeta `examples`.
3. Abre `i000_play_camera1.sgd`, `f012_savepnt.sgd` y `r000_genkan.pk2`. Las texturas se reconstruyen con sus paletas originales en color, también al exportar GLB, OBJ y PNG.
4. Abre `m001_mafuyu.mdl`. El modelo conserva su pose almacenada; sus matrices óseas están corregidas. En esta muestra los brazos NO están horizontales en el archivo original.

El paquete de solo cambios se copia sobre la versión `PZ Viewer(1).zip`, respetando las carpetas. Si utilizas un EXE empaquetado, copiar archivos Python al lado NO actualiza el código que contiene: ejecuta esta versión desde `run_viewer.bat` o recompila el ejecutable con estas fuentes.

## Alcance global

El lector FF1 distingue `TRI2` de `MonotoneTRI2` por las categorías del binario, no por el nombre, la carpeta ni el hash del modelo. Los ejemplos no se han modificado. El modo normal NO debe cargar las paletas monocromas como si fueran texturas adicionales. Los modelos nuevos que utilicen esta misma estructura pasan por la misma corrección.

Algunas paletas originales son muy oscuras y tienen poca saturación. Se recuperan sus valores originales; no se aplica un filtro para convertirlas artificialmente en colores vivos.

## Exportación desde consola

```bat
python pz_export_cli.py "examples\r000_genkan.pk2" -o "exports_ff1\room" -f glb,obj --game ff1
python pz_export_cli.py "examples\i000_play_camera1.sgd" -o "exports_ff1\camera" -f glb,obj --game ff1
python pz_export_cli.py "examples\m001_mafuyu.mdl" -o "exports_ff1\mafuyu" -f glb,obj --game ff1
```

El parámetro legado `--t-pose` del exportador NO reconstruye una T-pose para FF1. Esta entrega conserva la pose almacenada y corrige la conversión de las matrices; no es un retargeter ni un lector de animaciones.

## Repetir las pruebas e inspeccionar otro modelo

Solo las herramientas de verificación necesitan pytest y numpy; el nuevo parser no añade dependencias al visor.

```bat
python -m pip install pytest numpy Pillow
python -m pytest tests/ff1 -q
python tools/inspect_ff1.py "examples\r000_genkan.pk2" -o "inspection_ff1\room"
```

La inspección produce PNG sin recoloreado, una hoja de texturas y `inspection.json` con dimensiones, hashes RGBA, píxeles no grises y diagnósticos. Sustituye el archivo de entrada por otro recurso FF1 para inspeccionarlo del mismo modo.

## Documentación

El diagnóstico, las evidencias binarias, las limitaciones y la lista de cambios están en `docs/ff1/color-and-pose.md`. Las pruebas usan las muestras originales y paquetes sintéticos; no contienen reglas especiales que alteren la lectura de Mafuyu, la cámara o la habitación.
