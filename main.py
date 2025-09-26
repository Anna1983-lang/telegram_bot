import logging
import os
import shutil
from datetime import datetime

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiohttp import web
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Логирование
logging.basicConfig(level=logging.INFO)

# --- Настройки
TOKEN = "8475192387:AAESFlpUUqJzlqPTQkcAv1sDVeZJSFOQV0w"
POLICY_PDF = "policy.pdf"
CONSENT_PDF = "consent2.pdf"
EXCEL_FILE = "consents.xlsx"
ADMIN_ID = 1227847495

# --- Шрифты для PDF
pdfmetrics.registerFont(TTFont("DejaVu", "DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DejaVu-Bold", "DejaVuSans-Bold.ttf"))

WEBHOOK_HOST = "https://telegram-bot-hdtw.onrender.com"
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

router = Router()

# --- Текст согласия
AGREEMENT_TEXT = """
🔒 Согласие на обработку персональных данных

Нажимая «Согласен», вы подтверждаете, что даёте согласие
на обработку ваших персональных данных в соответствии
с политикой конфиденциальности.
"""

# --- FSM: Состояния для ввода ФИО и ИНН
class ConsentStates(StatesGroup):
    waiting_fio = State()
    waiting_inn = State()

# --- Команда старт
@router.message(CommandStart())
async def start_handler(m: Message):
    kb = [[
        {"text": "✅ Согласен", "callback_data": "agree"},
        {"text": "❌ Не согласен", "callback_data": "disagree"}
    ]]
    await m.answer(AGREEMENT_TEXT, reply_markup={"inline_keyboard": kb})

# --- Пользователь выбрал "Согласен" - просим ввести ФИО
@router.callback_query(F.data == "agree")
async def consent_agree_handler(c: CallbackQuery, state: FSMContext):
    await c.message.answer("Пожалуйста, введите свои ФИО полностью (пример: Иванов Иван Иванович)")
    await state.set_state(ConsentStates.waiting_fio)
    await c.answer()

# --- Ввод ФИО
@router.message(ConsentStates.waiting_fio)
async def get_fio(m: Message, state: FSMContext):
    fio = m.text.strip()
    await state.update_data(fio=fio)
    await m.answer("Теперь введите свой ИНН:")
    await state.set_state(ConsentStates.waiting_inn)

# --- Ввод ИНН и финализация (Excel, PDF, уведомление админу)
@router.message(ConsentStates.waiting_inn)
async def get_inn(m: Message, state: FSMContext, bot: Bot):
    inn = m.text.strip()
    data = await state.get_data()
    fio = data.get('fio', '')

    user = m.from_user
    status = "Согласен"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # --- Запись в Excel (openpyxl)
    import openpyxl
    if not os.path.exists(EXCEL_FILE):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["User ID", "Username", "Имя", "ФИО", "ИНН", "Статус", "Время"])
    else:
        wb = openpyxl.load_workbook(EXCEL_FILE)
        ws = wb.active

    ws.append([user.id, user.username, user.full_name, fio, inn, status, timestamp])
    wb.save(EXCEL_FILE)

    # --- PDF
    pdf_name = f"confirm_{user.id}_{int(datetime.now().timestamp())}.pdf"
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    cpdf = canvas.Canvas(pdf_name, pagesize=A4)
    cpdf.setFont("DejaVu", 12)
    cpdf.drawString(100, 800, "Подтверждение согласия")
    cpdf.drawString(100, 770, f"User ID: {user.id}")
    cpdf.drawString(100, 750, f"Имя: {user.full_name}")
    cpdf.drawString(100, 730, f"ФИО: {fio}")
    cpdf.drawString(100, 710, f"ИНН: {inn}")
    cpdf.drawString(100, 690, f"Статус: {status}")
    cpdf.drawString(100, 670, f"Время: {timestamp}")
    cpdf.drawString(100, 650, f"Актуальные документы: {POLICY_PDF}, {CONSENT_PDF}")
    cpdf.save()

    await m.answer_document(FSInputFile(pdf_name), caption="Ваше подтверждение в PDF")
    os.remove(pdf_name)

    await m.answer("Спасибо! Ваши данные сохранены.")
    await state.clear()

    # --- Уведомление админу
    admin_msg = f"{user.full_name or user.username} выбрал: согласен\nФИО: {fio}\nИНН: {inn}"
    await bot.send_message(ADMIN_ID, admin_msg)

# --- Пользователь выбрал "Не согласен"
@router.callback_query(F.data == "disagree")
async def consent_disagree_handler(c: CallbackQuery):
    user = c.from_user
    status = "Не согласен"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Запись в Excel (без ФИО и ИНН)
    import openpyxl
    if not os.path.exists(EXCEL_FILE):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["User ID", "Username", "Имя", "ФИО", "ИНН", "Статус", "Время"])
    else:
        wb = openpyxl.load_workbook(EXCEL_FILE)
        ws = wb.active

    ws.append([user.id, user.username, user.full_name, "", "", status, timestamp])
    wb.save(EXCEL_FILE)

    await c.message.edit_text("Спасибо, ваш выбор зафиксирован.")
    await c.answer()

# --- Отправка PDF документов пользователю
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

# --- Webhook запуск
async def on_startup(bot: Bot):
    await bot.set_webhook(WEBHOOK_URL)

async def on_shutdown(bot: Bot):
    await bot.delete_webhook()

async def main():
    import asyncio
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

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
