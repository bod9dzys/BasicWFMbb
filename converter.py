import csv
import openpyxl
import re  # Додано для очищення username
from unidecode import unidecode  # Додано для транслітерації
from datetime import datetime, time, timedelta

# --- 1. НАЛАШТУВАННЯ ---

# Вхідний файл (ваш графік XLSX)
INPUT_FILE = 'october.xlsx'

# Назва аркуша (листа) в Excel.
SHEET_NAME = None  # (None = брати перший активний аркуш)

# Вихідний файл (готовий для імпорту в Django)
OUTPUT_FILE = 'import_for_wfm.csv'

# Мапа статусів: як називається статус у вашому файлі -> як він називається в Django
STATUS_MAP = {
    'Відпустка': 'vacation',
    'OFF': 'day_off',
    'вихідний': 'day_off',
    'Лікарняний': 'sick',
    # ... додайте інші статуси, якщо вони є
}


# --- НОВА ФУНКЦІЯ ГЕНЕРАЦІЇ USERNAME ---
def generate_username(full_name: str) -> str:
    """
    Генерує username з повного імені.
    Приклад: "Ступак Максим" -> "mstupak"
    """
    # "Ступак Максим" -> "Stupak Maksym"
    latin_name = unidecode(full_name)

    parts = latin_name.split()
    username_raw = ""

    if len(parts) >= 2:
        # parts[0] = "Stupak" (Прізвище)
        # parts[1] = "Maksym" (Ім'я)
        last_name = parts[0]
        first_name = parts[1]

        # "M" + "Stupak" -> "MStupak"
        username_raw = f"{first_name[0]}{last_name}"
    elif len(parts) == 1:
        # Fallback для одного слова
        username_raw = parts[0]
    else:
        return f"user_{datetime.now().timestamp()}"  # Fallback для порожніх

    # Очищуємо від апострофів, пробілів тощо і переводимо в нижній регістр
    # "MStupak" -> "mstupak"
    username_clean = re.sub(r'[^a-zA-Z0-9]', '', username_raw).lower()

    return username_clean


# --- КІНЕЦЬ НОВОЇ ФУНКЦІЇ ---


# --- 2. ГОЛОВНИЙ СКРИПТ (змінено) ---

def convert_schedule_xlsx(input_path, output_path, sheet_name=None):
    print(f"Відкриваю XLSX файл: {input_path}...")

    try:
        workbook = openpyxl.load_workbook(input_path, data_only=True)
        sheet = workbook[sheet_name] if sheet_name else workbook.active
        print(f"Читаю аркуш: '{sheet.title}'")
    except Exception as e:
        print(f"!!! ПОМИЛКА: Не можу відкрити файл. {e}")
        return

    # Словник для збереження згенерованих імен, щоб бачити, що ми наробили
    generated_usernames_cache = {}

    # Готуємо вихідний CSV файл
    with open(output_path, mode='w', encoding='utf-8', newline='') as outfile:
        writer = csv.writer(outfile)
        writer.writerow(['id', 'agent', 'start', 'end', 'direction', 'status', 'activity', 'comment'])

        # Читаємо заголовок (перший рядок) з датами
        header_row = sheet[1]
        dates = []
        for cell in header_row[1:]:
            if isinstance(cell.value, datetime):
                dates.append(cell.value.date())
            elif cell.value:
                try:
                    dates.append(datetime.strptime(str(cell.value), '%Y-%m-%d').date())
                except Exception:
                    dates.append(None)
            else:
                dates.append(None)

        print(f"Знайдено {len([d for d in dates if d])} дат в заголовку.")

        processed_shifts = 0

        # Обробляємо кожного агента (рядки, починаючи з 2-го)
        for row in sheet.iter_rows(min_row=2):
            agent_full_name_cell = row[0].value
            if not agent_full_name_cell:
                continue

            agent_full_name = str(agent_full_name_cell).strip()

            # --- ОСЬ ТУТ ЗМІНА ---
            # Замість пошуку в AGENT_USERNAME_MAP, ми генеруємо ім'я

            if agent_full_name not in generated_usernames_cache:
                # Генеруємо username, якщо бачимо це ім'я вперше
                agent_username = generate_username(agent_full_name)
                generated_usernames_cache[agent_full_name] = agent_username
                # Повідомляємо вас про те, яке ім'я було створено
                print(f"  [Info] Згенеровано: '{agent_full_name}'  ->  '{agent_username}'")
            else:
                # Використовуємо вже згенероване ім'я з кешу
                agent_username = generated_usernames_cache[agent_full_name]
            # --- КІНЕЦЬ ЗМІНИ ---

            shifts_cells = row[1:]

            # Проходимо по кожній даті та клітинці для цього агента
            for date_obj, shift_cell in zip(dates, shifts_cells):

                shift_raw_value = shift_cell.value

                if not shift_raw_value or not date_obj:
                    continue

                shift_raw = str(shift_raw_value).strip()

                # --- 3. ЛОГІКА ПАРСИНГУ КЛІТИНКИ (без змін) ---
                try:
                    output_row = {
                        "id": "",
                        "agent": agent_username,  # <-- Використовуємо згенероване ім'я
                        "direction": "calls",
                        "activity": "",
                        "comment": ""
                    }

                    if shift_raw in STATUS_MAP:
                        output_row["status"] = STATUS_MAP[shift_raw]
                        start_dt = datetime.combine(date_obj, time(0, 0))
                        end_dt = datetime.combine(date_obj + timedelta(days=1), time(0, 0))

                    else:
                        output_row["status"] = "work"
                        time_parts = shift_raw.split('-')
                        start_str = time_parts[0].strip()
                        end_str = time_parts[1].strip()

                        h_start, m_start = map(int, start_str.split(':'))
                        start_dt = datetime.combine(date_obj, time(h_start, m_start))

                        if "24:00" in end_str:
                            end_dt = datetime.combine(date_obj + timedelta(days=1), time(0, 0))
                        else:
                            h_end, m_end = map(int, end_str.split(':'))
                            end_dt = datetime.combine(date_obj, time(h_end, m_end))

                        if end_dt <= start_dt:
                            end_dt += timedelta(days=1)

                    output_row["start"] = start_dt.isoformat()
                    output_row["end"] = end_dt.isoformat()

                    writer.writerow([
                        output_row["id"],
                        output_row["agent"],
                        output_row["start"],
                        output_row["end"],
                        output_row["direction"],
                        output_row["status"],
                        output_row["activity"],
                        output_row["comment"]
                    ])
                    processed_shifts += 1

                except Exception as e:
                    print(f"!!! ПОМИЛКА: Не вдалося обробити клітинку: "
                          f"Агент='{agent_full_name}', Дата='{date_obj}', Значення='{shift_raw}'")
                    print(f"    Текст помилки: {e}")

    print("-" * 30)
    print(f"Готово! Оброблено {processed_shifts} змін.")
    print(f"Результат збережено у файл: {output_path}")


# --- 4. ЗАПУСК СКРИПТУ ---
if __name__ == "__main__":
    convert_schedule_xlsx(INPUT_FILE, OUTPUT_FILE, sheet_name=SHEET_NAME)