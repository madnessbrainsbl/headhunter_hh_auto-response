import requests
import json
import time
import webbrowser
from datetime import datetime, timedelta
from urllib.parse import urlencode, parse_qs, urlparse
import logging
import re
import random
import os
import sys
import subprocess
from typing import Optional

# Получаем путь к директории скрипта
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

BASE_URL = 'https://api.hh.ru'
OAUTH_URL = 'https://hh.ru/oauth'
TOKEN_URL = 'https://api.hh.ru/token'
APP_USER_AGENT = 'AutoJobApplyBot/1.0'
CONTACT_EMAIL_ENV = 'HH_CONTACT_EMAIL'
REQUEST_TIMEOUT_SECONDS = 15
MAX_REQUEST_RETRIES = 3
DEFAULT_MANUAL_APPLY_LIMIT = 10
DEFAULT_SELENIUM_APPLY_LIMIT = 200
APPLICATION_LIMIT_WINDOW_HOURS = 24
SEARCH_RESULT_LIMIT = 1500
SEARCH_PAGE_LIMIT = 15
MAX_SEARCH_NETWORK_ERRORS_WITHOUT_RESULTS = 3
AUTHORIZATION_CODE_GRANT = 'authorization_code'
CLIENT_CREDENTIALS_GRANT = 'client_credentials'
REFRESH_TOKEN_GRANT = 'refresh_token'
APP_TOKEN_REFRESH_TOO_EARLY = 'app token refresh too early'
EMAIL_PATTERN = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
BLACKLISTED_CONTACT_DOMAINS = {'example.com', 'hh.ru'}
HH_ALLOWED_APPLY_HOST_SUFFIX = 'hh.ru'
LOCAL_CONFIG_FILE = 'hh_local_config.json'
REQUIRED_CREDENTIAL_KEYS = ('HH_CLIENT_ID', 'HH_CLIENT_SECRET', 'HH_RESUME_ID')

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(SCRIPT_DIR, 'hh_auto_apply.log'), encoding='utf-8'),
        logging.StreamHandler()
    ]
)


def build_user_agent(contact_email: Optional[str]) -> str:
    """Возвращает User-Agent в формате, который принимает HH API."""
    if contact_email is None or not contact_email.strip():
        return APP_USER_AGENT

    normalized_email = contact_email.strip().lower()
    if not EMAIL_PATTERN.fullmatch(normalized_email):
        raise ValueError(f'{CONTACT_EMAIL_ENV} должен быть email-адресом')

    email_domain = normalized_email.rsplit('@', 1)[1]
    if email_domain in BLACKLISTED_CONTACT_DOMAINS:
        raise ValueError(f'{CONTACT_EMAIL_ENV} должен быть реальным контактным email, не {email_domain}')

    return f'{APP_USER_AGENT} ({normalized_email})'


def build_hh_headers(user_agent: str, access_token: Optional[str] = None) -> dict[str, str]:
    headers = {
        'User-Agent': user_agent,
        'HH-User-Agent': user_agent,
        'Accept': 'application/json',
    }

    if access_token:
        headers['Authorization'] = f'Bearer {access_token}'

    return headers


def build_client_credentials_payload(client_id: str, client_secret: str) -> dict[str, str]:
    return {
        'grant_type': CLIENT_CREDENTIALS_GRANT,
        'client_id': client_id,
        'client_secret': client_secret,
    }


def is_allowed_hh_url(url: str) -> bool:
    parsed_url = urlparse(url)
    hostname = parsed_url.hostname or ''
    return parsed_url.scheme == 'https' and (
        hostname == HH_ALLOWED_APPLY_HOST_SUFFIX
        or hostname.endswith(f'.{HH_ALLOWED_APPLY_HOST_SUFFIX}')
    )


def get_vacancy_apply_url(vacancy: dict) -> Optional[str]:
    for field_name in ('apply_alternate_url', 'alternate_url'):
        url = vacancy.get(field_name)
        if isinstance(url, str) and is_allowed_hh_url(url):
            return url

    return None


def parse_saved_timestamp(value: object) -> Optional[datetime]:
    if not isinstance(value, str):
        return None

    try:
        return datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None


def count_recent_timestamps(timestamps: list[object], window_hours: int) -> int:
    cutoff = datetime.now() - timedelta(hours=window_hours)
    count = 0

    for timestamp in timestamps:
        parsed_timestamp = parse_saved_timestamp(timestamp)
        if parsed_timestamp is None:
            continue
        if parsed_timestamp >= cutoff:
            count += 1

    return count


def write_json_atomic(path: str, data: object) -> None:
    temp_path = f'{path}.tmp'
    with open(temp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)


def load_local_config() -> dict[str, str]:
    config_path = os.path.join(SCRIPT_DIR, LOCAL_CONFIG_FILE)
    if not os.path.exists(config_path):
        return {}

    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    if not isinstance(config, dict):
        raise ValueError(f'{LOCAL_CONFIG_FILE} должен содержать JSON-объект')

    return {str(key): str(value) for key, value in config.items() if value}


def get_config_value(local_config: dict[str, str], env_name: str, legacy_name: str | None = None) -> str:
    return (
        os.environ.get(env_name)
        or local_config.get(env_name)
        or (local_config.get(legacy_name) if legacy_name else None)
        or ''
    ).strip()


def load_runtime_credentials() -> dict[str, str]:
    local_config = load_local_config()
    credentials = {
        'HH_CLIENT_ID': get_config_value(local_config, 'HH_CLIENT_ID', 'CLIENT_ID'),
        'HH_CLIENT_SECRET': get_config_value(local_config, 'HH_CLIENT_SECRET', 'CLIENT_SECRET'),
        'HH_REDIRECT_URI': get_config_value(local_config, 'HH_REDIRECT_URI', 'REDIRECT_URI')
        or 'https://localhost/callback',
        'HH_RESUME_ID': get_config_value(local_config, 'HH_RESUME_ID', 'RESUME_ID'),
    }

    missing_keys = [key for key in REQUIRED_CREDENTIAL_KEYS if not credentials[key]]
    if missing_keys:
        missing = ', '.join(missing_keys)
        raise ValueError(
            f'Не заданы обязательные настройки: {missing}. '
            f'Укажите их в переменных окружения или в {LOCAL_CONFIG_FILE}.'
        )

    return credentials


