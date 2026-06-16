"""
Автоматизация откликов на hh.ru через Selenium
Работает через браузер, обходя ограничения API
"""

import json
import time
import random
import os
import logging
import sys
from datetime import datetime, timedelta
from urllib.parse import urlparse
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    ElementClickInterceptedException,
    StaleElementReferenceException
)

HH_ALLOWED_HOST_SUFFIX = 'hh.ru'
DEFAULT_API_CACHE_FILE = 'vacancies_cache.json'
APPLICATION_LIMIT_WINDOW_HOURS = 24
APPLY_CONFIRM_ATTEMPTS = 2
LOGIN_WAIT_SECONDS = 180
LOGIN_POLL_SECONDS = 5
RESPONSE_STATE_WAIT_SECONDS = 7
RESPONSE_SUCCESS_TEXTS = (
    'отклик отправлен',
    'вы откликнулись',
    'ваш отклик',
)
RESPONSE_DENIED_TEXTS = (
    'вам отказали',
    'работодатель отказал',
)
RESPONSE_ALREADY_TEXTS = (
    'уже откликнулись',
    'отклик уже отправлен',
)
RESPONSE_LIMIT_TEXTS = (
    'в течение 24 часов можно совершить',
    'не более 200 откликов',
    'исчерпали лимит откликов',
    'попробуйте отправить отклик позднее',
)
RESPONSE_READY_TEXTS = (
    'откликнуться',
)
RESPONSE_MODAL_SELECTOR = (
    '[data-qa="vacancy-response-popup"], '
    '.bloko-modal, '
    '[role="dialog"], '
    '[class*="modal"]'
)
RESPONSE_SUBMIT_SELECTORS = (
    '[data-qa="vacancy-response-submit-popup"]',
    '[data-qa="vacancy-response-letter-submit"]',
    'button[type="submit"]',
    'button[class*="submit"]',
    '.bloko-modal button[class*="primary"]',
)
RESPONSE_BLOCKER_TEXTS = (
    ('подтвердите, что вы не робот', 'Требуется капча'),
    ('captcha', 'Требуется капча'),
    ('капча', 'Требуется капча'),
    ('ответьте на вопрос', 'Не заполнены вопросы работодателя'),
    ('ответьте на вопросы', 'Не заполнены вопросы работодателя'),
    ('вакансия в архиве', 'Вакансия в архиве'),
    ('вакансия перемещена в архив', 'Вакансия в архиве'),
    ('попробуйте позднее', 'HH просит попробовать позднее'),
    ('попробуйте позже', 'HH просит попробовать позднее'),
    ('что-то пошло не так', 'Ошибка HH после клика'),
    ('произошла ошибка', 'Ошибка HH после клика'),
)
STRICT_TITLE_EXCLUDE_KEYWORDS = (
    'junior',
    'intern',
    'entry level',
    'beginner',
    'стажер',
    'стажёр',
    'начинающ',
    'джуниор',
    'младший',
    'менеджер',
    'директор',
    'начальник',
    'руководитель',
    'head of',
    'chief',
    'ciso',
    'продаж',
    'sales',
    'account manager',
    'business development',
    'project manager',
    'product manager',
    'руководитель проекта',
    'руководитель проектов',
    'пресейл',
    'presale',
    'pre-sale',
    'преподаватель',
    'лектор',
    'teacher',
    'инструктор',
    'автор',
    'студенческ',
    'курсов',
    'диплом',
    'реферат',
    'сертификац',
    'hr',
    'рекрутер',
)
STRICT_TITLE_INCLUDE_KEYWORDS = (
    'appsec',
    'application security',
    'devsecops',
    'devsec',
    'red team',
    'redteam',
    'pentest',
    'пентест',
    'penetration',
    'тестированию на проникновение',
    'тестирование на проникновение',
    'анализу защищ',
    'анализ защищ',
    'security engineer',
    'web security',
    'cyber security',
    'cybersecurity',
    'кибербезопасност',
    'информационной безопасност',
    'инфраструктурной безопасност',
    'soc',
    'siem',
    'уязвимост',
)
VISIBLE_RESPONSE_STATE_SCRIPT = r"""
const viewportWidth = window.innerWidth;
const viewportHeight = window.innerHeight;

function controlInfo(element) {
  const rect = element.getBoundingClientRect();
  const style = getComputedStyle(element);
  const text = (element.innerText || element.textContent || '').trim();
  const visible = (
    style.display !== 'none'
    && style.visibility !== 'hidden'
    && style.opacity !== '0'
    && rect.width > 0
    && rect.height > 0
  );
  const inViewport = (
    visible
    && rect.bottom > 0
    && rect.right > 0
    && rect.top < viewportHeight
    && rect.left < viewportWidth
  );

  return {
    text,
    visible,
    inViewport,
    left: rect.left,
    top: rect.top,
    right: rect.right,
    bottom: rect.bottom,
    width: rect.width,
    height: rect.height,
    area: rect.width * rect.height,
    centerX: rect.left + (rect.width / 2),
    centerY: rect.top + (rect.height / 2),
  };
}

const bodyText = document.body ? document.body.innerText : '';
const controls = Array.from(document.querySelectorAll('button, a, [role="button"]'))
  .map((element, index) => ({ index, ...controlInfo(element) }))
  .filter((control) => control.visible && control.inViewport && control.text);

return {
  hasModal: bodyText.includes('Отклик на вакансию'),
  controls,
};
"""
VISIBLE_APPLY_TARGET_SCRIPT = r"""
const viewportWidth = window.innerWidth;
const viewportHeight = window.innerHeight;

function isVisible(element) {
  const rect = element.getBoundingClientRect();
  const style = getComputedStyle(element);
  return (
    style.display !== 'none'
    && style.visibility !== 'hidden'
    && style.opacity !== '0'
    && rect.width > 0
    && rect.height > 0
    && rect.bottom > 0
    && rect.right > 0
    && rect.top < viewportHeight
    && rect.left < viewportWidth
  );
}

function targetInfo(element, index) {
  const rect = element.getBoundingClientRect();
  const disabled = element.disabled || element.getAttribute('aria-disabled') === 'true';
  return {
    found: true,
    index,
    text: (element.innerText || element.textContent || '').trim(),
    disabled,
    left: rect.left,
    top: rect.top,
    right: rect.right,
    bottom: rect.bottom,
    width: rect.width,
    height: rect.height,
    area: rect.width * rect.height,
    centerX: rect.left + (rect.width / 2),
    centerY: rect.top + (rect.height / 2),
    hasModal: (document.body?.innerText || '').includes('Отклик на вакансию'),
  };
}

const hasModal = (document.body?.innerText || '').includes('Отклик на вакансию');
let candidates = Array.from(document.querySelectorAll('button, a, [role="button"]'))
  .map((element, index) => ({ element, index }))
  .filter(({ element }) => isVisible(element))
  .map(({ element, index }) => ({ element, ...targetInfo(element, index) }))
  .filter((candidate) => candidate.text.toLowerCase() === 'откликнуться' && !candidate.disabled);

if (!candidates.length) {
  return { found: false, hasModal };
}

if (hasModal) {
  const modalCandidates = candidates.filter((candidate) => candidate.top > viewportHeight * 0.35);
  candidates = modalCandidates.length ? modalCandidates : candidates;
  candidates.sort((left, right) => right.area - left.area || right.top - left.top);
} else {
  const pageCandidates = candidates.filter((candidate) => candidate.top > 100 && candidate.top < viewportHeight - 10);
  candidates = pageCandidates.length ? pageCandidates : candidates;
  candidates.sort((left, right) => left.top - right.top || right.area - left.area);
}

const target = candidates[0];
target.element.scrollIntoView({ block: 'center', inline: 'center' });
return targetInfo(target.element, target.index);
"""

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')


