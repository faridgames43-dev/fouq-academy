# إعدادات gunicorn — يقرؤها تلقائيًا الأمر `gunicorn app:app` من مجلد المشروع.
# عامل واحد (لأن قاعدة البيانات SQLite) لكن بعدة خيوط، حتى لا يتجمد الموقع كله
# عندما يدخل عدد كبير من اللاعبين معًا أو يستغرق طلب ثقيل (تشفير كلمة المرور).
worker_class = "gthread"
workers = 1
threads = 8
timeout = 120
graceful_timeout = 30
keepalive = 5
