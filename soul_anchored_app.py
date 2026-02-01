import os
import re
import io
import time
import json
import tempfile
import streamlit as st
import pandas as pd
from datetime import datetime
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from supabase import create_client, Client
import google.generativeai as genai

# Configuration from Streamlit Secrets
try:
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
    FOLDER_ID = st.secrets.get("FOLDER_ID", "15xna7XFA7W3liDawGjbHqpF7o4_nmo1e")
except KeyError as e:
    st.error(f"Configuração ausente nos Secrets: {e}")
    st.stop()

# Setup Gemini
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    gemini_model = genai.GenerativeModel('gemini-2.0-flash')
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
    """Uses Gemini to align script with audio in 10s increments."""
    if not gemini_model:
        return None
    
    with st.status("Analizando áudio e roteiro com IA...", expanded=True) as status:
        st.write("Enviando áudio para o Gemini...")
        audio_file = genai.upload_file(path=audio_path)
        
        while audio_file.state.name == "PROCESSING":
            time.sleep(2)
            audio_file = genai.get_file(audio_file.name)
            
        prompt = f"""
        Você é um Diretor de Montagem especializado em vídeos para redes sociais.
        Analise este áudio de narração e o roteiro abaixo.
        
        OBJETIVO: Dividir o roteiro em blocos de 10 segundos baseando-se no RITMO real da narração (time-alignment).
        
        ROTEIRO:
        {script_text}
        
        REGRAS DE OUTPUT:
        Retorne um JSON puro (sem markdown) no seguinte formato:
        [
          {{
            "timestamp": "00:00",
            "script_fragment": "o texto exato dito entre 0 e 10s",
            "visual_theme": "descrição curta do tema visual/ação para este trecho"
          }},
          ... (continuar a cada 10 segundos até o fim do áudio)
        ]
        """
        
        st.write("Sincronizando ritmo...")
        response = gemini_model.generate_content([audio_file, prompt])
        
        # Cleanup
        genai.delete_file(audio_file.name)
        
        try:
            # Clean possible markdown formatting
            clean_json = re.search(r'\[.*\]', response.text, re.DOTALL).group()
            return json.loads(clean_json)
        except Exception as e:
            st.error(f"Erro ao processar resposta da IA: {e}")
            return None

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
st.subheader("Cérebro Editorial Multimodal 🧠🎙️")

with st.sidebar:
    st.header("📊 Status")
    st.success("✅ Supabase Conectado")
    st.info("💡 Este modo usa o Gemini para ouvir o seu áudio e alinhar o roteiro perfeitamente.")

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
        audio_file = st.file_uploader("Upload de Áudio da Narração (.mp3/wav)", type=['mp3', 'wav'])
        if audio_file:
            st.audio(audio_file)

    if script_text and audio_file:
        if st.button("🧠 Gerar Storyboard (IA Multimodal)"):
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{audio_file.name.split('.')[-1]}") as tmp:
                tmp.write(audio_file.getvalue())
                tmp_path = tmp.name
            
            gemini_storyboard = get_storyboard_from_gemini(tmp_path, script_text)
            
            if gemini_storyboard:
                supabase = get_supabase_client()
                
                # Anti-Repetition
                recent_res = supabase.table("video_library").select("file_id").order("last_used_at", desc=True).limit(5).execute()
                recent_ids = [v['file_id'] for v in recent_res.data]
                
                # Pool
                pool_res = supabase.table("video_library").select("*").order("last_used_at", desc=False, nullsfirst=True).execute()
                videos_pool = pool_res.data
                
                storyboard_final = []
                used_in_this_session = []

                for block in gemini_storyboard:
                    desc_theme = block.get('visual_theme', '')
                    # Combine original tags with AI theme for better matching
                    tags_needed = [w.lower() for w in re.findall(r'\w{5,}', desc_theme + " " + block.get('script_fragment', ''))]
                    
                    best_match = None
                    candidates = [v for v in videos_pool if v['file_id'] not in recent_ids and v['file_id'] not in used_in_this_session]
                    
                    for v in candidates:
                        v_tags = [t.lower() for t in v.get('tags', [])]
                        if any(t in v_tags for t in tags_needed):
                            best_match = v; break
                    
                    if not best_match:
                        if candidates: best_match = candidates[0]
                        else: best_match = videos_pool[0]
                    
                    storyboard_final.append({
                        "Tempo": block['timestamp'],
                        "Trecho do Roteiro": block['script_fragment'],
                        "TEMA IDENTIFICADO": desc_theme,
                        "ARQUIVO SUGERIDO": f"🎬 {best_match['file_name']}",
                        "file_id": best_match['file_id'],
                        "file_name": best_match['file_name']
                    })
                    used_in_this_session.append(best_match['file_id'])

                st.session_state['current_storyboard'] = storyboard_final
                st.success("Storyboard alinhado ao áudio com sucesso!")
            
            os.remove(tmp_path)

    if 'current_storyboard' in st.session_state:
        sb = st.session_state['current_storyboard']
        df_sb = pd.DataFrame(sb)[["Tempo", "Trecho do Roteiro", "TEMA IDENTIFICADO", "ARQUIVO SUGERIDO"]]
        
        st.divider()
        st.header("📋 Storyboard Técnico (IA Alinhada)")
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