def is_allowed_hh_url(url):
    parsed_url = urlparse(url)
    hostname = parsed_url.hostname or ''
    return parsed_url.scheme == 'https' and (
        hostname == HH_ALLOWED_HOST_SUFFIX
        or hostname.endswith(f'.{HH_ALLOWED_HOST_SUFFIX}')
    )


def get_api_vacancy_url(vacancy):
    for field_name in ('alternate_url', 'apply_alternate_url'):
        url = vacancy.get(field_name)
        if isinstance(url, str) and is_allowed_hh_url(url):
            return url

    return None


def resolve_workspace_path(path):
    if os.path.isabs(path):
        return path

    return os.path.join(SCRIPT_DIR, path)


def wait_before_browser_close():
    if not sys.stdin.isatty():
        return

    try:
        input("\nНажмите Enter для закрытия браузера...")
    except EOFError:
        logging.info("stdin закрыт, браузер будет закрыт автоматически")


def write_json_atomic(path, data):
    temp_path = f"{path}.tmp"
    with open(temp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)

# Настройка логирования
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(SCRIPT_DIR, 'hh_selenium.log'), encoding='utf-8'),
        logging.StreamHandler()
    ]
)

class HHSeleniumBot:
    def __init__(self, headless=False, debugger_address=None):
        self.driver = None
        self.wait = None
        self.headless = headless
        self.debugger_address = debugger_address

        # Файлы данных
        self.applied_file = os.path.join(SCRIPT_DIR, 'applied_vacancies_selenium.json')
        self.shared_applied_file = os.path.join(SCRIPT_DIR, 'applied_vacancies.json')
        self.config_file = os.path.join(SCRIPT_DIR, 'hh_selenium_config.json')
        self.api_cache_file = os.path.join(SCRIPT_DIR, DEFAULT_API_CACHE_FILE)

        # Загружаем конфиг
        self.config = self.load_config()

        # Загружаем список откликов
        self.applied_vacancies = self.load_applied()

        # Счетчики
        self.applied_today = self.count_sent_today()
        self.skipped = 0
        self.errors = 0
        self.response_limit_reached = False

        # Настройки задержек (секунды) - БЫСТРЫЙ РЕЖИМ
        self.delay_between_actions = (0.5, 1.5)  # Между действиями
        self.delay_between_vacancies = (1, 2)  # Между вакансиями
        self.delay_after_apply = (0.5, 1)  # После отклика

    def load_config(self):
        """Загружает конфигурацию"""
        default_config = {
            'search_url': 'https://hh.ru/search/vacancy?text=python+developer&area=1',
            'api_cache_file': DEFAULT_API_CACHE_FILE,
            'cover_letter': 'Добрый день! Заинтересован в данной позиции. Готов обсудить детали.',
            'max_applications': 200,
            'skip_with_tests': True,
            'skip_applied': True,
            'keywords_include': [],  # Вакансии должны содержать эти слова
            'keywords_exclude': ['стажер', 'intern', 'junior'] # Исключить вакансии с этими словами
        }

        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    # Объединяем с дефолтными значениями
                    default_config.update(config)
        except Exception as e:
            logging.warning(f"Ошибка загрузки конфига: {e}")

        # Сохраняем конфиг для редактирования
        self.save_config(default_config)
        return default_config

    def save_config(self, config):
        """Сохраняет конфигурацию"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.error(f"Ошибка сохранения конфига: {e}")

    def load_applied(self):
        """Загружает список вакансий, на которые уже откликнулись"""
        try:
            if os.path.exists(self.applied_file):
                with open(self.applied_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logging.warning(f"Ошибка загрузки списка откликов: {e}")
        return {}

    def count_sent_today(self):
        cutoff = datetime.now() - timedelta(hours=APPLICATION_LIMIT_WINDOW_HOURS)
        count = 0
        for entry in self.applied_vacancies.values():
            if not isinstance(entry, dict):
                continue
            if entry.get('status') not in ('sent', 'sent_wrong_filter'):
                continue
            try:
                applied_at = datetime.strptime(str(entry.get('date', '')), '%Y-%m-%d %H:%M:%S')
            except ValueError:
                continue
            if applied_at >= cutoff:
                count += 1
        return count

    def remove_from_api_cache(self, vacancy_id):
        """Удаляет обработанную вакансию из API-кеша."""
        try:
            if not os.path.exists(self.api_cache_file):
                return

            with open(self.api_cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)

            vacancies = cache_data.get('vacancies', [])
            if not isinstance(vacancies, list):
                return

            original_count = len(vacancies)
            cache_data['vacancies'] = [
                vacancy
                for vacancy in vacancies
                if str(vacancy.get('id')) != str(vacancy_id)
            ]

            if len(cache_data['vacancies']) == original_count:
                return

            cache_data['total_count'] = len(cache_data['vacancies'])
            write_json_atomic(self.api_cache_file, cache_data)
            logging.info(f"Вакансия {vacancy_id} удалена из API-кеша")
        except Exception as e:
            logging.error(f"Ошибка удаления вакансии из API-кеша: {e}")

    def save_shared_applied(self, vacancy_id, timestamp, status):
        """Синхронизирует Selenium-историю с общей историей API-бота."""
        if status not in ('sent', 'sent_wrong_filter'):
            return

        try:
            if os.path.exists(self.shared_applied_file):
                with open(self.shared_applied_file, 'r', encoding='utf-8') as f:
                    shared_applied = json.load(f)
            else:
                shared_applied = {}

            shared_applied[str(vacancy_id)] = timestamp

            write_json_atomic(self.shared_applied_file, shared_applied)
        except Exception as e:
            logging.error(f"Ошибка синхронизации общей истории откликов: {e}")

    def save_applied(self, vacancy_id, vacancy_name, status='sent'):
        """Сохраняет информацию об отклике"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.applied_vacancies[str(vacancy_id)] = {
            'name': vacancy_name,
            'date': timestamp,
            'status': status
        }
        try:
            write_json_atomic(self.applied_file, self.applied_vacancies)
            self.save_shared_applied(vacancy_id, timestamp, status)
            self.remove_from_api_cache(vacancy_id)
        except Exception as e:
            logging.error(f"Ошибка сохранения отклика: {e}")

    def skip_cached_vacancy(self, vacancy_id):
        """Убирает из текущего API-кеша вакансию, которую точно не надо повторять."""
        self.remove_from_api_cache(vacancy_id)

    def get_visible_page_text(self):
        try:
            return self.driver.find_element(By.TAG_NAME, 'body').text.lower()
        except Exception:
            return self.driver.page_source.lower()

    def is_response_limit_reached(self):
        page_text = self.get_visible_page_text()
        return any(text in page_text for text in RESPONSE_LIMIT_TEXTS)

    def is_disabled_element(self, element):
        try:
            if not element.is_enabled():
                return True
        except Exception:
            pass

        for attribute_name in ('disabled', 'aria-disabled'):
            try:
                attribute_value = element.get_attribute(attribute_name)
            except Exception:
                continue
            if str(attribute_value).lower() in ('true', 'disabled'):
                return True

        return False

    def get_response_blocker_message(self):
        if self.is_response_limit_reached():
            return "Лимит откликов"

        page_text = self.get_visible_page_text()

        if 'обязательное поле' in page_text and 'сопровод' in page_text:
            return "Сопроводительное письмо не заполнено"

        for text, message in RESPONSE_BLOCKER_TEXTS:
            if text in page_text:
                return message

        return None

    def find_response_modal(self, wait_seconds=0):
        try:
            if wait_seconds > 0:
                return WebDriverWait(self.driver, wait_seconds).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, RESPONSE_MODAL_SELECTOR))
                )

            for modal in self.driver.find_elements(By.CSS_SELECTOR, RESPONSE_MODAL_SELECTOR):
                try:
                    if modal.is_displayed():
                        return modal
                except Exception:
                    continue
        except TimeoutException:
            return None
        except Exception as e:
            logging.debug(f"Не удалось найти модалку отклика: {e}")

        return None

    def get_visible_response_state(self):
        try:
            state = self.driver.execute_script(VISIBLE_RESPONSE_STATE_SCRIPT)
            if isinstance(state, dict):
                return state
        except Exception as e:
            logging.debug(f"Не удалось прочитать видимые кнопки отклика: {e}")

        return {'hasModal': False, 'controls': []}

    def get_response_control_texts(self):
        selectors = [
            '[data-qa="vacancy-response-link-top"]',
            '[data-qa="vacancy-response-link-bottom"]',
            'a[data-qa*="vacancy-response"]',
            'button[data-qa*="vacancy-response"]',
        ]
        texts = []

        for selector in selectors:
            try:
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
            except Exception:
                continue

            for element in elements:
                try:
                    if element.is_displayed():
                        text = element.text.lower().strip()
                        if text:
                            texts.append(text)
                except Exception:
                    continue

        visible_state = self.get_visible_response_state()
        for control in visible_state.get('controls', []):
            text = str(control.get('text', '')).lower().strip()
            if text:
                texts.append(text)

        return texts

    def detect_response_state(self):
        visible_state = self.get_visible_response_state()
        visible_control_texts = [
            str(control.get('text', '')).lower().strip()
            for control in visible_state.get('controls', [])
            if str(control.get('text', '')).strip()
        ]
        visible_control_text = ' '.join(visible_control_texts)

        has_chat = any(text == 'чат' for text in visible_control_texts)
        has_other_resume = any('отклик другим резюме' in text for text in visible_control_texts)
        has_apply = any(text == 'откликнуться' for text in visible_control_texts)

        if self.is_response_limit_reached():
            return 'limit'
        if has_chat and has_other_resume:
            return 'success'
        if any(text in visible_control_text for text in RESPONSE_DENIED_TEXTS):
            return 'denied'
        if any(text in visible_control_text for text in RESPONSE_ALREADY_TEXTS):
            return 'already'
        if has_apply:
            return 'ready'

        response_control_text = ' '.join(self.get_response_control_texts())

        if any(text in response_control_text for text in RESPONSE_READY_TEXTS):
            return 'ready'
        if any(text in response_control_text for text in RESPONSE_DENIED_TEXTS):
            return 'denied'
        if any(text in response_control_text for text in RESPONSE_SUCCESS_TEXTS):
            return 'success'
        if any(text in response_control_text for text in RESPONSE_ALREADY_TEXTS):
            return 'already'

        return 'unknown'

    def wait_for_response_state(self):
        deadline = time.time() + RESPONSE_STATE_WAIT_SECONDS
        while time.time() < deadline:
            state = self.detect_response_state()
            if state not in ('unknown', 'ready'):
                return state
            time.sleep(0.5)

        return self.detect_response_state()

    def find_visible_apply_text_buttons(self):
        buttons = []
        for element in self.driver.find_elements(By.CSS_SELECTOR, 'button, a'):
            try:
                text = element.text.lower().strip()
                if (
                    element.is_displayed()
                    and text == 'откликнуться'
                    and not self.is_disabled_element(element)
                ):
                    buttons.append(element)
            except Exception:
                continue

        return buttons

    def click_viewport_coordinates(self, x, y):
        try:
            self.driver.execute_cdp_cmd('Input.dispatchMouseEvent', {
                'type': 'mouseMoved',
                'x': x,
                'y': y,
            })
            self.driver.execute_cdp_cmd('Input.dispatchMouseEvent', {
                'type': 'mousePressed',
                'x': x,
                'y': y,
                'button': 'left',
                'clickCount': 1,
            })
            self.driver.execute_cdp_cmd('Input.dispatchMouseEvent', {
                'type': 'mouseReleased',
                'x': x,
                'y': y,
                'button': 'left',
                'clickCount': 1,
            })
            return True
        except Exception as e:
            logging.debug(f"CDP-клик не сработал: {e}")
            return False

    def click_element_with_mouse(self, element):
        if self.is_disabled_element(element):
            logging.debug("Клик по disabled-элементу отклика пропущен")
            return False

        try:
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center', inline: 'center'});",
                element,
            )
            time.sleep(0.2)
            rect = self.driver.execute_script(
                """
                const rect = arguments[0].getBoundingClientRect();
                return {
                    centerX: rect.left + (rect.width / 2),
                    centerY: rect.top + (rect.height / 2),
                };
                """,
                element,
            )
            if self.click_viewport_coordinates(rect['centerX'], rect['centerY']):
                time.sleep(0.8)
                return True
        except Exception as e:
            logging.debug(f"Клик мышью по элементу не сработал: {e}")

        try:
            element.click()
            time.sleep(0.8)
            return True
        except Exception as e:
            logging.debug(f"Обычный клик по элементу не сработал: {e}")
            return False

    def get_element_context_text(self, element):
        try:
            return self.driver.execute_script(
                """
                const element = arguments[0];
                const parent = element.closest('div, form, section') || element.parentElement;
                return [
                    element.getAttribute('data-qa') || '',
                    element.getAttribute('name') || '',
                    element.getAttribute('placeholder') || '',
                    element.getAttribute('aria-label') || '',
                    parent ? parent.innerText : '',
                ].join(' ').toLowerCase();
                """,
                element,
            )
        except Exception:
            return ''

    def is_cover_letter_required(self):
        page_text = self.get_visible_page_text()
        return 'обязательное поле' in page_text and 'сопровод' in page_text

    def get_text_input_value(self, element):
        try:
            value = element.get_attribute('value')
            if value:
                return value
        except Exception:
            pass

        try:
            return element.text or ''
        except Exception:
            return ''

    def set_text_input_value(self, element, text):
        try:
            element.click()
            element.send_keys(Keys.CONTROL, 'a')
            element.send_keys(Keys.DELETE)
            element.send_keys(text)
            time.sleep(0.2)
        except Exception as e:
            logging.debug(f"send_keys для сопроводительного письма не сработал: {e}")

        if text.strip() in self.get_text_input_value(element):
            return True

        try:
            self.driver.execute_script(
                """
                const element = arguments[0];
                const value = arguments[1];
                const prototype = Object.getPrototypeOf(element);
                const descriptor =
                    Object.getOwnPropertyDescriptor(prototype, 'value')
                    || Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')
                    || Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');

                if (descriptor && descriptor.set) {
                    descriptor.set.call(element, value);
                } else {
                    element.value = value;
                    element.textContent = value;
                }

                element.dispatchEvent(new Event('input', { bubbles: true }));
                element.dispatchEvent(new Event('change', { bubbles: true }));
                """,
                element,
                text,
            )
            time.sleep(0.2)
        except Exception as e:
            logging.debug(f"JS-ввод сопроводительного письма не сработал: {e}")

        return text.strip() in self.get_text_input_value(element)

    def click_response_submit_button(self, modal):
        for selector in RESPONSE_SUBMIT_SELECTORS:
            try:
                submit_buttons = modal.find_elements(By.CSS_SELECTOR, selector)
            except Exception:
                continue

            for submit_button in submit_buttons:
                try:
                    if submit_button.is_displayed() and not self.is_disabled_element(submit_button):
                        if self.click_element_with_mouse(submit_button):
                            return True
                except Exception:
                    continue

        return False

    def submit_open_response_modal(self, cover_letter, letter_sent):
        modal = self.find_response_modal(wait_seconds=2)
        if not modal:
            return False, letter_sent, 0, None

        if not letter_sent:
            letter_sent = self.fill_cover_letter(cover_letter, wait_seconds=5)

        questions_answered = self.answer_employer_questions()

        if self.is_cover_letter_required() and not letter_sent:
            return False, letter_sent, questions_answered, "Сопроводительное письмо не заполнено"

        blocker_message = self.get_response_blocker_message()
        if blocker_message and blocker_message != "Сопроводительное письмо не заполнено":
            return False, letter_sent, questions_answered, blocker_message

        if self.click_response_submit_button(modal):
            return True, letter_sent, questions_answered, None

        if self.click_lowest_visible_apply_button():
            return True, letter_sent, questions_answered, None

        blocker_message = self.get_response_blocker_message()
        return False, letter_sent, questions_answered, blocker_message or "Кнопка отправки отклика недоступна"

    def build_success_message(self, letter_sent, questions_answered):
        parts = []
        if letter_sent:
            parts.append("письмо")
        if questions_answered:
            parts.append(f"вопросы({questions_answered})")
        return "С " + ", ".join(parts) if parts else "Отправлено"

    def resolve_response_state(self, state, letter_sent, questions_answered):
        if state == 'denied':
            return False, "Вам отказали"
        if state == 'already':
            return False, "Уже откликнулись"
        if state == 'limit':
            return False, "Лимит откликов"
        if state == 'success':
            return True, self.build_success_message(letter_sent, questions_answered)

        return None

    def confirm_response_submission(self, cover_letter, letter_sent, questions_answered):
        last_state = 'unknown'

        for attempt in range(1, APPLY_CONFIRM_ATTEMPTS + 1):
            state = self.wait_for_response_state()
            last_state = state
            resolved_state = self.resolve_response_state(state, letter_sent, questions_answered)
            if resolved_state is not None:
                return resolved_state

            blocker_message = self.get_response_blocker_message()
            if blocker_message:
                return False, blocker_message

            modal = self.find_response_modal()
            if modal:
                submitted, letter_sent, answered_now, blocker_message = self.submit_open_response_modal(
                    cover_letter,
                    letter_sent,
                )
                questions_answered += answered_now
                if blocker_message:
                    return False, blocker_message
                if submitted:
                    continue

            if state == 'ready' and self.click_lowest_visible_apply_button():
                continue

            if attempt < APPLY_CONFIRM_ATTEMPTS:
                time.sleep(1)

        return False, f"Статус отклика не изменился после {APPLY_CONFIRM_ATTEMPTS} попыток ({last_state})"

    def find_cover_letter_fields(self):
        selectors = [
            '[data-qa="vacancy-response-letter-text"]',
            '[data-qa="vacancy-response-popup-form-letter-input"]',
            'textarea[name="letter"]',
            'textarea[placeholder*="Сопровод"]',
            'textarea[placeholder*="сопровод"]',
            'textarea[placeholder*="письмо"]',
            'textarea',
            '[contenteditable="true"]',
        ]
        fields = []
        seen = set()

        for selector in selectors:
            try:
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
            except Exception:
                continue

            for element in elements:
                try:
                    element_id = element.id
                    if element_id in seen or not element.is_displayed():
                        continue
                    context_text = self.get_element_context_text(element)
                    is_letter_field = (
                        'сопровод' in context_text
                        or 'письм' in context_text
                        or 'letter' in context_text
                    )
                    is_textarea_fallback = (
                        element.tag_name.lower() == 'textarea'
                        and 'сопровод' in self.get_visible_page_text()
                    )
                    if is_letter_field or is_textarea_fallback:
                        fields.append(element)
                        seen.add(element_id)
                except Exception:
                    continue

        return fields

    def fill_cover_letter(self, cover_letter, wait_seconds=0):
        if not cover_letter.strip():
            return False

        deadline = time.time() + wait_seconds
        while True:
            fields = self.find_cover_letter_fields()
            for field in fields:
                if self.set_text_input_value(field, cover_letter):
                    logging.info("   ✉️ Сопроводительное письмо заполнено")
                    return True

            if time.time() >= deadline:
                return False
            time.sleep(0.5)

    def click_lowest_visible_apply_button(self):
        try:
            target = self.driver.execute_script(VISIBLE_APPLY_TARGET_SCRIPT)
        except Exception as e:
            logging.debug(f"Не удалось выбрать видимую кнопку отклика: {e}")
            target = {'found': False}

        if target.get('found'):
            if self.click_viewport_coordinates(target['centerX'], target['centerY']):
                time.sleep(0.8)
                return True

        buttons = self.find_visible_apply_text_buttons()
        if not buttons:
            return False

        def vertical_position(element):
            try:
                return element.location.get('y', 0)
            except Exception:
                return 0

        button = sorted(buttons, key=vertical_position)[-1]
        return self.click_element_with_mouse(button)

    def is_dead_session_message(self, message):
        lower_message = str(message).lower()
        return (
            'invalid session id' in lower_message
            or 'no such window' in lower_message
            or 'target window already closed' in lower_message
            or 'web view not found' in lower_message
        )

    def random_delay(self, delay_range):
        """Случайная задержка"""
        delay = random.uniform(*delay_range)
        time.sleep(delay)

    def init_driver(self):
        """Инициализация браузера"""
        options = Options()

        if self.debugger_address:
            options.debugger_address = self.debugger_address
            try:
                self.driver = webdriver.Chrome(options=options)
                self.wait = WebDriverWait(self.driver, 10)
                logging.info(f"✅ Подключен к Chrome debugger: {self.debugger_address}")
                return True
            except Exception as e:
                logging.error(f"❌ Ошибка подключения к Chrome debugger {self.debugger_address}: {e}")
                return False

        if self.headless:
            options.add_argument('--headless')

        # Антидетект настройки
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option('excludeSwitches', ['enable-automation'])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument('--disable-infobars')
        options.add_argument('--start-maximized')
        options.add_argument('--disable-extensions')

        # User-Agent
        options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

        # Профиль для сохранения cookies
        profile_dir = os.path.join(SCRIPT_DIR, 'chrome_profile')
        options.add_argument(f'--user-data-dir={profile_dir}')

        try:
            self.driver = webdriver.Chrome(options=options)
            self.wait = WebDriverWait(self.driver, 10)

            # Скрываем webdriver
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            logging.info("✅ Браузер запущен")
            return True
        except Exception as e:
            logging.error(f"❌ Ошибка запуска браузера: {e}")
            return False

    def check_login(self):
        """Проверяет авторизацию на hh.ru"""
        try:
            self.driver.get('https://hh.ru')
            self.random_delay((2, 3))
            return self.is_logged_in_current_page()

        except Exception as e:
            logging.error(f"Ошибка проверки авторизации: {e}")
            return False

    def is_logged_in_current_page(self):
        """Проверяет текущую страницу без принудительной навигации."""
        current_url = self.driver.current_url.lower()
        if '/account/login' in current_url:
            return False

        login_buttons = self.driver.find_elements(By.CSS_SELECTOR, '[data-qa="login"]')
        for login_button in login_buttons:
            if login_button.is_displayed():
                return False

        logging.info("✅ Авторизация активна")
        return True

    def wait_for_login(self):
        """Ждет ручной вход в браузере без чтения stdin."""
        deadline = time.time() + LOGIN_WAIT_SECONDS
        while time.time() < deadline:
            time.sleep(LOGIN_POLL_SECONDS)
            if self.is_logged_in_current_page():
                print("✅ Авторизация успешна!")
                return True

            remaining = int(deadline - time.time())
            print(f"⏳ Жду авторизацию в браузере... осталось {remaining} сек")

        print("❌ Авторизация не выполнена за отведенное время")
        return False

    def login(self):
        """Ручная авторизация"""
        print("\n" + "="*60)
        print("🔐 ТРЕБУЕТСЯ АВТОРИЗАЦИЯ")
        print("="*60)
        print("\n1. Браузер откроет страницу входа hh.ru")
        print("2. Войдите в свой аккаунт вручную")
        print("3. После входа скрипт сам продолжит работу")
        print("\n" + "="*60)

        self.driver.get('https://hh.ru/account/login')
        return self.wait_for_login()

    def get_vacancy_id_from_url(self, url):
        """Извлекает ID вакансии из URL"""
        try:
            # URL вида: https://hh.ru/vacancy/12345678
            if '/vacancy/' in url:
                parts = url.split('/vacancy/')
                if len(parts) > 1:
                    vacancy_id = parts[1].split('?')[0].split('/')[0]
                    return vacancy_id
        except:
            pass
        return None

    def normalize_title(self, title):
        return str(title or '').lower().replace('ё', 'е')

    def find_keyword(self, text, keywords):
        normalized_text = self.normalize_title(text)
        for keyword in keywords:
            normalized_keyword = self.normalize_title(keyword)
            if normalized_keyword and normalized_keyword in normalized_text:
                return keyword
        return None

    def validate_security_title(self, title):
        excluded_keyword = self.find_keyword(
            title,
            tuple(self.config.get('keywords_exclude', [])) + STRICT_TITLE_EXCLUDE_KEYWORDS,
        )
        if excluded_keyword:
            return False, f"Исключено по ключевому слову: {excluded_keyword}"

        if not self.find_keyword(title, STRICT_TITLE_INCLUDE_KEYWORDS):
            return False, "Не technical security/appsec/pentest/devsecops/soc/redteam"

        include_keywords = self.config.get('keywords_include', [])
        if include_keywords and not self.find_keyword(title, include_keywords):
            return False, "Не содержит обязательных ключевых слов"

        return True, "OK"

    def answer_employer_questions(self):
        """Отвечает на вопросы работодателя"""
        questions_answered = 0

        try:
            # Ищем форму с вопросами
            question_selectors = [
                '[data-qa="task-body"]',
                '[data-qa="vacancy-response-popup-form-question"]',
                '.vacancy-response-popup-form__question',
                '[class*="question"]',
                'label[class*="question"]'
            ]

            # Ищем все вопросы на странице
            questions = []
            for selector in question_selectors:
                try:
                    found = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    if found:
                        questions.extend(found)
                except:
                    continue

            if not questions:
                return 0

            # Получаем ответы из конфига
            default_answers = self.config.get('question_answers', {})

            for question_elem in questions:
                try:
                    question_text = question_elem.text.lower().strip()
                    if not question_text:
                        continue

                    # Ищем поле ввода рядом с вопросом
                    input_field = None

                    # Пробуем найти input/textarea внутри или рядом
                    try:
                        input_field = question_elem.find_element(By.CSS_SELECTOR, 'input, textarea')
                    except NoSuchElementException:
                        # Ищем следующий элемент
                        try:
                            parent = question_elem.find_element(By.XPATH, '..')
                            input_field = parent.find_element(By.CSS_SELECTOR, 'input, textarea')
                        except:
                            pass

                    if input_field and input_field.is_displayed():
                        # Определяем ответ на основе ключевых слов
                        answer = self.get_answer_for_question(question_text, default_answers)

                        if answer:
                            input_field.clear()
                            input_field.send_keys(answer)
                            questions_answered += 1
                            logging.debug(f"Ответ на вопрос: {question_text[:50]}...")

                    # Обрабатываем радио-кнопки и чекбоксы
                    try:
                        radio_buttons = question_elem.find_elements(By.CSS_SELECTOR, 'input[type="radio"]')
                        if radio_buttons:
                            # Выбираем первый вариант или "Да"
                            for radio in radio_buttons:
                                label = radio.find_element(By.XPATH, 'following-sibling::*|../label|../../label')
                                label_text = label.text.lower() if label else ""
                                if 'да' in label_text or 'yes' in label_text or 'готов' in label_text:
                                    radio.click()
                                    questions_answered += 1
                                    break
                            else:
                                # Если не нашли "Да", кликаем первый
                                if radio_buttons:
                                    radio_buttons[0].click()
                                    questions_answered += 1
                    except:
                        pass

                    # Обрабатываем select
                    try:
                        selects = question_elem.find_elements(By.CSS_SELECTOR, 'select')
                        for select in selects:
                            options = select.find_elements(By.CSS_SELECTOR, 'option')
                            if len(options) > 1:
                                options[1].click()  # Выбираем первый не-пустой вариант
                                questions_answered += 1
                    except:
                        pass

                except Exception as e:
                    logging.debug(f"Ошибка обработки вопроса: {e}")
                    continue

            # Также ищем отдельные input/textarea с placeholder
            try:
                inputs = self.driver.find_elements(By.CSS_SELECTOR,
                    'input[placeholder*="ответ"], textarea[placeholder*="ответ"], '
                    'input[data-qa*="question"], textarea[data-qa*="question"]')

                for inp in inputs:
                    if inp.is_displayed() and not inp.get_attribute('value'):
                        placeholder = inp.get_attribute('placeholder') or ''
                        answer = self.get_answer_for_question(placeholder.lower(), default_answers)
                        if answer:
                            inp.send_keys(answer)
                            questions_answered += 1
            except:
                pass

        except Exception as e:
            logging.debug(f"Ошибка при ответе на вопросы: {e}")

        return questions_answered

    def get_answer_for_question(self, question_text, custom_answers):
        """Подбирает ответ на вопрос по ключевым словам"""
        question_text = question_text.lower()

        # Сначала проверяем кастомные ответы из конфига
        for keyword, answer in custom_answers.items():
            if keyword.lower() in question_text:
                return answer

        # Стандартные ответы по ключевым словам
        default_responses = {
            # Зарплата
            ('зарплат', 'оклад', 'доход', 'salary', 'ожидани'): 'от 150000',
            # Опыт
            ('опыт', 'стаж', 'experience', 'лет работ'): '3 года',
            # Готовность
            ('готов', 'когда', 'приступить', 'start', 'начать'): 'Готов приступить сразу',
            # Переезд
            ('переезд', 'relocation', 'переехать'): 'Готов рассмотреть',
            # Командировки
            ('командировк', 'travel', 'поездк'): 'Готов к командировкам',
            # Удаленка
            ('удален', 'remote', 'дистанц'): 'Предпочитаю удаленный формат, но готов к гибриду',
            # Английский
            ('english', 'англий', 'язык'): 'Intermediate (B1)',
            # Образование
            ('образован', 'универ', 'вуз', 'диплом'): 'Высшее техническое',
            # Почему мы/вы
            ('почему', 'why', 'мотив'): 'Интересный проект и возможность профессионального роста',
            # Сильные стороны
            ('сильн', 'strength', 'преимущ'): 'Аналитический склад ума, быстрая обучаемость',
            # Ссылки
            ('github', 'portfolio', 'портфолио', 'ссылк'): 'Готов предоставить по запросу',
        }

        for keywords, answer in default_responses.items():
            for keyword in keywords:
                if keyword in question_text:
                    return answer

        # Если ничего не подошло - универсальный ответ
        return 'Готов обсудить детали на собеседовании'

    def is_vacancy_suitable(self, vacancy_element):
        """Проверяет подходит ли вакансия по критериям"""
        try:
            # Получаем название вакансии
            title_elem = vacancy_element.find_element(By.CSS_SELECTOR, '[data-qa="serp-item__title"]')
            return self.validate_security_title(title_elem.text)

        except Exception as e:
            return False, f"Ошибка проверки: {e}"

    def is_api_vacancy_suitable(self, vacancy):
        """Проверяет API-вакансию по строгому title-фильтру."""
        return self.validate_security_title(vacancy.get('name', ''))

    def load_api_vacancies(self):
        """Загружает вакансии, найденные API-ботом."""
        cache_file = resolve_workspace_path(self.config.get('api_cache_file', DEFAULT_API_CACHE_FILE))

        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
        except FileNotFoundError:
            logging.error(f"API-кеш не найден: {cache_file}")
            return []
        except json.JSONDecodeError as e:
            logging.error(f"API-кеш поврежден: {e}")
            return []

        vacancies = cache_data.get('vacancies', [])
        if not isinstance(vacancies, list):
            logging.error("API-кеш не содержит список vacancies")
            return []

        logging.info(f"📦 Загружено вакансий из API-кеша: {len(vacancies)}")
        return vacancies

    def process_api_vacancies(self, vacancies):
        """Откликается через браузер на вакансии из API-кеша."""
        vacancies_processed = 0
        max_applications = self.config.get('max_applications', 50)

        for index, vacancy in enumerate(vacancies, 1):
            if self.applied_today >= max_applications:
                logging.info("🛑 Достигнут лимит откликов за 24 часа")
                break

            vacancy_id = str(vacancy.get('id') or '')
            vacancy_name = vacancy.get('name', 'Без названия')
            employer = vacancy.get('employer', {}).get('name', 'Неизвестно')
            vacancy_url = get_api_vacancy_url(vacancy)

            logging.info(f"\n[{index}/{len(vacancies)}] {vacancy_name}")
            logging.info(f"   🏢 {employer}")

            if not vacancy_id:
                logging.info("   ⏭️ Пропуск: нет ID вакансии")
                self.skipped += 1
                continue

            if self.config.get('skip_applied', True) and vacancy_id in self.applied_vacancies:
                logging.info("   ⏭️ Уже откликались")
                self.skipped += 1
                self.skip_cached_vacancy(vacancy_id)
                continue

            if not vacancy_url:
                logging.info("   ⏭️ Пропуск: нет безопасной HH-ссылки")
                self.skipped += 1
                self.skip_cached_vacancy(vacancy_id)
                continue

            if self.config.get('skip_with_tests', True) and vacancy.get('has_test', False):
                logging.info("   ⏭️ Пропуск: есть тест")
                self.skipped += 1
                self.skip_cached_vacancy(vacancy_id)
                continue

            suitable, reason = self.is_api_vacancy_suitable(vacancy)
            if not suitable:
                logging.info(f"   ⏭️ Пропуск: {reason}")
                self.skipped += 1
                self.skip_cached_vacancy(vacancy_id)
                continue

            success, message = self.apply_to_vacancy(vacancy_url, vacancy_name)

            if success:
                logging.info(f"   ✅ {message}")
                self.applied_today += 1
                vacancies_processed += 1
                self.save_applied(vacancy_id, vacancy_name, 'sent')
            elif message == "Уже откликнулись":
                logging.info("   ⏭️ Уже откликнулись")
                self.skipped += 1
                self.save_applied(vacancy_id, vacancy_name, 'already_applied')
            elif message == "Вам отказали":
                logging.info("   ⏭️ Вам отказали")
                self.skipped += 1
                self.save_applied(vacancy_id, vacancy_name, 'denied')
            elif message == "Лимит откликов":
                logging.info("   🛑 Лимит откликов HH исчерпан")
                self.response_limit_reached = True
                break
            else:
                logging.info(f"   ❌ {message}")
                self.errors += 1
                if self.is_dead_session_message(message):
                    logging.error("🛑 Сессия браузера умерла, останавливаю обработку")
                    break

            self.random_delay(self.delay_between_vacancies)

        return vacancies_processed

    def apply_to_vacancy(self, vacancy_url, vacancy_name):
        """Откликается на конкретную вакансию с сопроводительным письмом"""
        try:
            self.driver.get(vacancy_url)
            time.sleep(1)

            initial_state = self.detect_response_state()
            if initial_state == 'denied':
                return False, "Вам отказали"
            if initial_state == 'success':
                return False, "Уже откликнулись"
            if initial_state == 'already':
                return False, "Уже откликнулись"
            if initial_state == 'limit':
                return False, "Лимит откликов"

            cover_letter = self.config.get('cover_letter', '')
            letter_sent = self.fill_cover_letter(cover_letter)

            # Ищем кнопку "Откликнуться"
            apply_selectors = [
                '[data-qa="vacancy-response-link-top"]',
                '[data-qa="vacancy-response-link-bottom"]',
                'a[data-qa*="vacancy-response"]',
                'button[data-qa*="vacancy-response"]'
            ]

            apply_btn = None
            for selector in apply_selectors:
                try:
                    apply_btn = WebDriverWait(self.driver, 3).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                    )
                    if apply_btn.is_displayed():
                        break
                except TimeoutException:
                    continue

            if not apply_btn:
                logging.warning(f"⚠️ Кнопка отклика не найдена")
                return False, "Кнопка не найдена"

            # Проверяем текст кнопки
            btn_text = apply_btn.text.lower()
            if 'отклик отправлен' in btn_text or 'уже откликнулись' in btn_text or 'вы откликнулись' in btn_text:
                return False, "Уже откликнулись"
            if 'вам отказали' in btn_text:
                return False, "Вам отказали"

            # Кликаем на кнопку отклика
            if not self.click_element_with_mouse(apply_btn):
                return False, "Не удалось нажать кнопку отклика"
            time.sleep(1)

            if self.is_response_limit_reached():
                return False, "Лимит откликов"

            questions_answered = 0
            submitted, letter_sent, answered_now, blocker_message = self.submit_open_response_modal(
                cover_letter,
                letter_sent,
            )
            questions_answered += answered_now

            if blocker_message:
                return False, blocker_message

            if submitted:
                time.sleep(0.5)

            return self.confirm_response_submission(cover_letter, letter_sent, questions_answered)

        except ElementClickInterceptedException:
            return False, "Элемент перекрыт"
        except Exception as e:
            logging.error(f"Ошибка отклика: {e}")
            return False, str(e)

    def process_search_page(self):
        """Обрабатывает страницу поиска вакансий"""
        vacancies_processed = 0

        try:
            # Ждем загрузки списка вакансий
            self.wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, '[data-qa="vacancy-serp__results"]'))
            )

            # Получаем все вакансии на странице
            vacancy_items = self.driver.find_elements(By.CSS_SELECTOR, '[data-qa="vacancy-serp__vacancy"]')

            logging.info(f"📋 Найдено вакансий на странице: {len(vacancy_items)}")

            for i, vacancy in enumerate(vacancy_items):
                if self.applied_today >= self.config.get('max_applications', 50):
                    logging.info("🛑 Достигнут лимит откликов за 24 часа")
                    return vacancies_processed

                try:
                    # Получаем ссылку и название
                    title_elem = vacancy.find_element(By.CSS_SELECTOR, '[data-qa="serp-item__title"]')
                    vacancy_url = title_elem.get_attribute('href')
                    vacancy_name = title_elem.text
                    vacancy_id = self.get_vacancy_id_from_url(vacancy_url)

                    logging.info(f"\n[{i+1}/{len(vacancy_items)}] {vacancy_name}")

                    # Проверяем, откликались ли уже
                    if vacancy_id and str(vacancy_id) in self.applied_vacancies:
                        logging.info("   ⏭️ Уже откликались")
                        self.skipped += 1
                        continue

                    # Проверяем критерии
                    suitable, reason = self.is_vacancy_suitable(vacancy)
                    if not suitable:
                        logging.info(f"   ⏭️ Пропуск: {reason}")
                        self.skipped += 1
                        continue

                    # Проверяем наличие теста
                    if self.config.get('skip_with_tests', True):
                        try:
                            test_badge = vacancy.find_element(By.CSS_SELECTOR, '[data-qa="vacancy-serp__vacancy-test"]')
                            logging.info("   ⏭️ Пропуск: есть тест")
                            self.skipped += 1
                            continue
                        except NoSuchElementException:
                            pass

                    # Откликаемся
                    success, message = self.apply_to_vacancy(vacancy_url, vacancy_name)

                    if success:
                        logging.info(f"   ✅ {message}")
                        self.applied_today += 1
                        vacancies_processed += 1
                        if vacancy_id:
                            self.save_applied(vacancy_id, vacancy_name, 'sent')
                    elif message == "Лимит откликов":
                        logging.info("   🛑 Лимит откликов HH исчерпан")
                        self.response_limit_reached = True
                        return vacancies_processed
                    else:
                        logging.info(f"   ❌ {message}")
                        self.errors += 1

                    # Возвращаемся на страницу поиска
                    self.driver.back()
                    time.sleep(0.8)  # Быстрый возврат

                    # Обновляем список вакансий после возврата
                    self.wait.until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, '[data-qa="vacancy-serp__results"]'))
                    )
                    vacancy_items = self.driver.find_elements(By.CSS_SELECTOR, '[data-qa="vacancy-serp__vacancy"]')

                except StaleElementReferenceException:
                    logging.warning("   ⚠️ Элемент устарел, пропускаем")
                    continue
                except Exception as e:
                    logging.error(f"   ❌ Ошибка: {e}")
                    self.errors += 1
                    continue

        except Exception as e:
            logging.error(f"Ошибка обработки страницы: {e}")

        return vacancies_processed

    def go_to_next_page(self):
        """Переходит на следующую страницу"""
        try:
            next_btn = self.driver.find_element(By.CSS_SELECTOR, '[data-qa="pager-next"]')
            next_btn.click()
            self.random_delay(self.delay_between_actions)
            return True
        except NoSuchElementException:
            logging.info("📄 Больше страниц нет")
            return False
        except Exception as e:
            logging.error(f"Ошибка перехода на следующую страницу: {e}")
            return False

    def run(self):
        """Основной цикл работы"""
        print("\n" + "="*60)
        print("🤖 HH.RU SELENIUM BOT")
        print("="*60)

        # Инициализация
        if not self.init_driver():
            return

        try:
            # Проверяем авторизацию
            if not self.check_login():
                if not self.login():
                    return

            # Переходим на страницу поиска
            search_url = self.config.get('search_url')
            logging.info(f"\n🔍 Открываем поиск: {search_url}")
            self.driver.get(search_url)
            self.random_delay(self.delay_between_actions)

            # Обрабатываем страницы
            page = 1
            while self.applied_today < self.config.get('max_applications', 50):
                logging.info(f"\n📄 Страница {page}")

                processed = self.process_search_page()

                if self.response_limit_reached:
                    logging.info("🛑 Остановка: HH сообщил лимит откликов")
                    break

                if processed == 0 and self.errors > 5:
                    logging.warning("⚠️ Слишком много ошибок, останавливаемся")
                    break

                # Переходим на следующую страницу
                if not self.go_to_next_page():
                    break

                page += 1

            # Итоги
            print("\n" + "="*60)
            print("📊 ИТОГИ")
            print("="*60)
            print(f"✅ Откликов за последние 24 часа: {self.applied_today}")
            print(f"⏭️ Пропущено: {self.skipped}")
            print(f"❌ Ошибок: {self.errors}")
            print("="*60)

        except KeyboardInterrupt:
            print("\n\n⛔ Остановлено пользователем")
        except Exception as e:
            logging.error(f"Критическая ошибка: {e}")
        finally:
            if self.driver:
                if sys.stdin.isatty():
                    wait_before_browser_close()
                self.driver.quit()

    def run_api_cache(self):
        """Откликается через браузер на вакансии, найденные API-ботом."""
        print("\n" + "="*60)
        print("🤖 HH.RU API CACHE AUTO APPLY")
        print("="*60)

        if not self.init_driver():
            return

        try:
            if not self.check_login():
                if not self.login():
                    return

            vacancies = self.load_api_vacancies()
            if not vacancies:
                print("\n❌ В API-кеше нет вакансий. Сначала запустите поиск через test.py")
                return

            self.process_api_vacancies(vacancies)

            print("\n" + "="*60)
            print("📊 ИТОГИ")
            print("="*60)
            print(f"✅ Откликов за последние 24 часа: {self.applied_today}")
            print(f"⏭️ Пропущено: {self.skipped}")
            print(f"❌ Ошибок: {self.errors}")
            print("="*60)

        except KeyboardInterrupt:
            print("\n\n⛔ Остановлено пользователем")
        except Exception as e:
            logging.error(f"Критическая ошибка API-cache режима: {e}")
        finally:
            if self.driver:
                if sys.stdin.isatty():
                    wait_before_browser_close()
                self.driver.quit()


