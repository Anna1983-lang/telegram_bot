import asyncio
import logging
import os
import shutil
import time
from datetime import datetime
from textwrap import wrap

from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, FSInputFile

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter

from aiohttp import web

logging.basicConfig(level=logging.INFO)

# 🔑 Токен
TOKEN = "8475192387:AAESFlpUUqJzlqPTQkcAv1sDVeZJSFOQV0w"

# 🔧 Файлы
POLICY_PDF = "policy.pdf"
CONSENT_PDF = "consent2.pdf"
EXCEL_FILE = "consents.xlsx"

# 🔧 ID администратора
ADMIN_ID = 1227847495

# 🔧 Webhook config
WEBHOOK_PATH = "/webhook"
BASE_WEB_URL = os.getenv("RENDER_EXTERNAL_URL", "https://telegram-bot-hdtw.onrender.com")
WEBHOOK_URL = f"{BASE_WEB_URL}{WEBHOOK_PATH}"

# Подключаем шрифты
pdfmetrics.registerFont(TTFont("DejaVu", "DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DejaVu-Bold", "DejaVuSans-Bold.ttf"))

router = Router()

# ───────────────────────── Excel ─────────────────────────
def init_excel_if_needed(path: str):
    if os.path.exists(path):
        return
    wb = Workbook()
    ws = wb.active
    ws.title = "Consents"
    ws.append(["Timestamp", "User ID", "Username", "First name", "Last name", "Status"])
    widths = [20, 15, 25, 20, 20, 15]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    wb.save(path)

def append_excel_entry(path: str, ts: str, user, status: str):
    init_excel_if_needed(path)
    wb = load_workbook(path)
    ws = wb.active
    ws.append([
        ts,
        user.id,
        user.username or "",
        user.first_name or "",
        user.last_name or "",
        status
    ])
    wb.save(path)

def get_user_status(path: str, user_id: int):
    if not os.path.exists(path):
        return None
    wb = load_workbook(path)
    ws = wb.active
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[1] == user_id:
            return row[5]  # Status
    return None

# ─────────────── PDF подтверждение (кириллица) ───────────
def make_confirmation_pdf(filename: str, user, status: str, ts: str) -> str:
    c = canvas.Canvas(filename, pagesize=A4)
    width, height = A4
    y = height - 40

    c.setFont("DejaVu-Bold", 14)
    c.drawString(40, y, "Подтверждение выбора по согласию на обработку ПДн")
    y -= 30

    c.setFont("DejaVu", 11)
    header = [
        f"Выбор: {status}",
        f"Дата и время: {ts}",
        f"Telegram: @{user.username}" if user.username else f"Telegram user_id: {user.id}",
        f"ФИО: {(user.first_name or '')} {(user.last_name or '')}".strip(),
        f"Актуальные версии документов: {POLICY_PDF} / {CONSENT_PDF}"
    ]
    for hl in header:
        for line in wrap(hl, 95):
            c.drawString(40, y, line)
            y -= 18
    y -= 10

    body = ("Настоящим подтверждается зафиксированное волеизъявление пользователя "
            "в электронном виде. Содержание согласия и политики конфиденциальности "
            f"предоставлено пользователю в виде файлов PDF ({POLICY_PDF}, {CONSENT_PDF}) "
            "до момента выражения согласия/отказа.")
    for line in wrap(body, 95):
        if y < 60:
            c.showPage()
            y = height - 40
            c.setFont("DejaVu", 11)
        c.drawString(40, y, line)
        y -= 18

    c.save()
    return filename

# ───────────────────────── БОТ ───────────────────────────
def start_keyboard():
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="📄 Политика (PDF)", callback_data="policy_pdf"),
            types.InlineKeyboardButton(text="📝 Согласие (PDF)", callback_data="consent_pdf"),
        ],
        [
            types.InlineKeyboardButton(text="✅ Согласен", callback_data="agree"),
            types.InlineKeyboardButton(text="❌ Не согласен", callback_data="disagree"),
        ],
    ])

