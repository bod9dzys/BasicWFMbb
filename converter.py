# -*- coding: utf-8 -*-
import csv
from datetime import datetime, time, timedelta

import openpyxl
from openpyxl import Workbook

# =========================
# 1) НАЛАШТУВАННЯ
# =========================

# Вхідний Excel з графіком
INPUT_FILE = "october.xlsx"

# Назва аркуша (None = перший активний)
SHEET_NAME = None

# Формат вихідного файлу: 'xlsx' або 'csv'
OUTPUT_FORMAT = "xlsx"   # 'xlsx' або 'csv'

# Ім'я вихідного файлу (без розширення, ми додамо самі)
OUTPUT_BASENAME = "import_for_wfm"

# Для CSV: щоб Excel в українській локалі не склеював колонки
CSV_DELIMITER = ";"
CSV_ENCODING = "utf-8"

# Мапа статусів
STATUS_MAP = {
    "Відпустка": "vacation",
    "OFF": "day_off",
    "вихідний": "day_off",
    "Лікарняний": "sick",
    # додай інші за потреби
}

# Назви колонок
HEADERS = ["id", "agent", "start", "end", "direction", "status", "activity", "comment"]

# Мапа активностей -> напрямів
ACTIVITY_DIRECTION_MAP = {
    "дзвінки": "calls",
    "дзінки": "calls",
    "дзвонки": "calls",
    "calls": "calls",
    "тікети": "tickets",
    "тикети": "tickets",
    "tickets": "tickets",
    "чати": "chats",
    "чат": "chats",
    "chats": "chats",
}
DEFAULT_DIRECTION = "calls"


# =========================
# 2) ДОПОМОЖНІ РЕЧІ ДЛЯ ВИВОДУ
# =========================
class OutputWriter:
    """
    Єдиний інтерфейс на два вихідних формати.
    Викликати .write_row(dict) для запису рядка
    і .close() після завершення.
    """

    def __init__(self, format_: str, basename: str):
        self.format_ = format_.lower().strip()
        self.rows_written = 0

        if self.format_ == "xlsx":
            self.filepath = f"{basename}.xlsx"
            self.wb: Workbook = Workbook()
            self.ws = self.wb.active
            self.ws.title = "export"
            # Пишемо заголовки
            self.ws.append(HEADERS)
            # Формат автоширини зробимо наприкінці
            self.is_csv = False

        elif self.format_ == "csv":
            self.filepath = f"{basename}.csv"
            self._csv_file = open(self.filepath, mode="w", encoding=CSV_ENCODING, newline="")
            self._writer = csv.writer(self._csv_file, delimiter=CSV_DELIMITER)
            self._writer.writerow(HEADERS)
            self.is_csv = True

        else:
            raise ValueError("OUTPUT_FORMAT має бути 'xlsx' або 'csv'.")

    @staticmethod
    def _fmt_dt(dt: datetime) -> str:
        # Excel зʼїсть ISO, але для людського вигляду й сортування достатньо цього:
        return dt.strftime("%Y-%m-%d %H:%M")

    def write_row(self, row: dict):
        values = [
            row.get("id", ""),
            row.get("agent", ""),
            self._fmt_dt(row["start"]) if isinstance(row.get("start"), datetime) else row.get("start", ""),
            self._fmt_dt(row["end"]) if isinstance(row.get("end"), datetime) else row.get("end", ""),
            row.get("direction", ""),
            row.get("status", ""),
            row.get("activity", ""),
            row.get("comment", ""),
        ]
        if self.is_csv:
            self._writer.writerow(values)
        else:
            self.ws.append(values)
        self.rows_written += 1

    def close(self):
        if self.is_csv:
            self._csv_file.close()
        else:
            # Трохи піджати ширину колонок
            for col_idx, header in enumerate(HEADERS, start=1):
                max_len = len(str(header))
                for row in self.ws.iter_rows(min_row=2, min_col=col_idx, max_col=col_idx):
                    cell_val = row[0].value
                    if cell_val is None:
                        continue
                    max_len = max(max_len, len(str(cell_val)))
                # приблизна ширина
                self.ws.column_dimensions[openpyxl.utils.get_column_letter(col_idx)].width = min(max_len + 2, 40)
            self.wb.save(self.filepath)


