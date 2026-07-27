import asyncio
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

import pytz
from dotenv import load_dotenv
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError, async_playwright


LOGIN_URL = "https://usuarios.lcnidiomas.edu.co/auth/login"
SCHEDULE_URL = "https://usuarios.lcnidiomas.edu.co/schedules/schedule-class/"
BOGOTA_TZ = pytz.timezone("America/Bogota")
DEBUG_DIR = Path("debug")


@dataclass
class Settings:
    email: str
    password: str
    sede: str
    tipo_clase: str
    preferred_times: list[str]
    saturday_times: list[str]
    max_classes: int
    headless: bool
    poll_seconds: int
    run_start: time
    run_end: time


@dataclass
class TargetPlan:
    target: datetime
    times: list[str]


def parse_clock(value: str, default: str) -> time:
    raw_value = (value or default).strip()
    try:
        return datetime.strptime(raw_value, "%H:%M").time()
    except ValueError as exc:
        raise SystemExit(f"Hora invalida {raw_value!r}. Usa formato HH:MM, por ejemplo 06:00.") from exc


def read_settings() -> Settings:
    load_dotenv()
    email = os.getenv("LCN_EMAIL", "").strip()
    password = os.getenv("LCN_PASSWORD", "").strip()
    if not email or not password:
        raise SystemExit("Faltan LCN_EMAIL y/o LCN_PASSWORD. Copia .env.example a .env y completalo.")

    preferred_times = [
        value.strip()
        for value in os.getenv("LCN_PREFERRED_TIMES", "").split(",")
        if value.strip()
    ]
    saturday_times = [
        value.strip()
        for value in os.getenv("LCN_SATURDAY_TIMES", "").split(",")
        if value.strip()
    ]

    return Settings(
        email=email,
        password=password,
        sede=os.getenv("LCN_SEDE", "Sede Aves María (Sabaneta)").strip(),
        tipo_clase=os.getenv("LCN_TIPO_CLASE", "Clase").strip(),
        preferred_times=preferred_times,
        saturday_times=saturday_times,
        max_classes=int(os.getenv("LCN_MAX_CLASSES", "99")),
        headless=os.getenv("LCN_HEADLESS", "true").lower() == "true",
        poll_seconds=int(os.getenv("LCN_POLL_SECONDS", "600")),
        run_start=parse_clock(os.getenv("LCN_RUN_START", ""), "06:00"),
        run_end=parse_clock(os.getenv("LCN_RUN_END", ""), "19:10"),
    )


def default_weekday_times(settings: Settings) -> list[str]:
    return settings.preferred_times or [
        "6:00 AM",
        "7:30 AM",
        "9:00 AM",
        "10:30 AM",
        "12:00 PM",
        "1:30 PM",
        "3:00 PM",
        "4:30 PM",
        "6:00 PM",
        "7:00 PM",
    ]


def default_saturday_times(settings: Settings) -> list[str]:
    return settings.saturday_times or [
        "9:45 AM",
        "11:15 AM",
        "1:30 PM",
        "3:00 PM",
    ]


def target_times_for_date(settings: Settings, target: datetime) -> list[str]:
    if target.weekday() == 5:
        return default_saturday_times(settings)
    return default_weekday_times(settings)


def available_target_plans(settings: Settings, now: datetime | None = None) -> list[TargetPlan]:
    """Tomorrow and the day after tomorrow, skipping Sundays."""
    now = now or datetime.now(BOGOTA_TZ)

    if now.weekday() == 5:
        monday = BOGOTA_TZ.localize(datetime.combine(now.date() + timedelta(days=2), time()))
        return [TargetPlan(target=monday, times=target_times_for_date(settings, monday))]

    plans: list[TargetPlan] = []
    for days_ahead in (1, 2):
        target = BOGOTA_TZ.localize(datetime.combine(now.date() + timedelta(days=days_ahead), time()))
        if target.weekday() == 6:
            continue
        plans.append(TargetPlan(target=target, times=target_times_for_date(settings, target)))

    return plans


def colombia_datetime_for_today(clock: time) -> datetime:
    now = datetime.now(BOGOTA_TZ)
    return BOGOTA_TZ.localize(datetime.combine(now.date(), clock))


