import requests
import json
import time
import webbrowser
from datetime import datetime, timedelta
from urllib.parse import urlencode, parse_qs, urlparse
import logging
import re
import random

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
        
        try:
            response = self.session.request(method, url, timeout=10, **kwargs)
            
            if response.status_code == 401:
                if self.refresh_access_token():
                    headers['Authorization'] = f'Bearer {self.access_token}'
                    response = self.session.request(method, url, timeout=10, **kwargs)
                else:
                    raise Exception("Не удалось обновить токен")
            
            response.raise_for_status()
            return response
            
        except requests.exceptions.RequestException as e:
            logging.error(f"Ошибка запроса {method} {url}: {e}")
            raise

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
        
        keywords = []
        exclusions = []
        
        for exclusion in exclusions:
            if exclusion in name:
                return False
        
        if keywords:
            for keyword in keywords:
                if keyword in name:
                    return True
            return False
        
        return True

    def get_vacancies(self, search_params):
        all_vacancies = []
        processed_ids = set()
        
        url = f"{self.base_url}/vacancies"
        
        params = {
            'per_page': 50,
            'page': 0,
            'order_by': 'publication_time',
            **search_params
        }
        
        try:
            response = self.make_authenticated_request('GET', url, params=params)
            data = response.json()
            
            total_pages = data.get('pages', 1)
            vacancies = data.get('items', [])
            
            for v in vacancies:
                if self.is_vacancy_suitable(v):
                    v_id = v.get('id')
                    if v_id not in processed_ids and str(v_id) not in self.applied_vacancies:
                        processed_ids.add(v_id)
                        all_vacancies.append(v)
            
            for page in range(1, min(5, total_pages)):
                params['page'] = page
                response = self.make_authenticated_request('GET', url, params=params)
                data = response.json()
                vacancies = data.get('items', [])
                
                for v in vacancies:
                    if self.is_vacancy_suitable(v):
                        v_id = v.get('id')
                        if v_id not in processed_ids and str(v_id) not in self.applied_vacancies:
                            processed_ids.add(v_id)
                            all_vacancies.append(v)
                
                time.sleep(1)
                
        except Exception as e:
            logging.error(f"Ошибка поиска вакансий: {e}")
        
        return all_vacancies

    def generate_cover_letter(self, vacancy_details):
        position_name = vacancy_details.get('name', 'данную позицию')
        company_name = vacancy_details.get('employer', {}).get('name', 'вашей компании')
        
        templates = [
            f"""Здравствуйте!

Заинтересовала вакансия "{position_name}" в {company_name}. 

Готов применить свои навыки и опыт для решения задач вашей команды.

С уважением!""",

            f"""Добрый день!

Рад возможности рассмотреть позицию "{position_name}".

Уверен, что смогу принести пользу компании {company_name}.

Буду рад обсудить детали!""",
        ]
        
        return random.choice(templates)

    def apply_to_vacancy(self, vacancy_id, cover_letter=None):
        url = f"{self.base_url}/negotiations"
        
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'HH-User-Agent': 'Auto Apply Bot'
        }
        
        form_data = {
            'vacancy_id': vacancy_id,
            'resume_id': self.resume_id
        }
        
        if cover_letter:
            form_data['message'] = cover_letter
        
        try:
            response = self.make_authenticated_request('POST', url, data=form_data, headers=headers)
            
            if response.status_code == 201:
                response_data = response.json()
                application_id = response_data.get('id')
                
                logging.info(f"Отклик создан с ID: {application_id}")
                self.save_applied_vacancy(vacancy_id)
                return True, "success", application_id
            else:
                return False, f"unexpected_code_{response.status_code}", None
                
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 403:
                try:
                    error_data = e.response.json()
                    errors = error_data.get('errors', [])
                    if errors:
                        error_type = errors[0].get('type', 'unknown')
                        error_value = errors[0].get('value', '')
                        
                        if error_value == 'already_applied':
                            return False, "already_applied", None
                        elif error_value == 'limit_exceeded':
                            return False, "limit_exceeded", None
                except:
                    pass
                return False, "forbidden", None
                
            elif e.response.status_code == 400:
                try:
                    error_data = e.response.json()
                    description = error_data.get('description', '')
                    
                    if 'Daily negotiations limit is exceeded' in description:
                        return False, "daily_limit_exceeded", None
                except:
                    pass
                return False, "bad_request", None
            else:
                return False, f"http_error_{e.response.status_code}", None
        except Exception as e:
            logging.error(f"Общая ошибка: {str(e)}")
            return False, "network_error", None

    def run_auto_applications(self, search_params):
        print(f"АВТОМАТИЧЕСКИЕ ОТКЛИКИ НА HH.RU")
        print("=" * 60)
        
        print("\nПроверяем статус резюме...")
        if not self.check_resume_status():
            print("Проблема с резюме! Отклики могут не работать.")
            return
        
        print("\nПроверяем существующие отклики...")
        total_applications = self.get_my_applications()
        
        print(f"\nИщем вакансии по заданным параметрам...")
        vacancies = self.get_vacancies(search_params)
        
        if not vacancies:
            print("Подходящие вакансии не найдены")
            return
        
        print(f"Найдено {len(vacancies)} подходящих вакансий")
        
        print("\nСписок вакансий для отклика:")
        for i, vacancy in enumerate(vacancies[:10], 1):
            employer = vacancy.get('employer', {}).get('name', 'Неизвестно')
            area = vacancy.get('area', {}).get('name', 'Неизвестно')
            print(f"   {i}. {vacancy.get('name', 'Без названия')} - {employer} ({area})")
        
        if len(vacancies) > 10:
            print(f"   ... и еще {len(vacancies) - 10} вакансий")
        
        confirm = input(f"\nОтправить отклики на {len(vacancies)} вакансий? (yes/no): ")
        if confirm.lower() not in ['yes', 'y', 'да', 'д']:
            print("Отменено")
            return
        
        print(f"\nНачинаем автоматические отклики...")
        
        successful_applications = 0
        processed = 0
        error_stats = {}
        daily_limit_reached = False
        
        for vacancy in vacancies:
            if daily_limit_reached:
                break
                
            vacancy_id = vacancy.get('id')
            vacancy_name = vacancy.get('name', 'Без названия')
            employer = vacancy.get('employer', {}).get('name', 'Неизвестно')
            processed += 1
            
            cover_letter = self.generate_cover_letter(vacancy)
            
            print(f"\n{processed}/{len(vacancies)}: {vacancy_name}")
            print(f"Компания: {employer}")
            
            success, error_type, application_id = self.apply_to_vacancy(vacancy_id, cover_letter)
            
            if success:
                successful_applications += 1
                print(f"УСПЕШНО! ID отклика: {application_id}")
            else:
                error_stats[error_type] = error_stats.get(error_type, 0) + 1
                
                error_messages = {
                    'already_applied': 'Уже откликались на эту вакансию',
                    'daily_limit_exceeded': 'Превышен дневной лимит откликов HH.ru',
                    'permission_denied': 'Нет прав для отклика',
                    'limit_exceeded': 'Превышен лимит откликов',
                    'test_required': 'Требуется пройти тест',
                    'resume_problem': 'Проблема с резюме',
                    'forbidden': 'Доступ запрещен',
                    'bad_request': 'Некорректный запрос'
                }
                
                error_msg = error_messages.get(error_type, f"Ошибка: {error_type}")
                print(error_msg)
                
                if error_type == 'daily_limit_exceeded':
                    print("\nДостигнут дневной лимит HH.ru. Попробуйте завтра.")
                    daily_limit_reached = True
            
            time.sleep(self.min_delay_between_requests)
        
        print(f"\nЗАВЕРШЕНО!")
        print(f"Обработано вакансий: {processed}")
        print(f"Успешных откликов: {successful_applications}")
        
        if error_stats:
            print(f"\nСтатистика ошибок:")
            for error_type, count in error_stats.items():
                print(f"   {error_type}: {count}")

