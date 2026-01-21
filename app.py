import streamlit as st
from apify_client import ApifyClient
import pandas as pd
import io
import time
import requests
from datetime import datetime

# --- 1. CONFIGURAÇÃO ---
st.set_page_config(page_title="Atlas Intelligence", page_icon="🌐", layout="centered")

# --- 2. ATORES (ROBÔS) ---
ACTOR_COMMENTS = "datadoping/linkedin-post-comments-scraper"
ACTOR_LIKES = "harvestapi/linkedin-post-reactions"

# --- 3. CSS CLEAN ---
st.markdown("""
<style>
    .stApp { background-color: #F0F2F6; color: #31333F; }
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    .clean-card {
        background-color: #FFFFFF;
        padding: 40px;
        border-radius: 12px;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05);
        border: 1px solid #E6E6EA;
        text-align: center;
        margin-bottom: 20px;
    }
    h1 { color: #1F1F1F !important; font-family: 'Helvetica Neue', sans-serif; font-weight: 700; margin-bottom: 0.5rem; }
    .subtitle { color: #666666; font-size: 1rem; margin-bottom: 2rem; }
    .stTextInput > div > div > input { background-color: #FFFFFF; color: #31333F; border: 1px solid #CED4DA; border-radius: 6px; height: 45px; padding-left: 15px; }
    .stTextInput > div > div > input:focus { border-color: #FF4B4B; box-shadow: none; }
    .stButton > button { background-color: #FF4B4B; color: white; border: none; border-radius: 6px; height: 48px; font-weight: 600; width: 100%; font-size: 16px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
    .stButton > button:hover { background-color: #D43F3F; color: white; border: none; }
    div[data-testid="stMetricValue"] { color: #FF4B4B; font-weight: 700; }
</style>
""", unsafe_allow_html=True)

# --- 4. SESSÃO ---
if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False

# --- 5. TELA DE LOGIN ---
def login_screen():
    if "SENHA_ACESSO" not in st.secrets:
        st.error("⚠️ Configure a SENHA_ACESSO nos Secrets!")
        st.stop()
    col1, col2, col3 = st.columns([1, 4, 1])
    with col2:
        st.markdown('<div class="clean-card">', unsafe_allow_html=True)
        st.markdown("<h1>ATLAS SYSTEM</h1>", unsafe_allow_html=True)
        st.markdown('<p class="subtitle">Acesso Seguro Corporativo</p>', unsafe_allow_html=True)
        senha = st.text_input("Senha de Acesso", type="password", label_visibility="collapsed", placeholder="Digite sua senha...")
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("ENTRAR NO SISTEMA"):
            if senha == st.secrets["SENHA_ACESSO"]:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Senha incorreta.")
        st.markdown('</div>', unsafe_allow_html=True)

