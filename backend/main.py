import json,os
import shutil

from uuid import uuid4
import uuid
from fastapi import FastAPI, Depends, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Text, create_engine, Column, String, Integer, JSON, ForeignKey, inspect, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from pydantic import BaseModel
from typing import List, Optional
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv
import random, string, secrets
from datetime import datetime, timedelta

import smtplib

# --- DATABASE SETUP ---
DATABASE_URL = "sqlite:///./droidal.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

from pathlib import Path
load_dotenv(dotenv_path=Path(__file__).parent / ".env")
print("SMTP_USER:", os.getenv("SMTP_USER"))
print("SMTP_PASS:", os.getenv("SMTP_PASS"))
print("SMTP_HOST:", os.getenv("SMTP_HOST"))




# --- DATABASE MODELS ---
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    email = Column(String, unique=True, index=True, nullable=True)
    username = Column(String, unique=True, index=True)
    password = Column(String)
    verified = Column(Integer, default=0)

class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    project_type = Column(String)
    user_id = Column(Integer, ForeignKey("users.id"))
    data = Column(JSON) 
    share_id = Column(String, unique=True, index=True, default=lambda: str(uuid4()))
    preview_bg = Column(String, nullable=True)
class CustomElement(Base):
    __tablename__ = "custom_elements"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    name = Column(String, nullable=True)
    data = Column(JSON)
    thumbnail = Column(Text)

class UserMedia(Base):
    __tablename__ = "user_media"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    media_data = Column(JSON)

class PagesSaveRequest(BaseModel):
    user_id: int
    book_id: str
    pages: list
    meta: dict = {}

class UserPages(Base):
    __tablename__ = "user_pages"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    book_id = Column(String)
    pages_data = Column(JSON)
    meta_data = Column(JSON)


Base.metadata.create_all(bind=engine)

def run_migrations():
    with engine.connect() as conn:
        inspector = inspect(engine)
        existing_columns = [col['name'] for col in inspector.get_columns('users')]
        existing_tables = inspector.get_table_names()
        
        if 'first_name' not in existing_columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN first_name VARCHAR"))
        if 'last_name' not in existing_columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN last_name VARCHAR"))
        if 'email' not in existing_columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN email VARCHAR"))

        # ── ADD THIS BLOCK ──
        proj_columns = [col['name'] for col in inspector.get_columns('projects')]
        if 'preview_bg' not in proj_columns:
            conn.execute(text("ALTER TABLE projects ADD COLUMN preview_bg VARCHAR"))
        # Add this inside run_migrations(), after the email check
        if 'verified' not in existing_columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN verified INTEGER DEFAULT 0"))

        if 'user_pages' not in existing_tables:
            conn.execute(text("""
                CREATE TABLE user_pages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER REFERENCES users(id),
                    book_id VARCHAR,
                    pages_data JSON,
                    meta_data JSON
                )
            """))

        conn.commit()

run_migrations()


# --- APP SETUP ---
app = FastAPI()

# Enable CORS for frontend-backend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Directory configurations
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VIDEO_DIR = os.path.join(BASE_DIR, "videos")
SOUNDS_DIR = os.path.join(BASE_DIR, "sounds")
if not os.path.exists(SOUNDS_DIR):
    os.makedirs(SOUNDS_DIR)
MEDIA_DIR = 'user_media'
os.makedirs(MEDIA_DIR, exist_ok=True)

# Ensure the folder exists
if not os.path.exists(VIDEO_DIR):
    os.makedirs(VIDEO_DIR)

# --- SCHEMAS ---
class LoginRequest(BaseModel):
    username: str
    password: str

class SignupRequest(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    username: str
    password: str


# Temporary store for unverified signups (never touches DB until verified)
pending_store = {}  # email -> {first_name, last_name, username, hashed_password, otp, expires_at}
reset_tokens  = {}  # token -> {email, expires_at}

def gen_otp():
    return ''.join(random.choices(string.digits, k=6))

def send_email(to: str, subject: str, html: str):
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", 587))

    if not smtp_user or not smtp_pass:
        raise HTTPException(500, detail="Email service not configured. Please set up .env file.")

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = smtp_user
        msg["To"]      = to
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP(smtp_host, smtp_port) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, to, msg.as_string())
    except smtplib.SMTPAuthenticationError:
        raise HTTPException(500, detail="Email authentication failed. Check your SMTP_USER and SMTP_PASS in .env")
    except smtplib.SMTPException as e:
        raise HTTPException(500, detail=f"Email sending failed: {str(e)}")

