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

# --- Настройки
TOKEN = "8475192387:AAESFlpUUqJzlqPTQkcAv1sDVeZJSFOQV0w"
POLICY_PDF = "policy.pdf"
CONSENT_PDF = "consent2.pdf"
OFFER_PDF = "ПУБЛИЧНОЕ ПРЕДЛОЖЕНИЕ (ОФЕРТА).pdf"
EXCEL_FILE = "consents.xlsx"
ADMINS = [1227847495, 5791748471]  # <-- список админов

WEBHOOK_HOST = "https://web-production-4d0f4.up.railway.app"
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

logging.basicConfig(level=logging.INFO)

pdfmetrics.registerFont(TTFont("DejaVu", "DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DejaVu-Bold", "DejaVuSans-Bold.ttf"))

router = Router()

AGREEMENT_TEXT = (
    "Здравствуйте! Ознакомьтесь с документами (PDF), затем выберите действие:"
)

class ConsentStates(StatesGroup):
    waiting_fio = State()
    waiting_inn = State()

@router.message(CommandStart())
async def start_handler(m: Message):
    kb = [
        [
            {"text": "📄 Политика (PDF)", "callback_data": "policy_pdf"},
            {"text": "📝 Согласие (PDF)", "callback_data": "consent_pdf"}
        ],
        [
            {"text": "📜 Оферта (PDF)", "callback_data": "offer_pdf"}
        ],
        [
            {"text": "✅ Согласен", "callback_data": "agree"},
            {"text": "❌ Не согласен", "callback_data": "disagree"}
        ]
    ]
    await m.answer(
        AGREEMENT_TEXT,
        reply_markup={"inline_keyboard": kb}
    )

@router.callback_query(F.data == "policy_pdf")
async def send_policy(c: CallbackQuery):
    await c.message.answer_document(
        FSInputFile(POLICY_PDF),
        caption="Политика конфиденциальности"
    )

@router.callback_query(F.data == "consent_pdf")
async def send_consent(c: CallbackQuery):
    await c.message.answer_document(
        FSInputFile(CONSENT_PDF),
        caption="Текст согласия"
    )

@router.callback_query(F.data == "offer_pdf")
async def send_offer(c: CallbackQuery):
    await c.message.answer_document(
        FSInputFile(OFFER_PDF),
        caption="Публичное предложение (ОФЕРТА)"
    )

@router.callback_query(F.data == "agree")
async def consent_agree_handler(c: CallbackQuery, state: FSMContext):
    await c.message.answer("Пожалуйста, введите свои ФИО полностью (пример: Иванов Иван Иванович)")
    await state.set_state(ConsentStates.waiting_fio)
    await c.answer()

@router.message(ConsentStates.waiting_fio)
async def get_fio(m: Message, state: FSMContext):
    fio = m.text.strip()
    await state.update_data(fio=fio)
    await m.answer("Теперь введите свой ИНН:")
    await state.set_state(ConsentStates.waiting_inn)

@router.message(ConsentStates.waiting_inn)
async def get_inn(m: Message, state: FSMContext, bot: Bot):
    inn = m.text.strip()
    data = await state.get_data()
    fio = data.get('fio', '')

    user = m.from_user
    status = "Согласен"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
    cpdf.drawString(100, 650, f"Актуальные документы: {POLICY_PDF}, {CONSENT_PDF}, {OFFER_PDF}")
    cpdf.save()

    await m.answer_document(FSInputFile(pdf_name), caption="Ваше подтверждение в PDF")
    os.remove(pdf_name)

    await m.answer("Спасибо! Ваши данные сохранены.")
    await state.clear()

    admin_msg = f"{user.full_name or user.username} выбрал: согласен\nФИО: {fio}\nИНН: {inn}"
    # уведомление всем админам
    for admin_id in ADMINS:
        await bot.send_message(admin_id, admin_msg)

@router.callback_query(F.data == "disagree")
async def consent_disagree_handler(c: CallbackQuery):
    user = c.from_user
    status = "Не согласен"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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

@router.message(Command("report"))
async def report(m: Message):
    if m.from_user.id not in ADMINS:
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

async def on_startup(bot: Bot):
    await bot.set_webhook(WEBHOOK_URL)

async def on_shutdown(bot: Bot):
    await bot.delete_webhook()

async def main():
    import asyncio
    bot = Bot(TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

    app = web.Application()
    app["bot"] = bot

    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    app.router.add_post(WEBHOOK_PATH, webhook_handler.handle)

    setup_application(app, dp, bot=bot)

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
