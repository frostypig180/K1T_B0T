import os
from werkzeug.utils import secure_filename
from pathlib import Path
from fastapi import FastAPI, File, UploadFile, HTTPException, Header, status, Depends
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets
from openai import OpenAI
import shutil
import asyncio
from typing import List, Dict
import threading
import queue
import json
import re
from dataclasses import dataclass, asdict
from database import create_user, create_conversation, get_conversations_by_class, save_message, save_summary, get_messages, get_all_conversations, clear_all_conversations

# ===============================================================================================
# Kit Bot: LLM Chatbot using vLLM and Mistral Instruct
# Authors: Eli Gruhlke, Ian Walch, Will Dani, Beaumont Ujlaky, Caleb Schweigert, and Erik Greiner
# ===============================================================================================

USER = os.getenv("ADMIN_USER")
PASS = os.getenv("ADMIN_PASS")
BOT_SILLINESS = 0.1

# Connect to local vLLM server
client = OpenAI(base_url="http://localhost:8000/v1", api_key="EMPTY")
model = "mistralai/Mistral-7B-Instruct-v0.3"
# Path to instructions directory
instructions_path = "/home/k1tbot/Documents/k1tbot/instructions"
# Path to bot rules file
rules_path = "/home/k1tbot/Documents/k1tbot/bot_rules/BotPrompt.txt"
# Path to summary rules file
summary_rules_path = "/home/k1tbot/Documents/k1tbot/bot_rules/SummaryPrompt.txt"
# Path to saved class configuration files
class_configs_dir = "/home/k1tbot/Documents/k1tbot/class_configs"
os.makedirs(class_configs_dir, exist_ok=True)
# Path to saved chat summaries
summaries_dir = "/home/k1tbot/Documents/k1tbot/chat_summaries"
os.makedirs(summaries_dir, exist_ok=True)
# Initialize chat histories and locks for multiple users
chat_histories: dict[str, list[dict[str, str]]] = {}
chat_locks: dict[str, asyncio.Lock] = {}
# Initialize chat summaries
chat_summaries: dict[str, dict] = {}
# session to database mapping
db_sessions: dict[str, dict] = {}
# Initialize FastAPI app
app = FastAPI()
# Allow frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://k1tb0t.com", "https://www.k1tb0t.com"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Basic auth for admin endpoints
security = HTTPBasic()
def check_auth(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, USER)
    correct_password = secrets.compare_digest(credentials.password, PASS)
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
# Uploads configuration
UPLOAD_FOLDER = instructions_path
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.pdf', '.csv', '.txt', '.json', '.mp4'}
# Mount uploads as static files
app.mount('/instructions', StaticFiles(directory=UPLOAD_FOLDER), name='instructions')


# ===============================================================================================
# Helper functions
# ===============================================================================================

def load_bot_rules() -> str:
    try:
        return Path(rules_path).read_text(encoding="utf-8").strip()
    except Exception as e:
        return f"Could not read bot rules! ({e})"

def load_summary_rules() -> str:
    try:
        return Path(summary_rules_path).read_text(encoding="utf-8").strip()
    except Exception as e:
        return (
            "ROLE: You are an educational analyst reviewing student reflection conversations with K1T B0T, an academic support chatbot."
            "BEHAVIOR RULES: Keep each response under 3 sentences."
        )

def load_all_class_configs() -> list[dict]:
    configs = []
    for filepath in sorted(Path(class_configs_dir).glob("*.json")):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                config = json.load(f)
                config["_filename"] = filepath.name
                configs.append(config)
        except Exception as e:
            print(f"Warning: Could not read class config {filepath.name}: {e}")
    return configs

def get_class_config(class_id: str) -> dict | None:
    for config in load_all_class_configs():
        if config.get("class_id") == class_id:
            return config
    return None

def get_summary_folder(config: dict) -> str:
    class_name = config.get("class_name", "Unknown_Class").replace(" ", "_")
    folder = os.path.join(summaries_dir, class_name)
    os.makedirs(folder, exist_ok=True)
    return folder