def verification_email_html(otp: str, first_name: str) -> str:
    return f"""
    <div style="background:#05071a;color:#fff;font-family:'Segoe UI',sans-serif;padding:40px;max-width:500px;margin:auto;border-radius:20px;border:1px solid rgba(99,102,241,0.3)">
      <h1 style="color:#818cf8;margin:0 0 8px;font-size:2rem">DROIDAL</h1>
      <h2 style="margin:0 0 20px;font-size:1.4rem">Verify your email, {first_name}!</h2>
      <p style="color:#94a3b8">Your code expires in <strong style="color:#c7d2fe">10 minutes</strong>.</p>
      <div style="background:rgba(99,102,241,0.12);border:1px solid rgba(99,102,241,0.35);border-radius:16px;padding:24px;text-align:center;letter-spacing:0.4em;font-size:2.5rem;font-weight:900;color:#fff;margin:24px 0">
        {otp}
      </div>
      <p style="color:#475569;font-size:0.8rem">If you didn't create an account, ignore this email.</p>
    </div>"""

def reset_email_html(reset_url: str, first_name: str) -> str:
    return f"""
    <div style="background:#05071a;color:#fff;font-family:'Segoe UI',sans-serif;padding:40px;max-width:500px;margin:auto;border-radius:20px;border:1px solid rgba(99,102,241,0.3)">
      <h1 style="color:#818cf8;margin:0 0 8px;font-size:2rem">DROIDAL</h1>
      <h2 style="margin:0 0 20px;font-size:1.4rem">Reset your password, {first_name}!</h2>
      <p style="color:#94a3b8">This link expires in <strong style="color:#c7d2fe">15 minutes</strong>.</p>
      <div style="text-align:center;margin:28px 0">
        <a href="{reset_url}" style="background:linear-gradient(90deg,#6366f1,#a855f7);color:#fff;text-decoration:none;padding:14px 36px;border-radius:12px;font-weight:800;font-size:1rem;display:inline-block">
          Reset Password
        </a>
      </div>
      <p style="color:#475569;font-size:0.8rem">If you didn't request this, ignore this email.</p>
    </div>"""

# --- DEPENDENCY ---
def get_db():
    db = SessionLocal()
    try: 
        yield db
    finally: 
        db.close()

# --- FILE SERVING ROUTES ---
@app.get("/")
def get_login(): 
    return FileResponse(os.path.join(FRONTEND_DIR, "login.html"))

@app.get("/signup")
def get_signup(): 
    return FileResponse(os.path.join(FRONTEND_DIR, "signup.html"))

@app.get("/verify-email")
def get_verify():
    return FileResponse(os.path.join(FRONTEND_DIR, "verify-email.html"))

@app.get("/forgot-password")
def get_forgot():
    return FileResponse(os.path.join(FRONTEND_DIR, "forgot-password.html"))

@app.get("/reset-password")
def get_reset():
    return FileResponse(os.path.join(FRONTEND_DIR, "reset-password.html"))
@app.get("/dashboard")
def get_dashboard(): 
    return FileResponse(os.path.join(FRONTEND_DIR, "dashboard.html"))

@app.get("/editor")
def get_editor(): 
    return FileResponse(os.path.join(FRONTEND_DIR, "editor.html"))

@app.get("/preview/{share_id}")
def get_preview(share_id: str):
    return FileResponse(os.path.join(FRONTEND_DIR, "flip.html"))

