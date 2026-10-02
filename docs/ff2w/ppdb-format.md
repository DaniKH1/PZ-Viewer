# PPDB / Project Zero 2 Wii — imágenes y paletas

Revisión 2026-10-02; comprobada en los PPDB de `ch000_bontage`, `ch000_doa`
y `ch000_goth`. Lector: `pz_core/ff2w/pz_mdlb_ff2w.py`, `parse_ppdb`.

## Corrección de la interpretación anterior

La documentación anterior fijaba los offsets a0x40 y afirmaba que las
paletas CI8 no estaban en el archivo. Ambas cosas son incorrectas para
estas muestras. El segundo puntero de cada entrada lleva precisamente
al descriptor de la paleta. También se estaban leyendo los índices CI8
como una imagen lineal, cuando están ordenados en tiles.

## Contenedor y cabecera TPL

Tras LZ11, `pk2` aparece en `+0x04`. El u32 big-endian en `+0x20` indica
la base de la cabecera TPL embebida: **0x80 en los tres archivos**.
Se mantiene el candidato 0x40 para la ruta histórica, validado por la firma.

A partir de la base `B`:

| Offset | Tipo | Contenido |
|---|---|---|
| `B+0x00` | `u32be` | Firma `0x0020AF30`. |
| `B+0x04` | `u32be` | Número de imágenes N. |
| `B+0x08` | `u32be` | Offset relativo de la tabla; 0x0C en las muestras. |
| `B+tabla+i*8` | `u32be` | Offset relativo del descriptor de imagen. |
| `B+tabla+i*8+4` | `u32be` | Offset relativo del descriptor de paleta; 0 si no hay. |

**Todos los punteros de esta estructura comparten B**, tanto descriptores
como píxeles y paletas. No es correcto localizar la tabla con B=0x80 y
seguir sumando 0x40 a los datos de píxeles.

## Descriptor de imagen

Tiene 36 bytes en estas muestras. Offsets desde el descriptor:

| Offset | Tipo | Contenido |
|---|---|---|
| `+0x00` | `u16be` | Altura. |
| `+0x02` | `u16be` | Anchura. |
| `+0x04` | `u32be` | Formato GX: 9=CI8, 14=CMPR. |
| `+0x08` | `u32be` | Offset relativo de píxeles: `B + valor`. |
| `+0x0C…+0x23` | varios | Parámetros de muestreo/LOD; el lector no reproduce estos estados. |

La longitud física de la imagen base depende de tiles completos, incluso
para dimensiones pequeñas:

```text
CI8: ceil(ancho/8) * ceil(alto/4) * 32 bytes
CMPR: ceil(ancho/8) * ceil(alto/8) * 32 bytes
```

Se valida el rango antes de decodificar. El lector entrega el nivel base;
esta revisión no añade un extractor general de mipmaps PPDB.

## Descriptor y datos de paleta

Desde el descriptor al que apunta el segundo u32 de la entrada:

| Offset | Tipo | Contenido |
|---|---|---|
| `+0x00` | `u16be` | Número de entradas. 256 en todas las paletas de los ejemplos. |
| `+0x02` | `u16be` | Flags/reserva; cero observado, no interpretado. |
| `+0x04` | `u32be` | Formato TLUT. 2=RGB5A3 en las doce paletas reales. |
| `+0x08` | `u32be` | Offset relativo a B de las entradas. |

Cada entrada RGB5A3 es un u16 big-endian. Bit15=1 selecciona RGB5 opaco;
bit15=0 selecciona A3/R4/G4/B4. Se expanden componentes repitiendo bits,
conservando el alfa. El decoder acepta además TLUT0=IA8 y 1=RGB565,
verificados con fixtures sintéticos, no con muestras reales de este ZIP.

CI8 almacena un tile8×4 tras otro, de izquierda a derecha y arriba abajo;
dentro del tile los índices van por filas. Para un píxel(x,y):

```text
offset = B + data_rel
       + ((y//4)*ceil(ancho/8) + x//8)*32
       + (y%4)*8 + x%8
```

Ese byte indexa la paleta RGBA. No es una intensidad ni una máscara por sí
mismo. Una paleta ausente o no soportada se informa como `resolved=False`;
ya no se fabrica una rampa gris. Un índice fuera de rango produce error.

## Ejemplo reproducible: ch000_doa.ppdb, imagen0

```text
B                           = 0x80
Tabla                       = B + 0x0C = 0x8C
Descriptor de imagen        = B + 0x480 = 0x500
Descriptor de paleta        = B + 0x05C = 0x0DC
Datos de paleta              = B + 0x080 = 0x100 (256 entradas, RGB5A3)
Datos de imagen             = B + 0x600 = 0x680 (256×256, CI8)
```

Suponer0x40 para los píxeles comenzaba64 bytes antes de los índices reales.
La textura final de cada archivo acaba exactamente al final del contenedor
al sumar **B**, una segunda comprobación independiente de los offsets.

| Archivo | Bytes descomprimidos | Imágenes base | CI8 | CMPR |
|---|---:|---:|---:|---:|
| ch000_bontage.ppdb | 337152 | 9 | 5 | 4 |
| ch000_doa.ppdb | 286848 | 10 | 2 | 8 |
| ch000_goth.ppdb | 369728 | 8 | 5 | 3 |

## Materiales y límites

El índice ETAM identifica la imagen del PPDB homónimo. Solo se componen
máscaras indicadas explícitamente por cada ETAM; una imagen no se considera
máscara por su número de slot. Por ejemplo, `ch000_doa` no declara 0→9 y la
imagen9 es una textura normal, mientras que `ch000_mio` sí declara las parejas
0→9 y 7→10 para cara/pestañas/encaje y pelo, respectivamente. El servidor debe
asignar a esos materiales la variante compuesta, no la textura difusa original.
En modelos de personaje MDLB, si una textura contiene niveles de alpha
intermedios además de 0 y 255, el visor mezcla esos texeles parcialmente
transparentes; las texturas con alpha exclusivamente binario siguen usando
recorte duro para conservar bordes limpios. Esta política se limita a
personajes FF2 Wii para no cambiar el renderizado de otras familias. Como pelo,
pestañas y encaje usan muchas tarjetas transparentes superpuestas, estas
conservan escritura de profundidad con un umbral alpha mínimo: así se mantiene
la cobertura gradual sin dibujar a través de tarjetas delanteras por el orden
de mezcla por objeto.

La decodificación CMPR anterior conserva sus valores de color al corregir
el offset de origen. Su interpolación se aproxima a DXT1: no se afirma
exactitud bit a bit con el hardware GX, especialmente para el RGB de
píxeles transparentes y la interpolación de colores. No se ha cambiado
esa aproximación en este arreglo.

Se comparan todos los píxeles CI8 con una implementación escalar
independiente de la fórmula de dirección y se prueban los 65536 valores
posibles RGB5A3. Los 15 niveles base CMPR coinciden con el decoder anterior
alimentado desde el offset corregido, no con un volcado de hardware.

Referencias de contraste para los formatos GX (los offsets específicos de
PPDB proceden de las muestras):

- Dolphin, decoder de texturas: https://raw.githubusercontent.com/dolphin-emu/dolphin/master/Source/Core/VideoCommon/TextureDecoder_Generic.cpp
- devkitPro/libogc, constantes GX/TLUT: https://raw.githubusercontent.com/devkitPro/libogc/master/gc/ogc/gx.h
