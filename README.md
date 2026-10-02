# svg_animator

Convierte un **SVG** en un **AnimatedVectorDrawable** de Android (el formato XML
nativo para vectores animados en apps móviles: `vector` + `animated-vector` +
`objectAnimator`/`set`), y ofrece herramientas para **verificar**, **reparar**,
**listar** y **editar** esos recursos una vez generados — por línea de comandos
o desde un **menú interactivo**.

Es un único script en Python, sin dependencias externas (solo librería estándar).

## Requisitos

- Python 3.9+ (usa `xml.etree.ElementTree`, `dataclasses`, f-strings; probado con 3.14).

## Instalación

No requiere instalación. Clona o copia `svg_animator.py` y ejecútalo con `python3`:

```bash
python3 svg_animator.py <subcomando> [opciones]
```

## Índice

- [Conceptos básicos](#conceptos-básicos)
- [`convert` — SVG → AnimatedVectorDrawable](#convert--svg--animatedvectordrawable)
- [`verify` — Verificar XML existentes](#verify--verificar-xml-existentes)
- [`repair` — Reparar XML existentes](#repair--reparar-xml-existentes)
- [`list` — Listar los trazos de un vector](#list--listar-los-trazos-de-un-vector)
- [`edit` — Editar atributos](#edit--editar-atributos)
- [`menu` — Menú interactivo](#menu--menú-interactivo)
- [Qué se soporta del SVG y limitaciones conocidas](#qué-se-soporta-del-svg-y-limitaciones-conocidas)
- [Integración en un proyecto Android](#integración-en-un-proyecto-android)

## Conceptos básicos

Un icono animado en Android se compone de tres piezas de XML:

| Archivo | Carpeta | Qué es |
|---|---|---|
| `{nombre}.xml` | `res/drawable/` | El `<vector>` estático: la forma (paths/groups) |
| `{nombre}_animated.xml` | `res/drawable/` | El `<animated-vector>`: referencia al vector y declara qué `<target>` (path o group) usa qué animador |
| `{nombre}_*.xml` | `res/animator/` | El/los `<objectAnimator>` o `<set>`: la animación en sí (propiedad, duración, etc.) |

`convert` genera los tres a partir de un SVG. `verify`/`repair`/`list`/`edit`
operan sobre esos XML ya generados (o escritos a mano).

## `convert` — SVG → AnimatedVectorDrawable

```bash
python3 svg_animator.py convert <archivo.svg> [opciones]
```

| Opción | Default | Descripción |
|---|---|---|
| `svg_file` (posicional) | — | Archivo SVG de entrada |
| `-o, --out-dir DIR` | `.` | Carpeta raíz de salida; se crean `drawable/` y `animator/` dentro |
| `--name NOMBRE` | nombre del SVG | Nombre base del recurso (se sanea a snake_case válido para Android) |
| `--animation {rotate,fade,draw}` | `rotate` | Tipo de animación a generar (ver tabla abajo) |
| `--duration MS` | `1000` | Duración de la animación en milisegundos |
| `--stagger MS` | `120` | Retraso entre el inicio de cada trazo, solo para `--animation draw` |
| `--width DP` | ancho del SVG/viewBox | Ancho del drawable en dp |
| `--height DP` | alto del SVG/viewBox | Alto del drawable en dp |

### Tipos de animación

| Valor | Efecto | Qué anima | Requiere |
|---|---|---|---|
| `rotate` | Giro de 360° en loop infinito | el `<group>` que envuelve todo el vector (`rotation`) | — |
| `fade` | Fundido + pulso de escala (0.85↔1.0) en loop infinito | el mismo `<group>` (`alpha`, `scaleX`, `scaleY`) | — |
| `draw` | Efecto de "dibujado" (`trimPathEnd` 0→1), con retraso escalonado por trazo (`--stagger`) | cada `<path>` individualmente | que el path tenga `stroke` (trimPath solo afecta el trazo, no el relleno — si ningún path tiene stroke, el programa avisa) |

### Ejemplo

```bash
python3 svg_animator.py convert icono.svg -o app/src/main/res --animation draw --duration 900 --stagger 150 --name ic_check
```

Genera:

```
app/src/main/res/drawable/ic_check.xml
app/src/main/res/drawable/ic_check_animated.xml
app/src/main/res/animator/ic_check_draw_0.xml
app/src/main/res/animator/ic_check_draw_1.xml
...
```

Salida real para un SVG de 3 figuras (círculo relleno, cuadrado con borde, triángulo) con `--animation rotate`:

**`drawable/sample.xml`**
```xml
<?xml version="1.0" encoding="utf-8"?>
<vector xmlns:android="http://schemas.android.com/apk/res/android" android:width="24dp" android:height="24dp" android:viewportWidth="24" android:viewportHeight="24">
    <group android:name="sample_group" android:pivotX="12" android:pivotY="12">
        <path android:name="path_1" android:pathData="M16.4,10 C16.4,13.535 13.535,16.4 10,16.4 C6.465,16.4 3.6,13.535 3.6,10 C3.6,6.465 6.465,3.6 10,3.6 C13.535,3.6 16.4,6.465 16.4,10 Z" android:fillColor="#ffff5722" android:fillAlpha="0.9"/>
        <path android:name="path_2" android:pathData="M4.4,3.6 L7.6,3.6 C8.042,3.6 8.4,3.958 8.4,4.4 L8.4,7.6 C8.4,8.042 8.042,8.4 7.6,8.4 L4.4,8.4 C3.958,8.4 3.6,8.042 3.6,7.6 L3.6,4.4 C3.6,3.958 3.958,3.6 4.4,3.6 Z" android:strokeColor="#ff2196f3" android:strokeWidth="1.5"/>
        <path android:name="path_3" android:pathData="M4,20 L12,4 L20,20 C14.667,22.667 9.333,22.667 4,20 Z" android:fillColor="#ff4caf50"/>
    </group>
</vector>
```

**`drawable/sample_animated.xml`**
```xml
<?xml version="1.0" encoding="utf-8"?>
<animated-vector xmlns:android="http://schemas.android.com/apk/res/android" android:drawable="@drawable/sample">
    <target android:name="sample_group" android:animation="@animator/sample_rotate"/>
</animated-vector>
```

**`animator/sample_rotate.xml`**
```xml
<?xml version="1.0" encoding="utf-8"?>
<objectAnimator xmlns:android="http://schemas.android.com/apk/res/android" android:propertyName="rotation" android:valueFrom="0" android:valueTo="360" android:valueType="floatType" android:duration="1000" android:repeatCount="infinite" android:interpolator="@android:anim/linear_interpolator"/>
```

## `verify` — Verificar XML existentes

```bash
python3 svg_animator.py verify <archivo1.xml> [archivo2.xml ...]
```

Acepta uno o varios archivos — típicamente el `vector` y su `animated-vector`
juntos (para poder cruzar referencias), pero también sirve con un solo archivo,
o con un `objectAnimator`/`set` suelto.

Cada archivo se identifica por su raíz (`<vector>`, `<animated-vector>`,
`<objectAnimator>`, `<set>`) y se valida según corresponda:

- **`<vector>`**: declaración `xmlns:android`, `android:width`/`height` (con unidad),
  `android:viewportWidth`/`viewportHeight` (numéricos, > 0), cada `path`/`group`/`clip-path`
  con `android:name` único, `pathData` no vacío y sintácticamente válido, colores en
  formato `#RRGGBB`/`#AARRGGBB`, alphas (`fillAlpha`, `strokeAlpha`, `trimPath*`) en `[0,1]`.
- **`<animated-vector>`**: `android:drawable="@drawable/..."` válido (y coincidente con
  el vector dado, si se pasó), cada `<target>` con `name`/`animation` válidos, que el
  `name` exista en el vector dado, y que el `@animator/...` referenciado se pueda
  localizar junto al archivo (carpeta `animator/` hermana de `drawable/`).
- **`<objectAnimator>`/`<set>`**: `propertyName` conocido, `duration`/`startOffset`
  numéricos, `repeatCount` entero o `infinite`, `repeatMode` válido, `valueType`
  coherente con la propiedad (`colorType` para colores, `pathType` para `pathData`,
  `floatType` para el resto).

Termina con código de salida `1` si hay algún `ERROR` (útil para CI).

### Ejemplo

```bash
python3 svg_animator.py verify res/drawable/ic_check.xml res/drawable/ic_check_animated.xml
```

```
== res/drawable/ic_check.xml  <vector> ==
  Sin problemas detectados.

== res/drawable/ic_check_animated.xml  <animated-vector> ==
  Sin problemas detectados.
```

Ejemplo con un XML con errores (nombre duplicado, color sin `#`, alpha fuera de
rango, `pathData` vacío):

```
== roto.xml  <vector> ==
  [WARNING] roto.xml: android:width='24' no tiene unidad (dp/px/...).
  [ERROR] roto.xml: Falta android:height.
  [ERROR] roto.xml :: path '(sin nombre)': android:fillColor='ff0000' tiene un formato de color invalido (usa #RRGGBB o #AARRGGBB).
  [WARNING] roto.xml :: path 'dup': android:fillAlpha=1.5 deberia estar en [0,1].
  [ERROR] roto.xml :: <path>: android:name='dup' duplicado.
  [ERROR] roto.xml :: path 'dup': Falta android:pathData o esta vacio.
  -> 4 error(es), 2 advertencia(s) sin resolver, 0 corregido(s).
```

## `repair` — Reparar XML existentes

```bash
python3 svg_animator.py repair <archivo1.xml> [archivo2.xml ...] [--write]
```

Ejecuta el mismo análisis que `verify`, pero **corrige automáticamente** lo que
se puede inferir sin adivinar la intención del autor:

| Problema | Corrección automática |
|---|---|
| Falta `xmlns:android` | se añade al reescribir el archivo |
| `width`/`height` sin unidad | se le agrega `dp` |
| Falta `width`/`height` | se deriva de `viewportWidth`/`viewportHeight` |
| Falta `viewportWidth`/`viewportHeight` | se deriva de `width`/`height` |
| `path`/`group` sin `android:name` | se asigna uno único (`path_1`, `group_2`, ...) |
| `android:name` duplicado | se renombra el duplicado (`dup` → `dup_2`) y se avisa revisar referencias |
| Color sin `#`, en 3/6/8 hex, o nombre (`red`) | se normaliza a `#AARRGGBB` |
| `fillAlpha`/`strokeAlpha`/`trimPath*` fuera de `[0,1]` | se recorta (clamp) al rango |
| `android:drawable` del `animated-vector` no coincide con el vector dado | se corrige a `@drawable/<nombre-del-vector>` |
| Falta `duration` en un `objectAnimator` | se asigna `300` (ms) por defecto |
| `valueType` no coincide con la propiedad | se corrige (`colorType`/`pathType`/`floatType`) |

Lo que **no** se puede inferir (p. ej. un `pathData` vacío, un color
irreconocible, un `target` que no existe en el vector, un `@animator/...` que no
se encuentra en disco, un `repeatCount` con texto inválido) se reporta como
`ERROR`/`WARNING` sin resolver, para que lo arregles a mano.

Por defecto es un **dry-run**: muestra el reporte (incluyendo lo que *se
corregiría*) pero no toca el disco. Con `--write` aplica los cambios — y antes
de sobrescribir, guarda una copia del original como `archivo.xml.bak`.

### Ejemplo

```bash
python3 svg_animator.py repair res/drawable/ic_check.xml --write
```

```
== res/drawable/ic_check.xml  <vector> ==
  [FIXED] res/drawable/ic_check.xml: android:width='24' no tenia unidad; se le agrego 'dp'.
  [FIXED] res/drawable/ic_check.xml :: <path>: Sin android:name; se asigno 'path_1'.
  -> 0 error(es), 0 advertencia(s) sin resolver, 2 corregido(s).

  escrito res/drawable/ic_check.xml
  (respaldo del original en res/drawable/ic_check.xml.bak)
```

## `list` — Listar los trazos de un vector

```bash
python3 svg_animator.py list <vector.xml> [--animated <animated-vector.xml>] [--json]
```

| Opción | Descripción |
|---|---|
| `file` (posicional) | Archivo `<vector>` a inspeccionar |
| `--animated ARCHIVO` | `animated-vector` asociado, para mostrar qué animación usa cada trazo/group |
| `--json` | Salida en JSON en lugar de tabla de texto (para scripting) |

Para cada `group`/`path`/`clip-path` muestra: nombre, jerarquía (indentado),
`fill`/`stroke`/alphas/`fillType`, longitud de `pathData`, y un **bounding box
aproximado** (calculado a partir de los puntos de control de las curvas, no es
exacto para curvas muy pronunciadas pero da una idea fiable del tamaño/posición).
Si se pasa `--animated`, añade una línea `animacion:` con un resumen de la
animación que le corresponde (propiedad, duración, offset, repetición).

### Ejemplo (texto)

```bash
python3 svg_animator.py list res/drawable/sample.xml --animated res/drawable/sample_animated.xml
```

```
res/drawable/sample.xml: vector 24dpx24dp, viewport 24x24
  [group] sample_group  pivotX=12 pivotY=12
      animacion: rotation 0->360 dur=1000ms repeat=infinite
    [path] path_1  fill=#ffff5722 fillAlpha=0.9
        pathData: 132 caracteres, bbox~=(3.6, 3.6, 16.4, 16.4)
    [path] path_2  stroke=#ff2196f3 strokeWidth=1.5
        pathData: 162 caracteres, bbox~=(3.6, 3.6, 8.4, 8.4)
    [path] path_3  fill=#ff4caf50
        pathData: 53 caracteres, bbox~=(4.0, 4.0, 20.0, 22.67)
```

Con una animación `draw` (por trazo, con desfase), cada `path` trae su propia
línea de animación:

```
    [path] path_2  stroke=#ff2196f3 strokeWidth=1.5
        pathData: 162 caracteres, bbox~=(3.6, 3.6, 8.4, 8.4)
        animacion: trimPathEnd 0->1 dur=1000ms offset=150ms
```

### Ejemplo (JSON)

```bash
python3 svg_animator.py list res/drawable/sample.xml --json
```

```json
{
  "file": "res/drawable/sample.xml",
  "width": "24dp",
  "height": "24dp",
  "viewportWidth": "24",
  "viewportHeight": "24",
  "items": [
    { "kind": "group", "depth": 0, "name": "sample_group", "pivotX": "12", "pivotY": "12", "animation": null },
    { "kind": "path", "depth": 1, "name": "path_1", "pathDataLength": 132,
      "bbox": [3.6, 3.6, 16.4, 16.4], "fillColor": "#ffff5722", "fillAlpha": "0.9",
      "fillType": null, "strokeColor": null, "strokeWidth": null, "strokeAlpha": null, "animation": null }
  ]
}
```

## `edit` — Editar atributos

```bash
python3 svg_animator.py edit <archivo.xml> [--target NOMBRE | --property NOMBRE | --index N] --set clave=valor [--set clave=valor ...] [--write]
```

| Opción | Descripción |
|---|---|
| `file` (posicional) | Archivo a editar: `vector`, `animated-vector`, `objectAnimator` o `set` |
| `--target NOMBRE` | `android:name` del `path`/`group`/`target` a editar. Si se omite, se edita la raíz del archivo |
| `--property NOMBRE` | Selecciona, dentro de un `<set>`, el `objectAnimator` hijo cuyo `propertyName` coincida |
| `--index N` | Selecciona, dentro de un `<set>`, el hijo N (0-based) |
| `--set clave=valor` | Atributo `android:*` a modificar (sin el prefijo `android:`). Repetible |
| `--write` | Aplica los cambios a disco (crea `archivo.xml.bak`) |

Sin `--write` solo muestra el diff (`clave: valor_viejo -> valor_nuevo`) sin
tocar el archivo.

### Ejemplos

Cambiar la duración y el desfase de un animador:

```bash
python3 svg_animator.py edit res/animator/ic_check_draw_1.xml --set duration=600 --set startOffset=200 --write
```

Cambiar el color de relleno de un path concreto dentro del vector:

```bash
python3 svg_animator.py edit res/drawable/ic_check.xml --target path_1 --set fillColor=#2196F3 --write
```

Redirigir un `target` del `animated-vector` a otro animador:

```bash
python3 svg_animator.py edit res/drawable/ic_check_animated.xml --target path_2 --set animation=@animator/ic_check_draw_0 --write
```

Editar, dentro de un `<set>` (p. ej. el animador de `fade`), solo el
`objectAnimator` de `scaleX`:

```bash
python3 svg_animator.py edit res/animator/ic_check_fade.xml --property scaleX --set valueFrom=0.6 --write
```

Salida típica (modo previsualización, sin `--write`):

```
Editando <path name='path_1'> en res/drawable/ic_check.xml
  android:fillColor: '#ffff5722' -> '#2196F3'

(No se escribio nada a disco; vuelve a ejecutar con --write para aplicar los cambios.)
```

## `menu` — Menú interactivo

```bash
python3 svg_animator.py            # sin argumentos abre el menú directamente
python3 svg_animator.py menu       # equivalente, explícito
```

Pensado para no tener que recordar nombres de archivos ni de atributos. Mantiene
una pequeña "sesión de trabajo" (un `vector` y un `animated-vector` cargados) que
se reutiliza en las demás opciones sin volver a escribir rutas:

```
--- Menu principal ---
1) Convertir un SVG nuevo
2) Cargar archivo(s) vector / animated-vector para trabajar
3) Verificar archivo(s) cargado(s)
4) Reparar archivo(s) cargado(s)
5) Listar los trazos del vector cargado
6) Editar atributos (vector, animated-vector u otro archivo)
0) Salir
```

- **Opción 1**: pide la ruta del SVG y las mismas opciones que `convert` (con
  valores por defecto sugeridos); al terminar, ofrece cargar los archivos
  generados directamente en la sesión.
- **Opción 2**: carga manualmente un `vector`/`animated-vector` existentes.
- **Opciones 3–5**: equivalentes a `verify`/`repair`/`list`, usando los archivos
  cargados (permiten añadir un archivo extra puntual, p. ej. un `objectAnimator`).
- **Opción 6**: el editor interactivo de atributos — ver detalle abajo.

### Edición con atajos de atributos (opción 6)

En vez de escribir `clave=valor` de memoria, al elegir qué `path`/`group`/`target`/
`objectAnimator` editar, el menú muestra sus atributos típicos **con el valor
actual**, numerados:

```
Atributos rapidos para <path> 'path_1':
   1) fillColor       color de relleno                 (actual: #ffff5722)
   2) fillAlpha       opacidad de relleno (0-1)        (actual: 0.9)
   3) fillType        regla de relleno                 (actual: -)
   4) strokeColor     color de trazo                   (actual: -)
   ...
  10) name            nombre                           (actual: path_1)
   L) atributo libre (clave=valor)
```

Con inteligencia según el tipo de atributo:

- **Colores** (`fillColor`, `strokeColor`): se puede escribir un hex o un
  **nombre de color** (`red`, `blue`, `orange`, ...) y se normaliza solo a
  `#AARRGGBB`.
- **Enumeraciones** (`fillType`, `valueType`, `repeatMode`, `ordering`,
  `repeatCount`, `propertyName`, `interpolator`): se listan las opciones válidas
  para elegir por número, sin tener que recordarlas.
- **`animation`** (en un `target`): explora la carpeta `animator/` hermana del
  archivo y lista los recursos disponibles para elegir por número, en vez de
  escribir `@animator/nombre` a mano.
- **`L` (atributo libre)**: siempre disponible para cualquier atributo no
  listado — no se pierde flexibilidad.

Se pueden encadenar varios atajos antes de confirmar (se muestra un resumen en
vivo de "cambios preparados"); al terminar (Enter en blanco) se pregunta si
aplicar a disco, igual que en `edit`.

## Qué se soporta del SVG y limitaciones conocidas

**Soportado:**
- `path` (comandos `M L H V C S Q T A Z`, absolutos y relativos)
- `rect` (con esquinas redondeadas `rx`/`ry`), `circle`, `ellipse`, `line`, `polyline`, `polygon`
- Grupos `<g>` anidados, con `transform` (`translate`, `scale`, `rotate`, `skewX`, `skewY`, `matrix`, combinados)
- `viewBox` con offset distinto de `0 0`
- Colores: `#rgb`, `#rrggbb`, `#rrggbbaa`, `rgb()`/`rgba()`, nombres básicos (`black`, `red`, `orange`, ...)
- `fill`, `stroke`, `stroke-width`, `opacity`, `fill-opacity`, `stroke-opacity`, `fill-rule`, herencia vía atributos o `style="..."`

Las curvas (incluyendo arcos `A`, convertidos primero a Bézier) son transformadas
de forma exacta bajo cualquier combinación de traslación/escala/rotación/sesgo,
porque las curvas de Bézier son invariantes frente a transformaciones afines.

**No soportado (se ignora, con aviso cuando aplica):**
- `<use>`, gradientes (`linearGradient`/`radialGradient`), `<clipPath>`/`<mask>` como recorte real, `<text>`, `<image>`, CSS externo/`<style>` con selectores (solo se lee `style="..."` inline).

## Integración en un proyecto Android

```bash
python3 svg_animator.py convert mi_icono.svg -o app/src/main/res --animation draw
```

En el layout:

```xml
<ImageView
    android:id="@+id/icon"
    android:layout_width="48dp"
    android:layout_height="48dp"
    android:src="@drawable/mi_icono_animated" />
```

Para disparar la animación en código (el `src` por sí solo no la inicia):

```kotlin
val drawable = icon.drawable as? AnimatedVectorDrawableCompat
    ?: AnimatedVectorDrawableCompat.create(context, R.drawable.mi_icono_animated)
icon.setImageDrawable(drawable)
drawable?.start()
```

`AnimatedVectorDrawableCompat` (de `androidx.vectordrawable:vectordrawable-animated`)
es la opción recomendada para compatibilidad hacia atrás; en API 25+ también
funciona `AnimatedVectorDrawable` directamente.
