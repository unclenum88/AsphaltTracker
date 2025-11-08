import os
import sys
import uvicorn
import threading
import webbrowser
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import create_engine, Column, Integer, String, DateTime, JSON, ForeignKey
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
import datetime, io, csv, json as _json
from passlib.context import CryptContext
from jose import jwt, JWTError
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from typing import List, Optional

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "asphalt.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False}, future=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    email = Column(String, unique=True, index=True, nullable=True)
    password_hash = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class Car(Base):
    __tablename__ = 'cars'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True)
    rarity = Column(String)
    base_stats = Column(JSON)

class PlayerMatch(Base):
    __tablename__ = 'player_matches'
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    car_id = Column(Integer, ForeignKey('cars.id'), nullable=True)
    track = Column(String)
    position = Column(Integer)
    lap_times = Column(JSON)
    telemetry = Column(JSON, nullable=True)
    notes = Column(String, nullable=True)
    occurred_at = Column(DateTime, default=datetime.datetime.utcnow)
    user = relationship('User')
    car = relationship('Car')

# Create tables if not exist
Base.metadata.create_all(bind=engine)

# Pydantic schemas
class UserCreate(BaseModel):
    username: str
    email: Optional[str] = None
    password: str

class UserLogin(BaseModel):
    username: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class CarSpec(BaseModel):
    name: str
    rarity: Optional[str] = None
    base_stats: dict

class MatchIn(BaseModel):
    user_id: int
    car_id: Optional[int]
    track: str
    position: int
    lap_times: List[float]
    notes: Optional[str] = None
    occurred_at: Optional[datetime.datetime] = None

# Auth config
SECRET_KEY = os.environ.get("ASPHALT_SECRET", "dev-secret-change-me")
ALGORITHM = "HS256"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/token")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def verify_password(plain, hashed):
    return pwd_context.verify(plain, hashed)

def get_password_hash(p):
    return pwd_context.hash(p)

def create_access_token(data: dict):
    return jwt.encode(data, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(token: str = Depends(oauth2_scheme), db = Depends(get_db)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub"))
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.query(User).get(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user

app = FastAPI(title="Asphalt Companion (Windows single-file)")

# Serve static frontend
frontend_dir = BASE_DIR / "frontend" / "dist"
if not frontend_dir.exists():
    frontend_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")

# Simple root serving index.html if present
@app.get("/", response_class=HTMLResponse)
def index():
    f = frontend_dir / "index.html"
    if f.exists():
        return FileResponse(str(f))
    return HTMLResponse("<h1>Asphalt Companion</h1><p>Frontend missing. Use /docs to access API docs.</p>")

# Endpoints
@app.post("/api/register")
def register(payload: UserCreate, db = Depends(get_db)):
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(status_code=400, detail="username taken")
    hashed = get_password_hash(payload.password)
    u = User(username=payload.username, email=payload.email, password_hash=hashed)
    db.add(u); db.commit(); db.refresh(u)
    return {"id": u.id, "username": u.username}

@app.post("/api/token", response_model=Token)
def login(form_data: UserLogin, db = Depends(get_db)):
    user = db.query(User).filter(User.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Bad credentials")
    token = create_access_token({"sub": str(user.id)})
    return {"access_token": token, "token_type": "bearer"}

@app.post("/api/cars")
def add_car(car: CarSpec, db = Depends(get_db)):
    c = Car(name=car.name, rarity=car.rarity, base_stats=car.base_stats)
    db.add(c); db.commit(); db.refresh(c)
    return {"id": c.id, "name": c.name}

@app.get("/api/users/{user_id}/stats")
def user_stats(user_id: int, db = Depends(get_db)):
    matches = db.query(PlayerMatch).filter(PlayerMatch.user_id == user_id).all()
    if not matches:
        return {"matches": 0, "wins": 0, "avg_position": None}
    wins = sum(1 for m in matches if m.position == 1)
    avg_pos = sum(m.position for m in matches) / len(matches)
    return {"matches": len(matches), "wins": wins, "avg_position": avg_pos}

@app.post("/api/matches/upload")
def upload_matches(file: UploadFile = File(...), current_user: User = Depends(get_current_user), db = Depends(get_db)):
    content = file.file.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(content))
    created = 0
    for row in reader:
        lap_times = [float(x) for x in row.get("lap_times","").split(";") if x.strip()]
        telemetry = {"nitro_used": float(row.get("nitro_used", 0))}
        car = db.query(Car).filter(Car.name == row.get("car_name")).first()
        car_id = car.id if car else None
        m = PlayerMatch(user_id=current_user.id, car_id=car_id, track=row.get("track"), position=int(row.get("position",0)), lap_times=lap_times, telemetry=telemetry)
        db.add(m); created += 1
    db.commit()
    return {"created": created}

# Simple seeder if no cars exist - adds a few example cars
def seed_cars():
    db = SessionLocal()
    try:
        cnt = db.query(Car).count()
        if cnt == 0:
            sample = [
                {"name": "Falcon GT", "rarity": "Epic", "base_stats": {"speed":780,"acceleration":95,"nitro_capacity":100}},
                {"name": "Viper X", "rarity": "Legend", "base_stats": {"speed":820,"acceleration":92,"nitro_capacity":110}},
            ]
            for s in sample:
                c = Car(name=s["name"], rarity=s["rarity"], base_stats=s["base_stats"])
                db.add(c)
            db.commit()
    finally:
        db.close()

seed_cars()

def open_browser_later(url):
    def _open():
        import time; time.sleep(1)
        webbrowser.open(url)
    threading.Thread(target=_open, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    url = f"http://127.0.0.1:{port}"
    open_browser_later(url)
    uvicorn.run("run_app:app", host="0.0.0.0", port=port, reload=False)
