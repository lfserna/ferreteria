USE restaurante_sistema;

-- Convierte cantidades y factores a unidades enteras.
-- Ejecutar después de respaldar la base si ya existen ventas reales.

UPDATE producto_presentaciones
SET factor_unidad_base = GREATEST(1, ROUND(factor_unidad_base));

UPDATE inventarios
SET cantidad_disponible = GREATEST(0, ROUND(cantidad_disponible)),
    cantidad_reservada = GREATEST(0, ROUND(cantidad_reservada)),
    cantidad_minima = GREATEST(0, ROUND(cantidad_minima)),
    cantidad_maxima = CASE WHEN cantidad_maxima IS NULL THEN NULL ELSE GREATEST(0, ROUND(cantidad_maxima)) END;

UPDATE inventario_movimientos
SET cantidad = GREATEST(1, ROUND(cantidad));

UPDATE orden_venta_detalles
SET cantidad = GREATEST(1, ROUND(cantidad));

UPDATE venta_detalles
SET cantidad = GREATEST(1, ROUND(cantidad));

ALTER TABLE producto_presentaciones
MODIFY factor_unidad_base INT UNSIGNED NOT NULL DEFAULT 1;

ALTER TABLE inventarios
MODIFY cantidad_disponible INT UNSIGNED NOT NULL DEFAULT 0,
MODIFY cantidad_reservada INT UNSIGNED NOT NULL DEFAULT 0,
MODIFY cantidad_minima INT UNSIGNED NOT NULL DEFAULT 0,
MODIFY cantidad_maxima INT UNSIGNED NULL;

ALTER TABLE inventario_movimientos
MODIFY cantidad INT UNSIGNED NOT NULL;

ALTER TABLE orden_venta_detalles
MODIFY cantidad INT UNSIGNED NOT NULL;

ALTER TABLE venta_detalles
MODIFY cantidad INT UNSIGNED NOT NULL;
