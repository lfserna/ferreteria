USE restaurante_sistema;

ALTER TABLE clientes
ADD COLUMN max_usuarios INT NOT NULL DEFAULT 10 AFTER estado;

UPDATE clientes
SET max_usuarios = 10
WHERE max_usuarios IS NULL OR max_usuarios <= 0;
