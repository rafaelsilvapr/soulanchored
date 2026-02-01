import sys
import traceback
import streamlit as st

# 1. Page Config MUST be first
st.set_page_config(page_title="Soul Anchored - Cérebro Editorial", page_icon="🧠", layout="wide")

try:
    import os
    import re
    import io
    import time
    import json
    import tempfile
    import subprocess
    import pandas as pd
    from datetime import datetime
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
    from google.auth.transport.requests import Request
    from supabase import create_client, Client
    import google.generativeai as genai
    from PIL import Image

    # --- UI Styling ---
    st.markdown("""
        <style>
        .stApp { background: linear-gradient(135deg, #07080c 0%, #11121d 100%); color: #e0e0e0; }
        h1, h2, h3 { font-family: 'Outfit', sans-serif; background: linear-gradient(to right, #00d2ff, #7000ff); -webkit-background-clip: text; -webkit-text-fill-color: transparent; font-weight: 800; }
        .stButton>button { background: linear-gradient(135deg, #7000ff 0%, #00d2ff 100%); color: white; border-radius: 8px; font-weight: 600; border: none; padding: 0.5rem 2rem; }
        .stTable { background-color: rgba(255,255,255,0.05); border-radius: 10px; }
        [data-testid="stMetricValue"] { color: #00d2ff; }
        .stProgress > div > div > div > div { background-image: linear-gradient(to right, #00d2ff, #7000ff); }
        </style>
        """, unsafe_allow_html=True)

    # --- Configuration ---
    if "SUPABASE_URL" not in st.secrets:
        st.error("❌ Erro: 'SUPABASE_URL' ausente nos Secrets.")
        st.stop()
        
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
    FOLDER_ID = st.secrets.get("FOLDER_ID", "15xna7XFA7W3liDawGjbHqpF7o4_nmo1e")

    # Setup Gemini
    if GOOGLE_API_KEY:
        try:
            genai.configure(api_key=GOOGLE_API_KEY)
            available_models = [m.name for m in genai.list_models() if "generateContent" in m.supported_generation_methods]
            preferred = ["models/gemini-1.5-flash", "models/gemini-2.0-flash", "models/gemini-1.5-pro"]
            selected_model = next((p for p in preferred if p in available_models), available_models[0] if available_models else None)
            if selected_model:
                gemini_model = genai.GenerativeModel(selected_model)
                st.sidebar.success(f"IA Ativa: {selected_model}")
            else:
                st.error("Nenhum modelo compatível encontrado.")
                gemini_model = None
        except Exception as e:
            st.error(f"Erro ao configurar Gemini: {e}")
            gemini_model = None
    else:
        gemini_model = None

    @st.cache_resource
    def get_supabase_client():
        return create_client(SUPABASE_URL, SUPABASE_KEY)

    # --- Google Drive Integration ---
    def get_drive_service():
        if "GOOGLE_TOKEN" not in st.secrets:
            # Fallback for local testing
            token_path = 'token.json'
            if os.path.exists(token_path):
                creds = Credentials.from_authorized_user_file(token_path)
                return build('drive', 'v3', credentials=creds)
            st.error("❌ GOOGLE_TOKEN não encontrado nos Secrets.")
            return None
        
        token_info = st.secrets["GOOGLE_TOKEN"]
        if isinstance(token_info, str): token_info = json.loads(token_info)
        creds = Credentials.from_authorized_user_info(token_info)
        
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        
        return build('drive', 'v3', credentials=creds)

    def extract_frame(service, file_id, output_path):
        try:
            request = service.files().get_media(fileId=file_id)
            with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as tmp:
                downloader = MediaIoBaseDownload(tmp, request, chunksize=1024*1024*5)
                done = False
                while not done:
                    status, done = downloader.next_chunk()
                    break # Get first 5MB
                tmp_path = tmp.name
            
            cmd = ['ffmpeg', '-y', '-ss', '00:00:02', '-i', tmp_path, '-vframes', '1', output_path]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            os.remove(tmp_path)
            return True
        except Exception as e:
            st.error(f"Erro ao extrair quadro: {e}")
            return False

    def analyze_vision(image_path):
        if not gemini_model: return {}
        try:
            img = Image.open(image_path)
            prompt = """
            Analise este vídeo para um sistema de montagem de vídeos de fé.
            Identifique:
            1. Ação Principal (verbo e movimento)
            2. Emoção Predominante (sentimento da cena)
            3. Descrição Visual Curta (contexto)
            Retorne APENAS um JSON: {"acao": "...", "emocao": "...", "descricao": "..."}
            Priorize termos como: ajoelhar com humildade, olhar sereno para o horizonte, lágrimas de alívio, abraço fraterno.
            """
            response = gemini_model.generate_content([prompt, img])
            json_match = re.search(r'\{.*\}', response.text, re.DOTALL)
            return json.loads(json_match.group()) if json_match else {}
        except Exception: return {}

    def get_storyboard_from_gemini(audio_path, script_text):
        if not gemini_model: return None
        with st.status("🧠 IA Analisando Áudio e Roteiro...", expanded=True) as status:
            try:
                st.write("📤 Enviando narração...")
                audio_file = genai.upload_file(path=audio_path)
                while audio_file.state.name == "PROCESSING":
                    time.sleep(2)
                    audio_file = genai.get_file(audio_file.name)
                
                prompt = f"""
                Você é um Diretor de Montagem Sênior. Sua tarefa é analisar o áudio de narração e o roteiro.
                OBJETIVO: Sincronizar o roteiro em blocos de 10 segundos baseando-se no ritmo real de fala.
                ROTEIRO: {script_text}
                Retorne APENAS um JSON puro no formato:
                [{{"timestamp": "00:00", "script_fragment": "...", "visual_theme": "...", "emocao_alvo": "..."}}]
                """
                st.write("⚡ Sincronizando conteúdo...")
                response = gemini_model.generate_content([audio_file, prompt])
                json_match = re.search(r'\[.*\]', response.text, re.DOTALL)
                res = json.loads(json_match.group()) if json_match else None
                genai.delete_file(audio_file.name)
                return res
            except Exception as e:
                st.error(f"Erro na análise: {e}")
                return None

    # --- Main App Interface ---
    st.title("Soul Anchored Assembler")
    st.subheader("Editorial Brain v2.0 🧠🎙️")

    tab1, tab2 = st.tabs(["🚀 Produção de Roteiro", "📂 Biblioteca & Sincronia"])

    supabase = get_supabase_client()

    with tab2:
        st.header("Biblioteca de Vídeos")
        col_btn1, col_btn2 = st.columns([1, 1])
        with col_btn1:
            if st.button("🔄 Sincronizar e Atualizar Biblioteca", use_container_width=True):
                service = get_drive_service()
                if service:
                    with st.status("🔍 Sincronizando com Google Drive...", expanded=True) as status:
                        # 1. Get Drive Files
                        query = f"'{FOLDER_ID}' in parents and trashed = false and mimeType contains 'video/'"
                        drive_files = service.files().list(q=query, fields="files(id, name, webViewLink)").execute().get('files', [])
                        
                        # 2. Get Supabase Files
                        db_files = supabase.table("video_library").select("*").execute().data or []
                        db_ids = {f['file_id'] for f in db_files}
                        
                        # Sequential naming help
                        existing_names = [f['file_name'] for f in db_files if f['file_name'].split('.')[0].isdigit()]
                        last_num = max([int(n.split('.')[0]) for n in existing_names]) if existing_names else 0
                        
                        # Identify Groups
                        group_1 = [f for f in drive_files if f['id'] not in db_ids]
                        group_2 = [f for f in db_files if not f.get('acao') or not f.get('emocao')]
                        
                        total = len(group_1) + len(group_2)
                        if total == 0:
                            st.info("Biblioteca já está atualizada.")
                        else:
                            progress_bar = st.progress(0)
                            idx = 0
                            
                            # Process Group 1 (New)
                            for f in group_1:
                                idx += 1
                                last_num += 1
                                new_name = f"{last_num:04d}.mp4"
                                st.write(f"🆕 Indexando [{idx}/{total}]: {f['name']} -> {new_name}")
                                
                                # Rename in Drive
                                service.files().update(fileId=f['id'], body={'name': new_name}).execute()
                                
                                # Analyze Vision
                                with tempfile.NamedTemporaryFile(suffix='.jpg') as tmp_img:
                                    if extract_frame(service, f['id'], tmp_img.name):
                                        meta = analyze_vision(tmp_img.name)
                                        data = {
                                            "file_id": f['id'], "file_name": new_name, "drive_link": f['webViewLink'],
                                            "acao": meta.get('acao'), "emocao": meta.get('emocao'), "descricao": meta.get('descricao'),
                                            "tags": [meta.get('acao'), meta.get('emocao')]
                                        }
                                        supabase.table("video_library").upsert(data).execute()
                                
                                progress_bar.progress(idx / total)

                            # Process Group 2 (Upgrade)
                            for f in group_2:
                                idx += 1
                                st.write(f"🆙 Fazendo Upgrade [{idx}/{total}]: {f['file_name']}")
                                with tempfile.NamedTemporaryFile(suffix='.jpg') as tmp_img:
                                    if extract_frame(service, f['file_id'], tmp_img.name):
                                        meta = analyze_vision(tmp_img.name)
                                        data = {
                                            "acao": meta.get('acao'), "emocao": meta.get('emocao'), "descricao": meta.get('descricao'),
                                            "tags": list(set((f.get('tags') or []) + [meta.get('acao'), meta.get('emocao')]))
                                        }
                                        supabase.table("video_library").update(data).eq("file_id", f['file_id']).execute()
                                progress_bar.progress(idx / total)
                            
                            st.success("✅ Sincronização Concluída!")
                            st.rerun()

        res = supabase.table("video_library").select("*").order("file_name", desc=False).execute()
        if res.data:
            df = pd.DataFrame(res.data)
            # Only show columns that exist in the DB
            display_cols = ["file_name", "acao", "emocao", "descricao", "last_used_at"]
            available_cols = [c for c in display_cols if c in df.columns]
            st.dataframe(df[available_cols], use_container_width=True)

    with tab1:
        col1, col2 = st.columns([1, 1])
        with col1:
            project_title = st.text_input("Título do Projeto", value="Nova Montagem")
            script_text = st.text_area("Roteiro Original", height=250, placeholder="Cole o roteiro...")
        with col2:
            audio_in = st.file_uploader("Upload de Áudio", type=['mp3', 'wav'])
            if audio_in: st.audio(audio_in)

        if st.button("🧠 Gerar Storyboard Semântico"):
            if not script_text or not audio_in:
                st.warning("Forneça o roteiro e o áudio.")
            else:
                with tempfile.NamedTemporaryFile(delete=False, suffix=f".{audio_in.name.split('.')[-1]}") as tmp:
                    tmp.write(audio_in.getvalue()); tmp_path = tmp.name
                storyboard = get_storyboard_from_gemini(tmp_path, script_text)
                os.remove(tmp_path)
                
                if storyboard:
                    all_videos = supabase.table("video_library").select("*").order("last_used_at", desc=False, nullsfirst=True).execute().data or []
                    recent_ids = set([v['file_id'] for v in sorted(all_videos, key=lambda x: x.get('last_used_at') or '', reverse=True)[:10]])
                    
                    final_plan = []
                    session_used = []
                    for block in storyboard:
                        target_emocao = block.get('emocao_alvo', '').lower()
                        visual_theme = block.get('visual_theme', '').lower()
                        
                        # Matching priority: 1. Emotion/Action, 2. Visual Theme, 3. Oldest Used
                        candidates = [v for v in all_videos if v['file_id'] not in recent_ids and v['file_id'] not in session_used]
                        best = None
                        
                        # High priority match
                        for v in candidates:
                            v_meta = f"{v.get('acao','')} {v.get('emocao','')} {v.get('descricao','')}".lower()
                            if target_emocao in v_meta or any(word in v_meta for word in visual_theme.split()):
                                best = v; break
                        
                        if not best:
                            best = candidates[0] if candidates else (all_videos[0] if all_videos else None)
                        
                        if best:
                            final_plan.append({
                                "Tempo": block['timestamp'], "Texto": block['script_fragment'],
                                "Sugestão Visual": block['visual_theme'], "ARQUIVO": f"🎬 {best['file_name']}",
                                "file_id": best['file_id'], "file_name": best['file_name'], "meta": f"{best.get('acao','')} | {best.get('emocao','')}"
                            })
                            session_used.append(best['file_id'])
                    
                    st.session_state['last_storyboard'] = final_plan
                    st.success("Storyboard gerado!")

    if 'last_storyboard' in st.session_state:
        sb = st.session_state['last_storyboard']
        st.divider()
        st.header("📋 Tabela de Montagem Técnico")
        st.table(pd.DataFrame(sb)[["Tempo", "Texto", "Sugestão Visual", "meta", "ARQUIVO"]])
        
        c1, c2 = st.columns(2)
        with c1:
            if st.button("✅ Confirmar Montagem e Registrar", use_container_width=True):
                now = datetime.now().isoformat()
                for item in sb:
                    supabase.table("video_library").update({"last_used_at": now}).eq("file_id", item['file_id']).execute()
                st.balloons(); st.success("Uso registrado!"); del st.session_state['last_storyboard']; st.rerun()
        with c2:
            txt = f"ROTEIRO TÉCNICO: {project_title}\n" + "="*30 + "\n"
            for item in sb: txt += f"[{item['Tempo']}] -> {item['file_name']} ({item['meta']})\n"
            st.download_button("📲 Baixar roteiro para WhatsApp", txt, file_name="roteiro.txt", use_container_width=True)

except Exception as e:
    st.error("❌ ERRO CRÍTICO"); st.exception(e); st.code(traceback.format_exc())
