-- ============================================================
-- Reglas que la BASE hace cumplir, no solo el codigo Python
-- ============================================================
-- Hasta el 23/10/2026 las reglas 3 y 4 del CLAUDE.md (movimientos
-- append-only, facturas que no se editan) vivian solo en los servicios.
-- Cualquier herramienta que abriera la base por su cuenta (DB Browser, el
-- CLI sqlite3) podia editar una factura emitida o borrar un movimiento sin
-- que nada lo frenara. Estos triggers abortan esas escrituras venga de
-- donde vengan, con un mensaje que dice por que.
--
-- Viven en un archivo aparte de schema.sql porque nombran columnas que una
-- base vieja recibe DESPUES, en las migraciones de connection.py (bs_rate,
-- ref_rate, payment_method...): connection.init_schema corre este archivo
-- al final, cuando todas existen. Todos son IF NOT EXISTS: idempotente.
--
-- Una migracion futura que necesite corregir datos de estas tablas tiene
-- que hacer DROP TRIGGER, su UPDATE, y volver a llamar a
-- create_integrity_triggers() - a proposito incomodo: es una excepcion.

-- Regla 3: inventory_movements es append-only.
CREATE TRIGGER IF NOT EXISTS trg_movimientos_no_se_editan
BEFORE UPDATE ON inventory_movements
BEGIN
    SELECT RAISE(ABORT, 'inventory_movements es append-only: un error se corrige con otro movimiento.');
END;

CREATE TRIGGER IF NOT EXISTS trg_movimientos_no_se_borran
BEFORE DELETE ON inventory_movements
BEGIN
    SELECT RAISE(ABORT, 'inventory_movements es append-only: un error se corrige con otro movimiento.');
END;

-- Regla 4: una factura emitida no se borra ni se edita; solo se anula
-- (ISSUED -> CANCELLED, con su fecha y motivo). La otra escritura que se
-- admite es la que hace SQLite solo: borrar un cliente deja customer_id en
-- NULL (ON DELETE SET NULL); el nombre y la cedula quedan, son snapshot.
CREATE TRIGGER IF NOT EXISTS trg_facturas_no_se_borran
BEFORE DELETE ON invoices
BEGIN
    SELECT RAISE(ABORT, 'Las facturas no se borran: se anulan o se corrigen con una nota de credito.');
END;

CREATE TRIGGER IF NOT EXISTS trg_facturas_solo_se_anulan
BEFORE UPDATE ON invoices
WHEN NEW.id IS NOT OLD.id
  OR NEW.series_id IS NOT OLD.series_id
  OR NEW.number IS NOT OLD.number
  OR NEW.display_number IS NOT OLD.display_number
  OR (NEW.customer_id IS NOT OLD.customer_id AND NEW.customer_id IS NOT NULL)
  OR NEW.customer_name IS NOT OLD.customer_name
  OR NEW.customer_tax_id IS NOT OLD.customer_tax_id
  OR NEW.customer_address IS NOT OLD.customer_address
  OR NEW.payment_method IS NOT OLD.payment_method
  OR NEW.tax_rate IS NOT OLD.tax_rate
  OR NEW.bs_rate IS NOT OLD.bs_rate
  OR NEW.ref_rate IS NOT OLD.ref_rate
  OR NEW.subtotal_cents IS NOT OLD.subtotal_cents
  OR NEW.tax_cents IS NOT OLD.tax_cents
  OR NEW.total_cents IS NOT OLD.total_cents
  OR NEW.notes IS NOT OLD.notes
  OR NEW.issued_at IS NOT OLD.issued_at
  OR ((NEW.status IS NOT OLD.status
       OR NEW.cancelled_at IS NOT OLD.cancelled_at
       OR NEW.cancel_reason IS NOT OLD.cancel_reason)
      AND NOT (OLD.status = 'ISSUED' AND NEW.status = 'CANCELLED'))
BEGIN
    SELECT RAISE(ABORT, 'Una factura emitida no se edita: solo se anula (ISSUED -> CANCELLED).');
END;

CREATE TRIGGER IF NOT EXISTS trg_lineas_de_factura_no_se_editan
BEFORE UPDATE ON invoice_lines
BEGIN
    SELECT RAISE(ABORT, 'Las lineas de una factura emitida no se editan.');
END;

CREATE TRIGGER IF NOT EXISTS trg_lineas_de_factura_no_se_borran
BEFORE DELETE ON invoice_lines
BEGIN
    SELECT RAISE(ABORT, 'Las lineas de una factura emitida no se borran.');
END;

-- Un producto con historia no se borra: se desactiva (is_active = 0).
-- inventory_movements.product_id tiene ON DELETE CASCADE desde la Fase 1:
-- borrar el producto se llevaria su historial de stock entero. Cambiar el
-- FK a RESTRICT obliga a reconstruir la tabla (SQLite no tiene ALTER
-- CONSTRAINT); este trigger da el mismo resultado sin tocar la tabla. Un
-- producto SIN movimientos ni ventas (cargado por error recien) si se
-- puede borrar.
CREATE TRIGGER IF NOT EXISTS trg_productos_con_historial_no_se_borran
BEFORE DELETE ON products
WHEN EXISTS (SELECT 1 FROM inventory_movements WHERE product_id = OLD.id)
  OR EXISTS (SELECT 1 FROM invoice_lines WHERE product_id = OLD.id)
BEGIN
    SELECT RAISE(ABORT, 'Un producto con movimientos o ventas no se borra: se desactiva.');
END;
