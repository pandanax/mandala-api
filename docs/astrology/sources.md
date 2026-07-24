# Источники и ссылки

> Ссылки на проверенные источники по астрологии. Используются для KB и верификации утверждений.
> Отмечено [TODO: проверить] там, где информация нуждается в дополнительной верификации.

## Программное обеспечение и библиотеки

### Swiss Ephemeris
- **Сайт:** https://www.astro.com/swisseph/
- Автор: Alois Treindl, Dieter Koch (Astrodienst AG)
- **Основа:** NASA/JPL planetary data
- В коде: используется через `pyswisseph` и `kerykeion`
- Лицензия: AGPL / LGPL (требует проверки при коммерческом использовании)

### Kerykeion (Python)
- **GitHub:** https://github.com/g-battaglia/kerykeion
- Обёртка над Swiss Ephemeris
- Текущая версия в проекте: v4+ (API изменился, `AspectsFactory` вместо `NatalAspects`)
- Типизированная, есть `py.typed`

### TimezoneFinder
- **GitHub:** https://github.com/jannikmi/timezonefinder
- Точное определение часового пояса по координатам (без API-запросов)

### Nominatim (OpenStreetMap)
- **Сайт:** https://nominatim.openstreetmap.org/
- Геокодинг города → координаты
- Ограничения: 1 запрос/сек, требует User-Agent (`mandala-astro/1.0 contact:...`)
- Для проде лучше рассмотреть self-hosted или платный geocoder

---

## Классические тексты (западная)

- **Ptolemy, «Tetrabiblos»** (ок. 150 н.э.) — основа западной традиции
  - Перевод: https://penelope.uchicago.edu/Thayer/E/Roman/Texts/Ptolemy/Tetrabiblos/
- **William Lilly, «Christian Astrology»** (1647) — классика хорарной и натальной астрологии
  - Свободный доступ: https://www.skyscript.co.uk/Christian_Astrology.pdf [TODO: проверить ссылку]

## Современные источники (западная)

- **Astro.com** — astro.com (Astrodienst, Швейцария)
  - Бесплатные карты, статьи, Swiss Ephemeris онлайн
  - Gavin Kash, Liz Greene, Robert Hand — авторы статей
- **Robert Hand, «Planets in Transit»** (Whitford Press, 1976) — стандартная книга по транзитам
- **Liz Greene, Howard Sasportas** — психологическая астрология
- **Skyscript.co.uk** — учебные материалы по традиционной и хорарной астрологии

## Классические тексты (ведическая)

- **Parashara, «Brihat Parashara Hora Shastra»** — основной канон Джйотиш
  - Переводы: B.V. Raman, R. Santhanam
- **Varaha Mihira, «Brihat Jataka»** (ок. VI в. н.э.) — другой фундаментальный текст
- **Phaladeepika** (Mantreshwara) — практическое руководство

## Современные источники (ведическая)

- **B.V. Raman, «A Manual of Hindu Astrology»** — классика на английском
- **Barbara Pijan Lama** — обширный бесплатный ресурс: https://www.barbarapijan.com/bpa/
  [TODO: проверить актуальность]
- **AstroSage** — https://www.astrosage.com — ведические расчёты онлайн
- **Parashara's Light** — профессиональная ведическая программа

## Накшатры (ведическая)

- **Komilla Sutton, «The Nakshatras»** — стандартная книга на английском
- **Dennis Harness, «The Nakshatras: The Lunar Mansions of Vedic Astrology»**
- Таблица накшатр: https://www.astrojyoti.com/nakshatrachart.htm [TODO: проверить]

## Айанамши

| Система айанамши | Примерное значение (2025) | Кто использует |
|-----------------|--------------------------|----------------|
| Lahiri (Chitrapaksha) | ~23.90° | Официальная Индия, большинство Джйотиш |
| Fagan-Bradley | ~24.00° | Западный сидеризм |
| Krishnamurti (KP) | ~23.86° | KP-астрология |
| Raman | ~22.46° | B.V. Raman школа |

Актуальные значения: https://www.astro.com/swisseph/swisseph.htm (Swiss Ephemeris docs)

## Ресурсы по прогнозированию (транзиты)

- **Robert Hand, «Planets in Transit»** — детальные интерпретации каждого транзита
- **Erin Sullivan, «Retrograde Planets»** — обратные движения планет и их значение
- **Café Astrology** — cafeastrology.com — популярные статьи по транзитам (упрощённо)

## Для совместимости (синастрия)

- **Robert Hand, «Planets in Synastry»** — стандарт
- **Liz Greene, «Relating»** — психологический подход к синастрии
- Гуна-милан онлайн: https://www.astrology.com.tr/compatibility.asp [TODO: проверить]

---

## Дисклеймер

Астрология — не наука в академическом смысле: нет доказательной базы в рандомизированных контрольных исследованиях. Продукт Mandala позиционируется как **инструмент самопознания и рефлексии**, не как предсказание событий.

Пользователь принимает это условие; LLM-промпты должны избегать категоричных предсказаний.
