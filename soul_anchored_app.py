import os
import re
import io
import time
import json
import tempfile
import uuid
import streamlit as st
import pandas as pd
from datetime import datetime
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from supabase import create_client, Client
import google.generativeai as genai

# Streamlit Page Config MUST be the first call
st.set_page_config(page_title="Soul Anchored - Cérebro Editorial", page_icon="🧠", layout="wide")

# Configuration from Streamlit Secrets
try:
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
    FOLDER_ID = st.secrets.get("FOLDER_ID", "15xna7XFA7W3liDawGjbHqpF7o4_nmo1e")
except Exception as e:
    st.error(f"⚠️ Erro ao carregar segredos: {e}")
    st.stop()

# Setup Gemini
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    gemini_model = genai.GenerativeModel('gemini-1.5-flash') # Using stable 1.5 flash for reliability
else:
    gemini_model = None

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

def get_storyboard_from_gemini(audio_path, script_text):
    if not gemini_model:
        st.error("IA não configurada (API Key ausente).")
        return None
    
    with st.status("🧠 IA Analisando Áudio e Roteiro...", expanded=True) as status:
        try:
            st.write("📤 Enviando narração...")
            audio_file = genai.upload_file(path=audio_path)
            
            # Wait for processing
            while audio_file.state.name == "PROCESSING":
                time.sleep(2)
                audio_file = genai.get_file(audio_file.name)
            
            if audio_file.state.name == "FAILED":
                st.error("Falha no processamento do arquivo de áudio pela IA.")
                return None

            prompt = f"""
            Você é um Diretor de Montagem Sênior. Sua tarefa é analisar o áudio de narração e o roteiro.
            
            OBJETIVO: Sincronizar o roteiro em blocos de 10 segundos baseando-se no ritmo real de fala.
            
            ROTEIRO A IDENTIFICAR:
            {script_text}
            
            REGRAS:
            1. Divida a cada 10s de áudio.
            2. Extraia o trecho exato do roteiro falado nesse intervalo.
            3. Defina um tema visual sugestivo.
            4. Retorne APENAS um JSON puro (sem markdown) no formato:
               [{{"timestamp": "00:00", "script_fragment": "...", "visual_theme": "..."}}]
            """
            
            st.write("⚡ Sincronizando conteúdo...")
            response = gemini_model.generate_content([audio_file, prompt])
            
            # Extract JSON string safely
            json_match = re.search(r'\[.*\]', response.text, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                genai.delete_file(audio_file.name) # Cleanup
                status.update(label="Sincronização concluída!", state="complete")
                return result
            else:
                st.error("A IA retornou um formato inesperado.")
                return None
        except Exception as e:
            st.error(f"Erro na análise multimodal: {e}")
            return None

# --- UI Header ---
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
st.subheader("Cérebro Editorial Multimodal 🧠🎙️")

tab1, tab2 = st.tabs(["🚀 Produção de Roteiro", "📂 Biblioteca"])

with tab2:
    st.header("Biblioteca de Vídeos")
    try:
        supabase = get_supabase_client()
        res = supabase.table("video_library").select("file_name, tags, last_used_at").order("last_used_at", desc=False, nullsfirst=True).execute()
        if res.data:
            df_lib = pd.DataFrame(res.data)
            st.dataframe(df_lib, use_container_width=True)
        else:
            st.warning("Biblioteca vazia. Indexe arquivos primeiro.")
    except Exception as e:
        st.error(f"Erro ao conectar ao Supabase: {e}")

with tab1:
    col1, col2 = st.columns([1, 1])
    with col1:
        project_title = st.text_input("Título do Projeto", value="Nova Montagem")
        script_text = st.text_area("Roteiro Original", height=250, placeholder="Cole o roteiro aqui...")
    
    with col2:
        audio_file = st.file_uploader("Upload de Áudio da Narração (.mp3/wav)", type=['mp3', 'wav'])
        if audio_file:
            st.audio(audio_file)

    if st.button("🧠 Gerar Storyboard Baseado no Áudio"):
        if not script_text or not audio_file:
            st.warning("Forneça o roteiro e o áudio para continuar.")
        else:
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{audio_file.name.split('.')[-1]}") as tmp:
                tmp.write(audio_file.getvalue())
                tmp_path = tmp.name
            
            storyboard = get_storyboard_from_gemini(tmp_path, script_text)
            os.remove(tmp_path)
            
            if storyboard:
                # Video Matching logic
                supabase = get_supabase_client()
                recent_res = supabase.table("video_library").select("file_id").order("last_used_at", desc=True).limit(5).execute()
                recent_ids = [v['file_id'] for v in (recent_res.data or [])]
                
                all_videos = supabase.table("video_library").select("*").order("last_used_at", desc=False, nullsfirst=True).execute().data or []
                
                final_plan = []
                session_used = []
                
                for block in storyboard:
                    theme = block.get('visual_theme', '')
                    script_chunk = block.get('script_fragment', '')
                    tags_needed = [w.lower() for w in re.findall(r'\w{5,}', theme + " " + script_chunk)]
                    
                    # Selection
                    candidates = [v for v in all_videos if v['file_id'] not in recent_ids and v['file_id'] not in session_used]
                    best_match = None
                    for v in candidates:
                        v_tags = [t.lower() for t in v.get('tags', [])]
                        if any(t in v_tags for t in tags_needed):
                            best_match = v; break
                    
                    if not best_match:
                        if candidates: best_match = candidates[0]
                        elif all_videos: best_match = all_videos[0]
                    
                    if best_match:
                        final_plan.append({
                            "Tempo": block['timestamp'],
                            "Texto": script_chunk,
                            "Sugestão Visual": theme,
                            "ARQUIVO": f"🎬 {best_match['file_name']}",
                            "file_id": best_match['file_id'],
                            "file_name": best_match['file_name']
                        })
                        session_used.append(best_match['file_id'])
                
                st.session_state['last_storyboard'] = final_plan
                st.success("Storyboard gerado!")

    if 'last_storyboard' in st.session_state:
        sb = st.session_state['last_storyboard']
        st.divider()
        st.header("📋 Tabela de Montagem Técnico")
        st.table(pd.DataFrame(sb)[["Tempo", "Texto", "Sugestão Visual", "ARQUIVO"]])
        
        c1, c2 = st.columns(2)
        with c1:
            if st.button("✅ Confirmar Montagem e Registrar Uso", use_container_width=True):
                supabase = get_supabase_client()
                now = datetime.now().isoformat()
                for item in sb:
                    supabase.table("video_library").update({"last_used_at": now}).eq("file_id", item['file_id']).execute()
                st.balloons()
                st.success("Uso registrado no banco!")
                del st.session_state['last_storyboard']
                st.rerun()
        
        with c2:
            txt = f"ROTEIRO TÉCNICO: {project_title}\n" + "="*30 + "\n"
            for item in sb:
                txt += f"[{item['Tempo']}] -> {item['file_name']}\n"
            st.download_button("📲 Baixar TXT para WhatsApp", txt, file_name="roteiro.txt", use_container_width=True)
