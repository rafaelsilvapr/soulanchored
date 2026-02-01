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
    import pandas as pd
    from datetime import datetime
    from supabase import create_client, Client
    import google.generativeai as genai

    # --- UI Styling ---
    st.markdown("""
        <style>
        .stApp { background: linear-gradient(135deg, #07080c 0%, #11121d 100%); color: #e0e0e0; }
        h1, h2, h3 { font-family: 'Outfit', sans-serif; background: linear-gradient(to right, #00d2ff, #7000ff); -webkit-background-clip: text; -webkit-text-fill-color: transparent; font-weight: 800; }
        .stButton>button { background: linear-gradient(135deg, #7000ff 0%, #00d2ff 100%); color: white; border-radius: 8px; font-weight: 600; border: none; padding: 0.5rem 2rem; }
        .stTable { background-color: rgba(255,255,255,0.05); border-radius: 10px; }
        [data-testid="stMetricValue"] { color: #00d2ff; }
        </style>
        """, unsafe_allow_html=True)

    # --- Configuration ---
    if "SUPABASE_URL" not in st.secrets:
        st.error("❌ Erro: 'SUPABASE_URL' ausente nos Secrets.")
        st.stop()
        
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]

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
                [{{"timestamp": "00:00", "script_fragment": "...", "visual_theme": "..."}}]
                """
                
                st.write("⚡ Sincronizando conteúdo...")
                response = gemini_model.generate_content([audio_file, prompt])
                
                json_match = re.search(r'\[.*\]', response.text, re.DOTALL)
                if json_match:
                    result = json.loads(json_match.group())
                    genai.delete_file(audio_file.name)
                    return result
                else:
                    st.error("A IA retornou um formato inesperado.")
                    return None
            except Exception as e:
                st.error(f"Erro na análise multimodal: {e}")
                return None

    # --- Main App Interface ---
    st.title("Soul Anchored Assembler")
    st.subheader("Cérebro Editorial Multimodal 🧠🎙️")

    tab1, tab2 = st.tabs(["🚀 Produção de Roteiro", "📂 Biblioteca"])

    with tab2:
        st.header("Biblioteca de Vídeos")
        try:
            supabase = get_supabase_client()
            # FIX: Using 'desc=False' and 'nullsfirst=True' for modern Supabase API
            res = supabase.table("video_library").select("file_name, tags, last_used_at").order("last_used_at", desc=False, nullsfirst=True).execute()
            if res.data:
                st.dataframe(pd.DataFrame(res.data), use_container_width=True)
            else:
                st.warning("Biblioteca vazia.")
        except Exception as e:
            st.error(f"Erro no banco de dados: {e}")

    with tab1:
        col1, col2 = st.columns([1, 1])
        with col1:
            project_title = st.text_input("Título do Projeto", value="Nova Montagem")
            script_text = st.text_area("Roteiro Original", height=250, placeholder="Cole o roteiro...")
        
        with col2:
            audio_in = st.file_uploader("Upload de Áudio (.mp3/wav)", type=['mp3', 'wav'])
            if audio_in: st.audio(audio_in)

        if st.button("🧠 Gerar Storyboard"):
            if not script_text or not audio_in:
                st.warning("Forneça o roteiro e o áudio.")
            else:
                with tempfile.NamedTemporaryFile(delete=False, suffix=f".{audio_in.name.split('.')[-1]}") as tmp:
                    tmp.write(audio_in.getvalue())
                    tmp_path = tmp.name
                
                storyboard = get_storyboard_from_gemini(tmp_path, script_text)
                os.remove(tmp_path)
                
                if storyboard:
                    # Match videos with anti-repetition
                    recent_res = supabase.table("video_library").select("file_id").order("last_used_at", desc=True).limit(5).execute()
                    recent_ids = [v['file_id'] for v in (recent_res.data or [])]
                    all_videos = supabase.table("video_library").select("*").order("last_used_at", desc=False, nullsfirst=True).execute().data or []
                    
                    final_plan = []
                    session_used = []
                    
                    for block in storyboard:
                        theme = block.get('visual_theme', '')
                        text = block.get('script_fragment', '')
                        tags_needed = [w.lower() for w in re.findall(r'\w{4,}', theme + " " + text)]
                        
                        candidates = [v for v in all_videos if v['file_id'] not in recent_ids and v['file_id'] not in session_used]
                        best = None
                        for v in candidates:
                            v_tags = str(v.get('tags', [])).lower()
                            if any(t in v_tags for t in tags_needed):
                                best = v; break
                        
                        if not best:
                            if candidates: best = candidates[0]
                            elif all_videos: best = all_videos[0]
                        
                        if best:
                            final_plan.append({
                                "Tempo": block['timestamp'],
                                "Texto": text,
                                "Sugestão Visual": theme,
                                "ARQUIVO": f"🎬 {best['file_name']}",
                                "file_id": best['file_id'],
                                "file_name": best['file_name']
                            })
                            session_used.append(best['file_id'])
                    
                    st.session_state['last_storyboard'] = final_plan
                    st.success("Storyboard gerado!")

    if 'last_storyboard' in st.session_state:
        sb = st.session_state['last_storyboard']
        st.divider()
        st.header("📋 Tabela de Montagem Técnico")
        st.table(pd.DataFrame(sb)[["Tempo", "Texto", "Sugestão Visual", "ARQUIVO"]])
        
        c1, c2 = st.columns(2)
        with c1:
            if st.button("✅ Confirmar Montagem e Registrar", use_container_width=True):
                now = datetime.now().isoformat()
                for item in sb:
                    supabase.table("video_library").update({"last_used_at": now}).eq("file_id", item['file_id']).execute()
                st.balloons()
                st.success("Uso registrado!")
                del st.session_state['last_storyboard']
                st.rerun()
        with c2:
            txt = f"ROTEIRO TÉCNICO: {project_title}\n" + "="*30 + "\n"
            for item in sb:
                txt += f"[{item['Tempo']}] -> {item['file_name']}\n"
            st.download_button("📲 Baixar roteiro para WhatsApp", txt, file_name="roteiro.txt", use_container_width=True)

except Exception as e:
    st.error("❌ ERRO CRÍTICO")
    st.exception(e)
    st.code(traceback.format_exc())
