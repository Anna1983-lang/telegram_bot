# main.py
import asyncio
import logging
import os
from datetime import datetime
from textwrap import wrap

from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, FSInputFile

from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ---------- Логирование ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Шрифты для PDF ----------
pdfmetrics.registerFont(TTFont("DejaVu", "DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DejaVu-Bold", "DejaVuSans-Bold.ttf"))

# ---------- Настройки ----------
TOKEN = os.environ.get("TELEGRAM_TOKEN", "8475192387:TEST")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "1227847495,5791748471").split(",")]

POLICY_PDF = "consent.pdf"
CONSENT_PDF = "consent2.pdf"
EXCEL_FILE = "consents.xlsx"

# ---------- Excel ----------
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
    ws.append([ts, user.id, user.username or "", user.first_name or "", user.last_name or "", status])
    wb.save(path)

def clear_excel(path: str):
    wb = Workbook()
    ws = wb.active
    ws.title = "Consents"
    ws.append(["Timestamp", "User ID", "Username", "First name", "Last name", "Status"])
    widths = [20, 15, 25, 20, 20, 15]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    wb.save(path)

# ---------- PDF ----------
def make_confirmation_pdf(filename: str, user, status: str, ts: str) -> str:
    c = canvas.Canvas(filename, pagesize=A4)
    width, height = A4
    y = height - 40

    c.setFont("DejaVu-Bold", 14)
    c.drawString(40, y, "Подтверждение выбора по согласию на обработку ПДн")
    y -= 26

    c.setFont("DejaVu", 11)
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
    body = ("Настоящим подтверждается зафиксированное волеизъявление пользователя "
            "в электронном виде. Содержание согласия и политики конфиденциальности "
            "предоставлено пользователю в виде файлов PDF.")
    for line in wrap(body, 100):
        if y < 60:
            c.showPage()
            y = height - 40
            c.setFont("DejaVu", 11)
        c.drawString(40, y, line)
        y -= 16

    c.save()
    return filename

# ---------- Bot ----------
router = Router()

@router.message(CommandStart())
async def start(m: Message):
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="📄 Политика (PDF)", callback_data="policy_pdf"),
         types.InlineKeyboardButton(text="📝 Согласие (PDF)", callback_data="consent_pdf")],
        [types.InlineKeyboardButton(text="✅ Согласен", callback_data="agree"),
         types.InlineKeyboardButton(text="❌ Не согласен", callback_data="disagree")]
    ])
    await m.answer("Здравствуйте! Ознакомьтесь с документами (PDF), затем выберите действие:", reply_markup=kb)

@router.callback_query(F.data == "policy_pdf")
async def send_policy(c: CallbackQuery):
    await c.message.answer_document(FSInputFile(POLICY_PDF), caption="Политика конфиденциальности (PDF)")
    await c.answer()

@router.callback_query(F.data == "consent_pdf")
async def send_consent(c: CallbackQuery):
    await c.message.answer_document(FSInputFile(CONSENT_PDF), caption="Текст согласия (PDF)")
    await c.answer()

@router.callback_query(F.data.in_({"agree", "disagree"}))
async def consent(c: CallbackQuery):
    user = c.from_user
    status = "Согласен" if c.data == "agree" else "Не согласен"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    append_excel_entry(EXCEL_FILE, ts, user, status)

    tmp_pdf = f"confirmation_{user.id}.pdf"
    make_confirmation_pdf(tmp_pdf, user, status, ts)
    await c.message.answer_document(FSInputFile(tmp_pdf), caption="Подтверждение (PDF)")
    os.remove(tmp_pdf)

    for admin in ADMIN_IDS:
        try:
            await c.bot.send_message(admin, f"📢 {user.first_name} ({user.id}) выбрал: {status}")
        except Exception:
            pass

    await c.answer("Ответ зафиксирован ✅")

@router.message(Command("report"))
async def report(m: Message):
    if m.from_user.id not in ADMIN_IDS:
        return await m.answer("Команда только для админов.")
    if not os.path.exists(EXCEL_FILE):
        return await m.answer("Нет данных.")
    await m.answer_document(FSInputFile(EXCEL_FILE), caption="Отчёт (Excel)")

@router.message(Command("clear"))
async def clear(m: Message):
    if m.from_user.id not in ADMIN_IDS:
        return await m.answer("Команда только для админов.")
    clear_excel(EXCEL_FILE)
    await m.answer("📑 Отчёт очищен ✅")

@router.message(Command("help"))
async def help_cmd(m: Message):
    await m.answer("Команды:\n/start\n/report (админ)\n/clear (админ)")

# ---------- Run ----------
async def main():
    bot = Bot(TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
