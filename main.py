# main.py
import asyncio
import os
import logging
from datetime import datetime
from textwrap import wrap
from typing import List, Optional, Tuple

from aiohttp import web, ClientSession

from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, FSInputFile, Update

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- ENV / SETTINGS ----------
TOKEN = os.environ.get("TELEGRAM_TOKEN", "8475192387:AAESFlpUUqJzlqPTQkcAv1sDVeZJSFOQV0w")

# несколько админов через запятую (ENV: ADMIN_IDS="1227847495,5791748471")
ADMIN_IDS = {
    int(x) for x in os.environ.get("ADMIN_IDS", "1227847495,5791748471").replace(" ", "").split(",") if x
}

WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # напр.: https://...onrender.com/webhook/8475192387
BOT_ID_PREFIX = TOKEN.split(":")[0]
WEBHOOK_PATH = f"/webhook/{BOT_ID_PREFIX}"

POLICY_PDF = "policy.pdf"
CONSENT_PDF = "consent2.pdf"
EXCEL_FILE = "consents.xlsx"

# шрифты для кириллицы в PDF
PDF_FONT_REGULAR_PATH = os.environ.get("PDF_FONT_REGULAR", "fonts/DejaVuSans.ttf")
PDF_FONT_BOLD_PATH    = os.environ.get("PDF_FONT_BOLD",    "fonts/DejaVuSans-Bold.ttf")
PDF_FONT_REGULAR_NAME = "DejaVuSans"
PDF_FONT_BOLD_NAME    = "DejaVuSans-Bold"

# ---------- aiogram v3 ----------
router = Router()
dp = Dispatcher()
dp.include_router(router)
bot = Bot(TOKEN)

# ---------- Excel утилиты ----------
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

def read_all_rows(path: str):
    if not os.path.exists(path):
        return []
    wb = load_workbook(path, read_only=True)
    ws = wb.active
    rows = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue  # пропускаем заголовок
        rows.append(row)  # (ts, user_id, username, first_name, last_name, status)
    return rows

def read_last_status_for_user(path: str, user_id: int) -> Optional[str]:
    last = None
    for row in read_all_rows(path):
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
    ws.append([ts, user.id, user.username or "", user.first_name or "", user.last_name or "", status])
    wb.save(path)

def rewrite_excel(path: str, rows: List[tuple]):
    wb = Workbook()
    ws = wb.active
    ws.title = "Consents"
    ws.append(["Timestamp", "User ID", "Username", "First name", "Last name", "Status"])
    widths = [20, 15, 25, 20, 20, 15]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for r in rows:
        ws.append(list(r))
    wb.save(path)

def filter_rows_by_period(rows: List[tuple], start: Optional[datetime], end: Optional[datetime]) -> List[tuple]:
    if not start and not end:
        return rows
    out = []
    for r in rows:
        try:
            ts = datetime.strptime(str(r[0]), "%Y-%m-%d %H:%M:%S")
        except Exception:
            try:
                ts = datetime.fromisoformat(str(r[0]))
            except Exception:
                continue
        if start and ts < start:
            continue
        if end and ts > end:
            continue
        out.append(r)
    return out

def parse_period(text_after_command: str) -> Tuple[Optional[datetime], Optional[datetime]]:
    """
    Поддерживает:
      ''                              -> весь период
      '2025-08-25'                    -> за день
      '2025-08-01 00:00 2025-08-31 23:59' -> произвольный интервал
    """
    s = text_after_command.strip()
    if not s:
        return None, None
    parts = s.split()
    fmt_date = "%Y-%m-%d"
    fmt_dt = "%Y-%m-%d %H:%M"
    try:
        if len(parts) == 1:
            start = datetime.strptime(parts[0], fmt_date)
            end = datetime.strptime(parts[0], fmt_date).replace(hour=23, minute=59, second=59)
            return start, end
        elif len(parts) >= 2:
            try:
                start = datetime.strptime(" ".join(parts[0:2]), fmt_dt)
                if len(parts) >= 4:
                    end = datetime.strptime(" ".join(parts[2:4]), fmt_dt)
                else:
                    end = None
            except ValueError:
                start = datetime.strptime(parts[0], fmt_date)
                end = datetime.strptime(parts[1], fmt_date).replace(hour=23, minute=59, second=59)
            return start, end
    except Exception:
        return None, None

