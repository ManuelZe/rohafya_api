from datetime import datetime, timedelta


def get_days_after(date_field=None, days=90):

    if date_field is None:
        return False

    today = datetime.now()
    if today >= date_field + timedelta(days=days):
        return False
    else:
        return True
    