def load_instructions(class_id: str) -> str:
    parts = []
    config = get_class_config(class_id)
    id = class_id.strip().lower()
    name = config.get("class_name", "").strip().lower()
    try:
        for file in sorted(Path(UPLOAD_FOLDER).glob("*.txt")):
            stem = file.stem.strip().lower()
            if id in stem or stem in name:
                parts.append(file.read_text(encoding="utf-8").strip())
        combined = "\n\n".join([p for p in parts if p])
        return combined if combined else "No instruction files found. Default behaviour: act like a helpful assistant."
    except Exception as e:
        return f"Could not read instructions! Default behaviour: act like a helpful assistant. ({e})"

def get_history_and_lock(sid: str, class_id: str | None = None):
    if sid not in chat_histories:
        chat_histories[sid] = [{"role": "system", "content": load_instructions(class_id)}]
    if sid not in chat_locks:
        chat_locks[sid] = asyncio.Lock()
    return chat_histories[sid], chat_locks[sid]

def get_or_create_db_session(sid: str, class_id: str | None = None):
    if sid not in db_sessions:
        user_id = create_user()
        conversation_id = create_conversation(user_id, class_id)
        db_sessions[sid] = {
            "user_id": str(user_id),
            "conversation_id": str(conversation_id),
            "message_index": 1,
            "class_id": class_id,
        }
    return db_sessions[sid]


# ===============================================================================================
# Admin + file management endpoints
# ===============================================================================================

@app.get("/admin")
def admin_root(auth: str = Depends(check_auth)):
    return FileResponse("admin/index.html")

@app.post('/upload')
async def upload(file: UploadFile = File(...)):
    original_name = Path(file.filename).name
    if not original_name:
        raise HTTPException(status_code=400, detail='Invalid filename')
    ext = Path(original_name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail='File type not allowed')
    sanitized = secure_filename(original_name)
    base, ext = os.path.splitext(sanitized)
    candidate = sanitized
    i = 1
    while os.path.exists(os.path.join(UPLOAD_FOLDER, candidate)):
        candidate = f"{base}_{i}{ext}"
        i += 1
    safe_name = candidate
    save_path = os.path.join(UPLOAD_FOLDER, safe_name)
    try:
        contents = await file.read()
        with open(save_path, 'wb') as out_f:
            out_f.write(contents)
    finally:
        await file.close()
    return {"filename": safe_name}

@app.delete("/delete")
async def delete_resources(payload: dict):
    files = payload.get("resources", [])
    if not files:
        raise HTTPException(status_code=400, detail="No files specified for deletion")
    deleted_files = []
    errors = []
    for filename in files:
        safe_name = Path(filename).name
        file_path = os.path.join(UPLOAD_FOLDER, safe_name)
        if not os.path.exists(file_path):
            errors.append(f"File not found: {safe_name}")
            continue
        try:
            os.remove(file_path)
            deleted_files.append(safe_name)
        except Exception as e:
            errors.append(f"Error deleting {safe_name}: {str(e)}")
    return {
        "deleted": deleted_files,
        "errors": errors
    }

@app.get("/list")
async def list_resources():
    if not os.path.exists(UPLOAD_FOLDER):
        return []
    return sorted([
        f for f in os.listdir(UPLOAD_FOLDER)
        if os.path.isfile(os.path.join(UPLOAD_FOLDER, f))
    ])


# ===============================================================================================
# Chat endpoint
# ===============================================================================================

