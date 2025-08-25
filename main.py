# main.py
import asyncio
import logging
import os
from datetime import datetime
from textwrap import wrap

from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, FSInputFile

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Настройки ----------
# Токен: читаем из ENV, если нет — можно временно вставить строкой (не рекомендую)
TOKEN = os.environ.get("TELEGRAM_TOKEN", "8475192387:AAESFlpUUqJzlqPTQkcAv1sDVeZJSFOQV0w")
# ADMIN ID: читает из ENV или можно заменить на число
ADMIN_ID = int(os.environ.get("ADMIN_ID", "1227847495"))

POLICY_PDF = "policy.pdf"       # файл политики (в репо)
CONSENT_PDF = "consent2.pdf"    # файл согласия (в репо) — ты сказала он переименован
EXCEL_FILE = "consents.xlsx"    # единый Excel-файл

router = Router()

# ---------- Утилиты для Excel/PDF ----------
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

def read_last_status_for_user(path: str, user_id: int):
    """Возвращает последний статус для user_id или None."""
    if not os.path.exists(path):
        return None
    wb = load_workbook(path, read_only=True)
    ws = wb.active
    last = None
    for row in ws.iter_rows(min_row=2, values_only=True):
        # row: Timestamp, User ID, Username, First, Last, Status
        try:
            uid = int(row[1])
        except Exception:
            continue
        if uid == user_id:
            last = row[5]
    return last

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

def make_confirmation_pdf(filename: str, user, status: str, ts: str) -> str:
    c = canvas.Canvas(filename, pagesize=A4)
    width, height = A4
    y = height - 40

    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, y, "Подтверждение выбора по согласию на обработку персональных данных")
    y -= 26

    c.setFont("Helvetica", 11)
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
            c.setFont("Helvetica", 11)
        c.drawString(40, y, line)
        y -= 16

    c.save()
    return filename

# ---------- Хэндлеры ----------
@router.message(CommandStart())
async def start(m: Message):
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="📄 Политика (PDF)", callback_data="policy_pdf"),
            types.InlineKeyboardButton(text="📝 Согласие (PDF)", callback_data="consent_pdf"),
        ],
        [
            types.InlineKeyboardButton(text="✅ Согласен", callback_data="agree"),
            types.InlineKeyboardButton(text="❌ Не согласен", callback_data="disagree"),
        ],
    ])
    await m.answer(
        "Здравствуйте! Ознакомьтесь с документами (PDF), затем нажмите «✅ Согласен» или «❌ Не согласен».",
        reply_markup=kb
    )

@router.callback_query(F.data == "policy_pdf")
async def send_policy_pdf(c: CallbackQuery):
    if not os.path.exists(POLICY_PDF):
        await c.answer("Файл policy.pdf не найден рядом с ботом.", show_alert=True)
        return
    await c.message.answer_document(FSInputFile(POLICY_PDF), caption="Политика конфиденциальности (PDF)")
    await c.answer()

@router.callback_query(F.data == "consent_pdf")
async def send_consent_pdf(c: CallbackQuery):
    if not os.path.exists(CONSENT_PDF):
        await c.answer(f"Файл {CONSENT_PDF} не найден рядом с ботом.", show_alert=True)
        return
    await c.message.answer_document(FSInputFile(CONSENT_PDF), caption="Текст согласия (PDF)")
    await c.answer()

@router.callback_query(F.data.in_({"agree", "disagree"}))
async def consent_handler(c: CallbackQuery):
    user = c.from_user
    action = c.data
    status = "Согласен" if action == "agree" else "Не согласен"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Проверка предыдущего статуса
    last = read_last_status_for_user(EXCEL_FILE, user.id)
    # Если уже согласился — изменить нельзя
    if last == "Согласен":
        await c.answer("Ваш выбор уже зафиксирован: Согласен. Изменить нельзя.", show_alert=True)
        return

    # Если ранее был отказ — можно дать согласие только один раз.
    if last == "Не согласен":
        if action == "disagree":
            await c.answer("Ваш выбор уже зафиксирован: Не согласен. Изменить нельзя.", show_alert=True)
            return
        # если был "Не согласен" и сейчас нажали "agree" — разрешаем и фиксируем.

    # Записываем результат в Excel
    append_excel_entry(EXCEL_FILE, ts, user, status)

    # Ответ пользователю
    if status == "Согласен":
        tmp_pdf = f"confirmation_{user.id}.pdf"
        try:
            make_confirmation_pdf(tmp_pdf, user, status, ts)
            await c.message.edit_text(f"Спасибо! Ваш выбор зафиксирован: {status}")
            await c.message.answer_document(FSInputFile(tmp_pdf), caption="Ваше подтверждение (PDF)")
        except Exception as e:
            logger.exception("Ошибка при генерации PDF")
            await c.message.edit_text(f"Спасибо! Ваш выбор зафиксирован: {status} (ошибка генерации PDF)")
        finally:
            try:
                if os.path.exists(tmp_pdf):
                    os.remove(tmp_pdf)
            except Exception:
                pass
    else:
        await c.message.edit_text("Отказ зафиксирован. Если передумаете — отправьте /start и согласуйте заново.")

    await c.answer()

@router.message(Command("help"))
async def help_cmd(m: Message):
    await m.answer(
        "Команды:\n"
        "• /start — показать кнопки\n"
        "• /report — отправить Excel-отчёт администратору\n"
        "• Кнопки: Политика (PDF), Согласие (PDF), ✅/❌"
    )

@router.message(Command("report"))
async def report_cmd(m: Message):
    # Отправляем отчет только администратору
    if m.from_user.id != ADMIN_ID:
        await m.reply("Команда доступна только администратору.")
        return
    if not os.path.exists(EXCEL_FILE):
        await m.reply("Отчет пока пуст (файл не найден).")
        return
    await m.reply_document(FSInputFile(EXCEL_FILE), caption="Отчёт по согласиям (Excel)")

# ---------- Запуск ----------
async def main():
    bot = Bot(TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    # Удаляем webhook если вдруг (чтобы не мешал polling)
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        pass
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
