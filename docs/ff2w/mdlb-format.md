# MDLB / Project Zero 2 Wii — geometría y huesos

Revisión: 2026-10-02. Implementación: `pz_core/ff2w/pz_mdlb_ff2w.py`.

Esta revisión está contrastada con `ch000_bontage`, `ch000_doa` y `ch000_goth`,
los tres modelos reciclados de Xbox que acompañan al proyecto. No pretende
que todas las variantes de Wii o todas sus animaciones estén resueltas.
El diagnóstico y las pruebas están en [WII_REPAIR.md](repair.md).

## Contenedor y tamaños

Los seis ejemplos MDLB/PPDB están envueltos en LZ11. Una vez descomprimido
el MDLB, en `+0x00` hay un entero big-endian y en `+0x04` los caracteres
`pk3`, seguidos por cero. No hay una firma literal `02pk3` en `+0x00`.
Hay otro contenedor `pk1` en `+0x40`; la exploración de chunks comienza en
`+0x80`. El parser conserva también la ruta de geometría `pk2` para PK2B.

Los chunks llevan FourCC de cuatro bytes (`ENOB`, `PAHS`, `TREV`, etc.) y
un `u32be` en `+0x04`. **En estas muestras, ese tamaño excluye los primeros
ocho bytes del chunk**: `fin = inicio + 8 + tamaño`. No debe confundirse
con el tamaño físico total. Los inicios observados están alineados a 32 bytes.
Por ejemplo, el primer GIEW de `ch000_doa` está en `0x6E60`, declara
`0xE38`, y el siguiente comienza en `0x7CA0 = 0x6E60 + 8 + 0xE38`.
Los buffers comienzan en `+0x20` aunque el tamaño lógico incluya padding.

## ENOB: estructura real

Cada uno de los tres ejemplos contiene 26 registros; cada ENOB declara
`0xB8` bytes y ocupa `0xC0` bytes físicos. Offsets relativos al tag:

| Offset | Tipo | Significado comprobado |
|---|---|---|
| `+0x00` | `char[4]` | `ENOB`. |
| `+0x04` | `u32be` | Tamaño sin los ocho bytes iniciales. |
| `+0x08` | `u32be` | Valor 7 en estas muestras; semántica no resuelta. |
| `+0x10` | `u16be` | ID del hueso. |
| `+0x12` | `s16be` | ID del padre; −1 indica raíz. |
| `+0x14` | `u16be` | Coincide con el número de hijos en estas muestras; no se usa para reconstruir padres. |
| `+0x20` | `f32be[4]` | Cuaternión local, orden **x, y, z, w**. |
| `+0x30` | `f32be[3]` | Escala local. |
| `+0x40` | `f32be[3]` | Traslación local. |
| `+0x50` | `f32be[16]` | Matriz almacenada; **inverse bind** en los ejemplos reciclados. |
| `+0x90` | subchunk | `EMAN`, con el nombre (`model_0` … `model_25`). |

No hay un padre `int32` en `+0x00` ni una matriz 3×4 en `+0x08`, como
indicaba la documentación anterior. Tampoco es correcto interpretar siempre
la matriz de `+0x50` como una posición global de reposo.

### Evidencia independiente de la matriz inversa

Definimos matrices para vectores columna. `S` es la matriz 4×4 formada
leyendo los 16 floats de `+0x50` por filas. Reconstruimos otra matriz desde
campos diferentes del archivo:

```text
L_i = T(traslación local) · R(cuaternión local xyzw) · Escala(local)
W_TRS_i = L_i                         si no hay padre
W_TRS_i = W_TRS_padre · L_i           en los demás casos
```

En los **78 registros** se cumple, con el redondeo de los floats:

```text
W_TRS_i · transpose(S_i) ≈ Identidad
```

La matriz que se usa para reconstruir geometría y huesos es, por tanto:

```text
W_bind_i = inverse(transpose(S_i))
```

Esto requiere una inversión, no solo mover la traslación ni transponer.
La tolerancia de selección es `5e-4` en la norma máxima elemento a elemento
del producto. Los errores máximos medidos son `1.99956e-4`, `1.85633e-4`
y `1.85397e-4` para bontage, doa y goth, respectivamente.

Ejemplo `ch000_bontage`, hueso 3: el ENOB empieza en `0x1480`. Los valores
almacenados en la última fila son `[0, -19.26300049, -1.07799995, 1]`.
La posición global recuperada es `[0, +19.26300049, +1.07799995]`, antes
de las conversiones de unidades y del centrado del visor.

El padre puede estar después del hijo en el archivo: hueso 1 → padre 3.
Los IDs se resuelven mediante un mapa, no como posiciones arbitrarias de lista.
La decisión de convención es **del rig completo**, también para huesos cuya
traslación es cero. No se infiere la rotación de esos huesos mirando dónde
aparecen valores de traslación.

### Separación respecto a la ruta nativa

