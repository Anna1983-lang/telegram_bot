import logging
import os
import csv
import shutil
from datetime import datetime

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiohttp import web
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Логирование
logging.basicConfig(level=logging.INFO)

# 🔑 Токен
TOKEN = "8475192387:AAESFlpUUqJzlqPTQkcAv1sDVeZJSFOQV0w"

# 🔒 Файлы согласия
POLICY_PDF = "policy.pdf"
CONSENT_PDF = "consent2.pdf"
EXCEL_FILE = "consents.xlsx"

# 🛡️ ID администратора
ADMIN_ID = 1227847495

# Подключаем шрифты
pdfmetrics.registerFont(TTFont("DejaVu", "DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DejaVu-Bold", "DejaVuSans-Bold.ttf"))

router = Router()

# --- Стартовое сообщение
AGREEMENT_TEXT = """
🔒 Согласие на обработку персональных данных

Нажимая «Согласен», вы подтверждаете, что даёте согласие
на обработку ваших персональных данных в соответствии
с политикой конфиденциальности.
"""

@router.message(CommandStart())
async def start_handler(m: Message):
    kb = [[
        {"text": "✅ Согласен", "callback_data": "agree"},
        {"text": "❌ Не согласен", "callback_data": "disagree"}
    ]]
    await m.answer(AGREEMENT_TEXT,
                   reply_markup={"inline_keyboard": kb})

# --- Обработка согласия
@router.callback_query(F.data.in_({"agree", "disagree"}))
async def consent_handler(c: CallbackQuery):
    user = c.from_user
    status = "Согласен" if c.data == "agree" else "Не согласен"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Записываем в Excel (CSV-совместимо)
    import openpyxl
    if not os.path.exists(EXCEL_FILE):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["User ID", "Username", "Имя", "Статус", "Время"])
    else:
        wb = openpyxl.load_workbook(EXCEL_FILE)
        ws = wb.active

    ws.append([user.id, user.username, user.full_name, status, timestamp])
    wb.save(EXCEL_FILE)

    # Отправляем PDF подтверждение
    pdf_name = f"confirm_{user.id}_{int(datetime.now().timestamp())}.pdf"
    cpdf = canvas.Canvas(pdf_name, pagesize=A4)
    cpdf.setFont("DejaVu", 12)
    cpdf.drawString(100, 800, "Подтверждение согласия")
    cpdf.drawString(100, 770, f"User ID: {user.id}")
    cpdf.drawString(100, 750, f"Имя: {user.full_name}")
    cpdf.drawString(100, 730, f"Статус: {status}")
    cpdf.drawString(100, 710, f"Время: {timestamp}")
    cpdf.drawString(100, 690, f"Актуальные документы: {POLICY_PDF}, {CONSENT_PDF}")
    cpdf.save()

    await c.message.answer_document(FSInputFile(pdf_name), caption="Ваше подтверждение в PDF")
    os.remove(pdf_name)

    await c.message.edit_text(f"Спасибо! Ваш выбор зафиксирован: {status}")
    await c.answer()

# --- Отправка файлов PDF пользователю
@router.callback_query(F.data == "policy_pdf")
async def send_policy(c: CallbackQuery):
    await c.message.answer_document(FSInputFile(POLICY_PDF), caption="Политика конфиденциальности")

@router.callback_query(F.data == "consent_pdf")
async def send_consent(c: CallbackQuery):
    await c.message.answer_document(FSInputFile(CONSENT_PDF), caption="Текст согласия")

# --- Отчёт админу
@router.message(Command("report"))
async def report(m: Message):
    if m.from_user.id != ADMIN_ID:
        await m.answer("⛔ У вас нет доступа")
        return
    if not os.path.exists(EXCEL_FILE):
        await m.answer("Файл ещё не создан")
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    temp_name = f"consents_{ts}.xlsx"
    shutil.copy(EXCEL_FILE, temp_name)
    await m.answer_document(FSInputFile(temp_name), caption="📊 Отчёт по согласиям")
    os.remove(temp_name)

# --- Запуск через webhook
async def on_startup(bot: Bot):
    await bot.set_webhook(WEBHOOK_URL)

async def on_shutdown(bot: Bot):
    await bot.delete_webhook()

TOKEN = TOKEN
WEBHOOK_HOST = "https://telegram-bot-hdtw.onrender.com"
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

async def main():
    bot = Bot(TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    app = web.Application()
    app["bot"] = bot
    app.router.add_post(WEBHOOK_PATH, dp.webhook_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 10000)))
    await site.start()

    await on_startup(bot)

    logging.info(f"Webhook запущен: {WEBHOOK_URL}")

    # держим процесс
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
