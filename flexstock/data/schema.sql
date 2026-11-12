-- ============================================================
-- FlexStock-lite  |  esquema v1
-- ============================================================
-- CONVENCIONES DE ESTE ESQUEMA
--
-- 1. Dinero  -> INTEGER en centavos.        1,50 se guarda como 150
-- 2. Cantidad-> INTEGER en milesimas.       2,5 kg se guarda como 2500
--    SQLite no tiene DECIMAL real: los REAL producen errores de
--    redondeo que descuadran la caja. Enteros escalados no fallan.
--    La conversion vive en flexstock/domain/money.py y en ningun otro lado.
-- 3. Fechas  -> TEXT ISO-8601 (YYYY-MM-DD HH:MM:SS). SQLite no tiene
--    tipo fecha; el formato ISO ordena correctamente como texto.
-- 4. Booleanos -> INTEGER 0/1.
-- ============================================================


-- ------------------------------------------------------------
-- CONFIGURACION
-- Clave/valor. Todo lo que el dueno pueda querer cambiar sin
-- tocar codigo vive aqui.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);


-- ------------------------------------------------------------
-- CATALOGO
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT    NOT NULL UNIQUE,   -- prefijo del SKU: 'FER', 'ELE'
    name        TEXT    NOT NULL UNIQUE,
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS suppliers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sku           TEXT    NOT NULL UNIQUE,  -- codigo nuevo: 'PROV-0001', autogenerado
    name          TEXT    NOT NULL UNIQUE,  -- nombre canonico: 'LIMAR', 'MEDINAS'
    tax_id        TEXT,                     -- RIF / identificacion fiscal
    phone         TEXT,
    email         TEXT,
    address       TEXT,
    contact_name  TEXT,                     -- contacto principal del proveedor
                                             -- (distinto del contacto por producto
                                             -- en product_suppliers, que es de la
                                             -- RELACION, no del proveedor)
    notes         TEXT,
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS products (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    sku            TEXT    NOT NULL UNIQUE,  -- codigo nuevo: 'FER-0001'
    legacy_code    TEXT             UNIQUE,  -- codigo del sistema viejo: 'Z10-05'
                                             -- puente de auditoria, NO borrar
    name           TEXT    NOT NULL,         -- descripcion corta (del txt, 40 chars)
    description    TEXT    NOT NULL DEFAULT '',  -- texto largo, se llena con el tiempo
    category_id    INTEGER          REFERENCES categories(id) ON DELETE SET NULL,
    unit           TEXT    NOT NULL DEFAULT 'UNI',
    cost_milli     INTEGER NOT NULL DEFAULT 0 CHECK (cost_milli >= 0),  -- costo unitario
    current_stock  INTEGER NOT NULL DEFAULT 0,  -- CACHE. La verdad son los movimientos.
    min_stock      INTEGER NOT NULL DEFAULT 0,  -- para el reporte de bajo stock
    is_active      INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at     TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_products_name        ON products(name);
CREATE INDEX IF NOT EXISTS idx_products_category    ON products(category_id);
CREATE INDEX IF NOT EXISTS idx_products_legacy_code ON products(legacy_code);


-- ------------------------------------------------------------
-- NIVELES DE PRECIO
-- 'Precio 1/2/3' del sistema viejo, pero con nombre.
-- Tabla propia para poder agregar un cuarto nivel sin migrar.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS price_tiers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,   -- 'Detal', 'Mayor', 'Especial'
    position    INTEGER NOT NULL UNIQUE,   -- orden en la UI
    is_default  INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1))
);

CREATE TABLE IF NOT EXISTS product_prices (
    product_id   INTEGER NOT NULL REFERENCES products(id)    ON DELETE CASCADE,
    tier_id      INTEGER NOT NULL REFERENCES price_tiers(id) ON DELETE CASCADE,
    price_milli  INTEGER NOT NULL CHECK (price_milli >= 0),  -- SIN impuesto
    PRIMARY KEY (product_id, tier_id)
);


-- ------------------------------------------------------------
-- PRODUCTO <-> PROVEEDOR   (muchos-a-muchos)
-- 308 productos del txt tienen DOS proveedores. Un FK no alcanza.
-- La fecha y el contacto pertenecen a la RELACION, no a ninguna
-- de las dos entidades.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS product_suppliers (
    product_id     INTEGER NOT NULL REFERENCES products(id)  ON DELETE CASCADE,
    supplier_id    INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    contact_name   TEXT,          -- 'RAMON' puede repetirse entre proveedores
    last_purchase  TEXT,          -- fecha ISO tomada del txt
    last_cost_milli INTEGER,      -- a cuanto compro la ultima vez
    note           TEXT,          -- 'SOLO PRECIO', etc.
    PRIMARY KEY (product_id, supplier_id)
);