# ---------- PDF (кириллица) ----------
def _register_pdf_fonts():
    registered = set(pdfmetrics.getRegisteredFontNames())
    try:
        if PDF_FONT_REGULAR_NAME not in registered and os.path.exists(PDF_FONT_REGULAR_PATH):
            pdfmetrics.registerFont(TTFont(PDF_FONT_REGULAR_NAME, PDF_FONT_REGULAR_PATH))
        if PDF_FONT_BOLD_NAME not in registered and os.path.exists(PDF_FONT_BOLD_PATH):
            pdfmetrics.registerFont(TTFont(PDF_FONT_BOLD_NAME, PDF_FONT_BOLD_PATH))
    except Exception:
        logger.exception("Не удалось зарегистрировать TTF-шрифты для PDF")

def make_confirmation_pdf(filename: str, user, status: str, ts: str) -> str:
    _register_pdf_fonts()

    c = canvas.Canvas(filename, pagesize=A4)
    width, height = A4
    y = height - 40
    try:
        c.setFont(PDF_FONT_BOLD_NAME, 14)
    except Exception:
        c.setFont("Helvetica-Bold", 14)
    c.drawString(40, y, "Подтверждение выбора по согласию на обработку ПДн")
    y -= 26
    try:
        c.setFont(PDF_FONT_REGULAR_NAME, 11)
    except Exception:
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
            try:
                c.setFont(PDF_FONT_REGULAR_NAME, 11)
            except Exception:
                c.setFont("Helvetica", 11)
            y = height - 40
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
    await m.answer("Здравствуйте! Ознакомьтесь с документами (PDF), затем нажмите «✅ Согласен» или «❌ Не согласен».", reply_markup=kb)

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

    last = read_last_status_for_user(EXCEL_FILE, user.id)
    if last == "Согласен":
        await c.answer("Ваш выбор уже зафиксирован: Согласен. Изменить нельзя.", show_alert=True)
        return
    if last == "Не согласен" and action == "disagree":
        await c.answer("Ваш выбор уже зафиксирован: Не согласен. Изменить нельзя.", show_alert=True)
        return

    append_excel_entry(EXCEL_FILE, ts, user, status)

    # уведомление всем админам
    text = (f"🆕 Новый выбор по согласию\n"
            f"Статус: {status}\n"
            f"Время: {ts}\n"
            f"User: {user.id} @{user.username or '—'} {user.first_name or ''} {user.last_name or ''}".strip())
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            logger.exception("Не удалось отправить уведомление админу %s", admin_id)

    if status == "Согласен":
        tmp_pdf = f"confirmation_{user.id}.pdf"
        try:
            make_confirmation_pdf(tmp_pdf, user, status, ts)
            await c.message.edit_text(f"Спасибо! Ваш выбор зафиксирован: {status}")
            await c.message.answer_document(FSInputFile(tmp_pdf), caption="Ваше подтверждение (PDF)")
        except Exception:
            logger.exception("Ошибка при генерации PDF")
            await c.message.edit_text(f"Спасибо! Ваш выбор зафиксирован: {status} (ошибка при создании PDF).")
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
        "• /start\n"
        "• /id\n"
        "• /ping\n"
        "• /report [YYYY-MM-DD] | [YYYY-MM-DD HH:MM YYYY-MM-DD HH:MM]\n"
        "• /clear_all — очистить все записи (админ)\n"
        "• /clear_user <user_id> — удалить записи пользователя (админ)"
    )

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

