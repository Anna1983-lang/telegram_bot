# main.py
import asyncio
import os
import logging
from datetime import datetime
from textwrap import wrap
from aiohttp import web

from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, FSInputFile, Update

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Настройки ----------
TOKEN = os.environ.get("TELEGRAM_TOKEN", "8475192387:AAESFlpUUqJzlqPTQkcAv1sDVeZJSFOQV0w")

# несколько админов
ADMIN_IDS = [1227847495, 5791748471]

WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # пример: https://telegram-bot.onrender.com/webhook/8475192387
BOT_ID_PREFIX = TOKEN.split(":")[0]
WEBHOOK_PATH = f"/webhook/{BOT_ID_PREFIX}"

POLICY_PDF = "policy.pdf"
CONSENT_PDF = "consent2.pdf"
EXCEL_FILE = "consents.xlsx"

# ---------- aiogram v3 ----------
router = Router()
dp = Dispatcher()
dp.include_router(router)
bot = Bot(TOKEN)

# ---------- Шрифты для кириллицы ----------
pdfmetrics.registerFont(TTFont("DejaVuSans", "DejaVuSans.ttf"))

# ---------- Excel/PDF утилиты ----------
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

def replace_excel_entry(path: str, ts: str, user, status: str):
    init_excel_if_needed(path)
    wb = load_workbook(path)
    ws = wb.active

    # удаляем старые строки пользователя
    rows_to_delete = []
    for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        try:
            uid = int(row[1])
        except Exception:
            continue
        if uid == user.id:
            rows_to_delete.append(i)
    for i in reversed(rows_to_delete):
        ws.delete_rows(i)

    # добавляем новую запись
    ws.append([ts, user.id, user.username or "", user.first_name or "", user.last_name or "", status])
    wb.save(path)

def make_confirmation_pdf(filename: str, user, status: str, ts: str) -> str:
    c = canvas.Canvas(filename, pagesize=A4)
    width, height = A4
    y = height - 40
    c.setFont("DejaVuSans", 14)
    c.drawString(40, y, "Подтверждение выбора по согласию на обработку ПДн")
    y -= 26
    c.setFont("DejaVuSans", 11)
    header = [
        f"Выбор: {status}",
        f"Дата и время: {ts}",
        f"Telegram: @{user.username}" if user.username else f"Telegram user_id: {user.id}",
        f"ФИО: {(user.first_name or '')} {(user.last_name or '')}".strip(),
        f"Документы: {POLICY_PDF} / {CONSENT_PDF}"
    ]
    for hl in header:
        for line in wrap(hl, 100):
            c.drawString(40, y, line)
            y -= 16
    y -= 8
    body = ("Настоящим подтверждается зафиксированное волеизъявление пользователя в электронном виде. "
            "Содержание согласия и политики конфиденциальности предоставлено пользователю в виде файлов PDF.")
    for line in wrap(body, 100):
        if y < 60:
            c.showPage()
            y = height - 40
            c.setFont("DejaVuSans", 11)
        c.drawString(40, y, line)
        y -= 16
    c.save()
    return filename

# ---------- Хэндлеры ----------
@router.message(CommandStart())
async def start(m: Message):
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="📄 Политика (PDF)", callback_data="policy_pdf"),
         types.InlineKeyboardButton(text="📝 Согласие (PDF)", callback_data="consent_pdf")],
        [types.InlineKeyboardButton(text="✅ Согласен", callback_data="agree"),
         types.InlineKeyboardButton(text="❌ Не согласен", callback_data="disagree")]
    ])
    await m.answer("Здравствуйте! Ознакомьтесь с документами (PDF), затем выберите действие:", reply_markup=kb)

@router.message(Command("id"))
async def whoami(m: Message):
    await m.answer(f"Ваш ID: {m.from_user.id}")

@router.message(Command("ping"))
async def ping(m: Message):
    await m.answer("pong")

