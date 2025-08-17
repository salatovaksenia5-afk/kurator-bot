import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import pytz

TIMEZONE = pytz.timezone("Europe/Moscow")

def _now_msk():
    return datetime.now(TIMEZONE)

# ====== Вставляем ключ напрямую ======
GOOGLE_KEY_JSON = '''{
  "type": "service_account",
  "project_id": "hallowed-fin-469221-f3",
  "private_key_id": "98be800f93ee663437e5e8a6f9c4efecb39b4b30",
  "private_key": "-----BEGIN PRIVATE KEY-----\\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDS4ORa+UPwBP2o\\nvOSXX7rnWQYkELOZYUc7ogVsLpywpTon5hiPVSY9tdhx3ibngKva92GUzRCdO1jc\\nmF6mwx0irMmK9I88aWg/x5rEWSZF9u2HHpLFyV5++MDfcbE6VB8MTisec1JGflDy\\n8zAoPuoHBpNq7pr483F1vXprtpDesOSF1gEQt/K8S9Z8AdP+L/Q+OTd9uJWJNG+B\\nGIsqB73OYV3DOF4n1UL1yoraeU2JTIeIYScg57PrYibHm+HgsSwiZssRM8FR+EKE\\nya2Cdy/XIukTJvuAmEmZ85TladmhHOddryOIWbg92LJi0YSD+aIBam/GHzXtuVRj\\npJdzdy6VAgMBAAECggEAG/kcIZs+MVkgIuGHuzLGMski4ObcRCTc06K+8GZQf7gz\\nOjayHFVRwM7d+uDarUvqwB2fsqLvKMRGGeEWcS2hsEdWZtnhJEThQNCkDZa71n0W\\nbh2Wn6kCIXqy7xEATvn4smOuIZhvmg1IhKnQwg3ycmMjbARhx1NXwiQT2LT7i6aS\\nh1CmVSHeoY5pONG8D5pPIQcfOq/erhjsHmXgmexw0u2+zqv+wX0Dhbe9O9JBqnnl\\n8iCcCz6yY7vq2hnE6ClE2hTRlylormpZy8u22FlNrIa58+jmsCrrqJw39blw5btP\\nlRtE1galSBSBfbFa5aGCP7hnCtcWKqVMTCErlyVqgQKBgQD/ScyKWiezYfpxUbqO\\nA7kg0ygBt77Kg6DU08cR+JtPNJYlg4gbpBgOTAMN9lDvOmSb5bFLQ7pxScBZzQSi\\nuDVQOdENS5om7mdGPSajeQmi5OB3SWehgceI+R952rCY3uJSDV8VICoELyEjOnwQ\\nQpKTO0egaazlITkp07GDMznG1QKBgQDTd2W/RxW1pQ3OMmR1imB7tttuhh3L2Obr\\nQAZWs5CKtkgzqE5Kc32wBpqn4euEFQ4K0cWDJMGNiFet6VTE7KvUCSnP2mPK8+L1\\n4q0xwmWOEgG0ioODA296e+A01uJ1+sa2sdTwWOsgVBnhsUSqoVv9M7Ct5t/b/UQy\\n0wvO7/sowQKBgAHaq12l4fvvjj4cddpqNIIEhpncl8oowpZJI30B7T7aBu0m02o/\\n+ty/uJX1YAkcx3ZKaMs/Jq+D9Z2xW4NDC0AV62rJTclSmfVspEczrdo9B1RWeCY4\\nJxbbmFruN7IkjEhESJiCr0twfDEhT51enmnrWE3V9qeDYkLngraNPLIxAoGAMJZU\\naHiawlukKmZlsqQSuxounNxv6DB0DkVtr2oeoeB0Anp/UpfqnxFFY6GDDZwQ1+eO\\n7Cz709sp2imscnq2mEdqtflFyJH06e4lQTObReNZRPQ2d1nIuWnRABMHgXgXRE7j\\n0D19+LWaJMMoNdRRYIIJ7EsJ0HAGxW68XdXB7YECgYEAoNuRuGCg3jZi1Lj5S5YU\\nEVNtT0S1+yGcjocZyAH/gnGbQrssUdmKQ2C6S84a6oYjDgKYwhVC9+8aie9uP5GX\\nPcAthpt4g0XkGLAhAhrPfXtMC1+fjNMs+6MtuCQLfFZtt/1LRyRDVlGrmWxUSya8\\n6QCb1edmoM3vfcVOBU0JcIQ=\\n-----END PRIVATE KEY-----\\n",
  "client_email": "kurator-bot-sa@hallowed-fin-469221-f3.iam.gserviceaccount.com",
  "client_id": "105441195965483825945",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token",
  "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
  "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/kurator-bot-sa%40hallowed-fin-469221-f3.iam.gserviceaccount.com",
  "universe_domain": "googleapis.com"
}'''

# ====== Вставляем ссылку на таблицу ======
SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/17zqwZ0MNNJWjzVfmBluLXyRGt-ogC14QxtXhTfEPsNU/edit"

def connect_sheets():
    try:
        creds_dict = json.loads(GOOGLE_KEY_JSON)
        scope = ["https://spreadsheets.google.com/feeds",
                 "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        sheet = client.open_by_url(SPREADSHEET_URL)
        summary = sheet.sheet1
        try:
            log_sheet = sheet.worksheet("Лог")
        except gspread.WorksheetNotFound:
            log_sheet = sheet.add_worksheet(title="Лог", rows=1000, cols=10)
            log_sheet.append_row(["ts", "tg_id", "fio", "role", "subject", "event", "details"])
        return summary, log_sheet
    except Exception as e:
        print("⚠️ Ошибка подключения к Google Sheets:", e)
        return None, None

WS_SUMMARY, WS_LOG = connect_sheets()

def gs_log_event(uid, fio, role, subject, event, details=""):
    if not WS_LOG:
        return
    try:
        WS_LOG.append_row([
            datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M:%S"),
            str(uid), fio or "", role or "", subject or "", event, details
        ])
    except Exception as e:
        print("⚠️ Sheets LOG error:", e)