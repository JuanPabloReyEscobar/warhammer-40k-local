import json
import re
import sqlite3
from html.parser import HTMLParser

import streamlit as st

from src.config import DB_PATH, SOURCE_URL
from src.database import connect, initialize
from src.importer import import_csv, search
from src.munitorum import ManualError, detachment_costs
from src.wahapedia_api import ApiError, catalogue, catalogues
from src.wahapedia_export import ExportError, stratagems

st.set_page_config(page_title="Warhammer 40,000 · 11.ª", page_icon="⚔️", layout="wide")
initialize(DB_PATH)


@st.cache_data(ttl=300, show_spinner=False)
def get_factions():
    return catalogues()


@st.cache_data(ttl=300, show_spinner=False)
def get_faction_catalogue(faction_id):
    return catalogue(faction_id)


@st.cache_data(ttl=900, show_spinner=False)
def get_detachment_costs(faction_name):
    return detachment_costs(faction_name)


@st.cache_data(ttl=900, show_spinner=False)
def get_stratagems():
    return stratagems()


def points_of(entry):
    for cost in entry.get("costs", []):
        if cost.get("name", "").lower() in {"pts", "points"}:
            return int(cost.get("value", 0))
    return 0


def unit_sizes(entry):
    options = (entry.get("wahapedia") or {}).get("pointsOptions", [])
    result = []
    seen = set()
    for option in options:
        models = option.get("models", "")
        points = option.get("points")
        key = (models, points)
        if models and points is not None and key not in seen:
            seen.add(key)
            result.append({"models": models, "points": int(points)})
    return result


def model_count_range(entry):
    details = entry.get("wahapedia") or {}
    ranges = []
    for composition in details.get("unitComposition", []):
        match = re.search(r"(\d+)\s*(?:[-\u2010-\u2015]\s*(\d+))?", str(composition))
        if match:
            low = int(match.group(1))
            high = int(match.group(2) or low)
            ranges.append((low, high))
    if ranges:
        return sum(low for low, _ in ranges), sum(high for _, high in ranges)
    thresholds = []
    for option in unit_sizes(entry):
        match = re.search(r"\d+", option["models"])
        if match:
            thresholds.append(int(match.group()))
    if thresholds:
        return min(thresholds), max(thresholds)
    return 1, 1


def gear_key(value):
    return re.sub(r"^[^\w]*\d+\s*", "", str(value)).strip().casefold()


def add_wargear_selection(selected, counts, group, item, quantity):
    quantity = int(quantity)
    if (group.get("item") or group.get("choices")) and re.search(r"\breplaced\b", group["text"], re.I):
        replaceable_text = re.split(r"\breplaced with\b", group["text"], maxsplit=1, flags=re.I)[0].casefold()
        for previous in selected:
            previous_name = gear_key(previous.get("seleccion", ""))
            if previous_name and previous_name in replaceable_text and previous.get("cantidad", 0) > 0:
                transferred = min(quantity, int(previous["cantidad"]))
                previous["cantidad"] -= transferred
                counts[previous_name] = max(0, counts.get(previous_name, 0) - transferred)
    selected.append({"opcion": group["label"], "seleccion": item, "cantidad": quantity, "regla": group["text"]})
    key = gear_key(item)
    counts[key] = counts.get(key, 0) + quantity


def cost_for_models(entry, model_count):
    tiers = []
    for option in unit_sizes(entry):
        match = re.search(r"\d+", option["models"])
        if match:
            tiers.append((int(match.group()), option["points"]))
    if not tiers:
        return points_of(entry)
    tiers = sorted(set(tiers))
    # Between two published sizes, the larger bracket's cost applies.
    return next((cost for threshold, cost in tiers if threshold >= model_count), tiers[-1][1])