def main():
    """Точка входа"""
    print("\n" + "="*60)
    print("🚀 HH.RU AUTO APPLY (SELENIUM)")
    print("="*60)
    print("\nНастройки в файле: hh_selenium_config.json")
    print("История откликов: applied_vacancies_selenium.json")
    print("\n" + "="*60)

    args = sys.argv[1:]
    if '--help' in args:
        print("\nКоманды:")
        print("  python hh_selenium.py --api-cache --limit 200")
        print("  python hh_selenium.py --api-cache --headless --limit 200")
        print("  python hh_selenium.py --api-cache --debugger 127.0.0.1:9122 --limit 200")
        print("  python hh_selenium.py")
        return

    if '--api-cache' in args:
        headless = '--headless' in args
        debugger_address = None
        if '--debugger' in args:
            debugger_index = args.index('--debugger') + 1
            if debugger_index >= len(args):
                print("❌ После --debugger нужен адрес, например 127.0.0.1:9122")
                return
            debugger_address = args[debugger_index]

        bot = HHSeleniumBot(headless=headless, debugger_address=debugger_address)

        if '--limit' in args:
            limit_index = args.index('--limit') + 1
            if limit_index >= len(args):
                print("❌ После --limit нужно число")
                return
            try:
                bot.config['max_applications'] = max(1, int(args[limit_index]))
            except ValueError:
                print("❌ Значение --limit должно быть числом")
                return

        bot.run_api_cache()
        return

    # Спрашиваем режим
    print("\nВыберите режим:")
    print("1. Обычный (с браузером)")
    print("2. Headless (без окна браузера)")
    print("3. Только настройка конфига")
    print("4. Откликаться по API-кешу")

    choice = input("\nВаш выбор (1/2/3/4): ").strip()

    if choice == '3':
        bot = HHSeleniumBot()
        print(f"\n✅ Конфиг создан: {bot.config_file}")
        print("Отредактируйте его и запустите снова")
        return

    headless = choice == '2'

    bot = HHSeleniumBot(headless=headless)
    if choice == '4':
        bot.run_api_cache()
    else:
        bot.run()


if __name__ == '__main__':
    main()
