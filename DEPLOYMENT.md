# Prueba gratuita en Streamlit Community Cloud

## Antes de publicar

- No subir `data/warhammer40k.sqlite3`: contiene la caché local de datos y listas.
- Mantener la aplicación como **privada** en Streamlit Community Cloud. La app guarda las listas en una SQLite compartida en el servidor; cualquier persona que entre a la misma app vería las listas de esa base.
- La plataforma gratuita no garantiza que los archivos locales persistan. Las listas y cachés pueden perderse si se reinicia o reconstruye la app. Exporta las listas a JSON como respaldo.
- La aplicación consulta Warhammer Matchmaker y Munitorum en línea; no será completamente sin conexión en la nube.

## Publicación

1. Crea un repositorio **privado** en GitHub y sube el código de esta carpeta. El `.gitignore` excluye la SQLite, credenciales, entornos virtuales y cachés de Python.
2. Entra a <https://share.streamlit.io/> y conecta GitHub.
3. Selecciona el repositorio, rama y `app.py`, y despliega.
4. En **App settings → Sharing**, conserva **Only specific people can view this app**. Invita únicamente tu correo.
5. Abre la URL `*.streamlit.app` que genere Streamlit.

Para volver a desplegar una actualización, sube los cambios al mismo repositorio; Streamlit reconstruye la app desde esos archivos.
