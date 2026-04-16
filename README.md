import random
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form
from pydantic import BaseModel, EmailStr
from sqlalchemy import create_engine, Column, Integer, String, Boolean, Float, DateTime, ForeignKey, Table
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship

# --- НАСТРОЙКИ ---
DATABASE_URL = "sqlite:///./site_data.db" # База данных в файле
VK_TOKEN = "твой_токен_вк"
VK_CHAT_ID = "1" # ID чата в ВК

# --- БД И МОДЕЛИ ---
Base = declarative_base()
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class UserDB(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True)
    email = Column(String, unique=True)
    password = Column(String)
    is_approved = Column(Boolean, default=False) # Одобрение админом
    is_admin = Column(Boolean, default=False)
    verification_code = Column(String, nullable=True)

class ReportDB(Base):
    __tablename__ = "reports"
    id = Column(Integer, primary_key=True)
    moderator_name = Column(String)
    target_nickname = Column(String)
    description = Column(String, nullable=True)
    file_path = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

class ProductDB(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    price = Column(Float)
    drop_chance = Column(Integer, default=10) # Шанс в кейсе

Base.metadata.create_all(bind=engine)
app = FastAPI(title="Moderation Manager System")

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def send_email_code(email: str, code: str):
    print(f"📧 [EMAIL] Отправлен код {code} на почту {email}")

def send_vk_log(message: str):
    print(f"🔵 [VK LOG] {message}")
    # Тут будет код: requests.get(f"https://api.vk.com/method/messages.send?message={message}...")

# --- API ЭНДПОИНТЫ ---

# 1. Регистрация
@app.post("/register")
def register(username: str, email: EmailStr, password: str):
    db = SessionLocal()
    code = str(random.randint(100000, 999999))
    new_user = UserDB(username=username, email=email, password=password, verification_code=code)
    db.add(new_user)
    db.commit()
    send_email_code(email, code)
    send_vk_log(f"Новая регистрация: {username}. Ожидает подтверждения кода и админа.")
    return {"message": "Код отправлен на почту. После ввода кода ждите одобрения админа."}

# 2. Одобрение пользователя (Только для Админа)
@app.post("/admin/approve/{user_id}")
def approve_user(user_id: int, admin_id: int):
    db = SessionLocal()
    admin = db.query(UserDB).filter(UserDB.id == admin_id).first()
    if not admin or not admin.is_admin:
        raise HTTPException(status_code=403, detail="Нет прав")
    
    user = db.query(UserDB).filter(UserDB.id == user_id).first()
    user.is_approved = True
    db.commit()
    send_vk_log(f"✅ Пользователь {user.username} одобрен админом {admin.username}")
    return {"status": "User approved"}

# 3. Создание отчета
@app.post("/reports/create")
async def create_report(
    moderator_id: int, 
    target_nickname: str = Form(...), 
    description: str = Form(None),
    file: UploadFile = File(...)
):
    db = SessionLocal()
    mod = db.query(UserDB).filter(UserDB.id == moderator_id).first()
    if not mod or not mod.is_approved:
        raise HTTPException(status_code=403, detail="Доступ запрещен или аккаунт не одобрен")

    file_location = f"files/{file.filename}"
    # Сохранение файла (упрощенно)
    with open(file_location, "wb") as f:
        f.write(await file.read())

    report = ReportDB(
        moderator_name=mod.username,
        target_nickname=target_nickname,
        description=description,
        file_path=file_location
    )
    db.add(report)
    db.commit()

    # Дублирование в ВК
    log_msg = f"📝 ОТЧЕТ: {mod.username} -> {target_nickname}\nСуть: {description}\nФайл: {file_location}"
    send_vk_log(log_msg)
    
    return {"status": "Report created"}

# 4. Магазин: Удаление/Изменение (Админ)
@app.put("/admin/product/{item_id}")
def update_product(item_id: int, name: str, price: float, admin_id: int):
    db = SessionLocal()
    admin = db.query(UserDB).filter(UserDB.id == admin_id).first()
    if not admin.is_admin: raise HTTPException(status_code=403)
    
    item = db.query(ProductDB).filter(ProductDB.id == item_id).first()
    item.name = name
    item.price = price
    db.commit()
    return {"status": "Updated"}

# 5. Магазин: Кейс (Рандом)
@app.post("/shop/open-case")
def open_case(user_id: int):
    db = SessionLocal()
    products = db.query(ProductDB).all()
    if not products: return {"error": "Магазин пуст"}
    
    # Логика шансов
    won_item = random.choices(
        products, 
        weights=[p.drop_chance for p in products], 
        k=1
    )[0]
    
    send_vk_log(f"🎁 Юзер {user_id} выбил из кейса: {won_item.name}")
    return {"win": won_item.name}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
