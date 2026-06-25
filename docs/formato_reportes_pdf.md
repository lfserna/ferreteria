# Formato estándar para reportes PDF

Este documento define el formato que deben seguir los reportes PDF del sistema de ferretería. Debe usarse como referencia para cualquier reporte nuevo que se implemente.

## Regla principal

Los reportes PDF deben seguir el formato visual del reporte de inventario actual:

- Hoja vertical A4.
- Margen general de la hoja.
- Borde/margen delgado alrededor del contenido.
- Encabezado superior dividido en tres zonas.
- Cuerpo central con una tabla clara de registros.
- Totales al final.
- Descarga directa en PDF.
- No mostrar el registro del reporte dentro de la interfaz del sistema.

## Encabezado

Arriba a la izquierda:

- Nombre del cliente o negocio.
- Sucursal, almacén o ubicación del reporte.
- Dirección correspondiente.
- Celular o teléfono correspondiente.

Centro:

- Título del reporte, por ejemplo: `REPORTE DE INVENTARIO`.
- Tipo de reporte o movimiento, por ejemplo: `ENTRADA`, `SALIDA`, `AMBOS`.
- Texto de moneda si corresponde: `( Expresado en Bs. )`.

Arriba a la derecha:

- Fecha y hora de generación del PDF.
- Número de reporte.
- Fecha o rango de fechas filtrado.

## Número de reporte

El número de reporte nunca debe inventarse ni generarse solo para mostrar.

Debe salir de una tabla real de base de datos o de una entidad ya almacenada.

Para reportes nuevos, si no existe una tabla de reportes para ese módulo, se debe crear una tabla interna de registro del reporte generado. El número visible debe salir del ID/correlativo real de esa tabla.

Ejemplo aceptado:

```text
REP-000001
REP-000002
REP-000003
```

Ejemplo no aceptado como fuente única:

```text
REP-20260624-191020
```

La fecha/hora puede usarse como dato informativo, pero no como fuente principal del número correlativo.

## Datos del generador

Debe mostrarse la persona que generó el reporte:

- Nombres.
- Apellido paterno.
- Apellido materno, si existe.

Si no hay nombre completo disponible, usar el `username`.

## Cuerpo del reporte

El cuerpo debe tener una tabla sencilla, legible y orientada a operación. Evitar tablas recargadas o diseños muy decorativos.

Para el reporte de inventario, el formato base usa estas columnas:

- Fecha/hora.
- Producto.
- Categoría.
- Almacén/ubicación.
- Cantidad.
- Precio.
- Total Bs.
- Stock.

En otros reportes, las columnas pueden cambiar según el módulo, pero deben mantener la misma estructura visual: datos claros, importes alineados a la derecha y resumen al final.

## Totales

Al final del reporte debe aparecer una sección de totales. Para reportes de inventario se usa:

- Total cantidad.
- Total Bs.

También puede incluir resumen adicional, por ejemplo:

- Stock total.
- Entradas.
- Salidas.

## Filtros aplicados

El PDF debe mostrar los filtros relevantes del reporte, por ejemplo:

- Periodo.
- Categorías.
- Ubicaciones.
- Tipo de movimiento.
- Rango de fechas.

## Comportamiento esperado

- El usuario genera el PDF desde un modal con filtros.
- El PDF se descarga directamente.
- No se debe abrir una pantalla intermedia con registros del reporte.
- El reporte puede crear un registro interno en base de datos para correlativo/trazabilidad, pero no debe listar esos registros en la interfaz salvo que se pida explícitamente.

## Archivos de referencia actuales

Reporte actual usado como referencia:

```text
app/routes/inventory_report_routes.py
app/templates/inventory/report_pdf.html
```

Cualquier reporte nuevo debe replicar este estilo visual y estas reglas de trazabilidad.
