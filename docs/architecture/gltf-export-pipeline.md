# Exportación Wii: matrices, unidades y validación

Revisión 2026-10-02. Esta página describe las rutas que **existen en este
repositorio**, no una API externa llamada `mdlb_parser.export_to_gltf`.

La exportación compartida permanece en `pz_core/export/pz_export.py`, sin modificar.
La corrección del modelo Wii ocurre en `pz_core/ff2w/pz_mdlb_ff2w.py`. La CLI
`pz_export_cli.py` incorpora una rama para MDLB/PK2B y su PPDB homónimo;
las ramas PS2/Xbox conservan su enrutamiento.

## Matrices de entrada

En los tres rigs reciclados, `ENOB+0x50` es inverse bind. Primero se
recupera la matriz global de reposo real y se aplica tanto a las posiciones
como a los huesos. Ver [MDLB_FORMAT.md](../ff2w/mdlb-format.md). SGDBone recibe
matrices globales serializadas por columnas, con traslación en `[12:15]`.
Los IDs de hueso del archivo se remapean a índices de la lista de joints.

Solo la traslación cambia unidades en la nueva ruta verificada. No se
multiplica una rotación por 0.05 y luego se divide otra vez indiscriminadamente.
La etiqueta `ff2w_rotation_scaled` permite que `build_ff2w_export_model`
compense solo la escala que la ruta legacy realmente añadió.

## Construcción GLB

El exportador genera un nodo Armature y los joints enlazados según los
padres del modelo, sin tabla de padres prefijada. Los índices de los nodos
no son necesariamente0…N−1: los nodos de las mallas preceden al armature.

```text
local_i = inverse(world_padre) · world_i
local_raíz = world_raíz
inverseBind_i = inverse(world_i)
```

Cada primitiva exportada incluye posición, normales calculadas, UV,
JOINTS_0 y WEIGHTS_0 cuando están presentes. Las texturas base se embeben
como PNG. El exportador conserva sus materiales estándar, no reproduce
los shaders originales ni garantiza el aspecto de todos los importadores.

## Dos rutas de unidades

`parse_ff2w_model` mantiene el factor 0.05 y el centrado de la herramienta.
La CLI nueva exporta directamente esas unidades, sin el zoom del viewport.
El servidor existente aplica factores por tipo:45.5 para MDLB,3.26 para
habitaciones y 20 para props. No se han recalibrado esos factores en este
arreglo porque cambiarían también el comportamiento de los modelos nativos.

Cuando el modelo tiene `ff2w_applied_scale`, el exportador GLB llama al
adapter Wii: conserva la escala del visor, deshace el centrado y prepara
las esquinas de triángulo, como hacía esta versión de la herramienta.
Por ello, un GLB del visor y uno de la CLI **no tienen por qué medir lo
mismo**. No es una separación entre huesos y malla: en cada ruta ambos
usan las mismas unidades. El OBJ conserva la política anterior del
exportador compartido. Esta revisión no unifica las políticas de unidades
entre todas las vías de exportación.

## Pruebas incluidas

En las tres parejas se abre el GLB binario generado, se recuperan los
accessors y se reconstruyen las matrices globales de la jerarquía. Se
comprueba que `world * inverseBind` es identidad dentro de la precisión
float32, que la piel en reposo reproduce sus posiciones y que trasladar
el joint de la cabeza0.1 unidades desplaza los vértices en esa cantidad
multiplicada por su peso, sin cambiar las otras coordenadas. También se
verifica que exportar no altera el modelo en memoria.

Es una prueba matemática del rig exportado, **no una reproducción de ANMB**
ni una prueba interactiva en Blender, Unity o el juego. Se comprueba además
la carga HTTP del servidor y la exportación HTTP de GLB/OBJ con sus PNG.
No se consiguió ejecutar el frontend WebGL en este entorno.

```bash
python -m unittest discover -s tests -v
python pz_export_cli.py examples/ch000_doa.mdlb -o export_doa -f glb,obj --game ff2w
```

La opción histórica `gltf` de la CLI sigue llamando al escritor GLB; esta
revisión no añade un escritor glTF textual. Para evitar una extensión
engañosa, utiliza `-f glb`.
