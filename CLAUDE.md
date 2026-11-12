# FlexStock-lite

Sistema de inventario y facturación para una ferretería. Python 3.13 + Tkinter
(CustomTkinter) + SQLite.
Migra el catálogo de 1.936 productos de un sistema DOS de ~2005.

El código, los nombres de tablas y los identificadores van en **inglés**.
La interfaz, los comentarios y los mensajes de error van en **español**.

## Comandos

```bash
python app.py          # arranca la aplicación
pytest -q              # tests (deben pasar antes de cualquier commit)
ruff check . && ruff format .
```

Los tests abren cientos de ventanas Tk. Con **Xvfb** instalado (Arch:
`sudo pacman -S xorg-server-xvfb`) `tests/conftest.py` las manda a una pantalla
invisible y no molestan a quien usa la PC; sin Xvfb van a la pantalla real y roban
el foco mientras corren. `FLEXSTOCK_TESTS_EN_PANTALLA=1 pytest ...` fuerza la
pantalla real, para mirar un test de diseño.

## Arquitectura

Las dependencias apuntan hacia adentro: `ui/ → services/ → domain/ ← data/`

- `domain/` — entidades, enums, errores, conversión de dinero.
  **Prohibido importar `sqlite3` o `tkinter` aquí.** Sin excepciones.
- `data/` — repositorios y `schema.sql`. **Único lugar del proyecto con SQL.**
- `services/` — reglas que tocan varias entidades (facturar, ajustar stock).
- `ui/` — CustomTkinter sobre Tkinter. Llama solo a `services/`, nunca a `data/`.
  Los widgets salen de `ui/kit.py` (`kit.Button`, `kit.Entry`, `kit.LabelFrame`...):
  son CTk pero entienden la interfaz ttk que ya usaban las paginas (`style=`,
  `width` en caracteres, `state()`, `current()`, `<<ComboboxSelected>>`).
  **No crear `ttk.Button`/`ttk.Entry`/`ttk.Label` nuevos**: se verian del
  diseno viejo y no seguirian el modo oscuro. Quedan en ttk/tk solo los que
  CTk no tiene: `ttk.Treeview` (tablas), `tk.Listbox`, `tk.Text`. Los
  dialogos heredan de `kit.Dialog`, y para pedir un dato suelto se usa
  `ui/simpledialog.py` (misma firma que la de tkinter).
- `importers/` — migración desde los `.txt` del sistema viejo.
- `reports/` — PDF con ReportLab.

Antes de escribir una consulta, leer `flexstock/data/schema.sql`.

## Frontend (CustomTkinter): cómo aprovecharlo

Mapa rápido de `ui/`:

| Archivo | Qué es |
|---|---|
| `kit.py` | Widgets CTk con interfaz ttk (`style=`, `width` en caracteres, `state()`, `current()`). `kit.Button(icon=...)`. |
| `theme.py` | Paleta (`LIGHT`/`DARK`), `pair("accent")` → `(claro, oscuro)`, `ctk_font(widget, "heading")`, tema CTk global. |
| `icons.py` + `assets/icons/` | Iconos Lucide (ISC) en PNG blanco 64 px, teñidos en memoria. |
| `widgets.py` | `SearchableTable`, `RateCard`, `toast()`, diálogos de carga, helpers de tamaño. |
| `main_window.py` | Barra lateral agrupada (Inicio / VENTAS / INVENTARIO / Configuración) y estado del respaldo abajo. |
| `home_page.py`, `config_page.py` | Inicio (tablero) y Configuración (tienda, respaldo, apariencia, columnas, modo avanzado). |

Reglas para que todo siga viéndose igual de bien:

- **Colores siempre por `pair()`** (o un `style=` de `kit`), nunca un `"#hex"` suelto en
  una página: un hex fijo no cambia con el modo oscuro. Un color nuevo se agrega a
  `Palette` en los DOS modos.
- **Letra siempre por `ctk_font(widget, rol)`**: son fuentes compartidas, Ctrl+/Ctrl-
  las reescala todas de una. Un `font=("Arial", 14)` suelto no escala.
- **Avisos**: lo que no pide decisión ("Respaldo creado", "PDF generado") va con
  `toast()`; lo que hay que leer sí o sí (errores, confirmaciones) sigue en `messagebox`.