CREATE INDEX IF NOT EXISTS idx_prodsup_supplier ON product_suppliers(supplier_id);

-- Historial de entregas: cada "Registrar proveedor..." agrega una fila
-- aca, que nunca se UPDATE ni se DELETE (mismo criterio que
-- inventory_movements con products.current_stock). product_suppliers de
-- arriba sigue siendo el vinculo ACTUAL -a quien se le compra esto hoy,
-- lo que lee "Quitar"- pero ahora es un cache de la ULTIMA fila de aca
-- en vez del unico lugar donde vive el dato: asi "Registrar proveedor"
-- dos veces para el mismo par ya no pisa la fecha/costo de la vez
-- anterior.
CREATE TABLE IF NOT EXISTS product_supplier_entries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id    INTEGER NOT NULL REFERENCES products(id)  ON DELETE CASCADE,
    supplier_id   INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    contact_name  TEXT,
    entry_date    TEXT,          -- fecha ISO de la entrega
    cost_milli    INTEGER,       -- a cuanto se compro ESTA entrega
    note          TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_prodsupentries_product_supplier
    ON product_supplier_entries(product_id, supplier_id);
CREATE INDEX IF NOT EXISTS idx_prodsupentries_supplier
    ON product_supplier_entries(supplier_id);


-- ------------------------------------------------------------
-- CLIENTES
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS customers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sku           TEXT    NOT NULL UNIQUE,  -- codigo nuevo: 'CLI-0001', autogenerado
    name          TEXT    NOT NULL,
    tax_id        TEXT             UNIQUE,  -- cedula / RIF: 'V-12345678', 'J-12345678'
    phone         TEXT,
    email         TEXT,
    address       TEXT,
    notes         TEXT,
    is_wholesale  INTEGER NOT NULL DEFAULT 0 CHECK (is_wholesale IN (0, 1)),
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_customers_name ON customers(name);


-- ------------------------------------------------------------
-- NUMERACION DE DOCUMENTOS
-- El id de la tabla NO es el numero de factura. Son dos cosas.
-- Editando next_number aqui se retoma desde donde haga falta.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS document_series (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_type     TEXT    NOT NULL CHECK (doc_type IN ('INVOICE', 'CREDIT_NOTE')),
    prefix       TEXT    NOT NULL UNIQUE,  -- 'FAC-', 'NC-'
    next_number  INTEGER NOT NULL DEFAULT 1 CHECK (next_number > 0),
    padding      INTEGER NOT NULL DEFAULT 6  -- FAC-000531
);


-- ------------------------------------------------------------
-- FACTURAS
-- tax_rate y los precios de linea son SNAPSHOTS: se copian al
-- emitir. Si manana sube el impuesto, las facturas viejas no
-- pueden cambiar de monto.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS invoices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id       INTEGER NOT NULL REFERENCES document_series(id),
    number          INTEGER NOT NULL,          -- para ordenar
    display_number  TEXT    NOT NULL UNIQUE,   -- 'FAC-000531', para mostrar
    customer_id     INTEGER          REFERENCES customers(id) ON DELETE SET NULL,
    customer_name   TEXT    NOT NULL,          -- SNAPSHOT
    customer_tax_id TEXT,                      -- SNAPSHOT
    customer_address TEXT,                     -- SNAPSHOT (Fase 7: PDF)
    status          TEXT    NOT NULL DEFAULT 'ISSUED'
                            CHECK (status IN ('ISSUED', 'CANCELLED')),
    payment_method  TEXT    CHECK (payment_method IN
                            ('EFECTIVO_BS', 'EFECTIVO_USD', 'TRANSFERENCIA',
                             'TARJETA', 'ZELLE')),
                            -- NULL = factura emitida antes de este campo, o
                            -- (en teoria) sin forma de pago registrada. La
                            -- UI exige elegir una al confirmar una factura
                            -- NUEVA (BillingService/InvoiceDialog); a nivel
                            -- de esquema queda NULLABLE a proposito para no
                            -- romper facturas viejas (regla del CLAUDE.md
                            -- de no forzar NOT NULL por una migracion).
    tax_rate        TEXT    NOT NULL,          -- SNAPSHOT, ej '16.00'
    bs_rate         TEXT,                      -- SNAPSHOT: Bs por $ con que se
                            -- paso esta factura a bolivares al confirmarla (la
                            -- BCV o la de referencia que se eligio), ej
                            -- '848.5458'. El PDF se imprime en Bs con ESTA tasa,
                            -- asi que reimprimir una factura vieja nunca cambia
                            -- su monto (regla 5). TEXT porque es un Decimal
                            -- exacto, igual que tax_rate. NULL = factura emitida
                            -- antes de este campo: su PDF usa la tasa BCV vigente.
    ref_rate        TEXT,                      -- SNAPSHOT: la tasa de referencia
                            -- con que se alteraron los precios al facturar
                            -- (Modo avanzado, ver Regla 7). Los montos en $ ya
                            -- estan a tasa BCV; con esta se puede volver al
                            -- precio del catalogo: catalogo = total x bs_rate /
                            -- ref_rate. NULL = se cobro el precio del catalogo
                            -- tal cual (o factura anterior a este campo).
    subtotal_cents  INTEGER NOT NULL DEFAULT 0,
    tax_cents       INTEGER NOT NULL DEFAULT 0,
    total_cents     INTEGER NOT NULL DEFAULT 0,
    notes           TEXT,
    issued_at       TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    cancelled_at    TEXT,
    cancel_reason   TEXT
);

