import os
import shutil
from uuid import uuid4
import uuid
from fastapi import FastAPI, Depends, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Text, create_engine, Column, String, Integer, JSON, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from pydantic import BaseModel
from typing import List, Optional

# --- DATABASE SETUP ---
DATABASE_URL = "sqlite:///./droidal.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- DATABASE MODELS ---
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password = Column(String)

class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    project_type = Column(String)
    user_id = Column(Integer, ForeignKey("users.id"))
    data = Column(JSON) 

class CustomElement(Base):
    __tablename__ = "custom_elements"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    name = Column(String, nullable=True)
    data = Column(JSON)
    thumbnail = Column(Text)

Base.metadata.create_all(bind=engine)

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

# Ensure the folder exists
if not os.path.exists(VIDEO_DIR):
    os.makedirs(VIDEO_DIR)

# --- SCHEMAS ---
class LoginRequest(BaseModel):
    username: str
    password: str

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

@app.get("/dashboard")
def get_dashboard(): 
    return FileResponse(os.path.join(FRONTEND_DIR, "dashboard.html"))

@app.get("/editor")
def get_editor(): 
    return FileResponse(os.path.join(FRONTEND_DIR, "editor.html"))

@app.get("/preview")
def get_preview():
    return FileResponse(os.path.join(FRONTEND_DIR, "flip.html"))

# Static file mounting for frontend assets and uploaded media
app.mount("/frontend", StaticFiles(directory=FRONTEND_DIR), name="frontend")
app.mount("/uploads", StaticFiles(directory=VIDEO_DIR), name="uploads")

# --- VIDEO UPLOAD API ---
@app.post("/upload_video")
async def upload_video(video: UploadFile = File(...)):
    file_extension = video.filename.split(".")[-1]
    unique_filename = f"{uuid.uuid4()}.{file_extension}"
    
    # Save to backend/videos/
    file_path = os.path.join(VIDEO_DIR, unique_filename)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(video.file, buffer)
    
    # The URL returned to the frontend should still start with /uploads/
    return {"url": f"/uploads/{unique_filename}"}



# --- AUTH API ---
@app.post("/api/signup")
def signup(req: LoginRequest, db: Session = Depends(get_db)):
    existing_user = db.query(User).filter(User.username == req.username).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Username already exists")
    
    new_user = User(username=req.username, password=req.password)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"status": "success", "user_id": new_user.id}

@app.post("/api/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == req.username, User.password == req.password).first()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return {"user_id": user.id, "username": user.username}

# --- PROJECTS API ---
@app.get("/api/projects/{user_id}")
def get_projects(user_id: int, db: Session = Depends(get_db)):
    return db.query(Project).filter(Project.user_id == user_id).all()

@app.get("/api/project/{p_id}")
def get_project(p_id: int, db: Session = Depends(get_db)):
    return db.query(Project).filter(Project.id == p_id).first()

@app.post("/api/projects/save")
def save_project(proj: dict, db: Session = Depends(get_db)):
    p_id = proj.get("id")
    data_content = proj.get("data")
    
    if isinstance(data_content, str):
        import json
        data_content = json.loads(data_content)

    if p_id:
        db_p = db.query(Project).filter(Project.id == p_id).first()
        if db_p:
            db_p.data = data_content
            db_p.title = proj.get('title', db_p.title)
            db.commit()
            return {"status": "updated", "id": db_p.id}
    
    new_p = Project(
        title=proj.get('title', 'Untitled'), 
        project_type=proj.get('type', 'flipbook'), 
        user_id=proj.get('user_id'), 
        data=data_content
    )
    db.add(new_p)
    db.commit()
    db.refresh(new_p)
    return {"id": new_p.id}

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)