- **Iconos nuevos**: bajar el SVG de una versión FIJA de `lucide-static`
  (`https://unpkg.com/lucide-static@0.460.0/icons/<nombre>.svg`), cambiar
  `currentColor` por `#ffffff`, pasarlo a PNG con `rsvg-convert -w 64 -h 64` y
  guardarlo en `assets/icons/`. Nada de emojis de color: Tk 8.6 en Linux no los
  dibuja siempre. Tamaños: 18 en botones, 20 en la barra, 22 en tarjetas.
- **Imágenes** (logo, fotos de producto si algún día): siempre `ctk.CTkImage` con Pillow
  (que ya viene con ReportLab), nunca `tk.PhotoImage`: CTkImage acepta JPG/PNG/etc.,
  escala en pantallas de alta densidad y puede tener versión clara y oscura. Una imagen
  grande se achica UNA vez al cargarla (`thumbnail`), no en cada repintado; y si es
  de la base (como el logo), vive en `settings`/una tabla, no como archivo suelto.
- **Rendimiento**: un widget CTk cuesta ~6 ms en crearse (un ttk, < 1 ms), porque se
  dibuja a mano en un Canvas. Para pocos controles no se nota; para MUCHAS filas sí:
  **una lista larga va en `ttk.Treeview` o `tk.Listbox`, nunca un widget por fila**
  (1.949 productos × 3 widgets serían ~35 s). Y no destruir/recrear un formulario
  entero en cada tecla: se actualizan sus variables.
- **Tablas**: `ttk.Treeview` con el estilo de `theme.py` (sin marco, filas alternadas
  con el tag `franja`, títulos alineados como sus datos). No admite botones ni iconos
  dentro de una celda: si alguna vez hace falta, va en la barra de acciones.
- **Tests de diseño** con `mostrar_con_tamano` (ver más abajo). El modo claro/oscuro de
  CTk es global al proceso: `conftest.py` lo vuelve a claro después de cada test.

Ideas que quedan por hacer en la interfaz, por impacto:
1. **Nueva factura / Nueva cotización por pasos** (1 Cliente → 2 Productos → 3 Confirmar)
   con el total grande y fijo a la derecha: son las pantallas más cargadas.
2. **Atajos de teclado globales** (F2 nueva factura, Ctrl+F buscar) mostrados en la
   barra lateral.
