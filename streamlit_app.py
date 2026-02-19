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
        /* Base App Theme - Light Mode */
        .stApp { background-color: #fcfcfc; color: #1e1e1e; }
        
        /* High Contrast Headers */
        h1, h2, h3 { 
            font-family: 'Outfit', sans-serif; 
            background: linear-gradient(to right, #005a8d, #3c008d); 
            -webkit-background-clip: text; 
            -webkit-text-fill-color: transparent; 
            font-weight: 800; 
            text-shadow: 0px 1px 1px rgba(0,0,0,0.05);
        }
        
        /* Readable Text Adjustments */
        p, label, span, div { color: #1e1e1e !important; font-weight: 450; }
        .stMarkdown { line-height: 1.6; }
        
        /* Sidebar Contrast */
        [data-testid="stSidebar"] { background-color: #f1f3f6 !important; border-right: 1px solid #e0e0e0; }
        
        /* Buttons - Premium Gradient */
        .stButton>button { 
            background: linear-gradient(135deg, #005a8d 0%, #3c008d 100%); 
            color: white !important; 
            border-radius: 8px; 
            font-weight: 600; 
            border: none; 
            padding: 0.6rem 2.2rem; 
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            transition: all 0.3s ease;
        }
        .stButton>button:hover { 
            transform: translateY(-2px); 
            box-shadow: 0 6px 12px rgba(0,0,0,0.15); 
            opacity: 0.95;
        }
        
        /* Table and Dataframes */
        .stTable, [data-testid="stDataFrame"] { background-color: white; border: 1px solid #e0e0e0; border-radius: 10px; }
        
        /* Metrics */
        [data-testid="stMetricValue"] { color: #005a8d !important; font-weight: 700; }
        
        /* Progress Bar */
        .stProgress > div > div > div > div { background-image: linear-gradient(to right, #005a8d, #3c008d); }
        
        /* Tabs Styling */
        .stTabs [data-baseweb="tab-list"] { gap: 8px; }
        .stTabs [data-baseweb="tab"] { 
            background-color: #f1f3f6; 
            border-radius: 8px 8px 0 0; 
            padding: 10px 20px; 
            color: #4a4a4a !important;
            border: 1px solid transparent;
        }
        .stTabs [aria-selected="true"] { 
            background-color: white !important; 
            color: #005a8d !important; 
            border: 1px solid #e0e0e0 !important;
            border-bottom: none !important;
            font-weight: bold !important;
        }
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

    def extract_frames(service, file_id, timestamps=['00:00:01', '00:00:04']):
        """Extracts multiple frames at given timestamps and returns a list of paths."""
        extracted_paths = []
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as tmp_video:
                request = service.files().get_media(fileId=file_id)
                downloader = MediaIoBaseDownload(tmp_video, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
                tmp_video_path = tmp_video.name
            
            for i, ts in enumerate(timestamps):
                output_path = f"{tmp_video_path}_frame_{i}.jpg"
                cmd = ['ffmpeg', '-y', '-ss', ts, '-i', tmp_video_path, '-vframes', '1', output_path]
                res = subprocess.run(cmd, capture_output=True)
                if res.returncode == 0:
                    extracted_paths.append(output_path)
            
            os.unlink(tmp_video_path)
            return extracted_paths
        except Exception as e:
            st.error(f"Erro ao extrair quadros: {e}")
            return []

    def encode_image(image_path):
        import base64
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')

    def analyze_vision(image_paths, engine="Gemini", retries=1):
        """Analyzes a sequence of images to describe action and emotion."""
        prompt = """
        Analise estas imagens que representam uma sequência de um vídeo de 5 segundos.
        IMAGE 1 é o início, IMAGE 2 é o fim.
        
        Descreva a AÇÃO LITERAL e o MOVIMENTO (ex: 'alguém sentando', 'carro passando', 'pessoa sorrindo').
        Identifique ELEMENTOS VISUAIS CONCRETOS.
        
        Retorne APENAS JSON: 
        {"acao": "descrição do movimento/ação detectada entre os frames", 
         "emocao": "vibe ou sentimento predominante", 
         "descricao": "resumo detalhado dos elementos visuais", 
         "elementos_visuais": ["lista de objetos/cenário"]}
        """
        
        for attempt in range(retries + 1):
            try:
                if engine == "OpenAI" and client_openai:
                    time.sleep(1)
                    content_list = [{"type": "text", "text": prompt}]
                    for path in image_paths:
                        base64_img = encode_image(path)
                        content_list.append({
                            "type": "image_url", 
                            "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"}
                        })

                    response = client_openai.chat.completions.create(
                        model="gpt-4o",
                        messages=[{"role": "user", "content": content_list}],
                        response_format={ "type": "json_object" }
                    )
                    content = response.choices[0].message.content
                    return json.loads(content)
                
                elif engine == "Gemini" and gemini_model:
                    input_list = [prompt]
                    for path in image_paths:
                        input_list.append(Image.open(path))
                        
                    response = gemini_model.generate_content(input_list)
                    
                    if not response.candidates or not response.candidates[0].content.parts:
                        raise Exception("Gemini bloqueou a imagem por motivos de segurança.")
                        
                    json_match = re.search(r'\{.*\}', response.text, re.DOTALL)
                    if not json_match:
                        try:
                            return json.loads(response.text.strip())
                        except:
                            raise Exception(f"Gemini enviou formato inválido.")
                    return json.loads(json_match.group())
                
                else:
                    raise Exception(f"Motor {engine} não configurado ou chave ausente.")

            except Exception as e:
                if "429" in str(e):
                    if attempt < retries:
                        st.warning(f"⏳ Limite atingido no {engine}. Aguardando 60s...")
                        time.sleep(60)
                        continue
                if attempt == retries:
                    raise e
        return {}

    def get_audio_duration(file_path):
        """Get duration of audio file in seconds using ffprobe."""
        try:
            cmd = [
                'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1', file_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                return float(result.stdout.strip())
        except Exception as e:
            st.error(f"Erro ao detectar duração do áudio: {e}")
        return None

    def get_semantic_storyboard(audio_path, script_text, engine="Gemini"):
        audio_duration = get_audio_duration(audio_path)
        duration_fmt = f"{int(audio_duration // 60):02d}:{int(audio_duration % 60):02d}" if audio_duration else "Desconhecida"
        
        with st.status(f"🧠 {engine} Analisando Conteúdo...", expanded=True) as status:
            try:
                prompt_base = f"""
                Você é um Diretor de Montagem de Elite.
                
                OBJETIVO: Alinhar o ROTEIRO ao ÁUDIO com precisão técnica.
                DURAÇÃO TOTAL DO ÁUDIO: {duration_fmt} ({audio_duration} segundos).
                
                INSTRUÇÕES CRÍTICAS:
                1. NÃO use blocos fixos de tempo. Divida o roteiro em frases ou parágrafos lógicos.
                2. Para cada bloco, IDENTIFIQUE o timestamp (MM:SS) exato em que a narração começa a dizer aquelas palavras.
                3. Descreva a IMAGEM LITERAL (visual_theme) que deve aparecer. Evite abstrações.
                4. O último bloco DEVE estar próximo ao final da duração total ({duration_fmt}).
                
                ROTEIRO: {script_text}
                
                Retorne APENAS JSON:
                {{ "storyboard": [
                    {{"timestamp": "00:00", "script_fragment": "...", "sugestao_visual_literal": "...", "elementos_chave": ["...", "..."], "emocao_alvo": "..."}},
                    ...
                ]}}
                """

                if engine == "Gemini" and gemini_model:
                    st.write("📤 Enviando narração para o Gemini (Sincronia por Áudio)...")
                    audio_file = genai.upload_file(path=audio_path)
                    while audio_file.state.name == "PROCESSING":
                        time.sleep(2)
                        audio_file = genai.get_file(audio_file.name)
                        
                    st.write("⚡ Sincronizando conteúdo no Gemini (Escuta Ativa)...")
                    # For Gemini, we add the audio to the prompt
                    response = gemini_model.generate_content([audio_file, f"Escute o áudio e alinhe o roteiro com precisão milimétrica. A duração total é {duration_fmt}. {prompt_base}"])
                    json_match = re.search(r'\{.*\}', response.text, re.DOTALL)
                    data = json.loads(json_match.group()) if json_match else {}
                    res = data.get('storyboard') if isinstance(data, dict) else data
                    genai.delete_file(audio_file.name)
                    return res if isinstance(res, list) else None

                elif engine == "OpenAI" and client_openai:
                    st.write(f"⚡ Gerando Storyboard no OpenAI (Distribuição Proporcional para {duration_fmt})...")
                    response = client_openai.chat.completions.create(
                        model="gpt-4o",
                        messages=[{"role": "user", "content": prompt_base}],
                        response_format={ "type": "json_object" }
                    )
                    content = response.choices[0].message.content
                    data = json.loads(content)
                    res = data.get('storyboard') if isinstance(data, dict) else data
                    return res if isinstance(res, list) else None

                else:
                    st.error(f"Motor {engine} não configurado.")
                    return None
            except Exception as e:
                st.error(f"Erro na análise ({engine}): {e}")
                return None

    # --- Main App Interface ---
    st.title("Soul Anchored Assembler")
    st.subheader("Editorial Brain v2.0 🧠🎙️")

    tab1, tab2, tab3 = st.tabs(["🚀 Produção de Roteiro", "📂 Biblioteca & Sincronia", "🔍 Busca & Descoberta"])

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
                            results = service.files().list(q=query, fields="nextPageToken, files(id, name, webViewLink, thumbnailLink)", pageToken=page_token).execute()
                            drive_files.extend(results.get('files', []))
                            page_token = results.get('nextPageToken')
                            if not page_token: break
                        
                        # 2. Get Supabase Files
                        db_files = supabase.table("video_library").select("*").execute().data or []
                        db_ids = {f['file_id'] for f in db_files}
                        
                        # Identify Groups
                        group_1 = [f for f in drive_files if f['id'] not in db_ids]
                        
                        # Upgrade IA (Group 2): Missing action/emotion
                        group_2 = [f for f in db_files if not f.get('acao') or f.get('acao') == 'None' or not f.get('emocao') or f.get('emocao') == 'None']
                        
                        # Update Thumbnails Only (Group 3): Has IA but missing thumbnail
                        group_3 = [f for f in db_files if f['file_id'] not in [x['file_id'] for x in group_2] and not f.get('thumbnail_link')]
                        
                        total = len(group_1) + len(group_2) + len(group_3)
                        st.write(f"📊 **Resumo da Varredura:** ({vision_engine})")
                        st.write(f"- Arquivos no Drive: {len(drive_files)}")
                        st.write(f"- Arquivos no Banco: {len(db_files)}")
                        st.write(f"- 🆕 Novos para indexar (Grupo 1): {len(group_1)}")
                        st.write(f"- 🆙 Para upgrade de IA (Grupo 2): {len(group_2)}")
                        st.write(f"- 🖼️ Para atualizar miniaturas (Grupo 3): {len(group_3)}")

                        if total == 0:
                            st.info(f"Biblioteca já está 100% atualizada com metadados de {vision_engine}.")
                        else:
                            # Map drive info for easy access (used for thumbnails)
                            drive_info_map = {f['id']: f for f in drive_files}
                            
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
                                    
                                    frame_paths = extract_frames(service, f['id'])
                                    if frame_paths:
                                        meta = analyze_vision(frame_paths, engine=vision_engine)
                                        if meta:
                                            # Use the first frame as the permanent thumbnail if drive link fails
                                            data = {
                                                "file_id": f['id'], "file_name": new_name, "drive_link": f['webViewLink'],
                                                "acao": meta.get('acao'), "emocao": meta.get('emocao'), "descricao": meta.get('descricao'),
                                                "tags": [meta.get('acao'), meta.get('emocao')],
                                                "thumbnail_link": f.get('thumbnailLink')
                                            }
                                            supabase.table("video_library").upsert(data).execute()
                                            consecutive_errors = 0
                                            time.sleep(1 if vision_engine == "OpenAI" else 2)
                                        else:
                                            raise Exception("IA recusou ou enviou resposta vazia")
                                        
                                        # Cleanup temp frames
                                        for p in frame_paths: 
                                            if os.path.exists(p): os.unlink(p)
                                    else:
                                        raise Exception("FFmpeg: Não foi possível extrair os quadros.")
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
                                    
                                    frame_paths = extract_frames(service, f['file_id'])
                                    if frame_paths:
                                        meta = analyze_vision(frame_paths, engine=vision_engine)
                                        if meta:
                                            # Fetch latest drive info to get thumbnailLink if missing
                                            drive_item = drive_info_map.get(f['file_id'])
                                            thumb = drive_item.get('thumbnailLink') if drive_item else None
                                            
                                            data = {
                                                "acao": meta.get('acao'), "emocao": meta.get('emocao'), "descricao": meta.get('descricao'),
                                                "tags": list(set((f.get('tags') or []) + [meta.get('acao'), meta.get('emocao')])),
                                                "thumbnail_link": thumb or f.get('thumbnail_link')
                                            }
                                            supabase.table("video_library").update(data).eq("file_id", f['file_id']).execute()
                                            time.sleep(1 if vision_engine == "OpenAI" else 2)
                                        else:
                                            raise Exception("IA recusou ou enviou resposta vazia")
                                        
                                        # Cleanup
                                        for p in frame_paths: 
                                            if os.path.exists(p): os.unlink(p)
                                    else:
                                        raise Exception("FFmpeg: Falha ao ler vídeo")
                                except Exception as e:
                                    failed_items.append({"file": f['file_name'], "error": str(e)})
                                    st.warning(f"⚠️ Falha em {f['file_name']}: {e}")
                                progress_bar.progress(idx / total)
                            
                            # Process Group 3 (Thumbnails Only)
                            for f in group_3:
                                idx += 1
                                try:
                                    st.write(f"🖼️ Atualizando Miniatura [{idx}/{total}]: {f['file_name']}")
                                    drive_item = drive_info_map.get(f['file_id'])
                                    if drive_item and drive_item.get('thumbnailLink'):
                                        supabase.table("video_library").update({"thumbnail_link": drive_item['thumbnailLink']}).eq("file_id", f['file_id']).execute()
                                    else:
                                        st.warning(f"⚠️ Drive não forneceu miniatura para {f['file_name']}")
                                except Exception as e:
                                    failed_items.append({"file": f['file_name'], "error": str(e)})
                                    st.warning(f"⚠️ Falha ao atualizar miniatura: {e}")
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
            story_engine = st.radio("Motor de Geração", ["Gemini", "OpenAI"], index=0, horizontal=True, help="Use OpenAI se o Gemini estiver fora de cota.")
            if audio_in: st.audio(audio_in)

        if st.button("🧠 Gerar Storyboard Semântico"):
            if not script_text or not audio_in:
                st.warning("Forneça o roteiro e o áudio.")
            else:
                with tempfile.NamedTemporaryFile(delete=False, suffix=f".{audio_in.name.split('.')[-1]}") as tmp:
                    tmp.write(audio_in.getvalue()); tmp_path = tmp.name
                storyboard = get_semantic_storyboard(tmp_path, script_text, engine=story_engine)
                os.remove(tmp_path)
                
                if storyboard:
                    # Filter for indexed videos only
                    raw_videos = supabase.table("video_library").select("*").order("last_used_at", desc=False, nullsfirst=True).execute().data or []
                    all_videos = [v for v in raw_videos if v.get('acao') and v.get('acao') != 'None' and v.get('emocao') and v.get('emocao') != 'None']
                    
                    if not all_videos:
                        st.error("⚠️ NENHUM VÍDEO INDEXADO ENCONTRADO. Por favor, sincronize a biblioteca primeiro.")
                        st.stop()
                    
                    recent_ids = set([v['file_id'] for v in sorted(all_videos, key=lambda x: x.get('last_used_at') or '', reverse=True)[:10]])
                    
                    final_plan = []
                    session_used = []
                    for block in storyboard:
                        target_emocao = block.get('emocao_alvo', '').lower()
                        sugestao_visual = block.get('sugestao_visual_literal', block.get('visual_theme', '')).lower()
                        elementos_chave = block.get('elementos_chave', [])
                        
                        # Matching priority: Score-based (Literal elements > Description > Emotion)
                        candidates = [v for v in all_videos if v['file_id'] not in recent_ids and v['file_id'] not in session_used]
                        best = None
                        best_score = -1

                        for v in candidates:
                            score = 0
                            v_acao = (v.get('acao') or '').lower()
                            v_desc = (v.get('descricao') or '').lower()
                            v_tags = (v.get('tags') or [])
                            if isinstance(v_tags, str): v_tags = [v_tags] 
                            v_tags = [str(t).lower() for t in v_tags]

                            # 1. Keyword match from 'elementos_chave'
                            for elem in elementos_chave:
                                elem = elem.lower()
                                if elem in v_acao or elem in v_desc: score += 5
                                if any(elem in t for t in v_tags): score += 3

                            # 2. Text match in description/action
                            if sugestao_visual in v_acao or sugestao_visual in v_desc: score += 10
                            
                            # 3. Emotion match
                            if target_emocao in str(v.get('emocao', '')).lower(): score += 1

                            if score > best_score:
                                best_score = score
                                best = v
                        
                        if not best:
                            best = candidates[0] if candidates else (all_videos[0] if all_videos else None)
                        
                        if best:
                            final_plan.append({
                                "Tempo": block['timestamp'], "Texto": block['script_fragment'],
                                "Sugestão Visual": block.get('sugestao_visual_literal', block.get('visual_theme', '')), "ARQUIVO": f"🎬 {best['file_name']}",
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
            try:
                # Re-construct text content to ensure it's fresh and valid
                sb_preview = f"ROTEIRO TÉCNICO: {project_title}\n" + "="*30 + "\n"
                for item in sb: 
                    sb_preview += f"[{item.get('Tempo', '00:00')}] -> {item.get('file_name', 'N/A')} ({item.get('meta', '')})\n"
                
                # Use a specific key and ensure proper encoding
                st.download_button(
                    label="📲 Baixar roteiro para WhatsApp",
                    data=sb_preview,
                    file_name=f"roteiro_{project_title.replace(' ', '_')}.txt",
                    mime="text/plain",
                    key="download_storyboard_btn",
                    use_container_width=True
                )
                
                # --- NEW: Media Kit (ZIP) Export ---
                if st.button("📦 Baixar Pasta do Vídeo (ZIP)", use_container_width=True, help="Baixa todos os vídeos e o roteiro em um único ZIP."):
                    import zipfile
                    import io
                    
                    service = get_drive_service()
                    if not service:
                        st.error("Erro ao acessar Google Drive.")
                        st.stop()
                        
                    zip_buffer = io.BytesIO()
                    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                        # 1. Add Script
                        zip_file.writestr(f"roteiro_{project_title.replace(' ', '_')}.txt", sb_preview)
                        
                        # 2. Add Videos
                        total_vids = len(sb)
                        progress_text = st.empty()
                        progress_bar = st.progress(0)
                        
                        for i, item in enumerate(sb):
                            f_id = item.get('file_id')
                            f_name = item.get('file_name', f"video_{i}.mp4")
                            
                            progress_text.text(f"📥 Baixando do Drive ({i+1}/{total_vids}): {f_name}")
                            try:
                                request = service.files().get_media(fileId=f_id)
                                video_data = io.BytesIO()
                                downloader = MediaIoBaseDownload(video_data, request)
                                done = False
                                while not done:
                                    _, done = downloader.next_chunk()
                                
                                zip_file.writestr(f_name, video_data.getvalue())
                            except Exception as vid_err:
                                st.warning(f"⚠️ Erro ao baixar {f_name}: {vid_err}")
                            
                            progress_bar.progress((i + 1) / total_vids)
                        
                        progress_text.text("✅ ZIP pronto para download!")
                        
                    st.download_button(
                        label="🔥 CLIQUE PARA SALVAR O ZIP",
                        data=zip_buffer.getvalue(),
                        file_name=f"Kit_{project_title.replace(' ', '_')}.zip",
                        mime="application/zip",
                        use_container_width=True
                    )
            except Exception as e:
                st.error(f"Erro ao preparar download: {e}")

    with tab3:
        st.header("🔍 Busca Inteligente de Vídeos")
        st.markdown("---")
        
        search_col1, search_col2 = st.columns([3, 1])
        with search_col1:
            search_query = st.text_input("O que você procura?", placeholder="Ex: 'alguém tomando café', 'clima de mistério', 'pessoa digitando'", key="video_search_input")
        with search_col2:
            search_mode = st.selectbox("Modo de Busca", ["Rápido (Palavras-chave)", "Profundo (IA Semântica)"])

        if search_query:
            all_vids = supabase.table("video_library").select("*").execute().data or []
            
            if not all_vids:
                st.warning("⚠️ Biblioteca vazia. Sincronize na segunda aba.")
            else:
                with st.spinner("Buscando matches perfeitos..."):
                    results = []
                    q = search_query.lower()
                    
                    if search_mode == "Rápido (Palavras-chave)":
                        for v in all_vids:
                            score = 0
                            v_acao = (v.get('acao') or '').lower()
                            v_desc = (v.get('descricao') or '').lower()
                            v_emocao = (v.get('emocao') or '').lower()
                            v_tags = v.get('tags') or []
                            if isinstance(v_tags, str): v_tags = [v_tags]
                            v_tags = [str(t).lower() for t in v_tags]
                            
                            # Matching
                            if q in v_acao: score += 10
                            if q in v_desc: score += 5
                            if q in v_emocao: score += 5
                            if any(q in t for t in v_tags): score += 7
                            
                            # Partial match for multi-word queries
                            words = q.split()
                            if len(words) > 1:
                                for word in words:
                                    if word in v_acao: score += 2
                                    if word in v_desc: score += 1
                                    
                            if score > 0:
                                results.append((v, score))
                    
                    else: # IA Semântica
                        # Prompt IA to extract keywords or rank based on explanation
                        if gemini_model:
                            prompt = f"""
                            Dada a solicitação: "{search_query}"
                            Extraia os 5 conceitos ou palavras-chave mais importantes para busca visual.
                            Retorne apenas uma lista JSON: ["palavra1", "palavra2", ...]
                            """
                            try:
                                response = gemini_model.generate_content(prompt)
                                json_match = re.search(r'\[.*\]', response.text, re.DOTALL)
                                keywords = json.loads(json_match.group()) if json_match else [search_query]
                                
                                for v in all_vids:
                                    score = 0
                                    v_text = f"{v.get('acao')} {v.get('descricao')} {v.get('emocao')} {' '.join(v.get('tags') or [])}".lower()
                                    for kw in keywords:
                                        if kw.lower() in v_text: score += 5
                                    if score > 0:
                                        results.append((v, score))
                            except:
                                # Fallback to keyword match
                                for v in all_vids:
                                    if q in str(v).lower(): results.append((v, 1))
                                    
                    # Sort by score
                    results.sort(key=lambda x: x[1], reverse=True)
                    
                    if results:
                        st.write(f"✅ Encontramos **{len(results)}** possíveis matches:")
                        
                        # Display in grid
                        cols_per_row = 4
                        for i in range(0, min(len(results), 24), cols_per_row):
                            cols = st.columns(cols_per_row)
                            for j in range(cols_per_row):
                                if i + j < len(results):
                                    v_data, v_score = results[i + j]
                                    with cols[j]:
                                        # Use thumbnail from DB or fallback
                                        thumb = v_data.get('thumbnail_link')
                                        if thumb:
                                            st.image(thumb, use_container_width=True)
                                        else:
                                            st.markdown(f"🎬 **{v_data['file_name']}**")
                                        
                                        st.write(f"**{v_data['file_name']}**")
                                        with st.expander("Ver Detalhes"):
                                            st.write(f"**Ação:** {v_data.get('acao')}")
                                            st.write(f"**Emoção:** {v_data.get('emocao')}")
                                            if v_data.get('tags'):
                                                clean_tags = [str(t) for t in v_data.get('tags') if t and str(t).lower() != 'none']
                                                if clean_tags:
                                                    st.caption(f"Tags: {', '.join(clean_tags)}")
                                            st.link_button("Abrir no Drive 🔗", v_data.get('drive_link', ''))
                    else:
                        st.info("🔍 Nenhum vídeo encontrado. Tente outras palavras ou use o modo 'Profundo'.")

    st.markdown("---")

except Exception as e:
    st.error("❌ ERRO CRÍTICO"); st.exception(e); st.code(traceback.format_exc())
