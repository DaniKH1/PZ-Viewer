# Arreglo Wii: modelos reciclados de Xbox

Cierra PZViewer y extrae este proyecto en una carpeta nueva. Ejecuta
`run_viewer.bat`. En **Extra → Fatal Frame 2 Wii**, elige `examples` y abre
uno de los tres MDLB. Su PPDB homónimo debe estar en la misma carpeta.

**B**: mostrar huesos. **F**: reencuadrar. No hace falta activar un modo Xbox:
la convención de matrices se verifica desde los datos de cada rig.

Corregidos: inverse bind ENOB, aplicación coherente a geometría/esqueleto,
base de los datos PPDB, paletas RGB5A3 y tiles CI8. Se mantiene intacta la
implementación de los demás juegos. Detalles y límites: `docs/ff2w/repair.md`.

Pruebas e inspección, desde la raíz del proyecto:

```bash
python -m unittest discover -s tests -v
python tools/inspect_wii.py examples --out inspection_wii --preview --textures
```

Exportar por CLI:

```bash
python pz_export_cli.py examples/ch000_doa.mdlb -o export_doa -f glb,obj --game ff2w
```

La CLI exporta unidades normalizadas del parser; el visor conserva su escala
histórica por tipo. Ambas rutas mantienen mallas y huesos en el mismo espacio.

Los PNG de `docs/validation_wii` son diagnósticos CPU, no capturas WebGL.
Las muestras incluidas son las tres recicladas: las regresiones de layouts
nativos son sintéticas, no una validación de todos los modelos nativos de Wii.
