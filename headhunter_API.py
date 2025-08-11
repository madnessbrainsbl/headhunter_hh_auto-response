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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('hh_auto_apply.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

class HHAutoApplicant:
    def __init__(self, client_id, client_secret, redirect_uri, resume_id):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.base_url = 'https://api.hh.ru'
        self.oauth_url = 'https://hh.ru/oauth'
        self.access_token = None
        self.refresh_token = None
        self.session = requests.Session()
        
        self.resume_id = resume_id
        
        self.max_applications_per_day = 200
        self.min_delay_between_requests = 3
        self.delay_after_429 = 60
        self.applied_today = 0
        self.applied_vacancies_file = 'applied_vacancies.json'
        self.load_applied_vacancies()
        
        self.ensure_token()

    def load_applied_vacancies(self):
        try:
            with open(self.applied_vacancies_file, 'r', encoding='utf-8') as f:
                self.applied_vacancies = json.load(f)
        except FileNotFoundError:
            self.applied_vacancies = {}
        
        today = datetime.now().strftime('%Y-%m-%d')
        self.applied_today = sum(1 for date in self.applied_vacancies.values() 
                                if date.startswith(today))

    def save_applied_vacancy(self, vacancy_id):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.applied_vacancies[str(vacancy_id)] = timestamp
        self.applied_today += 1
        
        try:
            with open(self.applied_vacancies_file, 'w', encoding='utf-8') as f:
                json.dump(self.applied_vacancies, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.error(f"Ошибка сохранения файла откликов: {e}")

    def load_token(self):
        try:
            with open('hh_token.json', 'r', encoding='utf-8') as f:
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
            with open('hh_token.json', 'w', encoding='utf-8') as f:
                json.dump(token_info, f, ensure_ascii=False, indent=2)
            logging.info("Токен сохранен")
        except Exception as e:
            logging.error(f"Ошибка сохранения токена: {e}")

    def get_authorization_url(self):
        params = {
            'response_type': 'code',
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'scope': 'vacancy_response'
        }
        
        auth_url = f"{self.oauth_url}/authorize?" + urlencode(params)
        return auth_url

    def authorize(self):
        print("Требуется авторизация...")
        
        auth_url = self.get_authorization_url()
        print(f"Откройте эту ссылку в браузере: {auth_url}")
        webbrowser.open(auth_url)
        
        callback_url = input("\nВставьте полный URL после авторизации: ")
        
        parsed_url = urlparse(callback_url)
        query_params = parse_qs(parsed_url.query)
        
        if 'code' not in query_params:
            raise Exception("Код авторизации не найден")
        
        auth_code = query_params['code'][0]
        return self.get_access_token(auth_code)

    def get_access_token(self, auth_code):
        token_data = {
            'grant_type': 'authorization_code',
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'redirect_uri': self.redirect_uri,
            'code': auth_code
        }
        
        try:
            response = requests.post(f"{self.oauth_url}/token", data=token_data)
            response.raise_for_status()
            
            token_info = response.json()
            self.access_token = token_info['access_token']
            self.refresh_token = token_info.get('refresh_token')
            
            self.save_token(token_info)
            print("Авторизация успешна!")
            return True
            
        except requests.exceptions.RequestException as e:
            logging.error(f"Ошибка получения токена: {e}")
            return False

    def refresh_access_token(self):
        if not self.refresh_token:
            return False
        
        token_data = {
            'grant_type': 'refresh_token',
            'refresh_token': self.refresh_token
        }
        
        try:
            response = requests.post(f"{self.oauth_url}/token", data=token_data)
            response.raise_for_status()
            
            token_info = response.json()
            self.access_token = token_info['access_token']
            self.save_token(token_info)
            
            logging.info("Токен обновлен")
            return True
            
        except requests.exceptions.RequestException as e:
            logging.error(f"Ошибка обновления токена: {e}")
            return False

    def ensure_token(self):
        if not self.load_token():
            if not self.authorize():
                raise Exception("Не удалось получить токен")

    def make_authenticated_request(self, method, url, **kwargs):
        headers = kwargs.get('headers', {})
        headers['Authorization'] = f'Bearer {self.access_token}'
        headers['User-Agent'] = 'HH-User-Agent'
        kwargs['headers'] = headers
        
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                response = self.session.request(method, url, timeout=10, **kwargs)
                
                if response.status_code == 401:
                    if self.refresh_access_token():
                        headers['Authorization'] = f'Bearer {self.access_token}'
                        response = self.session.request(method, url, timeout=10, **kwargs)
                    else:
                        raise Exception("Не удалось обновить токен")
                
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

    def check_resume_status(self):
        url = f"{self.base_url}/resumes/{self.resume_id}"
        
        try:
            response = self.make_authenticated_request('GET', url)
            resume = response.json()
            
            status = resume.get('status', {})
            status_id = status.get('id', 'unknown')
            status_name = status.get('name', 'Неизвестен')
            
            print(f"Статус резюме: {status_name} ({status_id})")
            
            if status_id != 'published':
                print(f"ВНИМАНИЕ: Резюме не активно! Статус: {status_name}")
                return False
            
            return True
            
        except Exception as e:
            print(f"Ошибка проверки резюме: {e}")
            return False

    def get_my_applications(self):
        url = f"{self.base_url}/negotiations"
        
        try:
            response = self.make_authenticated_request('GET', url, params={'per_page': 5})
            data = response.json()
            
            applications = data.get('items', [])
            total = data.get('found', 0)
            
            print(f"\nВсего откликов: {total}")
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
                            created_at = date_obj.strftime('%d.%m.%Y %H:%M')
                        except:
                            created_at = created_at[:16]
                    
                    print(f"   {i}. {vacancy.get('name', 'Без названия')} - {created_at}")
            
            return total
            
        except Exception as e:
            print(f"Ошибка получения откликов: {e}")
            return 0

    def is_vacancy_suitable(self, vacancy):
        name = vacancy.get('name', '').lower()
        
        # Ключевые слова 
        infosec_keywords = [
            'физическ'
        ]
        
        # Исключения 
        exclusions = [
            'физическ'
        ]
        
        # Проверяем исключения
        for exclusion in exclusions:
            if exclusion in name:
                return False
        
        
        for keyword in infosec_keywords:
            if keyword in name:
                return True
                
        return False

    def get_vacancies(self, search_params):
        """Поиск вакансий по текст"""
        all_vacancies = []
        processed_ids = set()
        
        
        infosec_queries = [

            "текст"
            "текст"
            "текст"
            "текст"
            "текст"
        ]
        
        url = f"{self.base_url}/vacancies"
        
        for query in infosec_queries:
            if len(all_vacancies) >= 500:  # Ограничиваем общее количество
                break
                
            print(f" Поиск вакансий: '{query}' (найдено: {len(all_vacancies)})")
            
            for page in range(10):  
                params = {
                    'text': query,
                    'per_page': 50,
                    'page': page,
                    'order_by': 'publication_time',
                    'period': 30  
                }
                
                try:
                    response = self.make_authenticated_request('GET', url, params=params)
                    data = response.json()
                    vacancies = data.get('items', [])
                    
                    if not vacancies:
                        break
                    
                    added_count = 0
                    for v in vacancies:
                        if len(all_vacancies) >= 500:
                            break
                            
                        v_id = v.get('id')
                        if not v_id or v_id in processed_ids or str(v_id) in self.applied_vacancies:
                            continue
                        
                        if self.is_vacancy_suitable(v):
                            processed_ids.add(v_id)
                            all_vacancies.append(v)
                            added_count += 1
                    
                    if added_count > 0:
                        print(f"   Страница {page + 1}: добавлено {added_count}")
                    
                    pages_total = data.get('pages', 0)
                    if page + 1 >= pages_total:
                        break
                        
                    time.sleep(1)  # Задержка между страницами
                    
                except Exception as e:
                    logging.error(f"Ошибка поиска '{query}': {e}")
                    break
        
        return all_vacancies

    def generate_infosec_cover_letter(self, vacancy_details):
        """Специальное сопроводительное письмо для  вакансий"""
        position_name = vacancy_details.get('name', 'данную позицию')
        company_name = vacancy_details.get('employer', {}).get('name', 'вашей компании')
        
        infosec_templates = [
            f"""Здравствуйте!

Заинтересовала позиция "{position_name}" в {company_name}.

Имею опыт работы в области "текст" и готов применить свои знания для "текст".

С уважением!""",

            f"""Добрый день!

Рассматриваю вакансию "{position_name}" как отличную возможность развиваться в сфере "текст".

Готов внести вклад в "текст" {company_name}.

Буду рад обсудить детали!""",

            f"""Здравствуйте!

Позиция "{position_name}" полностью соответствует моим профессиональным интересам в области "текст".

Готов применить свои навыки "текст" {company_name}.

Благодарю за рассмотрение!""",

            f"""Добрый день!

Область "текст" - моя профессиональная специализация. 

Заинтересован в позиции "{position_name}" и готов обсудить, как могу помочь в  {company_name}.

С уважением!""",
        ]
        
        return random.choice(infosec_templates)

    def apply_to_vacancy(self, vacancy_id, cover_letter=None):
        url = f"{self.base_url}/negotiations"
        
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'HH-User-Agent': 'Auto Apply Bot'
        }
        
        form_data = {
            'vacancy_id': str(vacancy_id),
            'resume_id': self.resume_id
        }
        
        if cover_letter:
            form_data['message'] = cover_letter
        
        try:
            response = self.make_authenticated_request('POST', url, data=form_data, headers=headers)
            
            if response.status_code == 201:
                # Проверяем, что ответ содержит JSON
                try:
                    response_data = response.json()
                    application_id = response_data.get('id', 'unknown')
                except (json.JSONDecodeError, ValueError):
                    application_id = 'success_no_id'
                
                logging.info(f"Отклик создан с ID: {application_id}")
                self.save_applied_vacancy(vacancy_id)
                return True, "success", application_id
            else:
                return False, f"unexpected_code_{response.status_code}", None
                
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 403:
                try:
                    # Проверяем, что ответ содержит JSON
                    if e.response.headers.get('content-type', '').startswith('application/json'):
                        error_data = e.response.json()
                        errors = error_data.get('errors', [])
                        if errors:
                            error_type = errors[0].get('type', 'unknown')
                            error_value = errors[0].get('value', '')
                            
                            if error_value == 'already_applied':
                                self.save_applied_vacancy(vacancy_id)
                                return False, "already_applied", None
                            elif error_value == 'limit_exceeded':
                                return False, "limit_exceeded", None
                    else:
                        # Ответ не JSON
                        logging.warning(f"403 ошибка без JSON ответа для вакансии {vacancy_id}")
                        return False, "forbidden_no_json", None
                except (json.JSONDecodeError, ValueError):
                    logging.warning(f"Не удалось распарсить JSON в 403 ответе для вакансии {vacancy_id}")
                    return False, "forbidden_invalid_json", None
                except Exception as ex:
                    logging.error(f"Ошибка обработки 403 ответа: {ex}")
                    return False, "forbidden_parse_error", None
                    
                return False, "forbidden", None
                
            elif e.response.status_code == 400:
                try:
                    if e.response.headers.get('content-type', '').startswith('application/json'):
                        error_data = e.response.json()
                        description = error_data.get('description', '')
                        
                        if 'Daily negotiations limit is exceeded' in description:
                            return False, "daily_limit_exceeded", None
                    else:
                        logging.warning(f"400 ошибка без JSON ответа для вакансии {vacancy_id}")
                        return False, "bad_request_no_json", None
                except (json.JSONDecodeError, ValueError):
                    logging.warning(f"Не удалось распарсить JSON в 400 ответе для вакансии {vacancy_id}")
                    return False, "bad_request_invalid_json", None
                except Exception as ex:
                    logging.error(f"Ошибка обработки 400 ответа: {ex}")
                    return False, "bad_request_parse_error", None
                    
                return False, "bad_request", None
            else:
                return False, f"http_error_{e.response.status_code}", None
                
        except (json.JSONDecodeError, ValueError) as e:
            logging.error(f"JSON decode error для вакансии {vacancy_id}: {str(e)}")
            return False, "json_decode_error", None
        except Exception as e:
            logging.error(f"Общая ошибка для вакансии {vacancy_id}: {str(e)}")
            return False, "network_error", None

    def run_auto_applications(self, search_params):
        print(f" АВТООТКЛИКИ НА ВАКАНСИИ")
        print("=" * 70)
        
        print("\n🔍 Проверяем статус резюме...")
        if not self.check_resume_status():
            print("❌ Проблема с резюме! Отклики могут не работать.")
            return
        
        print("\n📊 Проверяем существующие отклики...")
        total_applications = self.get_my_applications()
        
        print(f"\n🛡️ Ищем вакансии ...")
        vacancies = self.get_vacancies(search_params)
        
        if not vacancies:
            print("❌ Вакансии не найдены")
            return
        
        print(f"\n✅ Найдено {len(vacancies)}  вакансий")
        
        print("\n📋 Примеры найденных вакансий:")
        for i, vacancy in enumerate(vacancies[:10], 1):
            employer = vacancy.get('employer', {}).get('name', 'Неизвестно')
            area = vacancy.get('area', {}).get('name', 'Неизвестно')
            has_external = "🔗" if vacancy.get('apply_alternate_url') else "📝"
            print(f"   {i}. {has_external} {vacancy.get('name', 'Без названия')} - {employer} ({area})")
        
        if len(vacancies) > 10:
            print(f"   ... и еще {len(vacancies) - 10}  вакансий")
        
        print(f"\n🚀 Начинаем массовые отклики ...")
        
        successful_applications = 0
        processed = 0
        error_stats = {}
        daily_limit_reached = False
        
        for vacancy in vacancies:
            if daily_limit_reached or self.applied_today >= self.max_applications_per_day:
                break
                
            vacancy_id = vacancy.get('id')
            vacancy_name = vacancy.get('name', 'Без названия')
            employer = vacancy.get('employer', {}).get('name', 'Неизвестно')
            processed += 1
            
            # Используем специальное письмо 
            cover_letter = self.generate_infosec_cover_letter(vacancy)
            
            print(f"\n🛡️ {processed}/{len(vacancies)}: {vacancy_name}")
            print(f"🏢 {employer}")
            
            success, error_type, application_id = self.apply_to_vacancy(vacancy_id, cover_letter)
            
            if success:
                successful_applications += 1
                print(f"✅ УСПЕШНО! ID: {application_id}")
            else:
                error_stats[error_type] = error_stats.get(error_type, 0) + 1
                
                error_messages = {
                    'already_applied': '📝 Уже откликались',
                    'daily_limit_exceeded': '📊 Дневной лимит HH.ru',
                    'limit_exceeded': '📊 Лимит откликов',
                    'forbidden': '🚫 Доступ запрещен',
                    'forbidden_no_json': '🚫 Доступ запрещен (не JSON)',
                    'forbidden_invalid_json': '🚫 Доступ запрещен (плохой JSON)',
                    'bad_request': '❌ Только внешний отклик',
                    'bad_request_no_json': '❌ Плохой запрос (не JSON)',
                    'bad_request_invalid_json': '❌ Плохой запрос (плохой JSON)',
                    'json_decode_error': '❌ Ошибка парсинга ответа',
                    'network_error': '🌐 Сетевая ошибка'
                }
                
                error_msg = error_messages.get(error_type, f"❌ {error_type}")
                print(error_msg)
                
                if error_type in ['daily_limit_exceeded', 'limit_exceeded']:
                    print("\n⏸️ Достигнут лимит откликов")
                    daily_limit_reached = True
            
            # Задержка между запросами
            delay = random.randint(2, 4)
            time.sleep(delay)
        
        print(f"\n🎯 ЗАВЕРШЕНО!")
        print(f"📊 Обработано  вакансий: {processed}")
        print(f"✅ Успешных откликов: {successful_applications}")
        if processed > 0:
            print(f"📈 Процент успеха: {successful_applications/processed*100:.1f}%")
        
        if error_stats:
            print(f"\n📋 Статистика ошибок:")
            for error_type, count in error_stats.items():
                print(f"   {error_type}: {count}")

def main():
    # Получаем конфигурацию из переменных окружения или файла config.py
    try:
        from config import CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, RESUME_ID
        print("📁 Конфигурация загружена из config.py")
    except ImportError:
        # Пытаемся получить из переменных окружения
        CLIENT_ID = os.getenv('HH_CLIENT_ID')
        CLIENT_SECRET = os.getenv('HH_CLIENT_SECRET') 
        REDIRECT_URI = os.getenv('HH_REDIRECT_URI', 'https://localhost/hh-auth')
        RESUME_ID = os.getenv('HH_RESUME_ID')
        
        if not all([CLIENT_ID, CLIENT_SECRET, RESUME_ID]):
            print("❌ ОШИБКА КОНФИГУРАЦИИ!")
            print("\nСпособы настройки:")
            print("\n1. Создайте файл config.py с содержимым:")
            print("CLIENT_ID = 'your_client_id_here'")
            print("CLIENT_SECRET = 'your_client_secret_here'")
            print("REDIRECT_URI = 'https://localhost/hh-auth'")
            print("RESUME_ID = 'your_resume_id_here'")
            print("\n2. Или установите переменные окружения:")
            print("export HH_CLIENT_ID='your_client_id_here'")
            print("export HH_CLIENT_SECRET='your_client_secret_here'")
            print("export HH_REDIRECT_URI='https://localhost/hh-auth'")
            print("export HH_RESUME_ID='your_resume_id_here'")
            print("\n📖 Инструкция получения данных:")
            print("1. Зарегистрируйте приложение на https://dev.hh.ru/admin")
            print("2. Получите CLIENT_ID и CLIENT_SECRET")
            print("3. Укажите REDIRECT_URI в настройках приложения")
            print("4. Найдите ID резюме в URL при его просмотре на hh.ru")
            return
        else:
            print("🌍 Конфигурация загружена из переменных окружения")
    
    
    search_params = {}
    
    print("HH.ru Автоотклики ")
    print("=" * 70)
    
    try:
        applicant = HHAutoApplicant(CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, RESUME_ID)
        applicant.run_auto_applications(search_params)
        
    except KeyboardInterrupt:
        print("\n⏹️ Остановлено пользователем")
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")

if __name__ == "__main__":
    main()
