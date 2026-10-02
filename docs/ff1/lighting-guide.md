# FF1: iluminación de habitaciones y colores por vértice

## Abrir la versión corregida

Esta entrega continúa **PZViewer_FF1_color_huesos.zip**. El ZIP completo conserva sus arreglos de paletas y huesos, además de los parsers de Xbox y Wii. El paquete de solo cambios se aplica sobre esa entrega anterior, no sobre una versión arbitraria del programa.

Cierra el visor anterior y sus procesos Python. Extrae el ZIP completo en una carpeta nueva y ejecuta `run_viewer.bat`. Selecciona **Fatal Frame 1**, entra en `examples` y abre `r000_genkan.pk2`. Conserva `r000_genkan.lit` a su lado: contiene las luces de esa habitación. No es necesario modificar los archivos del juego.

Con un LIT válido, los colores por vértice se activan al cargar. El panel muestra `FF1 LIT: 2 point, 1 spot. Static vertex lighting (V).` La tecla **V** alterna entre la iluminación recuperada y las texturas sin esa modulación. La escena debe permanecer oscura, con variaciones locales cálidas y azuladas; no se ha aumentado la exposición de todo el mapa.

Se busca un `.lit` con el mismo nombre base del recurso, también con extensión `.LIT`. No se elige otro archivo de luces de la carpeta. Sin LIT ni luces embebidas, no se inventan las fuentes: la vista inicial mantiene las texturas sin activar el gris oscuro del lector antiguo. Los errores de iluminación se muestran sin borrar la geometría.

Si usas un EXE empaquetado, copiar `.py` a su lado no cambia el código incorporado. Ejecuta estas fuentes o recompila tu ejecutable. Para usar el navegador del sistema: `python pz_viewer.py --browser`.

## Qué añade

Lector FF1 de registros LIGHT, cálculo estático por vértice a partir de posiciones, normales y materiales, conservación de los colores de cachés ya pobladas, integración en visor/CLI y exportación GLB con materiales sin una segunda iluminación. Los parsers FF2/FF3, Xbox y Wii no se han reescrito.

Es una reconstrucción de la **iluminación estática**, no del fotograma completo del juego. No añade linterna, parpadeo, niebla, sombras proyectadas ni rayos volumétricos. La iluminación azul afecta las superficies dentro de su cono; no se crea un haz visible en el aire.

## Exportar y verificar

```bat
python pz_export_cli.py "examples\r000_genkan.pk2" -o "exports_ff1\room_lit" -f glb,obj --game ff1
python -m pip install pytest numpy Pillow
python -m pytest tests/ff1 -q
python tools/inspect_ff1_lighting.py "examples\r000_genkan.pk2" -o "inspection_lighting"
```

El parser nuevo usa la biblioteca estándar. NumPy y pytest son para verificación; Pillow ya lo utiliza la herramienta. Node es opcional: permite ejecutar seis pruebas de la lógica real del frontend con dobles de Three/DOM; no son pruebas GPU.

La inspección produce seis renders CPU y `lighting.json`: resultado, comportamiento gris anterior reproducido, texturas sin iluminación, luces puntuales, foco y ambiente por separado. Mantienen cámara y exposición. No son capturas del juego ni del frontend WebGL.

En GLB se exporta `COLOR_0` y `KHR_materials_unlit`. La copia exportada limita los multiplicadores RGB a [0,1] para interoperabilidad; los 53 vértices con algún canal superior a 1 del ejemplo conservan su ganancia original en el visor. La exportación no emula la aritmética de color de PS2 y puede diferir en brillo en aplicaciones modernas. OBJ necesita un visor que admita su extensión de colores por vértice. DAE/FBX conservan el comportamiento previo, sin garantía nueva de iluminación.

Consulta `docs/ff1/room-lighting.md` para la evidencia binaria, las pruebas y las limitaciones. `docs/ff1/guide.md` y `docs/ff1/color-and-pose.md` documentan la reparación anterior de paletas/pose, que se conserva.