@app.post("/chat")
async def chat(payload: dict, x_session_id: str = Header(None, alias="X-Session-Id")):
    if not x_session_id:
        raise HTTPException(400, "Missing X-Session-Id header")

    user_input = payload["message"]
    class_id = payload.get("class_id")
    chat_history, chat_lock = get_history_and_lock(x_session_id, class_id)

    db_session = get_or_create_db_session(x_session_id, class_id)
    conversation_id = db_session["conversation_id"]

    if "Hello my mechanized assistant!" in user_input.strip():
        bot_rules = load_bot_rules()
        chat_history.clear()
        chat_history.append({"role": "system", "content": bot_rules})
        user_input = load_instructions(class_id)  # Reset to instructions as first user message
        print(f"\x1b[43m[{x_session_id}] New Session Started.\x1b[0m")
    else:
        print(f"\x1b[43m[{x_session_id}] User: {user_input}\x1b[0m")

    user_msg = {"role": "user", "content": user_input}
    temp_messages = list(chat_history) + [user_msg]

    async def stream():
        async with chat_lock:
            index = db_session["message_index"]
            q: "queue.Queue[str|object]" = queue.Queue()
            DONE = object()

            save_message(conversation_id, "user", user_input, index)
            index += 1

            def worker():
                try:
                    resp = client.chat.completions.create(
                        model=model,
                        messages=temp_messages,
                        temperature=BOT_SILLINESS,
                        max_tokens=150,
                        stream=True,
                    )
                    for chunk in resp:
                        delta = chunk.choices[0].delta
                        if delta and delta.content:
                            q.put(delta.content)
                    q.put(DONE)
                except Exception as e:
                    q.put(f"[ERROR] {e}")
                    q.put(DONE)

            threading.Thread(target=worker, daemon=True).start()
            collected = ""
            yield ""
            while True:
                item = await asyncio.to_thread(q.get)
                if item is DONE:
                    break
                collected += item
                yield item

            chat_history.append(user_msg)
            chat_history.append({"role": "assistant", "content": collected})

            save_message(conversation_id, "bot", collected, index)
            index += 1
            db_session["message_index"] = index

            print(f"\x1b[42m[{x_session_id}] K1T B0T: {collected}\x1b[0m")

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(stream(), media_type="text/plain; charset=utf-8", headers=headers)


# ===============================================================================================
# Per-session summary
# ===============================================================================================

def build_class_summary_prompt(messages: list[dict[str, str]], summary_type: str, class_id: str) -> list[dict[str, str]]:
    week_topic = load_instructions(class_id)
    if not messages:
        return []
    transcript_lines = []
    current_session = None
    seen_sessions = set()
    for msg in messages:
        sid = msg["session_id"]
        # New session
        if sid not in seen_sessions:
            seen_sessions.add(sid)
            current_session = sid
            # Skip FIRST message of this session
            transcript_lines.append(f"\n=== SESSION {sid[:8]} ===")
            continue
        speaker = "Student" if msg["sender"] == "user" else "K1T B0T"
        transcript_lines.append(f"{speaker}: {msg['content']}")
    transcript = "\n".join(transcript_lines)

    if summary_type == "general":
        return [
            {
                "role": "system",
                "content": load_summary_rules(),
            },
            {
                "role": "user",
                "content": (
                    "Below are multiple conversations between students and K1T B0T, an AI reflection chatbot.\n\n"
                    "The professor's topic and instructions for this week are:\n"
                    "=== WEEK TOPIC ===\n"
                    + week_topic + "\n"
                    "=== END WEEK TOPIC ===\n\n"
                    "=== ALL CONVERSATIONS ===\n"
                    + transcript + "\n"
                    "=== END ALL CONVERSATIONS ===\n\n"
                    "Analyze all of these conversations together and generate ONE overall summary for the class."
                    "Keep the summary short, under 3 sentences."
                ),
            },
        ]
    if summary_type == "strengths":
        return [
            {
                "role": "system",
                "content": (
                    "You are an educational analyst. You analyze student reflection conversations. Be clear and concise."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Below are multiple conversations between students and K1T B0T, an AI reflection chatbot.\n\n"
                    "The professor's topic and instructions for this week are:\n"
                    "=== WEEK TOPIC ===\n"
                    + week_topic + "\n"
                    "=== END WEEK TOPIC ===\n\n"
                    "=== ALL CONVERSATIONS ===\n"
                    + transcript + "\n"
                    "=== END ALL CONVERSATIONS ===\n\n"
                    "Analyze all of these conversations together and generate a summary of the main topics that students seem STRONG in based on their reflections. "
                    "Focus on areas where students seem to have a good understanding or are doing well."
                    "Keep the summary short, under 3 sentences."
                ),
            },
        ]
    if summary_type == "needs_help":
        return [
            {
                "role": "system",
                "content": (
                    "You are an educational analyst. You analyze student reflection conversations. Be clear and concise."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Below are multiple conversations between students and K1T B0T, an AI reflection chatbot.\n\n"
                    "The professor's topic and instructions for this week are:\n"
                    "=== WEEK TOPIC ===\n"
                    + week_topic + "\n"
                    "=== END WEEK TOPIC ===\n\n"
                    "=== ALL CONVERSATIONS ===\n"
                    + transcript + "\n"
                    "=== END ALL CONVERSATIONS ===\n\n"
                    "Analyze all of these conversations together and generate a summary of the main topics that students seem to NEED HELP in based on their reflections. "
                    "Focus on areas where students seem to be struggling or have misunderstandings."
                    "Keep the summary short, under 3 sentences."
                ),
            },
        ]
    return []
    