@app.get("/api/preview/{share_id}")
def get_preview_data(share_id: str, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.share_id == share_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {
        "data": project.data,
        "title": project.title,
        "preview_bg": project.preview_bg or ""    # ← ADD
    }

# Static file mounting for frontend assets and uploaded media
app.mount("/frontend", StaticFiles(directory=FRONTEND_DIR), name="frontend")
app.mount("/uploads", StaticFiles(directory=VIDEO_DIR), name="uploads")
app.mount("/sounds", StaticFiles(directory=SOUNDS_DIR), name="sounds")

# --- VIDEO UPLOAD API ---
@app.post("/upload_video")
async def upload_video(video: UploadFile = File(...)):
    allowed = {'.mp4', '.webm', '.mov', '.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp'}
    ext = os.path.splitext(video.filename)[1].lower()
    if ext not in allowed:
        raise HTTPException(status_code=400, detail="File type not allowed")
    
    unique_filename = f"{uuid.uuid4()}{ext}"
    file_path = os.path.join(VIDEO_DIR, unique_filename)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(video.file, buffer)
    
    return {"url": f"/uploads/{unique_filename}"}

# --- AUTH API ---
@app.post("/api/signup")
def signup(req: SignupRequest, db: Session = Depends(get_db)):
    # Check real DB for duplicates
    if req.email:
        if db.query(User).filter(User.email == req.email).first():
            raise HTTPException(400, detail="Email already registered")
    if db.query(User).filter(User.username == req.username).first():
        raise HTTPException(400, detail="Username already exists")

    # If same email is re-submitting, just overwrite — no username conflict check
    if req.email and req.email not in pending_store:
        if any(p["username"] == req.username for p in pending_store.values()):
            raise HTTPException(400, detail="Username already taken")

    otp = gen_otp()
    pending_store[req.email] = {
        "first_name":      req.first_name,
        "last_name":       req.last_name,
        "username":        req.username,
        "hashed_password": req.password.strip(),
        "otp":             otp,
        "expires_at":      datetime.utcnow() + timedelta(minutes=10)
    }

    send_email(req.email, "Verify your Droidal account",
               verification_email_html(otp, req.first_name or req.username))

    return {"status": "otp_sent", "message": "Check your email for the verification code."}


# REPLACE your /api/login with this:
@app.post("/api/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(
        (User.username == req.username) | (User.email == req.username)
    ).first()

    if not user:
        # No account at all — tell frontend to redirect to signup
        raise HTTPException(status_code=404, detail="No account found. Please sign up.")
    
    if user.password.strip() != req.password.strip():
        # Account exists but wrong password — suggest forgot password
        raise HTTPException(status_code=401, detail="Wrong password.")
    
    if not user.verified:
        raise HTTPException(status_code=403, detail="Email not verified. Please check your inbox.")

    return {
        "user_id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name
    }



@app.post("/api/verify-email")
def verify_email(payload: dict, db: Session = Depends(get_db)):
    email = payload.get("email")
    code  = payload.get("code")
    pending = pending_store.get(email)

    if not pending:
        raise HTTPException(400, detail="No pending registration for this email.")
    if datetime.utcnow() > pending["expires_at"]:
        del pending_store[email]
        raise HTTPException(410, detail="OTP expired. Please sign up again.")
    if pending["otp"] != code:
        raise HTTPException(400, detail="Invalid code.")

    # ✅ Only NOW create the real user in DB
    new_user = User(
        first_name = pending["first_name"],
        last_name  = pending["last_name"],
        email      = email,
        username   = pending["username"],
        password   = pending["hashed_password"],
        verified   = 1
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    del pending_store[email]

    return {"status": "success", "user_id": new_user.id}


@app.post("/api/resend-verification")
def resend_verification(payload: dict):
    email   = payload.get("email")
    pending = pending_store.get(email)

    if not pending:
        raise HTTPException(404, detail="No pending registration. Please sign up again.")

    otp = gen_otp()
    pending_store[email]["otp"]        = otp
    pending_store[email]["expires_at"] = datetime.utcnow() + timedelta(minutes=10)

    send_email(email, "Your new Droidal verification code",
               verification_email_html(otp, pending["first_name"] or pending["username"]))

    return {"message": "New code sent."}


@app.post("/api/forgot-password")
def forgot_password(payload: dict, request: Request, db: Session = Depends(get_db)):
    email = payload.get("email")
    print(f"=== FORGOT PASSWORD DEBUG ===")
    print(f"Email entered: '{email}'")
    
    # Print ALL users in DB so we can see what's there
    all_users = db.query(User).all()
    print(f"Total users in DB: {len(all_users)}")
    for u in all_users:
        print(f"  → id={u.id} username={u.username} email={u.email} verified={u.verified}")
    print(f"=============================")

    user = db.query(User).filter(User.email == email).first()
    if not user:
        return {"message": "No account found.", "found": False}

    token = secrets.token_urlsafe(32)
    reset_tokens[token] = {
        "email":      email,
        "expires_at": datetime.utcnow() + timedelta(minutes=15)
    }
    base_url = str(request.base_url).rstrip("/")
    reset_url = f"{base_url}/reset-password?token={token}"
    send_email(email, "Reset your Droidal password",
               reset_email_html(reset_url, user.first_name or user.username))

    return {"message": "Reset link sent.", "found": True}



@app.post("/api/reset-password")
def reset_password(payload: dict, db: Session = Depends(get_db)):
    token = payload.get("token")
    new_password = payload.get("new_password")
    stored = reset_tokens.get(token)

    if not stored:
        raise HTTPException(400, detail="Invalid or already used token.")
    if datetime.utcnow() > stored["expires_at"]:
        del reset_tokens[token]
        raise HTTPException(410, detail="Reset link expired.")

    user = db.query(User).filter(User.email == stored["email"]).first()
    if not user:
        raise HTTPException(404, detail="User not found.")

    user.password = new_password.strip()  # plain text, same as signup
    db.commit()
    del reset_tokens[token]

    return {"message": "Password reset successfully."}

# --- PROJECTS API ---
@app.get("/api/projects/{user_id}")
def get_projects(user_id: int, db: Session = Depends(get_db)):
    return db.query(Project).filter(Project.user_id == user_id).all()

@app.get("/api/project/{p_id}")
def get_project(p_id: int, db: Session = Depends(get_db)):
    p = db.query(Project).filter(Project.id == p_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"id": p.id, "title": p.title, "data": p.data, 
            "share_id": p.share_id, "preview_bg": p.preview_bg or ""}

@app.post("/api/projects/save")
def save_project(proj: dict, db: Session = Depends(get_db)):
    p_id = proj.get("id")
    data_content = proj.get("data")
    preview_bg = proj.get("preview_bg", None)   # ← ADD
    
    if isinstance(data_content, str):
        import json
        data_content = json.loads(data_content)

    if p_id:
        db_p = db.query(Project).filter(Project.id == p_id).first()
        if db_p:
            db_p.data = data_content
            db_p.title = proj.get('title', db_p.title)
            if preview_bg is not None:             # ← ADD
                db_p.preview_bg = preview_bg       # ← ADD
            if not db_p.share_id:
                db_p.share_id = str(uuid4())
            db.commit()
            return {"status": "updated", "id": db_p.id, "share_id": db_p.share_id}
    
    new_p = Project(
        title=proj.get('title', 'Untitled'),
        project_type=proj.get('type', 'flipbook'),
        user_id=proj.get('user_id'),
        data=data_content,
        preview_bg=preview_bg,                     # ← ADD
        share_id=str(uuid4())
    )
    db.add(new_p)
    db.commit()
    db.refresh(new_p)
    return {"id": new_p.id, "share_id": new_p.share_id}
    
@app.post("/api/projects/save-bg")
def save_project_bg(payload: dict, db: Session = Depends(get_db)):
    share_id   = payload.get("share_id")
    project_id = payload.get("project_id")
    preview_bg = payload.get("preview_bg", "")

    if share_id:
        db_p = db.query(Project).filter(Project.share_id == share_id).first()
    elif project_id:
        try:
            db_p = db.query(Project).filter(Project.id == int(project_id)).first()
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid project_id")
        
    else:
        raise HTTPException(status_code=400, detail="No project identifier provided")

    if not db_p:
        raise HTTPException(status_code=404, detail="Project not found")

    db_p.preview_bg = preview_bg
    db.commit()
    return {"status": "saved", "share_id": db_p.share_id}



# --- CUSTOM ELEMENTS API ---
@app.post("/api/custom-elements/save")
def save_custom_element(payload: dict, db: Session = Depends(get_db)):
    """Saves a custom element, including its JSON data and thumbnail."""
    new_el = CustomElement(
        user_id=payload["user_id"],
        name=payload.get("name", "Untitled"),
        data=payload["element_data"],
        thumbnail=payload.get("thumbnail") 
    )
    db.add(new_el)
    db.commit()
    db.refresh(new_el)
    return {"status": "saved"}

@app.get("/api/custom-elements/{user_id}")
def get_custom_elements(user_id: int, db: Session = Depends(get_db)):
    return db.query(CustomElement).filter(CustomElement.user_id == user_id).all()


class MediaSaveRequest(BaseModel):
    user_id: int
    media: dict   # { image: [...], video: [...] }

@app.post("/api/media/save")
def save_media(req: MediaSaveRequest, db: Session = Depends(get_db)):
    row = db.query(UserMedia).filter(UserMedia.user_id == req.user_id).first()
    if row:
        row.media_data = req.media
    else:
        row = UserMedia(user_id=req.user_id, media_data=req.media)
        db.add(row)
    db.commit()
    return {"ok": True}

@app.get("/api/media/load")
def load_media(user_id: int, db: Session = Depends(get_db)):
    row = db.query(UserMedia).filter(UserMedia.user_id == user_id).first()
    if not row or not row.media_data:
        return {"image": [], "video": []}
    return row.media_data

@app.post("/api/pages/save")
def save_pages(req: PagesSaveRequest, db: Session = Depends(get_db)):
    row = db.query(UserPages).filter(
        UserPages.user_id == req.user_id,
        UserPages.book_id == req.book_id
    ).first()
    if row:
        row.pages_data = req.pages
        row.meta_data  = req.meta
    else:
        row = UserPages(
            user_id    = req.user_id,
            book_id    = req.book_id,
            pages_data = req.pages,
            meta_data  = req.meta
        )
        db.add(row)
    db.commit()
    return {"ok": True}

@app.get("/api/pages/load")
def load_pages(user_id: int, book_id: str, db: Session = Depends(get_db)):
    row = db.query(UserPages).filter(
        UserPages.user_id == user_id,
        UserPages.book_id == book_id
    ).first()
    if not row:
        return {"pages": [], "meta": {}}
    return {"pages": row.pages_data, "meta": row.meta_data or {}}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)