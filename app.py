# -*- coding: utf-8 -*-
"""
Gestion de stock - pièces découpées pour meubles de van
Application Streamlit + Supabase (PostgreSQL) + PDF (ReportLab).
"""

from __future__ import annotations

import io
from datetime import datetime

import pandas as pd
import streamlit as st
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

st.set_page_config(page_title="Stock meubles van", page_icon="🪚", layout="wide")

# ----------------------------- Connexion Supabase -----------------------------
# La connexion utilise le fichier .streamlit/secrets.toml
conn = st.connection("supabase", type="sql")


def init_db():
    """Crée les tables si elles n'existent pas."""
    with conn.session as s:
        s.execute("""
            CREATE TABLE IF NOT EXISTS pieces (
                ref TEXT PRIMARY KEY,
                nom TEXT NOT NULL,
                materiau TEXT DEFAULT '',
                epaisseur_mm REAL,
                longueur_mm REAL,
                largeur_mm REAL,
                stock INTEGER NOT NULL DEFAULT 0,
                notes TEXT DEFAULT '',
                updated_at TEXT NOT NULL
            )
        """)
        s.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id SERIAL PRIMARY KEY,
                order_code TEXT UNIQUE NOT NULL,
                client TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Brouillon',
                notes TEXT DEFAULT ''
            )
        """)
        s.execute("""
            CREATE TABLE IF NOT EXISTS order_lines (
                id SERIAL PRIMARY KEY,
                order_id INTEGER NOT NULL,
                ref TEXT NOT NULL,
                nom_snapshot TEXT NOT NULL,
                quantite INTEGER NOT NULL
            )
        """)
        s.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        s.execute("INSERT INTO settings(key,value) VALUES('seuil_commun','5') ON CONFLICT (key) DO NOTHING")
        s.execute("INSERT INTO settings(key,value) VALUES('nom_atelier','Atelier meubles van') ON CONFLICT (key) DO NOTHING")
        s.commit()


# Initialiser la base une seule fois par session
if "db_initialized" not in st.session_state:
    init_db()
    st.session_state.db_initialized = True


def get_setting(key, default=""):
    result = conn.query("SELECT value FROM settings WHERE key = :key", params={"key": key}, ttl=0)
    return result.iloc[0]["value"] if not result.empty else default


def set_setting(key, value):
    with conn.session as s:
        s.execute(
            "INSERT INTO settings(key, value) VALUES(:key, :value) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            params={"key": key, "value": str(value)}
        )
        s.commit()


def read_pieces():
    return conn.query("SELECT * FROM pieces ORDER BY ref", ttl=0)


def read_orders():
    return conn.query("SELECT * FROM orders ORDER BY id DESC", ttl=0)


def get_order(order_id):
    order_df = conn.query("SELECT * FROM orders WHERE id = :id", params={"id": order_id}, ttl=0)
    if order_df.empty:
        return None, []
    order = order_df.iloc[0].to_dict()
    lines_df = conn.query(
        "SELECT ref, nom_snapshot AS nom, quantite FROM order_lines WHERE order_id = :id ORDER BY ref",
        params={"id": order_id}, ttl=0
    )
    return order, lines_df.to_dict("records")


def nullable_num(v):
    if pd.isna(v) or v == "":
        return None
    return float(v)


def upsert_piece(row, allow_stock_update=True):
    now = datetime.now().isoformat(timespec="seconds")
    with conn.session as s:
        existing = conn.query("SELECT ref FROM pieces WHERE ref = :ref", params={"ref": str(row["ref"])}, ttl=0)
        if not existing.empty:
            if allow_stock_update:
                s.execute("""
                    UPDATE pieces SET nom=:nom, materiau=:materiau, epaisseur_mm=:epaisseur_mm,
                    longueur_mm=:longueur_mm, largeur_mm=:largeur_mm, stock=:stock,
                    notes=:notes, updated_at=:updated_at WHERE ref=:ref
                """, params={
                    "nom": str(row["nom"]), "materiau": str(row.get("materiau", "") or ""),
                    "epaisseur_mm": nullable_num(row.get("epaisseur_mm")),
                    "longueur_mm": nullable_num(row.get("longueur_mm")),
                    "largeur_mm": nullable_num(row.get("largeur_mm")),
                    "stock": int(row.get("stock", 0)),
                    "notes": str(row.get("notes", "") or ""), "updated_at": now, "ref": str(row["ref"])
                })
            else:
                s.execute("""
                    UPDATE pieces SET nom=:nom, materiau=:materiau, epaisseur_mm=:epaisseur_mm,
                    longueur_mm=:longueur_mm, largeur_mm=:largeur_mm, notes=:notes,
                    updated_at=:updated_at WHERE ref=:ref
                """, params={
                    "nom": str(row["nom"]), "materiau": str(row.get("materiau", "") or ""),
                    "epaisseur_mm": nullable_num(row.get("epaisseur_mm")),
                    "longueur_mm": nullable_num(row.get("longueur_mm")),
                    "largeur_mm": nullable_num(row.get("largeur_mm")),
                    "notes": str(row.get("notes", "") or ""), "updated_at": now, "ref": str(row["ref"])
                })
        else:
            s.execute("""
                INSERT INTO pieces(ref, nom, materiau, epaisseur_mm, longueur_mm, largeur_mm, stock, notes, updated_at)
                VALUES(:ref, :nom, :materiau, :epaisseur_mm, :longueur_mm, :largeur_mm, :stock, :notes, :updated_at)
            """, params={
                "ref": str(row["ref"]), "nom": str(row["nom"]), "materiau": str(row.get("materiau", "") or ""),
                "epaisseur_mm": nullable_num(row.get("epaisseur_mm")),
                "longueur_mm": nullable_num(row.get("longueur_mm")),
                "largeur_mm": nullable_num(row.get("largeur_mm")),
                "stock": int(row.get("stock", 0)),
                "notes": str(row.get("notes", "") or ""), "updated_at": now
            })
        s.commit()


# --- Fonctions PDF (identiques à votre code original) ---
def make_pdf(order, lines, atelier, threshold):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=15*mm, leftMargin=15*mm, topMargin=14*mm, bottomMargin=14*mm)
    styles = getSampleStyleSheet()
    story = []
    story.append(Paragraph(f"<b>FICHE ATELIER — {order['order_code']}</b>", styles["Title"]))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(f"<b>Atelier :</b> {atelier}", styles["Normal"]))
    story.append(Paragraph(f"<b>Client :</b> {order['client'] or 'Non renseigné'}", styles["Normal"]))
    story.append(Paragraph(f"<b>Date :</b> {order['created_at']}", styles["Normal"]))
    story.append(Paragraph(f"<b>Statut :</b> {order['status']}", styles["Normal"]))
    if order["notes"]:
        story.append(Paragraph(f"<b>Notes :</b> {order['notes']}", styles["Normal"]))
    story.append(Spacer(1, 6*mm))
    story.append(Paragraph("<b>Pièces à préparer pour la commande</b>", styles["Heading2"]))
    data = [["Préparé", "Référence", "Désignation", "Quantité"]]
    for line in lines:
        data.append(["[ ]", line["ref"], line["nom"], str(line["quantite"])])
    if len(data) == 1:
        data.append(["", "", "Aucune pièce", ""])
    table = Table(data, colWidths=[20*mm, 32*mm, 95*mm, 25*mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#263746")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("GRID", (0,0), (-1,-1), 0.4, colors.HexColor("#9AA6AF")),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#F1F4F6")]),
        ("LEFTPADDING", (0,0), (-1,-1), 5), ("RIGHTPADDING", (0,0), (-1,-1), 5),
        ("TOPPADDING", (0,0), (-1,-1), 7), ("BOTTOMPADDING", (0,0), (-1,-1), 7),
    ]))
    story.append(table)
    story.append(Spacer(1, 7*mm))
    story.append(Paragraph("<b>Contrôle avant départ</b>", styles["Heading2"]))
    for item in ["Toutes les pièces sont préparées", "Quantités vérifiées", "Commande emballée"]:
        story.append(Paragraph(f"[ ] {item}", styles["Normal"]))
    story.append(Spacer(1, 5*mm))
    story.append(Paragraph(f"<i>Seuil commun d'alerte de stock : {threshold} pièce(s). Les stocks sont déduits lors de la validation de la commande.</i>", styles["BodyText"]))
    doc.build(story)
    return buffer.getvalue()


def make_restock_pdf(pieces_df, threshold, atelier):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=12*mm, leftMargin=12*mm, topMargin=12*mm, bottomMargin=12*mm)
    styles = getSampleStyleSheet()
    story = [
        Paragraph("<b>LISTE DE RÉAPPROVISIONNEMENT</b>", styles["Title"]), Spacer(1, 3*mm),
        Paragraph(f"<b>Atelier :</b> {atelier} &nbsp;&nbsp; <b>Date :</b> {datetime.now().strftime('%d/%m/%Y')} &nbsp;&nbsp; <b>Seuil commun :</b> {threshold} pièce(s)", styles["Normal"]), Spacer(1, 6*mm)
    ]
    data = [["À refaire", "Référence", "Désignation", "Matériau", "Ép. (mm)", "Stock actuel", "Quantité cible*"]]
    for _, p in pieces_df.iterrows():
        target = max(threshold * 2, threshold + 1)
        data.append([ "[ ]", p["ref"], p["nom"], p.get("materiau", "") or "",
                      "" if pd.isna(p.get("epaisseur_mm")) else str(p["epaisseur_mm"]),
                      str(int(p["stock"])), str(max(0, target - int(p["stock"]))) ])
    if len(data) == 1:
        data.append(["", "", "Aucune pièce sous le seuil", "", "", "", ""])
    table = Table(data, colWidths=[18*mm, 27*mm, 70*mm, 40*mm, 20*mm, 25*mm, 30*mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#263746")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("GRID", (0,0), (-1,-1), 0.4, colors.HexColor("#9AA6AF")),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#F1F4F6")]),
        ("FONTSIZE", (0,0), (-1,-1), 8), ("TOPPADDING", (0,0), (-1,-1), 6), ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ]))
    story.append(table)
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph("*La quantité cible proposée est le double du seuil commun. Ajuste la quantité selon tes habitudes de production.", styles["BodyText"]))
    doc.build(story)
    return buffer.getvalue()


# ----------------------------- Interface Streamlit -----------------------------

threshold = int(get_setting("seuil_commun", "5"))
atelier = get_setting("nom_atelier", "Atelier meubles van")

st.title("🪚 Gestion de stock — meubles en kit pour van")
st.caption("Version 2 · stock de pièces découpées, commandes et fiches PDF · données stockées sur Supabase")

if "form_key" not in st.session_state:
    st.session_state.form_key = 0

with st.sidebar:
    st.header("Paramètres")
    new_threshold = st.number_input("Seuil commun d'alerte (pièces)", min_value=0, max_value=100000, value=threshold, step=1)
    new_atelier = st.text_input("Nom de l'atelier", value=atelier)
    if st.button("Enregistrer les paramètres", width="stretch"):
        set_setting("seuil_commun", int(new_threshold))
        set_setting("nom_atelier", new_atelier.strip() or "Atelier meubles van")
        st.success("Paramètres enregistrés.")
        st.rerun()
    st.divider()
    st.caption("Données sauvegardées en ligne sur Supabase.")

threshold = int(get_setting("seuil_commun", "5"))
atelier = get_setting("nom_atelier", "Atelier meubles van")
pieces_df = read_pieces()
low_df = pieces_df[pieces_df["stock"] <= threshold] if not pieces_df.empty else pieces_df

tabs = st.tabs(["📊 Tableau de bord", "📦 Inventaire", "🧾 Commandes", "⚠️ À refabriquer", "📥 Import / Export"])

with tabs[0]:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Références en catalogue", len(pieces_df))
    c2.metric("Pièces disponibles", int(pieces_df["stock"].sum()) if not pieces_df.empty else 0)
    c3.metric("Références en alerte", len(low_df))
    c4.metric("Ruptures", int((pieces_df["stock"] == 0).sum()) if not pieces_df.empty else 0)
    st.subheader("Alertes de stock")
    if low_df.empty:
        st.success("Aucune pièce sous le seuil commun.")
    else:
        st.warning(f"{len(low_df)} référence(s) au seuil ou en dessous (≤ {threshold}).")
        st.dataframe(low_df[["ref", "nom", "materiau", "epaisseur_mm", "stock"]].rename(columns={
            "ref":"Référence", "nom":"Désignation", "materiau":"Matériau", "epaisseur_mm":"Épaisseur (mm)", "stock":"Stock"
        }), width="stretch", hide_index=True)
    st.subheader("Dernières commandes")
    orders_df = read_orders()
    if orders_df.empty:
        st.info("Aucune commande pour le moment.")
    else:
        st.dataframe(orders_df[["order_code", "client", "created_at", "status"]].head(8).rename(columns={
            "order_code":"Commande", "client":"Client", "created_at":"Date", "status":"Statut"
        }), width="stretch", hide_index=True)

with tabs[1]:
    st.subheader("Inventaire des pièces")
    st.caption("La référence est unique. Les dimensions sont informatives et peuvent être laissées vides.")
    with st.expander("➕ Ajouter une pièce", expanded=False):
        with st.form("add_piece_form", clear_on_submit=True):
            col1, col2, col3 = st.columns(3)
            ref = col1.text_input("Référence *")
            nom = col2.text_input("Désignation *")
            materiau = col3.text_input("Matériau / panneau")
            col4, col5, col6, col7 = st.columns(4)
            ep = col4.number_input("Épaisseur (mm)", min_value=0.0, value=0.0, step=1.0)
            longueur = col5.number_input("Longueur (mm)", min_value=0.0, value=0.0, step=10.0)
            largeur = col6.number_input("Largeur (mm)", min_value=0.0, value=0.0, step=10.0)
            stock = col7.number_input("Stock initial", min_value=0, value=0, step=1)
            notes = st.text_input("Notes")
            submitted = st.form_submit_button("Ajouter la pièce")
            if submitted:
                if not ref.strip() or not nom.strip():
                    st.error("La référence et la désignation sont obligatoires.")
                elif not read_pieces().empty and ref.strip() in read_pieces()["ref"].values:
                    st.error("Cette référence existe déjà.")
                else:
                    upsert_piece({"ref":ref.strip(),"nom":nom.strip(),"materiau":materiau,
                                  "epaisseur_mm":ep or None,"longueur_mm":longueur or None,
                                  "largeur_mm":largeur or None,"stock":int(stock),"notes":notes})
                    st.success("Pièce ajoutée.")
                    st.rerun()

    pieces_df = read_pieces()
    if pieces_df.empty:
        st.info("Commence par ajouter une pièce ou importer un fichier CSV.")
    else:
        st.dataframe(pieces_df.rename(columns={
            "ref":"Référence","nom":"Désignation","materiau":"Matériau","epaisseur_mm":"Épaisseur (mm)",
            "longueur_mm":"Longueur (mm)","largeur_mm":"Largeur (mm)","stock":"Stock","notes":"Notes","updated_at":"Mis à jour"
        }), width="stretch", hide_index=True)
        st.markdown("**Ajuster rapidement le stock**")
        refs = pieces_df["ref"].tolist()
        chosen_ref = st.selectbox("Pièce à ajuster", refs, key="adjust_ref")
        current = pieces_df.loc[pieces_df["ref"] == chosen_ref].iloc[0]
        a, b, c = st.columns([2,2,3])
        delta = a.number_input("Variation (+ réception / − perte)", value=0, step=1, key="stock_delta")
        a_reason = b.selectbox("Motif", ["Production / réassort", "Correction inventaire", "Pièce abîmée", "Autre"], key="stock_reason")
        if c.button("Appliquer la variation", width="stretch"):
            new_stock = int(current["stock"]) + int(delta)
            if new_stock < 0:
                st.error("Le stock ne peut pas devenir négatif.")
            else:
                with conn.session as s:
                    s.execute("UPDATE pieces SET stock=:stock, updated_at=:updated_at WHERE ref=:ref",
                              params={"stock": new_stock, "updated_at": datetime.now().isoformat(timespec="seconds"), "ref": chosen_ref})
                    s.commit()
                st.success(f"Stock de {chosen_ref} : {current['stock']} → {new_stock}. Motif : {a_reason}.")
                st.rerun()

with tabs[2]:
    st.subheader("Créer une commande")
    pieces_df = read_pieces()
    
    col1, col2 = st.columns(2)
    default_code = f"CMD-{datetime.now().strftime('%Y%m%d-%H%M')}"
    order_code = col1.text_input("Référence de commande *", value=default_code, key=f"new_order_code_{st.session_state.form_key}")
    client = col2.text_input("Client", key=f"new_order_client_{st.session_state.form_key}")
    notes = st.text_area("Notes pour l'atelier", key=f"new_order_notes_{st.session_state.form_key}")
    
    st.markdown("**Pièces nécessaires**")
    selected_refs = []
    qty_by_ref = {}
    
    if pieces_df.empty:
        st.info("Ajoute ou importe d'abord des pièces dans l'inventaire.")
    else:
        selected_refs = st.multiselect(
            "Sélectionner les pièces de cette commande", 
            pieces_df["ref"].tolist(),
            format_func=lambda r: f"{r} — {pieces_df.loc[pieces_df['ref']==r,'nom'].iloc[0]}", 
            key=f"new_order_refs_{st.session_state.form_key}"
        )
        if selected_refs:
            cols = st.columns(min(3, len(selected_refs)))
            for i, r in enumerate(selected_refs):
                qty_by_ref[r] = cols[i % len(cols)].number_input(
                    f"Quantité — {r}", min_value=1, value=1, step=1, 
                    key=f"new_qty_{r}_{st.session_state.form_key}"
                )
    
    if st.button("Créer la commande (brouillon)", type="primary", key="create_order_button"):
        if not order_code.strip():
            st.error("La référence de commande est obligatoire.")
        elif not selected_refs:
            st.error("Sélectionne au moins une pièce.")
        else:
            try:
                existing = conn.query("SELECT id FROM orders WHERE order_code = :code", params={"code": order_code.strip()}, ttl=0)
                if not existing.empty:
                    st.error("Cette référence de commande existe déjà.")
                else:
                    with conn.session as s:
                        result = s.execute(
                            "INSERT INTO orders(order_code, client, created_at, status, notes) VALUES(:code, :client, :created_at, :status, :notes) RETURNING id",
                            params={
                                "code": order_code.strip(), "client": client.strip(),
                                "created_at": datetime.now().strftime("%d/%m/%Y %H:%M"),
                                "status": "Brouillon", "notes": notes.strip()
                            }
                        )
                        oid = result.scalar()
                        for r in selected_refs:
                            row = pieces_df.loc[pieces_df["ref"] == r].iloc[0]
                            s.execute(
                                "INSERT INTO order_lines(order_id, ref, nom_snapshot, quantite) VALUES(:oid, :ref, :nom, :qty)",
                                params={"oid": oid, "ref": r, "nom": row["nom"], "qty": int(qty_by_ref[r])}
                            )
                        s.commit()
                        
                        st.session_state["last_order_id"] = oid
                        st.session_state.form_key += 1
                        st.success("✅ Brouillon créé. Le stock n'est pas encore déduit.")
                        st.rerun()
            except Exception as e:
                st.error(f"❌ Erreur lors de la création : {type(e).__name__} — {e}")
                import traceback
                st.code(traceback.format_exc())

    st.divider()
    st.subheader("Commandes enregistrées")
    orders_df = read_orders()
    if orders_df.empty:
        st.info("Aucune commande enregistrée.")
    else:
        display_orders = orders_df[["id","order_code","client","created_at","status"]].rename(columns={
            "id":"ID","order_code":"Commande","client":"Client","created_at":"Date","status":"Statut"
        })
        st.dataframe(display_orders, width="stretch", hide_index=True)
        ids = orders_df["id"].tolist()
        default_id = st.session_state.get("last_order_id", ids[0])
        if default_id not in ids:
            default_id = ids[0]
        selected_id = st.selectbox("Commande à consulter", ids, index=ids.index(default_id), format_func=lambda i: orders_df.loc[orders_df["id"]==i,"order_code"].iloc[0])
        order, lines = get_order(int(selected_id))
        if order:
            st.write(f"**{order['order_code']}** — {order['client'] or 'Client non renseigné'} — {order['status']}")
            line_df = pd.DataFrame(lines)
            st.dataframe(line_df.rename(columns={"ref":"Référence","nom":"Désignation","quantite":"Quantité"}), width="stretch", hide_index=True)
            pdf = make_pdf(order, lines, atelier, threshold)
            st.download_button("📄 Télécharger la fiche atelier PDF", data=pdf, file_name=f"{order['order_code']}_fiche_atelier.pdf", mime="application/pdf")
            if order["status"] == "Brouillon":
                if st.button("Valider la commande et déduire le stock", type="primary", key=f"validate_{selected_id}"):
                    latest_order, latest_lines = get_order(int(selected_id))
                    latest_pieces = read_pieces().set_index("ref")
                    shortages = []
                    for line in latest_lines:
                        if line["ref"] not in latest_pieces.index or int(latest_pieces.loc[line["ref"], "stock"]) < int(line["quantite"]):
                            available = 0 if line["ref"] not in latest_pieces.index else int(latest_pieces.loc[line["ref"], "stock"])
                            shortages.append(f"{line['ref']} : besoin {line['quantite']}, disponible {available}")
                    if shortages:
                        st.error("Stock insuffisant : " + " ; ".join(shortages) + ". La commande n'a pas été validée.")
                    else:
                        with conn.session as s:
                            for line in latest_lines:
                                s.execute("UPDATE pieces SET stock = stock - :qty, updated_at = :updated_at WHERE ref = :ref",
                                          params={"qty": int(line["quantite"]), "updated_at": datetime.now().isoformat(timespec="seconds"), "ref": line["ref"]})
                            s.execute("UPDATE orders SET status='Validée' WHERE id=:id", params={"id": int(selected_id)})
                            s.commit()
                        st.success("Commande validée : stock déduit.")
                        st.rerun()
            elif order["status"] == "Validée":
                st.success("Stock déjà déduit pour cette commande. Ne pas valider à nouveau.")

with tabs[3]:
    st.subheader("Pièces à refabriquer")
    pieces_df = read_pieces()
    low_df = pieces_df[pieces_df["stock"] <= threshold].copy() if not pieces_df.empty else pieces_df
    if low_df.empty:
        st.success("Aucune pièce à refabriquer selon le seuil commun.")
    else:
        target = max(threshold * 2, threshold + 1)
        low_df["quantite_a_refaire"] = (target - low_df["stock"]).clip(lower=0).astype(int)
        st.caption(f"Les pièces dont le stock est inférieur ou égal à {threshold} sont listées. La quantité proposée vise {target} pièces en stock.")
        st.dataframe(low_df[["ref","nom","materiau","epaisseur_mm","stock","quantite_a_refaire"]].rename(columns={
            "ref":"Référence","nom":"Désignation","materiau":"Matériau","epaisseur_mm":"Épaisseur (mm)",
            "stock":"Stock actuel","quantite_a_refaire":"Quantité à refaire"
        }), width="stretch", hide_index=True)
        pdf = make_restock_pdf(low_df, threshold, atelier)
        st.download_button("📄 Télécharger la liste de réapprovisionnement PDF", data=pdf, file_name="liste_pieces_a_refabriquer.pdf", mime="application/pdf")

with tabs[4]:
    st.subheader("Importer un catalogue Excel / CSV")
    st.write("Le fichier doit contenir au minimum une colonne de référence et une colonne de désignation. Le stock peut être inclus.")
    st.caption("Colonnes reconnues : ref/référence/code, nom/désignation/pièce, stock/quantité, matériau, épaisseur, longueur, largeur, notes.")
    uploaded = st.file_uploader("Choisir un fichier CSV ou Excel", type=["csv","xlsx","xls"])
    import_mode = st.radio("Si une référence existe déjà", ["Mettre à jour aussi le stock depuis le fichier", "Mettre à jour les informations sans modifier le stock"], index=1)
    if uploaded is not None:
        try:
            if uploaded.name.lower().endswith(".csv"):
                try:
                    import_df = pd.read_csv(uploaded, sep=None, engine="python", encoding="utf-8-sig")
                except UnicodeDecodeError:
                    uploaded.seek(0)
                    import_df = pd.read_csv(uploaded, sep=None, engine="python", encoding="cp1252")
            else:
                import_df = pd.read_excel(uploaded)
            st.write("Aperçu du fichier :")
            st.dataframe(import_df.head(10), width="stretch", hide_index=True)
            if st.button("Importer le catalogue", type="primary"):
                # Nettoyage simplifié pour l'exemple
                cleaned = import_df.copy()
                cleaned.columns = [str(c).strip().lower() for c in cleaned.columns]
                # Renommage de base (simplifié)
                rename_map = {"référence":"ref", "reference":"ref", "désignation":"nom", "designation":"nom", "quantité":"stock", "quantite":"stock"}
                cleaned = cleaned.rename(columns=rename_map)
                if "ref" not in cleaned.columns or "nom" not in cleaned.columns:
                    st.error("Colonnes 'ref' et 'nom' obligatoires.")
                else:
                    cleaned["ref"] = cleaned["ref"].astype(str).str.strip()
                    cleaned["nom"] = cleaned["nom"].astype(str).str.strip()
                    cleaned = cleaned[(cleaned["ref"] != "") & (cleaned["nom"] != "")]
                    if "stock" not in cleaned.columns:
                        cleaned["stock"] = 0
                    cleaned["stock"] = pd.to_numeric(cleaned["stock"], errors="coerce").fillna(0).astype(int)
                    
                    update_stock = import_mode == "Mettre à jour aussi le stock depuis le fichier"
                    for _, row in cleaned.iterrows():
                        upsert_piece(row, allow_stock_update=update_stock)
                    st.success(f"{len(cleaned)} référence(s) importée(s) ou mise(s) à jour.")
                    st.rerun()
        except Exception as exc:
            st.error(f"Impossible de lire ce fichier : {exc}")
    st.divider()
    st.subheader("Exporter le stock")
    pieces_df = read_pieces()
    if not pieces_df.empty:
        csv_bytes = pieces_df.drop(columns=["updated_at"], errors="
