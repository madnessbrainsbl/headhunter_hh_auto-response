# HH.ru Auto Apply Bot

Бот для поиска вакансий на HH.ru и автоматических откликов с приоритетом на кибербезопасность, AppSec, DevSecOps, SOC, Red Team и пентест.

Когда официальный HH API не дает доступ к `/negotiations`, бот использует браузерный Selenium-режим и отправляет отклики через открытую сессию Chrome.

## Возможности

- Поиск вакансий через HH API с широким набором ИБ-запросов.
- Автоотклики через Selenium, если API откликов закрыт.
- Заполнение сопроводительных писем.
- Обработка обязательных писем, вопросов работодателя, отказов, уже отправленных откликов и лимита HH.
- Rolling-счетчик откликов за последние 24 часа.
- Кеш вакансий и удаление уже обработанных вакансий из кеша.
- Фильтры против нерелевантных вакансий: продажи, менеджмент, junior, стажировки, преподаватели, HR и другие.
- Тесты для критичной логики.

## Установка

Нужны:

- Python 3.10+
- Google Chrome
- Selenium Manager сам подберет ChromeDriver

Установить зависимости:

```powershell
pip install -r requirements.txt
```

## Настройка

Скопируйте пример локального конфига:

```powershell
Copy-Item hh_local_config.example.json hh_local_config.json
```

Заполните `hh_local_config.json`:

```json
{
  "HH_CLIENT_ID": "your_client_id",
  "HH_CLIENT_SECRET": "your_client_secret",
  "HH_REDIRECT_URI": "https://localhost/callback",
  "HH_RESUME_ID": "your_resume_id"
}
```

Можно использовать переменные окружения с теми же именами:

- `HH_CLIENT_ID`
- `HH_CLIENT_SECRET`
- `HH_REDIRECT_URI`
- `HH_RESUME_ID`

Переменные окружения имеют приоритет над `hh_local_config.json`.

Selenium-настройки можно скопировать отдельно:

```powershell
Copy-Item hh_selenium_config.example.json hh_selenium_config.json
```

## Запуск

Основной запуск:

```powershell
python test.py
```

Если HH API закрывает `/negotiations`, скрипт сам запустит Selenium-режим.

Запуск Selenium напрямую:

```powershell
python hh_selenium.py --api-cache --limit 200
```

Полезные команды:

```powershell
python test.py --clear-cache
python test.py --selenium-limit 50
python hh_selenium.py --api-cache --debugger 127.0.0.1:9122 --limit 200
```

## Проверка

```powershell
python -m py_compile test.py hh_selenium.py
pytest -q
```

## Что не коммитится

В репозиторий специально не добавляются:

- `hh_token.json`
- `hh_app_token.json`
- `hh_local_config.json`
- `hh_selenium_config.json`
- `applied_vacancies*.json`
- `vacancies_cache*.json`
- `*.log`
- Chrome profile и локальные runtime-папки

Эти файлы содержат токены, секреты, историю откликов, кеш вакансий или локальное состояние браузера. Их нельзя публиковать на GitHub.

## Структура

- `test.py` - основной API-поиск, кеш, авторизация и запуск Selenium fallback.
- `hh_selenium.py` - браузерная автоматизация откликов.
- `hh_local_config.example.json` - пример локального конфига с HH credentials.
- `hh_selenium_config.example.json` - пример настроек Selenium-режима.
- `tests/` - тесты для заголовков, кеша, фильтров, лимитов и Selenium-логики.

## Важно

Перед запуском проверьте фильтры и сопроводительное письмо под свой профиль. Массовые отклики отправляются от вашего аккаунта HH.ru.
