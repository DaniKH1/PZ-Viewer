# XPR0 de Xbox: recursos, dimensiones y BC3

## Evidencia y alcance

Se han inspeccionado `m000_miku4.xpr` y `m000_miku5.xpr`, incluidos todos sus
recursos y mipmaps. Las estructuras D3D de Xbox se han contrastado con la
implementación abierta de Cxbx-Reloaded, especialmente `X_D3DResource`,
`X_D3DPixelContainer` y las máscaras `X_D3DFORMAT_*`:

`https://raw.githubusercontent.com/Cxbx-Reloaded/Cxbx-Reloaded/master/src/core/hle/D3D8/XbD3D8Types.h`

Referencia complementaria sobre creación de cabeceras de texturas Xbox:

`https://raw.githubusercontent.com/evands/plex/master/tools/XBMCTex/xbox.cpp`

Las referencias ayudan a interpretar los campos; las bases de offsets,
longitudes y resultados publicados aquí se comprueban con los binarios incluidos.
No se copia una especificación externa suponiendo que cualquier XPR sea idéntico.

## Cabecera y descriptores

| Offset | Tipo | Contenido |
|---|---|---|
| 0 | char[4] | `XPR0`. |
| 4 | u32 | Tamaño declarado del archivo. |
| 8 | u32 | Tamaño de cabecera y **base de la sección de datos**; `0x800` en ambas muestras. |
| 12 | descriptor[] | Descriptores de 20 bytes. |

| Offset del descriptor | Campo | Significado |
|---|---|---|
| 0 | Common | Tipo de recurso y referencias; `0x00040001` en las muestras. |
| 4 | Data | Offset relativo a la sección de datos. |
| 8 | Lock | Cero en las muestras. |
| 12 | Format | Bitfield de formato Xbox. |
| 16 | Size | Cero en los recursos BC examinados. |

Una palabra `FFFFFFFF` en posición de Common termina la tabla. Los bytes de
relleno posteriores no son descriptores. La dirección de una textura es:

`file_offset = header_size + Data`

Por tanto, el recurso 0 con Data=0 empieza en `0x800`, **no en la cabecera**.
Se conservan todos los recursos, incluso los no referenciados por un material.
El lector diferencia `offset` absoluto y `data_offset` relativo.

## Format no es una tabla de tags ni una marca de compilación

| Bits | Significado |
|---|---|
| 0..1 | Canal DMA. |
| 2 | Indicador de cubemap. |
| 3 | Selección de borde. |
| 4..7 | Dimensionalidad; 2 en las muestras. |
| 8..15 | Código de formato de píxel. |
| 16..19 | Número de niveles mip. |
| 20..23 | log2 de anchura. |
| 24..27 | log2 de altura. |
| 28..31 | log2 de profundidad. |

Los códigos implementados son `0C=DXT1/BC1`, `0E=DXT3/BC2` y `0F=DXT5/BC3`.
**Todos los recursos de estas dos muestras son DXT5.** Anchura y altura son
independientes: `0x08830F29` significa 256×256 con tres mips;
`0x07830F29`, 256×128; `0x06730F29`, 128×64. El cambio en una dimensión no
implica un cambio de códec.

En miku4 hay 32 recursos; en miku5, 30. Cada uno declara tres mipmaps:
96 y 90 imágenes respectivamente. También hay texturas diminutas, incluida
una 4×4 con niveles 4×4, 2×2 y 1×1: cada nivel requiere al menos un bloque BC.

## Tamaños, alineación y orden

Para cada nivel:

`bytes = ceil(width/4) * ceil(height/4) * bytes_por_bloque`

BC1 utiliza 8 bytes por bloque; BC2 y BC3, 16. La dimensión de cada mip se reduce
por mitades, con mínimo uno. Los niveles de estas muestras son consecutivos,
sin alineación independiente entre ellos. Los inicios de asignaciones están
alineados a `0x80`; el archivo está acolchado a `0x800`. El padding después de
una textura no inventa más mipmaps.

El lector mantiene por separado el tamaño asignado (`size`), los bytes usados
por los mipmaps (`bytes_used`) y el padding restante (`trailing_bytes`). Comprueba
que las imágenes no atraviesen el siguiente recurso ni el tamaño declarado.
Los bloques comprimidos se recorren en orden de filas: no se aplica Morton ni
un deswizzle pensado para imágenes sin comprimir.

## Color y alfa

El canal alfa BC3 utiliza dos extremos y dieciséis selectores de tres bits.
Se implementan las dos ramas de interpolación. Patrones como `49 92 24` son
selectores comprimidos válidos, no una prueba de relleno o ausencia de alfa.
BC2 usa nibbles de alfa explícito; BC1 conserva su caso transparente cuando
corresponde. BC2/BC3 siguen usando cuatro colores aunque el orden de sus
extremos RGB coincida con el caso transparente de BC1.

La decodificación conserva el alfa por defecto. La opción explícita
`use_alpha=False` permite una vista opaca para inspección; nunca modifica los
bytes fuente ni se activa silenciosamente en el parser. La opción antigua
`include_self_record` se acepta por compatibilidad pero no elimina el recurso
cero, porque ese supuesto recurso autorreferencial no existe.

## Comprobación independiente

Las 186 imágenes mip se han envuelto individualmente en una cabecera DDS y
comparado con el decodificador DDS/BC3 de Pillow. Dimensiones y **cada byte RGBA
coinciden exactamente**: diferencia máxima 0 y 0 canales distintos. La prueba
está en `test_every_sample_mip_matches_independent_pillow_dds_decoder`.

También hay bloques sintéticos para BC1 transparente, BC2 con alfa explícito y
las dos ramas BC3, además de pruebas de archivos truncados, recursos solapados,
offsets inválidos y formatos no admitidos.

## API y límites

`parse_xpr0_records` devuelve un registro por recurso con metadatos validados.
`parse_xpr0` devuelve una imagen por mip. Con `decode=False` devuelve los mismos
metadatos e `image=None`, en lugar de una lista vacía. El adaptador del visor
separa las imágenes base de los mipmaps; no confunde cada nivel con una textura
adicional. El análisis no utiliza estado global mutable entre archivos.

El soporte implementado es para recursos 2D no cubemap BC1/BC2/BC3. Formatos
lineales, paletizados, sin compresión, cubemaps, volumen y disposiciones Size
no reconocidas se rechazan con diagnóstico; no se decodifican mediante guesses.
BC1/BC2 están cubiertos por pruebas sintéticas, pero las muestras reales
aportadas solo ejercitan BC3. No se ha reconstruido la política original de
renderizado de materiales: conservar alfa en los píxeles y decidir entre
cutout/blending son problemas distintos.