# --- 6. APP PRINCIPAL ---
def main_app():
    c1, c2 = st.columns([8, 1])
    with c2:
        if st.button("Sair"):
            st.session_state.authenticated = False
            st.rerun()

    st.markdown("# Atlas Intelligence")
    st.markdown("Extrator de dados do LinkedIn (Comentários + Likes).")
    st.markdown("---")

    if "APIFY_TOKEN" not in st.secrets:
        st.error("Token Apify ausente.")
        st.stop()
    
    api_token = st.secrets["APIFY_TOKEN"]
    clay_url = st.secrets.get("CLAY_WEBHOOK", "") 

    st.markdown('<div class="clean-card" style="padding: 30px; text-align: left;">', unsafe_allow_html=True)
    st.markdown("### Novo Relatório")
    url_input = st.text_input("Link do Post", placeholder="Cole a URL do LinkedIn aqui...", label_visibility="collapsed")
    st.markdown("<br>", unsafe_allow_html=True)
    
    if st.button("INICIAR EXTRAÇÃO"):
        if not url_input:
            st.warning("⚠️ O campo de link está vazio.")
        else:
            with st.status("Processando dados...", expanded=True) as status:
                try:
                    client = ApifyClient(api_token)
                    
                    # 1. COMENTÁRIOS
                    status.write("💬 Buscando Comentários...")
                    run_c = client.actor(ACTOR_COMMENTS).call(run_input={ 
                        "posts": [url_input], "maxComments": 200, "minDelay": 1, "maxDelay": 4 
                    })
                    data_c = client.dataset(run_c["defaultDatasetId"]).list_items().items
                    df_c = pd.DataFrame(data_c)
                    cols_c = ['text', 'posted_at', 'comment_url', 'author', 'owner_name', 'owner_profile_url']
                    if not df_c.empty:
                        df_c = df_c[[c for c in cols_c if c in df_c.columns]]

                    # 2. LIKES
                    status.write("👍 Buscando Reações...")
                    run_l = client.actor(ACTOR_LIKES).call(run_input={
                        "posts": [url_input], "maxItems": 1000
                    })
                    data_l = client.dataset(run_l["defaultDatasetId"]).list_items().items
                    
                    lista_l = []
                    for item in data_l:
                        actor = item.get('actor', {})
                        lista_l.append({
                            "Nome": actor.get('name') or item.get('name'),
                            "Headline": actor.get('position') or item.get('headline'),
                            "Perfil URL": actor.get('linkedinUrl') or actor.get('profileUrl') or item.get('profileUrl'),
                            "Reação": item.get('reactionType'),
                        })
                    df_l = pd.DataFrame(lista_l)

                    # 3. EXCEL (Mantido para backup)
                    status.write("📊 Criando Excel...")
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                        if not df_c.empty: df_c.to_excel(writer, index=False, sheet_name='Comentarios')
                        if not df_l.empty: df_l.to_excel(writer, index=False, sheet_name='Likes')

                    # 4. CLAY (ENVIO LINHA POR LINHA)
                    if clay_url:
                        status.write("📡 Enviando para Clay (Linha a Linha)...")
                        
                        total_itens = len(df_c) + len(df_l)
                        progresso = st.progress(0)
                        contador = 0

                        # Enviar Comentários (Um por um)
                        if not df_c.empty and 'owner_name' in df_c.columns:
                            for index, row in df_c.iterrows():
                                payload = {
                                    "Origem": "Comentario",
                                    "Nome": row.get('owner_name'),
                                    "Perfil_URL": row.get('owner_profile_url'),
                                    "Post_Link": url_input,
                                    "Data_Extracao": datetime.now().isoformat()
                                }
                                requests.post(clay_url, json=payload)
                                contador += 1
                                progresso.progress(min(contador / total_itens, 1.0))

                        # Enviar Likes (Um por um)
                        if not df_l.empty and 'Nome' in df_l.columns:
                            for index, row in df_l.iterrows():
                                payload = {
                                    "Origem": "Like",
                                    "Nome": row.get('Nome'),
                                    "Perfil_URL": row.get('Perfil URL'),
                                    "Tipo_Reacao": row.get('Reação'),
                                    "Post_Link": url_input,
                                    "Data_Extracao": datetime.now().isoformat()
                                }
                                requests.post(clay_url, json=payload)
                                contador += 1
                                progresso.progress(min(contador / total_itens, 1.0))
                        
                        st.success(f"Enviados {contador} leads para o Clay!")

                    status.update(label="Sucesso!", state="complete", expanded=False)
                    
                    st.markdown("<br>", unsafe_allow_html=True)
                    m1, m2 = st.columns(2)
                    m1.metric("Comentários", len(df_c))
                    m2.metric("Likes/Reações", len(df_l))
                    
                    st.markdown("<br>", unsafe_allow_html=True)
                    st.download_button("📥 BAIXAR EXCEL", data=buffer, file_name="Atlas_Dados.xlsx", use_container_width=True)

                except Exception as e:
                    st.error(f"Erro: {e}")
    st.markdown('</div>', unsafe_allow_html=True)

if st.session_state.authenticated:
    main_app()
else:
    login_screen()