def seconds_until_active_window(settings: Settings) -> int:
    now = datetime.now(BOGOTA_TZ)
    today_start = colombia_datetime_for_today(settings.run_start)
    today_end = colombia_datetime_for_today(settings.run_end)

    if today_start <= now <= today_end:
        return 0
    if now < today_start:
        return max(1, int((today_start - now).total_seconds()))

    tomorrow_start = today_start + timedelta(days=1)
    return max(1, int((tomorrow_start - now).total_seconds()))


def seconds_until_next_attempt(settings: Settings) -> int:
    now = datetime.now(BOGOTA_TZ)
    today_start = colombia_datetime_for_today(settings.run_start)
    today_end = colombia_datetime_for_today(settings.run_end)

    elapsed = max(0, int((now - today_start).total_seconds()))
    interval = max(60, settings.poll_seconds)
    next_elapsed = ((elapsed // interval) + 1) * interval
    next_attempt = today_start + timedelta(seconds=next_elapsed)

    if next_attempt <= today_end:
        return max(1, int((next_attempt - now).total_seconds()))

    tomorrow_start = today_start + timedelta(days=1)
    return max(1, int((tomorrow_start - now).total_seconds()))


def normalize_time(text: str) -> str | None:
    match = re.search(r"(\d{1,2}):(\d{2})\s*([AP]\.?M\.?)?", text, re.IGNORECASE)
    if not match:
        return None
    hour = int(match.group(1))
    minute = match.group(2)
    meridiem = match.group(3)

    if meridiem:
        marker = meridiem.upper().replace(".", "")
        if marker == "AM":
            hour = 0 if hour == 12 else hour
        elif marker == "PM":
            hour = 12 if hour == 12 else hour + 12

    return f"{hour:02d}:{minute}"


def comparable_text(text: str) -> str:
    for _ in range(3):
        if "Ã" not in text and "Â" not in text:
            break
        try:
            repaired = text.encode("latin1").decode("utf-8")
        except UnicodeError:
            break
        if repaired == text:
            break
        text = repaired
    normalized = unicodedata.normalize("NFKD", text)
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", without_accents).strip().lower()


async def fill_first_matching(page: Page, selectors: list[str], value: str) -> None:
    last_error: Exception | None = None
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            await locator.wait_for(state="visible", timeout=15000)
            await locator.fill(value)
            current_value = await locator.input_value()
            if current_value != value:
                await locator.click()
                await locator.press("Control+A")
                await locator.type(value, delay=35)
            return
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"No pude llenar el campo con valor {value!r}") from last_error


async def click_first_matching(page: Page, selectors: list[str]) -> None:
    last_error: Exception | None = None
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            await locator.wait_for(state="visible", timeout=4000)
            await locator.click()
            return
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"No pude hacer clic en ninguno de estos selectores: {selectors}") from last_error


async def login(page: Page, settings: Settings) -> None:
    await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=90000)
    try:
        await page.wait_for_load_state("networkidle", timeout=20000)
    except PlaywrightTimeoutError:
        pass
    await page.wait_for_timeout(1000)
    await fill_first_matching(
        page,
        [
            "input[type='email']",
            "input[name='email']",
            "input[id*='email' i]",
            "input[name*='email' i]",
            "input[placeholder*='email' i]",
            "input:below(:text('Ingrese su email'))",
            "form input[type='text']",
        ],
        settings.email,
    )
    await fill_first_matching(
        page,
        [
            "input[type='password']",
            "input[name='password']",
            "input[id*='password' i]",
            "input[name*='password' i]",
            "input[placeholder*='contraseña' i]",
            "input:below(:text('Ingrese su contraseña'))",
        ],
        settings.password,
    )
    await click_first_matching(
        page,
        [
            "button:has-text('Iniciar sesión')",
            "input[type='submit']",
            "button[type='submit']",
        ],
    )
    try:
        await page.wait_for_url(lambda url: "/auth/login" not in url, timeout=12000)
    except PlaywrightTimeoutError:
        await page.wait_for_load_state("networkidle")

    if "/auth/login" in page.url:
        await save_debug(page, "login-failed")
        body_text = await page.locator("body").inner_text()
        short_text = " ".join(body_text.split())
        raise RuntimeError(
            "LCN no acepto el inicio de sesion o la pagina no avanzo. "
            f"Texto visible: {short_text[:300]}"
        )

    await page.wait_for_load_state("networkidle")


