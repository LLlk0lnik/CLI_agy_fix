# Проверка интеграции Zed и переход на ACP

Дата: 5 ноября 2026. Проверены относящиеся к Antigravity пути поставки, запуска и ACP-взаимодействия. Это не аудит всего Zed и не исходников закрытого агента Google.

## Источники

- [Google: ACP для Zed и других редакторов](https://antigravity.google/docs/ide/extensions/acp/).
- [Zed: Google Antigravity в ACP Registry](https://zed.dev/acp/agent/antigravity-acp).
- [Текущий манифест Google ACP](https://github.com/agentclientprotocol/registry/blob/main/antigravity-acp/agent.json), получен 2026-11-05; версия 1.4.5. SHA256 загруженного JSON: `00955e5d71f60d2c8255847fd35966f7f27e25c3bc3c59b28d08b1df9fcd1fac`.
- Zed, фиксированный коммит `ddc9eda92552757d5800758c8d410403ef557a07`:
  - [agent_server_store.rs](https://github.com/zed-industries/zed/blob/ddc9eda92552757d5800758c8d410403ef557a07/crates/project/src/agent_server_store.rs): бинарная поставка из реестра, хеш архива и команда запуска.
  - [custom.rs](https://github.com/zed-industries/zed/blob/ddc9eda92552757d5800758c8d410403ef557a07/crates/agent_servers/src/custom.rs): окружение процесса и proxy env.
  - [acp/v1.rs](https://github.com/zed-industries/zed/blob/ddc9eda92552757d5800758c8d410403ef557a07/crates/agent_servers/src/acp/v1.rs): stdio, initialize, clientInfo, authenticate, session/new/load, config options, prompt, permissions.
  - [acp/runtime.rs](https://github.com/zed-industries/zed/blob/ddc9eda92552757d5800758c8d410403ef557a07/crates/agent_servers/src/acp/runtime.rs): lifecycle дочернего процесса.
  - [auth_methods.rs](https://github.com/zed-industries/zed/blob/ddc9eda92552757d5800758c8d410403ef557a07/crates/acp_thread/src/auth_methods.rs) и [config_options.rs](https://github.com/zed-industries/zed/blob/ddc9eda92552757d5800758c8d410403ef557a07/crates/acp_thread/src/config_options.rs): адаптация методов входа и выбора модели.

## Результаты сравнения

| Область | Zed | agyCLI после изменения |
|---|---|---|
| Агент | Google ACP из реестра | Тот же Google ACP 1.4.5 из тех же архивов |
| Вход и токены | Агент обрабатывает authenticate | Агент обрабатывает authenticate; Python не читает OAuth-файлы |
| Сеть Google | Запросы выполняет агент | Запросы выполняет агент; собственного HTTP API-шлюза нет |
| Транспорт клиента | JSON-RPC ACP через stdio | JSON-RPC ACP через stdio |
| Идентичность | clientInfo.name=zed | clientInfo.name=agycli-terminal, без маскировки |
| Модели | Опции, объявленные агентом | Только объявленные агентом ID/опции; нет алиасов |
| Права | Обработка ACP permission requests | Подтверждение или отказ; yolo явно выбирает allow_once |
| Сессии | new/load/list и UI истории | new/load; last-session.json только с ID и cwd |
| Редакторские возможности | Буферы, FS, terminal, forms | Не реализованы и объявлены отключёнными |
| Профиль | Определяется настройками Zed/агента | Отдельный GEMINI_HOME; профиль Zed не затрагивается |
| Прокси | Env процесса | Env процесса с явным локальным Happ; без прямого fallback |

Не копируются имя Zed, его метаданные возможностей, доступ к неподдерживаемым моделям или токены. Google документирует, что сторонние модели могут быть доступны только перечисленным редакторам. Терминальный ACP-клиент не может обещать идентичный каталог моделей.

## Подлинность бинарников

Оба официальных архива загружены с dl.google.com. SHA256 архивов совпал с ACP Registry. Распакованные ACP и localharness_external побайтово совпали с уже имеющимися runtime-файлами:

- macOS arm64: архив `886fff7767d08994ca39eb02c3d01a7532c7325a94aa33aaf52d52068733284f`.
- Windows x64: архив `d24f4c73c9f96d4f44384e281c855a56c72d8eaa9c5b0fad7ccb1635c6b63f50`.

Ссылки архивов и хеши отдельных файлов находятся в BUILD-MANIFEST.json. Runtime-файлы не изменялись. Перед каждым запуском проверяются размеры и SHA256 ACP и harness.

## Удалённые пути

Удалены SmartDNS, DoH-запросы, статические IP SNI-прокси, собственный refresh токена, loadCodeAssist/каталог/generation HTTP-запросы, региональный failover и подстановка моделей. Все пользовательские входы направлены в ACP; старый agy_native.py теперь только совместимый вход. Локальные DNS ОС, конфигурация Happ и настройки Zed не менялись.

## Проверки и границы уверенности

Проверены stdio-сценарии с тестовым subprocess-агентом: handshake без входа; advertised authenticate; одна попытка входа при AuthenticationRequired; передача 403 без повторного входа/fallback; запрос, модели и разрешения; resume без повтора истории; отказ при изменённом runtime; отсутствие чтения файла токена клиентом.

Реальный оригинальный macOS ACP 1.4.5 прошёл initialize в пустом временном профиле. Реальная генерация после апелляции не проверялась. Windows-бинарник проверен на соответствие официальному архиву, но на Windows не запускался. Архивы Google содержат закрытые исполняемые файлы: сетевое поведение и внутренние проверки аккаунта не были доказаны аудитом исходников.

Совпадение ACP-архитектуры не отменяет региональные ограничения и не гарантирует отсутствие ограничений аккаунта. Доступность локального порта Happ не доказывает страну выхода. Браузер входа имеет отдельные настройки сети. Существующий серверный 403 исправлением клиента не снимается.

Итог проверки реализации: 42 Python-теста каждой платформенной копии прошли на macOS; 4 теста инструментов сборки и паритета исходников прошли. Новый клиент с настоящим ACP 1.4.5 прошёл initialize в пустом временном профиле. Генерация и авторизация аккаунта при этой проверке не запускались.
