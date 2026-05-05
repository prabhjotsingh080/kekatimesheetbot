"""
main.py — Keka Timesheet Automation Pipeline with Streamlit UI
"""

import sys
import os
import subprocess

# Auto-launch Streamlit if run as standard python script
import streamlit as st
# Streamlit UI Configuration
st.set_page_config(page_title="Keka Automation", page_icon="⏱️", layout="centered")

def run_script_realtime(script_name, args=None, log_file=None):
    """Yields output lines from a subprocess and optionally writes to a log file."""
    if args is None:
        args = []
        
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    
    cmd = [sys.executable, script_name] + args
    process = subprocess.Popen(
        cmd,
        cwd=os.path.dirname(os.path.abspath(__file__)),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env
    )
    
    import re
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    
    # Read output line by line as it is generated
    for line in iter(process.stdout.readline, ''):
        if log_file:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(ansi_escape.sub('', line))
        yield line
    process.stdout.close()
    process.wait()
    if process.returncode != 0:
        raise RuntimeError(f"❌ {script_name} failed with exit code {process.returncode}")

st.title("⏱️ Keka Timesheet Automation")
st.markdown("Automate your Keka timesheet entries using **Playwright** and **Gemini AI**.")

# Initialize session state for tracking progress
if "stage" not in st.session_state:
    st.session_state.stage = "input"  # input -> login -> processing -> completed

# --- STAGE 1: INPUT ---
if st.session_state.stage == "input":
    with st.container(border=True):
        st.subheader("📝 Time Entry Log")
        st.markdown("Describe your tasks in natural language, or leave empty to use the existing `input.json`.")
        
        if "user_log_text" not in st.session_state:
            st.session_state.user_log_text = ""

        audio_value = st.audio_input("🎤 Record Voice Log")
        if audio_value is not None:
            if st.session_state.get("last_audio") != audio_value:
                st.session_state.last_audio = audio_value
                with st.spinner("Transcribing with Gladia..."):
                    import tempfile
                    import os
                    from gladia_transcribe import upload_audio, create_transcription_job, poll_result
                    
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
                        tmp.write(audio_value.getvalue())
                        tmp_path = tmp.name
                    
                    try:
                        audio_url = upload_audio(tmp_path)
                        job_id, result_url = create_transcription_job(audio_url)
                        data = poll_result(result_url)
                        transcript = data.get("result", {}).get("transcription", {}).get("full_transcript", "")
                        
                        if transcript:
                            if st.session_state.user_log_text:
                                st.session_state.user_log_text += " " + transcript
                            else:
                                st.session_state.user_log_text = transcript
                            st.rerun()
                    except Exception as e:
                        st.error(f"Transcription failed: {e}")
                    finally:
                        if os.path.exists(tmp_path):
                            os.unlink(tmp_path)

        st.text_area(
            "Your Time Log:", 
            placeholder="e.g. 5th may and 6 hours on collibra and 4 hours on atlan rnd on 6th may",
            key="user_log_text"
        )
        
        if st.button("🚀 Start Automation", type="primary"):
            st.session_state.user_log = st.session_state.user_log_text
            st.session_state.stage = "login"
            st.rerun()

# --- STAGE 2: LOGIN ---
elif st.session_state.stage == "login":
    with st.container(border=True):
        st.subheader("🌐 Launching Browser")
        st.info("A Chrome window should be opening in the background.\n\n"
                "Please log in with `prabhjot.singh@cloudsufi.com` and navigate to the **Timesheet** page.")
        
        # Launch Chrome only once per login stage entry
        if "chrome_launched" not in st.session_state:
            bat_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launch_chrome.bat")
            if os.path.exists(bat_path):
                subprocess.Popen(["cmd", "/c", bat_path], creationflags=subprocess.CREATE_NEW_CONSOLE)
            st.session_state.chrome_launched = True
            
        st.warning("⚠️ Do not click the button below until you have fully loaded the Timesheet page in Chrome!")
        
        col1, col2 = st.columns([1, 1])
        with col1:
            if st.button("✅ I am on the Timesheet Page", type="primary", use_container_width=True):
                st.session_state.stage = "processing"
                st.rerun()
        with col2:
            if st.button("⬅️ Go Back", use_container_width=True):
                st.session_state.stage = "input"
                del st.session_state["chrome_launched"]
                st.rerun()

# --- STAGE 3: PROCESSING ---
elif st.session_state.stage == "processing":
    st.subheader("⚙️ Processing Pipeline")
    
    user_log = st.session_state.get("user_log", "").strip()
    args = [user_log] if user_log else []

    try:
        from datetime import datetime
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "automation.log")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(f"--- Keka Automation Pipeline Logs ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ---\n\n")

        # Step 1: LLM Reader
        with st.status("🧠 Step 1: Parsing Natural Language with Gemini", expanded=True) as status1:
            log_container1 = st.empty()
            log_text1 = ""
            for line in run_script_realtime("llmreader.py", args, log_file=log_path):
                log_text1 += line
                log_container1.code(log_text1, language="text")
            status1.update(label="✅ Step 1: Parsed Natural Language (input.json)", state="complete", expanded=False)
            
        # Step 2: Fetch
        with st.status("📥 Step 2: Fetching Keka Tasks", expanded=True) as status2:
            log_container2 = st.empty()
            log_text2 = ""
            for line in run_script_realtime("fetch.py", log_file=log_path):
                log_text2 += line
                log_container2.code(log_text2, language="text")
            status2.update(label="✅ Step 2: Fetched Keka Tasks (fetched.json)", state="complete", expanded=False)
            
        # Step 3: Fill
        with st.status("✍️ Step 3: Filling Timesheet", expanded=True) as status3:
            log_container3 = st.empty()
            log_text3 = ""
            for line in run_script_realtime("fill.py", log_file=log_path):
                log_text3 += line
                log_container3.code(log_text3, language="text")
            status3.update(label="✅ Step 3: Timesheet Filled Successfully!", state="complete", expanded=False)
            
        st.session_state.stage = "completed"
        st.rerun()

    except Exception as e:
        st.error(f"Pipeline Interrupted: {e}")
        if st.button("🔄 Retry Pipeline", type="primary"):
            st.session_state.stage = "input"
            if "chrome_launched" in st.session_state:
                del st.session_state["chrome_launched"]
            st.rerun()

# --- STAGE 4: COMPLETED ---
elif st.session_state.stage == "completed":
    st.success("🎉 All stages completed successfully! Your timesheet is up to date.")
    st.balloons()
    
    if st.button("🔄 Start Another Entry", type="primary"):
        st.session_state.stage = "input"
        if "chrome_launched" in st.session_state:
            del st.session_state["chrome_launched"]
        st.session_state.user_log = ""
        st.rerun()
