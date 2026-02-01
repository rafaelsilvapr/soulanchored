import os
import re
import io
import time
import tempfile
import subprocess
import json
import uuid
import shutil
import zipfile
import streamlit as st
import pandas as pd
from datetime import datetime
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload
from supabase import create_client, Client
from mutagen.mp3 import MP3
from mutagen.wave import WAVE
from PIL import Image
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
    # No Cloud, tentamos recuperar o token dos secrets ou forçamos novo login
    if "GOOGLE_TOKEN" in st.secrets:
        token_info = json.loads(st.secrets["GOOGLE_TOKEN"])
        creds = Credentials.from_authorized_user_info(token_info, SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if "GOOGLE_CREDENTIALS" not in st.secrets:
                st.error("Erro: Credenciais do Google (JSON) não encontradas nos Secrets.")
                return None
            
            creds_info = json.loads(st.secrets["GOOGLE_CREDENTIALS"])
            flow = InstalledAppFlow.from_client_config(creds_info, SCOPES)
            creds = flow.run_local_server(port=0)
            
    return build('drive', 'v3', credentials=creds)

def download_file_from_drive(service, file_id, destination):
    request = service.files().get_media(fileId=file_id)
    fh = io.FileIO(destination, 'wb')
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    return True

def generate_draft_meta(project_name, draft_id, drafts_path):
    now_ms = int(time.time() * 1000 * 1000)
    meta = {
        "draft_name": project_name,
        "draft_id": draft_id,
        "draft_root_path": drafts_path,
        "create_time": now_ms,
        "update_time": now_ms,
        "draft_type": 0,
        "draft_version": "1.0",
        "save_location": 0
    }
    return meta

def generate_draft_content(project_name, clips, audio_info, duration_s):
    # CapCut uses microseconds (ms * 1000) for time, and 10^6 for seconds
    FPS = 30
    US_PER_S = 1000000
    
    materials = {"videos": [], "audios": []}
    v_track_segments = []
    a_track_segments = []
    
    # Audio Material & Segment
    a_id = str(uuid.uuid4()).upper()
    materials["audios"].append({
        "id": a_id,
        "path": audio_info['local_path'],
        "duration": int(duration_s * US_PER_S),
        "type": "audio"
    })
    
    a_track_segments.append({
        "id": str(uuid.uuid4()).upper(),
        "material_id": a_id,
        "target_timerange": {"start": 0, "duration": int(duration_s * US_PER_S)},
        "source_timerange": {"start": 0, "duration": int(duration_s * US_PER_S)},
        "type": "audio"
    })
    
    # Video Clips
    for i, clip in enumerate(clips):
        v_id = str(uuid.uuid4()).upper()
        materials["videos"].append({
            "id": v_id,
            "path": clip['local_path'],
            "duration": int(clip['duration'] * US_PER_S),
            "type": "video"
        })
        
        # Every video starts at index * 10s
        target_start = i * 10 * US_PER_S
        v_track_segments.append({
            "id": str(uuid.uuid4()).upper(),
            "material_id": v_id,
            "target_timerange": {"start": target_start, "duration": int(clip['duration'] * US_PER_S)},
            "source_timerange": {"start": 0, "duration": int(clip['duration'] * US_PER_S)},
            "type": "video"
        })
        
    content = {
        "materials": materials,
        "tracks": [
            {"id": str(uuid.uuid4()).upper(), "type": "video", "segments": v_track_segments},
            {"id": str(uuid.uuid4()).upper(), "type": "audio", "segments": a_track_segments}
        ],
        "canvas_config": {"width": 1080, "height": 1920},
        "fps": FPS
    }
    return content

# Check if running on Streamlit Cloud
IS_CLOUD = st.secrets.get("RUNNING_ON_CLOUD", False) or os.getenv("STREAMLIT_RUNTIME_ENV") == "cloud"
# Manual override or better detection: Streamlit Cloud usually has specific env vars
if not IS_CLOUD:
    # Check for common Streamlit Cloud environment variables
    if os.environ.get("HOSTNAME", "").startswith("streamlit"):
        IS_CLOUD = True

# --- UI Layout ---
st.set_page_config(page_title="Soul Anchored Assembler", page_icon="🎬", layout="wide")

# Custom CSS
st.markdown("""
    <style>
    .stApp { background: linear-gradient(135deg, #0a0b10 0%, #1a1b25 100%); color: #e0e0e0; }
    [data-testid="stSidebar"] { background-color: rgba(255, 255, 255, 0.03); backdrop-filter: blur(10px); }
    h1, h2, h3 { font-family: 'Outfit', sans-serif; background: linear-gradient(to right, #ffffff, #7000ff); -webkit-background-clip: text; -webkit-text-fill-color: transparent; font-weight: 800; }
    .stButton>button { background: linear-gradient(135deg, #7000ff 0%, #00d2ff 100%); color: white; border-radius: 50px; font-weight: 600; box-shadow: 0 4px 15px rgba(112, 0, 255, 0.3); }
    </style>
    """, unsafe_allow_html=True)

st.title("Soul Anchored Assembler")
if IS_CLOUD:
    st.subheader("Cloud Edition ☁️ (Exportar Kit)")
else:
    st.subheader("Native Draft Injection 🚀 (Local Edition)")

with st.sidebar:
    if not IS_CLOUD:
        st.header("⚙️ Configurações Local")
        drafts_path = st.text_input("Caminho da Pasta de Rascunhos (CapCut)", 
                                   value="/Users/rafaelrodriguesdasilva/Movies/CapCut/User Data/Projects/com.lveditor.draft")
    
    st.divider()
    st.header("📊 Status")
    st.success("✅ Supabase Conectado")
    if IS_CLOUD:
        st.info("💡 Modo Nuvem: O projeto será exportado como um arquivo de kit (.zip).")
    else:
        st.info("💡 Modo Local: O rascunho será injetado diretamente no CapCut.")

tab1, tab2 = st.tabs(["🚀 Produção", "📂 Indexar Biblioteca"])

with tab2:
    st.header("Sincronização Drive -> Supabase")
    if st.button("🔍 Iniciar Varredura e Indexação"):
        drive_service = get_drive_service()
        supabase = get_supabase_client()
        if not drive_service: st.error("Falha na autenticação do Google Drive.")
        else:
            with st.status("Indexando vídeos...", expanded=True) as status:
                query = f"'{FOLDER_ID}' in parents and trashed = false and mimeType contains 'video/'"
                results = drive_service.files().list(q=query, pageSize=100, fields="nextPageToken, files(id, name, webViewLink)").execute()
                items = results.get('files', [])
                for item in items:
                    st.write(f"Processando: {item['name']}")
                    existing = supabase.table("video_library").select("file_id, tags").eq("file_id", item['id']).execute()
                    if not existing.data or not existing.data[0].get('tags'):
                        basic_tags = [t.lower() for t in re.split(r'[-_\s]+', os.path.splitext(item['name'])[0]) if len(t) > 2]
                        supabase.table("video_library").upsert({"file_id": item['id'], "file_name": item['name'], "drive_link": item['webViewLink'], "tags": list(set(basic_tags))}).execute()
                        st.write(f"✅ {item['name']} indexado.")
                    else: st.write(f"⏭️ {item['name']} já está no banco.")
                status.update(label="Sincronização concluída!", state="complete", expanded=False)
            st.success("Biblioteca atualizada!")

with tab1:
    st.header("Novo Projeto de Montagem")
    col1, col2 = st.columns([1, 2])
    
    with col1:
        project_title = st.text_input("Título do Projeto", value="Nova Montagem")
        audio_file = st.file_uploader("Upload de Áudio (.mp3/wav)", type=['mp3', 'wav'])
        script_text = st.text_area("Roteiro Original", height=300)
        
    if audio_file and script_text:
        file_ext = audio_file.name.split('.')[-1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_ext}") as tmp_audio:
            tmp_audio.write(audio_file.getvalue())
            tmp_audio_path = tmp_audio.name
            
        try:
            if file_ext == 'mp3': duration = MP3(tmp_audio_path).info.length
            else: duration = WAVE(tmp_audio_path).info.length
        except: duration = 0
            
        st.success(f"Duração Detectada: {duration:.2f}s")
        
        production_label = "📦 Gerar Kit (.zip)" if IS_CLOUD else "🏗️ Injetar no CapCut"
        
        if st.button(production_label):
            if not IS_CLOUD and not os.path.exists(drafts_path):
                st.error("ERRO: Caminho de rascunhos inválido. Verifique o sidebar."); st.stop()

            supabase = get_supabase_client()
            drive_service = get_drive_service()
            if not drive_service: st.error("Erro Google Drive"); st.stop()
                
            with st.status("Processando montagem..." if IS_CLOUD else "Injetando rascunho...", expanded=True) as status:
                with tempfile.TemporaryDirectory() as temp_dir:
                    # In Cloud, we use a temp dir. In Local, we use the CapCut folder directly or a managed subfolder.
                    if IS_CLOUD:
                        work_path = temp_dir
                        clips_dir = os.path.join(work_path, "videos")
                        os.makedirs(clips_dir)
                    else:
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
                        folder_name = f"SA_{timestamp}_{project_title.replace(' ', '_')}"
                        work_path = os.path.join(drafts_path, folder_name)
                        os.makedirs(work_path, exist_ok=True)
                        clips_dir = work_path

                    # Save audio
                    audio_final_name = f"audio.{file_ext}"
                    audio_dest = os.path.join(work_path, audio_final_name)
                    with open(audio_dest, 'wb') as f: f.write(audio_file.getvalue())
                    
                    num_segments = int(duration // 10) + (1 if duration % 10 > 0 else 0)
                    sentences = re.split(r'[.!?]+', script_text)
                    sentences = [s.strip() for s in sentences if s.strip()] or ["..."]
                    sentences_per_block = max(1, len(sentences) // num_segments)
                    
                    selected_clips_data = []
                    progress_bar = st.progress(0)
                    
                    for i in range(num_segments):
                        block_text = " ".join(sentences[i*sentences_per_block : (i+1)*sentences_per_block])
                        res = supabase.table("video_library").select("*").order("last_used_at", desc=False, nullsfirst=True).execute()
                        videos = res.data
                        tags_needed = [w.lower() for w in re.findall(r'\w{5,}', block_text)]
                        
                        best_video = next((v for v in videos if any(t.lower() in [vt.lower() for vt in v.get('tags', [])] for t in tags_needed)), videos[0] if videos else None)
                        
                        if best_video:
                            dest_path = os.path.join(clips_dir, best_video['file_name'])
                            if not os.path.exists(dest_path):
                                download_file_from_drive(drive_service, best_video['file_id'], dest_path)
                            
                            try:
                                cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', dest_path]
                                v_dur = float(subprocess.check_output(cmd).decode().strip())
                            except: v_dur = 10.0
                            
                            selected_clips_data.append({
                                "id": best_video['file_id'], "name": best_video['file_name'],
                                "local_path": dest_path, "duration": min(10.0, v_dur)
                            })
                        progress_bar.progress((i + 1) / num_segments)

                    if IS_CLOUD:
                        # XML Generation for Cloud Download
                        xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<xmeml version="5">
    <project><name>{project_title}</name><children><sequence><name>Montagem</name><duration>{int(duration*30)}</duration><rate><timebase>30</timebase></rate><media><video><track>"""
                        for i, clip in enumerate(selected_clips_data):
                            start = i * 10 * 30
                            frames = int(clip['duration'] * 30)
                            xml_content += f"""<clipitem id="c-{i}"><name>{clip['name']}</name><duration>{frames}</duration><rate><timebase>30</timebase></rate><start>{start}</start><end>{start+frames}</end><in>0</in><out>{frames}</out><file id="f-{i}"><name>{clip['name']}</name><pathurl>file://./videos/{clip['name']}</pathurl></file></clipitem>"""
                        xml_content += f"""</track></video><audio><track><clipitem id="a-1"><name>{audio_final_name}</name><duration>{int(duration*30)}</duration><rate><timebase>30</timebase></rate><start>0</start><end>{int(duration*30)}</end><in>0</in><out>{int(duration*30)}</out><file id="af-1"><name>{audio_final_name}</name><pathurl>file://./{audio_final_name}</pathurl></file></clipitem></track></audio></media></sequence></children></project></xmeml>"""
                        
                        zip_buffer = io.BytesIO()
                        with zipfile.ZipFile(zip_buffer, "w") as zf:
                            zf.writestr("montagem.xml", xml_content)
                            zf.write(audio_dest, audio_final_name)
                            for clip in selected_clips_data:
                                zf.write(clip['local_path'], f"videos/{clip['name']}")
                        st.session_state['zip_data'] = zip_buffer.getvalue()
                    else:
                        # Native Injection Files
                        draft_id = str(uuid.uuid4()).upper()
                        with open(os.path.join(work_path, "draft_meta.info"), "w", encoding="utf-8") as f:
                            json.dump(generate_draft_meta(project_title, draft_id, drafts_path), f, indent=4)
                        with open(os.path.join(work_path, "draft_content.json"), "w", encoding="utf-8") as f:
                            json.dump(generate_draft_content(project_title, selected_clips_data, {"local_path": audio_dest}, duration), f, indent=4)
                    
                    st.session_state['last_clip_ids'] = [c['id'] for c in selected_clips_data]
                    status.update(label="Kit Pronto!" if IS_CLOUD else "Injeção Concluída!", state="complete")
            
            if IS_CLOUD:
                st.download_button("📥 Baixar Kit de Edição (.zip)", st.session_state['zip_data'], file_name=f"{project_title}.zip")
            else:
                st.success(f"✅ Projeto '{project_title}' injetado no CapCut!")

    if 'last_clip_ids' in st.session_state:
        st.divider()
        if st.button("✅ Confirmar Uso de Clipes"):
            supabase = get_supabase_client(); now = datetime.now().isoformat()
            for vid in st.session_state['last_clip_ids']:
                supabase.table("video_library").update({"last_used_at": now}).eq("file_id", vid).execute()
            st.success("Registros atualizados!")
            del st.session_state['last_clip_ids']