async def select_option_by_text(page: Page, label_text: str, option_text: str) -> None:
    label_xpath = (
        "xpath=//*[self::label or self::span or self::div or self::p or self::strong]"
        f"[contains(normalize-space(.), '{label_text}')]"
    )
    label = page.locator(label_xpath).last
    await label.wait_for(state="visible", timeout=8000)

    field = page.locator(
        f"xpath=({label_xpath.removeprefix('xpath=')})[last()]/following::*[self::select or self::input][1]"
    ).first
    await field.wait_for(state="visible", timeout=8000)

    tag_name = await field.evaluate("element => element.tagName.toLowerCase()")
    if tag_name == "select":
        options = await field.locator("option").all_inner_texts()
        wanted = comparable_text(option_text)
        wanted_words = [word for word in re.split(r"[^a-z0-9]+", wanted) if len(word) >= 3]
        selected_index = None

        for index, raw_option in enumerate(options):
            option = comparable_text(raw_option)
            if option == wanted or wanted in option or option in wanted:
                selected_index = index
                break
            if wanted_words and all(word in option for word in wanted_words):
                selected_index = index
                break
            common_words = sum(1 for word in wanted_words if word in option)
            required_words = min(len(wanted_words), 3)
            if wanted_words and common_words >= required_words:
                selected_index = index
                break

        if selected_index is None:
            raise RuntimeError(
                f"No encontre la opcion {option_text!r}. Opciones visibles: "
                + ", ".join(option.strip() for option in options if option.strip())
            )

        await field.evaluate(
            """(select, index) => {
                select.selectedIndex = index;
                select.dispatchEvent(new Event('input', { bubbles: true }));
                select.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            selected_index,
        )
        return

    await field.click()
    await page.get_by_text(option_text, exact=True).click(timeout=5000)


async def set_date(page: Page, target: datetime) -> None:
    date_value = target.strftime("%d-%m-%Y")
    date_input = page.locator("input[placeholder='AAAA/MM/DD'], input[type='date']").first
    await date_input.wait_for(state="visible", timeout=8000)
    await date_input.fill(date_value)
    await date_input.press("Enter")


async def choose_time(page: Page, desired_time: str | None = None) -> str | None:
    time_field = page.locator(
        "xpath=//*[contains(normalize-space(), 'Seleccionar Hora')]/following::*[self::select or self::input][1]"
    ).first
    await time_field.wait_for(state="visible", timeout=8000)
    await page.wait_for_timeout(700)

    tag_name = await time_field.evaluate("element => element.tagName.toLowerCase()")
    desired_normalized = normalize_time(desired_time) if desired_time else None

    if tag_name == "select":
        options = await time_field.locator("option").evaluate_all(
            """options => options.map((option, index) => ({
                index,
                text: option.innerText,
                disabled: option.disabled
            }))"""
        )

        for option in options:
            normalized = normalize_time(option["text"])
            if option["disabled"] or not normalized:
                continue
            if desired_normalized and normalized != desired_normalized:
                continue
            await time_field.evaluate(
                """(select, index) => {
                    select.selectedIndex = index;
                    select.dispatchEvent(new Event('input', { bubbles: true }));
                    select.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                option["index"],
            )
            return normalized

        if desired_normalized:
            return None

        for option in options:
            normalized = normalize_time(option["text"])
            if normalized and not option["disabled"]:
                await time_field.evaluate(
                    """(select, index) => {
                        select.selectedIndex = index;
                        select.dispatchEvent(new Event('input', { bubbles: true }));
                        select.dispatchEvent(new Event('change', { bubbles: true }));
                    }""",
                    option["index"],
                )
                return normalized
        return None

    await time_field.click()
    visible_options = await page.locator("[role='option'], .dropdown-item, li, option").all_inner_texts()
    if desired_normalized:
        for option in visible_options:
            if normalize_time(option) == desired_normalized:
                await page.get_by_text(option, exact=True).click()
                return desired_normalized
        return None

    for option in visible_options:
        normalized = normalize_time(option)
        if normalized:
            await page.get_by_text(option, exact=True).click()
            return normalized
    return None


async def prepare_schedule_form(page: Page, settings: Settings, target: datetime) -> None:
    await page.goto(SCHEDULE_URL, wait_until="networkidle")
    await page.wait_for_timeout(1000)
    await select_option_by_text(page, "Seleccionar sede", settings.sede)
    await select_option_by_text(page, "Seleccionar Tipo de Clase", settings.tipo_clase)
    await set_date(page, target)


async def try_schedule_time(page: Page, settings: Settings, target: datetime, desired_time: str) -> bool:
    await prepare_schedule_form(page, settings, target)

    selected_time = await choose_time(page, desired_time)
    if not selected_time:
        print(f"No encontre disponible {desired_time} para {target:%Y-%m-%d}.")
        return False

    print(f"Intentando agendar {target:%Y-%m-%d} a las {selected_time}.")
    await click_first_matching(page, ["button:has-text('AGENDAR')", "button:has-text('Agendar')"])

    try:
        await click_first_matching(
            page,
            [
                "button:has-text('Confirmar')",
                "button:has-text('Aceptar')",
                "button:has-text('Sí')",
                "button:has-text('Si')",
            ],
        )
    except RuntimeError:
        pass

    await page.wait_for_timeout(2000)
    page_text = await page.locator("body").inner_text()
    if "No disponible" in page_text or "No hay agendas disponibles" in page_text:
        print(f"No disponible despues de intentar {target:%Y-%m-%d} {selected_time}. Sigo con la siguiente hora.")
        return False

    success_words = ["agendada", "reservada", "exitosamente", "success"]
    if any(word.lower() in page_text.lower() for word in success_words):
        print(f"Clase agendada: {target:%Y-%m-%d} {selected_time}.")
        return True

    print("No pude confirmar si quedo agendada. Revisa Mis reservas.")
    return False


async def try_schedule_all_times(page: Page, settings: Settings, target: datetime, times_to_try: list[str]) -> int:
    scheduled = 0

    for desired_time in times_to_try:
        if scheduled >= settings.max_classes:
            break
        if await try_schedule_time(page, settings, target, desired_time):
            scheduled += 1
            await page.wait_for_timeout(1000)

    return scheduled


async def run_once(settings: Settings) -> int:
    scheduled = 0
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=settings.headless)
        page = await browser.new_page(viewport={"width": 1366, "height": 768})
        try:
            await login(page, settings)
        except Exception:
            await save_debug(page, "login-error")
            raise

        for plan in available_target_plans(settings):
            if scheduled >= settings.max_classes:
                break
            try:
                readable_times = ", ".join(plan.times)
                print(f"Buscando {plan.target:%Y-%m-%d} en estos horarios: {readable_times}.")
                scheduled += await try_schedule_all_times(page, settings, plan.target, plan.times)
            except PlaywrightTimeoutError as exc:
                print(f"La pagina no cargo como esperaba: {exc}")
                await save_debug(page, "timeout")
            except Exception as exc:
                print(f"Fallo intentando agendar {plan.target:%Y-%m-%d}: {exc}")
                await save_debug(page, "error")

        await browser.close()
    return scheduled


