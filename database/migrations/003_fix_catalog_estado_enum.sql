USE restaurante_sistema;

-- Normaliza los estados de catálogo para evitar errores 1265 Data truncated.
-- Ejecutar si la base fue creada con una versión anterior del esquema.

UPDATE categorias_producto
SET estado = CASE
    WHEN UPPER(estado) IN ('ACTIVO','ACTIVA','1') THEN 'ACTIVO'
    ELSE 'INACTIVO'
END;

UPDATE marcas
SET estado = CASE
    WHEN UPPER(estado) IN ('ACTIVO','ACTIVA','1') THEN 'ACTIVO'
    ELSE 'INACTIVO'
END;

ALTER TABLE categorias_producto
MODIFY estado ENUM('ACTIVO','INACTIVO') NOT NULL DEFAULT 'ACTIVO';

ALTER TABLE marcas
MODIFY estado ENUM('ACTIVO','INACTIVO') NOT NULL DEFAULT 'ACTIVO';
