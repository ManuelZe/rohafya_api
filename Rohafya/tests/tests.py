from datetime import date, datetime, time

today = datetime.today()
day = today.day

if day < 20:
    # Période = 21 du mois précédent → 20 du mois actuel
    if today.month == 1:
        prev_month = 12
        prev_year = today.year - 1
    else:
        prev_month = today.month - 1
        prev_year = today.year

    start_date = datetime(prev_year, prev_month, 21)
    end_date = datetime(today.year, today.month, 21)

    print(start_date, "------", end_date)