(() => {
	'use strict';
	let selectedTemplate = 'coverLetter_1',
		delay = 100,
		selectors = {
			pagerNext: '[data-qa="pager-next"]',
			modalOverlay: '[data-qa="modal-overlay"]',
			alertBox: '[data-qa="magritte-alert"]',
			countryConfirmBtn: '[data-qa="relocation-warning-confirm"]',
			vacancyCards: '[data-qa="vacancy-serp__vacancy"]',
			vacancyTitle: "[data-qa='serp-item__title']",
			addCoverLetter: '[data-qa="vacancy-response-letter-toggle-text"]',
			respondBtn: '[data-qa="vacancy-serp__vacancy_response"]',
			respondBtnPopup: '[data-qa="vacancy-response-submit-popup"]',
		},
		sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
		isRunning = false;

	const CONFIG = {
		enabled: true,
		profileData: {
			name: 'Имя',
			experience: 'Опыт работы',
			skills: 'Основные навыки',
			salary: 'Желаемая зарплата',
			location: 'Город',
			workFormat: 'Формат работы',
			english: 'Уровень английского',
			education: 'Образование',
			startDate: 'Когда готов начать',
			achievements: 'Достижения',
		},
	};

	let vacancyContext = { title: '', id: '', url: '', searchUrl: '' };

	function getVacancyTitle() {
		if (
			vacancyContext.title &&
			!isSearchText(vacancyContext.title) &&
			!isSystemMessage(vacancyContext.title)
		) {
			return vacancyContext.title;
		}

		const metaTitleMatch = document.title.match(/Вакансия\s+(.+?)\s+в\s+/);
		if (metaTitleMatch) {
			const extractedTitle = metaTitleMatch[1].trim();
			if (!isSearchText(extractedTitle) && !isSystemMessage(extractedTitle)) {
				return extractedTitle;
			}
		}

		const titleSelectors = [
			'h1:not([data-qa="title-description"])',
			'[data-qa="vacancy-title"]:not([data-qa="title-description"])',
			'[data-qa="bloko-header-1"]:not([data-qa="title-description"])',
			'.vacancy-title:not(.title-description)',
			'[data-qa="vacancy-name"]',
			'[itemprop="title"]',
		];

		for (const selector of titleSelectors) {
			const elements = document.querySelectorAll(selector);
			for (const element of elements) {
				if (element && element.textContent) {
					const text = element.textContent.trim();
					if (
						!isSearchText(text) &&
						!isSystemMessage(text) &&
						text.length > 3
					) {
						return text;
					}
				}
			}
		}

		const metaTags = ['meta[property="og:title"]', 'meta[name="title"]'];
		for (const metaSelector of metaTags) {
			const metaEl = document.querySelector(metaSelector);
			if (metaEl && metaEl.content) {
				const content = metaEl.content.trim();
				const titleMatch = content.match(
					/Вакансия\s+(.+?)\s+в\s+|^(.+?)\s+—|^(.+?)\s+\||^(.+?)$/
				);
				if (titleMatch) {
					const extracted = (
						titleMatch[1] ||
						titleMatch[2] ||
						titleMatch[3] ||
						titleMatch[4] ||
						''
					).trim();
					if (
						!isSearchText(extracted) &&
						!isSystemMessage(extracted) &&
						extracted.length > 3
					) {
						return extracted;
					}
				}
			}
		}

		return 'данную позицию';
	}

	function isSearchText(text) {
		if (!text || typeof text !== 'string') return true;
		const searchPatterns = [
			/найден[а-я]*\s+\d+/i,
			/\d+\s+подходящ[а-я]*/i,
			/\d+\s+вакансий/i,
			/результат[а-я]*\s+поиска/i,
			/поиск\s+работы/i,
			/страниц[а-я]*\s+\d+/i,
		];
		return searchPatterns.some((pattern) => pattern.test(text));
	}

	function isSystemMessage(text) {
		if (!text || typeof text !== 'string') return true;
		const systemPatterns = [
			/вероятность\s+получить\s+отклик/i,
			/не\s+останавливайтесь/i,
			/продолжайте\s+в\s+том\s+же\s+духе/i,
			/поздравляем/i,
			/успешно\s+отправлен/i,
			/ваш\s+отклик/i,
		];
		return systemPatterns.some((pattern) => pattern.test(text));
	}

	function setVacancyContext(title, id = '', url = '', searchUrl = '') {
		vacancyContext = { title, id, url: url || window.location.href, searchUrl };
	}

	function returnToSearch() {
		if (vacancyContext.searchUrl) {
			window.location.href = vacancyContext.searchUrl;
			return;
		}

		const backLinks = [
			document.querySelector('[data-qa="back-to-search"]'),
			document.querySelector('a[href*="search/vacancy"]'),
			...Array.from(document.querySelectorAll('a')).filter(
				(link) =>
					link.textContent.toLowerCase().includes('поиск') ||
					link.href.includes('search/vacancy')
			),
		].filter(Boolean);

		if (backLinks.length > 0) {
			backLinks[0].click();
			return;
		}

		if (window.history.length > 1) {
			window.history.back();
			return;
		}

		window.location.href = '/search/vacancy';
	}

	class VacancyAnalyzer {
		constructor() {
			this.techKeywords = {
				frontend: [
					'react',
					'vue',
					'angular',
					'javascript',
					'typescript',
					'frontend',
					'фронтенд',
				],
				backend: [
					'node.js',
					'python',
					'java',
					'php',
					'backend',
					'бэкенд',
					'api',
				],
				qa: ['qa', 'тест', 'автотест', 'testing'],
				management: ['менеджер', 'руководитель', 'лид', 'lead', 'manager'],
			};
			this.levelKeywords = {
				junior: ['junior', 'джуниор', 'начинающий', 'младший'],
				middle: ['middle', 'миддл', 'средний'],
				senior: ['senior', 'сеньор', 'ведущий', 'старший'],
			};
		}

		analyzeVacancy(title, description = '') {
			const text = (title + ' ' + description).toLowerCase();
			const analysis = {
				title: title,
				technologies: [],
				level: this.detectLevel(text),
			};

			for (const [category, keywords] of Object.entries(this.techKeywords)) {
				if (keywords.some((keyword) => text.includes(keyword))) {
					analysis.technologies.push(category);
				}
			}
			return analysis;
		}

		detectLevel(text) {
			for (const [level, keywords] of Object.entries(this.levelKeywords)) {
				if (keywords.some((keyword) => text.includes(keyword))) {
					return level;
				}
			}
			return 'middle';
		}
	}

	class AIAssistant {
		constructor() {
			this.patterns = {
				salary: /зарплат|оклад|доход|компенсац|salary/i,
				experience: /опыт|стаж|лет работы|experience/i,
				location: /город|откуда|местоположение|location/i,
				format: /формат|удален|офис|remote|гибрид/i,
				english: /английск|english|язык/i,
				skills: /навык|умеете|технолог|стек|skill/i,
			};
			this.vacancyAnalyzer = new VacancyAnalyzer();
		}

		generateAnswer(question) {
			const q = question.toLowerCase();
			const profile = CONFIG.profileData;

			if (this.patterns.salary.test(q)) return profile.salary;
			if (this.patterns.location.test(q)) return profile.location;
			if (this.patterns.experience.test(q)) return profile.experience;
			if (this.patterns.format.test(q)) return profile.workFormat;
			if (this.patterns.english.test(q)) return profile.english;
			if (this.patterns.skills.test(q)) return profile.skills;

			return 'Готов подробно обсудить на собеседовании.';
		}

		adaptCoverLetter(baseTemplate, vacancyTitle, vacancyDescription = '') {
			const analysis = this.vacancyAnalyzer.analyzeVacancy(
				vacancyTitle,
				vacancyDescription
			);
			let adaptedLetter = baseTemplate.replace(/{#vacancyName}/g, vacancyTitle);

			const personalizations = this.generatePersonalizations(analysis);
			if (personalizations.length > 0) {
				const parts = adaptedLetter.split('\n\n');
				if (parts.length >= 3) {
					parts.splice(-2, 0, personalizations.join(' '));
				} else {
					parts.splice(-1, 0, personalizations.join(' '));
				}
				adaptedLetter = parts.join('\n\n');
			}

			return adaptedLetter;
		}

		generatePersonalizations(analysis) {
			const personalizations = [];

			if (analysis.technologies.length > 0) {
				const mainTech = analysis.technologies[0];
				const techTexts = {
					frontend: `Специализируюсь на frontend-разработке с пониманием современных технологий.`,
					backend: `Имею опыт backend-разработки и работы с API.`,
					qa: `Понимаю важность качества и имею опыт тестирования.`,
					management: `Имею опыт управления проектами и командной работы.`,
				};
				if (techTexts[mainTech]) {
					personalizations.push(techTexts[mainTech]);
				}
			}

			return personalizations;
		}
	}

	const assistant = new AIAssistant();

	let templates = {
		coverLetter_1:
			'Добрый день!\n\nМеня заинтересовала предложенная Вами вакансия {#vacancyName}. Ознакомившись с требованиями, считаю, что мой опыт позволяет претендовать на данную должность.\n\nГотов обсудить детали на собеседовании.\n\nС уважением.',
		coverLetter_2:
			'Здравствуйте!\n\nС интересом рассмотрел вашу вакансию {#vacancyName}.\n\nИмею опыт работы с современными технологиями и готов применить свои знания в вашей компании.\n\nБуду рад встрече!',
		coverLetter_3:
			'Добрый день!\n\nВакансия {#vacancyName} соответствует моим профессиональным целям.\n\nГотов внести вклад в развитие компании.\n\nЖду возможности обсудить сотрудничество.',
	};

	function loadSettings() {
		try {
			const saved = localStorage.getItem('hh_cover_letters');
			if (saved) templates = { ...templates, ...JSON.parse(saved) };

			const savedTemplate = localStorage.getItem('hh_selected_template');
			if (savedTemplate && templates[savedTemplate])
				selectedTemplate = savedTemplate;
		} catch (error) {
			console.error('Ошибка загрузки настроек:', error);
		}
	}

	function saveSettings() {
		try {
			localStorage.setItem('hh_cover_letters', JSON.stringify(templates));
			localStorage.setItem('hh_selected_template', selectedTemplate);
		} catch (error) {
			console.error('Ошибка сохранения настроек:', error);
		}
	}

	function setReactValue(element, value) {
		if (!element || !value) return;

		element.focus();
		element.click();
		element.value = '';

		const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
			window.HTMLTextAreaElement.prototype,
			'value'
		).set;
		nativeInputValueSetter.call(element, value);

		element.dispatchEvent(
			new Event('input', { bubbles: true, cancelable: true })
		);
		element.dispatchEvent(
			new Event('change', { bubbles: true, cancelable: true })
		);
		element.value = value;
	}

	async function findAndAnswerQuestions() {
		await sleep(200);
		let questionsAnswered = 0;
		const allTextareas = document.querySelectorAll(
			'textarea[name*="task"]:not([name="letter"])'
		);

		for (const textarea of allTextareas) {
			if (!textarea.value && textarea.offsetParent !== null) {
				const parent =
					textarea.closest('[data-qa="task-body"]') ||
					textarea.closest('.bloko-control-group');
				if (parent) {
					const questionText = parent.textContent
						.trim()
						.replace('Ответьте на вопросы', '')
						.replace(
							'Для отклика необходимо ответить на несколько вопросов работодателя',
							''
						)
						.trim();

					if (questionText && questionText.length > 10) {
						const answer = assistant.generateAnswer(questionText);
						setReactValue(textarea, answer);
						questionsAnswered++;
						await sleep(50);
					}
				}
			}
		}

		return questionsAnswered;
	}

	async function fillCoverLetter(coverLetterKey, vacancyName) {
		await sleep(1000);

		let targetInput = document.querySelector('textarea[name="letter"]');

		if (!targetInput) {
			const addLetterBtns = [
				document.querySelector(
					'[data-qa="vacancy-response-letter-toggle-text"]'
				),
				document.querySelector('[data-qa="vacancy-response-letter-toggle"]'),
				...Array.from(document.querySelectorAll('button, span')).filter((el) =>
					el.textContent.toLowerCase().includes('сопроводительное письмо')
				),
			].filter(Boolean);

			if (addLetterBtns.length > 0) {
				addLetterBtns[0].click();
				await sleep(1500);
				targetInput = document.querySelector('textarea[name="letter"]');
			}
		}

		if (!targetInput) {
			const allTextareas = document.querySelectorAll('textarea');
			for (const textarea of allTextareas) {
				const isVisible = textarea.offsetParent !== null;
				const isEmpty = !textarea.value;
				const notTask = !textarea.name?.includes('task');

				if (
					isVisible &&
					isEmpty &&
					notTask &&
					!textarea.name?.includes('comment')
				) {
					targetInput = textarea;
					break;
				}
			}
		}

		if (targetInput) {
			const vacancyDescription =
				document.querySelector('[data-qa="vacancy-description"]')
					?.textContent || '';
			const coverText = assistant.adaptCoverLetter(
				templates[coverLetterKey],
				vacancyName,
				vacancyDescription
			);

			targetInput.scrollIntoView({ behavior: 'smooth', block: 'center' });
			await sleep(100);
			setReactValue(targetInput, coverText);

			await sleep(100);
			if (targetInput.value !== coverText) {
				targetInput.focus();
				targetInput.click();
				await sleep(100);
				targetInput.value = coverText;
				targetInput.dispatchEvent(new Event('input', { bubbles: true }));
			}

			return true;
		}

		return false;
	}

	async function findAndClickRespondButton() {
		await sleep(500);

		const respondBtns = [
			document.querySelector('[data-qa="vacancy-response-submit-popup"]'),
			document.querySelector('[data-qa="vacancy-response-letter-submit"]'),
			...Array.from(document.querySelectorAll('button')).filter((btn) =>
				btn.textContent?.trim().toLowerCase().includes('откликнуться')
			),
		].filter(Boolean);

		if (respondBtns.length > 0) {
			const finalBtn = respondBtns[respondBtns.length - 1];
			finalBtn.scrollIntoView({ behavior: 'smooth', block: 'center' });
			await sleep(300);
			finalBtn.click();

			await sleep(2000);
			returnToSearch();
			return true;
		}

		return false;
	}

	async function processModal(vacancyName) {
		if (CONFIG.enabled) await findAndAnswerQuestions();
		await fillCoverLetter(selectedTemplate, vacancyName);
		await sleep(200);
		await findAndClickRespondButton();
	}

	async function processResponsePage() {
		const vacancyTitle = getVacancyTitle();
		await sleep(1000);

		if (CONFIG.enabled) {
			const answered = await findAndAnswerQuestions();
		}

		await sleep(200);
		await fillCoverLetter(selectedTemplate, vacancyTitle);
		await sleep(200);

		const buttonClicked = await findAndClickRespondButton();
		return buttonClicked;
	}

	async function startProcessing() {
		const button = document.getElementById('mb-start-stop');
		if (isRunning) {
			isRunning = false;
			button.style.backgroundColor = '#28a745';
			button.innerHTML = '▶️ СТАРТ';
			return;
		}

		isRunning = true;
		button.style.backgroundColor = '#dc3545';
		button.innerHTML = '⏹️ СТОП';

		try {
			const currentUrl = window.location.href;

			if (currentUrl.includes('applicant/vacancy_response')) {
				await processResponsePage();
			} else if (
				currentUrl.includes('/vacancy/') &&
				!currentUrl.includes('search')
			) {
				const respondBtn = document.querySelector(
					'[data-qa="vacancy-response-link-top"]'
				);
				if (respondBtn) {
					respondBtn.click();
					await sleep(500);
					await processResponsePage();
				}
			} else if (
				currentUrl.includes('search/vacancy') ||
				currentUrl.includes('vacancies')
			) {
				const searchUrl = window.location.href;
				let processedCount = 0;

				while (isRunning) {
					const vacancyCards = document.querySelectorAll(
						selectors.vacancyCards
					);
					if (vacancyCards.length === 0) break;

					for (const card of vacancyCards) {
						if (!isRunning) break;

						const respondBtn = card.querySelector(selectors.respondBtn);
						const vacancyTitleElement = card.querySelector(
							selectors.vacancyTitle
						);

						let vacancyTitle = vacancyTitleElement?.innerText || 'вакансию';
						if (isSearchText(vacancyTitle) || isSystemMessage(vacancyTitle)) {
							const vacancyLink = card.querySelector('a[href*="/vacancy/"]');
							if (vacancyLink) vacancyTitle = vacancyLink.textContent.trim();
						}

						const vacancyId =
							card
								.querySelector('a[href*="/vacancy/"]')
								?.href.match(/\/vacancy\/(\d+)/)?.[1] || '';
						setVacancyContext(vacancyTitle, vacancyId, '', searchUrl);

						if (respondBtn && respondBtn.innerText?.includes('Откликнуться')) {
							processedCount++;
							card.scrollIntoView({ behavior: 'smooth', block: 'center' });
							card.style.border = '2px solid #0059b3';

							respondBtn.click();
							await sleep(300);

							const isModal = document.querySelector(selectors.modalOverlay);
							if (isModal) {
								await processModal(vacancyTitle);
							} else {
								await sleep(500);
								await processResponsePage();
							}

							card.style.border = '';
							await sleep(2000);
						}
					}

					const nextBtn = document.querySelector(selectors.pagerNext);
					if (nextBtn && !nextBtn.disabled && isRunning) {
						nextBtn.click();
						await sleep(800);
					} else {
						break;
					}
				}
			}
		} catch (error) {
			console.error('Ошибка:', error);
		} finally {
			isRunning = false;
			button.style.backgroundColor = '#28a745';
			button.innerHTML = '▶️ СТАРТ';
		}
	}

	function createPanel() {
		const panel = document.createElement('div');
		panel.style.cssText = `
			position: fixed; bottom: 20px; right: 20px; background: white;
			border: 2px solid #0059b3; border-radius: 12px; padding: 20px;
			z-index: 10000; box-shadow: 0 4px 16px rgba(0,0,0,0.2);
			font-size: 12px; width: 280px; font-family: Arial, sans-serif;
		`;

		panel.innerHTML = `
			<div style="font-weight: bold; color: #0059b3; margin-bottom: 15px; display: flex; justify-content: space-between; align-items: center;">
				<span>📷 MadnessBrains </span>
				<button id="mb-minimize" style="background: none; border: none; cursor: pointer; font-size: 16px;">_</button>
			</div>
			
			<div style="margin-bottom: 15px;">
				<div style="font-weight: bold; margin-bottom: 8px; color: #333;">📝 Шаблоны писем:</div>
				<div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 5px; margin-bottom: 8px;">
					<button class="mb-template-btn" data-template="coverLetter_1" style="padding: 8px 4px; border: 1px solid #ddd; border-radius: 4px; cursor: pointer; font-size: 11px; background: #f8f9fa;">📝 1</button>
					<button class="mb-template-btn" data-template="coverLetter_2" style="padding: 8px 4px; border: 1px solid #ddd; border-radius: 4px; cursor: pointer; font-size: 11px; background: #f8f9fa;">📄 2</button>
					<button class="mb-template-btn" data-template="coverLetter_3" style="padding: 8px 4px; border: 1px solid #ddd; border-radius: 4px; cursor: pointer; font-size: 11px; background: #f8f9fa;">📄 3</button>
				</div>
			</div>
			
			<button id="mb-start-stop" style="width: 100%; padding: 12px; background: #28a745; color: white; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; font-size: 16px;">
				▶️ СТАРТ
			</button>
		`;

		document.body.appendChild(panel);

		const minimizeBtn = document.createElement('button');
		minimizeBtn.innerHTML = '📷';
		minimizeBtn.style.cssText = `
			position: fixed; bottom: 20px; right: 20px; width: 50px; height: 50px;
			border-radius: 50%; background: #0059b3; color: white; border: none;
			cursor: pointer; font-size: 24px; z-index: 10001; display: none;
			box-shadow: 0 4px 12px rgba(0,0,0,0.3);
		`;
		document.body.appendChild(minimizeBtn);

		document.getElementById('mb-minimize').addEventListener('click', () => {
			panel.style.display = 'none';
			minimizeBtn.style.display = 'block';
		});

		minimizeBtn.addEventListener('click', () => {
			panel.style.display = 'block';
			minimizeBtn.style.display = 'none';
		});

		document.querySelectorAll('.mb-template-btn').forEach((btn) => {
			btn.addEventListener('click', () => {
				selectedTemplate = btn.dataset.template;
				saveSettings();
				updateTemplateButtons();
			});
		});

		document
			.getElementById('mb-start-stop')
			.addEventListener('click', startProcessing);
		updateTemplateButtons();
	}

	function updateTemplateButtons() {
		for (let i = 1; i <= 3; i++) {
			const btn = document.querySelector(`[data-template="coverLetter_${i}"]`);
			if (btn) {
				const templateKey = `coverLetter_${i}`;
				if (templateKey === selectedTemplate) {
					btn.style.background = '#0059b3';
					btn.style.color = 'white';
				} else {
					btn.style.background = '#f8f9fa';
					btn.style.color = '#333';
				}
			}
		}
	}

	(async function init() {
		loadSettings();
		await sleep(delay);
		createPanel();

		if (window.location.href.includes('vacancy_response')) {
			setTimeout(async () => {
				if (CONFIG.enabled) {
					const answered = await findAndAnswerQuestions();
					const vacancyName = getVacancyTitle();
					await fillCoverLetter(selectedTemplate, vacancyName);
				}
			}, 2000);
		}
	})();
})();
