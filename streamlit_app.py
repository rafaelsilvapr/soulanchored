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
    OPENAI_API_KEY = st.secrets.get("OPENAI_API_KEY")
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
                st.sidebar.success(f"IA Gemini Ativa: {selected_model}")
            else:
                gemini_model = None
        except Exception as e:
            st.sidebar.error(f"Erro Gemini: {e}")
            gemini_model = None
    else:
        gemini_model = None

    # Setup OpenAI
    if OPENAI_API_KEY:
        from openai import OpenAI
        import base64
        client_openai = OpenAI(api_key=OPENAI_API_KEY)
        st.sidebar.success("IA OpenAI Ativa: gpt-4o")
    else:
        client_openai = None

    # Remove cache to ensure fresh secrets are used after user updates them
    def get_supabase_client():
        return create_client(SUPABASE_URL, SUPABASE_KEY)

    # --- Utility Diagnostics ---
    def show_db_diagnostics():
        try:
            temp_supabase = get_supabase_client()
            # Changed 'id' to 'file_id' to match schema
            count_res = temp_supabase.table("video_library").select("file_id", count="exact").limit(1).execute()
            st.sidebar.info(f"💾 BD Conectado: {SUPABASE_URL[:15]}...")
            st.sidebar.info(f"📊 Arquivos no Banco: {count_res.count if count_res.count is not None else 0}")
        except Exception as e:
            st.sidebar.error(f"❌ Erro de Conexão BD: {e}")

    # Call diagnostics
    show_db_diagnostics()

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
            with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as tmp_video:
                request = service.files().get_media(fileId=file_id)
                downloader = MediaIoBaseDownload(tmp_video, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
                tmp_video_path = tmp_video.name
            
            # Try multiple timestamps to find a valid frame
            for ts in ['00:00:02', '00:00:00', '00:00:05']:
                cmd = ['ffmpeg', '-y', '-ss', ts, '-i', tmp_video_path, '-vframes', '1', output_path]
                res = subprocess.run(cmd, capture_output=True)
                if res.returncode == 0:
                    os.unlink(tmp_video_path)
                    return True
            
            os.unlink(tmp_video_path)
            return False
        except Exception as e:
            st.error(f"Erro ao extrair quadro: {e}")
            return False

    def encode_image(image_path):
        import base64
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')

    def analyze_vision(image_path, engine="Gemini", retries=1):
        prompt = """
        Analise este vídeo para um sistema de montagem de vídeos de fé.
        Identifique: 1. Ação Principal, 2. Emoção Predominante, 3. Descrição Visual.
        Retorne APENAS JSON: {"acao": "...", "emocao": "...", "descricao": "..."}
        """
        
        for attempt in range(retries + 1):
            try:
                if engine == "OpenAI" and client_openai:
                    time.sleep(1)
                    base64_image = encode_image(image_path)
                    response = client_openai.chat.completions.create(
                        model="gpt-4o",
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": prompt},
                                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                                ],
                            }
                        ],
                        response_format={ "type": "json_object" }
                    )
                    content = response.choices[0].message.content
                    refusal = getattr(response.choices[0].message, 'refusal', None)
                    
                    if refusal:
                        raise Exception(f"OpenAI recusou por política de segurança: {refusal}")
                    if not content:
                        raise Exception("OpenAI retornou conteúdo vazio.")
                    return json.loads(content)
                
                elif engine == "Gemini" and gemini_model:
                    img = Image.open(image_path)
                    response = gemini_model.generate_content([prompt, img])
                    
                    # Handle Gemini Safety Blocking
                    if not response.candidates or not response.candidates[0].content.parts:
                        raise Exception("Gemini bloqueou a imagem por motivos de segurança (Safety Filter).")
                        
                    if not response.text:
                        raise Exception("Gemini não conseguiu gerar texto para esta imagem.")
                        
                    json_match = re.search(r'\{.*\}', response.text, re.DOTALL)
                    if not json_match:
                        raise Exception(f"Gemini enviou formato inválido.")
                    return json.loads(json_match.group())
                
                else:
                    raise Exception(f"Motor {engine} não configurado ou chave ausente.")

            except Exception as e:
                if "429" in str(e):
                    if attempt < retries:
                        st.warning(f"⏳ Limite atingido no {engine}. Aguardando 60s... ({attempt+1}/{retries})")
                        time.sleep(60)
                        continue
                if attempt == retries:
                    raise e # Re-raise at the last attempt so the sync loop catches it
        return {}

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
        
        # PERSISTENT ERRORS DISPLAY
        if "sync_errors" in st.session_state and st.session_state.sync_errors:
            with st.expander("📉 Relatório da Última Sincronização (FALHAS)", expanded=True):
                st.table(st.session_state.sync_errors)
                if st.button("Limpar Relatório"):
                    st.session_state.sync_errors = []
                    st.rerun()

        col_m1, col_m2 = st.columns([1, 2])
        with col_m1:
            vision_engine = st.radio("Motor de Visão (IA)", ["Gemini", "OpenAI"], help="Se o Gemini atingir o limite de cota, use o OpenAI (GPT-4o).")
        
        col_btn1, col_btn2 = st.columns([1, 1])
        with col_btn1:
            if st.button("🔄 Sincronizar e Atualizar Biblioteca", use_container_width=True):
                st.session_state.sync_errors = [] # Reset on new run
                service = get_drive_service()
                if service:
                    with st.status("🔍 Sincronizando com Google Drive...", expanded=True) as status:
                        # 1. Get Drive Files with Pagination
                        drive_files = []
                        page_token = None
                        while True:
                            query = f"'{FOLDER_ID}' in parents and trashed = false and mimeType contains 'video/'"
                            results = service.files().list(q=query, fields="nextPageToken, files(id, name, webViewLink)", pageToken=page_token).execute()
                            drive_files.extend(results.get('files', []))
                            page_token = results.get('nextPageToken')
                            if not page_token: break
                        
                        # 2. Get Supabase Files
                        db_files = supabase.table("video_library").select("*").execute().data or []
                        db_ids = {f['file_id'] for f in db_files}
                        
                        # Identify Groups
                        group_1 = [f for f in drive_files if f['id'] not in db_ids]
                        
                        # IMPROVED FILTER: Handle the 'None' strings seen in the screenshot
                        group_2 = [f for f in db_files if not f.get('acao') or f.get('acao') == 'None' or not f.get('emocao') or f.get('emocao') == 'None']
                        
                        total = len(group_1) + len(group_2)
                        st.write(f"📊 **Resumo da Varredura:** ({vision_engine})")
                        st.write(f"- Arquivos no Drive: {len(drive_files)}")
                        st.write(f"- Arquivos no Banco: {len(db_files)}")
                        st.write(f"- 🆕 Novos para indexar (Grupo 1): {len(group_1)}")
                        st.write(f"- 🆙 Para upgrade de IA (Grupo 2): {len(group_2)}")

                        if total == 0:
                            st.info(f"Biblioteca já está 100% atualizada com metadados de {vision_engine}.")
                        else:
                            # Sequential naming help
                            existing_names = [f['file_name'] for f in db_files if f['file_name'] and f['file_name'].split('.')[0].isdigit()]
                            last_num = max([int(n.split('.')[0]) for n in existing_names]) if existing_names else 0
                            
                            st.write(f"🚀 Iniciando processamento de {total} itens via {vision_engine}...")
                            progress_bar = st.progress(0)
                            idx = 0
                            failed_items = []
                            consecutive_errors = 0
                            
                            # Process Group 1 (New)
                            for f in group_1:
                                idx += 1
                                last_num += 1
                                new_name = f"{last_num:04d}.mp4"
                                try:
                                    st.write(f"🆕 Indexando [{idx}/{total}]: {f['name']} -> {new_name}")
                                    service.files().update(fileId=f['id'], body={'name': new_name}).execute()
                                    
                                    with tempfile.NamedTemporaryFile(suffix='.jpg') as tmp_img:
                                        if extract_frame(service, f['id'], tmp_img.name):
                                            meta = analyze_vision(tmp_img.name, engine=vision_engine)
                                            if meta:
                                                data = {
                                                    "file_id": f['id'], "file_name": new_name, "drive_link": f['webViewLink'],
                                                    "acao": meta.get('acao'), "emocao": meta.get('emocao'), "descricao": meta.get('descricao'),
                                                    "tags": [meta.get('acao'), meta.get('emocao')]
                                                }
                                                supabase.table("video_library").upsert(data).execute()
                                                consecutive_errors = 0
                                                time.sleep(3 if vision_engine == "OpenAI" else 10) # Pacing
                                            else:
                                                raise Exception("IA recusou ou enviou resposta vazia (Filtro de Segurança?)")
                                        else:
                                            raise Exception("FFmpeg: Arquivo pode estar corrompido ou é muito curto.")
                                except Exception as e:
                                    failed_items.append({"file": f['name'], "error": str(e)})
                                    st.warning(f"⚠️ Falha em {f['name']}: {e}")
                                
                                progress_bar.progress(idx / total)
                                if len(failed_items) >= 5:
                                    st.error("🚨 Limite de 5 falhas atingido. O processo foi interrompido para economizar seus tokens e permitir revisão.")
                                    break

                            # Process Group 2 (Upgrade)
                            for f in group_2:
                                if len(failed_items) >= 5: break
                                idx += 1
                                try:
                                    st.write(f"🆙 Fazendo Upgrade [{idx}/{total}]: {f['file_name']} ({vision_engine})")
                                    with tempfile.NamedTemporaryFile(suffix='.jpg') as tmp_img:
                                        if extract_frame(service, f['file_id'], tmp_img.name):
                                            meta = analyze_vision(tmp_img.name, engine=vision_engine)
                                            if meta:
                                                data = {
                                                    "acao": meta.get('acao'), "emocao": meta.get('emocao'), "descricao": meta.get('descricao'),
                                                    "tags": list(set((f.get('tags') or []) + [meta.get('acao'), meta.get('emocao')]))
                                                }
                                                supabase.table("video_library").update(data).eq("file_id", f['file_id']).execute()
                                                time.sleep(3 if vision_engine == "OpenAI" else 10) # Pacing
                                            else:
                                                raise Exception("IA recusou ou enviou resposta vazia")
                                        else:
                                            raise Exception("FFmpeg: Falha ao ler vídeo")
                                except Exception as e:
                                    failed_items.append({"file": f['file_name'], "error": str(e)})
                                    st.warning(f"⚠️ Falha em {f['file_name']}: {e}")
                                progress_bar.progress(idx / total)
                            
                            st.session_state.sync_errors = failed_items
                            if failed_items:
                                st.error(f"Sincronização Finalizada com {len(failed_items)} falhas.")
                            else:
                                st.success(f"✅ Sincronização Finalizada com Sucesso!")

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
                    # Filter for indexed videos only
                    raw_videos = supabase.table("video_library").select("*").order("last_used_at", desc=False, nullsfirst=True).execute().data or []
                    all_videos = [v for v in raw_videos if v.get('acao') and v.get('acao') != 'None' and v.get('emocao') and v.get('emocao') != 'None']
                    
                    if not all_videos:
                        st.error("⚠️ NENHUM VÍDEO INDEXADO ENCONTRADO. Por favor, sincronize a biblioteca primeiro.")
                        return
                    
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
