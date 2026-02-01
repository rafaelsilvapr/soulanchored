import streamlit as st
import sys
import traceback

# 1. Mandatory First Call
st.set_page_config(page_title="Soul Anchored", layout="wide")

st.title("Soul Anchored - Debug Mode 🧠")
st.write("Verificando ambiente...")

try:
    import os
    import pandas as pd
    from supabase import create_client
    import google.generativeai as genai
    st.success("✅ Importações Básicas OK")
except Exception as e:
    st.error(f"❌ Erro de Importação: {e}")
    st.code(traceback.format_exc())
    st.stop()

# 2. Check Secrets
st.write("Verificando Segredos...")
missing_secrets = []
for key in ["SUPABASE_URL", "SUPABASE_KEY", "GOOGLE_API_KEY"]:
    if key not in st.secrets:
        missing_secrets.append(key)

if missing_secrets:
    st.error(f"❌ Segredos ausentes: {', '.join(missing_secrets)}")
    st.info("Por favor, adicione esses segredos no painel do Streamlit Cloud.")
else:
    st.success("✅ Segredos configurados!")
    
    # 3. Try to initialize Supabase
    try:
        supabase = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
        st.success("✅ Conexão com Supabase OK")
    except Exception as e:
        st.error(f"❌ Erro Supabase: {e}")

st.divider()
st.write("Se você está vendo esta mensagem, o 'Oh no' foi vencido. Agora vou restaurar a lógica editorial completa.")
