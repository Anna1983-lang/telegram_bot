import logging
import os
import shutil
from datetime import datetime
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiohttp import web
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

import openpyxl

# === НАСТРОЙКИ ===
TOKEN = "8475192387:AAESFlpUUqJzlqPTQkcAv1sDVeZJSFOQV0w"
POLICY_PDF = "policy.pdf"
CONSENT_PDF = "consent2.pdf"
OFFER_PDF = "ПУБЛИЧНОЕ ПРЕДЛОЖЕНИЕ (ОФЕРТА).pdf"
EXCEL_FILE = "consents.xlsx"
ADMIN_ID = 1227847495

# --- Регистрируем шрифты для PDF
pdfmetrics.registerFont(TTFont("DejaVu", "DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DejaVu-Bold", "DejaVuSans-Bold.ttf"))

# FSM для согласия (сбор ФИО и ИНН)
class ConsentForm(StatesGroup):
    waiting_for_fullname = State()
    waiting_for_inn = State()

# FSM-сторадж
storage = MemoryStorage()
router = Router()

# --- ТЕКСТЫ
AGREEMENT_TEXT = (
    "🔒 Согласие на обработку персональных данных\n\n"
    "Нажимая «Согласен», вы подтверждаете, что даёте согласие на обработку ваших персональных данных в соответствии с политикой конфиденциальности."
)

# --- СТАРТ
@router.message(CommandStart())
async def start_handler(m: Message, state: FSMContext):
    kb = [
        [{"text": "📄 Политика", "callback_data": "policy_pdf"},
         {"text": "📝 Согласие", "callback_data": "consent_pdf"},
         {"text": "📑 Оферта", "callback_data": "offer_pdf"}],
        [{"text": "✅ Согласен", "callback_data": "agree"},
         {"text": "❌ Не согласен", "callback_data": "disagree"}]
    ]
    await m.answer(AGREEMENT_TEXT, reply_markup={"inline_keyboard": kb})
    await state.clear()

# --- ОТПРАВКА PDF
@router.callback_query(F.data == "policy_pdf")
async def send_policy(c: CallbackQuery):
    await c.message.answer_document(FSInputFile(POLICY_PDF), caption="Политика конфиденциальности")
    await c.answer()

@router.callback_query(F.data == "consent_pdf")
async def send_consent(c: CallbackQuery):
    await c.message.answer_document(FSInputFile(CONSENT_PDF), caption="Текст согласия")
    await c.answer()

@router.callback_query(F.data == "offer_pdf")
async def send_offer(c: CallbackQuery):
    await c.message.answer_document(FSInputFile(OFFER_PDF), caption="Публичное предложение (ОФЕРТА)")
    await c.answer()

# --- ОБРАБОТКА СОГЛАСИЯ
@router.callback_query(F.data == "agree")
async def consent_agree(c: CallbackQuery, state: FSMContext):
    await state.set_state(ConsentForm.waiting_for_fullname)
    await c.message.edit_text("Пожалуйста, введите ФИО полностью (например: Иванов Иван Иванович):")
    await c.answer()

@router.message(ConsentForm.waiting_for_fullname)
async def get_fullname(m: Message, state: FSMContext):
    await state.update_data(fullname=m.text)
    await state.set_state(ConsentForm.waiting_for_inn)
    await m.answer("Теперь введите ИНН:")

@router.message(ConsentForm.waiting_for_inn)
async def get_inn(m: Message, state: FSMContext):
    data = await state.get_data()
    fullname = data["fullname"]
    inn = m.text.strip()
    user = m.from_user
    status = "Согласен"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Excel лог
    if not os.path.exists(EXCEL_FILE):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["User ID", "Username", "ФИО", "ИНН", "Статус", "Время"])
    else:
        wb = openpyxl.load_workbook(EXCEL_FILE)
        ws = wb.active
    ws.append([user.id, user.username or "", fullname, inn, status, timestamp])
    wb.save(EXCEL_FILE)

    # PDF-подтверждение
    pdf_name = f"confirm_{user.id}_{int(datetime.now().timestamp())}.pdf"
    cpdf = canvas.Canvas(pdf_name, pagesize=A4)
    cpdf.setFont("DejaVu", 12)
    cpdf.drawString(100, 800, "Подтверждение согласия")
    cpdf.drawString(100, 770, f"User ID: {user.id}")
    cpdf.drawString(100, 750, f"Имя: {fullname}")
    cpdf.drawString(100, 730, f"ИНН: {inn}")
    cpdf.drawString(100, 710, f"Статус: {status}")
    cpdf.drawString(100, 690, f"Время: {timestamp}")
    cpdf.drawString(100, 670, f"Актуальные документы: {POLICY_PDF}, {CONSENT_PDF}, {OFFER_PDF}")
    cpdf.save()

    await m.answer("Спасибо! Ваш выбор зафиксирован: Согласен")
    await m.answer_document(FSInputFile(pdf_name), caption="Ваше подтверждение (PDF)")
    os.remove(pdf_name)

    # Отправка админу
    try:
        admin_text = (
            f"Пользователь {user.full_name or user.username or user.id} выбрал: согласен\n"
            f"ФИО: {fullname}\nИНН: {inn}\nВремя: {timestamp}"
        )
        bot = m.bot
        await bot.send_message(ADMIN_ID, admin_text)
    except Exception:
        pass

    await state.clear()

@router.callback_query(F.data == "disagree")
async def consent_disagree(c: CallbackQuery):
    await c.message.edit_text("Отказ зафиксирован. Если передумаете — отправьте /start и согласуйте заново.")
    await c.answer()

# --- ОТЧЁТ ДЛЯ АДМИНА
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

# --- ЗАПУСК ДЛЯ RENDER / RAILWAY ---
TOKEN = TOKEN
WEBHOOK_HOST = os.environ.get("WEBHOOK_HOST", "")  # можно задать через переменную
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}" if WEBHOOK_HOST else None

async def main():
    bot = Bot(TOKEN)
    dp = Dispatcher(storage=storage)
    dp.include_router(router)

    # Для webhook (если не нужен — просто dp.start_polling(bot))
    app = web.Application()
    app["bot"] = bot
    app.router.add_post(WEBHOOK_PATH, dp.webhook_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 10000)))
    await site.start()
    if WEBHOOK_URL:
        await bot.set_webhook(WEBHOOK_URL)
    print(f"=== Webhook запущен: {WEBHOOK_URL} ===")

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
