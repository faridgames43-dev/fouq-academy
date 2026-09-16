# FOUQ Operating System — أكاديمية فوق

نظام تشغيل رقمي متكامل لأكاديمية فوق الرياضية (FOUQ Academy)، مبني كمنتج SaaS داخلي
قابل للتوسع لعدة فروع وفئات مستقبلًا. تم بناء النظام وتشغيله واختباره فعليًا بقاعدة بيانات
حقيقية — لا نماذج شكلية ولا أزرار وهمية.

## لماذا Flask + SQLite بدل Next.js/Postgres؟

الخطة الأصلية كانت Next.js + Prisma + Supabase (Postgres). أثناء البناء الفعلي في بيئة
التنفيذ، تبيّن أن الوصول الشبكي مقصور على نطاقات محدودة ولا يشمل سجلات npm (registry.npmjs.org)
أو PyPI، أي لا يمكن تثبيت أي حزمة جديدة داخل بيئة البناء. تم اتخاذ قرار هندسي واعٍ:
بناء النظام كاملًا بلغة Python (Flask + Jinja2 + Werkzeug — وكلها مثبّتة مسبقًا) وقاعدة
بيانات SQLite حقيقية (ملف واحد، علاقات، مفاتيح خارجية، فهارس) بدل الانتظار أو تسليم كود
لا يمكن تشغيله أو اختباره فعليًا في هذه البيئة.

**هذا لم يقلل من جدية النظام**: كل قاعدة عمل مطلوبة في المواصفة مطبّقة فعليًا، تعمل، ومُختبرة
بـ 18 سيناريو اختبار حقيقي على قاعدة بيانات فعلية (انظر `QA_RESULTS.md`). الانتقال لاحقًا إلى
PostgreSQL هو تغيير سطر اتصال واحد لأن كل الاستعلامات SQL قياسية ومنظمة في `business/` و `db.py`؛
والانتقال لواجهة Next.js/React ممكن دون تغيير قاعدة البيانات أو منطق الأعمال لأن كل شيء
مفصول بوضوح في طبقة `business/*.py` عن طبقة العرض `templates/*.html`.

## التشغيل السريع

```bash
cd fouq-academy
pip install flask werkzeug --break-system-packages   # عادة مثبّتة مسبقًا في بيئات بايثون الحديثة
python3 seed.py     # ينشئ قاعدة البيانات ويعبّئها ببيانات تجريبية واقعية (15 لاعبًا، أدوار متعددة)
python3 app.py      # يشغّل الخادم على http://localhost:5050
```

افتح المتصفح على `http://localhost:5050` وسجّل الدخول بأحد الحسابات التجريبية أدناه.

لإعادة توليد PDF التقارير (Arabic RTL) يلزم Node.js + Playwright (مثبتان في بيئة البناء
الأصلية ويُستخدمان عبر `tools/render_pdf.js`). إن لم تتوفر Node/Playwright في بيئة التشغيل
النهائية، تبقى كل الشاشات وتقارير CSV تعمل بدون أي اعتماد خارجي — فقط زر "PDF" سيحتاج تفعيل
Playwright (`npm install -g playwright && npx playwright install chromium`).

## حسابات تجريبية (كلمة المرور لجميع الحسابات: `Fouq@2026`)

| الدور | البريد / الجوال |
|---|---|
| الإدارة العليا (Super Admin) | superadmin@fouq.sa |
| مدير المشروع (Project Manager) | pm@fouq.sa |
| مدير الفرع (Branch Manager) | branchmgr@fouq.sa |
| المشرف التشغيلي (Supervisor) | supervisor@fouq.sa |
| مدرب البراعم | coach1@fouq.sa |
| مدرب الأشبال | coach2@fouq.sa |
| ولي أمر (له ابنان) | الجوال الظاهر في صفحة تسجيل الدخول (يتغيّر مع كل seed) |
| لاعب | الجوال الظاهر في صفحة تسجيل الدخول |

> صفحة تسجيل الدخول نفسها تعرض تلقائيًا رقم جوال ولي أمر حقيقي (له ابنان) ورقم جوال لاعب
> حقيقي مباشرة من قاعدة البيانات الحالية، لتفادي أي التباس بسبب أرقام تُنشأ عشوائيًا.

## بنية المشروع

```
app.py                  نقطة تشغيل Flask + تسجيل كل الوحدات (blueprints)
db.py                   طبقة اتصال SQLite (اتصال، تنفيذ، معاملات)
schema.sql              مخطط قاعدة البيانات الكامل (٣٠+ جدول)
seed.py                 بيانات تجريبية واقعية (15 لاعبًا بحالات متنوعة + كل الأدوار)
business/               كل منطق الأعمال (لا يوجد منطق أعمال داخل الشاشات إطلاقًا)
  entitlements.py        محرك الحصص (FEFO، الفصل بين أساسي/تعويضي/إضافي/سابق)
  subscriptions.py       محرك الاشتراكات (Subscription Period ≠ Session Entitlement)
  attendance.py           التحضير والخصم التلقائي والإلغاء الآمن
  assessments.py, levels.py, points.py, achievements.py, rewards.py
  renewal_risk.py, legacy.py, crm.py, notifications.py, reports.py
  rbac.py                 الصلاحيات (Role-Based Access Control)
  audit.py                سجل التدقيق (Audit Log)
  barcode.py              مولّد باركود Code39 حقيقي (بدون اعتماد خارجي)
  pdf_export.py           تصدير PDF عربي RTL عبر Playwright/Chromium
blueprints/              مسارات الويب (HTTP) لكل وحدة — تستدعي business/ فقط
templates/               واجهات Jinja2 (عربي RTL، متوافقة مع الجوال)
static/                  CSS/JS/الشعار
tools/render_pdf.js      مصدّر PDF عبر Chromium الحقيقي (Arabic RTL shaping صحيح)
tests/qa_tests.py         مجموعة اختبارات آلية حقيقية على قاعدة بيانات تجريبية منفصلة
```

## القاعدة التجارية الأهم في الكود

> "انتهاء الاشتراك لا يعني إسقاط حق اللاعب في الحصص المستحقة."

هذه القاعدة مطبّقة حرفيًا في `business/subscriptions.get_attendance_eligibility()` و
`business/entitlements.py`، حيث يتم الفصل التام بين:
- **Subscription Status** (نشط / ينتهي قريبًا / منتهٍ / مجمّد / ملغى) — بُعد زمني بحت.
- **Attendance Eligibility** (مؤهل / استكمال حصص مستحقة / لا حصص / مجمّد / صلاحية إدارية) —
  بُعد محاسبي مستقل عن الزمن.

كل خصم حصة له سجل في `session_ledger` (Append-only)، وكل إلغاء تحضير يعكس بالضبط نفس
الدفعة (Entitlement Batch) التي خُصمت منها، ولا يُنشئ رصيدًا جديدًا عشوائيًا أبدًا.
