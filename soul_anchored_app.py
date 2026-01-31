import os
import re
import io
import time
import tempfile
import subprocess
import zipfile
import streamlit as st
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload
from supabase import create_client, Client
from mutagen.mp3 import MP3
from mutagen.wav import WAV
from PIL import Image
import google.generativeai as genai

# Load environment variables
load_dotenv()

# Configuration
# On Streamlit Cloud, use st.secrets. On local, use os.getenv
SUPABASE_URL = st.secrets.get("SUPABASE_URL") or os.getenv("SUPABASE_URL")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY") or os.getenv("SUPABASE_KEY")
GOOGLE_API_KEY = st.secrets.get("GOOGLE_API_KEY") or os.getenv("GOOGLE_API_KEY")
FOLDER_ID = '15xna7XFA7W3liDawGjbHqpF7o4_nmo1e'

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
    # On Streamlit Cloud, we might need a different auth flow or a pre-stored token in secrets
    # For now, we maintain the local token.json check
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists('credentials.json') and not st.secrets.get("GOOGLE_CREDENTIALS"):
                st.error("Error: Google credentials not found.")
                return None
            
            if os.path.exists('credentials.json'):
                flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            else:
                import json
                creds_info = json.loads(st.secrets["GOOGLE_CREDENTIALS"])
                flow = InstalledAppFlow.from_client_config(creds_info, SCOPES)
                
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return build('drive', 'v3', credentials=creds)

def download_file_from_drive(service, file_id, destination):
    request = service.files().get_media(fileId=file_id)
    fh = io.FileIO(destination, 'wb')
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    return True

# --- UI Layout ---
st.set_page_config(page_title="Soul Anchored Montage App", page_icon="🎬", layout="wide")

# Custom CSS for Premium Look
st.markdown("""
    <style>
    .stApp { background: linear-gradient(135deg, #0a0b10 0%, #1a1b25 100%); color: #e0e0e0; }
    [data-testid="stSidebar"] { background-color: rgba(255, 255, 255, 0.03); backdrop-filter: blur(10px); }
    h1, h2, h3 { font-family: 'Outfit', sans-serif; background: linear-gradient(to right, #ffffff, #7000ff); -webkit-background-clip: text; -webkit-text-fill-color: transparent; font-weight: 800; }
    .stButton>button { background: linear-gradient(135deg, #7000ff 0%, #00d2ff 100%); color: white; border-radius: 50px; font-weight: 600; box-shadow: 0 4px 15px rgba(112, 0, 255, 0.3); }
    </style>
    """, unsafe_allow_html=True)

st.title("Soul Anchored Montage App")
st.subheader("Cloud Edition ☁️")

