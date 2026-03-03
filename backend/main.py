import os
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, String, Integer, JSON, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from pydantic import BaseModel
from typing import Any, List, Optional

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
    name = Column(String)
    data = Column(JSON)  # Stores the Fabric.js object JSON
    thumbnail = Column(String, nullable=True) # Stores base64 image string

# Create tables
Base.metadata.create_all(bind=engine)

# --- APP SETUP ---
app = FastAPI()

# Enable CORS for frontend communication
app.add_middleware(
    CORSMiddleware, 
    allow_origins=["*"], 
    allow_methods=["*"], 
    allow_headers=["*"]
)

# Paths for Frontend (Assumes main.py is in /backend and html is in /frontend)
# Adjust this path if your folder structure is different
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

# --- SCHEMAS (Pydantic) ---
class LoginRequest(BaseModel):
    username: str
    password: str

class CustomElementCreate(BaseModel):
    user_id: int
    name: str
    element_data: Any  # From frontend: payload.element_data
    thumbnail: Optional[str] = None

class CustomElementResponse(BaseModel):
    id: int
    user_id: int
    name: str
    data: Any          # Maps to the DB 'data' column
    thumbnail: Optional[str] = None

    class Config:
        from_attributes = True

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

# Serve static files (CSS, JS, Images)
app.mount("/frontend", StaticFiles(directory=FRONTEND_DIR), name="frontend")

# --- API ROUTES ---

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

@app.get("/api/projects/{user_id}")
def get_projects(user_id: int, db: Session = Depends(get_db)):
    return db.query(Project).filter(Project.user_id == user_id).all()

@app.get("/api/project/{p_id}")
def get_project(p_id: int, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == p_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project

@app.post("/api/projects/save")
def save_project(proj: dict, db: Session = Depends(get_db)):
    p_id = proj.get("id")
    if p_id:
        db_p = db.query(Project).filter(Project.id == p_id).first()
        if db_p:
            db_p.data = proj.get("data")
            db_p.title = proj.get("title", db_p.title)
            db.commit()
            return {"status": "updated", "id": db_p.id}
    
    new_p = Project(
        title=proj.get('title', 'Untitled'), 
        project_type=proj.get('type', 'flipbook'), 
        user_id=proj.get('user_id'), 
        data=proj.get('data', [])
    )
    db.add(new_p)
    db.commit()
    db.refresh(new_p)
    return {"id": new_p.id}

# --- CUSTOM ELEMENTS ROUTES ---

@app.post("/api/custom-elements/save")
def save_custom_element(element: CustomElementCreate, db: Session = Depends(get_db)):
    try:
        new_el = CustomElement(
            user_id=element.user_id,
            name=element.name,
            data=element.element_data, # Correctly maps element_data to DB data column
            thumbnail=element.thumbnail
        )
        db.add(new_el)
        db.commit()
        db.refresh(new_el)
        return {"status": "success", "id": new_el.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/custom-elements/{user_id}", response_model=List[CustomElementResponse])
def get_custom_elements(user_id: int, db: Session = Depends(get_db)):
    # Returns an empty list [] if none found, satisfying frontend .forEach()
    elements = db.query(CustomElement).filter(CustomElement.user_id == user_id).all()
    return elements

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)