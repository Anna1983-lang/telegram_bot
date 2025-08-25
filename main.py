import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery
from aiohttp import web
import csv
from datetime import datetime
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import openpyxl

# Логирование
logging.basicConfig(level=logging.INFO)

# 🔑 Токен бота (оставь как есть или через Render → Environment Variables)
TOKEN = os.getenv("BOT_TOKEN", "8475192387:AAESFlpUUqJzlqPTQkcAv1sDVeZJSFOQV0w")

# 📄 файлы
EXCEL_FILE = "consents.xlsx"
CONSENT_FILE = "consent2.pdf"
POLICY_FILE = "policy.pdf"

# 👤 ID администратора
ADMIN_ID = 1227847495

# Подключаем шрифты
pdfmetrics.registerFont(TTFont("DejaVu", "DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DejaVu-Bold", "DejaVuSans-Bold.ttf"))

router = Router()

# ================== ОБРАБОТЧИКИ ==================
@router.message(CommandStart())
async def start_handler(m: Message):
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="✅ Согласен", callback_data="agree")],
            [types.InlineKeyboardButton(text="❌ Не согласен", callback_data="disagree")]
        ]
    )
    await m.answer("🔒 Согласие на обработку персональных данных\n\n"
                   "Нажимая «Согласен», вы подтверждаете согласие на обработку данных.", 
                   reply_markup=kb)

@router.callback_query(F.data.in_({"agree", "disagree"}))
async def consent_handler(c: CallbackQuery):
    user = c.from_user
    status = "Согласен" if c.data == "agree" else "Не согласен"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # сохраняем в Excel
    if not os.path.exists(EXCEL_FILE):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["UserID", "Username", "Status", "Timestamp"])
        wb.save(EXCEL_FILE)

    wb = openpyxl.load_workbook(EXCEL_FILE)
    ws = wb.active
    ws.append([user.id, user.username, status, timestamp])
    wb.save(EXCEL_FILE)

    await c.message.edit_text(f"Спасибо! Ваш выбор зафиксирован: {status}")
    await c.answer()

@router.message(lambda m: m.text == "/report")
async def report_handler(m: Message):
    if m.from_user.id != ADMIN_ID:
        await m.answer("⛔ У вас нет доступа.")
        return
    if os.path.exists(EXCEL_FILE):
        await m.answer_document(types.FSInputFile(EXCEL_FILE))
    else:
        await m.answer("Файл с согласиями пока пуст.")

# ================== ЗАПУСК ==================
async def on_startup(bot: Bot):
    await bot.set_webhook(WEBHOOK_URL)

async def on_shutdown(bot: Bot):
    await bot.delete_webhook()

async def main():
    bot = Bot(TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    # aiohttp webserver
    app = web.Application()
    app.router.add_post("/webhook", dp.callback_query)
    app.on_startup.append(lambda _: on_startup(bot))
    app.on_cleanup.append(lambda _: on_shutdown(bot))

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 10000)))
    await site.start()

    logging.info("🚀 Бот запущен на Render (webhook)")
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
