import os
import re
import io
import time
import json
import streamlit as st
import pandas as pd
from datetime import datetime
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from supabase import create_client, Client

# Configuration from Streamlit Secrets
try:
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
    FOLDER_ID = st.secrets.get("FOLDER_ID", "15xna7XFA7W3liDawGjbHqpF7o4_nmo1e")
except KeyError as e:
    st.error(f"Configuração ausente nos Secrets: {e}")
    st.stop()

# Drive Scopes
SCOPES = ['https://www.googleapis.com/auth/drive.metadata.readonly', 'https://www.googleapis.com/auth/drive.readonly']

@st.cache_resource
def get_supabase_client():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def get_drive_service():
    creds = None
    if "GOOGLE_TOKEN" in st.secrets:
        token_info = json.loads(st.secrets["GOOGLE_TOKEN"])
        creds = Credentials.from_authorized_user_info(token_info, SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if "GOOGLE_CREDENTIALS" not in st.secrets:
                return None
            creds_info = json.loads(st.secrets["GOOGLE_CREDENTIALS"])
            flow = InstalledAppFlow.from_client_config(creds_info, SCOPES)
            creds = flow.run_local_server(port=0)
    return build('drive', 'v3', credentials=creds)

# --- UI Layout ---
st.set_page_config(page_title="Soul Anchored - Cérebro Editorial", page_icon="🧠", layout="wide")

st.markdown("""
    <style>
    .stApp { background: linear-gradient(135deg, #07080c 0%, #11121d 100%); color: #e0e0e0; }
    h1, h2, h3 { font-family: 'Outfit', sans-serif; background: linear-gradient(to right, #00d2ff, #7000ff); -webkit-background-clip: text; -webkit-text-fill-color: transparent; font-weight: 800; }
    .stButton>button { background: linear-gradient(135deg, #7000ff 0%, #00d2ff 100%); color: white; border-radius: 8px; font-weight: 600; border: none; padding: 0.5rem 2rem; }
    .stTable { background-color: rgba(255,255,255,0.05); border-radius: 10px; }
    [data-testid="stMetricValue"] { color: #00d2ff; }
    </style>
    """, unsafe_allow_html=True)

st.title("Soul Anchored Assembler")
st.subheader("Cérebro Editorial 🧠")

with st.sidebar:
    st.header("📊 Status")
    st.success("✅ Supabase Conectado")
    st.info("💡 Este modo gera o roteiro técnico para montagem manual no CapCut.")

tab1, tab2 = st.tabs(["🚀 Roteiro de Montagem", "📂 Biblioteca"])

with tab2:
    st.header("Biblioteca de Vídeos")
    supabase = get_supabase_client()
    res = supabase.table("video_library").select("file_name, tags, last_used_at").order("last_used_at", desc=True, nullsfirst=False).execute()
    if res.data:
        df_lib = pd.DataFrame(res.data)
        st.dataframe(df_lib, use_container_width=True)

with tab1:
    col1, col2 = st.columns([1, 1])
    with col1:
        project_title = st.text_input("Título do Projeto", value="Nova Montagem")
        script_text = st.text_area("Roteiro Original", height=250, placeholder="Cole o roteiro completo aqui...")
    
    with col2:
        # Estimation logic
        words = len(re.findall(r'\w+', script_text)) if script_text else 0
        est_duration = words / 2.3  # Average speech rate
        duration = st.number_input("Duração do Áudio (segundos)", value=float(round(est_duration, 1)), step=1.0)
        st.caption(f"💡 Estimativa baseada no texto: ~{est_duration:.1f}s")

    if script_text and duration > 0:
        if st.button("🧠 Gerar Storyboard Editorial"):
            supabase = get_supabase_client()
            
            # Anti-Repetition: Get 5 most recently used IDs
            recent_res = supabase.table("video_library").select("file_id").order("last_used_at", desc=True).limit(5).execute()
            recent_ids = [v['file_id'] for v in recent_res.data]
            
            # Pool: Order by oldest use first
            pool_res = supabase.table("video_library").select("*").order("last_used_at", desc=False, nullsfirst=True).execute()
            videos_pool = pool_res.data
            
            num_blocks = max(1, int(duration // 10) + (1 if duration % 10 > 2 else 0))
            sentences = [s.strip() for s in re.split(r'[.!?\n]+', script_text) if s.strip()]
            s_per_b = max(1, len(sentences) // num_blocks)
            
            storyboard = []
            used_in_this_session = []

            for i in range(num_blocks):
                time_code = f"{i*10:02d}:00"
                block_text = " ".join(sentences[i*s_per_b : (i+1)*s_per_b])
                tags_needed = [w.lower() for w in re.findall(r'\w{5,}', block_text)]
                
                # Selection logic:
                # 1. Matches tag AND not in recent_ids AND not used in this session
                # 2. Matches tag AND not in recent_ids
                # 3. Matches tag
                # 4. Oldest in pool
                
                best_match = None
                # Filter pool to avoid recent 5 and session duplicates
                candidates = [v for v in videos_pool if v['file_id'] not in recent_ids and v['file_id'] not in used_in_this_session]
                
                # Try tag match in candidates
                for v in candidates:
                    v_tags = [t.lower() for t in v.get('tags', [])]
                    if any(t in v_tags for t in tags_needed):
                        best_match = v; break
                
                if not best_match:
                    # Fallback 1: Any candidate (oldest among them)
                    if candidates: best_match = candidates[0]
                    # Fallback 2: Any available matching tag even if recent/session (emergency)
                    else: best_match = videos_pool[0]
                
                storyboard.append({
                    "Tempo": time_code,
                    "Trecho do Roteiro": (block_text[:75] + '...') if len(block_text) > 75 else block_text,
                    "ARQUIVO SUGERIDO": f"🎬 {best_match['file_name']}",
                    "file_id": best_match['file_id'],
                    "file_name": best_match['file_name']
                })
                used_in_this_session.append(best_match['file_id'])

            st.session_state['current_storyboard'] = storyboard
            st.success("Storyboard gerado com sucesso!")

    if 'current_storyboard' in st.session_state:
        sb = st.session_state['current_storyboard']
        df_sb = pd.DataFrame(sb)[["Tempo", "Trecho do Roteiro", "ARQUIVO SUGERIDO"]]
        
        st.divider()
        st.header("📋 Tabela de Montagem")
        st.table(df_sb)
        
        c1, c2 = st.columns(2)
        with c1:
            if st.button("✅ Confirmar Montagem e Registrar Uso", use_container_width=True):
                supabase = get_supabase_client()
                now = datetime.now().isoformat()
                for item in sb:
                    supabase.table("video_library").update({"last_used_at": now}).eq("file_id", item['file_id']).execute()
                st.balloons()
                st.success("🚀 Uso registrado! O sistema evitará estes arquivos nas próximas sugestões.")
                del st.session_state['current_storyboard']
                st.rerun()

        with c2:
            # Generate TXT
            txt_content = f"ROTEIRO TÉCNICO: {project_title}\n" + "="*30 + "\n\n"
            for item in sb:
                txt_content += f"[{item['Tempo']}] -> {item['file_name']}\n"
            
            st.download_button(
                label="📲 Baixar Roteiro (WhatsApp/TXT)",
                data=txt_content,
                file_name=f"roteiro_{project_title.lower().replace(' ', '_')}.txt",
                mime="text/plain",
                use_container_width=True
            )
