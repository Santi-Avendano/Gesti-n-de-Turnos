# Documento de Especificación de Requerimientos (REQUIREMENTS.md)

# Documento de Especificación de Requerimientos (REQUIREMENTS.md)

## 1. Visión General del Proyecto
El proyecto consiste en el desarrollo de una aplicación web para la gestión y asignación de turnos (Turnero). El sistema operará bajo un modelo de **grilla de horarios predefinida**, donde los administradores configuran la disponibilidad y los usuarios finales reservan bloques de tiempo específicos. El sistema contará con control de acceso basado en roles.

## 2. Arquitectura Tecnológica Base
* **Backend:** Python con FastAPI.
* **Frontend:** React (TypeScript).
* **Base de Datos:** Relacional (PostgreSQL / MySQL).
* **Autenticación:** JWT.

## 3. Roles y Permisos
* **Administrador (Admin):** Define días laborales, horarios de atención, excepciones (feriados) y duraciones de los turnos. Tiene control total sobre el calendario y las reservas.
* **Usuario Común:** Visualiza únicamente los horarios disponibles generados por el sistema, solicita reservas y gestiona sus propios turnos.

## 4. Requerimientos Funcionales (RF)

### Épica 1: Gestión de Identidad y Acceso (IAM)
* **RF-1.1:** Registro de nuevos usuarios comunes.
* **RF-1.2:** Inicio de sesión (Login) con email y contraseña, retornando un token JWT.
* **RF-1.3:** Restricción de rutas privadas y endpoints según el rol (Admin vs. User).

### Épica 2: Configuración de Disponibilidad (Exclusivo Administrador)
* **RF-2.1 (Grilla Base):** El Administrador debe poder definir una grilla semanal de atención (ej. Lunes a Viernes de 09:00 a 18:00).
* **RF-2.2 (Duración de Slots):** El Administrador debe poder establecer la duración estándar de cada turno dentro de la grilla (ej. bloques de 30 minutos).
* **RF-2.3 (Excepciones y Feriados):** El Administrador debe poder bloquear días completos o rangos horarios específicos para que el sistema no genere disponibilidad en esos periodos.
* **RF-2.4 (Generación Automática):** El backend debe ser capaz de calcular y exponer los bloques de tiempo disponibles cruzando la "Grilla Base" con los "Turnos ya reservados" y las "Excepciones".

### Épica 3: Visualización del Calendario
* **RF-3.1:** El frontend renderizará un calendario interactivo.
* **RF-3.2:** Para el Usuario Común, el calendario solo debe mostrar los días que tienen al menos un turno disponible. Al seleccionar un día, se desplegarán los horarios exactos libres.
* **RF-3.3:** Para el Administrador, el calendario mostrará la vista global (turnos ocupados, usuarios asignados y bloques libres).

### Épica 4: Gestión y Reserva de Turnos
* **RF-4.1 (Reserva):** Un Usuario Común autenticado debe poder seleccionar un horario disponible y confirmar la reserva. El sistema cambiará el estado de ese bloque a "Ocupado".
* **RF-4.2 (Validación de Concurrencia):** El backend debe implementar bloqueos a nivel de base de datos (ej. transacciones o *mutex*) para evitar que dos usuarios reserven el mismo turno en el mismo milisegundo.
* **RF-4.3 (Cancelación Usuario):** Un Usuario Común puede cancelar su turno. El bloque de tiempo volverá a estar disponible en la grilla automáticamente.
* **RF-4.4 (Gestión Admin):** El Administrador puede cancelar o reasignar cualquier turno. Si el Admin cancela un turno, el sistema debe permitir notificar al usuario (lógica a definir en futuras iteraciones).

## 5. Requerimientos No Funcionales (RNF)

* **RNF-1 (UI/UX):** Interfaz minimalista y monocromática. Fondos gris claro, tipografías y componentes en tonos sólidos oscuros/grises. Cero uso de degradados.
* **RNF-2 (Gestión de Tiempo):** El backend almacena fechas/horas en formato UTC. El frontend procesa y renderiza convirtiendo a la zona horaria local del usuario (ej. GMT-3).
* **RNF-3 (Rendimiento):** Tiempo de respuesta de endpoints de disponibilidad < 500ms.
* **RNF-4 (Escalabilidad de Código):** Separación estricta en el frontend entre lógica de estado y componentes de presentación.

## 6. Estructura de Proyecto Propuesta (High-Level)
```text
/turnero-project
├── /backend                 # Entorno FastAPI
│   ├── /app
│   │   ├── /api             # Routers y endpoints (auth, turnos, usuarios)
│   │   ├── /core            # Configuraciones, seguridad (JWT)
│   │   ├── /models          # Modelos ORM (Base de datos)
│   │   ├── /schemas         # Pydantic models (Validación de datos)
│   │   └── /services        # Lógica de negocio
│   └── requirements.txt
└── /frontend                # Entorno React + Vite/CRA
    ├── /src
    │   ├── /components      # Componentes UI reutilizables
    │   ├── /context         # Contextos globales (AuthContext, CalendarContext)
    │   ├── /hooks           # Custom hooks
    │   ├── /pages           # Vistas principales (Dashboard, Login)
    │   └── /services        # Clientes Axios/Fetch para consumir la API
    └── package.json