def main():
    CLIENT_ID = "YOUR_CLIENT_ID_HERE"
    CLIENT_SECRET = "YOUR_CLIENT_SECRET_HERE"
    REDIRECT_URI = "YOUR_REDIRECT_URI_HERE"
    RESUME_ID = "YOUR_RESUME_ID_HERE"
    
    search_params = {
        'text': 'python developer',
        'area': 1,
        'experience': 'noExperience',
        'employment': 'full',
        'schedule': 'remote',
        'period': 30,
    }
    
    print("HH.ru Автоматические отклики")
    print("=" * 60)
    
    if CLIENT_ID == "YOUR_CLIENT_ID_HERE":
        print("Необходимо настроить конфигурацию!")
        print("\nИнструкция:")
        print("1. Зарегистрируйте приложение на https://dev.hh.ru/admin")
        print("2. Получите CLIENT_ID и CLIENT_SECRET")
        print("3. Укажите REDIRECT_URI (например: https://localhost/hh-auth)")
        print("4. Найдите ID вашего резюме в URL при его просмотре")
        print("5. Заполните конфигурацию в этом файле")
        return
    
    try:
        applicant = HHAutoApplicant(CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, RESUME_ID)
        applicant.run_auto_applications(search_params)
        
    except KeyboardInterrupt:
        print("\nОстановлено пользователем")
    except Exception as e:
        print(f"\nОшибка: {e}")

if __name__ == "__main__":
    main()
