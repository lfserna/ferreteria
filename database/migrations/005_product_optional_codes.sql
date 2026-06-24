USE restaurante_sistema;

ALTER TABLE productos
MODIFY codigo_producto VARCHAR(80) NULL,
MODIFY codigo_barras VARCHAR(120) NULL;

CREATE TABLE IF NOT EXISTS producto_codigos (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    cliente_id BIGINT UNSIGNED NOT NULL,
    producto_id BIGINT UNSIGNED NOT NULL,
    ubicacion_stock_id BIGINT UNSIGNED NULL,
    codigo VARCHAR(120) NOT NULL,
    tipo_codigo ENUM('INTERNO','BARRAS','SERIE') NOT NULL DEFAULT 'INTERNO',
    estado ENUM('DISPONIBLE','VENDIDO','INACTIVO') NOT NULL DEFAULT 'DISPONIBLE',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NULL,
    UNIQUE KEY uq_producto_codigo_individual_cliente (cliente_id, codigo),
    FOREIGN KEY (cliente_id) REFERENCES clientes(id),
    FOREIGN KEY (producto_id) REFERENCES productos(id),
    FOREIGN KEY (ubicacion_stock_id) REFERENCES ubicaciones_stock(id)
) ENGINE=InnoDB;