# =========================
# 4) ОСНОВНА ЛОГІКА
# =========================
def convert_schedule_xlsx(input_path, output_basename, sheet_name=None, output_format="xlsx"):
    print(f"Відкриваю XLSX файл: {input_path}...")

    try:
        workbook = openpyxl.load_workbook(input_path, data_only=True)
        sheet = workbook[sheet_name] if sheet_name else workbook.active
        print(f"Читаю аркуш: '{sheet.title}'")
    except Exception as e:
        print(f"!!! ПОМИЛКА: Не можу відкрити файл. {e}")
        return

    # ініціалізуємо writer під обраний формат
    writer = OutputWriter(output_format, output_basename)

    # Заголовок з датами в першому рядку (починаючи з 3-ї колонки, бо A=активність, B=агент)
    header_row = sheet[1]
    dates = []
    for cell in header_row[2:]:
        if isinstance(cell.value, datetime):
            dates.append(cell.value.date())
        elif cell.value:
            # спроба розпарсити строку
            parsed = None
            for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
                try:
                    parsed = datetime.strptime(str(cell.value), fmt).date()
                    break
                except Exception:
                    continue
            dates.append(parsed)
        else:
            dates.append(None)

    print(f"Знайдено {len([d for d in dates if d])} дат в заголовку.")

    processed_shifts = 0

    # Проходимо по кожному агенту (починаючи з 2 рядка)
    for row in sheet.iter_rows(min_row=2):
        activity_cell = row[0].value if len(row) > 0 else None
        agent_full_name_cell = row[1].value if len(row) > 1 else None
        if not agent_full_name_cell:
            continue

        agent_full_name = str(agent_full_name_cell).strip()

        direction_code, activity_label = _resolve_direction_and_activity(activity_cell)

        shifts_cells = row[2:]

        for date_obj, shift_cell in zip(dates, shifts_cells):
            shift_raw_value = shift_cell.value
            if not shift_raw_value or not date_obj:
                continue

            shift_raw = str(shift_raw_value).strip()

            try:
                out = {
                    "id": "",
                    "agent": agent_full_name,
                    "direction": direction_code,
                    "activity": activity_label,
                    "comment": "",
                }

                if shift_raw in STATUS_MAP:
                    out["status"] = STATUS_MAP[shift_raw]
                    start_dt = datetime.combine(date_obj, time(0, 0))
                    end_dt = datetime.combine(date_obj + timedelta(days=1), time(0, 0))
                else:
                    out["status"] = "work"
                    parts = shift_raw.split("-")
                    if len(parts) != 2:
                        raise ValueError("Невірний формат інтервалу, очікується 'HH:MM-HH:MM' або статус.")

                    start_str = parts[0].strip()
                    end_str = parts[1].strip()

                    h_start, m_start = map(int, start_str.split(":"))
                    start_dt = datetime.combine(date_obj, time(h_start, m_start))

                    if end_str == "24:00":
                        end_dt = datetime.combine(date_obj + timedelta(days=1), time(0, 0))
                    else:
                        h_end, m_end = map(int, end_str.split(":"))
                        end_dt = datetime.combine(date_obj, time(h_end, m_end))

                    if end_dt <= start_dt:
                        end_dt += timedelta(days=1)

                out["start"] = start_dt
                out["end"] = end_dt

                writer.write_row(out)
                processed_shifts += 1

            except Exception as e:
                print(
                    f"!!! ПОМИЛКА: Не вдалося обробити клітинку: "
                    f"Агент='{agent_full_name}', Дата='{date_obj}', Значення='{shift_raw}'"
                )
                print(f"     Текст помилки: {e}")

    writer.close()

    print("-" * 30)
    print(f"Готово! Оброблено {processed_shifts} змін.")
    print(f"Результат збережено у файл: {writer.filepath}")


# =========================
# 5) ДОПОМІЖНІ ФУНКЦІЇ
# =========================
def _resolve_direction_and_activity(activity_cell):
    label = ""
    if activity_cell is not None:
        label = str(activity_cell).strip()
    normalized = label.casefold()
    direction = ACTIVITY_DIRECTION_MAP.get(normalized)

    if not direction:
        if normalized in {"calls", "tickets", "chats"}:
            direction = normalized
        else:
            if label:
                print(f" [Warn] Невідома активність '{label}'. Використовую напрям за замовчуванням '{DEFAULT_DIRECTION}'.")
            direction = DEFAULT_DIRECTION
    return direction, label


# =========================
# 6) ЗАПУСК
# =========================
if __name__ == "__main__":
    convert_schedule_xlsx(
        INPUT_FILE,
        OUTPUT_BASENAME,
        sheet_name=SHEET_NAME,
        output_format=OUTPUT_FORMAT,
    )