CREATE INDEX IF NOT EXISTS idx_invoices_customer ON invoices(customer_id);
CREATE INDEX IF NOT EXISTS idx_invoices_issued   ON invoices(issued_at);

CREATE TABLE IF NOT EXISTS invoice_lines (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id        INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    product_id        INTEGER          REFERENCES products(id) ON DELETE SET NULL,
    product_sku       TEXT    NOT NULL,        -- SNAPSHOT
    product_name      TEXT    NOT NULL,        -- SNAPSHOT
    unit              TEXT    NOT NULL,        -- SNAPSHOT
    quantity_milli    INTEGER NOT NULL CHECK (quantity_milli > 0),
    unit_price_cents  INTEGER NOT NULL CHECK (unit_price_cents >= 0),  -- SNAPSHOT
    line_total_cents  INTEGER NOT NULL,
    tier_id           INTEGER          REFERENCES price_tiers(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_invoice_lines_invoice ON invoice_lines(invoice_id);
CREATE INDEX IF NOT EXISTS idx_invoice_lines_product ON invoice_lines(product_id);


-- ------------------------------------------------------------
-- NOTAS DE CREDITO (devoluciones)
-- No editan la factura original: la referencian.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS credit_notes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id       INTEGER NOT NULL REFERENCES document_series(id),
    number          INTEGER NOT NULL,
    display_number  TEXT    NOT NULL UNIQUE,   -- 'NC-000012'
    invoice_id      INTEGER NOT NULL REFERENCES invoices(id),
    reason          TEXT    NOT NULL,
    tax_rate        TEXT    NOT NULL,          -- SNAPSHOT (el de la factura original)
    subtotal_cents  INTEGER NOT NULL DEFAULT 0,
    tax_cents       INTEGER NOT NULL DEFAULT 0,
    total_cents     INTEGER NOT NULL DEFAULT 0,
    issued_at       TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS credit_note_lines (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    credit_note_id    INTEGER NOT NULL REFERENCES credit_notes(id) ON DELETE CASCADE,
    invoice_line_id   INTEGER          REFERENCES invoice_lines(id) ON DELETE SET NULL,
    product_id        INTEGER          REFERENCES products(id) ON DELETE SET NULL,
    product_sku       TEXT    NOT NULL,
    product_name      TEXT    NOT NULL,
    quantity_milli    INTEGER NOT NULL CHECK (quantity_milli > 0),
    unit_price_cents  INTEGER NOT NULL,
    line_total_cents  INTEGER NOT NULL,
    restock           INTEGER NOT NULL DEFAULT 1 CHECK (restock IN (0, 1))
                      -- 0 = devuelto pero danado, no vuelve al inventario
);


-- ------------------------------------------------------------
-- COTIZACIONES / NOTAS DE ENTREGA  (Modo avanzado)
-- Documento informal, separado de invoices a proposito: no mueve
-- stock, no entra al cierre de caja, no usa document_series (su
-- CHECK de doc_type no se puede ampliar en bases ya creadas sin
-- reconstruir la tabla) - el numero sale de MAX(number) + 1 dentro
-- de la misma transaccion del INSERT.
-- Las dos tasas son SNAPSHOTS: reimprimir en Bs o en $ BCV da
-- siempre el mismo monto que el dia que se creo.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS quotations (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    number           INTEGER NOT NULL UNIQUE,
    display_number   TEXT    NOT NULL UNIQUE,   -- 'NE-000001'
    customer_id      INTEGER          REFERENCES customers(id) ON DELETE SET NULL,
    customer_name    TEXT    NOT NULL,          -- SNAPSHOT
    customer_tax_id  TEXT,                      -- SNAPSHOT
    customer_address TEXT,                      -- SNAPSHOT
    status           TEXT    NOT NULL DEFAULT 'ISSUED'
                             CHECK (status IN ('ISSUED', 'CANCELLED')),
    tax_rate         TEXT    NOT NULL,          -- SNAPSHOT; '0' = sin IVA
    personal_rate    TEXT    NOT NULL,          -- SNAPSHOT, Bs por USD propio
    bcv_rate         TEXT    NOT NULL,          -- SNAPSHOT, Bs por USD oficial
    total_cents      INTEGER NOT NULL DEFAULT 0,  -- USD
    total_bs_cents   INTEGER NOT NULL DEFAULT 0,  -- Bs
    total_bcv_cents  INTEGER NOT NULL DEFAULT 0,  -- USD a tasa BCV
    notes            TEXT,
    issued_at        TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    cancelled_at     TEXT,
    cancel_reason    TEXT
);

CREATE INDEX IF NOT EXISTS idx_quotations_issued ON quotations(issued_at);

CREATE TABLE IF NOT EXISTS quotation_lines (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    quotation_id      INTEGER NOT NULL REFERENCES quotations(id) ON DELETE CASCADE,
    product_id        INTEGER          REFERENCES products(id) ON DELETE SET NULL,
    product_sku       TEXT    NOT NULL,        -- SNAPSHOT
    product_name      TEXT    NOT NULL,        -- SNAPSHOT
    unit              TEXT    NOT NULL,        -- SNAPSHOT
    quantity_milli    INTEGER NOT NULL CHECK (quantity_milli > 0),
    unit_price_cents  INTEGER NOT NULL CHECK (unit_price_cents >= 0),  -- SNAPSHOT, USD
    line_total_cents  INTEGER NOT NULL,        -- USD
    tier_id           INTEGER          REFERENCES price_tiers(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_quotation_lines_quotation ON quotation_lines(quotation_id);


-- ------------------------------------------------------------
-- MOVIMIENTOS DE INVENTARIO   (append-only)
-- Esta es la VERDAD del stock. products.current_stock es un cache
-- reconstruible sumando esta tabla.
-- Nunca se hace UPDATE ni DELETE aqui.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS inventory_movements (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    type            TEXT    NOT NULL
                            CHECK (type IN ('IN', 'OUT', 'ADJUSTMENT', 'INITIAL')),
    quantity_milli  INTEGER NOT NULL CHECK (quantity_milli <> 0),
                    -- SIEMPRE el delta con signo: -3000 son 3 unidades menos
    unit_cost_milli INTEGER,       -- costo al momento, para valorar el inventario
    reason          TEXT,          -- obligatorio en ADJUSTMENT (lo valida el servicio)
    reference_type  TEXT           CHECK (reference_type IN
                            ('INVOICE', 'CREDIT_NOTE', 'PURCHASE', 'COUNT', 'IMPORT')),
    reference_id    INTEGER,       -- id del documento que lo origino
    counted_by      TEXT,          -- quien conto, texto libre (no hay login)
    note            TEXT,          -- bitacora opcional del ajuste
    created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_movements_product ON inventory_movements(product_id);
CREATE INDEX IF NOT EXISTS idx_movements_created ON inventory_movements(created_at);
CREATE INDEX IF NOT EXISTS idx_movements_ref     ON inventory_movements(reference_type, reference_id);


-- ============================================================
-- DATOS SEMILLA
-- ============================================================
INSERT OR IGNORE INTO settings (key, value) VALUES
    ('schema_version',    '1'),
    ('company_name',      'Ferreteria'),
    ('company_tax_id',    ''),
    ('company_address',   ''),
    ('company_phone',     ''),
    ('default_tax_rate',  '16.00'),
    ('currency_symbol',   '$'),
    ('allow_negative_stock', '1');   -- advertir pero permitir

INSERT OR IGNORE INTO categories (code, name) VALUES ('GEN', 'General');

-- Orden real de precio en el catalogo (ver PRODR.TXT: Precio1 <= Precio2
-- <= Precio3 en la inmensa mayoria de las filas): Mayor es el mas bajo
-- (venta al por mayor), Detal el mas alto (venta al detal, mostrador),
-- Especial queda en el medio. 'Detal' es el default: la venta comun de
-- mostrador, no la mayorista.
INSERT OR IGNORE INTO price_tiers (name, position, is_default) VALUES
    ('Mayor',    1, 0),
    ('Especial', 2, 0),
    ('Detal',    3, 1);

INSERT OR IGNORE INTO document_series (doc_type, prefix, next_number, padding) VALUES
    ('INVOICE',     'FAC-', 1, 6),
    ('CREDIT_NOTE', 'NC-',  1, 6);