@router.message(CommandStart())
async def start(m: Message):
    await m.answer(
        "Здравствуйте! Ознакомьтесь с документами (PDF), затем нажмите «✅ Согласен» или «❌ Не согласен».",
        reply_markup=start_keyboard()
    )

@router.message(Command("ping"))
async def ping(m: Message):
    await m.answer("pong ✅")

@router.callback_query(F.data == "policy_pdf")
async def send_policy_pdf(c: CallbackQuery):
    if not os.path.exists(POLICY_PDF):
        await c.answer("Файл policy.pdf не найден рядом с ботом.", show_alert=True)
        return
    f = FSInputFile(POLICY_PDF, filename=f"policy_{int(time.time())}.pdf")
    await c.message.answer_document(f, caption="Политика конфиденциальности (PDF)")
    await c.answer()

@router.callback_query(F.data == "consent_pdf")
async def send_consent_pdf(c: CallbackQuery):
    if not os.path.exists(CONSENT_PDF):
        await c.answer("Файл consent.pdf не найден рядом с ботом.", show_alert=True)
        return
    f = FSInputFile(CONSENT_PDF, filename=f"consent_{int(time.time())}.pdf")
    await c.message.answer_document(f, caption="Текст согласия (PDF)")
    await c.answer()

@router.callback_query(F.data.in_({"agree", "disagree"}))
async def consent_handler(c: CallbackQuery):
    user = c.from_user
    status = "Согласен" if c.data == "agree" else "Не согласен"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    existing_status = get_user_status(EXCEL_FILE, user.id)
    if existing_status == "Согласен":
        await c.answer("Ваш выбор уже зафиксирован: Согласен. Изменить нельзя.", show_alert=True)
        return
    elif existing_status == "Не согласен" and status == "Не согласен":
        await c.answer("Вы уже отказались ранее. Ответ зафиксирован.", show_alert=True)
        return

    append_excel_entry(EXCEL_FILE, ts, user, status)

    try:
        text = (f"Новый ответ!\n"
                f"ID: {user.id}\n"
                f"Имя: {(user.first_name or '')} {(user.last_name or '')}\n"
                f"Username: @{user.username}\n"
                f"Статус: {status}\n"
                f"Время: {ts}")
        await c.bot.send_message(ADMIN_ID, text)
    except Exception as e:
        logging.warning(f"Не удалось уведомить админа: {e}")

    if status == "Согласен":
        pdf_name = f"Подтверждение_{user.id}.pdf"
        make_confirmation_pdf(pdf_name, user, status, ts)
        await c.message.edit_text("Спасибо! Ваш выбор зафиксирован: Согласен")
        await c.message.answer_document(FSInputFile(pdf_name, filename=f"confirm_{int(time.time())}.pdf"),
                                        caption="Ваше подтверждение (PDF)")
        try: os.remove(pdf_name)
        except: pass
    else:
        await c.message.edit_text("Отказ зафиксирован. Если передумаете — сможете согласиться один раз.")

    await c.answer()

@router.message(Command("report"))
async def send_report(m: Message):
    if m.from_user.id != ADMIN_ID:
        await m.answer("⛔ Нет доступа")
        return
    if not os.path.exists(EXCEL_FILE):
        await m.answer("Файл consents.xlsx ещё не создан")
        return

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    temp_name = f"consents_{ts}.xlsx"
    shutil.copy(EXCEL_FILE, temp_name)

    await m.answer_document(FSInputFile(temp_name), caption="📊 Отчёт по согласиям")
    try: os.remove(temp_name)
    except: pass

# ───────────────────────── WEBHOOK ─────────────────────────
async def on_startup(bot: Bot):
    await bot.set_webhook(WEBHOOK_URL)

async def on_shutdown(bot: Bot):
    await bot.delete_webhook()

async def main():
    bot = Bot(TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    app = web.Application()
    app.router.add_post(WEBHOOK_PATH, dp.webhook_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 10000)))
    await site.start()

    await on_startup(bot)
    logging.info(f"Webhook set: {WEBHOOK_URL}")

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await on_shutdown(bot)

if __name__ == "__main__":
    asyncio.run(main())
