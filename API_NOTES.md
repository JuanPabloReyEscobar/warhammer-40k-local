# API integration

La app consulta la API pública Wahapedia/Warhammer Matchmaker para facciones, unidades, perfiles y reglas: https://warhammer-matchmaker.com/api/wahapedia/docs/index.html.

Las estratagemas no aparecen en el esquema de esa API. Para ellas, la app descarga y cachea el CSV oficial de exportación de Wahapedia: https://wahapedia.ru/wh40k11ed/Stratagems.csv. El archivo usa `|` como delimitador y enlaza `faction_id` y `detachment_id`; la app muestra las estratagemas Core, las de facción sin destacamento y las de los destacamentos guardados en la lista.

Los costos DP de destacamentos se consultan en el Munitorum Field Manual oficial: https://mfm.warhammer-community.com/en. Las respuestas quedan en la caché de SQLite local y se revalidan con ETag cuando el servidor lo permite.

Atribución visible: “Powered by Wahapedia”.