3. **Ficha del producto con foto — OPCIONAL** (pedido el 23/10/2026: "dejarlo
   como opcional"). `ctk.CTkImage` con Pillow, guardada en la base como el logo
   (una tabla `product_images` o una columna BLOB, no archivos sueltos), achicada
   UNA vez al cargarla. Que la ficha y Nueva factura funcionen igual sin foto.
4. **Gráfico de ventas del mes** en Inicio (matplotlib embebido: sería dependencia
   nueva, justificarla antes).

## Reglas innegociables

1. **Dinero en centavos, cantidades en milésimas.** Nunca `float` ni `REAL` para
   valores monetarios. Toda conversión pasa por `domain/money.py`; si aparece un
   `* 100` o `* 1000` fuera de ese archivo, es un bug.
2. **`products.current_stock` es un caché.** Solo `StockService` lo modifica, y
   siempre en la misma transacción que su `InventoryMovement`. La verdad del
   stock es la suma de `inventory_movements`.
3. **`inventory_movements` es append-only.** Nunca `UPDATE` ni `DELETE` ahí.
4. **Las facturas no se borran ni se editan.** Se anulan (`status='CANCELLED'`)
   o se corrigen con una nota de crédito que las referencia.
5. **Snapshots en documentos.** `invoices.tax_rate`, `invoices.bs_rate`,
   `invoices.ref_rate` e
   `invoice_lines.unit_price_cents` se copian al emitir. Reimprimir una factura
   vieja nunca puede cambiar su monto. `bs_rate` es la tasa Bs/$ con que se pasó
   la factura a bolívares al confirmarla (la BCV o la de referencia que se eligió):
   el PDF de una factura sale **solo en Bs** con esa tasa. La factura sigue
   guardándose en $; una anterior a ese campo (`bs_rate` NULL) usa la BCV vigente.
   La cuenta $→Bs vive en un solo lugar, `domain/bs_breakdown.py`, y la usan la
   pantalla, el detalle y el PDF para que den siempre los mismos números.
6. **Producto ↔ proveedor es muchos-a-muchos** (`product_suppliers`). 308 productos
   del catálogo tienen dos proveedores. Nunca proponer un FK en `products`.
7. **Precios sin impuesto.** El impuesto se aplica al facturar, leyendo la tasa de
   `settings`, y se congela en la factura. El check "Cobrar IVA" arranca
   **desmarcado** en factura y cotización: se tilda a propósito.
   Con **Modo avanzado**, al agregar una línea el precio del nivel elegido entra
   multiplicado por `tasa de referencia / BCV` (`agregar_linea(price_factor=...)`),
   así la factura queda expresada en $ a tasa BCV y cobrarla a la BCV da los
   mismos Bs que cobrar el precio del catálogo a la tasa de referencia. Ningún
   monto sale de la tasa de referencia: la factura cobra con `bs_rate` (la BCV)
   y guarda la de referencia en `ref_rate` solo para poder volver al precio de
   lista (`domain/bs_breakdown.py::catalog_total`, columna "Total $ ref" —
   la clave sigue siendo `total_catalogo`: renombrarla le apagaría la
   columna a quien ya la tenía prendida).
8. **Rutas con `pathlib`**, nunca strings. Archivos siempre con `encoding=` explícito.
   El proyecto corre en Windows y en Arch Linux.
9. `PRAGMA foreign_keys = ON` en **cada** conexión: SQLite las ignora por defecto.
10. La base de datos **nunca** entra a Git.

## Política de carga en los repositorios

Un repositorio no adivina qué necesita quien lo llama. Ofrece contratos distintos
y el nombre del método dice cuál es:

- `list_summary(...)` — sin relaciones. Para tablas de listado (1.936 filas).
- `get(id)` — completo, con precios y proveedores. Para la ficha de un registro.
- `load_suppliers_for(products)` — carga en lote: **una** consulta
  `WHERE product_id IN (...)` y agrupación en memoria.

**Prohibido iterar una lista llamando a un método que consulta la base por cada
elemento.** Eso es el N+1. Si hacen falta relaciones para varios registros, se
usa carga en lote o un `JOIN`, nunca un bucle de consultas.

## Datos de origen

Tres `.txt` en **CP850** (DOS), ancho fijo, CRLF. Son el mismo catálogo tres veces:

- `PRODR.TXT` — maestro: código, descripción, costo, existencia, precio 1/2/3
- `LP.TXT` — redundante: los mismos precios × 1,16 (IVA). Solo para verificar
- `INV.TXT` — aporta la unidad de medida

Bajo cada producto hay 0, 1 o 2 líneas indentadas con formato
`fecha-PROVEEDOR/CONTACTO`. Hay 700 variantes de nombre que corresponden a
~80-150 proveedores reales. La normalización se revisa a mano vía CSV, no se
adivina automáticamente.

## Cómo trabajar en este repo

- Un cambio por vez. Correr `pytest` después de cada uno.
- Ante una decisión de arquitectura con trade-offs, exponer las opciones y
  esperar respuesta en vez de elegir en silencio.
- **Un test nunca espera por reloj.** Nada de `time.sleep(...)` seguido de
  un `update()` suelto, ni de asumir cuántos ciclos tarda el gestor de
  ventanas en aplicar un `geometry()`: con la máquina cargada eso falla
  sin que nada esté roto, y un test que falla a veces enseña a ignorar el
  rojo. Se usa la fixture `esperar` de `tests/conftest.py`, que corre el
  mainloop hasta que se cumple la condición. Ahí mismo vive la guarda que
  impide que un test salga a internet de verdad.
- **Un test que mide el diseno usa `mostrar_con_tamano`** (tambien en
  `tests/conftest.py`), no una ventana oculta: Tk no termina de acomodar
  los widgets de una ventana que nunca se mostro, y un boton puede quedar
  en 1 px de alto aunque en la ventana real se vea perfecto.
- No agregar dependencias sin justificarlas.
- Al explicar una solución, incluir qué pasa a nivel de sistema: complejidad,
  número de consultas, memoria. El autor es estudiante y busca aprender, no
  solo recibir código que funcione.

---

*Versión pública: se omiten las secciones de estado y pendientes del
negocio real (datos de uso, revisiones de la base y decisiones con el
dueño).*
