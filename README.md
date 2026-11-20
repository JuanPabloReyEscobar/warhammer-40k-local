# Warhammer 40,000 11.ª · aplicación local

Aplicación Python para consultar fichas, perfiles, armas, reglas y estratagemas, y crear listas locales de 11.ª edición.

## Arranque en Windows

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
streamlit run app.py
```

Abre `http://localhost:8501`. La primera consulta necesita internet. Los catálogos, estratagemas y Munitorum Field Manual se guardan en SQLite local para reutilizarlos.

## Uso

- **Crear y editar listas**: crea listas de 1.000 o 2.000 puntos, combina destacamentos, revisa costos DP, elige cantidades de miniaturas y asigna wargear con topes por tamaño (por ejemplo, una mejora por cada 5 modelos). Las reglas que dicen “reemplazar” ajustan la cantidad del equipo reemplazado. Puedes exportar la lista a JSON.
- **Unidades y perfiles**: consulta perfiles de modelos y armas, reglas, composición y opciones.
- **Consultar reglas**: busca dentro del catálogo de una facción.
- **Estratagemas**: selecciona una lista para ver las universales, de facción y las de sus destacamentos; puedes filtrar por fase.

Los puntos de unidad usan los tramos publicados. Las elecciones de wargear se guardan y se destacan en los perfiles; los costos adicionales de wargear aún no se suman al total. Los límites avanzados por unidad y líder también están pendientes.

Fuentes: [API Wahapedia/Warhammer Matchmaker](https://warhammer-matchmaker.com/api/wahapedia/docs/index.html), [exportación de datos Wahapedia](https://wahapedia.ru/wh40k11ed/the-rules/data-export/) y [Munitorum Field Manual](https://mfm.warhammer-community.com/en). Base local: `data/warhammer40k.sqlite3`.