def generate_class_summary_sync(messages: list[dict[str, str]], class_id: str) -> dict:
    general = build_class_summary_prompt(messages, "general", class_id)
    strengths = build_class_summary_prompt(messages, "strengths", class_id)
    needs_help = build_class_summary_prompt(messages, "needs_help", class_id)
    if not general or not strengths or not needs_help:
        return {
            "conversation_summary": "No student messages found for this class.",
            "topics_strong": "N/A",
            "topics_need_help": "N/A"
        }
    general_response = client.chat.completions.create(
        model=model,
        messages=general,
        temperature=0.3,
        max_tokens=512,
    )
    strengths_response = client.chat.completions.create(
        model=model,
        messages=strengths,
        temperature=0.3,
        max_tokens=512,
    )
    needs_help_response = client.chat.completions.create(
        model=model,
        messages=needs_help,
        temperature=0.3,
        max_tokens=512,
    )
    return {
        "conversation_summary": general_response.choices[0].message.content.strip(),
        "topics_strong": strengths_response.choices[0].message.content.strip(),
        "topics_need_help": needs_help_response.choices[0].message.content.strip(),
    }

def save_summary_to_file(summary: dict, folder: str):
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    if "session_id" in summary:
        filename = f"{timestamp}_{summary['session_id'][:8]}.json"
    else:
        filename = f"{timestamp}_class_{summary.get('class_id', 'unknown')}.json"

    filepath = os.path.join(folder, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

@app.post("/summary")
async def summarize_class(payload: dict):
    class_id = payload.get("class_id", "")
    if not class_id:
        raise HTTPException(400, "Missing class_id in request body")

    config = get_class_config(class_id)
    if not config:
        raise HTTPException(404, f"No class config found for class_id: {class_id}")

    conversations = await asyncio.to_thread(get_conversations_by_class, class_id)

    messages = []
    for convo in conversations:
        session_messages = await asyncio.to_thread(get_messages, convo["session_id"])
        for msg in session_messages:
            msg["session_id"] = convo["session_id"]
            messages.append(msg)

    if len(messages) < 2:
        raise HTTPException(400, "Not enough conversation data to summarize for this class")

    summary = await asyncio.to_thread(generate_class_summary_sync, messages, class_id)

    week_topic = load_instructions(class_id)
    session_ids = sorted({msg["session_id"] for msg in messages})

    result = {
        "class_id": class_id,
        "class_name": config.get("class_name"),
        "week_topic": week_topic[:200] + "..." if len(week_topic) > 200 else week_topic,
        **summary,
        "message_count": len(messages),
        "session_count": len(session_ids),
    }

    folder = get_summary_folder(config)
    save_summary_to_file(result, folder)

    print(f"\x1b[44m[{class_id}] Summary generated -> {folder}\x1b[0m")
    return result



# ===============================================================================================
# Class config endpoints
# ===============================================================================================

@app.get("/classes")
async def list_classes():
    configs = load_all_class_configs()
    return [
        {
            "class_id": c.get("class_id"),
            "class_name": c.get("class_name"),
        }
        for c in configs
    ]

@app.post("/upload-config")
async def upload_config(file: UploadFile = File(...)):
    if not file.filename.endswith(".json"):
        raise HTTPException(400, "Only .json files are allowed")
    try:
        contents = await file.read()
        config = json.loads(contents.decode("utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON file")
    finally:
        await file.close()
    required = ["class_id", "class_name"]
    missing = [f for f in required if f not in config]
    if missing:
        raise HTTPException(400, f"Missing required fields: {', '.join(missing)}")
    safe_name = config["class_id"].replace(" ", "_") + ".json"
    save_path = os.path.join(class_configs_dir, safe_name)
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)
    return {
        "filename": safe_name,
        "class_id": config["class_id"],
        "class_name": config["class_name"],
    }

@app.get("/config/{class_id}")
async def get_class_config_endpoint(class_id: str, auth: str = Depends(check_auth)):
    config = get_class_config(class_id)
    if not config:
        raise HTTPException(404, f"No class config found for class_id: {class_id}")
    return config

@app.put("/config/{class_id}")
async def update_class_config(class_id: str, payload: dict, auth: str = Depends(check_auth)):
    config = get_class_config(class_id)
    if not config:
        raise HTTPException(404, f"No class config found for class_id: {class_id}")
    required = ["class_id", "class_name"]
    missing = [f for f in required if f not in payload]
    if missing:
        raise HTTPException(400, f"Missing required fields: {', '.join(missing)}")
    if payload["class_id"] != class_id:
        existing = get_class_config(payload["class_id"])
        if existing:
            raise HTTPException(409, f"class_id '{payload['class_id']}' already exists")
    old_filename = config.get("_filename")
    new_filename = payload["class_id"].replace(" ", "_") + ".json"
    old_path = os.path.join(class_configs_dir, old_filename)
    new_path = os.path.join(class_configs_dir, new_filename)
    save_payload = {k: v for k, v in payload.items() if k != "_filename"}
    with open(new_path, "w", encoding="utf-8") as f:
        json.dump(save_payload, f, indent=4)
    if old_path != new_path and os.path.exists(old_path):
        os.remove(old_path)
    return {"updated": new_filename, **save_payload}

@app.delete("/config/{class_id}")
async def delete_class_config(class_id: str, auth: str = Depends(check_auth)):
    config = get_class_config(class_id)
    if not config:
        raise HTTPException(404, f"No class config found for class_id: {class_id}")
    filepath = os.path.join(class_configs_dir, config["_filename"])
    os.remove(filepath)
    return {"deleted": class_id}


# ===============================================================================================
# Summary retrieval endpoints
# ===============================================================================================

@app.get("/summaries")
async def get_all_summaries(auth: str = Depends(check_auth)):
    all_summaries = []
    for root, dirs, files in os.walk(summaries_dir):
        for filename in sorted(files):
            if filename.endswith(".json"):
                filepath = os.path.join(root, filename)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        all_summaries.append(json.load(f))
                except Exception:
                    continue
    return all_summaries

@app.get("/summaries/{class_id}")
async def get_class_summaries(class_id: str, auth: str = Depends(check_auth)):
    config = get_class_config(class_id)
    if not config:
        raise HTTPException(404, f"No class config found for class_id: {class_id}")
    class_name = config.get("class_name", "Unknown_class").replace(" ", "_")
    class_folder = os.path.join(summaries_dir, class_name)
    if not os.path.exists(class_folder):
        return []
    all_summaries = []
    for root, dirs, files in os.walk(class_folder):
        for filename in sorted(files):
            if filename.endswith(".json"):
                filepath = os.path.join(root, filename)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        all_summaries.append(json.load(f))
                except Exception:
                    continue
    return all_summaries

@app.get("/active-sessions/{class_id}")
async def get_active_sessions(class_id: str):
    return await asyncio.to_thread(get_conversations_by_class, class_id)

@app.delete("/conversations", dependencies=[Depends(check_auth)])
async def clear_conversations():
    await asyncio.to_thread(clear_all_conversations)
    chat_histories.clear()
    chat_locks.clear()
    chat_summaries.clear()
    db_sessions.clear()
    print("\x1b[41m[ADMIN] Database cleared.\x1b[0m")
    return {"cleared": True}

@app.get("/session/{session_id}")
async def get_session(session_id: str):
    messages = await asyncio.to_thread(get_messages, session_id)
    if not messages:
        raise HTTPException(404, f"No messages found for session: {session_id}")
    messages[0]["content"] = "{System prompt hidden for brevity}"
    return {
        "session_id": session_id,
        "messages": messages,
        "message_count": len(messages),
    }