class HHAutoApplicant:
    def __init__(self, client_id, client_secret, redirect_uri, resume_id):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.base_url = BASE_URL
        self.oauth_url = OAUTH_URL
        self.access_token = None
        self.refresh_token = None
        self.app_access_token = None
        self.api_negotiations_available = None
        self.session = requests.Session()
        self.user_agent = build_user_agent(os.environ.get(CONTACT_EMAIL_ENV))

        self.resume_id = resume_id

        self.max_applications_per_day = 200
        self.selenium_apply_limit = DEFAULT_SELENIUM_APPLY_LIMIT
        self.min_delay_between_requests = 2
        self.delay_after_429 = 60
        self.applied_today = 0

        # Все файлы сохраняются в директорию скрипта
        self.applied_vacancies_file = os.path.join(SCRIPT_DIR, 'applied_vacancies.json')
        self.vacancies_cache_file = os.path.join(SCRIPT_DIR, 'vacancies_cache.json')
        self.token_file = os.path.join(SCRIPT_DIR, 'hh_token.json')
        self.app_token_file = os.path.join(SCRIPT_DIR, 'hh_app_token.json')
        self.cache_lifetime_hours = 6  # Уменьшено для более частого обновления
        self.min_vacancies_in_cache = 50  # Минимум вакансий для использования кеша

        self.load_applied_vacancies()

        # Всегда исключаем вакансии с тестами
        self.skip_vacancies_with_tests = True

        # ПРИОРИТЕТ НА КИБЕРБЕЗОПАСНОСТЬ
        self.security_priority = True

        self.ensure_token()

    def load_applied_vacancies(self):
        try:
            with open(self.applied_vacancies_file, 'r', encoding='utf-8') as f:
                self.applied_vacancies = json.load(f)
        except FileNotFoundError:
            self.applied_vacancies = {}

        self.applied_today = count_recent_timestamps(
            list(self.applied_vacancies.values()),
            APPLICATION_LIMIT_WINDOW_HOURS,
        )

        print(f"📋 Загружено {len(self.applied_vacancies)} обработанных вакансий")
        print(f"📊 Откликов за последние 24 часа: {self.applied_today}")

    def save_applied_vacancy(self, vacancy_id):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.applied_vacancies[str(vacancy_id)] = timestamp
        self.applied_today += 1

        try:
            write_json_atomic(self.applied_vacancies_file, self.applied_vacancies)

            # Сразу удаляем из кеша
            self.remove_from_cache(vacancy_id)
        except Exception as e:
            logging.error(f"Ошибка сохранения файла откликов: {e}")

    def remove_from_cache(self, vacancy_id):
        """Удаляет вакансию из кеша"""
        try:
            if not os.path.exists(self.vacancies_cache_file):
                return

            with open(self.vacancies_cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)

            # Фильтруем вакансии
            original_count = len(cache_data['vacancies'])
            cache_data['vacancies'] = [v for v in cache_data['vacancies']
                                      if str(v.get('id')) != str(vacancy_id)]

            if len(cache_data['vacancies']) < original_count:
                cache_data['total_count'] = len(cache_data['vacancies'])
                write_json_atomic(self.vacancies_cache_file, cache_data)
                logging.info(f"Вакансия {vacancy_id} удалена из кеша")

        except Exception as e:
            logging.debug(f"Ошибка удаления из кеша: {e}")

    def sync_cache_with_applied(self):
        """Синхронизирует кеш с файлом обработанных вакансий"""
        try:
            if not os.path.exists(self.vacancies_cache_file):
                return

            with open(self.vacancies_cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)

            original_count = len(cache_data['vacancies'])

            filtered_vacancies = []
            removed_count = 0

            for v in cache_data['vacancies']:
                v_id = str(v.get('id'))
                if v_id not in self.applied_vacancies:
                    filtered_vacancies.append(v)
                else:
                    removed_count += 1

            if removed_count > 0:
                cache_data['vacancies'] = filtered_vacancies
                cache_data['total_count'] = len(cache_data['vacancies'])

                write_json_atomic(self.vacancies_cache_file, cache_data)

                print(f"🔄 Синхронизация кеша: удалено {removed_count} обработанных вакансий")
                print(f"   Осталось в кеше: {len(filtered_vacancies)} вакансий")

        except Exception as e:
            logging.error(f"Ошибка синхронизации кеша: {e}")

    def save_vacancies_cache(self, vacancies):
        """Сохраняет найденные вакансии в кеш"""
        filtered_vacancies = []
        for v in vacancies:
            v_id = str(v.get('id'))
            if v_id not in self.applied_vacancies:
                filtered_vacancies.append(v)

        cache_data = {
            'timestamp': datetime.now().isoformat(),
            'vacancies': filtered_vacancies,
            'total_count': len(filtered_vacancies)
        }

        try:
            write_json_atomic(self.vacancies_cache_file, cache_data)
            print(f"💾 Сохранено {len(filtered_vacancies)} вакансий в кеш")
            if len(vacancies) - len(filtered_vacancies) > 0:
                print(f"   (исключено обработанных: {len(vacancies) - len(filtered_vacancies)})")
            print(f"📁 Путь к кешу: {self.vacancies_cache_file}")
        except Exception as e:
            logging.error(f"Ошибка сохранения кеша вакансий: {e}")

    def load_vacancies_cache(self):
        """Загружает вакансии из кеша если он актуален И содержит достаточно вакансий"""
        try:
            with open(self.vacancies_cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)

            cache_time = datetime.fromisoformat(cache_data['timestamp'])
            age_hours = (datetime.now() - cache_time).total_seconds() / 3600

            if age_hours > self.cache_lifetime_hours:
                print(f"⏰ Кеш устарел (возраст: {age_hours:.1f} часов > {self.cache_lifetime_hours} часов)")
                return None

            vacancies = cache_data['vacancies']

            filtered_vacancies = []
            excluded_count = 0
            for v in vacancies:
                v_id = str(v.get('id'))
                if v_id not in self.applied_vacancies:
                    filtered_vacancies.append(v)
                else:
                    excluded_count += 1

            print(f"💾 В кеше найдено: {len(filtered_vacancies)} необработанных вакансий")
            if excluded_count > 0:
                print(f"   ⏭️ Исключено из кеша: {excluded_count} уже обработанных")
            print(f"⏰ Возраст кеша: {age_hours:.1f} часов")

            # ВАЖНО: Если вакансий меньше минимума - обновляем кеш
            if len(filtered_vacancies) < self.min_vacancies_in_cache:
                print(f"⚠️ В кеше мало вакансий ({len(filtered_vacancies)} < {self.min_vacancies_in_cache})")
                print("🔄 Требуется обновление поиска...")
                return None

            print(f"✅ Кеш актуален и содержит достаточно вакансий")
            return filtered_vacancies

        except FileNotFoundError:
            print("💾 Кеш вакансий не найден, будет выполнен новый поиск")
            return None
        except Exception as e:
            logging.error(f"Ошибка загрузки кеша вакансий: {e}")
            return None

    def clear_cache(self):
        """Очищает кеш вакансий"""
        try:
            if os.path.exists(self.vacancies_cache_file):
                os.remove(self.vacancies_cache_file)
                print("🗑️ Кеш вакансий очищен")
        except Exception as e:
            logging.error(f"Ошибка очистки кеша: {e}")

    def load_token(self):
        try:
            with open(self.token_file, 'r', encoding='utf-8') as f:
                token_info = json.load(f)
                self.access_token = token_info.get('access_token')
                self.refresh_token = token_info.get('refresh_token')
                logging.info("Токен загружен")
                return True
        except FileNotFoundError:
            logging.info("Токен не найден")
            return False

    def save_token(self, token_info):
        try:
            with open(self.token_file, 'w', encoding='utf-8') as f:
                json.dump(token_info, f, ensure_ascii=False, indent=2)
            logging.info("Токен сохранен")
        except Exception as e:
            logging.error(f"Ошибка сохранения токена: {e}")

    def load_app_token(self):
        try:
            with open(self.app_token_file, 'r', encoding='utf-8') as f:
                token_info = json.load(f)

            if token_info.get('client_id') != self.client_id:
                logging.info("Кеш токена приложения относится к другому client_id")
                return False

            self.app_access_token = token_info.get('access_token')
            if not self.app_access_token:
                logging.warning("В кеше токена приложения нет access_token")
                return False

            logging.info("Токен приложения загружен из кеша")
            return True

        except FileNotFoundError:
            logging.info("Токен приложения не найден")
            return False
        except (json.JSONDecodeError, OSError) as e:
            logging.error(f"Ошибка загрузки токена приложения: {e}")
            return False

    def save_app_token(self, token_info):
        app_token_info = dict(token_info)
        app_token_info['client_id'] = self.client_id
        app_token_info['created_at'] = datetime.now().isoformat()

        try:
            with open(self.app_token_file, 'w', encoding='utf-8') as f:
                json.dump(app_token_info, f, ensure_ascii=False, indent=2)
            logging.info("Токен приложения сохранен")
        except Exception as e:
            logging.error(f"Ошибка сохранения токена приложения: {e}")

    def clear_token(self):
        """Удаляет сохраненный токен"""
        try:
            if os.path.exists(self.token_file):
                os.remove(self.token_file)
                logging.info("Токен удален")
        except Exception as e:
            logging.error(f"Ошибка удаления токена: {e}")

    def get_authorization_url(self):
        params = {
            'response_type': 'code',
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'scope': 'resume vacancy_response'
        }

        auth_url = f"{self.oauth_url}/authorize?" + urlencode(params)
        return auth_url

    def authorize(self):
        print("\n⚠️ Требуется новая авторизация...")
        print("Старый токен будет удален.")

        self.clear_token()

        auth_url = self.get_authorization_url()
        print(f"\n🔗 Откройте эту ссылку в браузере:\n{auth_url}")
        webbrowser.open(auth_url)

        print("\n⚠️ ВАЖНО: После авторизации вы будете перенаправлены на localhost")
        print("Скопируйте ПОЛНЫЙ URL из адресной строки браузера")
        callback_url = input("\n📋 Вставьте полный URL после авторизации: ").strip()

        parsed_url = urlparse(callback_url)
        query_params = parse_qs(parsed_url.query)

        if 'code' not in query_params:
            raise Exception("Код авторизации не найден в URL")

        auth_code = query_params['code'][0]
        return self.get_access_token(auth_code)

    def get_access_token(self, auth_code):
        token_data = {
            'grant_type': AUTHORIZATION_CODE_GRANT,
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'redirect_uri': self.redirect_uri,
            'code': auth_code
        }

        try:
            response = requests.post(
                f"{self.oauth_url}/token",
                data=token_data,
                headers=build_hh_headers(self.user_agent),
                timeout=REQUEST_TIMEOUT_SECONDS
            )
            response.raise_for_status()

            token_info = response.json()
            self.access_token = token_info['access_token']
            self.refresh_token = token_info.get('refresh_token')

            self.save_token(token_info)
            print("✅ Авторизация успешна!")
            return True

        except requests.exceptions.RequestException as e:
            logging.error(f"Ошибка получения токена: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logging.error(f"Ответ сервера: {e.response.text}")
            return False

    def refresh_access_token(self):
        if not self.refresh_token:
            logging.warning("Refresh token отсутствует")
            return False

        token_data = {
            'grant_type': REFRESH_TOKEN_GRANT,
            'refresh_token': self.refresh_token
        }

        try:
            response = requests.post(
                f"{self.oauth_url}/token",
                data=token_data,
                headers=build_hh_headers(self.user_agent),
                timeout=REQUEST_TIMEOUT_SECONDS
            )
            response.raise_for_status()

            token_info = response.json()
            self.access_token = token_info['access_token']
            if 'refresh_token' in token_info:
                self.refresh_token = token_info['refresh_token']

            self.save_token(token_info)
            logging.info("Токен обновлен")
            return True

        except requests.exceptions.RequestException as e:
            logging.error(f"Ошибка обновления токена: {e}")
            return False

    def get_application_access_token(self):
        if self.load_app_token():
            return True

        token_data = build_client_credentials_payload(self.client_id, self.client_secret)

        try:
            response = requests.post(
                TOKEN_URL,
                data=token_data,
                headers=build_hh_headers(self.user_agent),
                timeout=REQUEST_TIMEOUT_SECONDS
            )
            response.raise_for_status()

            token_info = response.json()
            self.app_access_token = token_info['access_token']
            self.save_app_token(token_info)
            logging.info("Токен приложения получен")
            return True

        except requests.exceptions.RequestException as e:
            logging.error(f"Ошибка получения токена приложения: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logging.error(f"Ответ сервера: {e.response.text}")
                if APP_TOKEN_REFRESH_TOO_EARLY in e.response.text and self.load_app_token():
                    return True
            return False

    def ensure_token(self):
        if not self.load_token():
            if not self.authorize():
                raise Exception("Не удалось получить токен")
        else:
            try:
                token_is_valid = self.test_token()
            except requests.exceptions.RequestException as e:
                raise RuntimeError("HH API или DNS сейчас недоступны; сохраненный токен не удален") from e

            if not token_is_valid:
                print("\n⚠️ Текущий токен недействителен или не имеет нужных прав")
                if not self.authorize():
                    raise Exception("Не удалось получить новый токен")

    def test_token(self):
        """Проверяет валидность токена"""
        try:
            url = f"{self.base_url}/me"
            response = self.make_authenticated_request('GET', url)
            user_info = response.json()
            print(f"✅ Авторизован как: {user_info.get('first_name', '')} {user_info.get('last_name', '')}")
            return True
        except requests.exceptions.HTTPError as e:
            logging.error(f"Токен недействителен: {e}")
            return False
        except requests.exceptions.RequestException as e:
            logging.error(f"Сетевая ошибка проверки токена: {e}")
            raise

    def make_request(self, method, url, authenticated=True, **kwargs):
        """Универсальный метод для запросов (с авторизацией или без)"""
        headers = build_hh_headers(
            self.user_agent,
            self.access_token if authenticated and self.access_token else None
        )
        headers.update(kwargs.get('headers', {}))
        kwargs['headers'] = headers

        retry_count = 0

        while retry_count < MAX_REQUEST_RETRIES:
            try:
                response = self.session.request(method, url, timeout=REQUEST_TIMEOUT_SECONDS, **kwargs)

                if response.status_code == 401:
                    logging.warning("Получен 401 - токен недействителен")
                    if self.refresh_access_token():
                        headers['Authorization'] = f'Bearer {self.access_token}'
                        response = self.session.request(method, url, timeout=REQUEST_TIMEOUT_SECONDS, **kwargs)
                    else:
                        print("\n⚠️ Токен истек, требуется переавторизация")
                        if self.authorize():
                            headers['Authorization'] = f'Bearer {self.access_token}'
                            response = self.session.request(method, url, timeout=REQUEST_TIMEOUT_SECONDS, **kwargs)
                        else:
                            raise Exception("Не удалось обновить токен")

                if response.status_code == 403:
                    logging.error(f"403 Forbidden для {url}")
                    logging.error(f"Ответ: {response.text}")
                    error_msg = f"403 Forbidden: доступ к {url} запрещен"
                    http_error = requests.exceptions.HTTPError(error_msg)
                    http_error.response = response
                    raise http_error

                if response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', self.delay_after_429))
                    print(f"⏱️ Превышен лимит запросов. Ждем {retry_after} секунд...")
                    time.sleep(retry_after)
                    retry_count += 1
                    continue

                response.raise_for_status()
                return response

            except requests.exceptions.HTTPError as e:
                if e.response.status_code != 429:
                    raise
                retry_count += 1

            except requests.exceptions.RequestException as e:
                logging.error(f"Ошибка запроса {method} {url}: {e}")
                raise

        raise Exception(f"Превышено количество попыток после 429 ошибки")

    def make_authenticated_request(self, method, url, **kwargs):
        """Запрос с авторизацией (для совместимости)"""
        return self.make_request(method, url, authenticated=True, **kwargs)

    def make_application_request(self, method, url, **kwargs):
        """Запрос с токеном приложения для методов, не требующих пользователя."""
        if not self.app_access_token and not self.get_application_access_token():
            raise RuntimeError("Не удалось получить токен приложения HH")

        headers = build_hh_headers(self.user_agent, self.app_access_token)
        headers.update(kwargs.pop('headers', {}))
        kwargs['headers'] = headers

        response = self.session.request(method, url, timeout=REQUEST_TIMEOUT_SECONDS, **kwargs)
        if response.status_code == 401:
            self.app_access_token = None
            if not self.get_application_access_token():
                raise RuntimeError("Не удалось обновить токен приложения HH")
            kwargs['headers'] = build_hh_headers(self.user_agent, self.app_access_token)
            response = self.session.request(method, url, timeout=REQUEST_TIMEOUT_SECONDS, **kwargs)

        if response.status_code == 403:
            logging.error(f"403 Forbidden для app-token {url}")
            logging.error(f"Ответ: {response.text}")

        response.raise_for_status()
        return response

    def make_public_request(self, method, url, **kwargs):
        """Запрос без авторизации (публичный API)"""
        headers = build_hh_headers(self.user_agent)
        headers.update(kwargs.pop('headers', {}))

        try:
            # Используем прямой запрос без сессии (чтобы избежать cookies от авторизации)
            response = requests.request(method, url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS, **kwargs)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            if hasattr(e, 'response') and e.response is not None:
                logging.error(f"Ответ API: {e.response.text[:300]}")
            logging.error(f"Ошибка публичного запроса {method} {url}: {e}")
            raise

    def get_my_resumes(self):
        """Получает список резюме пользователя"""
        url = f"{self.base_url}/resumes/mine"

        try:
            response = self.make_authenticated_request('GET', url)
            data = response.json()
            resumes = data.get('items', [])

            print(f"\n📋 Ваши резюме:")
            for resume in resumes:
                resume_id = resume.get('id')
                title = resume.get('title', 'Без названия')
                status = resume.get('status', {}).get('name', 'Неизвестен')
                print(f"   ID: {resume_id}")
                print(f"   Название: {title}")
                print(f"   Статус: {status}")
                print(f"   ---")

            return resumes

        except requests.exceptions.HTTPError as e:
            if hasattr(e, 'response') and e.response is not None and e.response.status_code == 403:
                print("\n⚠️ API не дает доступ к /resumes/mine")
                print("💡 Это ограничение приложения HH.ru, продолжаем с указанным resume_id...")
            else:
                logging.error(f"Ошибка получения резюме: {e}")
            return None  # None = не удалось проверить, но можно продолжить
        except Exception as e:
            logging.error(f"Ошибка получения резюме: {e}")
            return None

    def check_resume_status(self):
        """Проверяет статус резюме"""
        resumes = self.get_my_resumes()

        # Если API не дает доступ к резюме, продолжаем с указанным resume_id
        if resumes is None:
            print(f"✅ Используем резюме ID: {self.resume_id}")
            return True

        if not resumes:
            print("❌ Резюме не найдены")
            return False

        resume_found = False
        for resume in resumes:
            if resume.get('id') == self.resume_id:
                resume_found = True
                status = resume.get('status', {})
                status_id = status.get('id', 'unknown')
                status_name = status.get('name', 'Неизвестен')

                print(f"\n✅ Резюме найдено!")
                print(f"   Статус: {status_name}")

                if status_id != 'published':
                    print(f"   ⚠️ ВНИМАНИЕ: Резюме не опубликовано!")
                    return False

                return True

        if not resume_found:
            print(f"\n⚠️ Резюме {self.resume_id} не найдено в списке, но продолжаем...")
            return True  # Продолжаем работу

    def get_my_applications(self):
        url = f"{self.base_url}/negotiations"

        try:
            response = self.make_authenticated_request('GET', url, params={'per_page': 5})
            data = response.json()

            applications = data.get('items', [])
            total = data.get('found', 0)
            self.api_negotiations_available = True

            print(f"\n📊 Всего откликов: {total}")
            print(f"Последние отклики:")

            if not applications:
                print("   Отклики не найдены")
            else:
                for i, app in enumerate(applications[:5], 1):
                    vacancy = app.get('vacancy', {})
                    created_at = app.get('created_at', '')

                    if created_at:
                        try:
                            date_obj = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                            created_at = date_obj.strftime('%Y-%m-%d %H:%M')
                        except:
                            created_at = created_at[:16]

                    print(f"   {i}. {vacancy.get('name', 'Без названия')} - {created_at}")

            return total

        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 403:
                self.api_negotiations_available = False
                print("⚠️ API /negotiations закрыт. Отклики будут отправляться через браузерный Selenium-режим.")
                return None

            print(f"Ошибка получения откликов: {e}")
            return None
        except Exception as e:
            print(f"Ошибка получения откликов: {e}")
            return None

    def get_vacancy_priority(self, vacancy):
        """Определяет приоритет вакансии (чем меньше число, тем выше приоритет)"""
        name = vacancy.get('name', '').lower()
        snippet = vacancy.get('snippet', {})
        requirement = (snippet.get('requirement') or '').lower()
        responsibility = (snippet.get('responsibility') or '').lower()
        full_text = f"{name} {requirement} {responsibility}"

        # ПРИОРИТЕТ 1 - Чистая кибербезопасность и пентестинг
        priority_1_keywords = [
            'пентест', 'pentest', 'penetration test',
            'ethical hacker', 'этичный хакер', 'white hat',
            'bug bounty', 'vulnerability researcher',
            'red team', 'offensive security',
            'security researcher', 'исследователь безопасности',
            'exploit', 'zero day', '0day',
            'кибербезопасность', 'cybersecurity', 'cyber security'
        ]

        # ПРИОРИТЕТ 2 - Информационная безопасность
        priority_2_keywords = [
            'информационная безопасность', 'информационной безопасности',
            'information security', 'infosec', 'it security',
            'security analyst', 'security engineer', 'security architect',
            'безопасность приложений', 'application security', 'appsec',
            'soc analyst', 'soc engineer', 'security operations',
            'incident response', 'threat intelligence', 'threat hunting',
            'malware analyst', 'reverse engineer', 'forensics'
        ]

        # ПРИОРИТЕТ 3 - Специализированная ИБ
        priority_3_keywords = [
            'siem', 'dlp', 'waf', 'ids', 'ips', 'edr', 'xdr',
            'devsecops', 'secops', 'security automation',
            'cloud security', 'network security', 'web security',
            'mobile security', 'iot security',
            'blue team', 'purple team',
            'security audit', 'security compliance', 'grc',
            'iso 27001', 'pci dss', 'gdpr'
        ]

        # ПРИОРИТЕТ 4 - Защита данных и крипто
        priority_4_keywords = [
            'защита информации', 'защита данных',
            'криптограф', 'шифрован', 'crypto',
            'blockchain security', 'smart contract audit',
            'фстэк', 'скзи', 'pki',
            'data protection', 'privacy engineer'
        ]

        # Проверяем приоритеты
        for keyword in priority_1_keywords:
            if keyword in name or keyword in full_text:
                return 1

        for keyword in priority_2_keywords:
            if keyword in name or keyword in full_text:
                return 2

        for keyword in priority_3_keywords:
            if keyword in name or keyword in full_text:
                return 3

        for keyword in priority_4_keywords:
            if keyword in name or keyword in full_text:
                return 4

        # ПРИОРИТЕТ 5 - Разработка с безопасностью
        if any(kw in full_text for kw in ['secure', 'security', 'безопасн']) and \
           any(kw in full_text for kw in ['developer', 'разработчик', 'python', 'javascript']):
            return 5

        # ПРИОРИТЕТ 6 - Чистая разработка
        if any(kw in full_text for kw in ['developer', 'разработчик', 'программист', 'python', 'javascript']):
            return 6

        # ПРИОРИТЕТ 7 - Остальное IT
        return 7

    def is_vacancy_suitable(self, vacancy):
        """Проверка с приоритетом на кибербезопасность"""
        name = vacancy.get('name', '').lower()
        snippet = vacancy.get('snippet', {})
        requirement = (snippet.get('requirement') or '').lower()
        responsibility = (snippet.get('responsibility') or '').lower()

        # Объединяем все тексты для проверки
        full_text = f"{name} {requirement} {responsibility}"

        # === СТРОГИЕ ИСКЛЮЧЕНИЯ ===
        strict_exclusions = [
            # НЕ IT безопасность
            'техника безопасности', 'охрана труда', 'от и тб',
            'промышленная безопасность', 'пожарная безопасность',
            'радиационная безопасность', 'экологическая безопасность',
            'транспортная безопасность', 'физическая охрана',
            'охранник', 'вахтер', 'сторож', 'контролер кпп',

            # Промышленные инженеры
            'инженер-конструктор', 'инженер кипиа', 'асутп',
            'инженер-механик', 'инженер-электрик',
            'инженер-строитель', 'инженер-технолог',
            'главный инженер карьера', 'главный инженер завода',
            'инженер по ремонту', 'инженер по наладке',
            'инженер технического надзора',

            # Другое нерелевантное
            'менеджер по продажам', 'торговый представитель',
            'кассир', 'продавец', 'водитель', 'курьер',
            'повар', 'официант', 'бармен', 'уборщица'
        ]

        # Проверяем строгие исключения
        for exclusion in strict_exclusions:
            if exclusion in name or exclusion in full_text:
                return False

        # === КИБЕРБЕЗОПАСНОСТЬ - ВСЕГДА ПОДХОДИТ ===
        cybersec_keywords = [
            # Основные термины
            'информационная безопасность', 'информационной безопасности',
            'кибербезопасность', 'cybersecurity', 'cyber security',
            'information security', 'infosec', 'it security', 'itsec',

            # Пентестинг и исследования
            'пентест', 'pentest', 'penetration', 'пентестер', 'pentester',
            'ethical hack', 'этичный хакер', 'white hat', 'gray hat',
            'bug bounty', 'bug hunter', 'vulnerability', 'exploit',
            'security research', 'исследователь безопасности',
            'red team', 'offensive', 'attack simulation',

            # SOC и мониторинг
            'soc', 'security operations', 'security operation center',
            'siem', 'soar', 'xdr', 'edr', 'mdr', 'ndr',
            'incident response', 'incident handler', 'dfir',
            'threat', 'threat intelligence', 'threat hunting',
            'cyber threat', 'киберугроз', 'кибератак',

            # Анализ и защита
            'security analyst', 'security engineer', 'security architect',
            'malware', 'вредоносн', 'virus', 'вирус',
            'reverse engineer', 'реверс', 'forensic', 'форензик',
            'blue team', 'purple team', 'defense',

            # Специализированные области
            'application security', 'appsec', 'web security',
            'cloud security', 'network security', 'endpoint security',
            'mobile security', 'iot security', 'devsecops', 'secops',
            'security automation', 'security orchestration',

            # Защита данных
            'dlp', 'data loss', 'data protection', 'защита информации',
            'защита данных', 'защита персональных', 'конфиденциальн',
            'криптограф', 'шифрован', 'crypto', 'encryption',
            'pki', 'скзи', 'фстэк', 'ксзи',

            # Аудит и комплаенс
            'security audit', 'аудит безопасности', 'security assessment',
            'compliance', 'grc', 'iso 27001', 'iso27001',
            'pci dss', 'pci-dss', 'gdpr', 'sox',

            # Другие маркеры ИБ
            'waf', 'ids', 'ips', 'firewall', 'фаервол', 'межсетев',
            'vpn', 'ztna', 'zero trust', 'sase', 'casb',
            'антивирус', 'antivirus', 'av', 'sandbox',
            'безопасность приложений', 'безопасность сети',
            'безопасность инфраструктуры', 'security specialist',
            'security expert', 'security consultant', 'security admin',
            'security manager', 'security director', 'ciso', 'cso'
        ]

        # Проверяем наличие ключевых слов кибербезопасности
        for keyword in cybersec_keywords:
            if keyword in name or keyword in full_text:
                return True

        # === РАЗРАБОТКА (вторичный приоритет) ===
        dev_keywords = [
            'python', 'javascript', 'developer', 'разработчик',
            'программист', 'backend', 'frontend', 'fullstack'
        ]

        # Разработка принимается только если есть упоминание безопасности
        has_dev = any(kw in full_text for kw in dev_keywords)
        has_security_context = any(kw in full_text for kw in ['secur', 'безопасн', 'защит'])

        if has_dev and has_security_context:
            return True

        # Чистая разработка - только высокоприоритетные языки
        if any(kw in name for kw in ['python developer', 'python разработчик',
                                      'javascript developer', 'security developer']):
            return True

        return False

    def get_vacancies(self, search_params):
        """Поиск с приоритетом на кибербезопасность"""

        # Синхронизация и проверка кеша
        self.sync_cache_with_applied()

        cached_vacancies = self.load_vacancies_cache()

        # Используем кеш только если в нем достаточно вакансий
        if cached_vacancies is not None and len(cached_vacancies) >= self.min_vacancies_in_cache:
            print("💾 Используется кеш вакансий")
            print(f"✅ В кеше достаточно вакансий: {len(cached_vacancies)}")
            # Сортируем кешированные вакансии по приоритету
            cached_vacancies.sort(key=lambda v: self.get_vacancy_priority(v))
            return cached_vacancies
        elif cached_vacancies is not None and len(cached_vacancies) < self.min_vacancies_in_cache:
            print(f"⚠️ В кеше мало вакансий ({len(cached_vacancies)} < {self.min_vacancies_in_cache})")
            print("🔄 Автоматическое обновление поиска...")
        else:
            print("🔄 Выполняется новый поиск вакансий...")

        all_vacancies = []
        processed_ids = set()
        excluded_with_tests = 0
        excluded_already_applied = 0
        excluded_not_suitable = 0
        network_errors_without_results = 0
        stop_search_reason = None

        # ПРИОРИТЕТНЫЕ запросы по кибербезопасности
        search_queries = [
            # === ТОПОВЫЕ ЗАПРОСЫ ПО КИБЕРБЕЗОПАСНОСТИ ===
            '"пентестер"',
            '"pentester"',
            '"penetration tester"',
            '"ethical hacker"',
            '"security researcher"',
            '"bug bounty"',
            '"red team"',
            '"offensive security"',
            '"vulnerability researcher"',
            '"exploit developer"',

            '"кибербезопасность"',
            '"cybersecurity"',
            '"cyber security"',
            '"информационная безопасность"',
            '"information security"',
            '"security analyst"',
            '"security engineer"',
            '"security architect"',
            '"security specialist"',

            '"SOC analyst"',
            '"SOC engineer"',
            '"SIEM administrator"',
            '"incident response"',
            '"threat intelligence"',
            '"threat hunting"',
            '"malware analyst"',
            '"reverse engineer"',
            '"forensics analyst"',

            '"application security"',
            '"appsec engineer"',
            '"devsecops"',
            '"security operations"',
            '"blue team"',
            '"purple team"',

            # === РАСШИРЕННЫЕ ЗАПРОСЫ ПО ИБ ===
            'пентест',
            'pentest',
            'penetration testing',
            'ethical hacking',
            'vulnerability assessment',
            'security testing',
            'security audit',

            'кибербезопасность',
            'cybersecurity',
            'информационная безопасность',
            'information security',
            'IT security',
            'security operations center',

            'SIEM SOAR',
            'XDR EDR MDR',
            'DLP WAF IDS IPS',
            'incident management',
            'security monitoring',

            'cloud security',
            'network security',
            'web application security',
            'mobile security',
            'endpoint security',

            'защита информации',
            'защита данных',
            'безопасность приложений',
            'безопасность инфраструктуры',

            'криптография',
            'СКЗИ ФСТЭК',
            'compliance security',
            'GRC analyst',
            'ISO 27001',
            'PCI DSS',

            # === РАЗРАБОТКА С БЕЗОПАСНОСТЬЮ ===
            'security developer',
            'secure coding',
            'security engineer developer',
            'python security',
            'security automation',

            # === СПЕЦИФИЧНЫЕ РОЛИ ===
            'DevSecOps engineer',
            'AppSec engineer',
            'Cloud Security Architect',
            'Zero Trust Architect',
            'Blockchain Security',
            'IoT Security',
            'OT Security',
            'ICS Security',
            'SCADA Security'
        ]

        url = f"{self.base_url}/vacancies"
        total_queries = len(search_queries)

        print(f"\n⚙️ Параметры поиска:")
        print(f"   🎯 ПРИОРИТЕТ: Кибербезопасность и пентестинг")
        print(f"   • Регион: Россия")
        print(f"   • Запросов: {total_queries}")

        for query_idx, query in enumerate(search_queries, 1):
            if stop_search_reason:
                break

            if len(all_vacancies) >= SEARCH_RESULT_LIMIT:
                break

            clean_query = query.replace('"', '')
            print(f"🔍 [{query_idx}/{total_queries}] {clean_query[:40]}... | Найдено ИБ: {len(all_vacancies)}")

            for page in range(SEARCH_PAGE_LIMIT):
                params = {
                    'text': query,
                    'per_page': 100,
                    'page': page,
                    'area': 113  # Россия
                }

                try:
                    response = self.make_application_request('GET', url, params=params)
                    data = response.json()
                    network_errors_without_results = 0
                    vacancies = data.get('items', [])

                    if not vacancies:
                        break

                    added_count = 0

                    for v in vacancies:
                        v_id = str(v.get('id'))

                        if not v_id or v_id in processed_ids:
                            continue

                        processed_ids.add(v_id)

                        if v_id in self.applied_vacancies:
                            excluded_already_applied += 1
                            continue

                        # Проверка соответствия (с приоритетом на ИБ)
                        if not self.is_vacancy_suitable(v):
                            excluded_not_suitable += 1
                            continue

                        if v.get('has_test', False):
                            excluded_with_tests += 1
                            continue

                        all_vacancies.append(v)
                        added_count += 1

                    if added_count > 0:
                        print(f"   🛡️ Страница {page + 1}: +{added_count} ИБ вакансий")

                    pages_total = data.get('pages', 0)
                    if page + 1 >= pages_total:
                        break

                    time.sleep(0.2)  # Маленькая задержка

                except requests.exceptions.HTTPError as e:
                    if e.response is not None and e.response.status_code == 403:
                        if all_vacancies:
                            stop_search_reason = "HH API запретил дальнейший поиск, использую уже найденные вакансии"
                            print(f"\n⚠️ {stop_search_reason}: {len(all_vacancies)}")
                            logging.error(f"Доступ к /vacancies запрещен после частичного поиска: {e.response.text[:300]}")
                            break

                        print("\n❌ HH API запретил доступ к поиску вакансий через /vacancies даже по токену приложения")
                        print("   Это уже ограничение приложения, сети или текущих правил HH API.")
                        print("   Без доступа HH к /vacancies бот не сможет найти вакансии и отправить автоотклики.")
                        logging.error(f"Доступ к /vacancies запрещен: {e.response.text[:300]}")
                        return []
                    logging.error(f"Ошибка поиска {query}: {e}")
                    break
                except requests.exceptions.RequestException as e:
                    logging.error(f"Сетевая ошибка поиска {query}: {e}")
                    if all_vacancies:
                        stop_search_reason = (
                            f"Сетевая ошибка поиска, использую частичный результат: "
                            f"{len(all_vacancies)} вакансий"
                        )
                        print(f"   ⚠️ {stop_search_reason}")
                        break

                    network_errors_without_results += 1
                    if network_errors_without_results >= MAX_SEARCH_NETWORK_ERRORS_WITHOUT_RESULTS:
                        stop_search_reason = (
                            "Сеть HH API не отвечает, подходящие вакансии до сбоя не найдены"
                        )
                        print(f"   ⚠️ {stop_search_reason}")
                        break

                    break
                except Exception as e:
                    logging.error(f"Ошибка поиска {query}: {e}")
                    break

        # СОРТИРОВКА ПО ПРИОРИТЕТУ (кибербезопасность первая)
        all_vacancies.sort(key=lambda v: self.get_vacancy_priority(v))

        print(f"\n📊 ИТОГИ ПОИСКА КИБЕРБЕЗОПАСНОСТИ:")
        if stop_search_reason:
            print(f"   ⚠️ Поиск остановлен досрочно: {stop_search_reason}")
        print(f"   🛡️ Найдено ИБ вакансий: {len(all_vacancies)}")
        print(f"   ⏭️ С тестами: {excluded_with_tests}")
        print(f"   📝 Уже обработано: {excluded_already_applied}")
        print(f"   🚫 Не подходят: {excluded_not_suitable}")
        print(f"   📋 Всего проверено: {len(processed_ids)}")

        if all_vacancies:
            # Подсчет по приоритетам
            priority_counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0, 7: 0}
            for v in all_vacancies:
                priority = self.get_vacancy_priority(v)
                priority_counts[priority] = priority_counts.get(priority, 0) + 1

            print(f"\n🎯 Распределение по приоритетам:")
            priority_names = {
                1: "🔴 Пентестинг и Red Team",
                2: "🟠 Информационная безопасность",
                3: "🟡 Специализированная ИБ",
                4: "🟢 Защита данных и крипто",
                5: "🔵 Разработка с безопасностью",
                6: "🟣 Чистая разработка",
                7: "⚪ Другое IT"
            }

            for priority in sorted(priority_counts.keys()):
                if priority_counts[priority] > 0:
                    print(f"   {priority_names.get(priority, f'Приоритет {priority}')}: {priority_counts[priority]}")

            self.save_vacancies_cache(all_vacancies)
        else:
            print("\n⚠️ Не найдено подходящих вакансий по кибербезопасности!")

        return all_vacancies

    def generate_cover_letter(self, vacancy_details):
        """Сопроводительное письмо с акцентом на кибербезопасность"""
        position_name = vacancy_details.get('name', 'данную позицию')
        company_name = vacancy_details.get('employer', {}).get('name', 'вашей компании')

        # Определяем тип вакансии
        name_lower = position_name.lower()

        # Для пентестинга
        if any(kw in name_lower for kw in ['пентест', 'pentest', 'ethical hack', 'red team']):
            templates = [
                f"""Здравствуйте!

Заинтересовала позиция "{position_name}" в {company_name}.

Имею опыт в проведении тестирования на проникновение и поиске уязвимостей.
Готов применить свои навыки для повышения уровня защищенности инфраструктуры компании.

С уважением!""",

                f"""Добрый день!

Позиция "{position_name}" полностью соответствует моей специализации.

Готов проводить комплексное тестирование безопасности и помогать в устранении выявленных уязвимостей.

Буду рад обсудить детали!"""
            ]
        # Для кибербезопасности
        elif any(kw in name_lower for kw in ['безопасност', 'security', 'soc', 'siem']):
            templates = [
                f"""Здравствуйте!

С интересом рассмотрел вакансию "{position_name}" в {company_name}.

Специализируюсь на информационной безопасности и готов внести вклад в защиту цифровых активов компании.

С уважением!""",

                f"""Добрый день!

Позиция "{position_name}" соответствует моему опыту в области кибербезопасности.

Готов применить свои знания для обеспечения надежной защиты информационной инфраструктуры {company_name}.

Благодарю за рассмотрение!"""
            ]
        # Для разработки
        elif any(kw in name_lower for kw in ['developer', 'разработчик', 'программист']):
            templates = [
                f"""Здравствуйте!

Заинтересовала позиция "{position_name}" в {company_name}.

Имею опыт разработки с акцентом на безопасность кода и защищенность приложений.

С уважением!""",

                f"""Добрый день!

Рассматриваю вакансию "{position_name}" как возможность применить навыки безопасной разработки.

Готов создавать качественные и защищенные решения для {company_name}.

Буду рад сотрудничеству!"""
            ]
        else:
            templates = [
                f"""Здравствуйте!

Заинтересовала позиция "{position_name}" в {company_name}.

Мой опыт в IT и информационной безопасности позволит эффективно решать поставленные задачи.

С уважением!"""
            ]

        return random.choice(templates)

    def apply_to_vacancy(self, vacancy_id, cover_letter=None):
        """Отправка отклика на вакансию"""
        url = f"{self.base_url}/negotiations"

        # Данные для отклика
        form_data = {
            'vacancy_id': str(vacancy_id),
            'resume_id': self.resume_id
        }

        if cover_letter:
            form_data['message'] = cover_letter

        try:
            # Не передаём дополнительные headers - make_authenticated_request сам добавит нужные
            response = self.make_authenticated_request('POST', url, data=form_data)

            # Принимаем и 200, и 201 как успешные
            if response.status_code in [200, 201]:
                self.save_applied_vacancy(vacancy_id)
                return True, "success", "ok"
            else:
                # Логируем неожиданный код
                logging.warning(f"Неожиданный код ответа: {response.status_code}")
                logging.warning(f"Ответ: {response.text}")
                return False, f"unexpected_code_{response.status_code}", None

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 403:
                try:
                    error_data = e.response.json()
                    errors = error_data.get('errors', [])
                    if errors and errors[0].get('value') == 'test_required':
                        return False, "test_required", None
                    elif errors and errors[0].get('value') == 'already_applied':
                        self.save_applied_vacancy(vacancy_id)
                        return False, "already_applied", None
                    elif errors and errors[0].get('value') == 'application_denied':
                        return False, "application_denied", None
                except (ValueError, KeyError) as parse_error:
                    logging.warning(f"Не удалось разобрать 403 при отклике: {parse_error}")
                return False, "forbidden", None

            elif e.response.status_code == 400:
                try:
                    error_data = e.response.json()
                    description = error_data.get('description', '')
                    if 'Daily negotiations limit is exceeded' in description:
                        return False, "daily_limit_exceeded", None
                except (ValueError, KeyError) as parse_error:
                    logging.warning(f"Не удалось разобрать 400 при отклике: {parse_error}")
                return False, "bad_request", None

            else:
                return False, f"http_error_{e.response.status_code}", None

        except Exception as e:
            logging.error(f"Ошибка отклика на вакансию {vacancy_id}: {e}")
            return False, "network_error", None

    def run_manual_applications(self, search_params, limit=DEFAULT_MANUAL_APPLY_LIMIT):
        print(f"\n{'='*70}")
        print("🧭 РУЧНЫЕ ОТКЛИКИ ЧЕРЕЗ БРАУЗЕР")
        print(f"{'='*70}")

        vacancies = self.get_vacancies(search_params)
        if not vacancies:
            print("\n❌ Нет вакансий для ручного отклика")
            return

        opened_count = 0
        for vacancy in vacancies[:limit]:
            vacancy_id = vacancy.get('id')
            vacancy_name = vacancy.get('name', 'Без названия')
            employer = vacancy.get('employer', {}).get('name', 'Неизвестно')
            apply_url = get_vacancy_apply_url(vacancy)

            if not vacancy_id or not apply_url:
                print(f"\n⚠️ Пропуск: нет ссылки для отклика: {vacancy_name}")
                continue

            cover_letter = self.generate_cover_letter(vacancy)
            opened_count += 1

            print(f"\n[{opened_count}/{min(limit, len(vacancies))}] {vacancy_name}")
            print(f"🏢 {employer}")
            print(f"🔗 {apply_url}")
            print("\n📨 Письмо:")
            print("-" * 50)
            print(cover_letter)
            print("-" * 50)

            webbrowser.open(apply_url)

            answer = input("Отклик отправлен? [y/N/q]: ").strip().lower()
            if answer == 'q':
                print("⏹️ Остановлено")
                break
            if answer == 'y':
                self.save_applied_vacancy(vacancy_id)
                print("✅ Отмечено как обработанное")
            else:
                print("⏭️ Не отмечено обработанным")

        print(f"\n📊 Открыто вакансий: {opened_count}")

    def run_selenium_api_cache(self, limit=DEFAULT_SELENIUM_APPLY_LIMIT):
        selenium_script = os.path.join(SCRIPT_DIR, 'hh_selenium.py')
        if not os.path.exists(selenium_script):
            raise FileNotFoundError(f"Не найден Selenium-скрипт: {selenium_script}")

        command = [
            sys.executable,
            selenium_script,
            '--api-cache',
            '--limit',
            str(limit),
        ]

        print(f"\n{'='*70}")
        print("🌐 API-отклики закрыты. Запускаю Selenium-отклики по найденным вакансиям.")
        print(f"   Лимит: {limit}")
        print(f"   Команда: {' '.join(command)}")
        print(f"{'='*70}\n")

        completed_process = subprocess.run(command, cwd=SCRIPT_DIR)
        if completed_process.returncode != 0:
            raise RuntimeError(f"Selenium-режим завершился с кодом {completed_process.returncode}")

    def run_auto_applications(self, search_params):
        print(f"\n{'='*70}")
        print(f"🛡️ АВТООТКЛИКИ: ПРИОРИТЕТ - КИБЕРБЕЗОПАСНОСТЬ")
        print(f"{'='*70}")
        print(f"📁 Рабочая директория: {SCRIPT_DIR}")

        # Синхронизация кеша при запуске
        print("\n🔄 Синхронизация кеша...")
        self.sync_cache_with_applied()

        print("\n🔍 Проверка резюме...")
        if not self.check_resume_status():
            print("❌ Проблема с резюме!")
            return

        print("\n📊 Проверка существующих откликов...")
        total_applications = self.get_my_applications()

        print(f"\n🎯 Поиск вакансий КИБЕРБЕЗОПАСНОСТИ...")

        vacancies = self.get_vacancies(search_params)

        if not vacancies:
            print("\n❌ Подходящие вакансии по кибербезопасности не найдены")
            print("\n💡 Рекомендации:")
            print("   1. Подождите несколько часов - появятся новые вакансии")
            print("   2. Используйте --clear-cache для принудительного обновления")
            return

        print(f"\n✅ К обработке: {len(vacancies)} вакансий (отсортированы по приоритету)")
        print(f"📊 Откликов за последние 24 часа: {self.applied_today}/{self.max_applications_per_day}")

        print("\n🎯 ТОП вакансии (первые будут обработаны):")
        for i, vacancy in enumerate(vacancies[:15], 1):
            employer = vacancy.get('employer', {}).get('name', 'Неизвестно')
            vacancy_name = vacancy.get('name', 'Без названия')
            priority = self.get_vacancy_priority(vacancy)

            # Эмодзи по приоритету
            priority_emoji = {
                1: "🔴",  # Пентестинг
                2: "🟠",  # ИБ
                3: "🟡",  # Спец ИБ
                4: "🟢",  # Защита данных
                5: "🔵",  # Dev+Security
                6: "🟣",  # Dev
                7: "⚪"   # Другое
            }.get(priority, "⚪")

            print(f"   {i}. {priority_emoji} {vacancy_name}")
            print(f"      🏢 {employer}")

        if len(vacancies) > 15:
            print(f"   ... и еще {len(vacancies) - 15} вакансий")

        if self.api_negotiations_available is False:
            self.run_selenium_api_cache(self.selenium_apply_limit)
            return

        print(f"\n🚀 Начинаем отклики (приоритет на кибербезопасность)...")
        print(f"⚙️ Задержка между откликами: 2-5 сек")

        successful_applications = 0
        processed = 0
        error_stats = {}
        daily_limit_reached = False
        priority_stats = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0, 7: 0}

        for vacancy in vacancies:
            if daily_limit_reached or self.applied_today >= self.max_applications_per_day:
                print(f"\n⏸️ Достигнут лимит за 24 часа ({self.max_applications_per_day})")
                break

            vacancy_id = vacancy.get('id')
            vacancy_name = vacancy.get('name', 'Без названия')
            employer = vacancy.get('employer', {}).get('name', 'Неизвестно')
            priority = self.get_vacancy_priority(vacancy)
            processed += 1

            if processed % 10 == 0:
                print(f"\n📊 Прогресс: {processed}/{len(vacancies)} | ✅ Успешно: {successful_applications} | 🎯 ИБ: {priority_stats.get(1, 0) + priority_stats.get(2, 0) + priority_stats.get(3, 0)}")

            cover_letter = self.generate_cover_letter(vacancy)

            # Эмодзи по приоритету
            priority_emoji = {
                1: "🔴",  # Пентестинг
                2: "🟠",  # ИБ
                3: "🟡",  # Спец ИБ
                4: "🟢",  # Защита данных
                5: "🔵",  # Dev+Security
                6: "🟣",  # Dev
                7: "⚪"   # Другое
            }.get(priority, "⚪")

            print(f"\n{priority_emoji} [{processed}/{len(vacancies)}] {vacancy_name}")
            print(f"🏢 {employer}")

            success, error_type, _ = self.apply_to_vacancy(vacancy_id, cover_letter)

            if success:
                successful_applications += 1
                priority_stats[priority] = priority_stats.get(priority, 0) + 1
                print(f"✅ Успешно отправлен отклик!")
            else:
                error_stats[error_type] = error_stats.get(error_type, 0) + 1

                error_messages = {
                    'already_applied': '📝 Уже откликались ранее',
                    'daily_limit_exceeded': '📊 Достигнут лимит за 24 часа',
                    'test_required': '📋 Требуется тест',
                    'application_denied': '🚫 Работодатель не принимает',
                    'forbidden': '🚫 Доступ запрещен',
                    'bad_request': '❌ Только внешний отклик',
                    'network_error': '🌐 Сетевая ошибка'
                }

                error_msg = error_messages.get(error_type, f"❌ {error_type}")
                print(f"{error_msg}")

                if error_type in ['daily_limit_exceeded']:
                    daily_limit_reached = True
                    break

            delay = random.randint(2, 5)
            time.sleep(delay)

        print(f"\n{'='*70}")
        print(f"📊 ИТОГИ РАБОТЫ:")
        print(f"   Обработано: {processed}")
        print(f"   ✅ Успешно: {successful_applications}")
        print(f"   📈 Всего за последние 24 часа: {self.applied_today}")

        if successful_applications > 0:
            print(f"\n🎯 Успешные отклики по приоритетам:")
            priority_names = {
                1: "🔴 Пентестинг и Red Team",
                2: "🟠 Информационная безопасность",
                3: "🟡 Специализированная ИБ",
                4: "🟢 Защита данных",
                5: "🔵 Разработка с безопасностью",
                6: "🟣 Чистая разработка",
                7: "⚪ Другое IT"
            }

            for priority in sorted(priority_stats.keys()):
                if priority_stats[priority] > 0:
                    print(f"   {priority_names.get(priority)}: {priority_stats[priority]}")

        if processed > 0:
            success_rate = (successful_applications / processed) * 100
            print(f"\n   📊 Успешность: {success_rate:.1f}%")

        if error_stats:
            print(f"\n📋 Статистика ошибок:")
            for error_type, count in sorted(error_stats.items(), key=lambda x: x[1], reverse=True):
                print(f"   • {error_type}: {count}")

        # Подсчет откликов на ИБ
        security_applications = sum(priority_stats.get(i, 0) for i in [1, 2, 3, 4])
        if security_applications > 0:
            print(f"\n🛡️ ОТКЛИКОВ НА КИБЕРБЕЗОПАСНОСТЬ: {security_applications} из {successful_applications} ({security_applications/max(successful_applications, 1)*100:.0f}%)")