def wargear_groups(entry, model_count):
    groups = []
    for raw in (entry.get("wahapedia") or {}).get("wargearOptions", []):
        if re.search(r"designer.?s note", str(raw), re.I):
            continue
        lines = [line.strip() for line in str(raw).splitlines() if line.strip()]
        choices = []
        label_lines = []
        for line in lines:
            if re.match(r"^[^\w]*\d+\s+", line):
                choices.append(re.sub(r"^[^\w]*", "", line).strip())
            else:
                label_lines.append(line)
        step_match = re.search(r"for every\s+(\d+)\s+models?", str(raw), re.I)
        per_step_match = re.search(r"for every\s+\d+\s+models?.*?\b(\d+)\s+models?\b", str(raw), re.I)
        if step_match:
            max_count = (int(model_count) // int(step_match.group(1))) * int(per_step_match.group(1) if per_step_match else 1)
        elif re.search(r"all models?.*?each", str(raw), re.I):
            max_count = int(model_count)
        else:
            max_count = 1
        item_match = re.search(r"(?:replaced with|equipped with)\s+(?:1|one)\s+(.+?)(?:[.;*]|$)", str(raw), re.I)
        item = item_match.group(1).strip() if item_match else None
        if item and "following" in item.casefold():
            item = None
        groups.append({"label": " ".join(label_lines) or str(raw), "choices": choices, "text": str(raw),
                       "item": item, "max_count": max_count, "excludes": []})
    for group in groups:
        excluded_phrase = re.search(r"not equipped with\s+(.+?)(?:\s+can\b|[.;]|$)", group["text"], re.I)
        if excluded_phrase:
            for candidate in groups:
                if candidate["item"] and candidate["item"].casefold() in excluded_phrase.group(1).casefold():
                    group["excludes"].append(candidate["item"])
    return groups


def all_units(catalogue_data):
    units = []
    seen = set()

    def visit(entry):
        if not isinstance(entry, dict):
            return
        if entry.get("type") == "unit" and entry.get("id") not in seen:
            seen.add(entry.get("id"))
            units.append(entry)
        for child in entry.get("selectionEntries", []):
            visit(child)
        for group in entry.get("selectionEntryGroups", []):
            visit(group)

    for entry in catalogue_data.get("sharedSelectionEntries", []):
        visit(entry)
    for group in catalogue_data.get("sharedSelectionEntryGroups", []):
        visit(group)
    return sorted(units, key=lambda item: item.get("name", "").casefold())


def space_marine_chapters(catalogue_data):
    excluded = {"adeptus astartes", "agents of the imperium"}
    return sorted({
        item.get("name", "").split(":", 1)[1].strip()
        for item in catalogue_data.get("categoryEntries", [])
        if str(item.get("name", "")).startswith("Faction:")
        and item.get("name", "").split(":", 1)[1].strip().casefold() not in excluded
    }, key=str.casefold)


def chapter_tags(unit):
    excluded = {"adeptus astartes", "agents of the imperium"}
    return {
        link.get("name", "").split(":", 1)[1].strip()
        for link in unit.get("categoryLinks", []) or []
        if str(link.get("name", "")).startswith("Faction:")
        and link.get("name", "").split(":", 1)[1].strip().casefold() not in excluded
    }


def detachments_of(catalogue_data):
    result = []
    for group in catalogue_data.get("sharedSelectionEntryGroups", []):
        if "detachment" in group.get("name", "").casefold():
            result.extend(group.get("selectionEntries", []))
    return sorted(result, key=lambda item: item.get("name", "").casefold())


def dp_budget(points_limit):
    # Official Strike Force at 2,000 pts has a 3 DP allowance; Incursion at 1,000 has 2.
    return 2 if int(points_limit) <= 1000 else 3


def render_profiles(profiles, selected_wargear=None):
    groups = {}
    selected_names = [gear_key(item.get("seleccion", ""))
                      for item in (selected_wargear or []) if item.get("seleccion") and item.get("cantidad", 1) > 0]
    for profile in profiles:
        kind = profile.get("typeName", "Perfil")
        row = {"Perfil": profile.get("name", "")}
        if "weapon" in kind.casefold():
            profile_name = profile.get("name", "").casefold()
            row["Equipo"] = "Elegido" if any(name and name in profile_name for name in selected_names) else "Disponible"
        for characteristic in profile.get("characteristics", []) or []:
            if isinstance(characteristic, dict):
                value = characteristic.get("$text", characteristic.get("value", ""))
                row[characteristic.get("name", "Dato")] = value
            elif isinstance(characteristic, str):
                row[characteristic] = ""
        groups.setdefault(kind, []).append(row)
    for kind, rows in groups.items():
        st.markdown(f"**{kind}**")
        st.dataframe(rows, use_container_width=True, hide_index=True)


def render_unit_details(unit, selected_wargear=None):
    if unit.get("profiles"):
        render_profiles(unit["profiles"], selected_wargear)
    if unit.get("rules"):
        st.markdown("**Reglas y habilidades**")
        for rule in unit["rules"]:
            with st.expander(rule.get("name", "Regla")):
                st.write(rule.get("description", ""))
    details = unit.get("wahapedia") or {}
    for title, key in (("Composición", "unitComposition"), ("Opciones de equipo", "wargearOptions"), ("Unidades que puede liderar", "leads")):
        values = details.get(key, [])
        if values:
            st.markdown(f"**{title}**")
            for value in values:
                st.write(f"• {value.get('name')}" if isinstance(value, dict) else f"• {value}")
    if details.get("loadout"):
        st.markdown("**Equipamiento inicial**")
        st.write(details["loadout"])
    if details.get("pointsOptions"):
        st.markdown("**Opciones de tamaño y puntos**")
        st.dataframe([{"Tamaño": item.get("models", ""), "Puntos": item.get("points", "")} for item in details["pointsOptions"]], use_container_width=True, hide_index=True)


def html_to_text(value):
    class TextParser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts = []

        def handle_starttag(self, tag, attrs):
            if tag in {"br", "p", "li", "div"}:
                self.parts.append("\n")

        def handle_endtag(self, tag):
            if tag in {"p", "li", "div"}:
                self.parts.append("\n")

        def handle_data(self, data):
            self.parts.append(data)

    parser = TextParser()
    parser.feed(value or "")
    return "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())


def find_text_records(value, query, found, seen):
    if isinstance(value, dict):
        name = value.get("name", "")
        description = value.get("description", "")
        profiles = value.get("profiles", [])
        characteristic_text = " ".join(
            str(char.get("name", "")) + " " + str(char.get("$text", char.get("value", "")))
            for profile in profiles if isinstance(profile, dict)
            for char in profile.get("characteristics", []) if isinstance(char, dict)
        )
        combined = f"{name} {description} {characteristic_text}"
        identity = value.get("id") or (name, description[:80])
        if query.casefold() in combined.casefold() and (name or description) and identity not in seen:
            seen.add(identity)
            found.append({"name": name or "Regla", "description": description or characteristic_text,
                          "kind": value.get("typeName", value.get("type", ""))})
        for child in value.values():
            find_text_records(child, query, found, seen)
    elif isinstance(value, list):
        for child in value:
            find_text_records(child, query, found, seen)


def list_rows():
    with connect(DB_PATH) as conn:
        return conn.execute("SELECT * FROM army_lists ORDER BY created_at DESC, id DESC").fetchall()


def get_list_units(list_id):
    with connect(DB_PATH) as conn:
        return conn.execute("SELECT * FROM army_list_units WHERE list_id=? ORDER BY id", (list_id,)).fetchall()


st.title("Warhammer 40,000 · 11.ª edición")
st.caption("Creador local de listas y consulta de reglas con datos de Wahapedia.")
st.markdown(f"Fuente: [Wahapedia Data Export 11.ª edición]({SOURCE_URL}) · Powered by Wahapedia")

with connect(DB_PATH) as conn:
    imported_files = conn.execute("SELECT file_name,row_count,imported_at FROM imported_files ORDER BY file_name").fetchall()
    csv_total = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    saved_lists = conn.execute("SELECT COUNT(*) FROM army_lists").fetchone()[0]

cards = st.columns(3)
cards[0].metric("Listas guardadas", saved_lists)
cards[1].metric("CSV importados", len(imported_files))
cards[2].metric("Edición", "11.ª")

with st.sidebar:
    st.header("Fuente de datos")
    st.caption("La API pública proporciona datos actuales de 11.ª. Se guardan en caché local para reutilizarlos.")
    st.link_button("Abrir documentación de la API", "https://warhammer-matchmaker.com/api/wahapedia/docs/index.html")
    st.link_button("Abrir Munitorum Field Manual", "https://mfm.warhammer-community.com/en")
    with st.expander("Importación CSV opcional"):
        uploads = st.file_uploader("Selecciona CSV de Wahapedia", type=["csv"], accept_multiple_files=True)
        if st.button("Importar CSV", disabled=not uploads):
            for upload in uploads:
                count = import_csv(DB_PATH, upload.name, upload.getvalue())
                st.write(f"{upload.name}: {count} filas")
            st.success("Importación terminada")

try:
    factions = get_factions()
    faction_error = None
except ApiError as exc:
    factions, faction_error = [], str(exc)

list_tab, unit_tab, rules_tab, stratagem_tab, csv_tab = st.tabs(["Crear y editar listas", "Unidades y perfiles", "Consultar reglas", "Estratagemas", "CSV importados"])

with list_tab:
    st.subheader("Nueva lista")
    if faction_error:
        st.error(faction_error)
        st.info("La app necesita conectarse una vez a la API para descargar facciones y unidades.")
    elif factions:
        faction_names = {f["name"]: f for f in factions}
        with st.form("new_list_form"):
            left, right = st.columns(2)
            list_name = left.text_input("Nombre de la lista", placeholder="Ej. Custodes 2000")
            faction_name = right.selectbox("Facción", sorted(faction_names, key=str.casefold))
            limit = left.selectbox("Límite de puntos", [1000, 2000], index=1)
            create = st.form_submit_button("Crear lista", type="primary")
        if create:
            if not list_name.strip():
                st.warning("Escribe un nombre para la lista.")
            else:
                faction = faction_names[faction_name]
                with connect(DB_PATH) as conn:
                    conn.execute("INSERT INTO army_lists(name,faction_id,faction_name,points_limit) VALUES (?,?,?,?)",
                                 (list_name.strip(), faction["id"], faction["name"], limit))
                st.success("Lista creada. Ahora selecciónala abajo para añadir unidades.")
                st.rerun()

    rows = list_rows()
    if rows and factions:
        options = {f"{row['name']} · {row['faction_name']} · {row['points_limit']} pts": row for row in rows}
        selected_label = st.selectbox("Tus listas", list(options))
        current = options[selected_label]
        try:
            data = get_faction_catalogue(current["faction_id"])
            units = all_units(data)
            detachments = detachments_of(data)
            active_chapter = ""
            if "space marine" in current["faction_name"].casefold():
                chapters = space_marine_chapters(data)
                chapter_options = ["Auto (según unidades) / sin capítulo"] + chapters
                chapter_key = f"chapter_{current['id']}"
                saved_chapter = current["chapter"] or chapter_options[0]
                if st.session_state.get(chapter_key) not in chapter_options:
                    st.session_state[chapter_key] = saved_chapter
                selected_chapter = st.selectbox("Capítulo de la lista", chapter_options, key=chapter_key,
                                                help="Los personajes y unidades con etiqueta de capítulo deben coincidir. En modo Auto, el primer elemento específico fija el capítulo.")
                selected_chapter_value = "" if selected_chapter.startswith("Auto") else selected_chapter
                if selected_chapter_value != current["chapter"]:
                    if st.button("Guardar capítulo", key=f"save_chapter_{current['id']}"):
                        with connect(DB_PATH) as conn:
                            conn.execute("UPDATE army_lists SET chapter=? WHERE id=?", (selected_chapter_value, current["id"]))
                        st.rerun()
                saved_chapter_tags = set()
                for saved_entry in get_list_units(current["id"]):
                    saved_chapter_tags.update(chapter_tags(json.loads(saved_entry["payload"])))
                active_chapter = selected_chapter_value or (next(iter(saved_chapter_tags)) if len(saved_chapter_tags) == 1 else "")
                if len(saved_chapter_tags) > 1:
                    st.error("La lista ya contiene etiquetas de capítulos incompatibles: " + ", ".join(sorted(saved_chapter_tags)))
                elif active_chapter and saved_chapter_tags and active_chapter not in saved_chapter_tags:
                    st.error(f"El capítulo seleccionado ({active_chapter}) no coincide con las unidades ya incluidas ({', '.join(sorted(saved_chapter_tags))}).")
                elif active_chapter:
                    st.caption(f"Capítulo aplicado: {active_chapter}")
            try:
                official_dp = get_detachment_costs(current["faction_name"])
                dp_error = None
            except ManualError as exc:
                official_dp, dp_error = {}, str(exc)
            if official_dp:
                detachments = [d for d in detachments if "".join(ch for ch in d.get("name", "").casefold() if ch.isalnum()) in official_dp]
            detachment_by_name = {d.get("name", ""): d for d in detachments}
            with connect(DB_PATH) as conn:
                saved_detachments = conn.execute("SELECT * FROM army_list_detachments WHERE list_id=? ORDER BY detachment_name", (current["id"],)).fetchall()
            saved_by_id = {d["detachment_id"]: d for d in saved_detachments}
            detachment_names = list(detachment_by_name)
            detachment_names_by_id = {d.get("id"): d.get("name", "") for d in detachments}
            detachments_key = f"detachments_{current['id']}"
            if detachments_key in st.session_state:
                st.session_state[detachments_key] = [name for name in st.session_state[detachments_key] if name in detachment_names]
            chosen_detachments = st.multiselect(
                "Destacamentos (puedes combinar varios)", detachment_names,
                default=[detachment_names_by_id[d_id] for d_id in saved_by_id if d_id in detachment_names_by_id],
                key=detachments_key)
            dp_budget_value = dp_budget(current["points_limit"])
            if dp_error:
                st.warning(dp_error + " Puedes escribir el DP manualmente; revisa el MFM antes de guardar.")
            if chosen_detachments:
                dp_rows = []
                for name in chosen_detachments:
                    detachment = detachment_by_name[name]
                    detachment_id = detachment.get("id", name)
                    mfm_row = official_dp.get("".join(ch for ch in name.casefold() if ch.isalnum()), {})
                    saved = saved_by_id.get(detachment_id)
                    dp_rows.append({"Destacamento": name,
                                    "DP": mfm_row.get("dp", saved["dp_cost"] if saved else None),
                                    "Fuente": "Munitorum Field Manual" if mfm_row else ("Guardado" if saved else "Completar DP"),
                                    "_id": detachment_id,
                                    "_payload": detachment})
                edited_dp = st.data_editor(
                    [{k: row[k] for k in ("Destacamento", "DP", "Fuente")} for row in dp_rows],
                    key=f"dp_editor_{current['id']}_{hash(tuple(chosen_detachments))}",
                    hide_index=True, use_container_width=True,
                    disabled=["Destacamento", "Fuente"],
                    column_config={"DP": st.column_config.NumberColumn("DP", min_value=1, max_value=3, step=1, format="%d")})
                rows_to_save = edited_dp.to_dict("records") if hasattr(edited_dp, "to_dict") else edited_dp
                missing = [r["Destacamento"] for r in rows_to_save if r.get("DP") is None]
                selected_dp_total = sum(int(r["DP"]) for r in rows_to_save if r.get("DP") is not None)
                if st.button("Guardar destacamentos", key=f"save_dp_{current['id']}", disabled=bool(missing) or selected_dp_total > dp_budget_value):
                    with connect(DB_PATH) as conn:
                        conn.execute("DELETE FROM army_list_detachments WHERE list_id=?", (current["id"],))
                        for record, edited in zip(dp_rows, rows_to_save):
                            conn.execute("INSERT INTO army_list_detachments(list_id,detachment_id,detachment_name,dp_cost,payload) VALUES (?,?,?,?,?)",
                                         (current["id"], record["_id"], record["Destacamento"], int(edited["DP"]), json.dumps(record["_payload"], ensure_ascii=False)))
                    st.rerun()
                if missing:
                    st.error("Falta indicar el costo DP de: " + ", ".join(missing))
                st.metric("Presupuesto de destacamentos", f"{selected_dp_total} / {dp_budget_value} DP")
                if selected_dp_total > dp_budget_value:
                    st.error("La selección supera el presupuesto DP de esta partida.")
                elif selected_dp_total == dp_budget_value:
                    st.success("Presupuesto DP completo.")
                for name in chosen_detachments:
                    detachment = detachment_by_name[name]
                    with st.expander(f"Regla del destacamento: {name}"):
                        for rule in detachment.get("rules", []):
                            st.markdown(f"**{rule.get('name', 'Regla')}**")
                            st.write(rule.get("description", ""))
            else:
                if saved_detachments:
                    st.warning("Los destacamentos guardados no están seleccionados. Si quieres quitarlos, usa el botón explícito.")
                    if st.button("Quitar todos los destacamentos guardados", key=f"clear_dp_{current['id']}"):
                        with connect(DB_PATH) as conn:
                            conn.execute("DELETE FROM army_list_detachments WHERE list_id=?", (current["id"],))
                        st.rerun()
                else:
                    st.info(f"Elige uno o más destacamentos. Presupuesto: {dp_budget_value} DP.")

            st.markdown("#### Añadir unidad")
            if units:
                unit_options = {f"{u.get('name','Unidad')} · {points_of(u)} pts": u for u in units}
                chosen_unit_label = st.selectbox("Unidad", list(unit_options), key=f"unit_{current['id']}")
                chosen_unit = unit_options[chosen_unit_label]
                candidate_chapters = chapter_tags(chosen_unit)
                chapter_conflict = bool(candidate_chapters and active_chapter and active_chapter not in candidate_chapters)
                if chapter_conflict:
                    st.error(f"{chosen_unit.get('name')} pertenece a {', '.join(sorted(candidate_chapters))}; no es compatible con {active_chapter}.")
                elif candidate_chapters and not active_chapter:
                    st.info(f"Al añadir esta unidad, la lista quedará asociada a {', '.join(sorted(candidate_chapters))}.")
                min_models, max_models = model_count_range(chosen_unit)
                model_count = st.number_input("Miniaturas en cada unidad", min_value=min_models, max_value=max_models,
                                              value=min_models, step=1,
                                              key=f"models_{current['id']}_{chosen_unit['id']}")
                size_label = f"{model_count} modelo" if model_count == 1 else f"{model_count} modelos"
                unit_points = cost_for_models(chosen_unit, model_count)
                st.caption(f"Costo por unidad de {size_label}: {unit_points} pts")
                selected_wargear = []
                equipment_options = wargear_groups(chosen_unit, model_count)
                selected_equipment_counts = {}
                if equipment_options:
                    with st.expander("Configurar opciones de wargear"):
                        for index, group in enumerate(equipment_options):
                            excluded_count = sum(selected_equipment_counts.get(gear_key(name), 0) for name in group["excludes"])
                            available_count = max(0, min(group["max_count"], int(model_count) - excluded_count))
                            widget_suffix = f"{current['id']}_{chosen_unit['id']}_{model_count}_{index}"
                            if available_count == 0:
                                st.caption(f"No aplica con {size_label}: {group['text']}")
                                continue
                            if group["choices"]:
                                label = group["label"]
                                choice = st.selectbox(label, ["Sin cambio"] + group["choices"], key=f"wargear_{widget_suffix}")
                                if choice != "Sin cambio":
                                    quantity = 1
                                    if available_count > 1:
                                        quantity = st.number_input("Cantidad de modelos con esta opción", min_value=1, max_value=available_count,
                                                                   value=1, step=1, key=f"wargear_qty_{widget_suffix}")
                                    add_wargear_selection(selected_wargear, selected_equipment_counts, group, choice, quantity)
                            elif group["item"]:
                                gear_count = st.number_input(f"Modelos con {group['item']} (máximo {available_count})", min_value=0,
                                                             max_value=available_count, value=0, step=1,
                                                             key=f"wargear_count_{widget_suffix}")
                                st.caption(group["text"])
                                if gear_count:
                                    add_wargear_selection(selected_wargear, selected_equipment_counts, group, group["item"], gear_count)
                            else:
                                enabled = st.checkbox(group["text"], key=f"wargear_{widget_suffix}")
                                if enabled:
                                    add_wargear_selection(selected_wargear, selected_equipment_counts, group, "Aplicar", 1)
                    selected_wargear = [item for item in selected_wargear if item.get("cantidad", 0) > 0]
                    st.caption("Las elecciones de wargear se guardan con la unidad. Los costos adicionales de equipo no se suman todavía al total de puntos.")
                with st.expander(f"Ver ficha: {chosen_unit.get('name', 'Unidad')}"):
                    render_unit_details(chosen_unit, selected_wargear)
                qty_col, add_col = st.columns([1, 3])
                quantity = qty_col.number_input("Cantidad", min_value=1, max_value=6, value=1, key=f"qty_{current['id']}")
                if add_col.button("Añadir a la lista", type="primary", key=f"add_{current['id']}", disabled=chapter_conflict):
                    with connect(DB_PATH) as conn:
                        if active_chapter and not current["chapter"]:
                            conn.execute("UPDATE army_lists SET chapter=? WHERE id=?", (active_chapter, current["id"]))
                        conn.execute("INSERT INTO army_list_units(list_id,unit_id,unit_name,points,quantity,size_label,wargear_json,payload) VALUES (?,?,?,?,?,?,?,?)",
                                     (current["id"], chosen_unit["id"], chosen_unit["name"], unit_points, quantity, size_label,
                                      json.dumps(selected_wargear, ensure_ascii=False), json.dumps(chosen_unit, ensure_ascii=False)))
                    st.rerun()

            entries = get_list_units(current["id"])
            total_points = sum(e["points"] * e["quantity"] for e in entries)
            st.markdown("#### Contenido")
            progress = min(total_points / max(current["points_limit"], 1), 1.0)
            st.progress(progress, text=f"{total_points} / {current['points_limit']} puntos")
            if entries:
                for entry in entries:
                    c1, c2, c3, c4 = st.columns([5, 1, 2, 1])
                    c1.write(entry["unit_name"])
                    c2.write(f"×{entry['quantity']}")
                    c3.write(f"{entry['points'] * entry['quantity']} pts")
                    if c4.button("Quitar", key=f"remove_{entry['id']}"):
                        with connect(DB_PATH) as conn:
                            conn.execute("DELETE FROM army_list_units WHERE id=?", (entry["id"],))
                        st.rerun()
                    with st.expander(f"Ver perfil y reglas: {entry['unit_name']} ({entry['size_label'] or 'tamaño publicado'})"):
                        st.caption(f"Costo por unidad: {entry['points']} pts · Tamaño: {entry['size_label'] or 'según ficha'}")
                        selected_gear = json.loads(entry["wargear_json"] or "[]")
                        render_unit_details(json.loads(entry["payload"]), selected_gear)
                        if selected_gear:
                            st.markdown("**Wargear seleccionado**")
                            for gear in selected_gear:
                                st.write(f"{gear.get('cantidad', 1)} × {gear['seleccion']} — {gear['opcion']}")
            else:
                st.info("Aún no hay unidades en esta lista.")
            with connect(DB_PATH) as conn:
                final_detachments = conn.execute("SELECT detachment_name,dp_cost FROM army_list_detachments WHERE list_id=? ORDER BY detachment_name", (current["id"],)).fetchall()
            export = {"name": current["name"], "edition": "11th", "faction": current["faction_name"],
                      "detachments": [{"name": d["detachment_name"], "dp": d["dp_cost"]} for d in final_detachments],
                      "points_limit": current["points_limit"], "points_total": total_points,
                      "detachment_points_total": sum(d["dp_cost"] for d in final_detachments),
                      "units": [{"name": e["unit_name"], "size": e["size_label"], "quantity": e["quantity"],
                                 "points_each": e["points"], "wargear": json.loads(e["wargear_json"] or "[]")} for e in entries]}
            st.download_button("Exportar lista JSON", json.dumps(export, ensure_ascii=False, indent=2),
                               file_name=f"{current['name'].replace(' ', '_')}.json", mime="application/json")
            if total_points > current["points_limit"]:
                st.warning("La lista supera el límite de puntos seleccionado.")
            st.caption("Los puntos mostrados son los valores publicados por la API. Esta primera versión calcula el total y conserva la selección; las restricciones avanzadas del reglamento se añadirán en otra etapa.")
        except ApiError as exc:
            st.error(str(exc))
    elif not faction_error:
        st.info("Crea una lista arriba para comenzar.")

with unit_tab:
    st.subheader("Fichas, perfiles y habilidades")
    if factions:
        faction_names = {f["name"]: f for f in factions}
        unit_faction = st.selectbox("Facción", sorted(faction_names, key=str.casefold), key="unit_faction")
        try:
            unit_data = get_faction_catalogue(faction_names[unit_faction]["id"])
            faction_units = all_units(unit_data)
            unit_by_name = {u.get("name", "Unidad"): u for u in faction_units}
            unit_name = st.selectbox("Unidad", list(unit_by_name), key="unit_browser_name")
            selected_profile_unit = unit_by_name[unit_name]
            st.subheader(f"{unit_name} · {points_of(selected_profile_unit)} pts")
            if selected_profile_unit.get("wahapedia", {}).get("link"):
                st.markdown(f"[Abrir ficha en Wahapedia]({selected_profile_unit['wahapedia']['link']})")
            render_unit_details(selected_profile_unit)
        except ApiError as exc:
            st.error(str(exc))
    elif faction_error:
        st.error(faction_error)

with rules_tab:
    st.subheader("Buscar en reglas y perfiles")
    if factions:
        faction_names = {f["name"]: f for f in factions}
        rules_faction = st.selectbox("Buscar dentro de la facción", sorted(faction_names, key=str.casefold), key="rules_faction")
        rules_query = st.text_input("Texto, unidad o habilidad", placeholder="Ej. Deep Strike")
        if rules_query.strip():
            try:
                data = get_faction_catalogue(faction_names[rules_faction]["id"])
                found, seen = [], set()
                find_text_records(data, rules_query.strip(), found, seen)
                st.caption(f"{len(found)} coincidencias")
                for item in found[:100]:
                    with st.expander(f"{item['name']} {('· ' + item['kind']) if item['kind'] else ''}"):
                        st.write(item["description"])
                if not found:
                    st.info("No encontré coincidencias dentro de este catálogo.")
            except ApiError as exc:
                st.error(str(exc))
    elif faction_error:
        st.error(faction_error)
    legacy_query = st.text_input("Buscar en CSV que hayas importado (opcional)")
    if legacy_query:
        results = search(DB_PATH, legacy_query)
        for result in results:
            record = json.loads(result["payload"])
            with st.expander(result["file_name"]):
                st.json(record)

with stratagem_tab:
    st.subheader("Estratagemas de esta lista")
    current_lists = list_rows()
    if not current_lists:
        st.info("Crea una lista primero para filtrar sus estratagemas por facción y destacamentos.")
    else:
        list_choices = {f"{row['name']} · {row['faction_name']} · {row['points_limit']} pts": row for row in current_lists}
        strat_list = st.selectbox("Lista", list(list_choices), key="stratagem_list")
        current = list_choices[strat_list]
        try:
            all_stratagems = get_stratagems()
            with connect(DB_PATH) as conn:
                selected_detachments = conn.execute("SELECT detachment_id,detachment_name FROM army_list_detachments WHERE list_id=? ORDER BY detachment_name", (current["id"],)).fetchall()
            selected_ids = {row["detachment_id"] for row in selected_detachments}
            core_stratagems = [row for row in all_stratagems
                               if not row.get("faction_id") and not row.get("detachment_id")
                               and row.get("type", "").strip() == "Core Stratagem"]
            faction_stratagems = [row for row in all_stratagems
                                  if row.get("faction_id") == current["faction_id"] and not row.get("detachment_id")]
            detachment_stratagems = [row for row in all_stratagems
                                     if row.get("faction_id") == current["faction_id"] and row.get("detachment_id") in selected_ids]
            combined = []
            seen_stratagems = set()
            for category, group in (("Universal", core_stratagems), ("Facción", faction_stratagems), ("Destacamento", detachment_stratagems)):
                for row in group:
                    if row.get("id") not in seen_stratagems:
                        seen_stratagems.add(row.get("id"))
                        combined.append((category, row))
            if not selected_detachments:
                st.info("Todavía no hay destacamentos guardados en esta lista. Guarda uno o varios desde la pestaña de creación.")
            if not combined:
                st.warning("El CSV no devolvió estratagemas para esta facción/lista.")
            else:
                phases = sorted({row.get("phase", "") for _, row in combined if row.get("phase")})
                phase_filter = st.selectbox("Fase", ["Todas"] + phases, key="stratagem_phase")
                filtered = [(category, row) for category, row in combined
                            if phase_filter == "Todas" or row.get("phase") == phase_filter]
                counts = {category: sum(1 for c, _ in filtered if c == category) for category in ("Universal", "Facción", "Destacamento")}
                st.caption(f"{len(filtered)} estratagemas: {counts['Universal']} universales, {counts['Facción']} de facción y {counts['Destacamento']} de tus destacamentos.")
                for category, row in filtered:
                    title = f"{row.get('name', 'Estratagema')} · {row.get('cp_cost', '?')} CP · {row.get('phase', 'Fase no indicada')}"
                    with st.expander(title):
                        st.caption(f"{category} · {row.get('type', '')} · {row.get('turn', '')}")
                        if row.get("detachment"):
                            st.caption(f"Destacamento: {row['detachment']}")
                        if row.get("legend"):
                            st.caption(html_to_text(row["legend"]))
                        st.write(html_to_text(row.get("description", "")))
            st.caption("Reglas de estratagemas: Powered by Wahapedia.")
        except ExportError as exc:
            st.error(str(exc))
            st.info("La primera descarga requiere internet; después se usa la copia local en caché.")

with csv_tab:
    if imported_files:
        st.dataframe([dict(row) for row in imported_files], use_container_width=True, hide_index=True)
        st.caption(f"{csv_total} registros CSV locales")
    else:
        st.info("No hacen falta CSV para usar la API. Puedes importar archivos CSV opcionalmente.")