@router.callback_query(F.data == "policy_pdf")
async def send_policy_pdf(c: CallbackQuery):
    if not os.path.exists(POLICY_PDF):
        await c.answer("Файл policy.pdf не найден.", show_alert=True)
        return
    await c.message.answer_document(FSInputFile(POLICY_PDF), caption="Политика конфиденциальности (PDF)")
    await c.answer()

@router.callback_query(F.data == "consent_pdf")
async def send_consent_pdf(c: CallbackQuery):
    if not os.path.exists(CONSENT_PDF):
        await c.answer(f"Файл {CONSENT_PDF} не найден.", show_alert=True)
        return
    await c.message.answer_document(FSInputFile(CONSENT_PDF), caption="Текст согласия (PDF)")
    await c.answer()

@router.callback_query(F.data.in_({"agree", "disagree"}))
async def consent_handler(c: CallbackQuery):
    user = c.from_user
    action = c.data
    status = "Согласен" if action == "agree" else "Не согласен"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    replace_excel_entry(EXCEL_FILE, ts, user, status)

    if status == "Согласен":
        tmp_pdf = f"confirmation_{user.id}.pdf"
        try:
            make_confirmation_pdf(tmp_pdf, user, status, ts)
            await c.message.edit_text(f"Спасибо! Ваш выбор зафиксирован: {status}")
            await c.message.answer_document(FSInputFile(tmp_pdf), caption="Ваше подтверждение (PDF)")
        finally:
            if os.path.exists(tmp_pdf):
                os.remove(tmp_pdf)
    else:
        await c.message.edit_text("Отказ зафиксирован.")

    # уведомление админам
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, f"Пользователь {user.id} ({user.username}) выбрал: {status}")
        except Exception:
            pass

    await c.answer()

@router.message(Command("help"))
async def help_cmd(m: Message):
    await m.answer("Команды:\n• /start\n• /id\n• /ping\n• /report — отчёт (только админ)\n• /clear — очистка (только админ)")

@router.message(Command("report"))
async def report_cmd(m: Message):
    if m.from_user.id not in ADMIN_IDS:
        await m.answer("Команда доступна только администратору.")
        return
    if not os.path.exists(EXCEL_FILE):
        await m.answer("Отчёт пуст (файл не найден).")
        return
    await m.answer_document(FSInputFile(EXCEL_FILE), caption="Отчёт по согласиям (Excel)")

@router.message(Command("clear"))
async def clear_cmd(m: Message):
    if m.from_user.id not in ADMIN_IDS:
        await m.answer("Команда доступна только администратору.")
        return
    if os.path.exists(EXCEL_FILE):
        os.remove(EXCEL_FILE)
    init_excel_if_needed(EXCEL_FILE)
    await m.answer("Все согласия очищены.")

# ---------- Webhook HTTP сервер ----------
async def on_startup(_app: web.Application):
    if not WEBHOOK_URL:
        logger.error("WEBHOOK_URL не задан.")
        return
    await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
    logger.info("Webhook установлен: %s", WEBHOOK_URL)

async def on_shutdown(_app: web.Application):
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await bot.session.close()
    except Exception:
        pass

async def handle(request: web.Request):
    data = await request.json()
    update = Update.model_validate(data)
    asyncio.create_task(dp.feed_update(bot, update))
    return web.Response(text="ok")

async def root(_request: web.Request):
    return web.Response(text="ok")

async def healthz(_request: web.Request):
    return web.Response(text="ok")

def create_app():
    app = web.Application()
    app.router.add_get("/", root)
    app.router.add_get("/healthz", healthz)
    app.router.add_post(WEBHOOK_PATH, handle)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_shutdown)
    return app

if __name__ == "__main__":
    if not WEBHOOK_URL:
        raise SystemExit("ERROR: WEBHOOK_URL is not set")
    app = create_app()
    port = int(os.environ.get("PORT", 10000))
    web.run_app(app, host="0.0.0.0", port=port)