@router.message(Command("report"))
async def report_cmd(m: Message):
    if not is_admin(m.from_user.id):
        await m.answer("Команда доступна только администратору.")
        return
    if not os.path.exists(EXCEL_FILE):
        await m.answer("Отчёт пока пуст (файл не найден).")
        return

    text_after = m.text.split(" ", 1)[1] if " " in m.text else ""
    start, end = parse_period(text_after)

    rows = read_all_rows(EXCEL_FILE)
    rows = filter_rows_by_period(rows, start, end)

    if not rows:
        await m.answer("За указанный период записей нет.")
        return

    try:
        tmp = "report_filtered.xlsx"
        rewrite_excel(tmp, rows)
        caption = "Отчёт по согласиям"
        if start or end:
            caption += f" (фильтр: {start or '…'} — {end or '…'})"
        await m.answer_document(FSInputFile(tmp), caption=caption)
    except Exception:
        logger.exception("Не удалось сформировать/отправить Excel")
        await m.answer("Ошибка при формировании отчёта. Проверьте логи.")
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

@router.message(Command("clear_all"))
async def clear_all_cmd(m: Message):
    if not is_admin(m.from_user.id):
        await m.answer("Команда доступна только администратору.")
        return
    rewrite_excel(EXCEL_FILE, [])
    await m.answer("Все записи очищены.")

@router.message(Command("clear_user"))
async def clear_user_cmd(m: Message):
    if not is_admin(m.from_user.id):
        await m.answer("Команда доступна только администратору.")
        return
    parts = m.text.strip().split()
    if len(parts) < 2 or not parts[1].isdigit():
        await m.answer("Использование: /clear_user <user_id>")
        return
    target = int(parts[1])
    rows = read_all_rows(EXCEL_FILE)
    new_rows = [r for r in rows if int(r[1]) != target]
    rewrite_excel(EXCEL_FILE, new_rows)
    await m.answer(f"Записи пользователя {target} удалены ({len(rows) - len(new_rows)} шт.).")

# ---------- Webhook / сервер ----------
async def on_startup(_app: web.Application):
    if not WEBHOOK_URL:
        logger.error("WEBHOOK_URL не задан (ENV).")
        return
    try:
        me = await bot.get_me()
        logger.info("Bot @%s (id=%s). ADMIN_IDS=%s", me.username, me.id, ADMIN_IDS)
        await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
        logger.info("Webhook установлен: %s", WEBHOOK_URL)
    except Exception:
        logger.exception("Не удалось установить webhook")

    # фоновая будилка self-ping /healthz каждые ~4 мин
    try:
        asyncio.create_task(_keepalive_task())
    except Exception:
        logger.exception("Не удалось запустить keepalive")

async def on_shutdown(_app: web.Application):
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook удалён")
    except Exception:
        logger.exception("Не удалось удалить webhook")
    try:
        await bot.session.close()
    except Exception:
        pass

async def _keepalive_task():
    if not WEBHOOK_URL:
        return
    base = WEBHOOK_URL.split("/webhook")[0]
    url = base + "/healthz"
    while True:
        try:
            async with ClientSession() as s:
                async with s.get(url, timeout=5) as r:
                    await r.text()
        except Exception:
            pass
        await asyncio.sleep(240)

async def handle(request: web.Request):
    # Получаем JSON от Telegram и передаём апдейт в диспетчер (aiogram 3)
    try:
        data = await request.json()
    except Exception:
        return web.Response(status=400, text="no json")
    try:
        update = Update.model_validate(data)  # pydantic v2
        asyncio.create_task(dp.feed_update(bot, update))  # не ждём — сразу 200 OK
        return web.Response(text="ok")
    except Exception:
        logger.exception("Ошибка обработки апдейта")
        return web.Response(status=500, text="error")

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
        logger.error("ERROR: WEBHOOK_URL not set.")
        raise SystemExit(1)
    app = create_app()
    port = int(os.environ.get("PORT", 10000))
    web.run_app(app, host="0.0.0.0", port=port)
