USE restaurante_sistema;

-- Restablece la contraseña demo de todos los usuarios del seed a: Admin123!
UPDATE usuarios
SET password_hash = 'pbkdf2:sha256:1000000$ferreteria-demo-2026$4b6f20e2d0c9051973427dbc357be88712008dd91a6149d8b21efe5da32f0696'
WHERE username IN ('admin','cajero.centro','cajero.norte','vendedor.centro','vendedor.norte','almacen.central');