`_resolve_bone_matrices` deja en `legacy_world` la ruta ordinaria con
traslación en `[3,7,11]`. Ante un rig coherente de vectores fila comprueba
la jerarquía local y distingue `inverse_bind_row_vector` de `world_row_vector`.
Si no puede demostrar ninguna de esas dos interpretaciones, produce un
error explícito. Los layouts mixtos/no clasificados conservan el tratamiento
legacy; no se consideran validados por esta reparación.

Los rigs comprobados no pasan por los reajustes anatómicos legacy de
`fix_bone_positions`. No se renombra, recoloca a mano ni reparenta ningún hueso.

## PAHS, TREV y GIEW

`PAHS +0x10` contiene el ID de shape y `+0x14` el ID del hueso de una
pieza rígida. `HSEM` agrupa triángulos por material; `LDIV` contiene las
referencias GX a los buffers.

### TREV rígido

`+0x08` indica el tipo de componente GX; `+0x09`, sus bits fraccionarios;
`+0x0A`, el número de vértices; datos en `+0x20`. Todos los TREV de estas
muestras usan tipo 4, tres float32 big-endian por posición, stride 12.
La ruta verificada también acepta tipo 3 como s16 dividido por `2^frac`.
No se elige el tipo por el nombre del modelo ni por la longitud aparente
del chunk. La ruta nativa legacy conserva su detección anterior.

La posición rígida es local al hueso de PAHS:

```text
p_world = W_bind_hueso · [x, y, z, 1]
```

Una matriz sin traslación puede seguir rotando; «traslación cero» no equivale
a «identidad». Si hay GIEW, ese buffer tiene prioridad sobre la copia TREV.

### GIEW: posiciones ya ponderadas

| Offset | Tipo | Significado |
|---|---|---|
| `+0x0C` | `u16be` | Número de registros. |
| `+0x0E` | `u16be` | Stride de registro. |
| `+0x10` | `u16be` | Primer índice de vértice de este bloque. |
| `+0x12` | `u8` | Número de influencias N. |
| `+0x20` | registros | Inicio del buffer. |

Al principio de cada registro hay cuatro bytes de IDs de hueso. Desde
`+4 + 8*k` se leen tres s16 de posición y el peso de esa influencia. Los
cuatro componentes se dividen por 1024. En las muestras, N=1 tiene stride 16
y N=2 stride 28; después hay datos de normales empaquetadas que no se usan
en esta reparación. No se ha confirmado una muestra N=3 o N=4.

```text
p_world = suma_k( R_bind_k · p_preponderada_k + peso_k · t_bind_k )
```

**No se vuelve a multiplicar la posición por el peso.** La posición ya está
ponderada. El peso almacenado en los registros N=1 de estas muestras es 1024,
y la suma de pesos de los registros N=2 también es 1024. El adapter mantiene
cuatro índices y cuatro pesos normalizados por vértice para exportación.

Hay dos bloques GIEW en bontage, cuatro en doa y dos en goth. En particular,
doa tiene geometría ponderada en la shape 0 además de en la shape 15: no debe
clasificarse todo el pelo/cabeza como rígido por su apariencia.

## UV, primitivas y normales

Los DCXT observados tienen tipo 3, 10 bits fraccionarios, count en `+0x0A`
y pares s16 en `+0x20`. Se divide por 1024 sin limitar UV a `[0,1]`.
Se conserva el vínculo posición/UV/peso de cada esquina, incluidas costuras.

En los LDIV de las muestras se conserva el decodificador de registros de
6 bytes (posición, normal, UV). La ruta legacy conserva su alternativa de
8 bytes con color. El opcode se enmascara con `0xF8`: `0x9A` es un strip
con otro VAT, no un tipo diferente ni un fan. Se corrige la distinción
`0xA0` fan / `0x80` quads y se comprueba con datos sintéticos. La topología
observada de los tres archivos no cambia respecto al lector original.

**Hay MRON en estos archivos.** El visor sigue calculando normales desde
la geometría; esta reparación no implementa las normales MRON ni las
empaquetadas de GIEW. Los shaders, estados GX, selección de capas y posibles
variantes de atributos no se dan por reconstruidos completamente.

## Salida del adapter

El parser conserva el factor 0.05 y centra la geometría con los pies en y=0.
Las matrices SGDBone se serializan por columnas con traslación en `[12:15]`.
Geometría y huesos usan el mismo centrado y conversión. Para los rigs
verificados solo se convierte la **traslación**, no la escala de la rotación.
La compensación legacy de escala del exportador permanece solo para los
huesos que realmente recibieron ese factor. Ver
[GLTF_EXPORT_PIPELINE.md](../architecture/gltf-export-pipeline.md).

## Referencias técnicas

Los offsets y la semántica ENOB se han obtenido de los binarios incluidos,
no de una especificación Xbox trasladada sin verificar. Para las constantes
GX y el significado estándar de inverse bind se han contrastado:

- devkitPro/libogc, definiciones GX: https://raw.githubusercontent.com/devkitPro/libogc/master/gc/ogc/gx.h
- Khronos, glTF Skins: https://github.khronos.org/glTF-Tutorials/gltfTutorial/gltfTutorial_020_Skins.html