def main():
    search_params = {}
    manual_apply_limit = DEFAULT_MANUAL_APPLY_LIMIT
    selenium_apply_limit = DEFAULT_SELENIUM_APPLY_LIMIT
    manual_apply_mode = False

    print("🛡️ HH.ru Bot: КИБЕРБЕЗОПАСНОСТЬ / ПЕНТЕСТИНГ")
    print("=" * 50)
    print("🎯 Приоритет: Информационная безопасность")

    # Обработка аргументов командной строки
    if len(sys.argv) > 1:
        if sys.argv[1] == '--clear-cache':
            cache_file = os.path.join(SCRIPT_DIR, 'vacancies_cache.json')
            if os.path.exists(cache_file):
                os.remove(cache_file)
                print("🗑️ Кеш очищен!")
                print("Запустите скрипт снова для нового поиска")
                return
        elif sys.argv[1] == '--manual-apply':
            manual_apply_mode = True
            if len(sys.argv) > 2:
                try:
                    manual_apply_limit = max(1, int(sys.argv[2]))
                except ValueError:
                    print("❌ Лимит для --manual-apply должен быть числом")
                    return
        elif sys.argv[1] == '--selenium-limit':
            if len(sys.argv) <= 2:
                print("❌ После --selenium-limit нужно число")
                return
            try:
                selenium_apply_limit = max(1, int(sys.argv[2]))
            except ValueError:
                print("❌ Лимит для --selenium-limit должен быть числом")
                return
        elif sys.argv[1] == '--help':
            print("\nОпции:")
            print("  --clear-cache  - Принудительно очистить кеш")
            print("  --manual-apply [N] - Открыть N найденных вакансий для ручного отклика")
            print("  --selenium-limit N - Лимит Selenium-откликов за запуск, по умолчанию 200")
            print("  --help        - Показать справку")
            print("\nОбычный запуск автоматически включает Selenium, если HH API закрывает /negotiations.")
            print("\n🎯 Фокус на:")
            print("  • Пентестинг и Red Team")
            print("  • Информационная безопасность")
            print("  • SOC, SIEM, Incident Response")
            print("  • Application Security")
            print("  • Cloud Security")
            return

    try:
        credentials = load_runtime_credentials()
        applicant = HHAutoApplicant(
            credentials['HH_CLIENT_ID'],
            credentials['HH_CLIENT_SECRET'],
            credentials['HH_REDIRECT_URI'],
            credentials['HH_RESUME_ID'],
        )
        if manual_apply_mode:
            applicant.run_manual_applications(search_params, manual_apply_limit)
        else:
            applicant.selenium_apply_limit = selenium_apply_limit
            applicant.run_auto_applications(search_params)

    except KeyboardInterrupt:
        print("\n⏹️ Остановлено пользователем")
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