with st.sidebar:
    st.header("⚙️ Status")
    if SUPABASE_URL and SUPABASE_KEY:
        st.success("✅ Supabase Conectado")
    else:
        st.error("❌ Supabase Não Configurado")
    
    st.divider()
    st.info("💡 Este app gera um arquivo .ZIP com todos os vídeos, o áudio e o XML prontos para o CapCut.")

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
        audio_file = st.file_uploader("Upload de Áudio (.mp3/wav)", type=['mp3', 'wav'])
        script_text = st.text_area("Roteiro Original", height=300)
        
    if audio_file and script_text:
        file_ext = audio_file.name.split('.')[-1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_ext}") as tmp_audio:
            tmp_audio.write(audio_file.getvalue())
            tmp_audio_path = tmp_audio.name
            
        try:
            if file_ext == 'mp3': duration = MP3(tmp_audio_path).info.length
            else: duration = WAV(tmp_audio_path).info.length
        except: duration = 0
            
        st.success(f"Duração: {duration:.2f}s")
        
        if st.button("📦 Gerar Kit de Edição (.zip)"):
            supabase = get_supabase_client()
            drive_service = get_drive_service()
            if not drive_service: st.error("Erro Google Drive"); st.stop()
                
            num_segments = int(duration // 10) + (1 if duration % 10 > 0 else 0)
            
            with st.status("Preparando Kit...", expanded=True) as status:
                with tempfile.TemporaryDirectory() as temp_dir:
                    clips_dir = os.path.join(temp_dir, "videos")
                    os.makedirs(clips_dir)
                    
                    # Copy audio to kit
                    audio_final_name = f"audio.{file_ext}"
                    audio_dest = os.path.join(temp_dir, audio_final_name)
                    with open(audio_dest, 'wb') as f: f.write(audio_file.getvalue())
                    
                    sentences = re.split(r'[.!?]+', script_text)
                    sentences = [s.strip() for s in sentences if s.strip()] or ["..."]
                    sentences_per_block = max(1, len(sentences) // num_segments)
                    
                    selected_clips = []
                    progress_bar = st.progress(0)
                    
                    for i in range(num_segments):
                        block_start = i * 10
                        block_text = " ".join(sentences[i*sentences_per_block : (i+1)*sentences_per_block])
                        
                        res = supabase.table("video_library").select("*").order("last_used_at", ascending=True, nulls_first=True).execute()
                        videos = res.data
                        tags_needed = [w.lower() for w in re.findall(r'\w{5,}', block_text)]
                        
                        best_video = None
                        for v in videos:
                            if any(t.lower() in [vt.lower() for vt in v.get('tags', [])] for t in tags_needed):
                                best_video = v; break
                        if not best_video and videos: best_video = videos[0]
                        
                        if best_video:
                            dest_path = os.path.join(clips_dir, best_video['file_name'])
                            if not os.path.exists(dest_path):
                                st.write(f"Baixando: {best_video['file_name']}")
                                download_file_from_drive(drive_service, best_video['file_id'], dest_path)
                            
                            try:
                                cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', dest_path]
                                v_dur = float(subprocess.check_output(cmd).decode().strip())
                            except: v_dur = 10.0
                            
                            clip_frames = min(300, int(v_dur * 30))
                            selected_clips.append({"id": best_video['file_id'], "name": best_video['file_name'], "start": block_start, "frames": clip_frames})
                        progress_bar.progress((i + 1) / num_segments)

                    # XML Generation (Relative Paths)
                    xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<xmeml version="5">
    <project>
        <name>Soul Anchored Kit</name>
        <children>
            <sequence>
                <name>Montagem Cloud</name>
                <duration>{int(duration * 30)}</duration>
                <rate><timebase>30</timebase></rate>
                <media>
                    <video>
                        <track>"""
                    for i, clip in enumerate(selected_clips):
                        start = clip['start'] * 30
                        xml_content += f"""
                            <clipitem id="clip-{i}">
                                <name>{clip['name']}</name>
                                <duration>{clip['frames']}</duration>
                                <rate><timebase>30</timebase></rate>
                                <start>{start}</start>
                                <end>{start + clip['frames']}</end>
                                <in>0</in>
                                <out>{clip['frames']}</out>
                                <file id="file-{i}"><name>{clip['name']}</name><pathurl>file://./videos/{clip['name']}</pathurl></file>
                            </clipitem>"""
                    xml_content += f"""</track>
                    </video>
                    <audio>
                        <track>
                            <clipitem id="audio-main">
                                <name>{audio_final_name}</name>
                                <duration>{int(duration * 30)}</duration>
                                <rate><timebase>30</timebase></rate>
                                <start>0</start>
                                <end>{int(duration * 30)}</end>
                                <in>0</in>
                                <out>{int(duration * 30)}</out>
                                <file id="audio-file"><name>{audio_final_name}</name><pathurl>file://./{audio_final_name}</pathurl></file>
                            </clipitem>
                        </track>
                    </audio>
                </media>
            </sequence>
        </children>
    </project>
</xmeml>"""
                    
                    # Create ZIP in memory
                    zip_buffer = io.BytesIO()
                    with zipfile.ZipFile(zip_buffer, "w") as zip_file:
                        zip_file.writestr("montagem.xml", xml_content)
                        zip_file.write(audio_dest, audio_final_name)
                        for clip in selected_clips:
                            zip_file.write(os.path.join(clips_dir, clip['name']), f"videos/{clip['name']}")
                    
                    st.session_state['zip_data'] = zip_buffer.getvalue()
                    st.session_state['last_clip_ids'] = [c['id'] for c in selected_clips]
                status.update(label="Kit Pronto!", state="complete")
            
            st.download_button("📥 Baixar Kit de Edição (.zip)", st.session_state['zip_data'], file_name="soul_anchored_kit.zip", mime="application/zip")

    if 'last_clip_ids' in st.session_state:
        st.divider()
        if st.button("✅ Confirmar Uso (Atualizar Supabase)"):
            supabase = get_supabase_client()
            now = datetime.now().isoformat()
            for vid in st.session_state['last_clip_ids']:
                supabase.table("video_library").update({"last_used_at": now}).eq("file_id", vid).execute()
            st.success("Registros atualizados!")
            del st.session_state['last_clip_ids']