async def save_debug(page: Page, name: str) -> None:
    DEBUG_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(BOGOTA_TZ).strftime("%Y%m%d-%H%M%S")
    screenshot_path = DEBUG_DIR / f"{timestamp}-{name}.png"
    html_path = DEBUG_DIR / f"{timestamp}-{name}.html"
    await page.screenshot(path=str(screenshot_path), full_page=True)
    html_path.write_text(await page.content(), encoding="utf-8")
    print(f"Guarde depuracion en {screenshot_path} y {html_path}")


async def run_forever(settings: Settings) -> None:
    while True:
        wait_seconds = seconds_until_active_window(settings)
        if wait_seconds:
            next_run = datetime.now(BOGOTA_TZ) + timedelta(seconds=wait_seconds)
            print(f"Fuera de ventana. Proximo intento: {next_run:%Y-%m-%d %H:%M:%S} Colombia.")
            await asyncio.sleep(wait_seconds)

        now = datetime.now(BOGOTA_TZ).strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{now} Colombia] Revisando disponibilidad...")
        await run_once(settings)

        wait_seconds = seconds_until_next_attempt(settings)
        next_run = datetime.now(BOGOTA_TZ) + timedelta(seconds=wait_seconds)
        print(f"Proximo intento: {next_run:%Y-%m-%d %H:%M:%S} Colombia.")
        await asyncio.sleep(wait_seconds)


if __name__ == "__main__":
    settings = read_settings()
    continuous = os.getenv("LCN_CONTINUOUS", "false").lower() == "true"
    if continuous:
        asyncio.run(run_forever(settings))
    else:
        scheduled_count = asyncio.run(run_once(settings))
        print(f"Clases agendadas en esta ejecucion: {scheduled_count}")
