import sys
import traceback

try:
    import streamlit as st
    
    # Page config must be the very first st call
    st.set_page_config(page_title="Soul Anchored - Cérebro Editorial", page_icon="🧠", layout="wide")
    
    st.info("🔄 Inicializando Cérebro Editorial...")

    import os
    import re
    import io
    import time
    import json
    import tempfile
    import uuid
    import pandas as pd
    from datetime import datetime
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from supabase import create_client, Client
    import google.generativeai as genai

    # --- Configuration ---
    if "SUPABASE_URL" not in st.secrets:
        st.error("❌ Erro: 'SUPABASE_URL' não encontrado nos Secrets do Streamlit.")
        st.stop()
        
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
    FOLDER_ID = st.secrets.get("FOLDER_ID", "15xna7XFA7W3liDawGjbHqpF7o4_nmo1e")

    # Setup Gemini
    if GOOGLE_API_KEY:
        genai.configure(api_key=GOOGLE_API_KEY)
        gemini_model = genai.GenerativeModel('gemini-1.5-flash')
    else:
        gemini_model = None

    @st.cache_resource
    def get_supabase_client():
        return create_client(SUPABASE_URL, SUPABASE_KEY)

    def get_storyboard_from_gemini(audio_path, script_text):
        if not gemini_model:
            st.error("IA não configurada.")
            return None
        
        with st.status("🧠 IA Analisando Áudio...", expanded=True) as status:
            try:
                st.write("📤 Enviando narração...")
                audio_file = genai.upload_file(path=audio_path)
                
                while audio_file.state.name == "PROCESSING":
                    time.sleep(2)
                    audio_file = genai.get_file(audio_file.name)
                
                prompt = f"""
                Você é um Diretor de Montagem. Sincronize o roteiro em blocos de 10s baseando-se no ritmo do áudio.
                ROTEIRO:
                {script_text}
                
                Retorne um JSON puro (sem markdown):
                [{{"timestamp": "00:00", "script_fragment": "...", "visual_theme": "..."}}]
                """
                
                st.write("⚡ Sincronizando...")
                response = gemini_model.generate_content([audio_file, prompt])
                
                json_match = re.search(r'\[.*\]', response.text, re.DOTALL)
                if json_match:
                    res = json.loads(json_match.group())
                    genai.delete_file(audio_file.name)
                    return res
                else:
                    st.error("IA retornou formato inválido.")
                    return None
            except Exception as e:
                st.error(f"Erro na análise: {e}")
                return None

    # --- UI Layout ---
    st.title("Soul Anchored Assembler")
    st.subheader("Cérebro Editorial Multimodal 🧠🎙️")

    tab1, tab2 = st.tabs(["🚀 Produção", "📂 Biblioteca"])

    with tab2:
        st.header("Biblioteca")
        try:
            sb_client = get_supabase_client()
            res = sb_client.table("video_library").select("file_name, tags, last_used_at").order("last_used_at", desc=False, nullsfirst=True).execute()
            if res.data:
                st.dataframe(pd.DataFrame(res.data), use_container_width=True)
            else:
                st.warning("Biblioteca vazia.")
        except Exception as e:
            st.error(f"Erro Supabase: {e}")

    with tab1:
        col1, col2 = st.columns(2)
        with col1:
            project_title = st.text_input("Título", value="Nova Montagem")
            script_text = st.text_area("Roteiro", height=250)
        
        with col2:
            audio_in = st.file_uploader("Áudio (.mp3/wav)", type=['mp3', 'wav'])
            if audio_in: st.audio(audio_in)

        if st.button("🧠 Gerar Storyboard Editorial"):
            if not script_text or not audio_in:
                st.warning("Preencha roteiro e áudio.")
            else:
                with tempfile.NamedTemporaryFile(delete=False, suffix=f".{audio_in.name.split('.')[-1]}") as tmp:
                    tmp.write(audio_in.getvalue())
                    tmp_path = tmp.name
                
                sb_data = get_storyboard_from_gemini(tmp_path, script_text)
                os.remove(tmp_path)
                
                if sb_data:
                    # Video Matching
                    all_v = sb_client.table("video_library").select("*").execute().data or []
                    final_plan = []
                    
                    for b in sb_data:
                        tags = [w.lower() for w in re.findall(r'\w{5,}', b.get('visual_theme', '') + " " + b.get('script_fragment', ''))]
                        best = None
                        for v in all_v:
                            if any(t in str(v.get('tags', [])).lower() for t in tags):
                                best = v; break
                        if not best and all_v: best = all_v[0]
                        
                        if best:
                            final_plan.append({
                                "Tempo": b['timestamp'],
                                "Texto": b['script_fragment'],
                                "Visual": b.get('visual_theme', ''),
                                "Arquivo": best['file_name'],
                                "file_id": best['file_id']
                            })
                    
                    st.session_state['sb_state'] = final_plan
                    st.success("Storyboard gerado com sucesso!")

    if 'sb_state' in st.session_state:
        sb = st.session_state['sb_state']
        st.divider()
        st.table(pd.DataFrame(sb)[["Tempo", "Texto", "Visual", "Arquivo"]])
        
        if st.button("✅ Confirmar Uso e Registrar"):
            now = datetime.now().isoformat()
            for item in sb:
                sb_client.table("video_library").update({"last_used_at": now}).eq("file_id", item['file_id']).execute()
            st.balloons()
            st.success("Uso registrado!")
            del st.session_state['sb_state']
            st.rerun()

except Exception as e:
    # This block executes if anything above fails
    import streamlit as st
    st.error("❌ ERRO FATAL: O aplicativo falhou ao iniciar.")
    st.exception(e)
    st.write("--- DETALHES TÉCNICOS ---")
    st.code(traceback.format_exc())
