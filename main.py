from fastapi import FastAPI
from database import init_db
import admin

app = FastAPI()

# Инициализация базы данных при старте
init_db()

# Подключаем роутер админ-панели
app.include_router(admin.router)

@app.get("/")
def read_root():
    return {"status": "Server is running perfectly"}
